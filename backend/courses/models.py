import uuid
from decimal import Decimal

from django.db import models
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator, MinValueValidator, MaxValueValidator
from users.models import User

from .validators import ALLOWED_SUBMISSION_FILE_EXTENSIONS, validate_submission_file_size

class Course(models.Model):
    class CourseType(models.TextChoices):
        LIVE = "LIVE", "Live"
        RECORDED = "RECORDED", "Recorded"

    title = models.CharField(max_length=255)
    description = models.TextField()
    price = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    thumbnail = models.ImageField(upload_to='course_thumbnails/', blank=True, null=True)
    is_published = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    course_type = models.CharField(
        max_length=20,
        choices=CourseType.choices,
        default=CourseType.RECORDED,
        db_index=True
    )

    def __str__(self):
        return self.title

class Module(models.Model):
    course = models.ForeignKey(Course, related_name='modules', on_delete=models.CASCADE)
    title = models.CharField(max_length=255)
    order = models.PositiveIntegerField(default=0)
    
    class Meta:
        ordering = ['order']

    def __str__(self):
        return f"{self.course.title} - {self.title}"

class VideoLesson(models.Model):
    module = models.ForeignKey(Module, related_name='lessons', on_delete=models.CASCADE)
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    transcript = models.TextField(blank=True, help_text="Paste the English transcript here. AI will use this to generate dubbed audio tracks.")
    timed_transcript = models.TextField(
        blank=True,
        help_text="Manually timed transcript for perfect dubbing sync. Each line: HH:MM:SS --> Text spoken at that time. Example: 00:00:05 --> Hello and welcome to this class"
    )
    video_file = models.FileField(upload_to='videos/lessons/', blank=True, null=True, help_text="Upload the original MP4 video file")
    
    # Audio tracks will be stored in TranslatedAudio model

    order = models.PositiveIntegerField(default=0)
    
    class Meta:
        ordering = ['order']

    def __str__(self):
        return self.title

class CourseInstructor(models.Model):
    """
    Real, explicit ownership/assignment relationship between a Course and a
    User who teaches/mentors it -- distinct from a student Enrollment.

    Phase 0 note: this model is introduced additively and is NOT yet wired
    into permissions, CourseViewSet.get_queryset, or teacher_students -- those
    still use the legacy "teacher enrolled in their own course" inference for
    now. This model exists so a data migration can capture today's real
    course/teacher relationships ahead of that rewiring (Phase 1), without
    touching any existing behavior yet. Supports multiple instructors per
    course (e.g. a Mentor alongside a Teacher) and an is_primary flag as a
    forward-looking hook for revenue/payout attribution.
    """
    class InstructorRole(models.TextChoices):
        TEACHER = "TEACHER", "Teacher"
        MENTOR = "MENTOR", "Mentor"
        ASSISTANT = "ASSISTANT", "Assistant"

    course = models.ForeignKey(Course, related_name='instructors', on_delete=models.CASCADE)
    user = models.ForeignKey(User, related_name='course_instructor_roles', on_delete=models.CASCADE)
    role = models.CharField(max_length=20, choices=InstructorRole.choices, default=InstructorRole.TEACHER)
    is_primary = models.BooleanField(default=False, help_text="Primary instructor for revenue/payout attribution (future use).")
    # Phase 3.5.2: percentage commission override for this specific
    # instructor/course, e.g. 30.00 = 30%. null = no rate configured yet --
    # the finance ledger (finance/services.py) treats a null rate as a
    # safe no-op (no LedgerEntry created), never a guessed/hardcoded
    # default, since no platform-wide default commission rate has been
    # decided (see the Phase 3.5.1 audit's Risks section). Admin-editable
    # via CourseInstructorAdmin -- this is a configuration input, not a
    # financial transaction record, so it does not get the read-only
    # treatment LedgerEntry/Payout get.
    commission_rate = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('course', 'user', 'role')
        ordering = ['-is_primary', 'created_at']

    def __str__(self):
        return f"{self.user.username} - {self.course.title} ({self.get_role_display()})"


