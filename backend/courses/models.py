from django.db import models
from django.conf import settings
from django.core.exceptions import ValidationError
from users.models import User

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
