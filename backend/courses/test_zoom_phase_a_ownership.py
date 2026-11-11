"""
Unit and integration tests for Phase A: Zoom Meeting Ownership, Durable Operations,
and Historical Conflict Auditing.

Target Database: Verified Local PostgreSQL 18 (natya_test_db on port 5434).
"""
import uuid
import datetime
import hashlib
import importlib
import concurrent.futures
from django.test import TestCase, TransactionTestCase
from django.utils import timezone
from django.core.exceptions import ValidationError
from django.db import IntegrityError, models, transaction
from django.db.models import ProtectedError
from django.db.migrations.exceptions import IrreversibleError
from django.contrib.auth import get_user_model

from courses.models import (
    Course,
    LiveClass,
    LiveBatch,
    ZoomMeetingRecord,
    LiveClassDurableOperation,
    HistoricalZoomConflict
)

migration_0035 = importlib.import_module("courses.migrations.0035_zoom_meeting_record_ownership_closure")
preflight_and_backfill_historical_data = migration_0035.preflight_and_backfill_historical_data

User = get_user_model()


class ZoomPhaseAOwnershipModelTests(TestCase):
    """
    Unit tests for model definitions, field validations, and relationships.
    """

    @classmethod
    def setUpTestData(cls):
        cls.instructor = User.objects.create_user(
            username="test_instructor_ph_a",
            email="instructor_ph_a@example.com",
            password="testpassword123"
        )
        cls.course = Course.objects.create(
            title="Phase A Test Course",
            description="Testing course for Phase A Zoom ownership",
            price=100.00
        )
        cls.live_class = LiveClass.objects.create(
            course=cls.course,
            instructor=cls.instructor,
            title="Phase A Live Session 1",
            scheduled_start=timezone.now() + datetime.timedelta(days=1),
            duration_minutes=60,
            meeting_provider=LiveClass.MeetingProvider.ZOOM,
            meeting_url="https://zoom.us/j/12345678901",
            zoom_meeting_id="12345678901",
            status=LiveClass.ClassStatus.SCHEDULED
        )

    def test_live_class_durable_operation_lifecycle(self):
        """Verify LiveClassDurableOperation creation, defaults, and status transitions."""
        op = LiveClassDurableOperation.objects.create(
            idempotency_key="op-test-provision-001",
            operation_type=LiveClassDurableOperation.OperationType.ZOOM_PROVISION,
            status=LiveClassDurableOperation.Status.IN_PROGRESS,
            live_class=self.live_class,
            payload={"topic": "Test Meeting"},
            payload_hash=hashlib.sha256(b"payload").hexdigest()
        )
        self.assertIsNotNone(op.id)
        self.assertEqual(op.status, LiveClassDurableOperation.Status.IN_PROGRESS)
        self.assertEqual(op.operation_type, LiveClassDurableOperation.OperationType.ZOOM_PROVISION)
        self.assertEqual(op.live_class, self.live_class)

        # Status transition to COMPLETED
        op.status = LiveClassDurableOperation.Status.COMPLETED
        op.response_payload = {"id": "12345678901", "join_url": "https://zoom.us/j/12345678901"}
        op.save()
        op.refresh_from_db()
        self.assertEqual(op.status, LiveClassDurableOperation.Status.COMPLETED)
        self.assertEqual(op.response_payload["id"], "12345678901")

    def test_durable_operation_unique_idempotency_key(self):
        """Enforces unique constraint on idempotency_key."""
        LiveClassDurableOperation.objects.create(
            idempotency_key="unique-idem-key-100",
            operation_type=LiveClassDurableOperation.OperationType.ZOOM_PROVISION,
            status=LiveClassDurableOperation.Status.IN_PROGRESS
        )
        with self.assertRaises(IntegrityError):
            LiveClassDurableOperation.objects.create(
                idempotency_key="unique-idem-key-100",
                operation_type=LiveClassDurableOperation.OperationType.ZOOM_PROVISION,
                status=LiveClassDurableOperation.Status.IN_PROGRESS
            )

    def test_zoom_meeting_record_assigned_state(self):
        """Verify ZoomMeetingRecord 1:1 assignment and properties."""
        zmr = ZoomMeetingRecord.objects.create(
            zoom_meeting_id="12345678901",
            topic="Phase A Live Session 1",
            state=ZoomMeetingRecord.State.ASSIGNED,
            live_class=self.live_class,
            historical_live_class_id=self.live_class.id
        )
        self.assertIsNotNone(zmr.id)
        self.assertIsNotNone(zmr.owner_token)
        self.assertEqual(zmr.state, ZoomMeetingRecord.State.ASSIGNED)
        self.assertEqual(self.live_class.zoom_meeting_record, zmr)

    def test_zoom_meeting_record_unique_meeting_id(self):
        """Enforces global uniqueness on zoom_meeting_id across all states."""
        ZoomMeetingRecord.objects.create(
            zoom_meeting_id="99988877711",
            topic="First Record",
            state=ZoomMeetingRecord.State.RESERVED
        )
        with self.assertRaises(IntegrityError):
            ZoomMeetingRecord.objects.create(
                zoom_meeting_id="99988877711",
                topic="Duplicate Record",
                state=ZoomMeetingRecord.State.RESERVED
            )

    def test_historical_zoom_conflict_retention(self):
        """Verify soft-link preservation and audit details on HistoricalZoomConflict."""
        conflict = HistoricalZoomConflict.objects.create(
            live_class=self.live_class,
            historical_live_class_id=self.live_class.id,
            live_class_snapshot={
                "title": self.live_class.title,
                "status": self.live_class.status,
                "scheduled_start": self.live_class.scheduled_start.isoformat()
            },
            zoom_meeting_id_raw="https://zoom.us/j/999888777?extra_param=overflow_value_here",
            conflict_type="LENGTH_OVERFLOW",
            details={"original_length": 58}
        )
        self.assertIsNotNone(conflict.id)
        self.assertFalse(conflict.resolved)
        self.assertEqual(conflict.conflict_type, "LENGTH_OVERFLOW")
        self.assertEqual(conflict.live_class_snapshot["title"], "Phase A Live Session 1")