class Bundle(models.Model):
    """
    Phase 3.3: a sellable catalog item made of multiple existing Courses,
    sold as one line item through the new Order/OrderItem system (see
    orders/models.py). Deliberately catalog data, not transactional data --
    lives alongside Course, not in the orders app -- mirroring the existing
    split where Purchase (a transaction) references Course (a catalog item)
    across the same app boundary.

    Uses a plain ManyToManyField for `courses`: Django's auto-generated
    through table already enforces a unique (bundle, course) pair at the DB
    level, so "prevent duplicate courses inside a bundle" needs no extra
    code -- `bundle.courses.add(x)` twice is naturally a no-op, not a
    duplicate row.
    """
    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255, unique=True, blank=True)
    description = models.TextField(blank=True)
    courses = models.ManyToManyField(Course, related_name='bundles', blank=True)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=3, default='INR')
    thumbnail = models.ImageField(upload_to='bundle_thumbnails/', blank=True, null=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            from django.utils.text import slugify
            base_slug = slugify(self.name) or 'bundle'
            slug = base_slug
            suffix = 1
            while Bundle.objects.filter(slug=slug).exclude(pk=self.pk).exists():
                suffix += 1
                slug = f"{base_slug}-{suffix}"
            self.slug = slug
        super().save(*args, **kwargs)

    @property
    def is_purchasable(self):
        """
        A Bundle can be visible to admins/browsable while still being
        assembled, but must never be BUYABLE unless it's active AND every
        course inside it is actually published -- otherwise a student could
        pay for access to a course that isn't publicly available yet. This
        is the single enforcement point OrderViewSet.create() checks;
        listing/detail views deliberately do NOT hide a non-purchasable
        bundle, they just mark it as such (so admins can still see/edit it
        and a storefront can show "coming soon").
        """
        if not self.is_active:
            return False
        course_list = list(self.courses.all())
        if not course_list:
            return False
        return all(c.is_published for c in course_list)


class Enrollment(models.Model):
    user = models.ForeignKey(User, related_name='enrollments', on_delete=models.CASCADE)
    course = models.ForeignKey(Course, related_name='enrollments', on_delete=models.CASCADE)
    enrolled_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('user', 'course')

    def __str__(self):
        return f"{self.user.username} enrolled in {self.course.title}"

class TranslatedAudio(models.Model):
    """
    An alternate audio track for a lesson, in a language other than the
    original (English) video audio. The track can come from anywhere --
    the AI dubbing pipeline (courses/services/ai_translator.py), a human
    voice artist, a studio, or any external service. The LMS only stores
    and serves the resulting file; it does not care how it was produced.
    """
    lesson = models.ForeignKey(VideoLesson, related_name='translated_audios', on_delete=models.CASCADE)
    language_code = models.CharField(max_length=10, help_text="e.g. ml-IN, ta-IN, hi-IN, es-ES")
    language_name = models.CharField(max_length=100, blank=True, default='', help_text="Display name, e.g. 'Malayalam'")
    audio_file = models.FileField(upload_to='videos/audios/')
    status = models.CharField(max_length=20, default='processing') # 'processing', 'completed', 'failed'
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('lesson', 'language_code')

    def __str__(self):
        return f"{self.lesson.title} - {self.language_code}"


class LessonProgress(models.Model):
    user = models.ForeignKey(User, related_name='lesson_progress', on_delete=models.CASCADE)
    lesson = models.ForeignKey(VideoLesson, related_name='progress_records', on_delete=models.CASCADE)

    last_watched_position = models.FloatField(default=0.0, help_text="Latest playback position in seconds")
    video_duration = models.FloatField(default=0.0, help_text="Total lesson video duration in seconds")
    completed = models.BooleanField(default=False)

    updated_at = models.DateTimeField(auto_now=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['user', 'lesson'], name='unique_user_lesson_progress')
        ]
        verbose_name_plural = "Lesson Progress Records"
        ordering = ['-updated_at']

    @property
    def progress_percentage(self):
        if self.video_duration > 0:
            return min(100.0, (self.last_watched_position / self.video_duration) * 100.0)
        return 0.0

    def __str__(self):
        return f"{self.user.username} - {self.lesson.title} ({self.progress_percentage:.1f}%)"


class RecurrenceRule(models.Model):
    """
    Phase 2: describes a recurring LiveClass series. One RecurrenceRule is
    shared by every LiveClass occurrence it generates (LiveClass.recurrence_rule
    FK) -- this is the "proper recurring-series relationship" rather than a
    set of unrelated duplicated rows: editing/cancelling "the whole series"
    means bulk-operating over every still-SCHEDULED LiveClass sharing this
    rule; editing/cancelling "one occurrence" means touching that single
    LiveClass row directly, exactly like the existing reschedule/cancel
    actions already do -- unchanged.

    Occurrences are generated eagerly (not via a rolling Celery-beat window)
    at creation time, capped at MAX_OCCURRENCES, to keep this additive and
    operationally simple rather than introducing new scheduled-job infra.
    """
    class Frequency(models.TextChoices):
        ONE_TIME = "ONE_TIME", "One-time"
        DAILY = "DAILY", "Daily"
        WEEKLY = "WEEKLY", "Weekly"

    MAX_OCCURRENCES = 52

    frequency = models.CharField(max_length=20, choices=Frequency.choices, default=Frequency.ONE_TIME)
    weekdays = models.JSONField(
        default=list, blank=True,
        help_text="For WEEKLY frequency: list of weekday ints, Monday=0..Sunday=6 (Python weekday()). Ignored otherwise."
    )
    end_date = models.DateField(null=True, blank=True, help_text="Last date an occurrence may fall on. Mutually exclusive with occurrence_count in practice, but either/both may be set as a safety bound.")
    occurrence_count = models.PositiveIntegerField(null=True, blank=True, help_text="Max number of occurrences to generate.")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='created_recurrence_rules')
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.get_frequency_display()} recurrence ({self.id})"


