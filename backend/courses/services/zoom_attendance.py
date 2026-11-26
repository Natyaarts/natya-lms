import logging
import urllib.parse
import secrets
import datetime
import re
import requests
from django.core.cache import cache
from django.conf import settings
from django.utils import timezone
from django.db import transaction, IntegrityError
from django.db.models import Sum
from django.contrib.auth import get_user_model
from courses.models import (
    LiveClass,
    Attendance,
    AttendanceSession,
    UnmatchedZoomParticipant,
    LiveClassJoinIntent,
    LiveClassRegistrant,
    ZoomIdentityLink,
)
from courses.services.zoom import ZoomService
from courses.services.recording import ZoomRecordingService
from courses.services.attendance_classification import (
    apply_automatic_attendance,
    finalization_ready_at,
    is_class_in_progress,
    meeting_end_estimate,
)
from courses.services.attendance_identity import (
    class_roster,
    excluded_user_reason,
    is_class_member,
    participant_email,
    participant_exclusion_reason,
    per_meeting_participant_id,
    stable_zoom_account_id,
)
from courses.services.zoom_meeting_runs import class_window, run_state

logger = logging.getLogger(__name__)
User = get_user_model()

# Two queue records within this window with the same Zoom participant/account
# ID are the same person's same join (webhook vs. report evidence).
UNMATCHED_SAME_JOIN_WINDOW = datetime.timedelta(minutes=10)


class ZoomTransientError(Exception):
    """Temporary Zoom/API failure (429, 5xx, network); the caller should retry."""


def _zoom_get(url, headers):
    try:
        res = requests.get(url, headers=headers, timeout=15)
        if res.status_code == 401:
            # The cached token expired or was replaced: refresh once and retry.
            rejected = str(headers.get("Authorization", "")).replace("Bearer ", "", 1)
            fresh = ZoomService.get_access_token(force_refresh=True, rejected_token=rejected or None)
            headers = dict(headers, Authorization=f"Bearer {fresh}")
            res = requests.get(url, headers=headers, timeout=15)
    except (requests.Timeout, requests.ConnectionError) as exc:
        raise ZoomTransientError(f"network error: {exc.__class__.__name__}") from exc
    if res.status_code == 429 or res.status_code >= 500:
        raise ZoomTransientError(f"Zoom API HTTP {res.status_code}")
    return res


def event_time(value, fallback=None):
    """Aware datetime of a Zoom timestamp (ISO or epoch ms), or None when missing/unparseable."""
    raw = value or fallback
    if not raw:
        return None
    try:
        if isinstance(raw, str) and raw.isdigit():
            raw = int(raw)
        return parse_zoom_timestamp(raw)
    except Exception:
        return None


def encode_zoom_uuid(uuid_str: str) -> str:
    """
    Encodes Zoom meeting UUID according to official Zoom API guidelines:
    If UUID contains '/' (begins with '/', contains single '/', or contains '//'),
    it must be double URL-encoded to prevent API gateways from parsing the slash as a sub-path.
    Otherwise, standard single URL encoding is applied.
    """
    if not uuid_str:
        return ""
    uuid_str = str(uuid_str).strip()
    if '/' in uuid_str:
        return urllib.parse.quote(urllib.parse.quote(uuid_str, safe=''), safe='')
    return urllib.parse.quote(uuid_str, safe='')


def parse_zoom_timestamp(ts_val):
    """
    Parses Zoom timestamp into timezone-aware UTC datetime.
    Accepts ISO 8601 strings, millisecond/second epoch timestamps, or datetime objects.
    """
    if not ts_val:
        return timezone.now()
    if isinstance(ts_val, datetime.datetime):
        if timezone.is_naive(ts_val):
            return timezone.make_aware(ts_val, datetime.timezone.utc)
        return ts_val.astimezone(datetime.timezone.utc)
    if isinstance(ts_val, (int, float)):
        sec = ts_val / 1000.0 if ts_val > 1e11 else float(ts_val)
        return datetime.datetime.fromtimestamp(sec, tz=datetime.timezone.utc)
    try:
        clean = str(ts_val).strip().replace('Z', '+00:00')
        dt = datetime.datetime.fromisoformat(clean)
        if timezone.is_naive(dt):
            dt = timezone.make_aware(dt, datetime.timezone.utc)
        return dt.astimezone(datetime.timezone.utc)
    except Exception as e:
        logger.warning(f"[ZoomAttendance] Failed to parse timestamp {ts_val}: {e}")
        return timezone.now()


