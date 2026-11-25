"""
Teacher salary: monthly teaching-pay statements built from live classes.

Per completed class taught by the teacher (the effective instructor: a
substitute, when assigned, is the one paid):

    hours pay   = hourly rate x paid minutes / 60
                  (hourly rate = the rate card tier matching the number of
                  attending students)
    class pay   = per_class_amount
    student pay = per_student_amount x attending students
    line amount = hours pay + class pay + student pay

  * Paid minutes = actual Zoom meeting time (meeting.started/ended webhooks),
    capped at the scheduled duration (setting). No Zoom time -> by default
    nothing is guessed: the line is flagged NO_ZOOM_TIME and pays 0 until the
    admin enters minutes; the admin can instead choose to pay the scheduled
    duration (setting).
  * Attending students = attendance records marked PRESENT (+ LATE / EXCUSED
    per the settings), excluding staff, teaching staff and excluded/test
    accounts (the same exclusion rule the Zoom attendance pipeline uses). The
    admin can correct the count per line while the statement is a draft.
  * These rules live in TeacherPayrollSettings (admin-editable) and apply
    whenever a draft is calculated; approved/paid statements never change.
  * Rate card: the teacher's card for the class's course, else their default
    card (course empty). No active card -> NO_RATE_CARD, pays 0.

Statements are DRAFT -> APPROVED -> PAID (or CANCELLED). A draft can be
regenerated any time; admin corrections are kept per class. Approval is
refused while any included line has a blocking flag. Nothing here moves
money -- PAID records a payment the admin made outside the system.
"""
import calendar
import datetime
from decimal import ROUND_HALF_UP, Decimal
from zoneinfo import ZoneInfo

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

PAYROLL_TZ = ZoneInfo("Asia/Kolkata")
CENT = Decimal("0.01")

# Owner/admin/test accounts that are never paid as teachers
# (same protected set used for historical recordings and Zoom host sync).
PAYROLL_EXCLUDED_USER_IDS = frozenset({1, 25, 39, 96})


# Flags. Blocking flags must be resolved (or the line excluded) before approval.
NO_RATE_CARD = "NO_RATE_CARD"
NO_ZOOM_TIME = "NO_ZOOM_TIME"
NO_TIER_FOR_STUDENTS = "NO_TIER_FOR_STUDENTS"
BLOCKING_FLAGS = frozenset({NO_RATE_CARD, NO_ZOOM_TIME, NO_TIER_FOR_STUDENTS})
ZOOM_TIME_CAPPED = "ZOOM_TIME_CAPPED"
SCHEDULED_TIME_USED = "SCHEDULED_TIME_USED"
ATTENDANCE_NOT_FINALIZED = "ATTENDANCE_NOT_FINALIZED"
NO_STUDENTS = "NO_STUDENTS"
MINUTES_OVERRIDDEN = "MINUTES_OVERRIDDEN"
STUDENTS_OVERRIDDEN = "STUDENTS_OVERRIDDEN"
SUBSTITUTE = "SUBSTITUTE"
EXCLUDED = "EXCLUDED"


class PayrollError(ValueError):
    """A request the payroll rules refuse (shown to the admin as-is)."""


def _money(value):
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


def month_period(year, month):
    """(first_date, last_date, start_utc, end_utc_exclusive) of a calendar month in Asia/Kolkata."""
    if not (1 <= int(month) <= 12) or not (2000 <= int(year) <= 2100):
        raise PayrollError("Invalid month.")
    year, month = int(year), int(month)
    first = datetime.date(year, month, 1)
    last = datetime.date(year, month, calendar.monthrange(year, month)[1])
    start = datetime.datetime.combine(first, datetime.time.min, tzinfo=PAYROLL_TZ)
    end = datetime.datetime.combine(last + datetime.timedelta(days=1), datetime.time.min, tzinfo=PAYROLL_TZ)
    return first, last, start.astimezone(datetime.timezone.utc), end.astimezone(datetime.timezone.utc)