class LiveClass(models.Model):
    class MeetingProvider(models.TextChoices):
        ZOOM = "ZOOM", "Zoom"
        GOOGLE_MEET = "GOOGLE_MEET", "Google Meet"
        TEAMS = "TEAMS", "Teams"
        OTHER = "OTHER", "Other"

    class ClassStatus(models.TextChoices):
        # "ongoing" from the Phase 2 spec maps to the existing LIVE value --
        # kept as-is rather than renamed, to avoid breaking every existing
        # consumer (tests, admin display, API clients) of this choice.
        # "Rescheduled" is deliberately NOT a persisted status here: it's
        # represented as an event (the existing `reschedule` action fires a
        # notification and updates scheduled_start) rather than a terminal
        # state, so a class can be rescheduled more than once -- making it a
        # status would trip the "only SCHEDULED classes can be rescheduled"
        # guard after the very first reschedule.
        SCHEDULED = "SCHEDULED", "Scheduled"
        LIVE = "LIVE", "Live"
        COMPLETED = "COMPLETED", "Completed"
        CANCELLED = "CANCELLED", "Cancelled"

    course = models.ForeignKey(
        Course,
        on_delete=models.CASCADE,
        related_name="live_classes",
        db_index=True
    )
    instructor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="conducted_live_classes",
        db_index=True
    )
    batch = models.ForeignKey(
        'LiveBatch',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="live_classes",
        db_index=True
    )
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    scheduled_start = models.DateTimeField(db_index=True)
    duration_minutes = models.PositiveIntegerField()
    meeting_provider = models.CharField(
        max_length=20,
        choices=MeetingProvider.choices,
        default=MeetingProvider.OTHER
    )
    meeting_url = models.URLField(max_length=1000)
    status = models.CharField(
        max_length=20,
        choices=ClassStatus.choices,
        default=ClassStatus.SCHEDULED,
        db_index=True
    )
    cancellation_reason = models.TextField(blank=True)
    recording_url = models.URLField(max_length=1000, blank=True)
    recording_uploaded_at = models.DateTimeField(null=True, blank=True)
    recurrence_rule = models.ForeignKey(
        RecurrenceRule, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='occurrences',
        help_text="Set when this class was generated as part of a recurring series."
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["scheduled_start"]
        verbose_name_plural = "Live Classes"

    def clean(self):
        super().clean()
        if self.duration_minutes is not None and self.duration_minutes <= 0:
            raise ValidationError({"duration_minutes": "Duration must be greater than 0."})
        if self.batch:
            if self.course and self.course != self.batch.course:
                raise ValidationError({"course": "LiveClass course must match batch course."})
            if self.instructor and self.instructor != self.batch.instructor:
                raise ValidationError({"instructor": "LiveClass instructor must match batch instructor."})

    def save(self, *args, **kwargs):
        if self.batch:
            self.course = self.batch.course
            self.instructor = self.batch.instructor
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.title} - {self.course.title} ({self.scheduled_start})"


class LiveBatch(models.Model):
    class BatchType(models.TextChoices):
        ONE_TO_ONE = "ONE_TO_ONE", "One-to-One"
        GROUP = "GROUP", "Group"

    course = models.ForeignKey(Course, related_name='live_batches', on_delete=models.CASCADE, db_index=True)
    instructor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="conducted_live_batches",
        db_index=True
    )
    batch_type = models.CharField(
        max_length=20,
        choices=BatchType.choices,
        db_index=True
    )
    max_participants = models.PositiveIntegerField(
        null=True, blank=True,
        help_text="Only meaningful for GROUP batches. Leave blank for no cap. ONE_TO_ONE is always capped at 1 regardless of this field."
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name_plural = "Live Batches"

    def clean(self):
        super().clean()
        if self.course and self.course.course_type != Course.CourseType.LIVE:
            raise ValidationError("A LiveBatch can only be created for a Course with type LIVE.")
        if self.instructor and not (
            self.instructor.is_superuser or self.instructor.is_staff
            or getattr(self.instructor, 'is_teacher', False)
            or getattr(self.instructor, 'is_mentor', False)
        ):
            raise ValidationError("The instructor must be a teacher, mentor, or administrator.")
        if self.batch_type == self.BatchType.ONE_TO_ONE and self.max_participants not in (None, 1):
            raise ValidationError({"max_participants": "A ONE_TO_ONE batch is always capped at 1 -- leave this blank."})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.get_batch_type_display()} Batch for {self.course.title} (Instructor: {self.instructor.username if self.instructor else 'None'})"


class LiveBatchStudent(models.Model):
    batch = models.ForeignKey(LiveBatch, related_name='students', on_delete=models.CASCADE, db_index=True)
    student = models.ForeignKey(settings.AUTH_USER_MODEL, related_name='live_batch_assignments', on_delete=models.CASCADE, db_index=True)
    purchase = models.ForeignKey(
        'orders.Purchase',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='live_batch_assignments',
        db_index=True
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('batch', 'student')
        ordering = ["-created_at"]
        verbose_name_plural = "Live Batch Students"

    def clean(self):
        super().clean()
        if self.batch and self.batch.batch_type == LiveBatch.BatchType.ONE_TO_ONE:
            existing_assignments = LiveBatchStudent.objects.filter(batch=self.batch)
            if self.pk:
                existing_assignments = existing_assignments.exclude(pk=self.pk)
            if existing_assignments.exists():
                raise ValidationError("A ONE_TO_ONE batch can have at most one student.")

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.student.username} assigned to batch {self.batch.id}"


