#!/usr/bin/env python3
"""
Assign CourseInstructor (TEACHER) and LiveBatch.instructor using
data/natya_analytics.xlsx ('Course Teachers & Learners'), matching
source Course Name -> LMS Course (Course.title) -> LiveBatch (LiveBatch.course_id).

Dry-run by default (no DB writes). Refuses to run against SQLite.
"""
import argparse
import csv
import os
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

import openpyxl

EXCLUDED_INSTRUCTOR_IDS = {1, 25, 39, 96}


def norm_email(val):
    return str(val).strip().lower() if val else ""


def norm_phone_variants(val):
    if not val:
        return "", ""
    s = str(val).strip().lstrip("'")
    digits = re.sub(r"\D", "", s)
    if not digits:
        return "", ""
    last10 = digits[-10:] if len(digits) >= 10 else digits
    return digits, last10


def norm_name(val):
    if not val:
        return ""
    s = re.sub(r"[^a-z0-9\s]", " ", str(val).strip().lower())
    return re.sub(r"\s+", " ", s).strip()


def compact_key(val):
    if not val:
        return ""
    return re.sub(r"[^a-z0-9]", "", str(val).strip().lower())


def filter_eligible_teachers(users):
    return [
        u
        for u in users
        if u.is_active and u.id not in EXCLUDED_INSTRUCTOR_IDS and u.is_teacher
    ]


def resolve_course_map(source_course_names, lms_courses):
    lms_by_norm = defaultdict(list)
    lms_by_compact = defaultdict(list)
    for c in lms_courses:
        n = norm_name(c.title)
        if n:
            lms_by_norm[n].append(c)
        ck = compact_key(c.title)
        if ck:
            lms_by_compact[ck].append(c)

    resolved = {}
    for src_name in source_course_names:
        matches = lms_by_norm.get(norm_name(src_name), [])
        if len(matches) == 1:
            resolved[src_name] = (matches[0], "")
        elif len(matches) > 1:
            ids = ", ".join(str(m.id) for m in matches)
            resolved[src_name] = (None, f"Multiple LMS Courses matched '{src_name}': {ids}")
        else:
            c_matches = lms_by_compact.get(compact_key(src_name), [])
            if len(c_matches) == 1:
                resolved[src_name] = (c_matches[0], "")
            elif len(c_matches) == 0:
                resolved[src_name] = (None, f"No LMS Course matched '{src_name}'")
            else:
                ids = ", ".join(str(m.id) for m in c_matches)
                resolved[src_name] = (None, f"Multiple LMS Courses matched '{src_name}': {ids}")
    return resolved


def run_self_tests():
    from types import SimpleNamespace

    users = [
        SimpleNamespace(id=1, is_active=True, is_teacher=True),
        SimpleNamespace(id=25, is_active=True, is_teacher=True),
        SimpleNamespace(id=39, is_active=True, is_teacher=True),
        SimpleNamespace(id=96, is_active=True, is_teacher=True),
        SimpleNamespace(id=40, is_active=False, is_teacher=True),
        SimpleNamespace(id=41, is_active=True, is_teacher=True),
        SimpleNamespace(id=42, is_active=True, is_teacher=False),
    ]
    assert {u.id for u in filter_eligible_teachers(users)} == {41}

    courses = [
        SimpleNamespace(id=10, title="G 117 CMJ"),
        SimpleNamespace(id=11, title="G118 CMS"),
        SimpleNamespace(id=20, title="Dup Course"),
        SimpleNamespace(id=21, title="Dup Course"),
    ]
    cmap = resolve_course_map([" G 117 CMJ", "G 118 CMS", "Dup Course", "Unknown"], courses)
    assert cmap[" G 117 CMJ"][0].id == 10
    assert cmap["G 118 CMS"][0].id == 11
    assert cmap["Dup Course"][0] is None
    assert cmap["Unknown"][0] is None
    print("SELF_TEST_PASSED: Course resolution and active teacher eligibility checks passed.")


def load_eb_env_if_needed():
    if "RDS_DB_NAME" in os.environ or "DATABASE_URL" in os.environ:
        return
    eb_env = Path("/opt/elasticbeanstalk/deployment/env")
    if eb_env.exists():
        for line in eb_env.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            if k and k not in os.environ:
                os.environ[k] = v


