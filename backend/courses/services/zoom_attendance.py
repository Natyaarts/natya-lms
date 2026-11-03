import logging
import urllib.parse
import secrets
import datetime
import re
import requests
from django.conf import settings
from django.utils import timezone
from django.db import transaction, IntegrityError
from django.contrib.auth import get_user_model
from courses.models import (
    LiveClass,
    Attendance,
    AttendanceSession,
    UnmatchedZoomParticipant,
    LiveClassJoinIntent,
    LiveClassRegistrant,
)
from courses.services.zoom import ZoomService
from courses.services.recording import ZoomRecordingService

logger = logging.getLogger(__name__)
User = get_user_model()


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

    @classmethod
    def register_student_for_meeting(cls, live_class, student):
        """
        Path A: If student has a valid email address, registers student with Zoom.
        Returns LiveClassRegistrant or None.
        """
        email = (student.email or '').strip()
        if not email or '@' not in email:
            return None

        existing = LiveClassRegistrant.objects.filter(live_class=live_class, student=student).first()
        if existing:
            return existing

        meeting_id = live_class.zoom_meeting_id
        if not meeting_id and live_class.meeting_url:
            match = re.search(r'/j/(\d+)', live_class.meeting_url)
            if match:
                meeting_id = match.group(1)

        if not meeting_id:
            logger.warning(f"[ZoomAttendance] LiveClass #{live_class.id} has no zoom_meeting_id for registration.")
            return None

        try:
            token = ZoomService.get_access_token()
            headers = {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json"
            }
            payload = {
                "email": email,
                "first_name": student.first_name.strip() or student.username,
                "last_name": student.last_name.strip() or "Student"
            }
            url = f"https://api.zoom.us/v2/meetings/{meeting_id}/registrants"
            res = requests.post(url, headers=headers, json=payload, timeout=10)

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
            else:
                logger.info(
                    f"[ZoomAttendance] Zoom registrant registration returned {res.status_code} for meeting {meeting_id}: {res.text}. "
                    f"Class will use standard join link."
                )
                return None
        except Exception as e:
            logger.warning(f"[ZoomAttendance] Error during Zoom meeting registrant call: {e}")
            return None

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
        Resolves Zoom participant to a Natya LMS student:
        1. Exact registrant_id match against LiveClassRegistrant.
        2. Exact verified email match against students enrolled/assigned to this class.
        3. Explicit customer_key matching a LiveClassJoinIntent token.
        Returns student (User) or None (routes to UnmatchedZoomParticipant).
        """
        registrant_id = str(participant.get('registrant_id') or '').strip()
        if registrant_id:
            reg = LiveClassRegistrant.objects.filter(
                live_class=live_class,
                registrant_id=registrant_id
            ).select_related('student').first()
            if reg:
                return reg.student

        # Check customer_key if passed by Zoom client
        customer_key = str(participant.get('customer_key') or '').strip()
        if customer_key:
            intent = LiveClassJoinIntent.objects.filter(
                live_class=live_class,
                token=customer_key,
                is_used=False
            ).select_related('student').first()
            if intent:
                intent.is_used = True
                intent.used_at = timezone.now()
                intent.save(update_fields=['is_used', 'used_at'])
                return intent.student

        # Check email
        email = (participant.get('email') or participant.get('user_email') or '').strip()
        if email and '@' in email:
            # Must match a user enrolled or assigned to this live class
            matched_user = User.objects.filter(email__iexact=email).first()
            if matched_user:
                is_member = False
                if live_class.batch and live_class.batch.students.filter(student=matched_user).exists():
                    is_member = True
                elif live_class.course and live_class.course.enrollments.filter(student=matched_user).exists():
                    is_member = True
                elif matched_user.is_staff or matched_user.is_superuser:
                    # Instructors/Staff handled separately
                    return matched_user

                if is_member:
                    return matched_user

        # If identity is ambiguous or phone-only without registrant_id, do NOT guess.
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

        live_class = ZoomRecordingService.find_live_class_for_zoom_meeting(meeting_id, meeting_uuid)
        if not live_class:
            logger.warning(f"[ZoomAttendance] No LiveClass found for meeting_id={meeting_id}, uuid={meeting_uuid}")
            return False

        participant_uuid = str(participant.get('participant_uuid') or participant.get('id') or '')
        zoom_user_id = str(participant.get('user_id') or '')
        registrant_id = str(participant.get('registrant_id') or '')
        user_name = str(participant.get('user_name') or '')
        email = str(participant.get('email') or participant.get('user_email') or '')
        join_time = parse_zoom_timestamp(participant.get('join_time') or payload.get('event_ts'))

        # Check if participant is staff/instructor/host
        instructor = live_class.instructor or (live_class.batch.instructor if live_class.batch else None)
        if email and instructor and instructor.email and email.lower() == instructor.email.lower():
            logger.info(f"[ZoomAttendance] Participant is instructor ({user_name}). Skipping student attendance.")
            return True

        student = cls.resolve_student_identity(live_class, participant, meeting_obj)

        if student:
            # If student is instructor/staff, skip
            if student.is_staff or student.is_superuser or (instructor and student == instructor):
                return True

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

                # Update duration minutes evidence (protects manual duration override, never auto-classifies status)
                total_sec = att.calculate_total_duration_seconds()
                if not getattr(att, 'is_duration_manual_override', False):
                    att.duration_minutes = int(total_sec // 60)
                att.save()

            logger.info(f"[ZoomAttendance] Student #{student.id} joined LiveClass #{live_class.id} (session={session_id})")
            return True
        else:
            # Route to UnmatchedZoomParticipant
            with transaction.atomic():
                unmatched, _ = UnmatchedZoomParticipant.objects.get_or_create(
                    live_class=live_class,
                    zoom_participant_uuid=participant_uuid,
                    status=UnmatchedZoomParticipant.Status.PENDING,
                    defaults={
                        'zoom_user_id': zoom_user_id,
                        'participant_name': user_name,
                        'participant_email': email,
                        'join_time': join_time,
                        'is_active': True,
                        'raw_evidence': participant
                    }
                )
                if not _:
                    unmatched.is_active = True
                    if not unmatched.join_time:
                        unmatched.join_time = join_time
                    unmatched.participant_name = user_name or unmatched.participant_name
                    unmatched.participant_email = email or unmatched.participant_email
                    unmatched.save()

            logger.info(f"[ZoomAttendance] Unmatched participant queued for LiveClass #{live_class.id}: name='{user_name}', uuid='{participant_uuid}'")
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

        live_class = ZoomRecordingService.find_live_class_for_zoom_meeting(meeting_id, meeting_uuid)
        if not live_class:
            logger.warning(f"[ZoomAttendance] No LiveClass found for meeting_id={meeting_id}, uuid={meeting_uuid}")
            return False

        participant_uuid = str(participant.get('participant_uuid') or participant.get('id') or '')
        zoom_user_id = str(participant.get('user_id') or '')
        registrant_id = str(participant.get('registrant_id') or '')
        user_name = str(participant.get('user_name') or '')
        email = str(participant.get('email') or participant.get('user_email') or '')
        leave_time = parse_zoom_timestamp(participant.get('leave_time') or payload.get('event_ts'))
        duration_sec = int(participant.get('duration', 0) or 0)

        instructor = live_class.instructor or (live_class.batch.instructor if live_class.batch else None)
        if email and instructor and instructor.email and email.lower() == instructor.email.lower():
            return True

        student = cls.resolve_student_identity(live_class, participant, meeting_obj)

        if student:
            if student.is_staff or student.is_superuser or (instructor and student == instructor):
                return True

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
                total_sec = att.calculate_total_duration_seconds()
                if not getattr(att, 'is_duration_manual_override', False):
                    att.duration_minutes = int(total_sec // 60)
                att.save()

            logger.info(f"[ZoomAttendance] Student #{student.id} left LiveClass #{live_class.id}. Total duration={att.duration_minutes}m")
            return True
        else:
            # Unmatched participant leave
            with transaction.atomic():
                unmatched = UnmatchedZoomParticipant.objects.filter(
                    live_class=live_class,
                    zoom_participant_uuid=participant_uuid,
                    status=UnmatchedZoomParticipant.Status.PENDING
                ).first()
                if unmatched:
                    unmatched.leave_time = leave_time
                    unmatched.is_active = False
                    if duration_sec > 0:
                        unmatched.duration_seconds = max(unmatched.duration_seconds, duration_sec)
                    elif unmatched.join_time:
                        unmatched.duration_seconds = max(unmatched.duration_seconds, int((leave_time - unmatched.join_time).total_seconds()))
                    unmatched.save()
                else:
                    UnmatchedZoomParticipant.objects.create(
                        live_class=live_class,
                        zoom_participant_uuid=participant_uuid,
                        zoom_user_id=zoom_user_id,
                        participant_name=user_name,
                        participant_email=email,
                        leave_time=leave_time,
                        duration_seconds=duration_sec,
                        is_active=False,
                        status=UnmatchedZoomParticipant.Status.PENDING,
                        raw_evidence=participant
                    )
            return True

    @classmethod
    def reconcile_meeting_participants(cls, live_class):
        """
        Reconciles attendance using Zoom Past Meeting Participants Report API:
        GET /v2/report/meetings/{meetingId}/participants
        Supports pagination, double-encoded UUIDs, and safe non-destructive evidence recording.
        Preserves manual admin decisions.
        """
        meeting_identifier = live_class.zoom_uuid or live_class.zoom_meeting_id
        if not meeting_identifier:
            logger.warning(f"[ZoomAttendance] LiveClass #{live_class.id} has no zoom_uuid or zoom_meeting_id for reconciliation.")
            return {"status": "no_meeting_id"}

        encoded_id = encode_zoom_uuid(meeting_identifier)
        token = ZoomService.get_access_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }

        all_participants = []
        next_page_token = ""
        page_size = 300

        while True:
            url = f"https://api.zoom.us/v2/report/meetings/{encoded_id}/participants?page_size={page_size}"
            if next_page_token:
                url += f"&next_page_token={urllib.parse.quote(next_page_token)}"

            res = requests.get(url, headers=headers, timeout=15)
            if res.status_code == 404:
                logger.info(f"[ZoomAttendance] Report not ready yet (404) for meeting {meeting_identifier}.")
                return {"status": "not_ready", "error": "Report not ready yet"}
            elif res.status_code != 200:
                logger.warning(f"[ZoomAttendance] Zoom report API returned {res.status_code}: {res.text}")
                return {"status": "error", "error": res.text}

            data = res.json()
            participants = data.get('participants', [])
            all_participants.extend(participants)
            next_page_token = data.get('next_page_token')
            if not next_page_token or len(participants) == 0:
                break

        reconciled_count = 0
        unmatched_count = 0

        for p in all_participants:
            join_time = parse_zoom_timestamp(p.get('join_time'))
            leave_time = parse_zoom_timestamp(p.get('leave_time'))
            dur_sec = int(p.get('duration', 0) or 0)
            p_uuid = str(p.get('id') or p.get('user_id') or '')

            student = cls.resolve_student_identity(live_class, p, {})

            if student:
                instructor = live_class.instructor or (live_class.batch.instructor if live_class.batch else None)
                if student.is_staff or student.is_superuser or (instructor and student == instructor):
                    continue

                with transaction.atomic():
                    att, _ = Attendance.objects.get_or_create(
                        live_class=live_class,
                        student=student,
                        defaults={'status': Attendance.Status.ABSENT}
                    )

                    # Create report session record
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

                    att.zoom_reported_duration_seconds += dur_sec
                    if not att.first_joined_at or join_time < att.first_joined_at:
                        att.first_joined_at = join_time
                    if not att.last_left_at or leave_time > att.last_left_at:
                        att.last_left_at = leave_time

                    # Recalculate duration from all sessions (protects manual duration override)
                    total_sec = att.calculate_total_duration_seconds()
                    if not getattr(att, 'is_duration_manual_override', False):
                        att.duration_minutes = int(total_sec // 60)
                    att.reconciliation_status = 'RECONCILED'
                    att.reconciled_at = timezone.now()
                    att.save()

                reconciled_count += 1
            else:
                unmatched_count += 1
                with transaction.atomic():
                    UnmatchedZoomParticipant.objects.get_or_create(
                        live_class=live_class,
                        zoom_participant_uuid=p_uuid,
                        join_time=join_time,
                        defaults={
                            'zoom_user_id': str(p.get('user_id') or ''),
                            'participant_name': str(p.get('name') or ''),
                            'participant_email': str(p.get('user_email') or ''),
                            'leave_time': leave_time,
                            'duration_seconds': dur_sec,
                            'is_active': False,
                            'status': UnmatchedZoomParticipant.Status.PENDING,
                            'raw_evidence': p
                        }
                    )

        logger.info(
            f"[ZoomAttendance] Reconciled LiveClass #{live_class.id}: "
            f"{reconciled_count} matched, {unmatched_count} unmatched, {len(all_participants)} total."
        )
        return {
            "status": "success",
            "reconciled": reconciled_count,
            "unmatched": unmatched_count,
            "total_records": len(all_participants)
        }

    @classmethod
    def link_unmatched_participant(cls, unmatched_id, student_id, admin_user):
        """
        Manually links an UnmatchedZoomParticipant to a student.
        Uses database row locking (select_for_update) to prevent concurrent resolution races.
        Preserves original evidence and updates duration without overwriting manual status.
        """
        with transaction.atomic():
            unmatched = UnmatchedZoomParticipant.objects.select_for_update().get(id=unmatched_id)
            if unmatched.status == UnmatchedZoomParticipant.Status.RESOLVED:
                raise ValueError("This participant has already been linked by another administrator.")

            student = User.objects.get(id=student_id)
            live_class = unmatched.live_class

            # Verify student belongs to this batch/class
            is_valid_student = False
            if live_class.batch and live_class.batch.students.filter(student=student).exists():
                is_valid_student = True
            elif live_class.course and live_class.course.enrollments.filter(student=student).exists():
                is_valid_student = True

            if not is_valid_student:
                raise ValueError("Student is not enrolled or assigned to this live class.")

            # Concurrency-safe row locking on Attendance to prevent race conditions
            att = Attendance.objects.filter(live_class=live_class, student=student).select_for_update().first()
            if not att:
                try:
                    with transaction.atomic():
                        att = Attendance.objects.create(
                            live_class=live_class,
                            student=student,
                            status=Attendance.Status.ABSENT
                        )
                except IntegrityError:
                    att = Attendance.objects.filter(live_class=live_class, student=student).select_for_update().first()

            join_time = unmatched.join_time or timezone.now()
            leave_time = unmatched.leave_time
            dur_sec = unmatched.duration_seconds
            session_id = f"manual:{unmatched.id}:{student.id}:{int(join_time.timestamp())}"

            AttendanceSession.objects.update_or_create(
                session_id=session_id,
                defaults={
                    'attendance': att,
                    'zoom_participant_uuid': unmatched.zoom_participant_uuid,
                    'zoom_user_id': unmatched.zoom_user_id,
                    'join_time': join_time,
                    'leave_time': leave_time,
                    'duration_seconds': dur_sec,
                    'is_active': unmatched.is_active,
                    'source': AttendanceSession.Source.MANUAL,
                    'raw_evidence': unmatched.raw_evidence
                }
            )

            if not att.first_joined_at or join_time < att.first_joined_at:
                att.first_joined_at = join_time
            if leave_time and (not att.last_left_at or leave_time > att.last_left_at):
                att.last_left_at = leave_time

            total_sec = att.calculate_total_duration_seconds()
            if not getattr(att, 'is_duration_manual_override', False):
                att.duration_minutes = int(total_sec // 60)
            att.is_currently_in_class = att.sessions.filter(is_active=True).exists()
            att.save()

            unmatched.status = UnmatchedZoomParticipant.Status.RESOLVED
            unmatched.linked_student = student
            unmatched.resolved_by = admin_user
            unmatched.resolved_at = timezone.now()
            unmatched.save()

            logger.info(f"[ZoomAttendance] Unmatched #{unmatched_id} successfully linked to Student #{student_id} by {admin_user.username}")
            return att, unmatched