class TeacherAvailability(models.Model):
    """
    Phase 2: a teacher/mentor's weekly availability windows, used to extend
    LiveClassSerializer's existing conflict-detection. Multiple rows for the
    same day naturally express a break (e.g. 09:00-12:00 and 13:00-17:00).
    Deliberately opt-in: if a user has zero rows, no availability
    restriction is enforced for them (backward compatible -- existing
    scheduling behavior for every instructor who predates this feature is
    unchanged).
    """
    class Weekday(models.IntegerChoices):
        MONDAY = 0, "Monday"
        TUESDAY = 1, "Tuesday"
        WEDNESDAY = 2, "Wednesday"
        THURSDAY = 3, "Thursday"
        FRIDAY = 4, "Friday"
        SATURDAY = 5, "Saturday"
        SUNDAY = 6, "Sunday"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, related_name='availability_windows', on_delete=models.CASCADE, db_index=True)
    day_of_week = models.IntegerField(choices=Weekday.choices)
    start_time = models.TimeField()
    end_time = models.TimeField()
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['day_of_week', 'start_time']
        verbose_name_plural = "Teacher Availability Windows"

    def clean(self):
        super().clean()
        if self.user and not (
            getattr(self.user, 'is_teacher', False) or getattr(self.user, 'is_mentor', False)
            or self.user.is_staff or self.user.is_superuser
        ):
            raise ValidationError("Only teacher, mentor, or admin accounts can set availability.")
        if self.start_time and self.end_time and self.start_time >= self.end_time:
            raise ValidationError({"end_time": "End time must be after start time."})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.user.username} - {self.get_day_of_week_display()} {self.start_time}-{self.end_time}"


class Attendance(models.Model):
    """
    Phase 2: per-student attendance for a LiveClass. Deliberately separate
    from LessonProgress (recorded-course watch progress) and Enrollment
    (course access) -- attendance is specific to a single live session.
    """
    class Status(models.TextChoices):
        PRESENT = "PRESENT", "Present"
        ABSENT = "ABSENT", "Absent"
        LATE = "LATE", "Late"
        EXCUSED = "EXCUSED", "Excused"

    live_class = models.ForeignKey(LiveClass, related_name='attendance_records', on_delete=models.CASCADE, db_index=True)
    student = models.ForeignKey(settings.AUTH_USER_MODEL, related_name='live_class_attendance', on_delete=models.CASCADE, db_index=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ABSENT)
    marked_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='attendance_marked')
    marked_at = models.DateTimeField(auto_now=True)
    notes = models.TextField(blank=True)

    class Meta:
        unique_together = ('live_class', 'student')
        ordering = ['-marked_at']
        verbose_name_plural = "Attendance Records"

    def __str__(self):
        return f"{self.student.username} - {self.live_class.title} ({self.status})"


# =============================================================================
# Phase 4.1: Assessment Engine Foundation
#
# Models only -- deliberately NOT the full quiz-taking flow. Hierarchy:
#   Course -> Module -> Assessment -> Question -> QuestionOption
#
# Student attempts/answers/scoring belong to a later phase (see each
# model's own docstring for why on_delete was chosen deliberately) --
# nothing here stores a student's answer, and nothing here computes or
# persists a score. is_correct on QuestionOption is intentionally the
# only place "the right answer" lives, and it must never be serialized to
# a learner-facing API (no such API exists yet in this phase).
# =============================================================================

class Assessment(models.Model):
    """
    One quiz/test attached to a single Module -- e.g. "Module 3 Quiz".
    CASCADE from Module: an assessment has no meaning independent of its
    module (same rationale as VideoLesson -> Module above); deleting a
    module should delete the assessments defined under it.
    """
    module = models.ForeignKey(Module, related_name='assessments', on_delete=models.CASCADE)
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    instructions = models.TextField(blank=True, help_text="Shown to the student before they start the attempt.")
    order = models.PositiveIntegerField(default=0, help_text="Display order among this module's assessments.")
    is_published = models.BooleanField(default=False, db_index=True)

    passing_percentage = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal("40.00"),
        validators=[MinValueValidator(Decimal("0.00")), MaxValueValidator(Decimal("100.00"))],
        help_text="Minimum percentage of marks required to pass this assessment.",
    )
    max_attempts = models.PositiveIntegerField(
        default=1,
        validators=[MinValueValidator(1)],
        help_text="How many times a student may attempt this assessment.",
    )
    time_limit_minutes = models.PositiveIntegerField(
        null=True, blank=True,
        validators=[MinValueValidator(1)],
        help_text="Leave blank for no time limit.",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['module', 'order'], name='unique_module_assessment_order'),
        ]
        ordering = ['order']

    def __str__(self):
        return f"{self.module.course.title} - {self.module.title} - {self.title}"