def parse_month(value):
    """'YYYY-MM' -> (year, month)."""
    try:
        year, month = str(value).strip().split("-")
        return int(year), int(month)
    except (TypeError, ValueError):
        raise PayrollError("Month must be in YYYY-MM format.")


def is_payable_teacher(user):
    return bool(
        user is not None
        and user.pk not in PAYROLL_EXCLUDED_USER_IDS
        and (getattr(user, "is_teacher", False) or getattr(user, "is_mentor", False))
    )


def _taught_by(teacher_id):
    """LiveClass filter matching LiveClass.effective_instructor_id == teacher_id."""
    return (
        Q(substitute_instructor_id=teacher_id)
        | Q(substitute_instructor__isnull=True, instructor_id=teacher_id)
        | Q(substitute_instructor__isnull=True, instructor__isnull=True, batch__instructor_id=teacher_id)
    )


def completed_classes(start_utc, end_utc):
    from courses.models import LiveClass

    return (
        LiveClass.objects
        .filter(status=LiveClass.ClassStatus.COMPLETED, scheduled_start__gte=start_utc, scheduled_start__lt=end_utc)
        .select_related("course", "batch")
        .order_by("scheduled_start", "id")
    )


def teachers_with_classes(year, month):
    """Ids of payable teachers who were the effective instructor of a completed class that month."""
    from django.contrib.auth import get_user_model

    _, _, start, end = month_period(year, month)
    ids = {lc.effective_instructor_id for lc in completed_classes(start, end)}
    ids.discard(None)
    users = get_user_model().objects.filter(pk__in=ids)
    return sorted(u.pk for u in users if is_payable_teacher(u))


def _claimed_class_ids(exclude_statement_id=None):
    """Classes already on a live (non-cancelled) statement."""
    from .models import TeacherPayrollLine, TeacherPayrollStatement

    qs = TeacherPayrollLine.objects.filter(live_class__isnull=False).exclude(
        statement__status=TeacherPayrollStatement.Status.CANCELLED
    )
    if exclude_statement_id:
        qs = qs.exclude(statement_id=exclude_statement_id)
    return set(qs.values_list("live_class_id", flat=True))


def payroll_settings():
    from .models import TeacherPayrollSettings

    return TeacherPayrollSettings.get_solo()


def counted_attendees(live_class, statuses=None):
    """Students counted as attending (statuses per the settings), never staff, teaching staff or excluded accounts."""
    from courses.models import Attendance
    from courses.services.attendance_identity import excluded_user_reason

    statuses = statuses or payroll_settings().counted_statuses()
    records = (
        Attendance.objects
        .filter(live_class=live_class, status__in=statuses)
        .select_related("student")
    )
    return sum(1 for r in records if excluded_user_reason(r.student, live_class) is None)


def zoom_minutes(live_class):
    from courses.services.conducted_time import get_conducted_time

    info = get_conducted_time(live_class)
    if not info or info.get("in_progress") or info.get("minutes") is None:
        return None
    return int(info["minutes"])


def active_rate_cards(teacher_id):
    """{course_id_or_None: card} of the teacher's active cards, tiers prefetched."""
    from .models import TeacherRateCard

    cards = TeacherRateCard.objects.filter(teacher_id=teacher_id, is_active=True).prefetch_related("tiers")
    return {card.course_id: card for card in cards}


def _tiers_snapshot(card):
    return [
        {"min_students": t.min_students, "max_students": t.max_students, "hourly_rate": str(t.hourly_rate)}
        for t in sorted(card.tiers.all(), key=lambda t: t.min_students)
    ]


