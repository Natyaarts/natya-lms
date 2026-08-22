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

class Enrollment(models.Model):
    user = models.ForeignKey(User, related_name='enrollments', on_delete=models.CASCADE)
    course = models.ForeignKey(Course, related_name='enrollments', on_delete=models.CASCADE)
    enrolled_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('user', 'course')

    def __str__(self):
        return f"{self.user.username} enrolled in {self.course.title}"

class TranslatedAudio(models.Model):
    lesson = models.ForeignKey(VideoLesson, related_name='translated_audios', on_delete=models.CASCADE)
    language_code = models.CharField(max_length=10, help_text="e.g. ml-IN, ta-IN, hi-IN, es-ES")
    audio_file = models.FileField(upload_to='videos/audios/')
    status = models.CharField(max_length=20, default='processing') # 'processing', 'completed', 'failed'
    created_at = models.DateTimeField(auto_now_add=True)

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


class LiveClass(models.Model):
    class MeetingProvider(models.TextChoices):
        ZOOM = "ZOOM", "Zoom"
        GOOGLE_MEET = "GOOGLE_MEET", "Google Meet"
        TEAMS = "TEAMS", "Teams"
        OTHER = "OTHER", "Other"

    class ClassStatus(models.TextChoices):
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
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name_plural = "Live Batches"

    def clean(self):
        super().clean()
        if self.course and self.course.course_type != Course.CourseType.LIVE:
            raise ValidationError("A LiveBatch can only be created for a Course with type LIVE.")
        if self.instructor and not (self.instructor.is_superuser or self.instructor.is_staff or getattr(self.instructor, 'is_teacher', False)):
            raise ValidationError("The instructor must be a teacher or administrator.")

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
