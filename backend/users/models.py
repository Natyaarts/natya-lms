from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.core.validators import FileExtensionValidator
from django.db import models

from .validators import ALLOWED_PROFILE_IMAGE_EXTENSIONS, validate_profile_image_size

class User(AbstractUser):
    # Roles
    is_teacher = models.BooleanField(default=False)
    is_student = models.BooleanField(default=True)
    is_mentor = models.BooleanField(
        default=False,
        help_text="Distinct from is_teacher. A mentor is assigned to students/courses/live "
                  "classes (see Mentorship, CourseInstructor) but does not automatically "
                  "receive teacher-level course-editing or admin/financial permissions."
    )
    
    # Onboarding
    is_onboarded = models.BooleanField(default=False)
    onboarding_data = models.JSONField(default=dict, blank=True)
    
    phone_number = models.CharField(max_length=15, blank=True, null=True)
    parent_name = models.CharField(max_length=255, blank=True, null=True)
    parent_phone = models.CharField(max_length=15, blank=True, null=True)

    def __str__(self):
        return self.username

class OnboardingField(models.Model):
    FIELD_TYPES = (
        ('text', 'Short Text'),
        ('textarea', 'Long Text'),
        ('date', 'Date Picker'),
        ('dropdown', 'Dropdown Select'),
        ('checkbox', 'Checkbox (True/False)')
    )
    
    name = models.CharField(max_length=50, help_text="Variable name (e.g. dob, address). No spaces.")
    label = models.CharField(max_length=100, help_text="Display label (e.g. Date of Birth)")
    field_type = models.CharField(max_length=20, choices=FIELD_TYPES, default='text')
    is_required = models.BooleanField(default=True)
    options = models.JSONField(blank=True, null=True, help_text="JSON list of options for dropdowns, e.g. [\"Male\", \"Female\"]")
    order = models.IntegerField(default=0)
    
    class Meta:
        ordering = ['order']
        
    def __str__(self):
        return f"{self.label} ({self.get_field_type_display()})"

class Mentorship(models.Model):
    """
    Explicit, persistent student <-> mentor relationship. Deliberately NOT
    derived from course Enrollment (unlike the legacy teacher<->student
    inference) -- a mentor relationship should survive independent of which
    courses a student happens to be enrolled in at any given moment.

    - One student may have multiple mentors (multiple rows, one per mentor).
    - One mentor may have multiple students (multiple rows, one per student).
    - Reassignment preserves history: set the old row INACTIVE rather than
      deleting it, then create a new ACTIVE row. The partial unique
      constraint below only guards against two simultaneously-ACTIVE rows
      for the same (student, mentor) pair.
    """
    class Status(models.TextChoices):
        ACTIVE = "ACTIVE", "Active"
        INACTIVE = "INACTIVE", "Inactive"

    student = models.ForeignKey(
        settings.AUTH_USER_MODEL, related_name='mentorships_as_student', on_delete=models.CASCADE
    )
    mentor = models.ForeignKey(
        settings.AUTH_USER_MODEL, related_name='mentorships_as_mentor', on_delete=models.CASCADE
    )
    assigned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, related_name='mentorships_assigned', on_delete=models.SET_NULL,
        null=True, blank=True, help_text="Admin/staff user who created this assignment."
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True)
    assigned_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-assigned_at']
        constraints = [
            models.UniqueConstraint(
                fields=['student', 'mentor'],
                condition=models.Q(status='ACTIVE'),
                name='unique_active_mentorship'
            )
        ]

    def __str__(self):
        return f"{self.mentor.username} mentors {self.student.username} ({self.status})"