def compute_line(line, settings=None):
    """(Re)compute a line's paid values and flags from its snapshot + admin corrections. Does not save."""
    from .models import TeacherPayrollSettings, tier_label

    settings = settings or payroll_settings()
    flags = []
    if line.is_substitute:
        flags.append(SUBSTITUTE)
    if not line.attendance_finalized:
        flags.append(ATTENDANCE_NOT_FINALIZED)

    # Minutes: admin value, else Zoom time (capped at the schedule if set),
    # else the scheduled duration or "unknown", per the settings.
    if line.minutes_override is not None:
        minutes = line.minutes_override
        flags.append(MINUTES_OVERRIDDEN)
    elif line.zoom_minutes is not None:
        minutes = line.zoom_minutes
        if settings.cap_zoom_time_at_schedule and line.scheduled_minutes and line.zoom_minutes > line.scheduled_minutes:
            minutes = line.scheduled_minutes
            flags.append(ZOOM_TIME_CAPPED)
    elif settings.missing_zoom_time == TeacherPayrollSettings.MissingZoomTime.USE_SCHEDULED and line.scheduled_minutes:
        minutes = line.scheduled_minutes
        flags.append(SCHEDULED_TIME_USED)
    else:
        minutes = None
        flags.append(NO_ZOOM_TIME)

    if line.attendee_count_override is not None:
        students = line.attendee_count_override
        flags.append(STUDENTS_OVERRIDDEN)
    else:
        students = line.attendee_count
    if students == 0:
        flags.append(NO_STUDENTS)

    hourly = Decimal("0")
    label = ""
    if line.rate_card_id is None:
        flags.append(NO_RATE_CARD)
    elif line.rate_tiers_snapshot:
        tier = next(
            (t for t in line.rate_tiers_snapshot
             if students >= t["min_students"] and (t["max_students"] is None or students <= t["max_students"])),
            None,
        )
        if tier is None:
            flags.append(NO_TIER_FOR_STUDENTS)
        else:
            hourly = Decimal(tier["hourly_rate"])
            label = tier_label(tier["min_students"], tier["max_students"])

    line.paid_minutes = minutes or 0
    line.students_counted = students
    line.hourly_rate = _money(hourly)
    line.tier_label = label

    blocked = any(f in BLOCKING_FLAGS for f in flags)
    if line.is_excluded or blocked:
        line.hours_amount = line.class_amount = line.student_amount = line.amount = Decimal("0.00")
    else:
        line.hours_amount = _money(hourly * Decimal(line.paid_minutes) / Decimal(60))
        line.class_amount = _money(line.per_class_amount)
        line.student_amount = _money(Decimal(line.per_student_amount) * students)
        line.amount = line.hours_amount + line.class_amount + line.student_amount
    if line.is_excluded:
        flags.append(EXCLUDED)
    line.flags = flags
    return line


def line_is_blocking(line):
    return not line.is_excluded and any(f in BLOCKING_FLAGS for f in (line.flags or []))


def recompute_totals(statement):
    lines = list(statement.lines.all())
    included = [ln for ln in lines if not ln.is_excluded]
    statement.class_count = len(included)
    statement.total_minutes = sum(ln.paid_minutes for ln in included)
    statement.classes_amount = _money(sum((ln.amount for ln in included), Decimal("0")))
    statement.net_amount = _money(statement.classes_amount + Decimal(statement.adjustment_amount))
    statement.save(update_fields=["class_count", "total_minutes", "classes_amount", "net_amount", "updated_at"])
    return statement


def _build_line(statement, live_class, teacher_id, cards, statuses):
    from .models import TeacherPayrollLine

    card = cards.get(live_class.course_id) or cards.get(None)
    line = TeacherPayrollLine(
        statement=statement,
        live_class=live_class,
        course=live_class.course,
        batch_id_snapshot=live_class.batch_id,
        class_title=(live_class.title or "")[:255],
        course_title=(live_class.course.title if live_class.course_id else "")[:255],
        scheduled_start=live_class.scheduled_start,
        is_substitute=live_class.substitute_instructor_id == teacher_id,
        scheduled_minutes=live_class.duration_minutes or 0,
        zoom_minutes=zoom_minutes(live_class),
        attendee_count=counted_attendees(live_class, statuses),
        attendance_finalized=live_class.attendance_finalized_at is not None,
        rate_card=card,
        rate_tiers_snapshot=_tiers_snapshot(card) if card else [],
        per_class_amount=card.per_class_amount if card else Decimal("0"),
        per_student_amount=card.per_student_amount if card else Decimal("0"),
    )
    return line


