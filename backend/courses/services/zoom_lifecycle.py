"""
Natya LMS — Enterprise Zoom Lifecycle & Concurrency Service.
Implements Phase B.4 specifications:
- Multi-table Zero-Teardown Guard under authoritative Level 1 row locks
- Deterministic lock hierarchy (Level 1 LiveClass -> Level 2 DOP -> Level 3 HostAccount -> Level 4 Reservation -> Level 5 MeetingRecord)
- Bounded deadlock retries for PostgreSQL SQLSTATE 40P01
- Two-phase external calls (Zoom API calls outside DB lock windows)
- Optimistic version revalidation on re-entry
- Host reservation lifecycle (Provisional -> Active -> Draining -> Released / Cancelled)
- Transactional Outbox processing
"""

import datetime
import functools
import hashlib
import logging
import random
import time
import uuid
import requests
from typing import Dict, Any, List, Optional, Tuple

from django.conf import settings
from django.db import transaction, models, OperationalError
from django.utils import timezone

from courses.models import (
    LiveClass,
    LiveClassDurableOperation,
    ZoomHostAccount,
    HostReservation,
    ZoomMeetingRecord,
    HistoricalZoomConflict,
    ZoomLifecycleOutbox,
    ZoomAdminAuditLog,
)
from courses.services.zoom import (
    ZoomService,
    ZoomAPIError,
    ZoomNotFoundError,
    ZoomRateLimitError,
    ZoomTransientError,
    ZoomAmbiguousOutcomeError,
    ZoomClientError,
    validate_host_id,
    validate_meeting_id,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class ZoomLifecycleError(Exception):
    """Base exception for Zoom lifecycle saga operations."""
    pass


class ZeroTeardownInvariantViolation(ZoomLifecycleError):
    """Raised when active, uncertain, or dangling obligations block provisioning."""
    def __init__(self, message: str, blocking_details: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.blocking_details = blocking_details or {}


class HostCapacityExhaustedError(ZoomLifecycleError):
    """Raised when no suitable host in the pool has available concurrency."""
    pass


class ConcurrencyConflictError(ZoomLifecycleError):
    """Raised when version check or optimistic concurrency lease fails."""
    pass


class RemoteVerificationError(ZoomLifecycleError):
    """Raised when Zoom cloud state contradicts local DB state or cannot be verified."""
    pass


class QuarantineVerificationError(ZoomLifecycleError):
    """Raised when quarantine cannot be resolved; the host reservation stays held."""
    pass


class QuarantineStateError(ZoomLifecycleError):
    """Raised when quarantine resolution is attempted on a DOP that is not quarantined."""
    pass


class EvidenceRejectedError(ZoomLifecycleError):
    """Raised when the database evidence boundary refuses a row (e.g. the caller lost its lease)."""
    pass


# ---------------------------------------------------------------------------
# Phase B.5 repair (B2/B3/B4/B5): actual Zoom call outcomes and verified evidence
# ---------------------------------------------------------------------------

# A provisioning CREATE can take: token fetch + POST + (401 -> token refresh + POST).
PROVISION_LEASE_SECONDS = max(120, 6 * ZoomService.DEFAULT_TIMEOUT_SECONDS)
RECOVERY_LEASE_SECONDS = 300
QUARANTINE_GRACE_SECONDS = 900
EVIDENCE_FRESHNESS_SECONDS = 600
ORPHAN_START_TOLERANCE_SECONDS = 120
ORPHAN_CREATED_SKEW_SECONDS = 300

_AMBIGUOUS_ZOOM_ERRORS = (ZoomAmbiguousOutcomeError, ZoomTransientError, requests.Timeout, requests.ConnectionError)
_OUTCOME_TOKEN = object()


class ZoomCallOutcome:
    """
    The classified result of ONE actual Zoom HTTP call. It can only be built by
    zoom_delete_outcome()/zoom_get_outcome(), which derive http_status from what
    the Zoom client actually returned or raised -- never from a caller's claim.
    """
    CONFIRMED_DELETED = "CONFIRMED_DELETED"   # DELETE answered 204
    CONFIRMED_ABSENT = "CONFIRMED_ABSENT"     # Zoom answered 404 (code 3001: meeting does not exist)
    EXISTS = "EXISTS"                         # GET answered 200
    FAILED = "FAILED"                         # definite non-confirmation (401/403/429/other 4xx)
    AMBIGUOUS = "AMBIGUOUS"                   # 5xx / timeout / connection drop / unexpected error

    __slots__ = ("meeting_id", "operation", "http_status", "result", "detail")

    def __init__(self, token, meeting_id, operation, http_status, result, detail=""):
        if token is not _OUTCOME_TOKEN:
            raise TypeError("ZoomCallOutcome is produced only by an actual Zoom call "
                            "(zoom_delete_outcome / zoom_get_outcome).")
        object.__setattr__(self, "meeting_id", str(meeting_id))
        object.__setattr__(self, "operation", operation)
        object.__setattr__(self, "http_status", http_status)
        object.__setattr__(self, "result", result)
        object.__setattr__(self, "detail", str(detail or "")[:500])

    def __setattr__(self, name, value):
        raise AttributeError("ZoomCallOutcome is immutable")

    @property
    def confirmed_gone(self) -> bool:
        return self.result in (self.CONFIRMED_DELETED, self.CONFIRMED_ABSENT)

    def as_dict(self) -> Dict[str, Any]:
        return {"meeting_id": self.meeting_id, "operation": self.operation,
                "http_status": self.http_status, "result": self.result, "detail": self.detail}

    def __repr__(self):
        return f"ZoomCallOutcome({self.operation} {self.meeting_id}: {self.result} HTTP {self.http_status})"


def _classify_zoom_exception(exc) -> Tuple[Optional[int], str]:
    """Classify by exception TYPE and status code -- never by message substrings."""
    status = getattr(exc, "status_code", None)
    if isinstance(exc, ZoomNotFoundError) and status == 404:
        return 404, ZoomCallOutcome.CONFIRMED_ABSENT
    if isinstance(exc, _AMBIGUOUS_ZOOM_ERRORS):
        return status, ZoomCallOutcome.AMBIGUOUS
    if isinstance(exc, ZoomAPIError):
        return status, ZoomCallOutcome.FAILED
    return None, ZoomCallOutcome.AMBIGUOUS


def zoom_delete_outcome(meeting_id, zoom_client=None) -> ZoomCallOutcome:
    """DELETE a meeting. ZoomService.delete_meeting returns only on HTTP 204 and raises otherwise."""
    client = zoom_client if zoom_client is not None else ZoomService
    try:
        client.delete_meeting(str(meeting_id))
    except Exception as exc:
        status, result = _classify_zoom_exception(exc)
        return ZoomCallOutcome(_OUTCOME_TOKEN, meeting_id, "DELETE", status, result, f"{type(exc).__name__}: {exc}")
    return ZoomCallOutcome(_OUTCOME_TOKEN, meeting_id, "DELETE", 204, ZoomCallOutcome.CONFIRMED_DELETED)


def zoom_get_outcome(meeting_id, zoom_client=None) -> ZoomCallOutcome:
    """GET a meeting. ZoomService.get_meeting returns only on HTTP 200 and raises otherwise."""
    client = zoom_client if zoom_client is not None else ZoomService
    try:
        client.get_meeting(str(meeting_id))
    except Exception as exc:
        status, result = _classify_zoom_exception(exc)
        return ZoomCallOutcome(_OUTCOME_TOKEN, meeting_id, "GET", status, result, f"{type(exc).__name__}: {exc}")
    return ZoomCallOutcome(_OUTCOME_TOKEN, meeting_id, "GET", 200, ZoomCallOutcome.EXISTS)


# evidence_type -> (operation, accepted results). Only confirmations are evidence.
_EVIDENCE_RULES = {
    "CANCELLATION_SAGA_DELETE": ("DELETE", (ZoomCallOutcome.CONFIRMED_DELETED, ZoomCallOutcome.CONFIRMED_ABSENT)),
    "ORPHAN_FENCING_TEARDOWN": ("DELETE", (ZoomCallOutcome.CONFIRMED_DELETED, ZoomCallOutcome.CONFIRMED_ABSENT)),
    "CRASH_RECOVERY_TEARDOWN": ("DELETE", (ZoomCallOutcome.CONFIRMED_DELETED, ZoomCallOutcome.CONFIRMED_ABSENT)),
    "OUTBOX_TEARDOWN_CONFIRMATION": ("DELETE", (ZoomCallOutcome.CONFIRMED_DELETED, ZoomCallOutcome.CONFIRMED_ABSENT)),
    "DUAL_GET_QUARANTINE_VERIFICATION": ("GET", (ZoomCallOutcome.CONFIRMED_ABSENT,)),
}


def _record_evidence_from_outcome(outcome: ZoomCallOutcome, evidence_type: str,
                                  dop_id: Optional[int] = None, worker_id: str = ""):
    """
    The only evidence writer. It takes no HTTP status argument: the status is
    the one carried by the ZoomCallOutcome of the actual call, and anything that
    is not a confirmation is refused. On PostgreSQL the insert goes through
    fn_record_verified_evidence (owner-only since migration 0038), which
    re-validates the type/status pairing and the DOP binding.
    """
    from django.db import connection
    from courses.models import ZoomVerifiedEvidence
    import json

    if not isinstance(outcome, ZoomCallOutcome):
        raise TypeError("Evidence can only be recorded from a ZoomCallOutcome of an actual Zoom call.")
    rule = _EVIDENCE_RULES.get(evidence_type)
    if rule is None:
        raise ValueError(f"Unknown evidence type {evidence_type!r}")
    operation, accepted = rule
    if outcome.operation != operation or outcome.result not in accepted:
        raise ValueError(f"Refusing to record {evidence_type} evidence for {outcome!r}: not a confirmation.")

    worker = str(worker_id or "")[:64]
    if connection.vendor == 'postgresql':
        from django.db import DatabaseError
        try:
            with transaction.atomic():  # savepoint: a rejection never poisons the caller's transaction
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT fn_record_verified_evidence(%s, %s, %s, %s::jsonb, %s, %s)",
                        [outcome.meeting_id, evidence_type, int(outcome.http_status),
                         json.dumps(outcome.as_dict()), worker, dop_id]
                    )
                    ev_id = cursor.fetchone()[0]
        except DatabaseError as exc:
            raise EvidenceRejectedError(f"{evidence_type} evidence for {outcome.meeting_id} rejected by the "
                                        f"database boundary: {str(exc).splitlines()[0]}") from exc
        return ZoomVerifiedEvidence.objects.get(id=ev_id)
    return ZoomVerifiedEvidence.objects.create(
        meeting_id=outcome.meeting_id, evidence_type=evidence_type, http_status=int(outcome.http_status),
        response_body=outcome.as_dict(), worker_id=worker, dop_id=dop_id,
    )


def _parse_zoom_time(value) -> Optional[datetime.datetime]:
    if not value:
        return None
    try:
        parsed = datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if timezone.is_naive(parsed):
        return None
    return parsed


def _list_host_meetings(host_user_id: str, zoom_client=None) -> List[Dict[str, Any]]:
    """ZoomService.list_user_meetings returns a LIST of {'meeting_id', 'topic', 'start_time', ...}."""
    client = zoom_client if zoom_client is not None else ZoomService
    meetings = client.list_user_meetings(host_user_id)
    if not isinstance(meetings, list):
        raise RemoteVerificationError(
            f"Unexpected list_user_meetings response type {type(meetings).__name__}; expected list.")
    return meetings


def _db_owned_meeting_ids(meeting_ids) -> set:
    ids = [str(m) for m in meeting_ids if m]
    if not ids:
        return set()
    owned = set(ZoomMeetingRecord.objects.filter(zoom_meeting_id__in=ids).values_list('zoom_meeting_id', flat=True))
    owned |= set(LiveClass.objects.filter(zoom_meeting_id__in=ids).values_list('zoom_meeting_id', flat=True))
    return {str(m) for m in owned}


def _orphan_deletion_candidates(meetings, payload: Dict[str, Any], dop_created_at) -> List[str]:
    """
    Meetings that can be attributed to THIS provisioning operation, strictly enough
    to delete. All must hold: same host (the listing is per host), exact topic,
    start within tolerance of what was sent, same duration, created no earlier
    than the operation, and not owned by any ZoomMeetingRecord or LiveClass.
    Topic alone never qualifies.
    """
    topic = payload.get('title') or payload.get('topic')
    expected_start = _parse_zoom_time(payload.get('scheduled_start'))
    duration = payload.get('duration_minutes')
    if not topic or expected_start is None or duration is None:
        return []
    candidates = []
    for m in meetings:
        mid = str(m.get('meeting_id') or '')
        start = _parse_zoom_time(m.get('start_time'))
        created = _parse_zoom_time(m.get('created_at'))
        if (mid and m.get('topic') == topic and start is not None
                and abs((start - expected_start).total_seconds()) <= ORPHAN_START_TOLERANCE_SECONDS
                and int(m.get('duration') or -1) == int(duration)
                and created is not None
                and created >= dop_created_at - datetime.timedelta(seconds=ORPHAN_CREATED_SKEW_SECONDS)):
            candidates.append(mid)
    owned = _db_owned_meeting_ids(candidates)
    return [c for c in candidates if c not in owned]


def _release_blocking_meetings(meetings, payload: Dict[str, Any], own_meeting_ids) -> List[str]:
    """
    Fail-closed check before RELEASING a host: any of this operation's known
    meetings still listed, or any unowned meeting with the same topic whose start
    is within tolerance (regardless of created_at/duration), blocks release.
    """
    own = {str(m) for m in own_meeting_ids if m}
    topic = payload.get('title') or payload.get('topic')
    expected_start = _parse_zoom_time(payload.get('scheduled_start'))
    blockers = []
    for m in meetings:
        mid = str(m.get('meeting_id') or '')
        if not mid:
            continue
        if mid in own:
            blockers.append(mid)
            continue
        start = _parse_zoom_time(m.get('start_time'))
        if (topic and m.get('topic') == topic and expected_start is not None and start is not None
                and abs((start - expected_start).total_seconds()) <= ORPHAN_START_TOLERANCE_SECONDS):
            blockers.append(mid)
    owned = _db_owned_meeting_ids(blockers) - own
    return [b for b in blockers if b not in owned]


# ---------------------------------------------------------------------------
# Stage 4: Concurrency & Bounded Deadlock Retry
# ---------------------------------------------------------------------------

def execute_with_deadlock_retry(max_retries: int = 3, base_delay: float = 0.05, max_delay: float = 1.0):
    """
    Decorator for executing transaction blocks with bounded exponential backoff
    specifically for recognized PostgreSQL deadlock errors (SQLSTATE 40P01)
    or database lock contention. Fresh transactions are initiated on retry.
    Never retries arbitrary IntegrityError or business logic failures.
    """
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            last_exception = None
            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except OperationalError as exc:
                    # Check for PostgreSQL SQLSTATE 40P01 or SQLite database is locked
                    err_msg = str(exc).lower()
                    pg_code = getattr(getattr(exc, '__cause__', None), 'pgcode', None)
                    is_deadlock = (pg_code == '40P01') or ('deadlock detected' in err_msg) or ('locked' in err_msg)
                    
                    if not is_deadlock or attempt == max_retries - 1:
                        logger.error(f"[ZoomLifecycle] Non-retryable or exhausted operational error on {func.__name__}: {exc}")
                        raise
                    
                    sleep_time = min(max_delay, (base_delay * (2 ** attempt)) + random.uniform(0.01, 0.05))
                    logger.warning(
                        f"[ZoomLifecycle] Deadlock/contention detected on attempt {attempt + 1}/{max_retries} "
                        f"for {func.__name__}. Retrying in {sleep_time:.3f}s..."
                    )
                    time.sleep(sleep_time)
                    last_exception = exc
            if last_exception:
                raise last_exception
        return wrapper
    return decorator


# ---------------------------------------------------------------------------
# Stage 2: Multi-Table Zero-Teardown Guard
# ---------------------------------------------------------------------------

def validate_zero_teardown_invariant(live_class_id: int) -> Dict[str, Any]:
    """
    Evaluates the zero-teardown invariant across all five independent tables.
    MUST be called within an active transaction holding Level 1 lock:
        SELECT id FROM courses_liveclass WHERE id = :id FOR UPDATE;

    Blocks if ANY of the following 5 obligations exist:
    1. Active or uncertain ZoomMeetingRecord (is_current=True or state in RESERVED, ASSIGNED, COMPENSATING, ADOPTED_PENDING_COMMIT)
    2. Active or draining HostReservation (status in PROVISIONAL, ACTIVE, DRAINING, MANUAL_REVIEW)
    3. In-progress, ambiguous, or compensation-pending LiveClassDurableOperation
    4. Pending or in-flight ZoomLifecycleOutbox messages
    5. Dangling LiveClass.zoom_meeting_id pointer without safely resolved terminal meeting record

    Terminal historical records (RELEASED, COMPENSATED, POISONED) DO NOT block.
    """
    try:
        live_class = LiveClass.objects.select_for_update().get(id=live_class_id)
    except LiveClass.DoesNotExist:
        raise ZoomLifecycleError(f"LiveClass {live_class_id} does not exist.")

    violations = {}

    # 1. Check ZoomMeetingRecord
    active_zmrs = ZoomMeetingRecord.objects.filter(
        live_class=live_class
    ).filter(
        models.Q(is_current=True) |
        models.Q(state__in=[
            ZoomMeetingRecord.State.RESERVED,
            ZoomMeetingRecord.State.ASSIGNED,
            ZoomMeetingRecord.State.COMPENSATING,
            ZoomMeetingRecord.State.ADOPTED_PENDING_COMMIT,
        ])
    )
    if active_zmrs.exists():
        violations["active_meeting_records"] = list(
            active_zmrs.values_list('zoom_meeting_id', 'state')
        )

    # 2. Check HostReservation
    active_reservations = HostReservation.objects.filter(
        live_class=live_class,
        status__in=[
            HostReservation.Status.PROVISIONAL,
            HostReservation.Status.ACTIVE,
            HostReservation.Status.DRAINING,
            HostReservation.Status.MANUAL_REVIEW,
        ]
    )
    if active_reservations.exists():
        violations["active_reservations"] = list(
            active_reservations.values_list('id', 'host_account_id', 'status')
        )

    # 3. Check LiveClassDurableOperation
    in_flight_dops = LiveClassDurableOperation.objects.filter(
        live_class=live_class,
        status__in=[
            LiveClassDurableOperation.Status.IN_PROGRESS,
            LiveClassDurableOperation.Status.OUTCOME_UNKNOWN,
            LiveClassDurableOperation.Status.COMPENSATION_REQUIRED,
            LiveClassDurableOperation.Status.COMPENSATING,
            LiveClassDurableOperation.Status.MANUAL_REVIEW_REQUIRED,
        ]
    )
    if in_flight_dops.exists():
        violations["in_flight_durable_operations"] = list(
            in_flight_dops.values_list('id', 'idempotency_key', 'status')
        )

    # 4. Check Transactional Outbox
    in_flight_outbox = ZoomLifecycleOutbox.objects.filter(
        aggregate_id=live_class.id,
        status__in=[
            ZoomLifecycleOutbox.Status.PENDING,
            ZoomLifecycleOutbox.Status.IN_FLIGHT,
            ZoomLifecycleOutbox.Status.RETRYING,
            ZoomLifecycleOutbox.Status.DEAD_LETTER,
        ]
    )
    if in_flight_outbox.exists():
        violations["in_flight_outbox"] = list(
            in_flight_outbox.values_list('id', 'event_type', 'status')
        )

    # 5. Check Dangling LiveClass Zoom Pointer
    raw_meeting_id = (live_class.zoom_meeting_id or '').strip()
    if raw_meeting_id:
        has_terminal_record = ZoomMeetingRecord.objects.filter(
            live_class=live_class,
            zoom_meeting_id=raw_meeting_id,
            state__in=[
                ZoomMeetingRecord.State.RELEASED,
                ZoomMeetingRecord.State.COMPENSATED,
                ZoomMeetingRecord.State.POISONED,
            ]
        ).exists()

        if not has_terminal_record:
            # Check if soft-linked HistoricalZoomConflict exists and is marked resolved
            has_resolved_conflict = HistoricalZoomConflict.objects.filter(
                historical_live_class_id=live_class.id,
                zoom_meeting_id_raw=raw_meeting_id,
                resolved=True
            ).exists()
            if not has_resolved_conflict:
                violations["dangling_zoom_pointer"] = {
                    "live_class_meeting_id": raw_meeting_id,
                    "reason": "LiveClass has zoom_meeting_id without safely resolved ZoomMeetingRecord or HistoricalZoomConflict"
                }

    if violations:
        raise ZeroTeardownInvariantViolation(
            f"Zero-teardown violation for LiveClass {live_class_id}: unresolved obligations exist.",
            blocking_details=violations
        )

    return {"status": "CLEAN", "live_class_id": live_class_id}


# ---------------------------------------------------------------------------
# Host Pool Allocation
# ---------------------------------------------------------------------------

def select_and_reserve_host(
    live_class: LiveClass,
    start_time: datetime.datetime,
    duration_minutes: int,
    preferred_host_id: Optional[int] = None,
    durable_operation: Optional[LiveClassDurableOperation] = None
) -> HostReservation:
    """
    Selects an available ZoomHostAccount from the active pool under deterministic
    Level 3 locks (ORDER BY id ASC FOR UPDATE). Enforces max concurrency per host.
    Authoritative teacher-to-host resolution and anti-host-stealing isolation.
    Creates a HostReservation in PROVISIONAL state.
    """
    from django.db.models.functions import Lower
    from django.contrib.auth import get_user_model

    end_time = start_time + datetime.timedelta(minutes=duration_minutes)
    UserModel = get_user_model()

    # 1. Resolve Effective Instructor and Teacher Email safely
    eff_inst = live_class.effective_instructor
    eff_inst_id = live_class.effective_instructor_id
    raw_email = getattr(eff_inst, 'email', None) if eff_inst else None
    teacher_email = raw_email.strip().lower() if (raw_email and raw_email.strip() and '@' in raw_email) else None

    # Check for ambiguous teacher identity (multiple user accounts with same email)
    if teacher_email:
        duplicate_users = UserModel.objects.filter(email__iexact=teacher_email).count()
        if duplicate_users > 1:
            raise HostCapacityExhaustedError(
                f"Ambiguous teacher identity: multiple user accounts share email '{teacher_email}'."
            )

    # 2. Gather other teachers' emails for anti-host-stealing protection
    other_teacher_emails = set(
        UserModel.objects.filter(
            models.Q(is_teacher=True) | models.Q(is_mentor=True)
        ).exclude(id=eff_inst_id).values_list('email', flat=True)
    )
    other_teacher_emails = {
        e.strip().lower() for e in other_teacher_emails if e and e.strip() and '@' in e
    }

    # 3. Check Dedicated Host Mapping for Current Teacher
    # Priority: explicit assigned_instructor, then fallback to email match
    dedicated_host = None
    if eff_inst:
        dedicated_host = ZoomHostAccount.objects.filter(
            account_type=ZoomHostAccount.AccountType.DEDICATED,
            assigned_instructor=eff_inst
        ).first()

    if not dedicated_host and teacher_email:
        dedicated_host = (
            ZoomHostAccount.objects.annotate(email_lower=Lower('email'))
            .filter(
                models.Q(account_type=ZoomHostAccount.AccountType.DEDICATED, email_lower=teacher_email) |
                models.Q(email_lower=teacher_email)
            )
            .first()
        )

    if dedicated_host:
        # If dedicated host exists but is not ACTIVE, fail closed! Never silently fall back.
        if dedicated_host.status != ZoomHostAccount.Status.ACTIVE:
            raise HostCapacityExhaustedError(
                f"Dedicated Zoom host account for instructor '{teacher_email or eff_inst}' ({dedicated_host.zoom_user_id}) "
                f"is {dedicated_host.status}. Dedicated hosts must be ACTIVE to conduct classes."
            )

    # 4. Formulate Host Query
    # Exclude UNCLASSIFIED hosts strictly!
    host_query = (
        ZoomHostAccount.objects.annotate(email_lower=Lower('email'))
        .filter(status=ZoomHostAccount.Status.ACTIVE)
        .exclude(account_type=ZoomHostAccount.AccountType.UNCLASSIFIED)
        .order_by('id')
    )

    if preferred_host_id:
        pref_host = (
            ZoomHostAccount.objects.annotate(email_lower=Lower('email'))
            .filter(id=preferred_host_id, status=ZoomHostAccount.Status.ACTIVE)
            .first()
        )
        if not pref_host:
            raise HostCapacityExhaustedError(
                f"Preferred host account {preferred_host_id} is not active or does not exist."
            )
        if pref_host.account_type == ZoomHostAccount.AccountType.UNCLASSIFIED:
            raise HostCapacityExhaustedError(
                f"Unauthorized host selection: host {preferred_host_id} is UNCLASSIFIED and cannot be allocated."
            )
        # Prevent selecting another teacher's dedicated host
        if pref_host.account_type == ZoomHostAccount.AccountType.DEDICATED:
            if not dedicated_host or pref_host.id != dedicated_host.id:
                raise HostCapacityExhaustedError(
                    f"Unauthorized host selection: host {preferred_host_id} is a dedicated host for another instructor."
                )
        if pref_host.email_lower in other_teacher_emails:
            raise HostCapacityExhaustedError(
                f"Unauthorized host selection: host {preferred_host_id} ({pref_host.email}) belongs to another instructor."
            )
        # If current teacher has a dedicated host, preferred host must match it
        if dedicated_host and dedicated_host.id != pref_host.id:
            raise HostCapacityExhaustedError(
                f"Instructor '{teacher_email}' is bound to dedicated host {dedicated_host.id}; cannot use host {preferred_host_id}."
            )
        host_query = host_query.filter(id=preferred_host_id)

    elif dedicated_host:
        # Dedicated active host strictly assigned to this teacher
        host_query = host_query.filter(id=dedicated_host.id)

    else:
        # General shared pool allocation:
        # Strictly exclude ALL dedicated hosts, other teachers' hosts, and unclassified hosts
        host_query = host_query.filter(account_type=ZoomHostAccount.AccountType.SHARED_POOL)
        if other_teacher_emails:
            host_query = host_query.exclude(email_lower__in=other_teacher_emails)
        host_query = host_query.exclude(assigned_instructor__isnull=False)

    candidate_hosts = list(host_query.select_for_update())
    if not candidate_hosts:
        raise HostCapacityExhaustedError("No active Zoom host accounts available in the pool.")

    selected_host = None
    for host in candidate_hosts:
        # Check active overlapping reservations for this host
        overlap_count = HostReservation.objects.filter(
            host_account=host,
            status__in=[
                HostReservation.Status.PROVISIONAL,
                HostReservation.Status.ACTIVE,
                HostReservation.Status.DRAINING,
                HostReservation.Status.MANUAL_REVIEW,
            ],
            scheduled_start__lt=end_time,
            scheduled_end__gt=start_time,
        ).exclude(live_class=live_class).count()

        if overlap_count < host.max_concurrency:
            selected_host = host
            break

    if not selected_host:
        raise HostCapacityExhaustedError(
            f"All {len(candidate_hosts)} active host accounts are at full concurrency between {start_time} and {end_time}."
        )

    # Determine generation counter
    last_res = HostReservation.objects.filter(live_class=live_class).order_by('-generation').first()
    gen = (last_res.generation + 1) if last_res else 1

    reservation = HostReservation.objects.create(
        live_class=live_class,
        host_account=selected_host,
        scheduled_start=start_time,
        scheduled_end=end_time,
        generation=gen,
        status=HostReservation.Status.PROVISIONAL,
        durable_operation=durable_operation,
    )
    return reservation


# ---------------------------------------------------------------------------
# Stage 3: Durable Lifecycle Workflows
# ---------------------------------------------------------------------------

@execute_with_deadlock_retry()
def provision_zoom_meeting(
    live_class_id: int,
    idempotency_key: Optional[str] = None,
    preferred_host_id: Optional[int] = None
) -> Dict[str, Any]:
    """
    Full saga for initial Zoom meeting provisioning:
    Phase 1 (In DB Transaction):
      1. Lock LiveClass FOR UPDATE (Level 1)
      2. Run Zero-Teardown Multi-Table Guard
      3. Create LiveClassDurableOperation (IN_PROGRESS)
      4. Select and reserve host (PROVISIONAL)
    Phase 2 (Outside DB Transaction):
      5. Call ZoomService.create_meeting
    Phase 3 (In DB Transaction):
      6. Lock LiveClass FOR UPDATE
      7. Revalidate DOP version and state
      8. If Zoom success:
         - Create ZoomMeetingRecord (ASSIGNED, is_current=True)
         - Transition HostReservation to ACTIVE
         - Transition DOP to COMPLETED
         - Update LiveClass.zoom_meeting_id & zoom_uuid
         - Enqueue ZoomLifecycleOutbox message
      9. If Zoom Ambiguous (timeout/chunked stream drop):
         - Transition DOP to OUTCOME_UNKNOWN
         - Transition HostReservation to MANUAL_REVIEW
    """
    if not idempotency_key:
        idempotency_key = f"prov-{live_class_id}-{uuid.uuid4().hex[:16]}"

    # Phase 1: DB Reservation under Level 1 exclusive lock
    with transaction.atomic():
        live_class = LiveClass.objects.select_for_update().get(id=live_class_id)
        validate_zero_teardown_invariant(live_class.id)

        # Check existing DOP with this idempotency key
        dop, created = LiveClassDurableOperation.objects.select_for_update().get_or_create(
            idempotency_key=idempotency_key,
            defaults={
                "operation_type": LiveClassDurableOperation.OperationType.ZOOM_PROVISION,
                "status": LiveClassDurableOperation.Status.IN_PROGRESS,
                "live_class": live_class,
                "payload": {
                    "live_class_id": live_class.id,
                    "title": live_class.title,
                    "scheduled_start": live_class.scheduled_start.isoformat(),
                    "duration_minutes": live_class.duration_minutes
                }
            }
        )

        if not created and dop.status == LiveClassDurableOperation.Status.COMPLETED:
            zmr = ZoomMeetingRecord.objects.filter(live_class=live_class, is_current=True).first()
            return {
                "status": "ALREADY_COMPLETED",
                "zoom_meeting_id": zmr.zoom_meeting_id if zmr else live_class.zoom_meeting_id,
                "dop_id": dop.id
            }

        # Phase B.5 repair (B4): an existing operation whose CREATE may still be
        # outstanding (in flight, crashed, or ambiguous) must never be re-driven --
        # a second CREATE could leave two meetings. It goes through crash recovery
        # or quarantine resolution instead.
        if not created and dop.status in (
            LiveClassDurableOperation.Status.IN_PROGRESS,
            LiveClassDurableOperation.Status.OUTCOME_UNKNOWN,
            LiveClassDurableOperation.Status.COMPENSATION_REQUIRED,
            LiveClassDurableOperation.Status.COMPENSATING,
        ):
            raise ConcurrencyConflictError(
                f"DOP #{dop.id} is {dop.status} (lease until {dop.lease_expires_at}); a CREATE may be "
                f"outstanding. Use recover_crashed_provision_dop / quarantine resolution, not a re-drive."
            )

        # Phase B.5 repair (B4): a real provisioning lease. Crash recovery may not
        # touch this DOP until the lease (which outlasts the whole CREATE call,
        # including its 401 retry) has expired; Phase 3 refuses to finalize if the
        # lease was taken over in the meantime.
        provision_worker = uuid.uuid4()
        dop.lease_worker = provision_worker
        dop.lease_expires_at = timezone.now() + datetime.timedelta(seconds=PROVISION_LEASE_SECONDS)
        dop.save(update_fields=['lease_worker', 'lease_expires_at', 'updated_at'])

        try:
            reservation = select_and_reserve_host(
                live_class=live_class,
                start_time=live_class.scheduled_start,
                duration_minutes=live_class.duration_minutes,
                preferred_host_id=preferred_host_id,
                durable_operation=dop
            )
        except Exception as e:
            dop.status = LiveClassDurableOperation.Status.FAILED
            dop.error_message = str(e)
            dop.save(update_fields=['status', 'error_message', 'updated_at'])
            raise
        host_user_id = reservation.host_account.zoom_user_id
        res_id = reservation.id
        dop_id = dop.id
        dop_version = dop.version
        class_title = live_class.title
        class_start_iso = live_class.scheduled_start.isoformat()
        class_duration = live_class.duration_minutes

    # Phase 2: Remote Zoom API Call OUTSIDE Database Transaction
    zoom_result = None
    zoom_error = None
    try:
        zoom_result = ZoomService.create_meeting(
            host_id=host_user_id,
            topic=class_title,
            start_time_iso=class_start_iso,
            duration_minutes=class_duration
        )
    except (ZoomAmbiguousOutcomeError, requests.Timeout, requests.ConnectionError) as amb_err:
        zoom_error = amb_err
    except ZoomAPIError as definite_err:
        zoom_error = definite_err
    except Exception as unk_err:
        zoom_error = unk_err

    # Phase 3: Final State Transition under Level 1 Lock
    definite_failure_exc = None
    fencing_conflict_exc = None
    fenced_teardown = None
    with transaction.atomic():
        live_class = LiveClass.objects.select_for_update().get(id=live_class_id)
        dop = LiveClassDurableOperation.objects.select_for_update().get(id=dop_id)
        reservation = HostReservation.objects.select_for_update().get(id=res_id)

        # Fencing: verify version/lease have not mutated concurrently and class is not cancelled
        is_fencing_violation = (
            dop.version != dop_version
            or dop.lease_worker != provision_worker
            or dop.status != LiveClassDurableOperation.Status.IN_PROGRESS
            or live_class.status == LiveClass.ClassStatus.CANCELLED
            or reservation.status != HostReservation.Status.PROVISIONAL
        )

        if is_fencing_violation:
            # Phase B.5 repair (B5): record compensation durably and STOP. The
            # success path below must never run for a fenced operation, and no
            # Zoom call is made while these locks are held.
            fenced_teardown, fencing_conflict_exc = _compensate_fenced_provision(
                live_class, dop, reservation, zoom_result, zoom_error, provision_worker, dop_version
            )

        elif zoom_result:
            dop.version += 1
            dop.lease_worker = None
            dop.lease_expires_at = None
            meeting_id = str(zoom_result["meeting_id"])
            uuid_val = zoom_result.get("uuid", "")
            join_url = zoom_result.get("join_url", "")
            host_url = zoom_result.get("host_url", "")

            # Create ZoomMeetingRecord
            zmr = ZoomMeetingRecord.objects.create(
                zoom_meeting_id=meeting_id,
                topic=class_title,
                state=ZoomMeetingRecord.State.ASSIGNED,
                live_class=live_class,
                host_account=reservation.host_account,
                scheduled_start=reservation.scheduled_start,
                scheduled_end=reservation.scheduled_end,
                is_current=True,
                generation=reservation.generation,
                durable_operation=dop
            )

            # Transition Reservation and DOP
            reservation.status = HostReservation.Status.ACTIVE
            reservation.save(update_fields=['status', 'updated_at'])

            dop.status = LiveClassDurableOperation.Status.COMPLETED
            dop.response_payload = zoom_result
            dop.save(update_fields=['status', 'response_payload', 'version', 'lease_worker',
                                    'lease_expires_at', 'updated_at'])

            # Update LiveClass pointers
            live_class.zoom_meeting_id = meeting_id
            live_class.zoom_uuid = uuid_val
            live_class.meeting_url = join_url
            if host_url:
                live_class.host_url = host_url
            live_class.save(update_fields=['zoom_meeting_id', 'zoom_uuid', 'meeting_url', 'host_url'])

            # Enqueue outbox notification
            ZoomLifecycleOutbox.objects.create(
                aggregate_id=live_class.id,
                event_type="ZOOM_MEETING_PROVISIONED",
                status=ZoomLifecycleOutbox.Status.PENDING,
                payload={
                    "live_class_id": live_class.id,
                    "meeting_id": meeting_id,
                    "host_id": host_user_id,
                    "host_reservation_id": reservation.id
                }
            )

            return {
                "status": "SUCCESS",
                "meeting_id": meeting_id,
                "host_id": host_user_id,
                "reservation_id": reservation.id,
                "dop_id": dop.id
            }

        elif isinstance(zoom_error, (ZoomAmbiguousOutcomeError, requests.Timeout, requests.ConnectionError)):
            # Ambiguous: Cannot safely rollback; must hold host and require reconciliation
            dop.version += 1
            dop.lease_worker = None
            dop.lease_expires_at = None
            dop.status = LiveClassDurableOperation.Status.OUTCOME_UNKNOWN
            dop.error_message = f"Ambiguous remote outcome: {str(zoom_error)}"
            dop.save(update_fields=['status', 'error_message', 'version', 'lease_worker',
                                    'lease_expires_at', 'updated_at'])

            reservation.status = HostReservation.Status.MANUAL_REVIEW
            reservation.release_reason = "Ambiguous remote outcome during CREATE; held until verified."
            reservation.save(update_fields=['status', 'release_reason', 'updated_at'])

            logger.error(f"[ZoomLifecycle] Ambiguous outcome provisioning LC {live_class_id}: {zoom_error}")
            return {
                "status": "OUTCOME_UNKNOWN",
                "dop_id": dop.id,
                "reservation_id": reservation.id,
                "error": str(zoom_error)
            }

        else:
            # Definite failure: Cancel reservation and mark DOP FAILED
            dop.version += 1
            dop.lease_worker = None
            dop.lease_expires_at = None
            dop.status = LiveClassDurableOperation.Status.FAILED
            dop.error_message = f"Zoom API definite failure: {str(zoom_error)}"
            dop.save(update_fields=['status', 'error_message', 'version', 'lease_worker',
                                    'lease_expires_at', 'updated_at'])

            reservation.status = HostReservation.Status.CANCELLED
            reservation.release_reason = f"Definite failure during CREATE: {str(zoom_error)}"
            reservation.save(update_fields=['status', 'release_reason', 'updated_at'])

            definite_failure_exc = ZoomLifecycleError(f"Zoom creation failed: {str(zoom_error)}")

    # Fenced: the compensation, hold and durable teardown outbox are committed.
    # Only now -- outside every lock -- attempt the immediate teardown. If it is
    # not confirmed, the committed outbox message is the durable retry.
    if fenced_teardown:
        _attempt_fenced_teardown(fenced_teardown)
    if fencing_conflict_exc:
        raise fencing_conflict_exc
    if definite_failure_exc:
        raise definite_failure_exc


def _compensate_fenced_provision(live_class, dop, reservation, zoom_result, zoom_error,
                                 provision_worker, dop_version):
    """
    Phase B.5 repair (B5). Runs inside provision Phase 3's transaction, under the
    LiveClass/DOP/reservation locks, and makes NO Zoom call. Records -- durably,
    in that transaction -- everything needed to tear down a meeting created by a
    CREATE that returned after the operation was fenced or cancelled:
      * a PENDING teardown outbox message (the durable retry),
      * the orphan meeting id on the DOP, which moves to COMPENSATION_REQUIRED
        (quarantine) while a remote meeting may exist,
      * the reservation held in MANUAL_REVIEW (never reactivated; a reservation
        that is already terminal is left as it is).
    Never creates a ZoomMeetingRecord and never reactivates a reservation.
    Returns (teardown_plan_or_None, ConcurrencyConflictError).
    """
    terminal_reservation = (HostReservation.Status.RELEASED, HostReservation.Status.COMPLETED,
                            HostReservation.Status.CANCELLED)
    orphan_meeting_id = str(zoom_result["meeting_id"]) if zoom_result and zoom_result.get("meeting_id") else None
    ambiguous = orphan_meeting_id is None and isinstance(zoom_error, _AMBIGUOUS_ZOOM_ERRORS)
    remote_may_exist = bool(orphan_meeting_id) or ambiguous

    outbox_id = None
    if orphan_meeting_id:
        outbox = ZoomLifecycleOutbox.objects.create(
            aggregate_id=live_class.id,
            event_type="ZOOM_MEETING_COMMIT_FAILED_NEEDS_TEARDOWN",
            status=ZoomLifecycleOutbox.Status.PENDING,
            payload={"meeting_id": orphan_meeting_id, "dop_id": dop.id,
                     "reservation_id": reservation.id, "reason": "in_flight_cancelled_or_fenced"}
        )
        outbox_id = outbox.id
        logger.critical(
            f"[ZoomLifecycle] Fencing violation for DOP {dop.id}: CREATE returned meeting {orphan_meeting_id} "
            f"after LiveClass {live_class.id} was fenced/cancelled. Teardown queued (outbox #{outbox_id})."
        )

    if remote_may_exist:
        if dop.status != LiveClassDurableOperation.Status.COMPLETED:
            dop.status = LiveClassDurableOperation.Status.COMPENSATION_REQUIRED
            # A remote meeting has only now become known: restart the grace period.
            dop.quarantined_at = timezone.now()
    elif dop.status not in (LiveClassDurableOperation.Status.COMPLETED, LiveClassDurableOperation.Status.FAILED,
                            LiveClassDurableOperation.Status.COMPENSATED):
        dop.status = LiveClassDurableOperation.Status.COMPENSATED
    response_payload = dict(dop.response_payload or {})
    if orphan_meeting_id:
        response_payload["orphan_meeting_id"] = orphan_meeting_id
    dop.response_payload = response_payload
    if orphan_meeting_id:
        dop.error_message = (f"Fencing violation: CREATE returned meeting {orphan_meeting_id} after the operation "
                             f"was fenced. Teardown queued in outbox #{outbox_id}; reservation held.")
    elif ambiguous:
        dop.error_message = "Fencing violation with ambiguous CREATE outcome; reservation held for quarantine verification."
    else:
        dop.error_message = f"Fencing violation; CREATE failed definitively ({zoom_error}); no remote meeting."
    dop.version += 1
    if dop.lease_worker == provision_worker:
        dop.lease_worker = None
        dop.lease_expires_at = None
    dop.save(update_fields=['status', 'response_payload', 'error_message', 'version',
                            'lease_worker', 'lease_expires_at', 'quarantined_at', 'updated_at'])

    if reservation.status not in terminal_reservation:
        if remote_may_exist:
            reservation.status = HostReservation.Status.MANUAL_REVIEW
            reservation.release_reason = ("Held: provisioning was fenced while a Zoom meeting may exist "
                                          f"({orphan_meeting_id or 'ambiguous CREATE'}).")
        else:
            reservation.status = HostReservation.Status.CANCELLED
            reservation.release_reason = "Fencing violation: CREATE failed definitively; nothing to tear down."
        reservation.save(update_fields=['status', 'release_reason', 'updated_at'])

    conflict = ConcurrencyConflictError(
        f"Fencing conflict: DOP {dop.id} changed from version {dop_version} while CREATE was in flight "
        f"(status={dop.status}). Orphan meeting: {orphan_meeting_id or 'none'}."
    )
    plan = None
    if orphan_meeting_id:
        plan = {"meeting_id": orphan_meeting_id, "dop_id": dop.id, "live_class_id": live_class.id,
                "fenced_version": dop.version, "reservation_id": reservation.id, "outbox_id": outbox_id}
    return plan, conflict


def _attempt_fenced_teardown(plan: Dict[str, Any]) -> ZoomCallOutcome:
    """
    Phase B.5 repair (B5). Immediate teardown attempt for a fenced CREATE, made
    with no locks held. Evidence is recorded only from a confirmed DELETE
    (HTTP 204, or 404 = already gone). Only then is the held reservation released
    -- and only if nobody has touched the DOP since the fencing commit. Anything
    unconfirmed leaves the committed outbox message as the durable retry.
    """
    outcome = zoom_delete_outcome(plan["meeting_id"])
    if not outcome.confirmed_gone:
        logger.warning(f"[ZoomLifecycle] Immediate teardown of fenced meeting {plan['meeting_id']} not confirmed "
                       f"({outcome.result}, HTTP {outcome.http_status}); outbox #{plan['outbox_id']} will retry.")
        return outcome

    try:
        _record_evidence_from_outcome(outcome, "ORPHAN_FENCING_TEARDOWN", dop_id=plan["dop_id"],
                                      worker_id="provision_fencing_guard")
    except EvidenceRejectedError as exc:
        logger.warning(f"[ZoomLifecycle] {exc}; reservation stays held for quarantine verification.")
        return outcome
    with transaction.atomic():
        LiveClass.objects.select_for_update().filter(id=plan["live_class_id"]).first()
        dop = LiveClassDurableOperation.objects.select_for_update().get(id=plan["dop_id"])
        outbox = ZoomLifecycleOutbox.objects.select_for_update().filter(
            id=plan["outbox_id"], status=ZoomLifecycleOutbox.Status.PENDING).first()
        if outbox:
            outbox.status = ZoomLifecycleOutbox.Status.COMPLETED
            outbox.error_message = f"Teardown confirmed immediately by provisioning fencing guard (HTTP {outcome.http_status})."
            outbox.save(update_fields=['status', 'error_message', 'updated_at'])
        if (dop.version == plan["fenced_version"]
                and dop.status == LiveClassDurableOperation.Status.COMPENSATION_REQUIRED):
            dop.status = LiveClassDurableOperation.Status.COMPENSATED
            dop.version += 1
            dop.error_message = (f"{dop.error_message} Teardown of {plan['meeting_id']} confirmed "
                                 f"(HTTP {outcome.http_status}).")
            dop.save(update_fields=['status', 'version', 'error_message', 'updated_at'])
            res = HostReservation.objects.select_for_update().filter(
                id=plan["reservation_id"], status=HostReservation.Status.MANUAL_REVIEW).first()
            if res:
                res.status = HostReservation.Status.CANCELLED
                res.release_reason = f"Fenced orphan meeting {plan['meeting_id']} teardown confirmed (HTTP {outcome.http_status})."
                res.save(update_fields=['status', 'release_reason', 'updated_at'])
    return outcome


@execute_with_deadlock_retry()
def replace_zoom_meeting(
    live_class_id: int,
    new_host_id: Optional[int] = None,
    new_start_time: Optional[datetime.datetime] = None,
    new_duration: Optional[int] = None
) -> Dict[str, Any]:
    """
    Replaces an active Zoom meeting with deterministic multi-host ordering.
    Replacement Safety Guarantees (Resolves DEF-01):
    1. Rejects replacement if any in-flight, ambiguous, or compensation-required DOP,
       provisional reservation, or in-flight outbox notification exists for this LiveClass.
    2. Old meeting and host reservation remain ACTIVE and protected while the new meeting is created.
    3. If replacement creation fails or is ambiguous, the old meeting is preserved intact, and the
       new provisional reservation is held (MANUAL_REVIEW) or cancelled.
    4. Handles DB uniqueness or commit failure without leaving a remotely created meeting untracked.
       Persists durable recovery metadata and queues an outbox teardown task.
    """
    with transaction.atomic():
        live_class = LiveClass.objects.select_for_update().get(id=live_class_id)

        # DEF-01 Guard 1: Reject if any unresolved or in-progress DurableOperation exists
        blocking_dops = list(
            LiveClassDurableOperation.objects.filter(
                live_class=live_class,
                status__in=[
                    LiveClassDurableOperation.Status.IN_PROGRESS,
                    LiveClassDurableOperation.Status.OUTCOME_UNKNOWN,
                    LiveClassDurableOperation.Status.COMPENSATION_REQUIRED,
                    LiveClassDurableOperation.Status.COMPENSATING,
                    LiveClassDurableOperation.Status.MANUAL_REVIEW_REQUIRED,
                ]
            ).values_list('id', 'operation_type', 'status')
        )
        if blocking_dops:
            raise ZeroTeardownInvariantViolation(
                f"Cannot replace Zoom meeting for LiveClass {live_class_id}: unresolved durable operation exists ({blocking_dops}).",
                blocking_details={"blocking_durable_operations": blocking_dops}
            )

        # DEF-01 Guard 2: Reject if any conflicting provisional/draining/manual review reservations exist
        blocking_res = list(
            HostReservation.objects.filter(
                live_class=live_class,
                status__in=[
                    HostReservation.Status.PROVISIONAL,
                    HostReservation.Status.DRAINING,
                    HostReservation.Status.MANUAL_REVIEW,
                ]
            ).values_list('id', 'status')
        )
        if blocking_res:
            raise ZeroTeardownInvariantViolation(
                f"Cannot replace Zoom meeting for LiveClass {live_class_id}: conflicting reservation exists ({blocking_res}).",
                blocking_details={"blocking_reservations": blocking_res}
            )

        # DEF-01 Guard 3: Reject if any in-flight teardown or replacement outbox records exist
        in_flight_teardown_outbox = list(
            ZoomLifecycleOutbox.objects.filter(
                aggregate_id=live_class.id,
                event_type__in=[
                    'ZOOM_MEETING_REPLACED',
                    'ZOOM_MEETING_TEARDOWN',
                    'ZOOM_MEETING_COMMIT_FAILED_NEEDS_TEARDOWN',
                ],
                status__in=[
                    ZoomLifecycleOutbox.Status.PENDING,
                    ZoomLifecycleOutbox.Status.IN_FLIGHT,
                    ZoomLifecycleOutbox.Status.RETRYING,
                ]
            ).values_list('id', 'event_type', 'status')
        )
        if in_flight_teardown_outbox:
            raise ZeroTeardownInvariantViolation(
                f"Cannot replace Zoom meeting for LiveClass {live_class_id}: in-flight teardown or replacement operations exist ({in_flight_teardown_outbox}).",
                blocking_details={"in_flight_teardown_outbox": in_flight_teardown_outbox}
            )

        current_zmr = ZoomMeetingRecord.objects.select_for_update().filter(
            live_class=live_class, is_current=True
        ).first()

        current_res = HostReservation.objects.select_for_update().filter(
            live_class=live_class,
            status=HostReservation.Status.ACTIVE
        ).first()

        if not current_zmr and not current_res and not live_class.zoom_meeting_id:
            raise ZoomLifecycleError(
                f"Cannot replace Zoom meeting for LiveClass {live_class_id}: no active meeting or reservation exists to replace."
            )

        start_time = new_start_time or live_class.scheduled_start
        duration = new_duration or live_class.duration_minutes

        # Multi-host deterministic ordering
        host_ids = []
        if current_res:
            host_ids.append(current_res.host_account_id)
        if new_host_id:
            host_ids.append(new_host_id)
        host_ids = sorted(list(set(host_ids)))

        if host_ids:
            ZoomHostAccount.objects.filter(id__in=host_ids).order_by('id').select_for_update()

        # Create replacement DOP
        dop = LiveClassDurableOperation.objects.create(
            idempotency_key=f"repl-{live_class.id}-{uuid.uuid4().hex[:16]}",
            operation_type=LiveClassDurableOperation.OperationType.ZOOM_RESCHEDULE,
            status=LiveClassDurableOperation.Status.IN_PROGRESS,
            live_class=live_class,
            payload={
                "old_meeting_id": current_zmr.zoom_meeting_id if current_zmr else None,
                "title": live_class.title,
                "scheduled_start": start_time.isoformat(),
                "duration_minutes": duration
            }
        )

        # Allocate new reservation in PROVISIONAL state without touching current_res
        new_res = select_and_reserve_host(
            live_class=live_class,
            start_time=start_time,
            duration_minutes=duration,
            preferred_host_id=new_host_id,
            durable_operation=dop
        )
        new_res_id = new_res.id
        new_host_user_id = new_res.host_account.zoom_user_id
        dop_id = dop.id
        dop_version = dop.version

    # Outside lock window: create new meeting
    new_zoom = None
    zoom_error = None
    try:
        new_zoom = ZoomService.create_meeting(
            host_id=new_host_user_id,
            topic=live_class.title,
            start_time_iso=start_time.isoformat(),
            duration_minutes=duration
        )
    except (ZoomAmbiguousOutcomeError, requests.Timeout, requests.ConnectionError) as amb_err:
        zoom_error = amb_err
    except Exception as exc:
        zoom_error = exc

    repl_failure_exc = None
    new_meeting_id = None
    try:
        with transaction.atomic():
            live_class = LiveClass.objects.select_for_update().get(id=live_class_id)
            dop = LiveClassDurableOperation.objects.select_for_update().get(id=dop_id)
            new_res = HostReservation.objects.select_for_update().get(id=new_res_id)
            current_res = HostReservation.objects.select_for_update().filter(
                live_class=live_class, status=HostReservation.Status.ACTIVE
            ).first()

            if dop.version != dop_version:
                raise ConcurrencyConflictError("Fencing conflict: DOP version changed during replacement.")
            dop.version += 1

            if new_zoom:
                new_meeting_id = str(new_zoom["meeting_id"])
                # Success: NOW and ONLY NOW retire the old meeting and reservation
                if current_zmr:
                    current_zmr = ZoomMeetingRecord.objects.select_for_update().get(id=current_zmr.id)
                    current_zmr.is_current = False
                    current_zmr.state = ZoomMeetingRecord.State.REASSIGNED
                    current_zmr.save(update_fields=['is_current', 'state', 'updated_at'])

                if current_res:
                    current_res.status = HostReservation.Status.RELEASED
                    current_res.release_reason = "Superseded by replacement meeting"
                    current_res.save(update_fields=['status', 'release_reason', 'updated_at'])

                ZoomMeetingRecord.objects.create(
                    zoom_meeting_id=new_meeting_id,
                    topic=live_class.title,
                    state=ZoomMeetingRecord.State.ASSIGNED,
                    live_class=live_class,
                    host_account=new_res.host_account,
                    scheduled_start=new_res.scheduled_start,
                    scheduled_end=new_res.scheduled_end,
                    is_current=True,
                    generation=new_res.generation,
                    durable_operation=dop
                )

                new_res.status = HostReservation.Status.ACTIVE
                new_res.save(update_fields=['status', 'updated_at'])

                dop.status = LiveClassDurableOperation.Status.COMPLETED
                dop.response_payload = new_zoom
                dop.save(update_fields=['status', 'response_payload', 'version', 'updated_at'])

                live_class.zoom_meeting_id = new_meeting_id
                live_class.zoom_uuid = new_zoom.get("uuid", "")
                live_class.save(update_fields=['zoom_meeting_id', 'zoom_uuid'])

                ZoomLifecycleOutbox.objects.create(
                    aggregate_id=live_class.id,
                    event_type="ZOOM_MEETING_REPLACED",
                    status=ZoomLifecycleOutbox.Status.PENDING,
                    payload={"new_meeting_id": new_meeting_id, "live_class_id": live_class.id}
                )
                return {"status": "SUCCESS", "new_meeting_id": new_meeting_id}

            elif isinstance(zoom_error, (ZoomAmbiguousOutcomeError, requests.Timeout, requests.ConnectionError)):
                # Ambiguous: Keep old meeting ACTIVE; hold candidate host reservation in MANUAL_REVIEW
                new_res.status = HostReservation.Status.MANUAL_REVIEW
                new_res.release_reason = f"Ambiguous remote outcome during replacement CREATE: {zoom_error}. Held."
                new_res.save(update_fields=['status', 'release_reason', 'updated_at'])

                dop.status = LiveClassDurableOperation.Status.OUTCOME_UNKNOWN
                dop.error_message = f"Ambiguous outcome during replacement: {zoom_error}"
                dop.save(update_fields=['status', 'error_message', 'version', 'updated_at'])

                logger.error(f"[ZoomLifecycle] Replacement outcome ambiguous for LC {live_class_id}: {zoom_error}")
                return {
                    "status": "OUTCOME_UNKNOWN",
                    "message": "Replacement outcome ambiguous; existing meeting preserved.",
                    "dop_id": dop.id
                }

            else:
                # Definite failure: Cancel new reservation, keep old meeting completely ACTIVE
                new_res.status = HostReservation.Status.CANCELLED
                new_res.release_reason = f"Replacement creation failed: {zoom_error}"
                new_res.save(update_fields=['status', 'release_reason', 'updated_at'])

                dop.status = LiveClassDurableOperation.Status.FAILED
                dop.error_message = str(zoom_error)
                dop.save(update_fields=['status', 'error_message', 'version', 'updated_at'])

                repl_failure_exc = ZoomLifecycleError(f"Replacement meeting creation failed: {zoom_error}. Existing meeting preserved.")

    except Exception as phase3_exc:
        # DEF-01: Durable recovery if DB commit fails after remote creation
        if new_zoom and new_meeting_id:
            logger.critical(
                f"[ZoomLifecycle] DB commit failed after remote creation of meeting {new_meeting_id} on LC {live_class_id}: {phase3_exc}. "
                "Engaging durable recovery to prevent untracked orphan."
            )
            try:
                with transaction.atomic():
                    dop = LiveClassDurableOperation.objects.select_for_update().get(id=dop_id)
                    dop.status = LiveClassDurableOperation.Status.MANUAL_REVIEW_REQUIRED
                    dop.error_message = f"Phase 3 commit failed after remote creation: {str(phase3_exc)}"
                    dop.payload["created_remote_meeting_id"] = new_meeting_id
                    dop.payload["created_remote_uuid"] = new_zoom.get("uuid", "")
                    dop.response_payload = new_zoom
                    dop.save(update_fields=['status', 'error_message', 'payload', 'response_payload', 'updated_at'])

                    new_res = HostReservation.objects.select_for_update().get(id=new_res_id)
                    new_res.status = HostReservation.Status.MANUAL_REVIEW
                    new_res.release_reason = f"Commit failed after Zoom CREATE; meeting {new_meeting_id} requires teardown."
                    new_res.save(update_fields=['status', 'release_reason', 'updated_at'])

                    ZoomLifecycleOutbox.objects.create(
                        aggregate_id=live_class_id,
                        event_type="ZOOM_MEETING_COMMIT_FAILED_NEEDS_TEARDOWN",
                        status=ZoomLifecycleOutbox.Status.PENDING,
                        payload={
                            "meeting_id": new_meeting_id,
                            "host_id": new_host_user_id,
                            "dop_id": dop_id,
                            "error": str(phase3_exc)
                        }
                    )
            except Exception as rec_err:
                logger.critical(f"[ZoomLifecycle] Durable recovery persistence failure: {rec_err}")
        raise

    if repl_failure_exc:
        raise repl_failure_exc


@execute_with_deadlock_retry()
def cancel_zoom_meeting(live_class_id: int, reason: str = "Class cancelled") -> Dict[str, Any]:
    """
    Cancels an active Zoom meeting:
    1. Locks LiveClass FOR UPDATE (Level 1)
    2. Identifies active ZMR and Reservation
    3. Deletes meeting on Zoom cloud (outside DB lock)
    4. Safe handling: On ambiguous DELETE outcome, reservation is HELD in MANUAL_REVIEW.
       Only on definite 204 or 404 is the reservation released.
    5. Clears LiveClass Zoom pointers only upon confirmed deletion.
    """
    meeting_id = None
    with transaction.atomic():
        live_class = LiveClass.objects.select_for_update().get(id=live_class_id)
        zmr = ZoomMeetingRecord.objects.select_for_update().filter(
            live_class=live_class, is_current=True
        ).first()
        res = HostReservation.objects.select_for_update().filter(
            live_class=live_class,
            status__in=[HostReservation.Status.ACTIVE, HostReservation.Status.PROVISIONAL]
        ).first()

        if zmr:
            meeting_id = zmr.zoom_meeting_id
        elif live_class.zoom_meeting_id:
            meeting_id = live_class.zoom_meeting_id

    # Call Zoom delete outside lock
    delete_error = None
    delete_success = False
    if meeting_id:
        try:
            ZoomService.delete_meeting(meeting_id)
            delete_success = True
        except ZoomNotFoundError:
            delete_success = True
            logger.info(f"[ZoomLifecycle] Meeting {meeting_id} already not found on Zoom (404); treated as deleted.")
        except (ZoomTransientError, requests.Timeout, requests.ConnectionError, ZoomRateLimitError) as amb_err:
            delete_error = amb_err
            logger.error(f"[ZoomLifecycle] Ambiguous error deleting meeting {meeting_id}: {amb_err}")
        except Exception as exc:
            delete_error = exc

    cancel_failure_exc = None
    with transaction.atomic():
        live_class = LiveClass.objects.select_for_update().get(id=live_class_id)
        if zmr:
            zmr = ZoomMeetingRecord.objects.select_for_update().get(id=zmr.id)
        if res:
            res = HostReservation.objects.select_for_update().get(id=res.id)

        if delete_success or not meeting_id:
            if zmr:
                zmr.state = ZoomMeetingRecord.State.RELEASED
                zmr.is_current = False
                zmr.save(update_fields=['state', 'is_current', 'updated_at'])

            if res:
                res.status = HostReservation.Status.CANCELLED
                res.release_reason = reason
                res.save(update_fields=['status', 'release_reason', 'updated_at'])

            live_class.zoom_meeting_id = ''
            live_class.zoom_uuid = ''
            live_class.save(update_fields=['zoom_meeting_id', 'zoom_uuid'])
            return {"status": "SUCCESS", "cancelled_meeting_id": meeting_id}
        else:
            # Ambiguous DELETE: DO NOT RELEASE RESERVATION
            if res:
                res.status = HostReservation.Status.MANUAL_REVIEW
                res.release_reason = f"Ambiguous delete outcome on Zoom: {delete_error}. Host held."
                res.save(update_fields=['status', 'release_reason', 'updated_at'])

            if zmr:
                zmr.state = ZoomMeetingRecord.State.COMPENSATING
                if zmr.live_class_id:
                    zmr.historical_live_class_id = zmr.live_class_id
                zmr.live_class = None
                zmr.is_current = False
                zmr.save(update_fields=['state', 'historical_live_class_id', 'live_class', 'is_current', 'updated_at'])

            cancel_failure_exc = ZoomLifecycleError(
                f"Cannot release host reservation: ambiguous outcome deleting meeting {meeting_id} on Zoom ({delete_error}). Host held in MANUAL_REVIEW."
            )

    if cancel_failure_exc:
        raise cancel_failure_exc


@execute_with_deadlock_retry()
def compensate_zoom_operation(dop_id: int, worker_id: Optional[uuid.UUID] = None, zoom_client=None) -> Dict[str, Any]:
    """
    Executes compensation for an incomplete saga.

    Release-closure B9:
    - Locks in the global order L1 LiveClass -> L2 DOP -> L3 ZoomMeetingRecord -> L5 HostReservation
      in both transactions (previously DOP before LiveClass, the inverse of every other path).
    - The Zoom DELETE is made with no lock held; the second transaction re-checks the claim
      (version + lease worker) before changing anything.
    - Fails closed: the host is released only when no remote meeting exists or its DELETE is
      confirmed (HTTP 204/404). Otherwise the DOP is quarantined (COMPENSATION_REQUIRED), its
      reservations are held (MANUAL_REVIEW) and a durable teardown is queued in the outbox.
    """
    worker_id = worker_id or uuid.uuid4()
    terminal_reservation = (HostReservation.Status.RELEASED, HostReservation.Status.COMPLETED,
                            HostReservation.Status.CANCELLED)

    with transaction.atomic():
        lc_id = LiveClassDurableOperation.objects.filter(id=dop_id).values_list('live_class_id', flat=True).first()
        if lc_id:
            LiveClass.objects.select_for_update().filter(id=lc_id).first()                     # L1
        dop = LiveClassDurableOperation.objects.select_for_update().get(id=dop_id)            # L2
        now = timezone.now()
        if dop.lease_expires_at and dop.lease_expires_at > now and dop.lease_worker and dop.lease_worker != worker_id:
            raise ConcurrencyConflictError(
                f"Fencing violation: DOP {dop_id} is leased to worker {dop.lease_worker} until {dop.lease_expires_at}"
            )
        dop.lease_worker = worker_id
        dop.lease_expires_at = now + datetime.timedelta(seconds=RECOVERY_LEASE_SECONDS)
        dop.version += 1
        dop.save(update_fields=['lease_worker', 'lease_expires_at', 'version', 'updated_at'])
        claim_version = dop.version
        zmr = ZoomMeetingRecord.objects.select_for_update().filter(durable_operation=dop).first()  # L3
        zmr_id = zmr.id if zmr else None
        meeting_to_delete = (zmr.zoom_meeting_id if zmr and zmr.state not in
                             (ZoomMeetingRecord.State.RELEASED, ZoomMeetingRecord.State.COMPENSATED) else None)

    outcome = zoom_delete_outcome(meeting_to_delete, zoom_client=zoom_client) if meeting_to_delete else None
    confirmed = outcome is None or outcome.confirmed_gone

    with transaction.atomic():
        live_class = LiveClass.objects.select_for_update().filter(id=lc_id).first() if lc_id else None  # L1
        dop = LiveClassDurableOperation.objects.select_for_update().get(id=dop_id)                    # L2
        if dop.version != claim_version or dop.lease_worker != worker_id:
            raise ConcurrencyConflictError(f"DOP #{dop_id} compensation claim was lost; nothing changed.")
        zmr = ZoomMeetingRecord.objects.select_for_update().get(id=zmr_id) if zmr_id else None          # L3
        reservations = list(HostReservation.objects.select_for_update().filter(
            durable_operation=dop).exclude(status__in=terminal_reservation))                           # L5
        dop.lease_expires_at = None

        if confirmed:
            if zmr:
                zmr.state = ZoomMeetingRecord.State.COMPENSATED
                if zmr.live_class_id:
                    zmr.historical_live_class_id = zmr.live_class_id
                zmr.live_class = None
                zmr.is_current = False
                zmr.save(update_fields=['state', 'historical_live_class_id', 'live_class', 'is_current', 'updated_at'])
            for res in reservations:
                res.status = HostReservation.Status.CANCELLED
                res.release_reason = "Saga compensation completed."
                res.save(update_fields=['status', 'release_reason', 'updated_at'])
            dop.status = LiveClassDurableOperation.Status.COMPENSATED
            dop.save(update_fields=['status', 'lease_expires_at', 'updated_at'])
            if live_class and meeting_to_delete and live_class.zoom_meeting_id == meeting_to_delete:
                live_class.zoom_meeting_id = ''
                live_class.zoom_uuid = ''
                live_class.save(update_fields=['zoom_meeting_id', 'zoom_uuid'])
            if outcome is not None and meeting_to_delete:
                logger.info(f"[ZoomLifecycle] Compensation of DOP {dop_id}: meeting {meeting_to_delete} "
                            f"deletion confirmed (HTTP {outcome.http_status}).")
            return {"status": "COMPENSATED", "dop_id": dop_id}

        ZoomLifecycleOutbox.objects.create(
            aggregate_id=lc_id or dop_id,
            event_type="ZOOM_MEETING_COMMIT_FAILED_NEEDS_TEARDOWN",
            status=ZoomLifecycleOutbox.Status.PENDING,
            payload={"meeting_id": meeting_to_delete, "dop_id": dop_id, "reason": "compensation_delete_unconfirmed"}
        )
        dop.status = LiveClassDurableOperation.Status.COMPENSATION_REQUIRED
        dop.quarantined_at = timezone.now()
        dop.response_payload = {**(dop.response_payload or {}), "orphan_meeting_id": meeting_to_delete}
        dop.error_message = (f"Compensation held: delete of meeting {meeting_to_delete} not confirmed "
                             f"({outcome.result}, HTTP {outcome.http_status}); teardown queued.")
        dop.save(update_fields=['status', 'quarantined_at', 'response_payload', 'error_message',
                                'lease_expires_at', 'updated_at'])
        for res in reservations:
            res.status = HostReservation.Status.MANUAL_REVIEW
            res.release_reason = f"Held: compensation delete of {meeting_to_delete} not confirmed."
            res.save(update_fields=['status', 'release_reason', 'updated_at'])
        return {"status": "HELD", "dop_id": dop_id, "meeting_id": meeting_to_delete}


# ---------------------------------------------------------------------------
# Outbox Queue Processing
# ---------------------------------------------------------------------------

def process_outbox_message(outbox_id: int, worker_id: Optional[uuid.UUID] = None, lease_seconds: int = 60) -> bool:
    """
    Processes a single outbox message with durable lease ownership and optimistic fencing.
    Resolves DEF-04:
    - Atomic claim under row lock prevents competing workers from dual-processing.
    - Stale IN_FLIGHT messages whose lease expired are safely reclaimable.
    - Version/lease verification on completion guarantees stale workers cannot overwrite newer state.
    """
    if not worker_id:
        worker_id = uuid.uuid4()

    now = timezone.now()
    claim_version = None

    # Step 1: Atomic lease claim under select_for_update()
    with transaction.atomic():
        try:
            msg = ZoomLifecycleOutbox.objects.select_for_update().get(id=outbox_id)
        except ZoomLifecycleOutbox.DoesNotExist:
            return False

        is_stale_in_flight = (
            msg.status == ZoomLifecycleOutbox.Status.IN_FLIGHT and
            msg.lease_expires_at and msg.lease_expires_at <= now
        )
        is_eligible = (
            msg.status == ZoomLifecycleOutbox.Status.PENDING or
            (msg.status == ZoomLifecycleOutbox.Status.RETRYING and (not msg.next_retry_at or msg.next_retry_at <= now)) or
            is_stale_in_flight
        )

        if not is_eligible:
            return False

        msg.status = ZoomLifecycleOutbox.Status.IN_FLIGHT
        msg.lease_worker = worker_id
        msg.lease_expires_at = now + datetime.timedelta(seconds=lease_seconds)
        msg.version += 1
        msg.save(update_fields=['status', 'lease_worker', 'lease_expires_at', 'version', 'updated_at'])
        claim_version = msg.version
        event_type = msg.event_type
        payload = msg.payload or {}

    # Step 2: Execute event handler outside DB lock
    process_success = False
    process_error = None
    try:
        if event_type == "ZOOM_MEETING_COMMIT_FAILED_NEEDS_TEARDOWN":
            teardown_meeting_id = payload.get("meeting_id")
            if teardown_meeting_id:
                try:
                    ZoomService.delete_meeting(teardown_meeting_id)
                except ZoomNotFoundError:
                    pass
        logger.info(f"[Outbox] Successfully dispatched {event_type} on aggregate {msg.aggregate_id}")
        process_success = True
    except Exception as exc:
        process_error = exc
        logger.error(f"[Outbox] Error processing outbox #{outbox_id} ({event_type}): {exc}")

    # Step 3: Atomic completion under version fencing
    with transaction.atomic():
        msg = ZoomLifecycleOutbox.objects.select_for_update().get(id=outbox_id)
        # Fencing check: verify our lease was not revoked or stolen
        if msg.version != claim_version or msg.lease_worker != worker_id:
            logger.warning(f"[Outbox] Fencing violation: Worker {worker_id} lost lease on outbox #{outbox_id}.")
            return False

        msg.version += 1
        msg.lease_worker = None
        msg.lease_expires_at = None

        if process_success:
            msg.status = ZoomLifecycleOutbox.Status.COMPLETED
            msg.save(update_fields=['status', 'lease_worker', 'lease_expires_at', 'version', 'updated_at'])
            return True
        else:
            msg.retry_count += 1
            msg.error_message = str(process_error)
            if msg.retry_count >= msg.max_retries:
                msg.status = ZoomLifecycleOutbox.Status.DEAD_LETTER
            else:
                msg.status = ZoomLifecycleOutbox.Status.RETRYING
                msg.next_retry_at = timezone.now() + datetime.timedelta(seconds=5 * (2 ** msg.retry_count))
            msg.save(update_fields=['status', 'retry_count', 'error_message', 'next_retry_at', 'lease_worker', 'lease_expires_at', 'version', 'updated_at'])
            return False


def recover_stale_outbox_messages(stale_seconds: int = 300) -> int:
    """
    Watchdog recovery for crashed or abandoned worker outbox tasks.
    Scans for messages in IN_FLIGHT whose lease has expired or were last updated > stale_seconds ago.
    Safely resets them to RETRYING with next_retry_at = now.
    """
    now = timezone.now()
    stale_cutoff = now - datetime.timedelta(seconds=stale_seconds)

    with transaction.atomic():
        stale_msgs = list(
            ZoomLifecycleOutbox.objects.select_for_update().filter(
                status=ZoomLifecycleOutbox.Status.IN_FLIGHT
            ).filter(
                models.Q(lease_expires_at__lte=now) |
                models.Q(lease_expires_at__isnull=True, updated_at__lte=stale_cutoff)
            )
        )
        recovered_count = 0
        for msg in stale_msgs:
            msg.status = ZoomLifecycleOutbox.Status.RETRYING
            msg.next_retry_at = now
            msg.lease_worker = None
            msg.lease_expires_at = None
            msg.version += 1
            msg.error_message = "Recovered by watchdog from crashed worker lease."
            msg.save(update_fields=['status', 'next_retry_at', 'lease_worker', 'lease_expires_at', 'version', 'error_message', 'updated_at'])
            recovered_count += 1
            logger.info(f"[OutboxWatchdog] Recovered stale outbox message #{msg.id}")
        return recovered_count


def process_outbox_queue(batch_size: int = 50, worker_id: Optional[uuid.UUID] = None) -> int:
    """
    Polls pending or eligible retrying outbox messages and executes them safely.
    First performs a watchdog sweep for stale IN_FLIGHT records.
    """
    if not worker_id:
        worker_id = uuid.uuid4()

    recover_stale_outbox_messages()

    now = timezone.now()
    pending_ids = list(
        ZoomLifecycleOutbox.objects.filter(
            models.Q(status=ZoomLifecycleOutbox.Status.PENDING) |
            models.Q(status=ZoomLifecycleOutbox.Status.RETRYING, next_retry_at__lte=now)
        ).order_by('created_at')[:batch_size].values_list('id', flat=True)
    )

    success_count = 0
    for outbox_id in pending_ids:
        if process_outbox_message(outbox_id, worker_id=worker_id):
            success_count += 1
    return success_count


class ZoomCancellationSagaWorker:
    """
    4-Phase Execution Worker for Zoom Meeting Cancellation:
    Phase 1: Claim & Lease (transitions meeting state to COMPENSATING, locks resources)
    Phase 2: Remote Zoom DELETE (invoked OUTSIDE locks with bounded timeouts)
    Phase 3: Durable evidence persistence (HTTP 204 or 404/3001 persisted in ZoomVerifiedEvidence)
    Phase 4: Finalization & replacement preservation (transitions meeting to COMPENSATED, releases host reservation, preserves replacement meeting if present)
    """

    @classmethod
    def claim_and_lease(cls, live_class_id: int, worker_id: Optional[uuid.UUID] = None, lease_seconds: int = 60) -> Optional[dict]:
        """
        Release-closure B7: the lease is DURABLE -- persisted on the ZoomMeetingRecord
        (lease_worker, lease_expires_at, owner_token, version) -- so finalization and the
        watchdog can fence against it. The class id is kept in historical_live_class_id.
        """
        if not worker_id:
            worker_id = uuid.uuid4()
        now = timezone.now()
        lease_expires = now + datetime.timedelta(seconds=lease_seconds)

        with transaction.atomic():
            # Level 1: Lock LiveClass
            lc = LiveClass.objects.select_for_update().filter(id=live_class_id).first()
            if not lc:
                return None

            # Fence any in-flight DOPs for this LiveClass to prevent delayed creation orphans
            in_flight_dops = list(LiveClassDurableOperation.objects.select_for_update().filter(
                live_class=lc,
                status__in=[
                    LiveClassDurableOperation.Status.IN_PROGRESS,
                    LiveClassDurableOperation.Status.OUTCOME_UNKNOWN
                ]
            ))
            for dop in in_flight_dops:
                dop.version += 1
                dop.status = LiveClassDurableOperation.Status.COMPENSATION_REQUIRED
                dop.error_message = "Cancelled by ZoomCancellationSagaWorker while creation was in flight."
                dop.save(update_fields=['version', 'status', 'error_message', 'updated_at'])

            # Level 3: Lock current ZoomMeetingRecord
            zmr = ZoomMeetingRecord.objects.select_for_update().filter(
                live_class=lc,
                is_current=True
            ).first()
            if not zmr:
                if in_flight_dops:
                    return {
                        "live_class_id": lc.id,
                        "meeting_id": None,
                        "worker_id": str(worker_id),
                        "lease_expires": lease_expires.isoformat(),
                        "already_finalized": False,
                        "fenced_in_flight_dops": [d.id for d in in_flight_dops]
                    }
                return None

            if zmr.state in ('COMPENSATED', 'RELEASED'):
                return {"already_finalized": True, "meeting_id": zmr.zoom_meeting_id}

            zmr.state = 'COMPENSATING'
            zmr.historical_live_class_id = lc.id
            zmr.live_class = None
            zmr.owner_token = worker_id
            zmr.lease_worker = worker_id
            zmr.lease_expires_at = lease_expires
            zmr.version += 1
            zmr.save(update_fields=['state', 'historical_live_class_id', 'live_class', 'owner_token',
                                    'lease_worker', 'lease_expires_at', 'version', 'updated_at'])

            return {
                "live_class_id": lc.id,
                "zmr_id": zmr.id,
                "meeting_id": zmr.zoom_meeting_id,
                "worker_id": str(worker_id),
                "lease_expires": lease_expires.isoformat(),
                "already_finalized": False
            }

    @classmethod
    def execute_remote_delete(cls, meeting_id: str, zoom_client=None) -> dict:
        """
        Executes external HTTP DELETE to Zoom. Runs strictly outside DB transaction locks.
        Phase B.5 repair (B2): the result is classified from the actual client
        response/exception type (never by message substrings) and carries the
        ZoomCallOutcome needed to record evidence. Unconfirmed deletes are
        returned, not raised, with confirmed=False.
        """
        if not meeting_id:
            return {"status": None, "action": "no_remote_meeting", "confirmed": True, "outcome": None, "body": {}}
        outcome = zoom_delete_outcome(meeting_id, zoom_client=zoom_client)
        return {"status": outcome.http_status, "action": outcome.result, "confirmed": outcome.confirmed_gone,
                "outcome": outcome, "body": outcome.as_dict()}

    @classmethod
    def persist_cancellation_evidence(cls, meeting_id: str, delete_result, worker_id: str = "",
                                      dop_id: Optional[int] = None):
        """
        Records CANCELLATION_SAGA_DELETE evidence from the result of
        execute_remote_delete(). HTTP statuses cannot be supplied by the caller.
        """
        outcome = delete_result.get("outcome") if isinstance(delete_result, dict) else delete_result
        if not isinstance(outcome, ZoomCallOutcome):
            raise TypeError("persist_cancellation_evidence requires the result of execute_remote_delete(); "
                            "HTTP statuses cannot be supplied by the caller.")
        if str(outcome.meeting_id) != str(meeting_id):
            raise ValueError(f"Outcome is for meeting {outcome.meeting_id}, not {meeting_id}.")
        return _record_evidence_from_outcome(outcome, "CANCELLATION_SAGA_DELETE", dop_id=dop_id,
                                             worker_id=str(worker_id))

    @classmethod
    def _bound_reservation(cls, zmr, live_class_id):
        """
        The single reservation provably tied to THIS meeting record: same DOP, or else the
        same class + host + generation (+ exact window when known). Never "any reservation of
        the class". Returns None unless exactly one candidate exists.
        """
        held = [HostReservation.Status.ACTIVE, HostReservation.Status.PROVISIONAL,
                HostReservation.Status.DRAINING, HostReservation.Status.MANUAL_REVIEW]
        qs = HostReservation.objects.select_for_update().filter(
            status__in=held, host_account_id=zmr.host_account_id, generation=zmr.generation)
        if zmr.durable_operation_id:
            qs = qs.filter(durable_operation_id=zmr.durable_operation_id)
        elif live_class_id:
            qs = qs.filter(live_class_id=live_class_id)
        elif zmr.scheduled_start and zmr.scheduled_end:
            qs = qs.filter(scheduled_start=zmr.scheduled_start, scheduled_end=zmr.scheduled_end)
        else:
            return None
        if zmr.scheduled_start and zmr.scheduled_end:
            qs = qs.filter(scheduled_start=zmr.scheduled_start, scheduled_end=zmr.scheduled_end)
        candidates = list(qs[:2])
        if len(candidates) != 1:
            if candidates:
                logger.warning(f"[CancellationSaga] Meeting {zmr.zoom_meeting_id}: {len(candidates)}+ candidate "
                               f"reservations; none cancelled (held for manual review).")
            return None
        return candidates[0]

    @classmethod
    def finalize_cancellation(cls, live_class_id: Optional[int], worker_id: Optional[uuid.UUID] = None,
                              replacement_meeting_id: Optional[str] = None) -> bool:
        """
        Release-closure B7: finalizes ONLY the meeting record this worker holds the lease on,
        and cancels ONLY the reservation bound to that record. Returns False (changing
        nothing) when the worker no longer owns the lease or the record belongs to another class.
        """
        if not worker_id:
            raise ValueError("finalize_cancellation requires the worker_id that holds the lease.")
        with transaction.atomic():
            # L1: Lock LiveClass
            lc = LiveClass.objects.select_for_update().filter(id=live_class_id).first() if live_class_id else None
            # L3: the record leased by this worker
            zmr = ZoomMeetingRecord.objects.select_for_update().filter(
                lease_worker=worker_id, state='COMPENSATING').first()
            if not zmr:
                return False
            if zmr.historical_live_class_id and live_class_id and zmr.historical_live_class_id != live_class_id:
                return False
            class_id = zmr.historical_live_class_id or live_class_id

            # L5: only the reservation bound to this meeting record
            res = cls._bound_reservation(zmr, class_id)

            zmr.state = 'COMPENSATED'
            zmr.live_class = None
            zmr.is_current = False
            zmr.lease_worker = None
            zmr.lease_expires_at = None
            zmr.version += 1
            zmr.save(update_fields=['state', 'live_class', 'is_current', 'lease_worker', 'lease_expires_at',
                                    'version', 'updated_at'])

            if res:
                res.status = HostReservation.Status.CANCELLED
                res.release_reason = f"Cancelled via ZoomCancellationSagaWorker (meeting {zmr.zoom_meeting_id} deleted)"
                res.save(update_fields=['status', 'release_reason', 'updated_at'])

            if lc:
                # Preserve replacement meeting if scheduled
                if replacement_meeting_id:
                    if lc.zoom_meeting_id != replacement_meeting_id:
                        lc.zoom_meeting_id = replacement_meeting_id
                        lc.save(update_fields=['zoom_meeting_id'])
                elif str(lc.zoom_meeting_id) == str(zmr.zoom_meeting_id):
                    lc.zoom_meeting_id = ""
                    lc.meeting_url = ""
                    lc.host_url = ""
                    lc.save(update_fields=['zoom_meeting_id', 'meeting_url', 'host_url'])

            return True

    @classmethod
    def run_watchdog_recovery(cls, stale_seconds: int = 60, zoom_client=None, lease_seconds: int = 120) -> int:
        """
        Release-closure B7: takes over only COMPENSATING records whose durable lease has
        expired (or, for lease-less legacy rows, that are stale), by claiming the lease under
        lock before any remote call. An unconfirmed delete releases the lease for the next
        sweep and changes nothing else; finalization is fenced by that lease.
        """
        now = timezone.now()
        stale_cutoff = now - datetime.timedelta(seconds=stale_seconds)
        candidates = list(
            ZoomMeetingRecord.objects.filter(state='COMPENSATING').filter(
                models.Q(lease_expires_at__lte=now)
                | models.Q(lease_expires_at__isnull=True, updated_at__lte=stale_cutoff)
            ).values_list('id', 'historical_live_class_id')
        )
        recovered = 0
        for zmr_id, lc_id in candidates:
            token = uuid.uuid4()
            with transaction.atomic():
                if lc_id:
                    LiveClass.objects.select_for_update().filter(id=lc_id).first()          # L1
                zmr = ZoomMeetingRecord.objects.select_for_update().filter(id=zmr_id).first()  # L3
                takeover_now = timezone.now()
                if (not zmr or zmr.state != 'COMPENSATING'
                        or (zmr.lease_expires_at and zmr.lease_expires_at > takeover_now)
                        or (zmr.lease_expires_at is None and zmr.updated_at > stale_cutoff)):
                    continue
                zmr.lease_worker = token
                zmr.owner_token = token
                zmr.lease_expires_at = takeover_now + datetime.timedelta(seconds=lease_seconds)
                zmr.version += 1
                zmr.save(update_fields=['lease_worker', 'owner_token', 'lease_expires_at', 'version', 'updated_at'])
                meeting_id = zmr.zoom_meeting_id

            delete_res = cls.execute_remote_delete(meeting_id, zoom_client=zoom_client)
            confirmed = delete_res["confirmed"]
            if confirmed and delete_res["outcome"] is not None:
                try:
                    cls.persist_cancellation_evidence(meeting_id, delete_res, worker_id="watchdog")
                except EvidenceRejectedError as exc:
                    logger.warning(f"[CancellationWatchdog] {exc}")
                    confirmed = False
            if not confirmed:
                # Not confirmed gone: release our lease so the next sweep retries; nothing else changes.
                ZoomMeetingRecord.objects.filter(id=zmr_id, lease_worker=token).update(lease_expires_at=timezone.now())
                logger.warning(f"[CancellationWatchdog] Delete of meeting {meeting_id} not confirmed "
                               f"({delete_res['action']}, HTTP {delete_res['status']}); left COMPENSATING.")
                continue
            if cls.finalize_cancellation(lc_id, worker_id=token):
                recovered += 1
                logger.info(f"[CancellationWatchdog] Recovered stale compensating meeting #{meeting_id}")

        return recovered


class QuarantinePeriodActiveError(Exception):
    """Raised when quarantine resolution is attempted before the 15-minute grace period has elapsed."""
    pass


class ZoomQuarantineService:
    """
    Phase B.5 repair (B3). Quarantine resolution fails closed:
    - only DOPs in a quarantine status (OUTCOME_UNKNOWN / COMPENSATION_REQUIRED),
    - only after the full grace period measured from quarantined_at (no bypass flag),
    - under L1 (LiveClass) -> L2 (DOP) -> L5 (HostReservation) locks with a lease
      claim and version fencing between the remote checks and finalization,
    - every known meeting id must answer HTTP 404 to two independent authenticated
      GETs through the real Zoom client, and the host's meeting listing must
      succeed and show nothing that could belong to this operation,
    - finalization re-checks the persisted DUAL_GET evidence (bound to this DOP,
      gathered after the grace period, fresh) before releasing anything.
    Any probe failure, timeout, auth failure, ambiguous outcome or remaining
    meeting raises and leaves the reservation held.
    """

    @classmethod
    def verify_and_resolve_quarantine(cls, dop_id: int, zoom_client=None, worker_id=None) -> dict:
        from courses.models import ZoomVerifiedEvidence

        worker_id = worker_id or uuid.uuid4()
        quarantine_statuses = LiveClassDurableOperation.QUARANTINE_STATUSES
        held_statuses = [HostReservation.Status.PROVISIONAL, HostReservation.Status.MANUAL_REVIEW]

        # Phase 1: lock, validate state/grace/lease, claim.
        with transaction.atomic():
            peek = LiveClassDurableOperation.objects.filter(id=dop_id).values('live_class_id').first()
            if peek is None:
                raise ValueError(f"DurableOperation #{dop_id} does not exist.")
            lc_id = peek['live_class_id']
            if lc_id:
                LiveClass.objects.select_for_update().filter(id=lc_id).first()
            dop = LiveClassDurableOperation.objects.select_for_update().get(id=dop_id)
            if dop.status not in quarantine_statuses:
                raise QuarantineStateError(f"DOP #{dop_id} is {dop.status}; only quarantined operations can be resolved.")
            now = timezone.now()
            if dop.lease_expires_at and dop.lease_expires_at > now and dop.lease_worker != worker_id:
                raise ConcurrencyConflictError(f"DOP #{dop_id} is leased to {dop.lease_worker} until {dop.lease_expires_at}.")
            quarantine_start = dop.quarantined_at or dop.updated_at
            elapsed = (now - quarantine_start).total_seconds()
            if elapsed < QUARANTINE_GRACE_SECONDS:
                raise QuarantinePeriodActiveError(
                    f"Quarantine active. {int(QUARANTINE_GRACE_SECONDS - elapsed)} seconds remaining in the "
                    f"mandatory {QUARANTINE_GRACE_SECONDS // 60}-minute grace period.")
            reservations = list(HostReservation.objects.select_for_update().filter(
                durable_operation=dop, status__in=held_statuses))
            dop.version += 1
            dop.lease_worker = worker_id
            dop.lease_expires_at = now + datetime.timedelta(seconds=RECOVERY_LEASE_SECONDS)
            dop.save(update_fields=['version', 'lease_worker', 'lease_expires_at', 'updated_at'])
            claim_version = dop.version
            payload = dict(dop.payload or {})
            response_payload = dict(dop.response_payload or {})
            meeting_ids = sorted({str(m) for m in (
                payload.get('meeting_id'), payload.get('zoom_meeting_id'),
                response_payload.get('orphan_meeting_id')) if m})
            host_ids = sorted(set(ZoomHostAccount.objects.filter(
                id__in=[r.host_account_id for r in reservations]).values_list('zoom_user_id', flat=True)))

        # Phase 2: remote verification with no locks held.
        failures = []
        for mid in meeting_ids:
            for probe in (1, 2):
                outcome = zoom_get_outcome(mid, zoom_client=zoom_client)
                if outcome.result != ZoomCallOutcome.CONFIRMED_ABSENT:
                    failures.append(f"meeting {mid} GET probe {probe}: {outcome.result} (HTTP {outcome.http_status})")
                    break
                try:
                    _record_evidence_from_outcome(outcome, "DUAL_GET_QUARANTINE_VERIFICATION", dop_id=dop_id,
                                                  worker_id=f"quarantine:{worker_id}")
                except EvidenceRejectedError as exc:
                    failures.append(str(exc))
                    break

        can_correlate = bool((payload.get('title') or payload.get('topic')) and payload.get('scheduled_start'))
        if host_ids and (can_correlate or meeting_ids):
            for host_user_id in host_ids:
                try:
                    meetings = _list_host_meetings(host_user_id, zoom_client=zoom_client)
                except Exception as exc:
                    failures.append(f"meeting listing for host {host_user_id} failed: {type(exc).__name__}")
                    continue
                blockers = _release_blocking_meetings(meetings, payload, meeting_ids)
                if blockers:
                    failures.append(f"host {host_user_id} still lists meeting(s) {blockers} that may belong to this operation")
        elif not meeting_ids:
            failures.append("no meeting id and no host/correlation data: remote state cannot be verified")

        if failures:
            with transaction.atomic():
                dop = LiveClassDurableOperation.objects.select_for_update().get(id=dop_id)
                if dop.version == claim_version and dop.lease_worker == worker_id:
                    dop.version += 1
                    dop.lease_worker = None
                    dop.lease_expires_at = None
                    dop.error_message = "Quarantine verification failed; host held: " + "; ".join(failures)
                    dop.save(update_fields=['version', 'lease_worker', 'lease_expires_at', 'error_message', 'updated_at'])
            raise QuarantineVerificationError(f"DOP #{dop_id} remains quarantined: " + "; ".join(failures))

        # Phase 3: finalize under locks, re-checking claim and persisted evidence.
        with transaction.atomic():
            if lc_id:
                LiveClass.objects.select_for_update().filter(id=lc_id).first()
            dop = LiveClassDurableOperation.objects.select_for_update().get(id=dop_id)
            if dop.version != claim_version or dop.lease_worker != worker_id or dop.status not in quarantine_statuses:
                raise ConcurrencyConflictError(f"DOP #{dop_id} changed during quarantine verification; nothing released.")
            now = timezone.now()
            evidence_floor = max(quarantine_start + datetime.timedelta(seconds=QUARANTINE_GRACE_SECONDS),
                                 now - datetime.timedelta(seconds=EVIDENCE_FRESHNESS_SECONDS))
            for mid in meeting_ids:
                probes = ZoomVerifiedEvidence.objects.filter(
                    meeting_id=mid, dop_id=dop.id, evidence_type="DUAL_GET_QUARANTINE_VERIFICATION",
                    http_status=404, verified_at__gte=evidence_floor).count()
                if probes < 2:
                    raise QuarantineVerificationError(
                        f"DOP #{dop_id}: meeting {mid} lacks two fresh, bound 404 probes ({probes}); host held.")
            dop.status = LiveClassDurableOperation.Status.COMPENSATED
            dop.version += 1
            dop.lease_worker = None
            dop.lease_expires_at = None
            dop.error_message = f"Quarantine resolved: verified absent {meeting_ids or '(host listing clear)'}."
            dop.save(update_fields=['status', 'version', 'lease_worker', 'lease_expires_at', 'error_message', 'updated_at'])
            released = []
            for res in HostReservation.objects.select_for_update().filter(durable_operation=dop, status__in=held_statuses):
                res.status = HostReservation.Status.RELEASED
                res.release_reason = "Quarantine resolved: dual GET 404 verified and host listing clear."
                res.save(update_fields=['status', 'release_reason', 'updated_at'])
                released.append(res.id)
            ZoomLifecycleOutbox.objects.create(
                aggregate_id=dop.live_class_id or dop.id,
                event_type="TEARDOWN_QUARANTINE_VERIFIED",
                status=ZoomLifecycleOutbox.Status.COMPLETED,
                payload={"meeting_ids": meeting_ids, "dop_id": dop.id, "released_reservations": released}
            )

        return {"status": "RESOLVED", "meeting_ids": meeting_ids, "dop_id": dop_id, "released_reservations": released}


def recover_crashed_provision_dop(dop_id: int, zoom_client=None, stale_seconds: int = 60,
                                  worker_id=None) -> Dict[str, Any]:
    """
    Phase B.5 repair (B4). Recovers a provisioning operation whose worker crashed
    after (possibly) sending CREATE but before Phase 3 persisted the result.

    - Refuses while the provisioning lease is active (or, for legacy DOPs without
      a lease, until updated_at is older than stale_seconds), then takes its own
      lease and bumps the version so a late Phase 3 of the crashed worker fences.
    - Lists the host's meetings through the real client (a LIST of
      {'meeting_id', 'topic', 'start_time', 'duration', 'created_at', ...}) and
      attributes a meeting to this operation only by full correlation (topic +
      start + duration + created after the DOP + not owned by any DB record).
    - Releases the host only after a CONFIRMED delete (HTTP 204, or 404) of the
      single correlated meeting. A failed listing, no correlated meeting (absence
      in a listing is not proof), several candidates, or an unconfirmed delete
      all leave the reservation held (MANUAL_REVIEW) and the DOP quarantined
      (OUTCOME_UNKNOWN); an unconfirmed delete also enqueues a durable teardown.
    - Finalization re-checks that this worker still owns the claim.
    """
    worker_id = worker_id or uuid.uuid4()
    now = timezone.now()

    # Phase 1: claim under L1 -> L2 -> L5 locks.
    with transaction.atomic():
        peek = LiveClassDurableOperation.objects.filter(id=dop_id).values('live_class_id').first()
        if peek is None:
            raise ValueError(f"DurableOperation #{dop_id} does not exist.")
        lc_id = peek['live_class_id']
        if lc_id:
            LiveClass.objects.select_for_update().filter(id=lc_id).first()
        dop = LiveClassDurableOperation.objects.select_for_update().get(id=dop_id)
        if dop.status not in (LiveClassDurableOperation.Status.IN_PROGRESS, LiveClassDurableOperation.Status.OUTCOME_UNKNOWN):
            return {"status": "ALREADY_RESOLVED", "dop_status": dop.status}
        if dop.lease_expires_at and dop.lease_expires_at > now:
            raise ConcurrencyConflictError(f"DOP #{dop_id} lease is still active until {dop.lease_expires_at}")
        if dop.lease_expires_at is None and (now - dop.updated_at).total_seconds() < stale_seconds:
            raise ConcurrencyConflictError(f"DOP #{dop_id} has no lease and was updated less than {stale_seconds}s ago")
        res = HostReservation.objects.select_for_update().filter(
            durable_operation=dop,
            status__in=[HostReservation.Status.PROVISIONAL, HostReservation.Status.MANUAL_REVIEW]
        ).first()
        dop.version += 1
        dop.lease_worker = worker_id
        dop.lease_expires_at = now + datetime.timedelta(seconds=RECOVERY_LEASE_SECONDS)
        dop.save(update_fields=['version', 'lease_worker', 'lease_expires_at', 'updated_at'])
        claim_version = dop.version
        res_id = res.id if res else None
        host_user_id = (ZoomHostAccount.objects.filter(id=res.host_account_id)
                        .values_list('zoom_user_id', flat=True).first() if res else None)
        payload = dict(dop.payload or {})
        dop_created_at = dop.created_at

    # Phase 2: remote inspection and teardown with no locks held.
    hold_reason = None
    orphan_meeting_id = None
    outcome = None
    if not host_user_id:
        hold_reason = "no held host reservation to inspect"
    else:
        try:
            meetings = _list_host_meetings(host_user_id, zoom_client=zoom_client)
        except Exception as exc:
            hold_reason = f"host meeting listing failed ({type(exc).__name__}: {exc})"
        else:
            candidates = _orphan_deletion_candidates(meetings, payload, dop_created_at)
            if len(candidates) > 1:
                hold_reason = f"{len(candidates)} correlated meetings {candidates}; refusing to choose one"
            elif not candidates:
                hold_reason = ("no correlated meeting visible; absence from one listing is not proof, "
                               "held for quarantine verification")
            else:
                orphan_meeting_id = candidates[0]
                outcome = zoom_delete_outcome(orphan_meeting_id, zoom_client=zoom_client)
                if outcome.confirmed_gone:
                    try:
                        _record_evidence_from_outcome(outcome, "CRASH_RECOVERY_TEARDOWN", dop_id=dop_id,
                                                      worker_id=f"crash_recovery:{worker_id}")
                    except EvidenceRejectedError as exc:
                        # Typically: our claim was taken over. Phase 3's fencing check decides; never release here.
                        hold_reason = f"teardown evidence rejected ({exc})"
                else:
                    hold_reason = (f"delete of orphan meeting {orphan_meeting_id} not confirmed "
                                   f"({outcome.result}, HTTP {outcome.http_status})")

    # Phase 3: finalize only if this worker still owns the claim.
    with transaction.atomic():
        if lc_id:
            LiveClass.objects.select_for_update().filter(id=lc_id).first()
        dop = LiveClassDurableOperation.objects.select_for_update().get(id=dop_id)
        if dop.version != claim_version or dop.lease_worker != worker_id:
            raise ConcurrencyConflictError(f"DOP #{dop_id} recovery claim was lost; nothing finalized.")
        res = HostReservation.objects.select_for_update().filter(id=res_id).first() if res_id else None
        dop.version += 1
        dop.lease_worker = None
        dop.lease_expires_at = None
        if orphan_meeting_id:
            dop.response_payload = {**(dop.response_payload or {}), "orphan_meeting_id": orphan_meeting_id}

        if hold_reason is None:
            dop.status = LiveClassDurableOperation.Status.COMPENSATED
            dop.error_message = (f"Recovered from worker crash: orphan meeting {orphan_meeting_id} deletion "
                                 f"confirmed (HTTP {outcome.http_status}).")
            dop.save(update_fields=['status', 'version', 'lease_worker', 'lease_expires_at',
                                    'response_payload', 'error_message', 'updated_at'])
            if res and res.status in (HostReservation.Status.PROVISIONAL, HostReservation.Status.MANUAL_REVIEW):
                res.status = HostReservation.Status.CANCELLED
                res.release_reason = (f"Crash recovery: orphan meeting {orphan_meeting_id} deletion confirmed "
                                      f"(HTTP {outcome.http_status}).")
                res.save(update_fields=['status', 'release_reason', 'updated_at'])
            return {"status": "RECOVERED", "orphan_deleted": orphan_meeting_id, "dop_id": dop_id,
                    "http_status": outcome.http_status}

        dop.status = LiveClassDurableOperation.Status.OUTCOME_UNKNOWN
        dop.error_message = f"Crash recovery held: {hold_reason}"
        dop.save(update_fields=['status', 'version', 'lease_worker', 'lease_expires_at',
                                'response_payload', 'error_message', 'updated_at'])
        if orphan_meeting_id:
            ZoomLifecycleOutbox.objects.create(
                aggregate_id=lc_id or dop.id,
                event_type="ZOOM_MEETING_COMMIT_FAILED_NEEDS_TEARDOWN",
                status=ZoomLifecycleOutbox.Status.PENDING,
                payload={"meeting_id": orphan_meeting_id, "dop_id": dop_id, "reason": "crash_recovery_delete_unconfirmed"}
            )
        if res and res.status == HostReservation.Status.PROVISIONAL:
            res.status = HostReservation.Status.MANUAL_REVIEW
            res.release_reason = f"Held by crash recovery: {hold_reason}"
            res.save(update_fields=['status', 'release_reason', 'updated_at'])
        return {"status": "HELD", "reason": hold_reason, "orphan_meeting_id": orphan_meeting_id, "dop_id": dop_id}


def run_provisioning_watchdog(zoom_client=None, limit: int = 50, stale_seconds: int = 120) -> Dict[str, Any]:
    """
    Phase B.5 runtime wiring for B3/B4 (Celery beat task + management command).

    1. Crash recovery: ZOOM_PROVISION operations still IN_PROGRESS whose provisioning
       lease has expired (or, for legacy rows without a lease, that are stale).
    2. Quarantine resolution: operations in a quarantine status whose grace period,
       measured from quarantined_at, has elapsed.

    It never releases a host itself. Each operation goes through
    recover_crashed_provision_dop / verify_and_resolve_quarantine, which release only
    after a confirmed teardown or verified absence; any error is isolated to that
    operation, logged, and leaves its reservation held for the next sweep.
    """
    now = timezone.now()
    report = {"recovered": [], "recovery_held": [], "resolved": [], "quarantine_held": [], "errors": []}

    crashed = list(
        LiveClassDurableOperation.objects.filter(
            operation_type=LiveClassDurableOperation.OperationType.ZOOM_PROVISION,
            status=LiveClassDurableOperation.Status.IN_PROGRESS,
        ).filter(
            models.Q(lease_expires_at__lte=now)
            | models.Q(lease_expires_at__isnull=True, updated_at__lte=now - datetime.timedelta(seconds=stale_seconds))
        ).order_by('id').values_list('id', flat=True)[:limit]
    )
    for dop_id in crashed:
        try:
            result = recover_crashed_provision_dop(dop_id, zoom_client=zoom_client, stale_seconds=stale_seconds)
            bucket = "recovered" if result.get("status") == "RECOVERED" else "recovery_held"
            report[bucket].append({"dop_id": dop_id, **{k: v for k, v in result.items() if k != "dop_id"}})
        except Exception as exc:
            logger.warning(f"[ZoomWatchdog] Crash recovery of DOP #{dop_id} left held: {type(exc).__name__}: {exc}")
            report["errors"].append({"dop_id": dop_id, "stage": "recovery", "error": f"{type(exc).__name__}: {exc}"})

    grace_cutoff = now - datetime.timedelta(seconds=QUARANTINE_GRACE_SECONDS)
    quarantined = list(
        LiveClassDurableOperation.objects.filter(
            status__in=LiveClassDurableOperation.QUARANTINE_STATUSES,
        ).filter(
            models.Q(quarantined_at__lte=grace_cutoff)
            | models.Q(quarantined_at__isnull=True, updated_at__lte=grace_cutoff)
        ).exclude(
            lease_expires_at__gt=now
        ).order_by('id').values_list('id', flat=True)[:limit]
    )
    for dop_id in quarantined:
        try:
            result = ZoomQuarantineService.verify_and_resolve_quarantine(dop_id, zoom_client=zoom_client)
            report["resolved"].append({"dop_id": dop_id, "released_reservations": result.get("released_reservations", [])})
        except (QuarantineVerificationError, QuarantinePeriodActiveError, QuarantineStateError,
                ConcurrencyConflictError) as exc:
            report["quarantine_held"].append({"dop_id": dop_id, "reason": f"{type(exc).__name__}: {exc}"})
        except Exception as exc:
            logger.warning(f"[ZoomWatchdog] Quarantine resolution of DOP #{dop_id} left held: {type(exc).__name__}: {exc}")
            report["errors"].append({"dop_id": dop_id, "stage": "quarantine", "error": f"{type(exc).__name__}: {exc}"})

    # 3. Cancellation compensation (release-closure B7): COMPENSATING meeting records whose
    #    durable lease expired -- e.g. an ambiguous cancel_zoom_meeting DELETE. Finalization
    #    is lease-fenced and cancels only the reservation bound to that meeting record.
    report["cancellations_finalized"] = []
    try:
        finalized = ZoomCancellationSagaWorker.run_watchdog_recovery(stale_seconds=stale_seconds, zoom_client=zoom_client)
        report["cancellations_finalized"] = [finalized] if finalized else []
    except Exception as exc:
        logger.warning(f"[ZoomWatchdog] Cancellation compensation sweep failed (records stay held): {exc}")
        report["errors"].append({"stage": "cancellation", "error": f"{type(exc).__name__}: {exc}"})

    if any(report.values()):
        logger.info(f"[ZoomWatchdog] {({k: len(v) for k, v in report.items()})}")
    return report