class TeacherProfile(models.Model):
    """
    Professional/public-facing information for a teacher, kept separate from
    User (which stays purely identity/auth). One row per teacher, created
    lazily (get_or_create) the first time it's needed rather than at
    User-creation time, so existing teacher accounts are never broken by
    this model's introduction.
    """
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, related_name='teacher_profile', on_delete=models.CASCADE
    )
    bio = models.TextField(blank=True)
    # Phase 3.9: previously an unvalidated ImageField -- an admin/teacher
    # could upload an arbitrarily large file, or (Pillow permitting) an
    # unexpected image format. FileExtensionValidator whitelists the
    # extension; validate_profile_image_size caps the upload at 5MB (see
    # users/validators.py). Existing already-stored images are entirely
    # unaffected -- validators only run on a NEW upload going through a
    # form/serializer's full_clean(), never retroactively against rows
    # already in the database.
    profile_image = models.ImageField(
        upload_to='profiles/teachers/', blank=True, null=True,
        validators=[FileExtensionValidator(allowed_extensions=ALLOWED_PROFILE_IMAGE_EXTENSIONS), validate_profile_image_size],
    )
    specialization = models.CharField(max_length=255, blank=True)
    qualifications = models.TextField(blank=True)
    experience_years = models.PositiveIntegerField(null=True, blank=True)
    languages = models.JSONField(default=list, blank=True, help_text='List of language names, e.g. ["English", "Malayalam"]')
    short_intro = models.CharField(max_length=500, blank=True)
    is_public = models.BooleanField(default=True, help_text="Visible on a public teacher profile page (future use).")
    is_active = models.BooleanField(default=True, help_text="Admin can deactivate the profile without touching the User account.")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Teacher Profile: {self.user.username}"


class MentorProfile(models.Model):
    """
    Professional/public-facing information for a mentor -- deliberately a
    separate model from TeacherProfile (not a shared "InstructorProfile"),
    matching how Mentor is already a distinct role from Teacher throughout
    this codebase (separate booleans, separate CourseInstructor role,
    separate permissions).
    """
    class AvailabilityStatus(models.TextChoices):
        AVAILABLE = "AVAILABLE", "Available"
        BUSY = "BUSY", "Busy"
        UNAVAILABLE = "UNAVAILABLE", "Unavailable"

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, related_name='mentor_profile', on_delete=models.CASCADE
    )
    bio = models.TextField(blank=True)
    # Phase 3.9: see TeacherProfile.profile_image's identical comment above.
    profile_image = models.ImageField(
        upload_to='profiles/mentors/', blank=True, null=True,
        validators=[FileExtensionValidator(allowed_extensions=ALLOWED_PROFILE_IMAGE_EXTENSIONS), validate_profile_image_size],
    )
    specialization = models.CharField(max_length=255, blank=True)
    qualifications = models.TextField(blank=True)
    experience_years = models.PositiveIntegerField(null=True, blank=True)
    languages = models.JSONField(default=list, blank=True, help_text='List of language names, e.g. ["English", "Tamil"]')
    availability_status = models.CharField(
        max_length=20, choices=AvailabilityStatus.choices, default=AvailabilityStatus.AVAILABLE
    )
    social_links = models.JSONField(default=dict, blank=True, help_text='e.g. {"instagram": "https://..."}')
    is_public = models.BooleanField(default=True, help_text="Visible on a public mentor profile page (future use).")
    is_active = models.BooleanField(default=True, help_text="Admin can deactivate the profile without touching the User account.")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Mentor Profile: {self.user.username}"