class ZoomPhaseADatabaseConstraintTests(TransactionTestCase):
    """
    Integration tests against PostgreSQL check constraints, exclusion constraints,
    and foreign key deletion protection.
    """

    def setUp(self):
        self.instructor = User.objects.create_user(
            username=f"inst_{uuid.uuid4().hex[:8]}",
            email=f"inst_{uuid.uuid4().hex[:8]}@example.com",
            password="testpassword123"
        )
        self.course = Course.objects.create(
            title=f"Course {uuid.uuid4().hex[:6]}",
            description="Testing course",
            price=100.00
        )
        self.live_class = LiveClass.objects.create(
            course=self.course,
            instructor=self.instructor,
            title="Active Session",
            scheduled_start=timezone.now() + datetime.timedelta(days=2),
            duration_minutes=60,
            meeting_provider=LiveClass.MeetingProvider.ZOOM,
            meeting_url="https://zoom.us/j/55566677788",
            zoom_meeting_id="55566677788",
            status=LiveClass.ClassStatus.SCHEDULED
        )

    def test_constraint_assigned_requires_live_class(self):
        """CHECK check_zmr_assigned_has_live_class: ASSIGNED requires live_class NOT NULL."""
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ZoomMeetingRecord.objects.create(
                    zoom_meeting_id="11122233344",
                    state=ZoomMeetingRecord.State.ASSIGNED,
                    live_class=None
                )

    def test_constraint_compensating_requires_no_live_class(self):
        """CHECK check_zmr_compensating_no_live_class: COMPENSATING requires live_class IS NULL."""
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ZoomMeetingRecord.objects.create(
                    zoom_meeting_id="22233344455",
                    state=ZoomMeetingRecord.State.COMPENSATING,
                    live_class=self.live_class
                )

    def test_constraint_compensated_requires_no_live_class(self):
        """CHECK check_zmr_compensated_no_live_class: COMPENSATED requires live_class IS NULL."""
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ZoomMeetingRecord.objects.create(
                    zoom_meeting_id="33344455566",
                    state=ZoomMeetingRecord.State.COMPENSATED,
                    live_class=self.live_class
                )

    def test_constraint_numeric_meeting_id_enforcement(self):
        """CHECK check_zmr_meeting_id_format: Malformed identifier allowed ONLY if state='POISONED'."""
        # Non-numeric invalid format with state RESERVED -> must fail
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ZoomMeetingRecord.objects.create(
                    zoom_meeting_id="bad-id",
                    state=ZoomMeetingRecord.State.RESERVED
                )

        # Non-numeric with state POISONED -> must SUCCEED
        poisoned = ZoomMeetingRecord.objects.create(
            zoom_meeting_id="OVERFLOW-MALFORMED-100",
            state=ZoomMeetingRecord.State.POISONED,
            quarantine_reason="Malformed identifier quarantine test"
        )
        self.assertIsNotNone(poisoned.id)
        self.assertEqual(poisoned.state, ZoomMeetingRecord.State.POISONED)

    def test_foreign_key_deletion_protection(self):
        """LiveClass deletion is protected when ASSIGNED ZoomMeetingRecord exists."""
        ZoomMeetingRecord.objects.create(
            zoom_meeting_id="55566677788",
            state=ZoomMeetingRecord.State.ASSIGNED,
            live_class=self.live_class,
            historical_live_class_id=self.live_class.id
        )
        # Attempting to delete LiveClass directly must be blocked by models.PROTECT
        with self.assertRaises(ProtectedError):
            self.live_class.delete()

    def test_historical_conflict_survives_live_class_removal(self):
        """Deleting a LiveClass preserves HistoricalZoomConflict row with live_class=NULL."""
        unprotected_class = LiveClass.objects.create(
            course=self.course,
            instructor=self.instructor,
            title="Unprotected Session",
            scheduled_start=timezone.now() + datetime.timedelta(days=3),
            duration_minutes=45,
            meeting_provider=LiveClass.MeetingProvider.ZOOM,
            meeting_url="https://zoom.us/j/44455566677",
            zoom_meeting_id="44455566677",
            status=LiveClass.ClassStatus.SCHEDULED
        )
        conflict = HistoricalZoomConflict.objects.create(
            live_class=unprotected_class,
            historical_live_class_id=unprotected_class.id,
            live_class_snapshot={"title": unprotected_class.title},
            zoom_meeting_id_raw="44455566677",
            conflict_type="DUPLICATE_COLLISION"
        )
        conflict_id = conflict.id
        unprotected_class_id = unprotected_class.id

        # Delete the LiveClass
        unprotected_class.delete()

        # Conflict row must still exist, with live_class set to NULL (SET_NULL)
        conflict.refresh_from_db()
        self.assertIsNone(conflict.live_class)
        self.assertEqual(conflict.historical_live_class_id, unprotected_class_id)

    def test_exclusion_constraint_blocks_overlapping_schedule(self):
        """PostgreSQL exclusion constraint blocks overlapping live class schedules for same instructor."""
        start = timezone.now() + datetime.timedelta(days=10)
        LiveClass.objects.create(
            course=self.course,
            instructor=self.instructor,
            title="Class A",
            scheduled_start=start,
            duration_minutes=60,
            meeting_url="https://zoom.us/j/12345678901",
            status=LiveClass.ClassStatus.SCHEDULED
        )
        # Attempt to create overlapping class for same instructor
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                LiveClass.objects.create(
                    course=self.course,
                    instructor=self.instructor,
                    title="Class B Overlapping",
                    scheduled_start=start + datetime.timedelta(minutes=30),
                    duration_minutes=60,
                    meeting_url="https://zoom.us/j/12345678901",
                    status=LiveClass.ClassStatus.SCHEDULED
                )


