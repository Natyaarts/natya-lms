from django.contrib.auth.models import AbstractUser
from django.db import models

class User(AbstractUser):
    # Roles
    is_teacher = models.BooleanField(default=False)
    is_student = models.BooleanField(default=True)
    
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

class OTPVerification(models.Model):
    # Can be an email or a phone number
    identifier = models.CharField(max_length=255)
    otp = models.CharField(max_length=6)
    created_at = models.DateTimeField(auto_now_add=True)
    is_verified = models.BooleanField(default=False)

    def __str__(self):
        return f"{self.identifier} - {self.otp}"