class OTPVerification(models.Model):
    class Purpose(models.TextChoices):
        LOGIN = 'LOGIN', 'Login'
        ACCOUNT_DELETION = 'ACCOUNT_DELETION', 'Account Deletion'

    # Can be an email or a phone number
    identifier = models.CharField(max_length=255)
    otp = models.CharField(max_length=6)
    purpose = models.CharField(
        max_length=30,
        choices=Purpose.choices,
        default=Purpose.LOGIN,
        db_index=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    is_verified = models.BooleanField(default=False)
    # Phase 3.9: brute-force protection. Incremented on every WRONG
    # verification attempt against this specific OTP record (see
    # VerifyOTPView) -- once it reaches MAX_VERIFY_ATTEMPTS, this record
    # can never be verified again (even with the correct code), forcing a
    # fresh OTP request instead of allowing unlimited guesses of a 6-digit
    # code within its validity window.
    attempts = models.PositiveSmallIntegerField(default=0)

    MAX_VERIFY_ATTEMPTS = 5

    def __str__(self):
        return f"{self.identifier} ({self.purpose}) - {self.otp}"


class AccountDeletionRequest(models.Model):
    """
    Stage 2D Step 2B: Durable model tracking an authenticated user's account deletion request.
    Provides idempotent tracking across re-authentication, confirmation, and the staged
    cleanup/anonymization workflows executed in subsequent phases.
    Never stores OTP codes or passwords.
    """
    class Status(models.TextChoices):
        PENDING = 'PENDING', 'Pending'          # Request verified via OTP, queued for execution
        PROCESSING = 'PROCESSING', 'Processing'    # Execution in progress (Razorpay, S3, data cleanup)
        COMPLETED = 'COMPLETED', 'Completed'      # Anonymization and cleanup finished
        FAILED = 'FAILED', 'Failed'             # Execution failed, eligible for retry
        CANCELLED = 'CANCELLED', 'Cancelled'       # Request cancelled by user or admin

    TERMINAL_STATUSES = (Status.COMPLETED, Status.FAILED, Status.CANCELLED)

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name='deletion_requests',
        on_delete=models.CASCADE,
        help_text="User requesting deletion."
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True
    )
    reason = models.TextField(blank=True, help_text="Optional user-provided reason for deleting account.")
    error_message = models.TextField(blank=True, help_text="Failure reason if status=FAILED.")
    cleanup_log = models.JSONField(
        default=dict,
        blank=True,
        help_text="Structured audit log of staged cleanup actions (e.g., razorpay_cancelled, s3_files_deleted)."
    )

    confirmed_at = models.DateTimeField(null=True, blank=True, help_text="Timestamp when deletion OTP was successfully verified.")
    completed_at = models.DateTimeField(null=True, blank=True, help_text="Timestamp when anonymization/cleanup completed.")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(
                fields=['user'],
                condition=~models.Q(status__in=['COMPLETED', 'FAILED', 'CANCELLED']),
                name='unique_active_deletion_request_per_user',
            ),
        ]

    def __str__(self):
        return f"DeletionRequest #{self.id} for user {self.user_id} ({self.status})"



class AdminAuditLog(models.Model):
    """
    Phase 3.9. A minimal, append-only audit trail for security-sensitive
    admin actions -- privilege/role changes, course-instructor assignment,
    refund actions, and other important admin-financial actions. Lives in
    `users` (already the app that owns roles/permissions) rather than a
    new dedicated app, to avoid introducing a seventh Django app for a
    single small model.

    Deliberately minimal: this is NOT a general-purpose activity feed and
    does not attempt to log every request. Callers explicitly call
    AdminAuditLog.record(...) at the specific action points listed in the
    Phase 3.9 report. `metadata` must never contain a secret/credential/
    raw payment-processor payload -- only already-non-sensitive
    identifiers and before/after values of the kind already shown
    elsewhere in this app's own admin/API responses.

    Read-only via Django admin (see users/admin.py) for the same reason
    every financial-record admin in this codebase is read-only: a
    hand-edited or hand-deleted audit row would defeat its own purpose.
    """
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, related_name='admin_audit_logs', on_delete=models.SET_NULL,
        null=True, blank=True, help_text="Who performed the action. Null only if the actor's account was later deleted.",
    )
    action = models.CharField(max_length=100, db_index=True, help_text="Short machine-readable action code, e.g. ROLE_CHANGE, COURSE_INSTRUCTOR_ASSIGNED.")
    target_type = models.CharField(max_length=100, blank=True, help_text="e.g. 'User', 'CourseInstructor', 'Refund', 'Payout'.")
    target_id = models.CharField(max_length=64, blank=True)
    description = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True, help_text="Non-sensitive structured detail only -- never a secret/credential/raw payment payload.")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['target_type', 'target_id']),
        ]

    def __str__(self):
        return f"[{self.created_at:%Y-%m-%d %H:%M}] {self.action} by {self.actor_id} on {self.target_type}#{self.target_id}"

    @classmethod
    def record(cls, *, actor, action, target_type='', target_id='', description='', metadata=None):
        """
        The single write path for this model -- every audit-log call site
        in the codebase goes through this, never a direct .objects.create().
        Never raises back to its caller: an audit-logging failure must
        never block or roll back the real action it's recording (the same
        "observability must never break the thing it observes" principle
        already established for NotificationService/finance ledger
        creation elsewhere in this codebase).
        """
        import logging
        logger = logging.getLogger('users.audit')
        try:
            return cls.objects.create(
                actor=actor, action=action, target_type=target_type, target_id=str(target_id),
                description=description, metadata=metadata or {},
            )
        except Exception:
            logger.error("AdminAuditLog.record failed for action=%s target=%s#%s", action, target_type, target_id, exc_info=True)
            return None
