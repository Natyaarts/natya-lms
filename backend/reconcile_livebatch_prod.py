#!/usr/bin/env python3
"""
Read-only / dry-run (by default) reconciliation script for LiveBatch.instructor
against data/natya_analytics.xlsx ('Course Teachers & Learners').

Designed to run on the EC2 production instance at /var/app/current using
/var/app/venv/staging-LQM1lest/bin/python with existing EB environment config.
Refuses to run against SQLite.
"""
import argparse
import csv
import os
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

import openpyxl

EXCLUDED_INSTRUCTOR_IDS = {1, 25, 96}
ELIGIBLE_TEACHER_ID_OVERRIDE = {39}


def norm_email(val):
    if not val:
        return ""
    return str(val).strip().lower()


def norm_phone_variants(val):
    """Return (exact_digits, last10) for phone matching."""
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


def filter_eligible_teachers(users):
    """Return active LMS users eligible as LiveBatch instructors."""
    return [
        u
        for u in users
        if u.is_active
        and u.id not in EXCLUDED_INSTRUCTOR_IDS
        and (u.is_teacher or u.id in ELIGIBLE_TEACHER_ID_OVERRIDE)
    ]


def resolve_batch_teacher(matched_students, course_student_hits, course_teachers):
    """
    Require course-level evidence:
    - All matched students must support the same single source course, AND
    - That source course must have exactly one distinct source teacher.
    Returns (chosen_teacher_key_or_None, status_or_None, detail_reason).
    """
    full_coverage_courses = [
        (course_name, course_teachers.get(course_name, set()))
        for course_name, hits in sorted(course_student_hits.items())
        if len(hits) == matched_students
    ]

    if len(full_coverage_courses) == 1:
        course_name, c_teachers = full_coverage_courses[0]
        if len(c_teachers) == 1:
            return next(iter(c_teachers)), None, f"course={course_name}"
        return None, "MANUAL_REVIEW_MULTI_TEACHER", f"course={course_name} has {len(c_teachers)} source teachers"

    if len(full_coverage_courses) > 1:
        desc = "; ".join(f"{c} ({len(ts)} teachers)" for c, ts in full_coverage_courses)
        return None, "MANUAL_REVIEW_MULTI_COURSE", f"multiple full-coverage courses: {desc}"

    top_courses = sorted(course_student_hits.items(), key=lambda kv: (-len(kv[1]), kv[0]))[:3]
    desc = "; ".join(f"{c} ({len(hits)}/{matched_students})" for c, hits in top_courses)
    return None, "MANUAL_REVIEW_SPLIT_COURSES", f"no single course covers all matched students: {desc}"


def run_self_tests():
    """Local unit validation for eligibility and course-level teacher resolution."""
    from types import SimpleNamespace

    users = [
        SimpleNamespace(id=1, is_active=True, is_teacher=True),
        SimpleNamespace(id=25, is_active=True, is_teacher=True),
        SimpleNamespace(id=96, is_active=True, is_teacher=True),
        SimpleNamespace(id=39, is_active=True, is_teacher=False),
        SimpleNamespace(id=40, is_active=False, is_teacher=True),
        SimpleNamespace(id=41, is_active=True, is_teacher=True),
        SimpleNamespace(id=42, is_active=False, is_teacher=False),
    ]
    eligible_ids = {u.id for u in filter_eligible_teachers(users)}
    assert eligible_ids == {39, 41}, f"Expected {{39, 41}}, got {eligible_ids}"

    # Inactive override ID 39 must also be excluded
    assert filter_eligible_teachers([SimpleNamespace(id=39, is_active=False, is_teacher=True)]) == []

    # Case 1: Students span different courses even with the same single teacher -> MANUAL_REVIEW
    t1 = ("teacher one", "9999999999", "t1@example.com")
    t2 = ("teacher two", "8888888888", "t2@example.com")
    chosen, status, _ = resolve_batch_teacher(
        matched_students=2,
        course_student_hits={"Course A": {101}, "Course B": {102}},
        course_teachers={"Course A": {t1}, "Course B": {t1}},
    )
    assert chosen is None and status == "MANUAL_REVIEW_SPLIT_COURSES", (chosen, status)

    # Case 2: All matched students in one course, but course has 2 teachers -> MANUAL_REVIEW
    chosen, status, _ = resolve_batch_teacher(
        matched_students=2,
        course_student_hits={"Course A": {101, 102}},
        course_teachers={"Course A": {t1, t2}},
    )
    assert chosen is None and status == "MANUAL_REVIEW_MULTI_TEACHER", (chosen, status)

    # Case 3: Multiple courses each cover all matched students -> MANUAL_REVIEW
    chosen, status, _ = resolve_batch_teacher(
        matched_students=2,
        course_student_hits={"Course A": {101, 102}, "Course B": {101, 102}},
        course_teachers={"Course A": {t1}, "Course B": {t1}},
    )
    assert chosen is None and status == "MANUAL_REVIEW_MULTI_COURSE", (chosen, status)

    # Case 4: All matched students support one source course with one teacher -> confirmed
    chosen, status, _ = resolve_batch_teacher(
        matched_students=2,
        course_student_hits={"Course A": {101, 102}, "Other": {101}},
        course_teachers={"Course A": {t1}, "Other": {t2}},
    )
    assert chosen == t1 and status is None, (chosen, status)

    print("SELF_TEST_PASSED: All eligibility and course-level teacher resolution checks passed.")