def generate_statement(teacher, year, month, actor=None):
    """
    Create (or rebuild, if still a draft) the teacher's statement for the month.
    Returns (statement, "created" | "regenerated"). Admin corrections on an
    existing draft are carried over per class.
    """
    from .models import TeacherPayrollLine, TeacherPayrollStatement

    if not is_payable_teacher(teacher):
        raise PayrollError("This account is not a payable teacher.")
    first, last, start, end = month_period(year, month)

    with transaction.atomic():
        statement = (
            TeacherPayrollStatement.objects.select_for_update()
            .filter(teacher=teacher, period_start=first)
            .exclude(status=TeacherPayrollStatement.Status.CANCELLED)
            .first()
        )
        if statement and statement.status != TeacherPayrollStatement.Status.DRAFT:
            raise PayrollError(
                f"The {first:%B %Y} statement is already {statement.get_status_display().lower()}. "
                "Cancel it first to rebuild it."
            )

        claimed = _claimed_class_ids(exclude_statement_id=statement.pk if statement else None)
        classes = [lc for lc in completed_classes(start, end).filter(_taught_by(teacher.pk)) if lc.pk not in claimed]
        if statement is None and not classes:
            raise PayrollError(f"No completed classes found for this teacher in {first:%B %Y}.")

        outcome = "regenerated"
        corrections = {}
        if statement is None:
            statement = TeacherPayrollStatement.objects.create(
                teacher=teacher, period_start=first, period_end=last, created_by=actor,
            )
            outcome = "created"
        else:
            for ln in statement.lines.all():
                if ln.live_class_id:
                    corrections[ln.live_class_id] = (
                        ln.minutes_override, ln.attendee_count_override, ln.is_excluded, ln.admin_note,
                    )
            statement.lines.all().delete()

        cards = active_rate_cards(teacher.pk)
        settings = payroll_settings()
        statuses = settings.counted_statuses()
        lines = []
        for lc in classes:
            line = _build_line(statement, lc, teacher.pk, cards, statuses)
            if lc.pk in corrections:
                line.minutes_override, line.attendee_count_override, line.is_excluded, line.admin_note = corrections[lc.pk]
            lines.append(compute_line(line, settings))
        TeacherPayrollLine.objects.bulk_create(lines)

        statement.generated_at = timezone.now()
        statement.save(update_fields=["generated_at", "updated_at"])
        recompute_totals(statement)
    return statement, outcome


def generate_month(year, month, actor=None, teacher_ids=None):
    """Generate statements for the given teachers (default: everyone who taught that month)."""
    from django.contrib.auth import get_user_model

    User = get_user_model()
    ids = list(teacher_ids) if teacher_ids else teachers_with_classes(year, month)
    results = []
    for teacher in User.objects.filter(pk__in=ids).order_by("pk"):
        try:
            statement, outcome = generate_statement(teacher, year, month, actor)
            results.append({"teacher_id": teacher.pk, "statement_id": statement.pk, "result": outcome})
        except PayrollError as exc:
            results.append({"teacher_id": teacher.pk, "statement_id": None, "result": "skipped", "reason": str(exc)})
    missing = set(ids) - {r["teacher_id"] for r in results}
    for teacher_id in sorted(missing):
        results.append({"teacher_id": teacher_id, "statement_id": None, "result": "skipped", "reason": "Teacher not found."})
    return results


def _require_draft(statement):
    from .models import TeacherPayrollStatement

    if statement.status != TeacherPayrollStatement.Status.DRAFT:
        raise PayrollError("Only a draft statement can be changed.")


