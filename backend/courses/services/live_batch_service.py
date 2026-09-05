from django.db import transaction
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.contrib.auth import get_user_model
from courses.models import Course, Enrollment, LiveBatch, LiveBatchStudent
from orders.models import Purchase

User = get_user_model()

class LiveBatchService:
    @staticmethod
    def assign_student(batch_id, student_id, purchase_id=None, request_user=None):
        """
        Assigns a student to a LiveBatch transaction-safely.
        Enforces batch capacity, duplicate checks, purchase validations, and enrollment sync.
        """
        with transaction.atomic():
            # 1. Lock the batch row using select_for_update()
            try:
                batch = LiveBatch.objects.select_for_update().get(pk=batch_id)
            except LiveBatch.DoesNotExist:
                raise ValidationError("LiveBatch does not exist.")

            # 2. Validate student user
            try:
                student = User.objects.get(pk=student_id)
            except User.DoesNotExist:
                raise ValidationError("Student does not exist.")

            if not student.is_active:
                raise ValidationError("Cannot assign an inactive student.")

            if getattr(student, 'is_teacher', False) and not student.is_superuser:
                raise ValidationError("Cannot assign a teacher as a student to a batch.")

            # 3. Validate capacity: ONE_TO_ONE is always capped at 1; a GROUP
            # batch respects its own max_participants if one is set (Phase 2).
            if batch.batch_type == LiveBatch.BatchType.ONE_TO_ONE:
                existing_count = LiveBatchStudent.objects.filter(batch=batch).exclude(student=student).count()
                if existing_count >= 1:
                    raise ValidationError("This ONE_TO_ONE batch is already assigned to a student.")
            elif batch.max_participants is not None:
                existing_count = LiveBatchStudent.objects.filter(batch=batch).exclude(student=student).count()
                if existing_count >= batch.max_participants:
                    raise ValidationError(f"This batch is full (max {batch.max_participants} participants).")

            # 4. Perform purchase validation
            purchase = None
            if purchase_id:
                try:
                    purchase = Purchase.objects.get(pk=purchase_id)
                except Purchase.DoesNotExist:
                    raise ValidationError("Purchase record does not exist.")

                if purchase.user != student:
                    raise ValidationError("Purchase user does not match the assigned student.")
                if purchase.course != batch.course:
                    raise ValidationError("Purchase course does not match the batch course.")
                if purchase.status != 'SUCCESS':
                    raise ValidationError("Purchase status must be SUCCESS.")
            else:
                # Paid course requires purchase, unless administrative override or free course
                is_admin = request_user and (request_user.is_superuser or request_user.is_staff)
                if batch.course.price > 0 and not is_admin:
                    raise ValidationError("Payment/Purchase is required to enroll in this paid course.")

            # 5. Check duplicate/idempotency
            assignment, created = LiveBatchStudent.objects.get_or_create(
                batch=batch,
                student=student,
                defaults={'purchase': purchase}
            )

            # 6. Enrollment Synchronization
            # Verify or create course-level Enrollment record
            Enrollment.objects.get_or_create(user=student, course=batch.course)

            return assignment, created
