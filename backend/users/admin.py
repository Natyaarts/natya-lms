from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from .models import User, OTPVerification, OnboardingField, Mentorship, TeacherProfile, MentorProfile

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

@admin.register(Mentorship)
class MentorshipAdmin(admin.ModelAdmin):
    list_display = ('student', 'mentor', 'status', 'assigned_by', 'assigned_at')
    list_filter = ('status',)
    search_fields = ('student__username', 'mentor__username')

@admin.register(TeacherProfile)
class TeacherProfileAdmin(admin.ModelAdmin):
    list_display = ('user', 'specialization', 'experience_years', 'is_public', 'is_active')
    list_filter = ('is_public', 'is_active')
    search_fields = ('user__username', 'user__email', 'specialization')

@admin.register(MentorProfile)
class MentorProfileAdmin(admin.ModelAdmin):
    list_display = ('user', 'specialization', 'availability_status', 'is_public', 'is_active')
    list_filter = ('availability_status', 'is_public', 'is_active')
    search_fields = ('user__username', 'user__email', 'specialization')