class Question(models.Model):
    """
    A single question belonging to one Assessment. CASCADE from Assessment
    for the same reason Assessment cascades from Module -- a question has
    no meaning independent of the assessment it was written for.

    question_type only supports SINGLE_CHOICE and MULTIPLE_CHOICE for now
    (per Phase 4.1 scope -- essay/free-text grading is explicitly out of
    scope for this phase, so no ESSAY/SHORT_ANSWER choice is added yet:
    adding the enum value without any way to grade it would be a
    half-built, misleading option in every admin dropdown).
    """
    class QuestionType(models.TextChoices):
        SINGLE_CHOICE = "SINGLE_CHOICE", "Single Choice"
        MULTIPLE_CHOICE = "MULTIPLE_CHOICE", "Multiple Choice"

    assessment = models.ForeignKey(Assessment, related_name='questions', on_delete=models.CASCADE)
    question_text = models.TextField()
    question_type = models.CharField(max_length=20, choices=QuestionType.choices, default=QuestionType.SINGLE_CHOICE)
    order = models.PositiveIntegerField(default=0)
    marks = models.DecimalField(
        max_digits=6, decimal_places=2, default=Decimal("1.00"),
        validators=[MinValueValidator(Decimal("0.01"))],
        help_text="Marks awarded for answering this question correctly. Decimal, not float, to keep scoring exact.",
    )
    explanation = models.TextField(blank=True, help_text="Optional explanation shown to the student after the attempt is graded (a later phase).")
    is_required = models.BooleanField(default=True, help_text="Whether the student must answer this question to submit the attempt.")

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['assessment', 'order'], name='unique_assessment_question_order'),
        ]
        ordering = ['order']

    def __str__(self):
        return f"{self.assessment.title} - Q{self.order}: {self.question_text[:50]}"


class QuestionOption(models.Model):
    """
    One answer choice for a Question. CASCADE from Question for the same
    reason Question cascades from Assessment.

    is_correct is the ONLY place the right answer is recorded. It must
    stay admin-only (see courses/admin.py) and must never be included in
    a learner-facing serializer -- Phase 4.2's safe serializers
    (SafeQuestionOptionSerializer et al. in courses/serializers.py)
    explicitly whitelist fields rather than excluding this one, so a
    field added to this model in the future can't leak by omission.

    Whether a SINGLE_CHOICE question has exactly one is_correct=True
    option, and a MULTIPLE_CHOICE question has at least one, cannot be
    expressed as a single-row database constraint (it depends on sibling
    rows) -- it is enforced at the admin-form layer instead (see
    QuestionOptionInlineFormSet in courses/admin.py), the only place that
    currently mutates these rows.
    """
    question = models.ForeignKey(Question, related_name='options', on_delete=models.CASCADE)
    option_text = models.TextField()
    order = models.PositiveIntegerField(default=0)
    is_correct = models.BooleanField(default=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['question', 'order'], name='unique_question_option_order'),
        ]
        ordering = ['order']

    def __str__(self):
        return f"{self.question} - Option {self.order}"


# =============================================================================
# Phase 4.2: Assessment Attempts & Scoring
#
# AssessmentAttempt / AssessmentAnswer / AssessmentAnswerOption.
#
# Historical-integrity strategy (see the Phase 4.2 report for the full
# reasoning): the attempt's score/percentage/passed are computed ONCE at
# submission time and stored on the row -- never recomputed from live
# Question/QuestionOption data on read. That alone means a later admin
# edit to a question's marks or an option's correctness can never change
# an already-submitted result. What CAN'T be protected that way is
# *deletion*: deleting a Question or QuestionOption that already has
# recorded answers would silently destroy that part of the historical
# record. So both `AssessmentAnswer.question` and
# `AssessmentAnswerOption.option` use on_delete=PROTECT (the same
# precedent this codebase already uses for Purchase/LedgerEntry/Refund
# referencing Course/User/SubscriptionPlan) -- deleting a question or
# option that a student has ever answered is a deliberate, blocked
# action, not a silent one. `AssessmentAttempt.assessment` is PROTECT for
# the same reason (an assessment with any attempts can't be deleted
# out from under its own history; unpublishing it is the correct move
# instead). No separate snapshot of question text/marks/correctness is
# stored -- that would be over-engineering for what this phase actually
# needs (see the report).
# =============================================================================

