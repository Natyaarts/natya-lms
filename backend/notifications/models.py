from django.db import models
from users.models import User
from courses.models import Course

class Notification(models.Model):
    NOTIFICATION_TYPES = (
        ('COURSE_UPDATE', 'Course Update'),
        ('ENROLLMENT', 'Enrollment'),
        ('PAYMENT', 'Payment'),
        ('ANNOUNCEMENT', 'Announcement'),
        ('COURSE_COMPLETION', 'Course Completion'),
        ('CERTIFICATE', 'Certificate'),
    )

    recipient = models.ForeignKey(
        User,
        related_name="notifications",
        on_delete=models.CASCADE,
        db_index=True
    )
    title = models.CharField(max_length=255)
    body = models.TextField()
    notification_type = models.CharField(
        max_length=50,
        choices=NOTIFICATION_TYPES,
        default='ANNOUNCEMENT'
    )
    is_read = models.BooleanField(default=False, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    read_at = models.DateTimeField(null=True, blank=True)
    action_url = models.CharField(max_length=500, blank=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.recipient.username} - {self.title} ({self.get_notification_type_display()})"


class Announcement(models.Model):
    sender = models.ForeignKey(
        User,
        related_name="announcements",
        on_delete=models.SET_NULL,
        null=True,
        blank=True
    )
    course = models.ForeignKey(
        Course,
        related_name="announcements",
        on_delete=models.SET_NULL,
        null=True,
        blank=True
    )
    title = models.CharField(max_length=255)
    content = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    is_published = models.BooleanField(default=False)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return self.title
