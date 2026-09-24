from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from .models import User, OTPVerification, OnboardingField, Mentorship, TeacherProfile, MentorProfile, AdminAuditLog, AccountDeletionRequest

class CustomUserAdmin(UserAdmin):
    fieldsets = UserAdmin.fieldsets + (
        ('LMS Roles', {'fields': ('is_student', 'is_teacher')}),
    )
    list_display = ['username', 'email', 'is_student', 'is_teacher', 'is_staff', 'is_superuser']
    # RBAC fix: is_staff/is_superuser removed from list_editable. Django's
    # changelist bulk-edit grid has no per-request hook to restrict which
    # fields are editable by role, so it could not be gated the way the
    # per-object change form can (see get_readonly_fields below) -- removing
    # them here is what actually closes the bulk-edit path for everyone,
    # while the object change form remains the (unrestricted, for Super
    # Admin) way to manage them, mirroring AdminUserSerializer.validate()
    # on the DRF side. is_student/is_teacher are unaffected.
    list_editable = ['is_student', 'is_teacher']

    def get_readonly_fields(self, request, obj=None):
        # Only a superuser may change is_staff/is_superuser here, matching
        # AdminUserSerializer.validate()'s DRF-side rule that only a Super
        # Admin can grant Super Admin or Admin (staff) status -- Django's
        # built-in admin previously had no equivalent guard.
        readonly = list(super().get_readonly_fields(request, obj))
        if not request.user.is_superuser:
            readonly += ['is_staff', 'is_superuser']
        return readonly

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


@admin.register(AdminAuditLog)
class AdminAuditLogAdmin(admin.ModelAdmin):
    """
    Phase 3.9. Read-only, same reasoning as every other financial/
    system-of-record admin in this codebase (LedgerEntry/Payout/Refund in
    finance/admin.py): a hand-edited or hand-deleted audit row would
    defeat its own purpose. AdminAuditLog.record() is the only code path
    that ever creates a row here.
    """
    list_display = ('created_at', 'action', 'actor', 'target_type', 'target_id')
    list_filter = ('action', 'target_type')
    search_fields = ('actor__username', 'target_type', 'target_id', 'description')
    readonly_fields = ('actor', 'action', 'target_type', 'target_id', 'description', 'metadata', 'created_at')

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(AccountDeletionRequest)
class AccountDeletionRequestAdmin(admin.ModelAdmin):
    list_display = ('id', 'user', 'status', 'created_at', 'confirmed_at', 'completed_at')
    list_filter = ('status', 'created_at')
    search_fields = ('user__username', 'user__email', 'reason', 'error_message')
    readonly_fields = ('user', 'status', 'reason', 'cleanup_log', 'error_message', 'confirmed_at', 'completed_at', 'created_at', 'updated_at')

    def has_add_permission(self, request):
        return False