def load_eb_env_if_needed():
    """If neither RDS_DB_NAME nor DATABASE_URL is in os.environ, load EB env file if present."""
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

    # Verify live connection works (read-only SELECT 1)
    with connection.cursor() as cur:
        cur.execute("SELECT 1")
        cur.fetchone()

    print(f"Verified PostgreSQL connection: host={host} | database={name}")


def main():
    default_app_dir = Path("/var/app/current") if Path("/var/app/current").exists() else Path(__file__).resolve().parent
    default_out = (
        Path("/tmp/natya_reconciliation_output")
        if Path("/tmp").exists()
        else default_app_dir / "data" / "reconciliation_output"
    )

    parser = argparse.ArgumentParser(
        description="Audit and optionally reconcile LiveBatch.instructor against natya_analytics.xlsx."
    )
    parser.add_argument("--app-dir", type=str, default=str(default_app_dir))
    parser.add_argument("--xlsx", type=str, default="")
    parser.add_argument("--sheet", type=str, default="Course Teachers & Learners")
    parser.add_argument("--out-dir", type=str, default=str(default_out))
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply unambiguous LiveBatch.instructor updates inside a transaction. Omit for dry-run.",
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="Run local unit validation checks and exit.",
    )
    args = parser.parse_args()

    if args.self_test:
        run_self_tests()
        return

    app_dir = Path(args.app_dir)
    setup_django(app_dir)
    validate_postgres()

    from django.conf import settings
    from django.contrib.auth import get_user_model
    from django.db import transaction
    from django.db.models import Prefetch
    from courses.models import LiveBatch, LiveBatchStudent

    xlsx_path = Path(args.xlsx) if args.xlsx else Path(settings.BASE_DIR) / "data" / "natya_analytics.xlsx"
    if not xlsx_path.exists():
        print(f"ERROR: Workbook not found: {xlsx_path}", file=sys.stderr)
        sys.exit(1)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    User = get_user_model()

    # 1. Load source workbook
    wb = openpyxl.load_workbook(xlsx_path, read_only=True, data_only=True)
    ws = wb[args.sheet]
    rows = list(ws.iter_rows(values_only=True))
    header = [str(c).strip() if c is not None else "" for c in rows[0]]
    col_idx = {name: i for i, name in enumerate(header)}

    required_cols = [
        "Course Name",
        "Course Subject",
        "Teacher Name",
        "Teacher Phone number",
        "Teacher Email id",
        "Learner Name",
        "Learner phone number",
        "Learner email id",
    ]
    for c in required_cols:
        if c not in col_idx:
            print(f"ERROR: Missing column '{c}' in sheet '{args.sheet}'", file=sys.stderr)
            sys.exit(1)

    def get_cell(row_tuple, idx):
        return row_tuple[idx] if idx < len(row_tuple) else None

    source_rows = []
    source_teachers = {}
    course_teachers = defaultdict(set)

    src_by_email = defaultdict(list)
    src_by_phone_exact = defaultdict(list)
    src_by_phone_last10 = defaultdict(list)
    src_by_name = defaultdict(list)

    for r in rows[1:]:
        if not r or all(v is None or str(v).strip() == "" for v in r):
            continue
        c_name = str(get_cell(r, col_idx["Course Name"]) or "").strip()
        c_subj = str(get_cell(r, col_idx["Course Subject"]) or "").strip()
        t_name = str(get_cell(r, col_idx["Teacher Name"]) or "").strip()
        t_phone = str(get_cell(r, col_idx["Teacher Phone number"]) or "").strip()
        t_email = str(get_cell(r, col_idx["Teacher Email id"]) or "").strip()
        l_name = str(get_cell(r, col_idx["Learner Name"]) or "").strip()
        l_phone = str(get_cell(r, col_idx["Learner phone number"]) or "").strip()
        l_email = str(get_cell(r, col_idx["Learner email id"]) or "").strip()

        t_digits, t_last10 = norm_phone_variants(t_phone)
        t_em = norm_email(t_email)
        t_key = (norm_name(t_name), t_last10, t_em)
        if t_key not in source_teachers:
            source_teachers[t_key] = {
                "key": t_key,
                "name": t_name,
                "phone_raw": t_phone,
                "phone_digits": t_digits,
                "phone_last10": t_last10,
                "email": t_em,
            }
        if c_name:
            course_teachers[c_name].add(t_key)

        l_digits, l_last10 = norm_phone_variants(l_phone)
        entry = {
            "course_name": c_name,
            "course_subject": c_subj,
            "teacher_key": t_key,
            "teacher_name": t_name,
            "learner_name": l_name,
            "learner_email": norm_email(l_email),
            "learner_phone_digits": l_digits,
            "learner_phone_last10": l_last10,
            "learner_name_norm": norm_name(l_name),
        }
        idx = len(source_rows)
        source_rows.append(entry)

        if entry["learner_email"]:
            src_by_email[entry["learner_email"]].append(idx)
        if entry["learner_phone_digits"]:
            src_by_phone_exact[entry["learner_phone_digits"]].append(idx)
        if entry["learner_phone_last10"] and len(entry["learner_phone_last10"]) == 10:
            src_by_phone_last10[entry["learner_phone_last10"]].append(idx)
        if entry["learner_name_norm"]:
            src_by_name[entry["learner_name_norm"]].append(idx)

    # 2. Index eligible LMS teacher accounts (exclude 1, 25, 96; keep 39 eligible)
    teacher_fields = (
        "id",
        "username",
        "first_name",
        "last_name",
        "email",
        "phone_number",
        "is_teacher",
        "is_active",
    )
    all_users = list(User.objects.only(*teacher_fields))
    eligible_teachers = filter_eligible_teachers(all_users)

    lms_t_by_email = defaultdict(list)
    lms_t_by_phone_exact = defaultdict(list)
    lms_t_by_phone_last10 = defaultdict(list)
    for u in eligible_teachers:
        em = norm_email(u.email)
        if em:
            lms_t_by_email[em].append(u)
        d, l10 = norm_phone_variants(u.phone_number)
        if d:
            lms_t_by_phone_exact[d].append(u)
        if l10 and len(l10) == 10:
            lms_t_by_phone_last10[l10].append(u)

    teacher_key_to_lms = {}
    unmatched_source_teachers = []
    for t_key, info in source_teachers.items():
        matched_u = None
        method = ""
        if info["email"] and len(lms_t_by_email.get(info["email"], [])) == 1:
            matched_u = lms_t_by_email[info["email"]][0]
            method = "email"
        elif info["phone_digits"] and len(lms_t_by_phone_exact.get(info["phone_digits"], [])) == 1:
            matched_u = lms_t_by_phone_exact[info["phone_digits"]][0]
            method = "phone_exact"
        elif info["phone_last10"] and len(lms_t_by_phone_last10.get(info["phone_last10"], [])) == 1:
            matched_u = lms_t_by_phone_last10[info["phone_last10"]][0]
            method = "phone_last10"

        if matched_u:
            teacher_key_to_lms[t_key] = (matched_u, method)
        else:
            unmatched_source_teachers.append(info)

    # 3. Audit ALL LiveBatch records
    batches = list(
        LiveBatch.objects.select_related("course", "instructor")
        .only(
            "id",
            "course_id",
            "course__id",
            "course__title",
            "instructor_id",
            "instructor__id",
            "instructor__username",
            "instructor__first_name",
            "instructor__last_name",
            "batch_type",
        )
        .prefetch_related(
            Prefetch(
                "students",
                queryset=LiveBatchStudent.objects.select_related("student").only(
                    "id",
                    "batch_id",
                    "student_id",
                    "student__id",
                    "student__username",
                    "student__first_name",
                    "student__last_name",
                    "student__email",
                    "student__phone_number",
                ),
            )
        )
        .order_by("id")
    )

    def match_student_to_source_indices(student):
        em = norm_email(student.email)
        if em and em in src_by_email:
            return src_by_email[em], "email"
        d, l10 = norm_phone_variants(student.phone_number)
        if d and d in src_by_phone_exact:
            return src_by_phone_exact[d], "phone_exact"
        if l10 and len(l10) == 10 and l10 in src_by_phone_last10:
            return src_by_phone_last10[l10], "phone_last10"
        full_name = norm_name(f"{student.first_name} {student.last_name}".strip() or student.username)
        if full_name and full_name in src_by_name:
            idxs = src_by_name[full_name]
            distinct_learners = {
                (source_rows[i]["learner_email"], source_rows[i]["learner_phone_last10"])
                for i in idxs
            }
            if len(distinct_learners) == 1:
                return idxs, "name_fallback"
        return [], "unmatched"

    counts = Counter()
    full_audit_rows = []
    correction_rows = []
    review_rows = []
    unmatched_students_seen = {}

    for batch in batches:
        enrollments = list(batch.students.all())
        total_students = len(enrollments)
        matched_students = 0

        course_student_hits = defaultdict(set)
        teacher_student_hits = defaultdict(set)

        for enr in enrollments:
            stu = enr.student
            idxs, _ = match_student_to_source_indices(stu)
            if not idxs:
                unmatched_students_seen[stu.id] = {
                    "student_id": stu.id,
                    "name": f"{stu.first_name} {stu.last_name}".strip() or stu.username,
                    "email": stu.email or "",
                    "phone": stu.phone_number or "",
                    "batch_id": batch.id,
                }
                continue
            matched_students += 1
            for i in idxs:
                sr = source_rows[i]
                course_student_hits[sr["course_name"]].add(stu.id)
                teacher_student_hits[sr["teacher_key"]].add(stu.id)

        expected_user = None
        if total_students == 0 or matched_students == 0:
            status = "MANUAL_REVIEW_NO_EVIDENCE"
            reason = f"total_students={total_students}, matched_students={matched_students}"
        else:
            chosen_t_key, review_status, course_detail = resolve_batch_teacher(
                matched_students=matched_students,
                course_student_hits=course_student_hits,
                course_teachers=course_teachers,
            )

            candidate_summary = []
            for tk, sids in sorted(teacher_student_hits.items(), key=lambda kv: -len(kv[1])):
                t_info = source_teachers[tk]
                lms_match = teacher_key_to_lms.get(tk)
                lms_desc = f"LMS#{lms_match[0].id}" if lms_match else "UNMATCHED"
                candidate_summary.append(
                    f"{t_info['name']} ({len(sids)}/{matched_students} students, {lms_desc})"
                )

            if chosen_t_key is None:
                status = review_status
                reason = f"{course_detail} | teachers: {'; '.join(candidate_summary)}"
            else:
                lms_match = teacher_key_to_lms.get(chosen_t_key)
                if not lms_match:
                    status = "MANUAL_REVIEW_UNMATCHED_TEACHER"
                    t_info = source_teachers[chosen_t_key]
                    reason = (
                        f"{course_detail} | Source teacher '{t_info['name']}' ({t_info['phone_raw']} / {t_info['email']}) "
                        f"not matched to eligible active LMS user"
                    )
                else:
                    expected_user = lms_match[0]
                    if batch.instructor_id == expected_user.id:
                        status = "CONFIRMED_CORRECT"
                        reason = f"{course_detail} | Matched via {lms_match[1]} ({matched_students}/{total_students} students)"
                    elif batch.instructor_id is None:
                        status = "MISSING_CONFIRMED"
                        reason = f"{course_detail} | NULL -> LMS#{expected_user.id} via {lms_match[1]} ({matched_students}/{total_students} students)"
                    else:
                        status = "WRONG_ASSIGNMENT"
                        reason = f"{course_detail} | LMS#{batch.instructor_id} -> LMS#{expected_user.id} via {lms_match[1]} ({matched_students}/{total_students} students)"

        counts[status] += 1

        curr_inst_name = ""
        if batch.instructor:
            curr_inst_name = (
                f"{batch.instructor.first_name} {batch.instructor.last_name}".strip()
                or batch.instructor.username
            )
        exp_inst_name = ""
        if expected_user:
            exp_inst_name = (
                f"{expected_user.first_name} {expected_user.last_name}".strip()
                or expected_user.username
            )

        row_dict = {
            "batch_id": batch.id,
            "course_id": batch.course_id,
            "course_title": batch.course.title if batch.course else "",
            "batch_type": batch.batch_type,
            "total_students": total_students,
            "matched_students": matched_students,
            "current_instructor_id": batch.instructor_id or "",
            "current_instructor_name": curr_inst_name,
            "expected_instructor_id": expected_user.id if expected_user else "",
            "expected_instructor_name": exp_inst_name,
            "status": status,
            "reason": reason,
        }
        full_audit_rows.append(row_dict)

        if status in ("WRONG_ASSIGNMENT", "MISSING_CONFIRMED"):
            correction_rows.append(row_dict)
        elif status.startswith("MANUAL_REVIEW"):
            review_rows.append(row_dict)

    # 4. Write CSV reports
    fieldnames = [
        "batch_id",
        "course_id",
        "course_title",
        "batch_type",
        "total_students",
        "matched_students",
        "current_instructor_id",
        "current_instructor_name",
        "expected_instructor_id",
        "expected_instructor_name",
        "status",
        "reason",
    ]
    for fname, data in [
        ("livebatch_full_audit.csv", full_audit_rows),
        ("livebatch_corrections.csv", correction_rows),
        ("livebatch_manual_review.csv", review_rows),
    ]:
        with open(out_dir / fname, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fieldnames)
            w.writeheader()
            w.writerows(data)

    with open(out_dir / "unmatched_students.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["student_id", "name", "email", "phone", "batch_id"])
        w.writeheader()
        w.writerows(unmatched_students_seen.values())

    with open(out_dir / "unmatched_source_teachers.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(
            f, fieldnames=["name", "phone_raw", "phone_digits", "phone_last10", "email"]
        )
        w.writeheader()
        for t in unmatched_source_teachers:
            w.writerow({k: t[k] for k in ["name", "phone_raw", "phone_digits", "phone_last10", "email"]})

    # 5. Apply only if --apply is explicitly passed
    updated_count = 0
    if args.apply and correction_rows:
        with transaction.atomic():
            for row in correction_rows:
                LiveBatch.objects.filter(id=row["batch_id"]).update(
                    instructor_id=row["expected_instructor_id"]
                )
                updated_count += 1

    manual_total = (
        counts["MANUAL_REVIEW_NO_EVIDENCE"]
        + counts["MANUAL_REVIEW_MULTI_TEACHER"]
        + counts["MANUAL_REVIEW_MULTI_COURSE"]
        + counts["MANUAL_REVIEW_SPLIT_COURSES"]
        + counts["MANUAL_REVIEW_UNMATCHED_TEACHER"]
    )
    print("=== LIVEBATCH INSTRUCTOR RECONCILIATION REPORT ===")
    print(f"Mode: {'APPLY' if args.apply else 'DRY-RUN (no DB writes)'}")
    print(f"Total batches checked: {len(batches)}")
    print(f"Existing assignments confirmed: {counts['CONFIRMED_CORRECT']}")
    print(f"Existing assignments identified as wrong: {counts['WRONG_ASSIGNMENT']}")
    print(f"NULL assignments with confirmed teacher: {counts['MISSING_CONFIRMED']}")
    print(
        f"Requiring manual review: {manual_total} "
        f"(multi-teacher={counts['MANUAL_REVIEW_MULTI_TEACHER']}, "
        f"multi-course={counts['MANUAL_REVIEW_MULTI_COURSE']}, "
        f"split-courses={counts['MANUAL_REVIEW_SPLIT_COURSES']}, "
        f"unmatched-teacher={counts['MANUAL_REVIEW_UNMATCHED_TEACHER']}, "
        f"no-evidence={counts['MANUAL_REVIEW_NO_EVIDENCE']})"
    )
    print(f"Unmatched LMS students (unique): {len(unmatched_students_seen)}")
    print(f"Unmatched source teachers: {len(unmatched_source_teachers)}")
    print(f"CSV outputs written to: {out_dir}")
    if args.apply:
        print(f"Updated LiveBatch.instructor on {updated_count} batches.")


if __name__ == "__main__":
    main()