class ZoomPhaseABackfillAndReconciliationTests(TestCase):
    """
    Validation of preflight_and_backfill_historical_data:
    - Partitioning across all 4 categories (Duplicates, Overflows, Cancelled, Clean Active)
    - Idempotency on repeated execution
    - Bidirectional reconciliation accounting
    """

    @classmethod
    def setUpTestData(cls):
        cls.instructor = User.objects.create_user(
            username="backfill_instructor",
            email="backfill_inst@example.com",
            password="testpassword123"
        )
        cls.course = Course.objects.create(
            title="Backfill Test Course",
            description="Testing historical backfill partitioning",
            price=150.00
        )

        base_time = timezone.now() + datetime.timedelta(days=5)

        # 1. Clean Active Class -> Case D
        cls.clean_class = LiveClass.objects.create(
            course=cls.course,
            instructor=cls.instructor,
            title="Clean Class",
            scheduled_start=base_time,
            duration_minutes=60,
            meeting_provider=LiveClass.MeetingProvider.ZOOM,
            meeting_url="https://zoom.us/j/10000000001",
            zoom_meeting_id="10000000001",
            status=LiveClass.ClassStatus.SCHEDULED
        )

        # 2. Cancelled Class -> Case C
        cls.cancelled_class = LiveClass.objects.create(
            course=cls.course,
            instructor=cls.instructor,
            title="Cancelled Class",
            scheduled_start=base_time + datetime.timedelta(hours=2),
            duration_minutes=60,
            meeting_provider=LiveClass.MeetingProvider.ZOOM,
            meeting_url="https://zoom.us/j/20000000002",
            zoom_meeting_id="20000000002",
            status=LiveClass.ClassStatus.CANCELLED
        )

        # 3. Duplicate Classes -> Case A
        cls.dup_class_1 = LiveClass.objects.create(
            course=cls.course,
            instructor=cls.instructor,
            title="Duplicate Class 1",
            scheduled_start=base_time + datetime.timedelta(hours=4),
            duration_minutes=60,
            meeting_provider=LiveClass.MeetingProvider.ZOOM,
            meeting_url="https://zoom.us/j/30000000003",
            zoom_meeting_id="30000000003",
            status=LiveClass.ClassStatus.COMPLETED
        )
        cls.dup_class_2 = LiveClass.objects.create(
            course=cls.course,
            instructor=cls.instructor,
            title="Duplicate Class 2",
            scheduled_start=base_time + datetime.timedelta(hours=6),
            duration_minutes=60,
            meeting_provider=LiveClass.MeetingProvider.ZOOM,
            meeting_url="https://zoom.us/j/30000000003",
            zoom_meeting_id="30000000003",
            status=LiveClass.ClassStatus.SCHEDULED
        )

        # 4. Malformed Non-Numeric Class (<= 64 chars) -> Case B
        malformed_id = "https://zoom.us/j/99999999999?pwd=test"
        cls.malformed_class = LiveClass.objects.create(
            course=cls.course,
            instructor=cls.instructor,
            title="Malformed ID Class",
            scheduled_start=base_time + datetime.timedelta(hours=8),
            duration_minutes=60,
            meeting_provider=LiveClass.MeetingProvider.ZOOM,
            meeting_url=malformed_id,
            zoom_meeting_id=malformed_id,
            status=LiveClass.ClassStatus.SCHEDULED
        )

    def test_backfill_partitioning_and_idempotency(self):
        """Execute backfill twice to verify partitioning and strict idempotency."""
        from django.apps import apps

        # Run 1: Initial backfill
        preflight_and_backfill_historical_data(apps, None)

        # Check Clean Class -> ASSIGNED
        clean_record = ZoomMeetingRecord.objects.get(zoom_meeting_id="10000000001")
        self.assertEqual(clean_record.state, ZoomMeetingRecord.State.ASSIGNED)
        self.assertEqual(clean_record.live_class, self.clean_class)

        # Check Cancelled Class -> POISONED
        cancelled_record = ZoomMeetingRecord.objects.get(zoom_meeting_id="20000000002")
        self.assertEqual(cancelled_record.state, ZoomMeetingRecord.State.POISONED)
        self.assertIsNone(cancelled_record.live_class)
        self.assertIn("HISTORICAL_CANCELLED", cancelled_record.quarantine_reason)

        # Check Duplicate Classes -> 2 conflicts, 1 POISONED record
        dup_conflicts = HistoricalZoomConflict.objects.filter(conflict_type="DUPLICATE_COLLISION")
        self.assertEqual(dup_conflicts.count(), 2)
        dup_record = ZoomMeetingRecord.objects.get(zoom_meeting_id="30000000003")
        self.assertEqual(dup_record.state, ZoomMeetingRecord.State.POISONED)
        self.assertIsNone(dup_record.live_class)

        # Check Malformed Class -> 1 MALFORMED_FORMAT conflict, 1 POISONED record
        malformed_conflicts = HistoricalZoomConflict.objects.filter(conflict_type="MALFORMED_FORMAT")
        self.assertEqual(malformed_conflicts.count(), 1)
        self.assertTrue(ZoomMeetingRecord.objects.filter(state=ZoomMeetingRecord.State.POISONED, quarantine_reason__contains="MALFORMED_FORMAT").exists())

        # Count total records before Run 2
        total_zmr_before = ZoomMeetingRecord.objects.count()
        total_conflicts_before = HistoricalZoomConflict.objects.count()

        # Run 2: Idempotent rerun
        preflight_and_backfill_historical_data(apps, None)

        # Counts MUST be identical (no duplicate rows inserted)
        self.assertEqual(ZoomMeetingRecord.objects.count(), total_zmr_before)
        self.assertEqual(HistoricalZoomConflict.objects.count(), total_conflicts_before)

    def test_bidirectional_reconciliation_accounting(self):
        """
        Verify bidirectional reconciliation SQL logic:
        Every LiveClass with a non-empty zoom_meeting_id must be accounted for by:
        EITHER an ASSIGNED ZoomMeetingRecord OR an entry in HistoricalZoomConflict.
        """
        from django.apps import apps
        preflight_and_backfill_historical_data(apps, None)

        # Every LiveClass with non-empty zoom_meeting_id
        historical_classes = LiveClass.objects.exclude(zoom_meeting_id='').exclude(zoom_meeting_id__isnull=True)
        unaccounted = []

        for lc in historical_classes:
            has_assigned_record = ZoomMeetingRecord.objects.filter(live_class=lc, state=ZoomMeetingRecord.State.ASSIGNED).exists()
            has_conflict = HistoricalZoomConflict.objects.filter(historical_live_class_id=lc.id).exists()
            has_poisoned_cancelled = (lc.status == 'CANCELLED' and ZoomMeetingRecord.objects.filter(historical_live_class_id=lc.id, state=ZoomMeetingRecord.State.POISONED).exists())

            if not (has_assigned_record or has_conflict or has_poisoned_cancelled):
                unaccounted.append(lc.id)

        # Unaccounted list MUST be empty
        self.assertEqual(unaccounted, [], f"Unaccounted historical classes found: {unaccounted}")