class ZoomAttendanceService:
    """
    Production Zoom attendance tracking service:
    1. Deterministic registration for students with valid email (Path A).
    2. Short-lived JoinIntent generation for phone-only students (Path B).
    3. Multi-session join/leave tracking with deduplication and active duration calculation.
    4. Safe out-of-order webhook processing.
    5. Unmatched participant queue with row locking for manual admin linking.
    6. Post-meeting report reconciliation without overwriting manual admin decisions.
    """

    @staticmethod
    def registrant_email(student):
        """
        The email used to register a student with Zoom: their own address, or
        (no usable email, e.g. phone-only accounts) an internal address that
        identifies the account. Zoom sends registrants no emails (registration
        is created with confirmation emails off).
        """
        email = (student.email or '').strip()
        if email and '@' in email and len(email) <= 254:
            return email
        domain = getattr(settings, 'ZOOM_REGISTRANT_EMAIL_DOMAIN', 'students.natyaarts.com')
        return f"student-{student.pk}@{domain}"

    @staticmethod
    def registrant_names(student):
        """(first_name, last_name) shown in Zoom: the student's real name when the account has one."""
        first = (student.first_name or '').strip()
        last = (student.last_name or '').strip()
        if first or last:
            return (first or last)[:64], (last if first else '')[:64]
        return "Student", f"#{student.pk}"

    @staticmethod
    def _meeting_id_for(live_class):
        meeting_id = live_class.zoom_meeting_id
        if not meeting_id and live_class.meeting_url:
            match = re.search(r'/j/(\d+)', live_class.meeting_url)
            if match:
                meeting_id = match.group(1)
        return str(meeting_id or '').strip()

    @classmethod
    def register_student_for_meeting(cls, live_class, student):
        """
        Path A: gives the student a PERSONAL Zoom join link (Zoom registrant),
        so Zoom reports exactly which student joined (registrant_id).
          * Already registered for this class -> reuse.
          * Registered for another class of the same Zoom meeting (recurring
            series, "register once") -> reuse that registration.
          * Otherwise register with Zoom; if the meeting was created without
            registration, turn it on once (auto-approve, no emails) and retry.
        Returns LiveClassRegistrant or None (caller falls back to the plain link).
        """
        existing = LiveClassRegistrant.objects.filter(live_class=live_class, student=student).first()
        if existing:
            return existing

        meeting_id = cls._meeting_id_for(live_class)
        if not meeting_id:
            logger.warning(f"[ZoomAttendance] LiveClass #{live_class.id} has no zoom_meeting_id for registration.")
            return None

        sibling = (
            LiveClassRegistrant.objects
            .filter(student=student, live_class__zoom_meeting_id=meeting_id)
            .exclude(registrant_id='')
            .order_by('-created_at')
            .first()
        )
        if sibling:
            registrant, _ = LiveClassRegistrant.objects.get_or_create(
                live_class=live_class, student=student,
                defaults={'registrant_id': sibling.registrant_id, 'join_url': sibling.join_url, 'email': sibling.email},
            )
            return registrant

        unsupported_key = f"zoom_registration_unsupported:{meeting_id}"
        if cache.get(unsupported_key):
            return None

        email = cls.registrant_email(student)
        first_name, last_name = cls.registrant_names(student)
        payload = {"email": email, "first_name": first_name}
        if last_name:
            payload["last_name"] = last_name

        try:
            res = cls._post_registrant(meeting_id, payload)
            if res.status_code != 201 and cls._registration_disabled(res):
                if ZoomService.enable_meeting_registration(meeting_id):
                    res = cls._post_registrant(meeting_id, payload)
                else:
                    cache.set(unsupported_key, True, 6 * 3600)
                    return None

            if res.status_code == 201:
                data = res.json()
                registrant, _ = LiveClassRegistrant.objects.update_or_create(
                    live_class=live_class,
                    student=student,
                    defaults={
                        'registrant_id': str(data.get('registrant_id') or data.get('id') or ''),
                        'join_url': data.get('join_url', ''),
                        'email': email,
                    }
                )
                logger.info(f"[ZoomAttendance] Registered student #{student.id} for meeting {meeting_id}: reg_id={registrant.registrant_id}")
                return registrant
            logger.info(
                f"[ZoomAttendance] Zoom registrant registration returned {res.status_code} for meeting {meeting_id}. "
                f"Class will use standard join link."
            )
            return None
        except Exception as e:
            logger.warning(f"[ZoomAttendance] Error during Zoom meeting registrant call: {e}")
            return None

    @staticmethod
    def _post_registrant(meeting_id, payload):
        url = f"https://api.zoom.us/v2/meetings/{meeting_id}/registrants"
        token = ZoomService.get_access_token()
        res = requests.post(url, headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                            json=payload, timeout=10)
        if res.status_code == 401:
            token = ZoomService.get_access_token(force_refresh=True, rejected_token=token)
            res = requests.post(url, headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                                json=payload, timeout=10)
        return res

    @staticmethod
    def _registration_disabled(res):
        """True when Zoom refused because the meeting has no registration enabled."""
        if res.status_code not in (400, 404):
            return False
        try:
            data = res.json()
        except Exception:
            data = {}
        message = str(data.get('message') or getattr(res, 'text', '') or '').lower()
        return 'registration' in message and ('not' in message or 'disabled' in message)

    @classmethod
    def create_join_intent(cls, live_class, student, ip_address=None, user_agent=""):
        """
        Path B: Generates a secure, short-lived JoinIntent token for student.
        """
        token = secrets.token_urlsafe(32)
        expires_at = timezone.now() + datetime.timedelta(minutes=15)
        intent = LiveClassJoinIntent.objects.create(
            student=student,
            live_class=live_class,
            token=token,
            ip_address=ip_address,
            user_agent=user_agent or "",
            expires_at=expires_at
        )
        return intent

    @classmethod
    def resolve_student_identity(cls, live_class, participant, meeting_obj):
        """
        Resolves a Zoom participant to an enrolled student using reliable
        evidence only, in this order:
          1. registrant_id from a Zoom registrant link;
          2. customer_key matching this class's join token (reusable, so the
             live webhook and the post-meeting report both resolve it);
          3. email that belongs to exactly one enrolled student of this class
             (several matching accounts -> ambiguous -> review queue);
          4. a stable Zoom account ID an admin explicitly linked before.
        Display names and device names are never used. Returns the student or
        None (the participant then goes to the admin review queue).
        """
        registrant_id = str(participant.get('registrant_id') or '').strip()
        if registrant_id:
            reg = LiveClassRegistrant.objects.filter(
                live_class=live_class,
                registrant_id=registrant_id
            ).select_related('student').first()
            if reg:
                return reg.student
            # Recurring meetings: one registration covers every occurrence, so the
            # student may join this class with the personal link from an earlier one.
            meeting_id = cls._meeting_id_for(live_class)
            if meeting_id:
                students = {
                    r.student for r in LiveClassRegistrant.objects.filter(
                        registrant_id=registrant_id, live_class__zoom_meeting_id=meeting_id,
                    ).select_related('student')
                }
                if len(students) == 1:
                    student = students.pop()
                    if is_class_member(live_class, student):
                        return student

        customer_key = str(participant.get('customer_key') or '').strip()
        if customer_key:
            intent = LiveClassJoinIntent.objects.filter(
                live_class=live_class,
                token=customer_key,
            ).select_related('student').first()
            if intent:
                if not intent.is_used:
                    intent.is_used = True
                    intent.used_at = timezone.now()
                    intent.save(update_fields=['is_used', 'used_at'])
                return intent.student

        email = participant_email(participant)
        if email and '@' in email:
            members = [u for u in User.objects.filter(email__iexact=email) if is_class_member(live_class, u)]
            if len(members) == 1:
                return members[0]
            if len(members) > 1:
                logger.warning(
                    f"[ZoomAttendance] LiveClass #{live_class.id}: {len(members)} enrolled accounts share one "
                    f"participant email; not choosing -- participant goes to review."
                )

        account_id = stable_zoom_account_id(participant)
        if account_id:
            link = ZoomIdentityLink.objects.filter(
                zoom_account_id=account_id, is_ambiguous=False
            ).select_related('student').first()
            if link and is_class_member(live_class, link.student):
                return link.student

        return None

    @classmethod
    def record_unmatched(cls, live_class, participant, *, source, join_time=None, leave_time=None,
                         duration_seconds=None, is_active=None):
        """
        Adds or updates the review-queue record for an unidentified participant.
        The same person's join seen by the live webhook and later by the report
        is merged into one record (matched on Zoom participant/account ID and
        join time), never duplicated.

        Returns (record, linked_student): linked_student is set when an admin
        already linked this participant in this class, so new evidence for the
        same join is attributed to that student. IGNORED records stay ignored.
        """
        per_meeting_id = per_meeting_participant_id(participant)
        account_id = stable_zoom_account_id(participant)
        participant_uuid = str(participant.get('participant_uuid') or participant.get('id') or per_meeting_id or '')
        name = str(participant.get('user_name') or participant.get('name') or '')
        email = participant_email(participant)
        evidence = dict(participant)
        evidence['_source'] = source

        # Zoom's own participant_uuid (not the fallbacks above) identifies one participant.
        zoom_participant_uuid = str(participant.get('participant_uuid') or '').strip()

        with transaction.atomic():
            record = cls._find_unmatched(
                live_class, participant_uuid, per_meeting_id, account_id, join_time, leave_time,
                zoom_participant_uuid=zoom_participant_uuid,
            )
            if record is None:
                record = UnmatchedZoomParticipant.objects.create(
                    live_class=live_class,
                    zoom_participant_uuid=participant_uuid,
                    zoom_user_id=per_meeting_id,
                    participant_name=name,
                    participant_email=email,
                    join_time=join_time,
                    leave_time=leave_time,
                    duration_seconds=max(0, int(duration_seconds or 0)),
                    is_active=bool(is_active) if is_active is not None else False,
                    status=UnmatchedZoomParticipant.Status.PENDING,
                    raw_evidence=evidence,
                )
                logger.info(f"[ZoomAttendance] LiveClass #{live_class.id}: unidentified participant queued for admin review (#{record.id}).")
                return record, None

            if record.status == UnmatchedZoomParticipant.Status.PENDING:
                if join_time and (not record.join_time or join_time < record.join_time):
                    record.join_time = join_time
                if leave_time and (not record.leave_time or leave_time > record.leave_time):
                    record.leave_time = leave_time
                if duration_seconds:
                    record.duration_seconds = max(record.duration_seconds, int(duration_seconds))
                elif record.join_time and record.leave_time:
                    record.duration_seconds = max(record.duration_seconds, int((record.leave_time - record.join_time).total_seconds()))
                if is_active is not None:
                    record.is_active = bool(is_active)
                record.participant_name = record.participant_name or name
                record.participant_email = record.participant_email or email
                if not stable_zoom_account_id(record.raw_evidence or {}) and account_id:
                    merged = dict(record.raw_evidence or {})
                    merged.setdefault('id', account_id)
                    record.raw_evidence = merged
                record.save()
                return record, None

            if record.status == UnmatchedZoomParticipant.Status.RESOLVED and record.linked_student_id:
                return record, record.linked_student
            return record, None  # IGNORED: stays out of attendance and out of the queue

    @classmethod
    def _find_unmatched(cls, live_class, participant_uuid, per_meeting_id, account_id, join_time, leave_time,
                        zoom_participant_uuid=""):
        reference = join_time or leave_time

        def same_join(rec):
            rec_time = rec.join_time or rec.leave_time
            if reference is None or rec_time is None or abs(rec_time - reference) <= UNMATCHED_SAME_JOIN_WINDOW:
                return True
            # A leave event without a duration closes this participant's open join record.
            return (
                join_time is None and leave_time is not None
                and rec.join_time is not None and rec.leave_time is None and rec.join_time <= leave_time
            )

        records = list(UnmatchedZoomParticipant.objects.select_for_update().filter(live_class=live_class).order_by('id'))
        if zoom_participant_uuid:
            for rec in records:
                if rec.zoom_participant_uuid == zoom_participant_uuid:
                    return rec
        for rec in records:
            if participant_uuid and rec.zoom_participant_uuid == participant_uuid and same_join(rec):
                return rec
            if per_meeting_id and rec.zoom_user_id == per_meeting_id and same_join(rec):
                return rec
            if account_id and stable_zoom_account_id(rec.raw_evidence or {}) == account_id and same_join(rec):
                return rec
        return None

    @classmethod
    def handle_participant_joined(cls, payload):
        """
        Handles 'meeting.participant_joined' webhook.
        Creates or activates an AttendanceSession. Updates duration evidence without auto-classifying status.
        """
        meeting_obj = payload.get('object', {})
        meeting_id = str(meeting_obj.get('id') or '')
        meeting_uuid = meeting_obj.get('uuid', '')
        participant = meeting_obj.get('participant', {}) or payload.get('participant', {})
        if not isinstance(participant, dict):
            participant = {}

        join_time = parse_zoom_timestamp(participant.get('join_time') or payload.get('event_ts'))
        live_class = ZoomRecordingService.find_live_class_for_zoom_meeting(meeting_id, meeting_uuid, at=join_time)
        if not live_class:
            logger.warning(f"[ZoomAttendance] No LiveClass found for meeting_id={meeting_id}, uuid={meeting_uuid}")
            return False

        participant_uuid = str(participant.get('participant_uuid') or participant.get('id') or '')
        zoom_user_id = str(participant.get('user_id') or '')
        registrant_id = str(participant.get('registrant_id') or '')

        # Instructors, staff, host accounts and test accounts: never counted, never queued.
        reason = participant_exclusion_reason(live_class, participant)
        if reason:
            logger.info(f"[ZoomAttendance] LiveClass #{live_class.id}: participant excluded ({reason}).")
            return True

        student = cls.resolve_student_identity(live_class, participant, meeting_obj)
        if student and excluded_user_reason(student, live_class):
            return True
        if not student:
            _, student = cls.record_unmatched(live_class, participant, source="webhook", join_time=join_time, is_active=True)
            if not student:
                return True

        if student:
            with transaction.atomic():
                att, created = Attendance.objects.get_or_create(
                    live_class=live_class,
                    student=student,
                    defaults={
                        'status': Attendance.Status.ABSENT,
                        'first_joined_at': join_time,
                        'joined_at': join_time,
                        'is_currently_in_class': True,
                        'notes': 'Recorded via Zoom webhook'
                    }
                )
                if not created:
                    if not att.first_joined_at:
                        att.first_joined_at = join_time
                    if not att.joined_at:
                        att.joined_at = join_time
                    att.is_currently_in_class = True
                    att.last_webhook_at = timezone.now()

                # Session deduplication key
                session_key = participant_uuid or zoom_user_id or str(int(join_time.timestamp()))
                session_id = f"{live_class.id}:{student.id}:{session_key}:{int(join_time.timestamp())}"

                try:
                    with transaction.atomic():
                        session, s_created = AttendanceSession.objects.get_or_create(
                            session_id=session_id,
                            defaults={
                                'attendance': att,
                                'zoom_participant_uuid': participant_uuid,
                                'zoom_user_id': zoom_user_id,
                                'zoom_registrant_id': registrant_id,
                                'join_time': join_time,
                                'is_active': True,
                                'source': AttendanceSession.Source.WEBHOOK,
                                'raw_evidence': participant
                            }
                        )
                except IntegrityError:
                    session = AttendanceSession.objects.filter(session_id=session_id).first()
                    s_created = False

                if session and not s_created and not session.is_active:
                    session.is_active = True
                    session.save(update_fields=['is_active', 'updated_at'])

                # Update duration evidence and promote to PRESENT once the threshold
                # is met. Never demotes mid-class; respects manual overrides.
                apply_automatic_attendance(att, final=False)
                att.save()

            logger.info(f"[ZoomAttendance] Student #{student.id} joined LiveClass #{live_class.id} (session={session_id})")
        return True

    @classmethod
    def handle_participant_left(cls, payload):
        """
        Handles 'meeting.participant_left' webhook.
        Closes active session, handles out-of-order deliveries, updates attendance duration without changing status.
        """
        meeting_obj = payload.get('object', {})
        meeting_id = str(meeting_obj.get('id') or '')
        meeting_uuid = meeting_obj.get('uuid', '')
        participant = meeting_obj.get('participant', {}) or payload.get('participant', {})
        if not isinstance(participant, dict):
            participant = {}

        leave_time = parse_zoom_timestamp(participant.get('leave_time') or payload.get('event_ts'))
        live_class = ZoomRecordingService.find_live_class_for_zoom_meeting(meeting_id, meeting_uuid, at=leave_time)
        if not live_class:
            logger.warning(f"[ZoomAttendance] No LiveClass found for meeting_id={meeting_id}, uuid={meeting_uuid}")
            return False

        participant_uuid = str(participant.get('participant_uuid') or participant.get('id') or '')
        zoom_user_id = str(participant.get('user_id') or '')
        registrant_id = str(participant.get('registrant_id') or '')
        duration_sec = int(participant.get('duration', 0) or 0)

        reason = participant_exclusion_reason(live_class, participant)
        if reason:
            logger.info(f"[ZoomAttendance] LiveClass #{live_class.id}: participant excluded ({reason}).")
            return True

        student = cls.resolve_student_identity(live_class, participant, meeting_obj)
        if student and excluded_user_reason(student, live_class):
            return True
        if not student:
            join_hint = leave_time - datetime.timedelta(seconds=duration_sec) if duration_sec > 0 else None
            _, student = cls.record_unmatched(
                live_class, participant, source="webhook", join_time=join_hint, leave_time=leave_time,
                duration_seconds=duration_sec, is_active=False,
            )
            if not student:
                return True

        if student:
            with transaction.atomic():
                att, _ = Attendance.objects.get_or_create(
                    live_class=live_class,
                    student=student,
                    defaults={'status': Attendance.Status.ABSENT}
                )
                att.last_left_at = leave_time
                att.left_at = leave_time
                att.last_webhook_at = timezone.now()

                # Find active session
                session = None
                if participant_uuid:
                    session = AttendanceSession.objects.filter(
                        attendance=att,
                        zoom_participant_uuid=participant_uuid,
                        is_active=True
                    ).order_by('-join_time').first()

                if not session:
                    session = AttendanceSession.objects.filter(
                        attendance=att,
                        is_active=True
                    ).order_by('-join_time').first()

                if session:
                    session.leave_time = leave_time
                    session.is_active = False
                    computed_dur = max(0, int((leave_time - session.join_time).total_seconds()))
                    session.duration_seconds = max(duration_sec, computed_dur)
                    session.save(update_fields=['leave_time', 'is_active', 'duration_seconds', 'updated_at'])
                else:
                    # Out-of-order: leave received before join event
                    join_ts = leave_time - datetime.timedelta(seconds=duration_sec) if duration_sec > 0 else leave_time
                    session_key = participant_uuid or zoom_user_id or str(int(leave_time.timestamp()))
                    session_id = f"{live_class.id}:{student.id}:{session_key}:{int(join_ts.timestamp())}"

                    try:
                        with transaction.atomic():
                            session, _ = AttendanceSession.objects.get_or_create(
                                session_id=session_id,
                                defaults={
                                    'attendance': att,
                                    'zoom_participant_uuid': participant_uuid,
                                    'zoom_user_id': zoom_user_id,
                                    'zoom_registrant_id': registrant_id,
                                    'join_time': join_ts,
                                    'leave_time': leave_time,
                                    'duration_seconds': duration_sec,
                                    'is_active': False,
                                    'source': AttendanceSession.Source.WEBHOOK,
                                    'raw_evidence': participant
                                }
                            )
                    except IntegrityError:
                        session = AttendanceSession.objects.filter(session_id=session_id).first()

                # Check if student still has any active sessions
                att.is_currently_in_class = att.sessions.filter(is_active=True).exists()
                # A disconnect never demotes (final=False): only promotes to PRESENT
                # once the threshold is met. Respects manual overrides.
                apply_automatic_attendance(att, final=False)
                att.save()

            logger.info(f"[ZoomAttendance] Student #{student.id} left LiveClass #{live_class.id}. Total duration={att.duration_minutes}m")
        return True

    @classmethod
    def _zoom_headers(cls):
        return {
            "Authorization": f"Bearer {ZoomService.get_access_token()}",
            "Content-Type": "application/json",
        }

    @classmethod
    def fetch_meeting_report(cls, live_class, meeting_identifier=None):
        """
        Read-only: fetches the Zoom Past Meeting Participants Report for one
        meeting run (GET /v2/report/meetings/{uuid-or-id}/participants),
        following pagination. Returns {"status": "success", "participants": [...]}
        or {"status": "no_meeting_id" | "not_ready" | "error", ...}.
        Raises ZoomTransientError on 429/5xx/network errors. Writes nothing.
        """
        meeting_identifier = meeting_identifier or live_class.zoom_uuid or cls._meeting_id_for(live_class)
        if not meeting_identifier:
            logger.warning(f"[ZoomAttendance] LiveClass #{live_class.id} has no zoom_uuid or zoom_meeting_id for reconciliation.")
            return {"status": "no_meeting_id"}

        encoded_id = encode_zoom_uuid(meeting_identifier)
        headers = cls._zoom_headers()
        all_participants = []
        next_page_token = ""
        while True:
            url = f"https://api.zoom.us/v2/report/meetings/{encoded_id}/participants?page_size=300"
            if next_page_token:
                url += f"&next_page_token={urllib.parse.quote(next_page_token)}"
            res = _zoom_get(url, headers)
            if res.status_code == 404:
                logger.info(f"[ZoomAttendance] LiveClass #{live_class.id}: report not ready yet (404).")
                return {"status": "not_ready", "error": "Report not ready yet"}
            if res.status_code != 200:
                logger.warning(f"[ZoomAttendance] LiveClass #{live_class.id}: Zoom report API returned {res.status_code}.")
                return {"status": "error", "error": f"Zoom report API HTTP {res.status_code}"}
            data = res.json()
            participants = data.get('participants', [])
            all_participants.extend(participants)
            next_page_token = data.get('next_page_token')
            if not next_page_token or len(participants) == 0:
                break
        return {"status": "success", "participants": all_participants}

    @classmethod
    def fetch_meeting_instances(cls, live_class):
        """
        UUIDs of every run (instance) of the class's meeting that started inside
        the class window: GET /v2/past_meetings/{meetingId}/instances, merged
        with runs seen via meeting.started/ended webhooks. Returns
        (uuids, source). Raises ZoomTransientError on 429/5xx/network errors.
        """
        uuids = set(run_state(live_class, refresh=True)["uuids"])
        source = "webhook" if uuids else ""
        meeting_id = cls._meeting_id_for(live_class)
        if meeting_id:
            res = _zoom_get(
                f"https://api.zoom.us/v2/past_meetings/{encode_zoom_uuid(meeting_id)}/instances",
                cls._zoom_headers(),
            )
            if res.status_code == 200:
                window_start, window_end = class_window(live_class)
                for inst in (res.json() or {}).get('meetings', []):
                    start = parse_zoom_timestamp(inst.get('start_time')) if inst.get('start_time') else None
                    if inst.get('uuid') and start and window_start <= start <= window_end:
                        uuids.add(inst['uuid'])
                        source = "instances_api" if source in ("", "instances_api") else "instances_api+webhook"
            elif res.status_code != 404:
                logger.warning(f"[ZoomAttendance] LiveClass #{live_class.id}: past-instances API returned {res.status_code}; using fallbacks.")
        return sorted(uuids), source

    @classmethod
    def fetch_meeting_reports(cls, live_class):
        """
        Reports for every meeting run of the class. Status:
          success    every run's report was fetched (complete);
          partial    some runs fetched, some not ready / failed (incomplete);
          not_ready  no run's report is available yet;
          error      a permanent API error and nothing fetched;
          no_meeting_id.
        Raises ZoomTransientError on 429/5xx/network errors.
        """
        if not (live_class.zoom_uuid or cls._meeting_id_for(live_class)):
            return {"status": "no_meeting_id"}
        uuids, source = cls.fetch_meeting_instances(live_class)
        if not uuids:
            # No run discovered (instances API unavailable, no webhooks): fall
            # back to the stored identifier; rows outside the class window are
            # dropped when applied.
            uuids, source = [live_class.zoom_uuid or cls._meeting_id_for(live_class)], "stored_identifier"

        participants, statuses = [], []
        for uuid in uuids:
            result = cls.fetch_meeting_report(live_class, meeting_identifier=uuid)
            statuses.append(result["status"])
            if result["status"] == "success":
                participants.extend(result["participants"])

        if all(st == "success" for st in statuses):
            status = "success"
        elif "success" in statuses:
            status = "partial"
        elif "error" in statuses:
            status = "error"
        else:
            status = "not_ready"
        return {"status": status, "participants": participants, "runs": len(uuids), "run_source": source}

    @classmethod
    def reconcile_meeting_participants(cls, live_class):
        """
        Fetches the reports of every meeting run and applies them. Attendance is
        finalized (ABSENT below threshold) only when every run's report was
        fetched and no run is still active; otherwise evidence is recorded
        promote-only and the caller retries. Raises ZoomTransientError on
        temporary Zoom failures.
        """
        report = cls.fetch_meeting_reports(live_class)
        if report["status"] not in ("success", "partial"):
            return report
        result = cls.apply_meeting_report(
            live_class, report["participants"], complete=report["status"] == "success",
            run_source=report.get("run_source", ""),
        )
        result["status"] = report["status"]
        result["runs"] = report.get("runs")
        return result

    @classmethod
    def apply_meeting_report(cls, live_class, all_participants, now=None, complete=True, run_source=""):
        """
        Serialized per class: a row lock on the LiveClass makes concurrent
        reconciliations of the same class (duplicate task, catch-up plus
        meeting.ended, manual Sync) run one after the other; the second one
        then finds everything already recorded and changes nothing.
        """
        with transaction.atomic():
            LiveClass.objects.select_for_update().filter(pk=live_class.pk).first()
            was_finalized = bool(
                LiveClass.objects.filter(pk=live_class.pk).values_list('attendance_finalized_at', flat=True).first()
            )
            result = cls._apply_meeting_report(live_class, all_participants, now=now, complete=complete,
                                               run_source=run_source)
        cls._audit_finalization(live_class, result, was_finalized)
        return result

    @staticmethod
    def _audit_finalization(live_class, result, was_finalized):
        """Audit trail entry (counts only, no student details) for each final pass."""
        classification = result.get("classification") or {}
        if not classification.get("final"):
            return
        try:
            from users.models import AdminAuditLog
            AdminAuditLog.record(
                actor=None,
                action="ZOOM_ATTENDANCE_FINALIZED" if not was_finalized else "ZOOM_ATTENDANCE_REFINALIZED",
                target_type="LiveClass",
                target_id=str(live_class.pk),
                description="Attendance finalized automatically from the Zoom participant report.",
                metadata={
                    "report_rows": result.get("total_records", 0),
                    "matched_rows": result.get("reconciled", 0),
                    "unmatched_rows": result.get("unmatched", 0),
                    "excluded_rows": result.get("excluded", 0),
                    "skipped_rows": result.get("skipped", 0),
                    "present": classification.get("present", 0),
                    "late": classification.get("late", 0),
                    "no_show_absent": classification.get("no_show_absent", 0),
                    "absent": classification.get("absent", 0),
                    "other": classification.get("other", 0),
                    "changed": classification.get("changed", 0),
                    "manual": classification.get("manual", 0),
                },
            )
        except Exception:
            logger.exception(f"[ZoomAttendance] Could not write the finalization audit entry for LiveClass #{live_class.pk}.")

    @classmethod
    def _apply_meeting_report(cls, live_class, all_participants, now=None, complete=True, run_source=""):
        """
        Records Zoom report rows as REPORT sessions (idempotent: keyed per
        participant + join time and updated in place), then runs the
        classification pass over every attendance row of the class. The pass is
        final (may set ABSENT) only if the report set is complete.
        Re-running with the same report changes nothing.
        """
        now = now or timezone.now()
        reconciled_count = unmatched_count = skipped_count = excluded_count = 0
        window_start, window_end = class_window(live_class)

        for p in all_participants:
            raw_join = p.get('join_time')
            raw_leave = p.get('leave_time')
            dur_sec = int(p.get('duration', 0) or 0)
            p_uuid = str(p.get('id') or p.get('user_id') or '')

            # Never substitute "now" for a missing timestamp: it would inflate the
            # duration and create a new session key on every run.
            if not raw_join:
                skipped_count += 1
                logger.warning(f"[ZoomAttendance] LiveClass #{live_class.id}: skipped report row without join_time.")
                continue
            join_time = parse_zoom_timestamp(raw_join)
            if raw_leave:
                leave_time = parse_zoom_timestamp(raw_leave)
            elif dur_sec > 0:
                leave_time = join_time + datetime.timedelta(seconds=dur_sec)
            else:
                skipped_count += 1
                logger.warning(f"[ZoomAttendance] LiveClass #{live_class.id}: skipped report row without leave_time or duration.")
                continue
            if not (window_start <= join_time <= window_end):
                skipped_count += 1
                logger.warning(f"[ZoomAttendance] LiveClass #{live_class.id}: skipped report row outside the class window.")
                continue

            if participant_exclusion_reason(live_class, p):
                excluded_count += 1
                continue
            student = cls.resolve_student_identity(live_class, p, {})
            if student and excluded_user_reason(student, live_class):
                excluded_count += 1
                continue
            if not student:
                _, student = cls.record_unmatched(
                    live_class, p, source="report", join_time=join_time, leave_time=leave_time,
                    duration_seconds=dur_sec, is_active=False,
                )
                if not student:
                    unmatched_count += 1
                    continue

            with transaction.atomic():
                att, _ = Attendance.objects.get_or_create(
                    live_class=live_class,
                    student=student,
                    defaults={'status': Attendance.Status.ABSENT}
                )
                session_id = f"report:{live_class.id}:{student.id}:{p_uuid}:{int(join_time.timestamp())}"
                AttendanceSession.objects.update_or_create(
                    session_id=session_id,
                    defaults={
                        'attendance': att,
                        'zoom_participant_uuid': p_uuid,
                        'zoom_user_id': str(p.get('user_id') or ''),
                        'zoom_registrant_id': str(p.get('registrant_id') or ''),
                        'join_time': join_time,
                        'leave_time': leave_time,
                        'duration_seconds': dur_sec,
                        'is_active': False,
                        'source': AttendanceSession.Source.REPORT,
                        'raw_evidence': p
                    }
                )
                # Recalculated from the stored REPORT sessions (not incremented),
                # so reprocessing the same report never inflates it.
                att.zoom_reported_duration_seconds = (
                    att.sessions.filter(source=AttendanceSession.Source.REPORT)
                    .aggregate(total=Sum('duration_seconds'))['total'] or 0
                )
                if not att.first_joined_at or join_time < att.first_joined_at:
                    att.first_joined_at = join_time
                if not att.last_left_at or leave_time > att.last_left_at:
                    att.last_left_at = leave_time
                # Duration from the union of all sessions (protects manual duration
                # override); promote-only here, the final pass below may demote.
                apply_automatic_attendance(att, final=False, now=now)
                att.reconciliation_status = 'RECONCILED'
                att.reconciled_at = now
                att.save()
            reconciled_count += 1

        classification = cls.finalize_attendance(
            live_class, now=now, complete=complete,
            evidence_rows=reconciled_count + unmatched_count + excluded_count,
            report_rows=len(all_participants), run_source=run_source,
        )

        logger.info(
            f"[ZoomAttendance] Reconciled LiveClass #{live_class.id}: {reconciled_count} matched, "
            f"{unmatched_count} unmatched, {excluded_count} excluded, {skipped_count} skipped, "
            f"{len(all_participants)} rows, complete={complete}. Classification: {classification}"
        )
        return {
            "status": "success",
            "reconciled": reconciled_count,
            "unmatched": unmatched_count,
            "excluded": excluded_count,
            "skipped": skipped_count,
            "total_records": len(all_participants),
            "complete": complete,
            "classification": classification,
        }

    @classmethod
    def finalization_blocker(cls, live_class, now, complete, evidence_rows, report_rows=None, run_source=""):
        """
        Why attendance cannot be finalized yet, or None. Nobody is marked ABSENT while any applies.

        Failed and not-ready reports never get here (reconciliation stops
        before applying them); a partial report set arrives with complete=False.
        A complete report with ZERO participants is valid evidence of a class
        nobody attended -- but only once Zoom has confirmed this meeting run
        happened and ended, and no live join event contradicts it.
        """
        if not complete:
            return "report_incomplete"          # some run's report failed / is not ready
        if evidence_rows is not None and evidence_rows == 0:
            if report_rows:
                # Rows exist but none fall in this class's time window: the
                # report is for another occurrence of the meeting ID.
                return "report_outside_class_window"
            if not cls._meeting_end_confirmed(live_class, run_source):
                return "empty_report_meeting_end_unconfirmed"
            if cls._live_join_evidence_exists(live_class):
                return "empty_report_contradicts_live_joins"
        if is_class_in_progress(live_class, now):
            return "meeting_in_progress"
        ready = finalization_ready_at(live_class)
        if ready is None or now < ready:
            return "waiting_finalization_period"
        return None

    @staticmethod
    def _meeting_end_confirmed(live_class, run_source):
        """
        Zoom itself confirmed a run of this class's meeting ended: a meeting.ended
        webhook inside the class window, or the run was listed by Zoom's past
        meeting instances API (which only lists meetings that have ended).
        """
        return bool(run_state(live_class)["ended"]) or "instances_api" in (run_source or "")

    @staticmethod
    def _live_join_evidence_exists(live_class):
        """Webhook joins recorded for this class (identified or not)."""
        return (
            AttendanceSession.objects.filter(attendance__live_class=live_class,
                                             source=AttendanceSession.Source.WEBHOOK).exists()
            or UnmatchedZoomParticipant.objects.filter(live_class=live_class).exists()
        )

    @classmethod
    def finalize_attendance(cls, live_class, now=None, complete=True, evidence_rows=None, report_rows=None,
                            run_source=""):
        """
        Classification pass over every attendance row of a class: PRESENT (or
        LATE) at or above the threshold; ABSENT below it only on a final pass.
        A pass is final only when every run's report was fetched and processed,
        the report has rows for this class, no run is still active and the
        configured waiting period after the meeting ended has passed.

        A final pass also marks ABSENT every roster student with no attendance
        row at all (never joined) -- only for classes inside the automation
        window (ZOOM_ATTENDANCE_AUTOMATION_START), so historical classes are
        never touched. Manual and EXCUSED rows are preserved. A final pass
        stamps LiveClass.attendance_finalized_at.
        """
        from courses.services.attendance_automation import automatic_reconciliation_allowed

        now = now or timezone.now()
        run_state(live_class, now, refresh=True)
        meeting_end_estimate(live_class, refresh=True)
        blocker = cls.finalization_blocker(live_class, now, complete, evidence_rows, report_rows, run_source)
        final = blocker is None
        counts = {"final": final, "not_final_reason": blocker, "present": 0, "late": 0, "absent": 0,
                  "other": 0, "changed": 0, "manual": 0, "no_show_absent": 0}

        if final and automatic_reconciliation_allowed(live_class):
            counts["no_show_absent"] = cls._mark_no_shows_absent(live_class, now)

        att_ids = list(Attendance.objects.filter(live_class=live_class).values_list('id', flat=True))
        for att_id in att_ids:
            with transaction.atomic():
                att = Attendance.objects.select_for_update().filter(pk=att_id).first()
                if not att:
                    continue
                att.live_class = live_class  # share the cached meeting-end estimate
                decision = apply_automatic_attendance(att, final=final, now=now)
                fields = list(decision['update_fields'])
                if final:
                    att.reconciliation_status = 'RECONCILED'
                    att.reconciled_at = now
                    fields += ['reconciliation_status', 'reconciled_at']
                if fields:
                    att.save(update_fields=fields)

            if decision['manual_status']:
                counts["manual"] += 1
            if decision['changed']:
                counts["changed"] += 1
            if att.status == Attendance.Status.PRESENT:
                counts["present"] += 1
            elif att.status == Attendance.Status.LATE:
                counts["late"] += 1
            elif att.status == Attendance.Status.ABSENT:
                counts["absent"] += 1
            else:
                counts["other"] += 1

        if final:
            live_class.attendance_finalized_at = now
            live_class.save(update_fields=['attendance_finalized_at'])
        return counts

    @classmethod
    def _mark_no_shows_absent(cls, live_class, now):
        """Creates ABSENT rows for roster students with no attendance row; existing rows are left alone."""
        existing = set(Attendance.objects.filter(live_class=live_class).values_list('student_id', flat=True))
        created = 0
        for student in class_roster(live_class):
            if student.pk in existing:
                continue
            _, was_created = Attendance.objects.get_or_create(
                live_class=live_class, student=student,
                defaults={
                    'status': Attendance.Status.ABSENT,
                    'duration_minutes': 0,
                    'reconciliation_status': 'RECONCILED',
                    'reconciled_at': now,
                    'notes': 'Automatically marked absent: no verified Zoom attendance.',
                },
            )
            created += int(was_created)
        return created

    @classmethod
    def link_unmatched_participant(cls, unmatched_id, student_id, admin_user):
        """
        Manually links an UnmatchedZoomParticipant to a student (row-locked
        against concurrent resolution). Also:
          * refuses instructors, staff and test accounts;
          * links other pending queue records of the same Zoom participant in
            this class (webhook vs. report evidence of the same join);
          * learns the participant's stable Zoom account ID (signed-in users
            only) for future classes; a conflicting earlier link makes that
            Zoom account ambiguous so it is never auto-matched again.
        Status is only demoted to ABSENT once the class's attendance has been
        finalized from a complete report.
        """
        with transaction.atomic():
            unmatched = UnmatchedZoomParticipant.objects.select_for_update().get(id=unmatched_id)
            if unmatched.status == UnmatchedZoomParticipant.Status.RESOLVED:
                raise ValueError("This participant has already been linked by another administrator.")
            if unmatched.status == UnmatchedZoomParticipant.Status.IGNORED:
                raise ValueError("This participant was ignored by an administrator.")

            student = User.objects.get(id=student_id)
            live_class = unmatched.live_class
            if not is_class_member(live_class, student):
                raise ValueError("Student is not enrolled or assigned to this live class.")
            if excluded_user_reason(student, live_class):
                raise ValueError("Instructors, staff and test accounts cannot be linked as students.")

            att = Attendance.objects.filter(live_class=live_class, student=student).select_for_update().first()
            if not att:
                try:
                    with transaction.atomic():
                        att = Attendance.objects.create(live_class=live_class, student=student, status=Attendance.Status.ABSENT)
                except IntegrityError:
                    att = Attendance.objects.filter(live_class=live_class, student=student).select_for_update().first()

            evidence = unmatched.raw_evidence or {}
            account_id = stable_zoom_account_id(evidence)
            records = [unmatched] + cls._same_participant_pending(live_class, unmatched, account_id)
            now = timezone.now()
            for rec in records:
                join_time = rec.join_time or rec.leave_time or now
                AttendanceSession.objects.update_or_create(
                    session_id=f"manual:{rec.id}:{student.id}:{int(join_time.timestamp())}",
                    defaults={
                        'attendance': att,
                        'zoom_participant_uuid': rec.zoom_participant_uuid,
                        'zoom_user_id': rec.zoom_user_id,
                        'join_time': join_time,
                        'leave_time': rec.leave_time,
                        'duration_seconds': rec.duration_seconds,
                        'is_active': rec.is_active,
                        'source': AttendanceSession.Source.MANUAL,
                        'raw_evidence': rec.raw_evidence,
                    }
                )
                if not att.first_joined_at or join_time < att.first_joined_at:
                    att.first_joined_at = join_time
                if rec.leave_time and (not att.last_left_at or rec.leave_time > att.last_left_at):
                    att.last_left_at = rec.leave_time
                rec.status = UnmatchedZoomParticipant.Status.RESOLVED
                rec.linked_student = student
                rec.resolved_by = admin_user
                rec.resolved_at = now
                rec.save()

            att.is_currently_in_class = att.sessions.filter(is_active=True).exists()
            apply_automatic_attendance(att, final=bool(live_class.attendance_finalized_at))
            att.save()

            learned = None
            if account_id:
                link, created = ZoomIdentityLink.objects.select_for_update().get_or_create(
                    zoom_account_id=account_id,
                    defaults={'student': student, 'created_by': admin_user, 'source_live_class': live_class},
                )
                if not created and link.student_id != student.id and not link.is_ambiguous:
                    link.is_ambiguous = True
                    link.save(update_fields=['is_ambiguous', 'updated_at'])
                    logger.warning(f"[ZoomAttendance] Zoom account linked to two different students; marked ambiguous (link #{link.id}).")
                learned = "ambiguous" if link.is_ambiguous else ("learned" if created else "existing")

            unmatched.learned_identity = learned
            logger.info(f"[ZoomAttendance] Unmatched #{unmatched_id} linked to Student #{student_id} by admin #{admin_user.id} (identity={learned}).")
            return att, unmatched

    @classmethod
    def _same_participant_pending(cls, live_class, unmatched, account_id):
        def near(a, b):
            return a is None or b is None or abs(a - b) <= UNMATCHED_SAME_JOIN_WINDOW

        ref = unmatched.join_time or unmatched.leave_time
        same = []
        for rec in UnmatchedZoomParticipant.objects.select_for_update().filter(
            live_class=live_class, status=UnmatchedZoomParticipant.Status.PENDING
        ).exclude(pk=unmatched.pk):
            rec_time = rec.join_time or rec.leave_time
            if not near(rec_time, ref):
                continue
            if (account_id and stable_zoom_account_id(rec.raw_evidence or {}) == account_id) or (
                unmatched.zoom_user_id and rec.zoom_user_id == unmatched.zoom_user_id
            ) or (unmatched.zoom_participant_uuid and rec.zoom_participant_uuid == unmatched.zoom_participant_uuid):
                same.append(rec)
        return same

    @classmethod
    def ignore_unmatched_participant(cls, unmatched_id, admin_user):
        """Admin dismisses a queue record (e.g. a guest or a teacher's second device)."""
        with transaction.atomic():
            unmatched = UnmatchedZoomParticipant.objects.select_for_update().get(id=unmatched_id)
            if unmatched.status == UnmatchedZoomParticipant.Status.RESOLVED:
                raise ValueError("This participant has already been linked to a student.")
            if unmatched.status != UnmatchedZoomParticipant.Status.IGNORED:
                unmatched.status = UnmatchedZoomParticipant.Status.IGNORED
                unmatched.resolved_by = admin_user
                unmatched.resolved_at = timezone.now()
                unmatched.save(update_fields=['status', 'resolved_by', 'resolved_at', 'updated_at'])
            logger.info(f"[ZoomAttendance] Unmatched #{unmatched_id} ignored by admin #{admin_user.id}.")
            return unmatched
