from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.db import models

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
    profile_image = models.ImageField(upload_to='profiles/teachers/', blank=True, null=True)
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
    profile_image = models.ImageField(upload_to='profiles/mentors/', blank=True, null=True)
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
    # Can be an email or a phone number
    identifier = models.CharField(max_length=255)
    otp = models.CharField(max_length=6)
    created_at = models.DateTimeField(auto_now_add=True)
    is_verified = models.BooleanField(default=False)

    def __str__(self):
        return f"{self.identifier} - {self.otp}"