def update_line(line, *, minutes_override=..., attendee_count_override=..., is_excluded=..., admin_note=...):
    """Apply admin corrections to one line of a draft (Ellipsis = leave unchanged)."""
    from .models import TeacherPayrollLine, TeacherPayrollStatement

    with transaction.atomic():
        statement = TeacherPayrollStatement.objects.select_for_update().get(pk=line.statement_id)
        _require_draft(statement)
        line = TeacherPayrollLine.objects.select_for_update().get(pk=line.pk)
        if minutes_override is not ...:
            if minutes_override is not None and line.scheduled_minutes and minutes_override > line.scheduled_minutes * 3:
                raise PayrollError("Minutes look wrong: more than three times the scheduled duration.")
            line.minutes_override = minutes_override
        if attendee_count_override is not ...:
            line.attendee_count_override = attendee_count_override
        if is_excluded is not ...:
            line.is_excluded = bool(is_excluded)
        if admin_note is not ...:
            line.admin_note = (admin_note or "")[:255]
        compute_line(line)
        line.save()
        recompute_totals(statement)
    return line


def set_adjustment(statement, *, amount=..., reason=..., notes=...):
    from .models import TeacherPayrollStatement

    with transaction.atomic():
        statement = TeacherPayrollStatement.objects.select_for_update().get(pk=statement.pk)
        _require_draft(statement)
        if amount is not ...:
            statement.adjustment_amount = _money(amount)
        if reason is not ...:
            statement.adjustment_reason = (reason or "")[:255]
        if notes is not ...:
            statement.notes = notes or ""
        if statement.adjustment_amount != 0 and not statement.adjustment_reason.strip():
            raise PayrollError("Give a reason for the bonus or deduction.")
        statement.save(update_fields=["adjustment_amount", "adjustment_reason", "notes", "updated_at"])
        recompute_totals(statement)
    return statement


def approve_statement(statement, actor):
    from .models import TeacherPayrollStatement

    with transaction.atomic():
        statement = TeacherPayrollStatement.objects.select_for_update().get(pk=statement.pk)
        _require_draft(statement)
        blocking = [ln for ln in statement.lines.all() if line_is_blocking(ln)]
        if blocking:
            raise PayrollError(
                f"{len(blocking)} class(es) still need review (missing Zoom time, rate card or rate tier). "
                "Correct or exclude them before approving."
            )
        recompute_totals(statement)
        if statement.net_amount <= 0:
            raise PayrollError("The net amount must be more than zero to approve. Cancel the statement instead.")
        statement.status = TeacherPayrollStatement.Status.APPROVED
        statement.approved_by = actor
        statement.approved_at = timezone.now()
        statement.save(update_fields=["status", "approved_by", "approved_at", "updated_at"])
    return statement


def mark_statement_paid(statement, actor, *, method="", reference_number=""):
    from .models import Payout, TeacherPayrollStatement

    if method and method not in Payout.Method.values:
        raise PayrollError("Unknown payment method.")
    with transaction.atomic():
        statement = TeacherPayrollStatement.objects.select_for_update().get(pk=statement.pk)
        if statement.status != TeacherPayrollStatement.Status.APPROVED:
            raise PayrollError("Only an approved statement can be marked paid.")
        statement.status = TeacherPayrollStatement.Status.PAID
        statement.paid_by = actor
        statement.paid_at = timezone.now()
        if method:
            statement.method = method
        if reference_number:
            statement.reference_number = reference_number[:255]
        statement.save(update_fields=["status", "paid_by", "paid_at", "method", "reference_number", "updated_at"])
    return statement


def cancel_statement(statement, actor, *, reason=""):
    from .models import TeacherPayrollStatement

    with transaction.atomic():
        statement = TeacherPayrollStatement.objects.select_for_update().get(pk=statement.pk)
        if statement.status not in (TeacherPayrollStatement.Status.DRAFT, TeacherPayrollStatement.Status.APPROVED):
            raise PayrollError("A paid or cancelled statement cannot be cancelled.")
        statement.status = TeacherPayrollStatement.Status.CANCELLED
        statement.cancelled_at = timezone.now()
        if reason:
            statement.notes = (statement.notes + "\n" if statement.notes else "") + f"Cancelled: {reason}"
        statement.save(update_fields=["status", "cancelled_at", "notes", "updated_at"])
    return statement