def setup_django(app_dir: Path):
    if str(app_dir) not in sys.path:
        sys.path.insert(0, str(app_dir))
    load_eb_env_if_needed()
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "core.settings")
    import django

    django.setup()


def validate_postgres():
    from django.conf import settings
    from django.db import connection

    db_cfg = settings.DATABASES.get("default", {})
    engine = db_cfg.get("ENGINE", "")
    host = db_cfg.get("HOST", "")
    name = db_cfg.get("NAME", "")

    if "sqlite" in engine.lower() or connection.vendor != "postgresql":
        print(
            f"ABORTED: Refusing to run against non-PostgreSQL database "
            f"(ENGINE={engine!r}, vendor={connection.vendor!r}).",
            file=sys.stderr,
        )
        sys.exit(2)

    with connection.cursor() as cur:
        cur.execute("SELECT 1")
        cur.fetchone()

    print(f"Verified PostgreSQL connection: host={host} | database={name}")


def main():
    default_app_dir = (
        Path("/var/app/current")
        if Path("/var/app/current").exists()
        else Path(__file__).resolve().parent
    )
    default_out = (
        Path("/tmp/natya_course_batch_assignment")
        if Path("/tmp").exists()
        else default_app_dir / "data" / "course_batch_assignment"
    )

    parser = argparse.ArgumentParser(
        description="Assign CourseInstructor and LiveBatch.instructor from natya_analytics.xlsx."
    )
    parser.add_argument("--app-dir", type=str, default=str(default_app_dir))
    parser.add_argument("--xlsx", type=str, default="")
    parser.add_argument("--sheet", type=str, default="Course Teachers & Learners")
    parser.add_argument("--out-dir", type=str, default=str(default_out))
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write CourseInstructor (TEACHER) and fill NULL LiveBatch.instructor for unambiguous 1-teacher courses.",
    )
    parser.add_argument(
        "--overwrite-wrong",
        action="store_true",
        help="With --apply, also overwrite mismatched non-null LiveBatch.instructor (still skips excluded IDs 1,25,39,96).",
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="Run local unit checks and exit.",
    )
    args = parser.parse_args()

    if args.self_test:
        run_self_tests()
        return

    setup_django(Path(args.app_dir))
    validate_postgres()

    from django.conf import settings
    from django.contrib.auth import get_user_model
    from django.db import transaction
    from courses.models import Course, CourseInstructor, LiveBatch

    xlsx_path = (
        Path(args.xlsx)
        if args.xlsx
        else Path(settings.BASE_DIR) / "data" / "natya_analytics.xlsx"
    )
    if not xlsx_path.exists():
        print(f"ERROR: Workbook not found: {xlsx_path}", file=sys.stderr)
        sys.exit(1)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    User = get_user_model()

    # 1. Load source workbook: Course Name -> set of teachers
    wb = openpyxl.load_workbook(xlsx_path, read_only=True, data_only=True)
    ws = wb[args.sheet]
    rows = list(ws.iter_rows(values_only=True))
    header = [str(c).strip() if c is not None else "" for c in rows[0]]
    col_idx = {name: i for i, name in enumerate(header)}

    def get_cell(row_tuple, idx):
        return row_tuple[idx] if idx < len(row_tuple) else None

    source_teachers = {}
    course_teacher_keys = defaultdict(set)

    for r in rows[1:]:
        if not r or all(v is None or str(v).strip() == "" for v in r):
            continue
        c_name = str(get_cell(r, col_idx["Course Name"]) or "").strip()
        t_name = str(get_cell(r, col_idx["Teacher Name"]) or "").strip()
        t_phone = str(get_cell(r, col_idx["Teacher Phone number"]) or "").strip()
        t_email = str(get_cell(r, col_idx["Teacher Email id"]) or "").strip()
        if not c_name or not t_name:
            continue

        t_digits, t_last10 = norm_phone_variants(t_phone)
        t_em = norm_email(t_email)
        t_key = (norm_name(t_name), t_last10, t_em)
        if t_key not in source_teachers:
            source_teachers[t_key] = {
                "name": t_name,
                "phone_raw": t_phone,
                "phone_digits": t_digits,
                "phone_last10": t_last10,
                "email": t_em,
            }
        course_teacher_keys[c_name].add(t_key)

    # 2. Match source teachers -> active eligible LMS teachers
    all_users = list(
        User.objects.only(
            "id", "username", "first_name", "last_name", "email", "phone_number", "is_teacher", "is_active"
        )
    )
    eligible_teachers = filter_eligible_teachers(all_users)
    eligible_by_id = {u.id: u for u in eligible_teachers}

    by_email = defaultdict(list)
    by_phone_exact = defaultdict(list)
    by_phone_last10 = defaultdict(list)
    by_user_name = defaultdict(list)
    for u in eligible_teachers:
        em = norm_email(u.email)
        if em:
            by_email[em].append(u)
        d, l10 = norm_phone_variants(u.phone_number)
        if d:
            by_phone_exact[d].append(u)
        if l10 and len(l10) == 10:
            by_phone_last10[l10].append(u)
        uname = norm_name(f"{u.first_name} {u.last_name}".strip() or u.username)
        if uname:
            by_user_name[uname].append(u)

    teacher_key_to_lms = {}
    teacher_name_to_lms = {}
    unmatched_teachers = []
    for t_key, info in source_teachers.items():
        matched_u = None
        if info["email"] and len(by_email.get(info["email"], [])) == 1:
            matched_u = by_email[info["email"]][0]
        elif info["phone_digits"] and len(by_phone_exact.get(info["phone_digits"], [])) == 1:
            matched_u = by_phone_exact[info["phone_digits"]][0]
        elif info["phone_last10"] and len(by_phone_last10.get(info["phone_last10"], [])) == 1:
            matched_u = by_phone_last10[info["phone_last10"]][0]

        if matched_u:
            teacher_key_to_lms[t_key] = matched_u
            teacher_name_to_lms[norm_name(info["name"])] = matched_u
        else:
            unmatched_teachers.append(info)

    # 2b. Also load All Sessions (class_name -> teacher_name) for courses missing from CTL
    if "All Sessions" in wb.sheetnames:
        ws_as = wb["All Sessions"]
        as_rows = list(ws_as.iter_rows(values_only=True))
        if as_rows:
            as_hdr = [str(c).strip() if c is not None else "" for c in as_rows[0]]
            as_idx = {name: i for i, name in enumerate(as_hdr)}
            if "class_name" in as_idx and "teacher_name" in as_idx:
                ctl_norm_courses = {norm_name(k) for k in course_teacher_keys.keys()}
                ctl_compact_courses = {compact_key(k) for k in course_teacher_keys.keys()}
                as_course_teachers = defaultdict(set)
                for r in as_rows[1:]:
                    if not r:
                        continue
                    c_name = str(get_cell(r, as_idx["class_name"]) or "").strip()
                    t_name = str(get_cell(r, as_idx["teacher_name"]) or "").strip()
                    if not c_name or not t_name:
                        continue
                    if norm_name(c_name) in ctl_norm_courses or compact_key(c_name) in ctl_compact_courses:
                        continue
                    t_norm = norm_name(t_name)
                    u = teacher_name_to_lms.get(t_norm)
                    if not u and len(by_user_name.get(t_norm, [])) == 1:
                        u = by_user_name[t_norm][0]
                    if u:
                        as_course_teachers[c_name].add(u)
                for c_name, u_set in as_course_teachers.items():
                    for u in u_set:
                        tk = (norm_name(f"{u.first_name} {u.last_name}"), "", norm_email(u.email))
                        teacher_key_to_lms[tk] = u
                        if tk not in source_teachers:
                            source_teachers[tk] = {
                                "name": f"{u.first_name} {u.last_name}".strip() or u.username,
                                "phone_raw": u.phone_number or "",
                                "phone_digits": "",
                                "phone_last10": "",
                                "email": norm_email(u.email),
                            }
                        course_teacher_keys[c_name].add(tk)

    # 3. Resolve source courses -> LMS Course
    lms_courses = list(Course.objects.only("id", "title"))
    course_map = resolve_course_map(course_teacher_keys.keys(), lms_courses)

    # Map LMS course_id -> list of matched eligible teacher User objects
    lms_course_to_teachers = {}
    lms_course_to_src_name = {}
    unmatched_courses = []
    for src_c_name, t_keys in sorted(course_teacher_keys.items()):
        lms_c, err = course_map[src_c_name]
        if not lms_c:
            unmatched_courses.append({"source_course": src_c_name, "reason": err})
            continue
        matched_users = []
        missing_t = []
        for tk in sorted(t_keys):
            u = teacher_key_to_lms.get(tk)
            if u:
                matched_users.append(u)
            else:
                missing_t.append(source_teachers[tk]["name"])
        if matched_users and not missing_t:
            lms_course_to_teachers[lms_c.id] = matched_users
            lms_course_to_src_name[lms_c.id] = src_c_name

    # 4. Plan CourseInstructor assignments & build existing CourseInstructor(TEACHER) fallback map
    existing_ci = set(
        CourseInstructor.objects.filter(role=CourseInstructor.InstructorRole.TEACHER).values_list(
            "course_id", "user_id"
        )
    )
    existing_ci_eligible_by_course = defaultdict(list)
    for cid, uid in existing_ci:
        if uid in eligible_by_id:
            existing_ci_eligible_by_course[cid].append(eligible_by_id[uid])
    ci_plan = []
    for cid, t_users in sorted(lms_course_to_teachers.items()):
        is_single = len(t_users) == 1
        for u in t_users:
            if (cid, u.id) not in existing_ci:
                ci_plan.append(
                    {
                        "course_id": cid,
                        "course_title": lms_course_to_src_name[cid],
                        "user_id": u.id,
                        "teacher_name": f"{u.first_name} {u.last_name}".strip() or u.username,
                        "is_primary": is_single,
                    }
                )

    # 5. Plan LiveBatch.instructor assignments across ALL batches
    batches = list(
        LiveBatch.objects.select_related("course", "instructor")
        .only(
            "id",
            "course_id",
            "course__title",
            "instructor_id",
            "instructor__username",
            "instructor__first_name",
            "instructor__last_name",
            "batch_type",
        )
        .order_by("id")
    )

    counts = Counter()
    batch_plan_rows = []
    batch_updates = []
    manual_rows = []

    for b in batches:
        curr_id = b.instructor_id
        curr_name = (
            f"{b.instructor.first_name} {b.instructor.last_name}".strip() or b.instructor.username
            if b.instructor
            else ""
        )
        t_users = lms_course_to_teachers.get(b.course_id)
        exp_user = None

        if curr_id in EXCLUDED_INSTRUCTOR_IDS:
            status = "MANUAL_REVIEW_EXCLUDED_CURRENT_INSTRUCTOR"
            reason = f"current_instructor_id={curr_id} is excluded"
        elif not t_users:
            ci_teachers = existing_ci_eligible_by_course.get(b.course_id, [])
            if curr_id is None and len(ci_teachers) == 1:
                exp_user = ci_teachers[0]
                status = "MISSING_CONFIRMED"
                reason = f"NULL -> LMS#{exp_user.id} (from existing single CourseInstructor TEACHER)"
                batch_updates.append((b.id, exp_user.id, status))
            elif curr_id is not None and len(ci_teachers) == 1 and curr_id == ci_teachers[0].id:
                exp_user = ci_teachers[0]
                status = "CONFIRMED_CORRECT"
                reason = f"Matches existing single CourseInstructor TEACHER LMS#{exp_user.id}"
            else:
                status = "MANUAL_REVIEW_UNMATCHED_COURSE_OR_TEACHER"
                reason = f"Course '{b.course.title if b.course else ''}' not unambiguously resolved in sheet or CourseInstructor"
        elif len(t_users) > 1:
            t_ids = {u.id for u in t_users}
            if curr_id in t_ids:
                status = "CONFIRMED_CORRECT_MULTI_TEACHER"
                reason = f"Current LMS#{curr_id} is one of {len(t_users)} sheet teachers"
            else:
                status = "MANUAL_REVIEW_MULTI_TEACHER"
                names = ", ".join(
                    f"{u.first_name} {u.last_name}".strip() or u.username for u in t_users
                )
                reason = f"Course has {len(t_users)} sheet teachers: {names}"
        else:
            exp_user = t_users[0]
            if curr_id == exp_user.id:
                status = "CONFIRMED_CORRECT"
                reason = f"Matches single sheet teacher LMS#{exp_user.id}"
            elif curr_id is None:
                status = "MISSING_CONFIRMED"
                reason = f"NULL -> LMS#{exp_user.id}"
                batch_updates.append((b.id, exp_user.id, status))
            else:
                status = "WRONG_ASSIGNMENT"
                reason = f"LMS#{curr_id} -> LMS#{exp_user.id}"
                if args.overwrite_wrong:
                    batch_updates.append((b.id, exp_user.id, status))

        counts[status] += 1
        row = {
            "batch_id": b.id,
            "course_id": b.course_id,
            "course_title": b.course.title if b.course else "",
            "batch_type": b.batch_type,
            "current_instructor_id": curr_id or "",
            "current_instructor_name": curr_name,
            "expected_instructor_id": exp_user.id if exp_user else "",
            "expected_instructor_name": (
                f"{exp_user.first_name} {exp_user.last_name}".strip() or exp_user.username
                if exp_user
                else ""
            ),
            "status": status,
            "reason": reason,
        }
        batch_plan_rows.append(row)
        if status.startswith("MANUAL_REVIEW") or status == "WRONG_ASSIGNMENT":
            manual_rows.append(row)

    # 6. Write CSVs
    with open(out_dir / "course_instructor_plan.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(
            f, fieldnames=["course_id", "course_title", "user_id", "teacher_name", "is_primary"]
        )
        w.writeheader()
        w.writerows(ci_plan)

    batch_fields = [
        "batch_id",
        "course_id",
        "course_title",
        "batch_type",
        "current_instructor_id",
        "current_instructor_name",
        "expected_instructor_id",
        "expected_instructor_name",
        "status",
        "reason",
    ]
    with open(out_dir / "livebatch_assignment_plan.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=batch_fields)
        w.writeheader()
        w.writerows(batch_plan_rows)

    with open(out_dir / "livebatch_manual_review.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=batch_fields)
        w.writeheader()
        w.writerows(manual_rows)

    # 7. Apply if --apply
    ci_created = 0
    batches_updated = 0
    if args.apply:
        with transaction.atomic():
            for item in ci_plan:
                _, created = CourseInstructor.objects.get_or_create(
                    course_id=item["course_id"],
                    user_id=item["user_id"],
                    role=CourseInstructor.InstructorRole.TEACHER,
                    defaults={"is_primary": item["is_primary"]},
                )
                if created:
                    ci_created += 1

            for b_id, exp_uid, st in batch_updates:
                if exp_uid in EXCLUDED_INSTRUCTOR_IDS:
                    continue
                if st == "MISSING_CONFIRMED":
                    batches_updated += LiveBatch.objects.filter(
                        id=b_id, instructor_id__isnull=True
                    ).update(instructor_id=exp_uid)
                elif st == "WRONG_ASSIGNMENT" and args.overwrite_wrong:
                    batches_updated += (
                        LiveBatch.objects.filter(id=b_id)
                        .exclude(instructor_id__in=EXCLUDED_INSTRUCTOR_IDS)
                        .update(instructor_id=exp_uid)
                    )

    print("=== COURSE & LIVEBATCH TEACHER ASSIGNMENT REPORT ===")
    print(f"Mode: {'APPLY' if args.apply else 'DRY-RUN (no DB writes)'} | overwrite_wrong={args.overwrite_wrong}")
    print(f"Total LiveBatch records checked: {len(batches)}")
    print(f"CourseInstructor (TEACHER) additions planned: {len(ci_plan)}")
    print(f"LiveBatch confirmed correct (1-teacher): {counts['CONFIRMED_CORRECT']}")
    print(f"LiveBatch confirmed correct (multi-teacher): {counts['CONFIRMED_CORRECT_MULTI_TEACHER']}")
    print(f"LiveBatch NULL -> confirmed 1-teacher (MISSING_CONFIRMED): {counts['MISSING_CONFIRMED']}")
    print(f"LiveBatch mismatched non-null (WRONG_ASSIGNMENT): {counts['WRONG_ASSIGNMENT']}")
    print(f"LiveBatch excluded current instructor (manual review): {counts['MANUAL_REVIEW_EXCLUDED_CURRENT_INSTRUCTOR']}")
    print(f"LiveBatch multi-teacher course (manual review): {counts['MANUAL_REVIEW_MULTI_TEACHER']}")
    print(f"LiveBatch unmatched course/teacher (manual review): {counts['MANUAL_REVIEW_UNMATCHED_COURSE_OR_TEACHER']}")
    print(f"Unmatched source teachers: {len(unmatched_teachers)} | Unmatched source courses: {len(unmatched_courses)}")
    print(f"CSV outputs written to: {out_dir}")
    if args.apply:
        print(f"APPLIED: CourseInstructor created={ci_created} | LiveBatch.instructor updated={batches_updated}")


if __name__ == "__main__":
    main()
