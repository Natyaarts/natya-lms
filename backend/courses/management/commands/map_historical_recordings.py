"""
Map historical recordings (old-platform sessions) to LMS batches and teacher accounts.

    python manage.py map_historical_recordings \
        --workbook "Natya Analytics (5).xlsx" \
        --livebatch-audit backend/data/reconciliation_output/livebatch_full_audit.csv \
        --corrections "regular teachers correct.xlsx" \
        --corrections natya_multi_teacher_batch_corrections.csv \
        [--teacher-accounts reviewed_teacher_accounts.csv] \
        [--output-dir backend/data/historical_mapping_output] \
        [--commit]

Dry run by default: reads only, writes review CSVs. With --commit it replaces the
contents of HistoricalRecordingClassMapping and HistoricalRecordingTeacher (and
nothing else). It never touches recordings, files, batches, enrollments, Zoom,
attendance or LiveBatch.instructor.

Rules (exact identifiers only, never names as identity):

Class -> batch
  The old platform's class id equals the "Course ID" of its "Student Courses"
  sheet, which lists each class's learners with phone/email. A learner counts
  only if their email and/or phone point to exactly one LMS account. A class is
  CONFIRMED to batch B only when
    * all its recording sessions are stored under one course,
    * exactly one LiveBatch of that course contains any of its learners
      (via LiveBatchStudent),
    * those matched learners are at least half of its resolved learners, and
    * no other class is confirmed to the same batch (no implicit merging).
  Anything else is AMBIGUOUS or UNRESOLVED and grants nothing.

Legacy teacher -> LMS user ("verified account mapping")
  For each confirmed class taught by a single legacy teacher across all its
  sessions, the reconciliation audit's expected instructor for the mapped batch
  (resolved there by teacher phone/email with student evidence) is a vote. A
  legacy teacher is verified when all votes (plus any reviewed
  --teacher-accounts rows) agree on one account, no other legacy teacher claims
  that account, and the account is an active teacher/mentor outside the
  protected ids.

Session teachers
  * Class listed in a correction file with a blank decision: pending, no
    teachers (a blank reply is never treated as approval).
  * Class with a confirmed correction: the corrected teacher(s), applied to all
    of the class's sessions. Each corrected name must equal the source display
    name of exactly one legacy teacher who taught that same class; that legacy
    id must be verified. If any part fails, the class gets no teachers.
  * Otherwise: the verified legacy teacher who conducted each session.
"""
import csv
import os
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from courses.historical_recording_access import PROTECTED_USER_IDS
from courses.models import (
    HistoricalRecordingClassMapping,
    HistoricalRecordingSession,
    HistoricalRecordingTeacher,
    LiveBatch,
    LiveBatchStudent,
)
from users.models import User

CONFIRMED = HistoricalRecordingClassMapping.Status.CONFIRMED
AMBIGUOUS = HistoricalRecordingClassMapping.Status.AMBIGUOUS
UNRESOLVED = HistoricalRecordingClassMapping.Status.UNRESOLVED

# Reconciliation audit statuses whose expected_instructor_id was established by evidence.
AUDIT_TRUSTED_STATUSES = {"MISSING_CONFIRMED", "CONFIRMED_CORRECT", "WRONG_ASSIGNMENT"}


# ---------------------------------------------------------------------------
# Normalisation helpers
# ---------------------------------------------------------------------------

def _s(value):
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return str(value).strip()


def norm_email(value):
    value = _s(value).lower()
    return value if "@" in value else ""


def norm_phone(value):
    """(exact digits, last 10 digits or '') -- last-10 only for numbers with >= 10 digits."""
    digits = re.sub(r"\D", "", _s(value).lstrip("'"))
    if not digits:
        return "", ""
    return digits, (digits[-10:] if len(digits) >= 10 else "")


def norm_name(value):
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", _s(value).lower())).strip()


def split_correction(text):
    return [p for p in (norm_name(x) for x in re.split(r"[,/&+;\n]|\band\b", _s(text), flags=re.I)) if p]


