from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from .models import User, OTPVerification, OnboardingField

class CustomUserAdmin(UserAdmin):
    fieldsets = UserAdmin.fieldsets + (
        ('LMS Roles', {'fields': ('is_student', 'is_teacher')}),
    )
    list_display = ['username', 'email', 'is_student', 'is_teacher', 'is_staff', 'is_superuser']
    list_editable = ['is_student', 'is_teacher', 'is_staff', 'is_superuser']

admin.site.register(User, CustomUserAdmin)

admin.site.register(OTPVerification)

@admin.register(OnboardingField)
class OnboardingFieldAdmin(admin.ModelAdmin):
    list_display = ('label', 'name', 'field_type', 'is_required', 'order')
    list_editable = ('order', 'is_required')
    search_fields = ('label', 'name')
