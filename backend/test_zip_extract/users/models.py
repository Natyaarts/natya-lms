from django.contrib.auth.models import AbstractUser
from django.db import models

class User(AbstractUser):
    # Add custom fields here if needed later
    is_teacher = models.BooleanField(default=False)
    is_student = models.BooleanField(default=True)
    phone_number = models.CharField(max_length=15, blank=True, null=True)
    parent_name = models.CharField(max_length=255, blank=True, null=True)
    parent_phone = models.CharField(max_length=15, blank=True, null=True)

    def __str__(self):
        return self.username

class OTPVerification(models.Model):
    # Can be an email or a phone number
    identifier = models.CharField(max_length=255)
    otp = models.CharField(max_length=6)
    created_at = models.DateTimeField(auto_now_add=True)
    is_verified = models.BooleanField(default=False)

    def __str__(self):
        return f"{self.identifier} - {self.otp}"