# ---------------------------------------------------------------------------
# Source readers
# ---------------------------------------------------------------------------

def _sheet_dicts(ws):
    rows = ws.iter_rows(values_only=True)
    header = None
    for row in rows:
        if header is None:
            if row and any(c is not None for c in row):
                header = [_s(c) for c in row]
            continue
        if row and any(c is not None for c in row):
            yield dict(zip(header, row))


def read_workbook(path):
    import openpyxl

    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    for name in ("All Sessions", "Student Courses"):
        if name not in wb.sheetnames:
            wb.close()
            raise CommandError(f"Workbook is missing the '{name}' sheet.")
    try:
        sessions = list(_sheet_dicts(wb["All Sessions"]))
        learners = list(_sheet_dicts(wb["Student Courses"]))
    finally:
        wb.close()
    return sessions, learners


def read_tabular(path):
    """Rows (dicts) from a CSV or every sheet of an XLSX."""
    if path.lower().endswith((".xlsx", ".xlsm")):
        import openpyxl

        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        out = []
        try:
            for ws in wb.worksheets:
                out.extend(_sheet_dicts(ws))
        finally:
            wb.close()
        return out
    with open(path, encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def read_corrections(paths):
    """
    class_id -> list of confirmed correction texts. A class listed only with
    blank decisions maps to an empty list (= decision pending). Rows without an
    exact Class ID (e.g. sheets keyed by course name) are ignored.
    """
    out = {}
    for path in paths:
        for row in read_tabular(path):
            cid = _s(row.get("Class ID"))
            if not cid:
                continue
            note_key = next((k for k in row if k and k.lower().startswith("correct teacher")), None)
            note = _s(row.get(note_key)) if note_key else ""
            out.setdefault(cid, [])
            if note:
                out[cid].append(note)
    return out


# ---------------------------------------------------------------------------
# Planning (pure apart from read-only queries)
# ---------------------------------------------------------------------------

@dataclass
class ClassResult:
    source_class_id: str
    status: str = UNRESOLVED
    reason: str = ""
    batch_id: int = None
    course_id: int = None
    recording_sessions: int = 0
    source_learners: int = 0
    resolved_learners: int = 0
    matched_learners: int = 0
    teacher_status: str = ""


@dataclass
class Plan:
    classes: dict = field(default_factory=dict)          # class_id -> ClassResult
    teacher_accounts: dict = field(default_factory=dict)  # legacy id -> (status, user_id, evidence)
    session_teachers: list = field(default_factory=list)  # (session, user_id, source, legacy_id)


class _UserIndex:
    def __init__(self):
        self.by_email = defaultdict(set)
        self.by_phone = defaultdict(set)
        self.by_last10 = defaultdict(set)
        self.users = {}
        for u in User.objects.values("id", "email", "phone_number", "is_active", "is_teacher", "is_mentor"):
            self.users[u["id"]] = u
            email = norm_email(u["email"])
            if email:
                self.by_email[email].add(u["id"])
            digits, last10 = norm_phone(u["phone_number"])
            if digits:
                self.by_phone[digits].add(u["id"])
            if last10:
                self.by_last10[last10].add(u["id"])

    def resolve(self, email, phone):
        """Exactly one LMS user consistent with every contact given, else None."""
        candidates = set()
        email = norm_email(email)
        if email:
            candidates |= self.by_email.get(email, set())
        digits, last10 = norm_phone(phone)
        if digits:
            phone_ids = self.by_phone.get(digits) or (self.by_last10.get(last10, set()) if last10 else set())
            candidates |= phone_ids
        if len(candidates) != 1:
            return None
        (uid,) = candidates
        return None if uid in PROTECTED_USER_IDS else uid

    def teacher_ok(self, uid):
        u = self.users.get(uid)
        return bool(u and u["is_active"] and (u["is_teacher"] or u["is_mentor"]) and uid not in PROTECTED_USER_IDS)


def build_plan(source_sessions, source_learners, audit_rows, corrections, manual_accounts):
    plan = Plan()
    users = _UserIndex()

    # --- source indexes --------------------------------------------------------
    class_teachers = defaultdict(set)    # class -> legacy teacher ids (all sessions)
    session_teacher = defaultdict(set)   # session id -> legacy teacher ids
    legacy_names = defaultdict(set)      # legacy id -> normalised display names
    for row in source_sessions:
        cid, sid, tid = _s(row.get("class_id")), _s(row.get("session_id")), _s(row.get("teacher_user_id"))
        if not cid or not tid:
            continue
        class_teachers[cid].add(tid)
        if sid:
            session_teacher[sid].add(tid)
        name = norm_name(row.get("teacher_name"))
        if name:
            legacy_names[tid].add(name)

    class_learners = defaultdict(dict)   # class -> learner key -> (email, phone)
    for row in source_learners:
        cid = _s(row.get("Course ID"))
        if not cid:
            continue
        email, phone = row.get("Student Email"), row.get("Student Phone")
        key = _s(row.get("Student ID")) or f"{norm_email(email)}|{norm_phone(phone)[0]}"
        class_learners[cid][key] = (email, phone)

    # --- LMS indexes (read only) -------------------------------------------------
    batch_course = dict(LiveBatch.objects.values_list("id", "course_id"))
    batches_by_course = defaultdict(set)
    for bid, course_id in batch_course.items():
        batches_by_course[course_id].add(bid)
    batch_students = defaultdict(set)
    for bid, sid in LiveBatchStudent.objects.values_list("batch_id", "student_id"):
        batch_students[bid].add(sid)

    rec_sessions = list(
        HistoricalRecordingSession.objects.exclude(source_class_id="")
        .values("id", "source_session_id", "source_class_id", "course_id")
    )
    sessions_by_class = defaultdict(list)
    for s in rec_sessions:
        sessions_by_class[s["source_class_id"].strip()].append(s)

    # --- class -> batch ----------------------------------------------------------
    for cid, sessions in sessions_by_class.items():
        res = ClassResult(cid, recording_sessions=len(sessions))
        plan.classes[cid] = res
        courses = {s["course_id"] for s in sessions}
        learners = class_learners.get(cid, {})
        res.source_learners = len(learners)
        resolved = {users.resolve(e, p) for e, p in learners.values()} - {None}
        res.resolved_learners = len(resolved)
        if len(courses) != 1:
            res.status, res.reason = AMBIGUOUS, f"recording sessions span {len(courses)} courses"
            continue
        (res.course_id,) = courses
        if not learners:
            res.reason = "no learners listed for this class id in the source"
            continue
        if not resolved:
            res.reason = "no source learner resolves to exactly one LMS account"
            continue
        candidates = batches_by_course.get(res.course_id, set())
        if not candidates:
            res.reason = "course has no live batch"
            continue
        hits = {b: len(batch_students[b] & resolved) for b in candidates}
        hits = {b: n for b, n in hits.items() if n}
        if not hits:
            res.reason = "no resolved learner belongs to a batch of this course"
            continue
        if len(hits) > 1:
            res.status, res.reason = AMBIGUOUS, f"learners split across {len(hits)} batches of the course"
            continue
        ((bid, matched),) = hits.items()
        res.matched_learners = matched
        if matched * 2 < len(resolved):
            res.reason = f"weak evidence: {matched}/{len(resolved)} resolved learners in batch {bid}"
            continue
        res.status, res.batch_id, res.reason = CONFIRMED, bid, f"{matched}/{len(resolved)} resolved learners in batch"

    claims = Counter(r.batch_id for r in plan.classes.values() if r.status == CONFIRMED)
    for r in plan.classes.values():
        if r.status == CONFIRMED and claims[r.batch_id] > 1:
            r.reason = f"batch {r.batch_id} matched by {claims[r.batch_id]} source classes"
            r.status, r.batch_id = AMBIGUOUS, None

    # --- legacy teacher -> LMS user ------------------------------------------------
    audit = {}
    for row in audit_rows:
        try:
            bid = int(_s(row.get("batch_id")))
        except ValueError:
            continue
        status = _s(row.get("status"))
        exp = _s(row.get("expected_instructor_id"))
        entry = (status, int(exp) if exp.isdigit() else None, _s(row.get("course_id")))
        if bid in audit and audit[bid] != entry:
            audit[bid] = ("CONFLICT", None, "")
        else:
            audit[bid] = entry

    votes = defaultdict(Counter)
    for r in plan.classes.values():
        if r.status != CONFIRMED or len(class_teachers.get(r.source_class_id, ())) != 1:
            continue
        (tid,) = class_teachers[r.source_class_id]
        status, exp, course = audit.get(r.batch_id, ("", None, ""))
        if status in AUDIT_TRUSTED_STATUSES and exp and course == str(batch_course.get(r.batch_id)):
            votes[tid][exp] += 1

    legacy_ids = set(votes) | set(manual_accounts) | {t for ts in class_teachers.values() for t in ts}
    proposed = {}
    for tid in legacy_ids:
        cands = set(votes.get(tid, {}))
        if tid in manual_accounts:
            cands.add(manual_accounts[tid])
        evidence = f"batch votes={sum(votes[tid].values())}" + ("; reviewed account row" if tid in manual_accounts else "")
        if not cands:
            plan.teacher_accounts[tid] = ("UNRESOLVED", None, "no confirmed single-teacher class with audit evidence")
        elif len(cands) > 1:
            plan.teacher_accounts[tid] = ("AMBIGUOUS", None, f"evidence points to {len(cands)} accounts; {evidence}")
        else:
            (uid,) = cands
            if uid in PROTECTED_USER_IDS:
                plan.teacher_accounts[tid] = ("REJECTED", None, "protected account")
            elif not users.teacher_ok(uid):
                plan.teacher_accounts[tid] = ("REJECTED", None, "account missing, inactive or not a teacher/mentor")
            else:
                proposed[tid] = (uid, evidence)
    owners = Counter(uid for uid, _ in proposed.values())
    verified = {}
    for tid, (uid, evidence) in proposed.items():
        if owners[uid] > 1:
            plan.teacher_accounts[tid] = ("AMBIGUOUS", None, "account claimed by several legacy teachers")
        else:
            plan.teacher_accounts[tid] = ("VERIFIED", uid, evidence)
            verified[tid] = uid

    # --- session teachers ------------------------------------------------------------
    for r in plan.classes.values():
        if r.status != CONFIRMED:
            continue
        sessions = sessions_by_class[r.source_class_id]
        notes = corrections.get(r.source_class_id)
        if notes is not None and not notes:
            r.teacher_status = "CORRECTION_PENDING"
            continue
        if notes:
            name_to_ids = defaultdict(set)
            for tid in class_teachers.get(r.source_class_id, ()):
                for n in legacy_names.get(tid, ()):
                    name_to_ids[n].add(tid)
            parts = [p for note in notes for p in split_correction(note)]
            chosen, ok = set(), bool(parts)
            for p in parts:
                ids = name_to_ids.get(p, set())
                if len(ids) != 1 or next(iter(ids)) not in verified:
                    ok = False
                    break
                chosen |= ids
            if not ok:
                r.teacher_status = "CORRECTION_UNRESOLVED"
                continue
            r.teacher_status = f"CORRECTION ({len(chosen)} teacher)"
            for s in sessions:
                for tid in sorted(chosen):
                    plan.session_teachers.append((s, verified[tid], HistoricalRecordingTeacher.Source.CORRECTION, tid))
            continue

        mapped = 0
        for s in sessions:
            tids = session_teacher.get(s["source_session_id"], set())
            if len(tids) == 1 and next(iter(tids)) in verified:
                (tid,) = tids
                plan.session_teachers.append((s, verified[tid], HistoricalRecordingTeacher.Source.SESSION_TEACHER, tid))
                mapped += 1
        r.teacher_status = f"SESSION_TEACHER ({mapped}/{len(sessions)} sessions)"

    return plan


# ---------------------------------------------------------------------------
# Command
# ---------------------------------------------------------------------------

class Command(BaseCommand):
    help = "Map historical recordings to LMS batches and teacher accounts (dry run unless --commit)."

    def add_arguments(self, parser):
        parser.add_argument("--workbook", required=True, help="Old-platform export with 'All Sessions' and 'Student Courses'.")
        parser.add_argument("--livebatch-audit", action="append", default=[], help="reconcile_livebatch_instructors output CSV (repeatable).")
        parser.add_argument("--corrections", action="append", default=[], help="Teacher correction CSV/XLSX with 'Class ID' (repeatable).")
        parser.add_argument("--teacher-accounts", help="Reviewed CSV: source_teacher_user_id,user_id")
        parser.add_argument("--output-dir", default=os.path.join("data", "historical_mapping_output"))
        parser.add_argument("--expect-sessions", type=int, help="Fail unless exactly this many recording sessions exist (e.g. 2192).")
        parser.add_argument("--commit", action="store_true", help="Write the mapping tables (default: dry run).")

    def handle(self, *args, **opts):
        for p in [opts["workbook"], *opts["livebatch_audit"], *opts["corrections"], *filter(None, [opts["teacher_accounts"]])]:
            if not os.path.isfile(p):
                raise CommandError(f"File not found: {p}")

        source_sessions, source_learners = read_workbook(opts["workbook"])
        audit_rows = [row for p in opts["livebatch_audit"] for row in read_tabular(p)]
        corrections = read_corrections(opts["corrections"])
        manual = {}
        if opts["teacher_accounts"]:
            for row in read_tabular(opts["teacher_accounts"]):
                tid, uid = _s(row.get("source_teacher_user_id")), _s(row.get("user_id"))
                if tid and uid.isdigit():
                    manual[tid] = int(uid)

        plan = build_plan(source_sessions, source_learners, audit_rows, corrections, manual)
        session_rows = self._session_rows(plan)
        total = HistoricalRecordingSession.objects.count()
        if len(session_rows) != total or len({row[0] for row in session_rows}) != total:
            raise CommandError(f"Session report covers {len(session_rows)} rows but {total} sessions exist.")
        if opts["expect_sessions"] is not None and total != opts["expect_sessions"]:
            raise CommandError(f"Expected {opts['expect_sessions']} recording sessions, found {total}.")
        self._write_reports(plan, session_rows, opts["output_dir"])
        self._summary(plan, session_rows)

        if not opts["commit"]:
            self.stdout.write(self.style.WARNING("DRY RUN: no database changes. Re-run with --commit after review."))
            return
        with transaction.atomic():
            HistoricalRecordingTeacher.objects.all().delete()
            HistoricalRecordingClassMapping.objects.all().delete()
            HistoricalRecordingClassMapping.objects.bulk_create([
                HistoricalRecordingClassMapping(
                    source_class_id=r.source_class_id,
                    batch_id=r.batch_id if r.status == CONFIRMED else None,
                    status=r.status,
                    reason=r.reason[:255],
                    source_learners=r.source_learners,
                    resolved_learners=r.resolved_learners,
                    matched_learners=r.matched_learners,
                )
                for r in plan.classes.values()
            ])
            HistoricalRecordingTeacher.objects.bulk_create(
                [
                    HistoricalRecordingTeacher(session_id=s["id"], teacher_id=uid, source=src, source_teacher_user_id=tid)
                    for s, uid, src, tid in plan.session_teachers
                    if uid not in PROTECTED_USER_IDS
                ],
                ignore_conflicts=True,
            )
        self.stdout.write(self.style.SUCCESS("Committed historical recording mappings."))

    @staticmethod
    def _session_rows(plan):
        """One row per HistoricalRecordingSession: (pk, source_session_id, class_id, status, reason, batch_id, teachers)."""
        teachers = defaultdict(set)
        for s, uid, _src, _tid in plan.session_teachers:
            teachers[s["id"]].add(uid)
        rows = []
        for s in HistoricalRecordingSession.objects.order_by("id").values("id", "source_session_id", "source_class_id"):
            cid = s["source_class_id"].strip()
            r = plan.classes.get(cid) if cid else None
            if r is None:
                rows.append((s["id"], s["source_session_id"], cid, UNRESOLVED, "no source class id", "", 0))
            else:
                rows.append((s["id"], s["source_session_id"], cid, r.status, r.reason, r.batch_id or "", len(teachers[s["id"]])))
        return rows

    def _write_reports(self, plan, session_rows, out_dir):
        os.makedirs(out_dir, exist_ok=True)
        with open(os.path.join(out_dir, "session_mappings.csv"), "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["session_pk", "source_session_id", "source_class_id", "status", "reason", "batch_id", "confirmed_teachers"])
            w.writerows(session_rows)
        with open(os.path.join(out_dir, "class_mappings.csv"), "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["source_class_id", "status", "reason", "batch_id", "course_id", "recording_sessions",
                        "source_learners", "resolved_learners", "matched_learners", "teacher_status"])
            for r in sorted(plan.classes.values(), key=lambda r: (r.status, r.source_class_id)):
                w.writerow([r.source_class_id, r.status, r.reason, r.batch_id or "", r.course_id or "",
                            r.recording_sessions, r.source_learners, r.resolved_learners, r.matched_learners, r.teacher_status])
        with open(os.path.join(out_dir, "teacher_accounts.csv"), "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["source_teacher_user_id", "status", "user_id", "evidence"])
            for tid, (status, uid, evidence) in sorted(plan.teacher_accounts.items()):
                w.writerow([tid, status, uid or "", evidence])
        with open(os.path.join(out_dir, "session_teachers.csv"), "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["session_pk", "source_session_id", "source_class_id", "teacher_user_id", "source", "source_teacher_user_id"])
            for s, uid, src, tid in plan.session_teachers:
                w.writerow([s["id"], s["source_session_id"], s["source_class_id"], uid, src, tid])
        self.stdout.write(f"Reports written to {os.path.abspath(out_dir)}")

    def _summary(self, plan, session_rows):
        by_status = Counter(r.status for r in plan.classes.values())
        sess = Counter(row[3] for row in session_rows)
        self.stdout.write("Classes:  " + ", ".join(f"{k}={by_status.get(k, 0)}" for k in (CONFIRMED, AMBIGUOUS, UNRESOLVED)))
        self.stdout.write(
            "Sessions: " + ", ".join(f"{k}={sess.get(k, 0)}" for k in (CONFIRMED, AMBIGUOUS, UNRESOLVED))
            + f" (total {len(session_rows)}; every session accounted for)"
        )
        no_class = sum(1 for row in session_rows if not row[2])
        self.stdout.write(f"Sessions without a source class id (never visible to non-admins): {no_class}")
        ta = Counter(v[0] for v in plan.teacher_accounts.values())
        self.stdout.write("Legacy teacher accounts: " + ", ".join(f"{k}={v}" for k, v in sorted(ta.items())))
        lms_teachers = {v[1] for v in plan.teacher_accounts.values() if v[0] == "VERIFIED"}
        self.stdout.write(f"Verified LMS teacher accounts: {len(lms_teachers)}")
        ts = Counter(r.teacher_status.split(" ")[0] for r in plan.classes.values() if r.status == CONFIRMED)
        self.stdout.write("Confirmed classes by teacher source: " + ", ".join(f"{k}={v}" for k, v in sorted(ts.items())))
        with_teacher = sum(1 for row in session_rows if row[6])
        self.stdout.write(f"Sessions with >=1 confirmed teacher: {with_teacher}; teacher rows: {len(plan.session_teachers)}")