class AssessmentAttempt(models.Model):
    """
    One student's attempt at one Assessment. `student` cascades with the
    User (matches LessonProgress.user's own precedent in this same app --
    an attempt is per-user learning state, not a financial/audit record
    that must outlive account deletion, unlike finance/orders' PROTECT-on-
    User convention which exists for a different reason).
    """
    class Status(models.TextChoices):
        IN_PROGRESS = "IN_PROGRESS", "In Progress"
        SUBMITTED = "SUBMITTED", "Submitted"
        TIMED_OUT = "TIMED_OUT", "Timed Out"

    assessment = models.ForeignKey(Assessment, related_name='attempts', on_delete=models.PROTECT)
    student = models.ForeignKey(settings.AUTH_USER_MODEL, related_name='assessment_attempts', on_delete=models.CASCADE)

    attempt_number = models.PositiveIntegerField(
        help_text="1-based, deterministic per (student, assessment) -- see AssessmentViewSet.start for how this is assigned race-safely."
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.IN_PROGRESS, db_index=True)

    started_at = models.DateTimeField(auto_now_add=True, help_text="The authoritative clock-start used for time_limit_minutes -- never trust a client-provided start time.")
    submitted_at = models.DateTimeField(null=True, blank=True)

    # Null until the attempt is actually scored (SUBMITTED or TIMED_OUT-
    # with-a-scored-submission never happens under this phase's chosen
    # design -- see the report's "answer saving" section -- so in
    # practice a TIMED_OUT attempt keeps these null). Decimal, never
    # float, for exact scoring arithmetic.
    score = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    percentage = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    passed = models.BooleanField(null=True, blank=True)
    # Phase 4.4: the sum of every question's `marks` AT SUBMISSION TIME --
    # frozen for the exact same historical-integrity reason `score` is
    # frozen. `score`/`percentage` alone are safe forever, but the result
    # screen also needs to display "8 / 10" (score / max marks); computing
    # that denominator fresh from LIVE Question.marks on every read would
    # let a later admin edit to a question's marks silently change what
    # "10" means for an already-submitted attempt, even though `score` and
    # `percentage` themselves stayed correct. Null until scored, same as
    # the three fields above.
    total_marks = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            # Deterministic numbering per (student, assessment) -- the DB-level
            # backstop behind AssessmentViewSet.start's select_for_update() locking.
            models.UniqueConstraint(fields=['student', 'assessment', 'attempt_number'], name='unique_student_assessment_attempt_number'),
            # At most one IN_PROGRESS attempt per (student, assessment) at a
            # time -- a partial unique index (condition=...), enforced by
            # the database itself, not just application code.
            models.UniqueConstraint(
                fields=['student', 'assessment'],
                condition=models.Q(status='IN_PROGRESS'),
                name='unique_active_attempt_per_student_assessment',
            ),
        ]
        ordering = ['-started_at']

    def __str__(self):
        return f"{self.student.username} - {self.assessment.title} - attempt {self.attempt_number} ({self.status})"


class AssessmentAnswer(models.Model):
    """
    One student's answer to one Question within one Attempt. `question` is
    PROTECT (see module docstring above) -- an answer has no meaning
    without its attempt (CASCADE), but the Question it references must
    never be silently deleted out from under recorded answers.

    Selected options are a genuine relational many-to-many (via the
    explicit AssessmentAnswerOption through-model below), never a JSON
    blob -- see that model's own docstring for why an explicit through
    model rather than a plain ManyToManyField.
    """
    attempt = models.ForeignKey(AssessmentAttempt, related_name='answers', on_delete=models.CASCADE)
    question = models.ForeignKey(Question, related_name='assessment_answers', on_delete=models.PROTECT)
    selected_options = models.ManyToManyField(QuestionOption, through='AssessmentAnswerOption', related_name='selected_in_answers')

    # Phase 4.4: frozen at scoring time by assessment_scoring.score_attempt
    # -- NOT re-derived from live QuestionOption.is_correct on read. This
    # is the one thing the Phase 4.2 report flagged as an open question
    # ("no per-question snapshot... worth confirming matches product
    # intent") and Phase 4.4 answers it: reviewing a submitted attempt
    # needs to show "was your answer correct / how many marks did you
    # get" per question, and without freezing it here, a later admin edit
    # to a question's marks or an option's correctness would silently
    # change what the review page shows for an already-graded attempt --
    # even though the attempt's own aggregate score/percentage/passed
    # were already safely frozen. This is the minimum addition that
    # closes that gap: no scoring RULE changes, just persisting a value
    # assessment_scoring.py already computes in its existing loop instead
    # of discarding it. Deliberately NOT snapshotting which OPTIONS were
    # correct at the time (that would mean tracking per-option historical
    # correctness, not just per-answer) -- see the Phase 4.4 CORRECTION
    # report for why that original trade-off turned out to be wrong, and
    # AssessmentAnswerOptionSnapshot below for what replaced it.
    is_correct = models.BooleanField(default=False)
    marks_awarded = models.DecimalField(max_digits=6, decimal_places=2, default=Decimal("0.00"))

    # Phase 4.4 CORRECTION: frozen alongside is_correct/marks_awarded, for
    # the same reason -- the review displays "marks_awarded / marks
    # possible" (e.g. "1/1"), and without also freezing the denominator, a
    # later admin edit to Question.marks would change what that "1" means
    # for an already-graded answer even though marks_awarded itself never
    # moves. This is the per-question twin of AssessmentAttempt.total_marks.
    marks_possible = models.DecimalField(max_digits=6, decimal_places=2, default=Decimal("0.00"))

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['attempt', 'question'], name='unique_attempt_question_answer'),
        ]

    def __str__(self):
        return f"{self.attempt} - {self.question}"


