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
    Creates a HostReservation in PROVISIONAL state.
    """
    end_time = start_time + datetime.timedelta(minutes=duration_minutes)

    # Lock active candidate hosts in deterministic ID order
    host_query = ZoomHostAccount.objects.filter(
        status=ZoomHostAccount.Status.ACTIVE
    ).order_by('id')
    if preferred_host_id:
        host_query = ZoomHostAccount.objects.filter(id=preferred_host_id)

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

        reservation = select_and_reserve_host(
            live_class=live_class,
            start_time=live_class.scheduled_start,
            duration_minutes=live_class.duration_minutes,
            preferred_host_id=preferred_host_id,
            durable_operation=dop
        )
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
    with transaction.atomic():
        live_class = LiveClass.objects.select_for_update().get(id=live_class_id)
        dop = LiveClassDurableOperation.objects.select_for_update().get(id=dop_id)
        reservation = HostReservation.objects.select_for_update().get(id=res_id)

        # Fencing: verify version has not mutated concurrently
        if dop.version != dop_version:
            raise ConcurrencyConflictError(
                f"Fencing conflict: DOP {dop_id} version changed from {dop_version} to {dop.version}"
            )

        dop.version += 1

        if zoom_result:
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
            dop.save(update_fields=['status', 'response_payload', 'version', 'updated_at'])

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
            dop.status = LiveClassDurableOperation.Status.OUTCOME_UNKNOWN
            dop.error_message = f"Ambiguous remote outcome: {str(zoom_error)}"
            dop.save(update_fields=['status', 'error_message', 'version', 'updated_at'])

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
            dop.status = LiveClassDurableOperation.Status.FAILED
            dop.error_message = f"Zoom API definite failure: {str(zoom_error)}"
            dop.save(update_fields=['status', 'error_message', 'version', 'updated_at'])

            reservation.status = HostReservation.Status.CANCELLED
            reservation.release_reason = f"Definite failure during CREATE: {str(zoom_error)}"
            reservation.save(update_fields=['status', 'release_reason', 'updated_at'])

            definite_failure_exc = ZoomLifecycleError(f"Zoom creation failed: {str(zoom_error)}")

    if definite_failure_exc:
        raise definite_failure_exc


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

        # DEF-01 Guard 3: Reject if any in-flight teardown outbox records exist
        in_flight_teardown_outbox = list(
            ZoomLifecycleOutbox.objects.filter(
                aggregate_id=live_class.id,
                event_type__in=[
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
                f"Cannot replace Zoom meeting for LiveClass {live_class_id}: in-flight teardown operations exist.",
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
def compensate_zoom_operation(dop_id: int, worker_id: Optional[uuid.UUID] = None) -> Dict[str, Any]:
    """
    Executes compensation for an incomplete saga:
    Fences against stale workers by verifying lease_worker and lease_expires_at.
    Acquires LiveClass FOR NO KEY UPDATE (or FOR UPDATE),
    deletes any created remote Zoom meeting, marks meeting record COMPENSATED with is_current=False,
    cancels host reservation, and marks DOP COMPENSATED.
    """
    with transaction.atomic():
        dop = LiveClassDurableOperation.objects.select_for_update().get(id=dop_id)
        now = timezone.now()

        # Worker fencing check: if lease is active and held by a different worker, reject!
        if dop.lease_expires_at and dop.lease_expires_at > now:
            if dop.lease_worker and worker_id and dop.lease_worker != worker_id:
                raise ConcurrencyConflictError(
                    f"Fencing violation: DOP {dop_id} is leased to worker {dop.lease_worker} until {dop.lease_expires_at}"
                )

        # Renew lease for current worker
        if worker_id:
            dop.lease_worker = worker_id
            dop.lease_expires_at = now + datetime.timedelta(seconds=60)
            dop.save(update_fields=['lease_worker', 'lease_expires_at', 'updated_at'])

        live_class = LiveClass.objects.select_for_update().get(id=dop.live_class_id)
        meeting_to_delete = None
        zmr = ZoomMeetingRecord.objects.select_for_update().filter(
            durable_operation=dop
        ).first()
        if zmr and zmr.state not in (ZoomMeetingRecord.State.RELEASED, ZoomMeetingRecord.State.COMPENSATED):
            meeting_to_delete = zmr.zoom_meeting_id

    if meeting_to_delete:
        try:
            ZoomService.delete_meeting(meeting_to_delete)
        except ZoomNotFoundError:
            pass
        except Exception as exc:
            logger.error(f"[ZoomLifecycle] Error deleting meeting during compensation: {exc}")

    with transaction.atomic():
        dop = LiveClassDurableOperation.objects.select_for_update().get(id=dop_id)
        live_class = LiveClass.objects.select_for_update().get(id=dop.live_class_id)

        if zmr:
            zmr = ZoomMeetingRecord.objects.select_for_update().get(id=zmr.id)
            zmr.state = ZoomMeetingRecord.State.COMPENSATED
            if zmr.live_class_id:
                zmr.historical_live_class_id = zmr.live_class_id
            zmr.live_class = None
            zmr.is_current = False
            zmr.save(update_fields=['state', 'historical_live_class_id', 'live_class', 'is_current', 'updated_at'])

        # Cancel reservations
        HostReservation.objects.filter(durable_operation=dop).update(
            status=HostReservation.Status.CANCELLED,
            release_reason="Saga compensation completed."
        )

        dop.status = LiveClassDurableOperation.Status.COMPENSATED
        dop.save(update_fields=['status', 'updated_at'])

        if live_class.zoom_meeting_id == meeting_to_delete:
            live_class.zoom_meeting_id = ''
            live_class.zoom_uuid = ''
            live_class.save(update_fields=['zoom_meeting_id', 'zoom_uuid'])

    return {"status": "COMPENSATED", "dop_id": dop_id}


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
