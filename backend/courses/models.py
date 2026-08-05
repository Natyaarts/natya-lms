from django.db import models
from users.models import User

class Course(models.Model):
    title = models.CharField(max_length=255)
    description = models.TextField()
    price = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    thumbnail = models.ImageField(upload_to='course_thumbnails/', blank=True, null=True)
    is_published = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

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