class ZoomPhaseASubstituteAndConcurrencyTests(TransactionTestCase):
    """
    Integration tests covering Defect 1, Defect 2, Defect 5, and Defect 6 requirements:
    - Substitute instructor identity & precedence
    - Substitute validation (eligibility, non-identity with primary)
    - Effective instructor exclusion constraint in PostgreSQL
    - Primary instructor freedom when substituted
    - Substitute double-booking prevention
    - Reversion/removal schedule collision detection
    - Concurrent multi-threaded idempotency-key creation
    - Concurrent substitute assignment
    - Worker lease expiration and fencing
    - Migration rollback prohibition (IrreversibleError)
    """

    def setUp(self):
        # Primary instructor (teacher)
        self.primary_inst = User.objects.create_user(
            username=f"prim_{uuid.uuid4().hex[:8]}",
            email=f"prim_{uuid.uuid4().hex[:8]}@example.com",
            password="testpassword123",
            is_staff=True
        )
        # Substitute instructor (teacher)
        self.sub_inst = User.objects.create_user(
            username=f"sub_{uuid.uuid4().hex[:8]}",
            email=f"sub_{uuid.uuid4().hex[:8]}@example.com",
            password="testpassword123",
            is_staff=True
        )
        # Ineligible student user
        self.student_user = User.objects.create_user(
            username=f"stud_{uuid.uuid4().hex[:8]}",
            email=f"stud_{uuid.uuid4().hex[:8]}@example.com",
            password="testpassword123",
            is_staff=False
        )
        self.course = Course.objects.create(
            title=f"Course {uuid.uuid4().hex[:6]}",
            description="Testing course",
            price=100.00
        )
        self.batch = LiveBatch.objects.create(
            course=self.course,
            instructor=self.primary_inst,
            batch_type=LiveBatch.BatchType.GROUP
        )

    def test_substitute_instructor_identity_and_precedence(self):
        """Defect 1: Substitute instructor identity is preserved and takes precedence."""
        start_time = timezone.now() + datetime.timedelta(days=20)
        lc = LiveClass.objects.create(
            course=self.course,
            batch=self.batch,
            title="Substituted Session",
            scheduled_start=start_time,
            duration_minutes=60,
            substitute_instructor=self.sub_inst,
            meeting_url="https://zoom.us/j/12345678901",
            status=LiveClass.ClassStatus.SCHEDULED
        )
        lc.refresh_from_db()

        # Batch instructor preserved on lc.instructor
        self.assertEqual(lc.instructor, self.primary_inst)
        # Substitute instructor preserved on lc.substitute_instructor
        self.assertEqual(lc.substitute_instructor, self.sub_inst)
        # Boolean flag is True
        self.assertTrue(lc.is_substitute_instructor)
        # Effective instructor is the substitute
        self.assertEqual(lc.effective_instructor, self.sub_inst)
        self.assertEqual(lc.effective_instructor_id, self.sub_inst.id)

        # Removal of substitute
        lc.substitute_instructor = None
        lc.save()
        lc.refresh_from_db()
        self.assertIsNone(lc.substitute_instructor)
        self.assertFalse(lc.is_substitute_instructor)
        # Effective instructor falls back to primary instructor
        self.assertEqual(lc.effective_instructor, self.primary_inst)
        self.assertEqual(lc.effective_instructor_id, self.primary_inst.id)

    def test_substitute_instructor_validation(self):
        """Defect 1: Ineligible substitute or self-substitution rejected."""
        start_time = timezone.now() + datetime.timedelta(days=21)

        # Case 1: Student cannot be substitute
        with self.assertRaises(ValidationError):
            lc = LiveClass(
                course=self.course,
                batch=self.batch,
                title="Invalid Sub Class",
                meeting_url="https://zoom.us/j/12345678901",
                scheduled_start=start_time,
                duration_minutes=60,
                substitute_instructor=self.student_user
            )
            lc.clean()

        # Case 2: Primary instructor cannot substitute for themselves
        with self.assertRaises(ValidationError):
            lc = LiveClass(
                course=self.course,
                batch=self.batch,
                title="Self Sub Class",
                meeting_url="https://zoom.us/j/12345678901",
                scheduled_start=start_time,
                duration_minutes=60,
                substitute_instructor=self.primary_inst
            )
            lc.clean()

    def test_inactive_substitute_rejected(self):
        """Phase A.3 Defect 2: Inactive teacher, mentor, and staff users are rejected; active eligible user is accepted."""
        start_time = timezone.now() + datetime.timedelta(days=28)

        # 1. Inactive teacher
        inactive_teacher = User.objects.create_user(
            username=f"inact_teach_{uuid.uuid4().hex[:6]}",
            email=f"inact_teach_{uuid.uuid4().hex[:6]}@example.com",
            password="testpassword123",
            is_teacher=True,
            is_active=False
        )
        with self.assertRaises(ValidationError) as ctx:
            lc1 = LiveClass(
                course=self.course,
                batch=self.batch,
                title="Inactive Teacher Sub",
                meeting_url="https://zoom.us/j/12345678921",
                scheduled_start=start_time,
                duration_minutes=60,
                substitute_instructor=inactive_teacher
            )
            lc1.clean()
        self.assertIn("must be an active user", str(ctx.exception))

        # 2. Inactive mentor
        inactive_mentor = User.objects.create_user(
            username=f"inact_mentor_{uuid.uuid4().hex[:6]}",
            email=f"inact_mentor_{uuid.uuid4().hex[:6]}@example.com",
            password="testpassword123",
            is_mentor=True,
            is_active=False
        )
        with self.assertRaises(ValidationError) as ctx:
            lc2 = LiveClass(
                course=self.course,
                batch=self.batch,
                title="Inactive Mentor Sub",
                meeting_url="https://zoom.us/j/12345678922",
                scheduled_start=start_time,
                duration_minutes=60,
                substitute_instructor=inactive_mentor
            )
            lc2.clean()
        self.assertIn("must be an active user", str(ctx.exception))

        # 3. Inactive staff
        inactive_staff = User.objects.create_user(
            username=f"inact_staff_{uuid.uuid4().hex[:6]}",
            email=f"inact_staff_{uuid.uuid4().hex[:6]}@example.com",
            password="testpassword123",
            is_staff=True,
            is_active=False
        )
        with self.assertRaises(ValidationError) as ctx:
            lc3 = LiveClass(
                course=self.course,
                batch=self.batch,
                title="Inactive Staff Sub",
                meeting_url="https://zoom.us/j/12345678923",
                scheduled_start=start_time,
                duration_minutes=60,
                substitute_instructor=inactive_staff
            )
            lc3.clean()
        self.assertIn("must be an active user", str(ctx.exception))

        # 4. Active eligible teacher (accepted)
        active_teacher = User.objects.create_user(
            username=f"act_teach_{uuid.uuid4().hex[:6]}",
            email=f"act_teach_{uuid.uuid4().hex[:6]}@example.com",
            password="testpassword123",
            is_teacher=True,
            is_active=True
        )
        lc_valid = LiveClass(
            course=self.course,
            batch=self.batch,
            title="Active Teacher Sub",
            meeting_url="https://zoom.us/j/12345678924",
            scheduled_start=start_time,
            duration_minutes=60,
            substitute_instructor=active_teacher
        )
        lc_valid.clean()  # Should not raise

    def test_substitute_user_deletion_under_set_null(self):
        """Phase A.3 Defect 3: Deleting a substitute user under SET_NULL triggers parity sync and satisfies check constraint."""
        start_time = timezone.now() + datetime.timedelta(days=29)
        temp_sub = User.objects.create_user(
            username=f"temp_sub_{uuid.uuid4().hex[:6]}",
            email=f"temp_sub_{uuid.uuid4().hex[:6]}@example.com",
            password="testpassword123",
            is_teacher=True,
            is_active=True
        )
        lc = LiveClass.objects.create(
            course=self.course,
            batch=self.batch,
            title="Class for Sub Deletion",
            meeting_url="https://zoom.us/j/12345678925",
            scheduled_start=start_time,
            duration_minutes=60,
            substitute_instructor=temp_sub,
            status=LiveClass.ClassStatus.SCHEDULED
        )
        lc.refresh_from_db()
        self.assertEqual(lc.substitute_instructor_id, temp_sub.id)
        self.assertTrue(lc.is_substitute_instructor)

        # Delete substitute user: on_delete=SET_NULL should run and DB trigger should update is_substitute_instructor to False
        temp_sub.delete()

        lc.refresh_from_db()
        self.assertIsNone(lc.substitute_instructor)
        self.assertFalse(lc.is_substitute_instructor)

    def test_bulk_and_sql_update_parity_trigger(self):
        """Phase A.3 Defect 3: Parity trigger synchronizes is_substitute_instructor on bulk ORM updates and raw SQL."""
        from django.db import connection
        start_time = timezone.now() + datetime.timedelta(days=30)
        new_sub = User.objects.create_user(
            username=f"parity_sub_{uuid.uuid4().hex[:6]}",
            email=f"parity_sub_{uuid.uuid4().hex[:6]}@example.com",
            password="testpassword123",
            is_teacher=True,
            is_active=True
        )
        lc = LiveClass.objects.create(
            course=self.course,
            batch=self.batch,
            title="Parity Trigger Class",
            meeting_url="https://zoom.us/j/12345678926",
            scheduled_start=start_time,
            duration_minutes=60,
            substitute_instructor=new_sub,
            status=LiveClass.ClassStatus.SCHEDULED
        )
        lc.refresh_from_db()
        self.assertTrue(lc.is_substitute_instructor)

        # 1. Bulk ORM update clearing substitute
        LiveClass.objects.filter(id=lc.id).update(substitute_instructor=None)
        lc.refresh_from_db()
        self.assertIsNone(lc.substitute_instructor)
        self.assertFalse(lc.is_substitute_instructor)

        # 2. Raw SQL update setting substitute_instructor_id directly
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE courses_liveclass SET substitute_instructor_id = %s WHERE id = %s",
                [new_sub.id, lc.id]
            )
        lc.refresh_from_db()
        self.assertEqual(lc.substitute_instructor_id, new_sub.id)
        self.assertTrue(lc.is_substitute_instructor)

    def test_historical_completed_classes_exclusion_constraint_policy(self):
        """Phase A.3 Defect 4: COMPLETED classes do not block scheduling; SCHEDULED and LIVE classes are strictly constrained."""
        start_time = timezone.now() - datetime.timedelta(days=10)

        # 1. Historical COMPLETED classes can overlap without constraint violation
        c_comp_1 = LiveClass.objects.create(
            course=self.course,
            instructor=self.primary_inst,
            title="Completed Class 1",
            meeting_url="https://zoom.us/j/12345678927",
            scheduled_start=start_time,
            duration_minutes=60,
            status=LiveClass.ClassStatus.COMPLETED
        )
        other_course = Course.objects.create(title="Historical Course", price=50.0)
        c_comp_2 = LiveClass.objects.create(
            course=other_course,
            instructor=self.primary_inst,
            title="Completed Class 2 (Overlapping)",
            meeting_url="https://zoom.us/j/12345678928",
            scheduled_start=start_time + datetime.timedelta(minutes=15),
            duration_minutes=60,
            status=LiveClass.ClassStatus.COMPLETED
        )
        self.assertIsNotNone(c_comp_1.id)
        self.assertIsNotNone(c_comp_2.id)

        # 2. Active SCHEDULED and LIVE classes are strictly protected against overlaps
        future_time = timezone.now() + datetime.timedelta(days=31)
        LiveClass.objects.create(
            course=self.course,
            instructor=self.primary_inst,
            title="Future Scheduled Class 1",
            meeting_url="https://zoom.us/j/12345678929",
            scheduled_start=future_time,
            duration_minutes=60,
            status=LiveClass.ClassStatus.SCHEDULED
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                LiveClass.objects.create(
                    course=other_course,
                    instructor=self.primary_inst,
                    title="Future Live Class 2 (Overlapping Active)",
                    meeting_url="https://zoom.us/j/12345678930",
                    scheduled_start=future_time + datetime.timedelta(minutes=20),
                    duration_minutes=60,
                    status=LiveClass.ClassStatus.LIVE
                )

    def test_effective_instructor_overlap_blocks_substitute(self):
        """Defect 2: Exclusion constraint enforces schedule against the substitute instructor."""
        start_time = timezone.now() + datetime.timedelta(days=22)

        # Class 1: Primary=P, Substitute=S
        LiveClass.objects.create(
            course=self.course,
            batch=self.batch,
            title="Class 1 with Sub S",
            scheduled_start=start_time,
            duration_minutes=60,
            substitute_instructor=self.sub_inst,
            meeting_url="https://zoom.us/j/12345678901",
            status=LiveClass.ClassStatus.SCHEDULED
        )

        # Class 2: Another course, Primary=S (no substitute) at overlapping time
        other_course = Course.objects.create(title="Other Course", price=50.0)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                LiveClass.objects.create(
                    course=other_course,
                    instructor=self.sub_inst,
                    title="Class 2 S Primary Overlap",
                    scheduled_start=start_time + datetime.timedelta(minutes=15),
                    duration_minutes=60,
                    meeting_url="https://zoom.us/j/12345678902",
                    status=LiveClass.ClassStatus.SCHEDULED
                )

    def test_primary_instructor_freed_when_substituted(self):
        """Defect 2: Primary instructor is NOT blocked by exclusion constraint when class is substituted."""
        start_time = timezone.now() + datetime.timedelta(days=23)

        # Class 1: Primary=P, Substituted by S
        LiveClass.objects.create(
            course=self.course,
            batch=self.batch,
            title="Class 1 P substituted by S",
            scheduled_start=start_time,
            duration_minutes=60,
            substitute_instructor=self.sub_inst,
            meeting_url="https://zoom.us/j/12345678901",
            status=LiveClass.ClassStatus.SCHEDULED
        )

        # Class 2: Primary=P on a different batch/course at the exact same time
        other_course = Course.objects.create(title="Other Course P", price=50.0)
        # Because P was substituted in Class 1, P is available to teach Class 2!
        c2 = LiveClass.objects.create(
            course=other_course,
            instructor=self.primary_inst,
            title="Class 2 P Teaching Elsewhere",
            scheduled_start=start_time,
            duration_minutes=60,
            meeting_url="https://zoom.us/j/12345678903",
            status=LiveClass.ClassStatus.SCHEDULED
        )
        self.assertIsNotNone(c2.id)

    def test_substitute_double_booking_blocked(self):
        """Defect 2: Substitute instructor cannot be double-booked across two substituted classes."""
        start_time = timezone.now() + datetime.timedelta(days=24)
        p2 = User.objects.create_user(
            username=f"prim2_{uuid.uuid4().hex[:8]}",
            email=f"prim2_{uuid.uuid4().hex[:8]}@example.com",
            password="testpassword123",
            is_staff=True
        )
        batch2 = LiveBatch.objects.create(
            course=self.course,
            instructor=p2,
            batch_type=LiveBatch.BatchType.GROUP
        )

        # Class 1 on Batch 1, substituted by S
        LiveClass.objects.create(
            course=self.course,
            batch=self.batch,
            title="Batch 1 Sub S",
            scheduled_start=start_time,
            duration_minutes=60,
            substitute_instructor=self.sub_inst,
            meeting_url="https://zoom.us/j/12345678904",
            status=LiveClass.ClassStatus.SCHEDULED
        )

        # Class 2 on Batch 2, also substituted by S at overlapping time -> MUST FAIL
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                LiveClass.objects.create(
                    course=self.course,
                    batch=batch2,
                    title="Batch 2 Sub S Overlap",
                    scheduled_start=start_time + datetime.timedelta(minutes=30),
                    duration_minutes=60,
                    substitute_instructor=self.sub_inst,
                    meeting_url="https://zoom.us/j/12345678905",
                    status=LiveClass.ClassStatus.SCHEDULED
                )

    def test_substitute_removal_triggers_overlap_if_primary_conflict(self):
        """Defect 2: Removing substitute restores primary instructor calendar check in DB."""
        start_time = timezone.now() + datetime.timedelta(days=25)
        # Class 1: Primary=P, Sub=S at start_time
        c1 = LiveClass.objects.create(
            course=self.course,
            batch=self.batch,
            title="Class 1 P Sub S",
            scheduled_start=start_time,
            duration_minutes=60,
            substitute_instructor=self.sub_inst,
            meeting_url="https://zoom.us/j/12345678906",
            status=LiveClass.ClassStatus.SCHEDULED
        )

        # Class 2: Primary=P teaches another class at start_time (allowed because c1 is substituted)
        other_course = Course.objects.create(title="Other Course P 2", price=50.0)
        LiveClass.objects.create(
            course=other_course,
            instructor=self.primary_inst,
            title="Class 2 P Active",
            scheduled_start=start_time,
            duration_minutes=60,
            meeting_url="https://zoom.us/j/12345678907",
            status=LiveClass.ClassStatus.SCHEDULED
        )

        # Attempt to remove substitute from c1 -> P becomes effective instructor on c1, conflicting with c2!
        c1.substitute_instructor = None
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                c1.save()

    def test_concurrent_idempotency_key_creation(self):
        """Defect 6: Multi-threaded concurrent inserts with identical idempotency_key."""
        key = f"concurrent-key-{uuid.uuid4().hex}"
        results = []

        def create_op(thread_idx):
            from django.db import connection
            try:
                LiveClassDurableOperation.objects.create(
                    idempotency_key=key,
                    operation_type=LiveClassDurableOperation.OperationType.ZOOM_PROVISION,
                    status=LiveClassDurableOperation.Status.IN_PROGRESS,
                    payload={"thread": thread_idx}
                )
                return "SUCCESS"
            except IntegrityError:
                return "INTEGRITY_ERROR"
            finally:
                connection.close()

        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(create_op, i) for i in range(5)]
            results = [f.result() for f in futures]

        self.assertEqual(results.count("SUCCESS"), 1)
        self.assertEqual(results.count("INTEGRITY_ERROR"), 4)

    def test_concurrent_substitute_assignment(self):
        """Defect 6: Concurrent threads attempting to assign substitute to overlapping classes."""
        start_time = timezone.now() + datetime.timedelta(days=26)
        p2 = User.objects.create_user(
            username=f"prim_other_{uuid.uuid4().hex[:8]}",
            email=f"prim_other_{uuid.uuid4().hex[:8]}@example.com",
            password="testpassword123",
            is_staff=True
        )
        c1 = LiveClass.objects.create(
            course=self.course,
            instructor=self.primary_inst,
            title="Class A",
            meeting_url="https://zoom.us/j/12345678911",
            scheduled_start=start_time,
            duration_minutes=60,
            status=LiveClass.ClassStatus.SCHEDULED
        )
        other_course = Course.objects.create(title="Course B", price=40.0)
        c2 = LiveClass.objects.create(
            course=other_course,
            instructor=p2,
            title="Class B",
            meeting_url="https://zoom.us/j/12345678912",
            scheduled_start=start_time + datetime.timedelta(minutes=15),
            duration_minutes=60,
            status=LiveClass.ClassStatus.SCHEDULED
        )

        def assign_sub(class_id):
            from django.db import connection, transaction
            try:
                with transaction.atomic():
                    target = LiveClass.objects.select_for_update().get(id=class_id)
                    target.substitute_instructor = self.sub_inst
                    target.save()
                return "SUCCESS"
            except IntegrityError:
                return "INTEGRITY_ERROR"
            finally:
                connection.close()

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            f1 = executor.submit(assign_sub, c1.id)
            f2 = executor.submit(assign_sub, c2.id)
            results = [f1.result(), f2.result()]

        self.assertEqual(results.count("SUCCESS"), 1)
        self.assertEqual(results.count("INTEGRITY_ERROR"), 1)

    def test_worker_lease_expiration_and_fencing(self):
        """Defect 6: Worker lease expiration, takeover, and fencing protection."""
        zmr = ZoomMeetingRecord.objects.create(
            zoom_meeting_id="99911122233",
            topic="Lease Test",
            state=ZoomMeetingRecord.State.RESERVED,
            lease_worker=uuid.uuid4(),
            lease_expires_at=timezone.now() - datetime.timedelta(seconds=10),  # Expired
            version=1
        )

        worker_b = uuid.uuid4()
        now = timezone.now()

        # Worker B reclaims expired lease using optimistic versioning
        reclaimed_count = ZoomMeetingRecord.objects.filter(
            id=zmr.id,
            version=1,
            lease_expires_at__lt=now
        ).update(
            lease_worker=worker_b,
            lease_expires_at=now + datetime.timedelta(seconds=60),
            version=models.F('version') + 1
        )
        self.assertEqual(reclaimed_count, 1)

        zmr.refresh_from_db()
        self.assertEqual(zmr.lease_worker, worker_b)
        self.assertEqual(zmr.version, 2)

        # Worker A attempts stale update using old version=1 -> rows affected = 0
        stale_count = ZoomMeetingRecord.objects.filter(
            id=zmr.id,
            version=1
        ).update(
            topic="Stale Worker A Overwrite"
        )
        self.assertEqual(stale_count, 0)
        zmr.refresh_from_db()
        self.assertEqual(zmr.topic, "Lease Test")  # Preserved!

    def test_migration_rollback_prohibited(self):
        """Defect 5: Migration 0035 reverse migration raises IrreversibleError."""
        with self.assertRaises(IrreversibleError) as ctx:
            migration_0035.reverse_preflight_and_backfill(None, None)
        self.assertIn("explicitly irreversible", str(ctx.exception))