class AssessmentAnswerOption(models.Model):
    """
    Explicit through-model for AssessmentAnswer.selected_options, rather
    than a plain ManyToManyField, for one reason: a plain M2M gives Django
    no way to set on_delete on the QuestionOption side of its implicit
    through table (it's always an unconditional cascade-on-delete). Using
    an explicit through model lets `option` be PROTECT instead, so
    deleting an option a student actually selected is a blocked, deliberate
    action (see the module docstring above) rather than a silent one.

    Records ONLY which options this student picked -- a fact about the
    submission, not about the question. It intentionally has no
    correctness/text snapshot of its own; see AssessmentAnswerOptionSnapshot
    for the (separate, complementary) historical record of what EVERY
    option looked like at submission time.
    """
    answer = models.ForeignKey(AssessmentAnswer, related_name='option_links', on_delete=models.CASCADE)
    option = models.ForeignKey(QuestionOption, related_name='answer_links', on_delete=models.PROTECT)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['answer', 'option'], name='unique_answer_option'),
        ]

    def __str__(self):
        return f"{self.answer} -> {self.option}"


class AssessmentAnswerOptionSnapshot(models.Model):
    """
    Phase 4.4 CORRECTION. The historical-integrity gap this closes: the
    original Phase 4.4 review derived "which option was correct" from
    LIVE QuestionOption.is_correct, so an admin flipping an option's
    correctness (or editing its text) after a student was graded would
    silently change what that student's review page shows -- even though
    the frozen aggregate (score/percentage/passed/total_marks) and the
    frozen per-answer fields (is_correct/marks_awarded/marks_possible)
    never moved. That's a genuine contradiction: "you got this right"
    (frozen, still true) next to "the correct answer was something else"
    (live, now different).

    One row per (answer, option) for EVERY option belonging to that
    question -- not just the ones the student selected (that's what
    AssessmentAnswerOption is for). `option_text`/`was_correct`/`order`
    are copied from the live QuestionOption at the exact moment
    assessment_scoring.score_attempt runs, and never touched again.
    `option` itself stays a real FK (PROTECT, same reasoning as
    AssessmentAnswerOption.option) purely for identity (matching a
    snapshot row back to the option it describes, e.g. so the frontend
    can highlight the one the student picked) -- every DISPLAYED fact
    about the option comes from this row's own frozen columns, never
    from `option.option_text`/`option.is_correct` directly.
    """
    answer = models.ForeignKey(AssessmentAnswer, related_name='option_snapshots', on_delete=models.CASCADE)
    option = models.ForeignKey(QuestionOption, related_name='answer_option_snapshots', on_delete=models.PROTECT)
    option_text = models.TextField()
    was_correct = models.BooleanField()
    order = models.PositiveIntegerField()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['answer', 'option'], name='unique_answer_option_snapshot'),
        ]
        ordering = ['order']

    def __str__(self):
        return f"{self.answer} snapshot: {self.option_text}"


# =============================================================================
# Phase 4.6: Course Completion Certificates.
#
# Sits ON TOP of Phase 4.5 -- this model has NO completion logic of its
# own. Eligibility is decided once, at generation time, by asking
# CourseSerializer (the exact same authoritative source of truth the
# learning page reads) -- see courses/services/certificates.py. Nothing
# here ever recomputes or re-derives "is this course complete."
# =============================================================================

class Certificate(models.Model):
    """
    One issued certificate for one (student, course) pair. `course` is
    PROTECT for the same historical-record reason `AssessmentAttempt.
    assessment` is PROTECT (and the same reason this codebase already
    uses PROTECT for Purchase/LedgerEntry/Refund -> Course/User/
    SubscriptionPlan elsewhere): a course that has ever had a certificate
    issued against it can't be deleted out from under that certificate's
    history. `student` is CASCADE, matching AssessmentAttempt.student's
    own precedent -- a certificate is this user's own record, not a
    financial document that must outlive account deletion.

    learner_name_snapshot/course_title_snapshot are frozen at issuance,
    copied once and never re-read from the live User/Course rows -- a
    later admin renaming the course, or the student changing their
    display name, must not silently rewrite an already-issued
    certificate's content. No assessment-derived data (scores, marks,
    pass percentage) is stored or displayed here at all -- Phase 4.4's
    historical snapshot machinery is for reviewing an attempt, not for
    certificates, and Section 6 of the Phase 4.6 brief explicitly
    prohibits inventing grades/scores/percentages on a certificate.

    verification_id follows the EXACT existing precedent of Order.
    order_number/Invoice.invoice_number (uuid4-based, generated once in
    save(), backed by a DB unique constraint -- no coordination needed
    between concurrent requests for "the next number").
    """
    student = models.ForeignKey(settings.AUTH_USER_MODEL, related_name='certificates', on_delete=models.CASCADE)
    course = models.ForeignKey(Course, related_name='certificates', on_delete=models.PROTECT)

    verification_id = models.CharField(max_length=32, unique=True, editable=False)
    learner_name_snapshot = models.CharField(max_length=255)
    course_title_snapshot = models.CharField(max_length=255)

    issued_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            # The DB-level backstop behind get_or_create_certificate's
            # try/except IntegrityError idempotency handling (mirrors
            # NotificationService.create_notification's own
            # idempotency_key precedent) -- one certificate per student
            # per course, full stop; no reissue mechanism in this phase.
            models.UniqueConstraint(fields=['student', 'course'], name='unique_certificate_per_student_course'),
        ]
        ordering = ['-issued_at']

    def save(self, *args, **kwargs):
        # Mirrors Order.save()/Invoice.save()'s exact existing precedent.
        # Only ever generated once: a re-save of an existing certificate
        # never touches an already-set verification_id, matching
        # "immutable after issuance."
        if not self.verification_id:
            self.verification_id = f"CERT-{uuid.uuid4().hex[:12].upper()}"
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.verification_id} - {self.learner_name_snapshot} - {self.course_title_snapshot}"


# =============================================================================
# Phase 4.7: Assignments & Grading.
#
# Deliberately NOT wired into courses/services/completion.py -- per
# explicit product decision, assignments do not count toward Phase 4.5
# module/course completion in this phase. Lessons + published
# assessments remain the sole authoritative completion inputs; this
# module contains zero calls into or changes to completion.py.
# =============================================================================

class Assignment(models.Model):
    """
    A module-scoped, teacher-graded piece of work -- structurally the
    sibling of Assessment (same module/order/is_published shape), not
    nested inside it. CASCADE from Module for the same reason Assessment
    does: an assignment has no meaning independent of the module it was
    written for.
    """
    module = models.ForeignKey(Module, related_name='assignments', on_delete=models.CASCADE)
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True, help_text="Instructions shown to the student before they submit.")
    max_marks = models.DecimalField(
        max_digits=6, decimal_places=2, default=Decimal("100.00"),
        validators=[MinValueValidator(Decimal("0.01"))],
        help_text="Maximum marks a graded submission can be awarded. Decimal, not float, matching Question.marks.",
    )
    order = models.PositiveIntegerField(default=0)
    is_published = models.BooleanField(default=False, db_index=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['module', 'order'], name='unique_module_assignment_order'),
        ]
        ordering = ['order']

    def __str__(self):
        return f"{self.module.course.title} - {self.module.title} - {self.title}"


class AssignmentSubmission(models.Model):
    """
    One submission ATTEMPT for one (student, assignment) pair -- mirrors
    AssessmentAttempt's own "each attempt is its own immutable row,
    attempt_number is 1-based and deterministic" design exactly, for the
    same reason: grading history must survive a resubmission, not be
    overwritten by it. `assignment` is PROTECT (same historical-record
    reasoning as AssessmentAttempt.assessment); `student` is CASCADE
    (same per-user-learning-state reasoning as AssessmentAttempt.student).
    `graded_by` is SET_NULL -- matches Attendance.marked_by's exact
    precedent: the grading teacher's own account lifecycle must never
    delete the historical grade record itself.

    Resubmission rule (enforced in courses/services/assignments.py, not
    here): a new row may only be created when there is no existing row
    for this (student, assignment) at all, OR the most recent row's
    status is RETURNED_FOR_REVISION. The partial unique constraint below
    is the DB-level backstop -- at most one PENDING (SUBMITTED) row per
    (student, assignment) at a time, so two concurrent submit requests
    can never both create one.
    """
    class Status(models.TextChoices):
        SUBMITTED = "SUBMITTED", "Submitted"
        GRADED = "GRADED", "Graded"
        RETURNED_FOR_REVISION = "RETURNED_FOR_REVISION", "Returned for Revision"

    assignment = models.ForeignKey(Assignment, related_name='submissions', on_delete=models.PROTECT)
    student = models.ForeignKey(settings.AUTH_USER_MODEL, related_name='assignment_submissions', on_delete=models.CASCADE)

    attempt_number = models.PositiveIntegerField(
        help_text="1-based, deterministic per (student, assignment) -- see AssignmentSubmissionViewSet.submit for race-safe assignment."
    )
    status = models.CharField(max_length=25, choices=Status.choices, default=Status.SUBMITTED, db_index=True)

    content = models.TextField(blank=True, help_text="Optional text submission.")
    submitted_file = models.FileField(
        upload_to='assignments/submissions/', blank=True, null=True,
        validators=[FileExtensionValidator(allowed_extensions=ALLOWED_SUBMISSION_FILE_EXTENSIONS), validate_submission_file_size],
        help_text="Optional file submission.",
    )

    marks_awarded = models.DecimalField(
        max_digits=6, decimal_places=2, null=True, blank=True,
        validators=[MinValueValidator(Decimal("0.00"))],
        help_text="Null until graded. Must not exceed the assignment's max_marks -- enforced in the grading service, not here (a plain field validator can't see the related Assignment's own max_marks).",
    )
    feedback = models.TextField(blank=True)
    graded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='graded_assignment_submissions',
    )
    graded_at = models.DateTimeField(null=True, blank=True)

    submitted_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['student', 'assignment', 'attempt_number'], name='unique_student_assignment_attempt_number'),
            models.UniqueConstraint(
                fields=['student', 'assignment'],
                condition=models.Q(status='SUBMITTED'),
                name='unique_pending_submission_per_student_assignment',
            ),
        ]
        ordering = ['-submitted_at']

    def __str__(self):
        return f"{self.student.username} - {self.assignment.title} - attempt {self.attempt_number} ({self.status})"
