from rest_framework import permissions

class IsSuperAdmin(permissions.BasePermission):
    """
    Allows access only to superusers.
    """
    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated and request.user.is_superuser)

class IsSuperAdminOrAdmin(permissions.BasePermission):
    """
    SUPER ADMIN (is_superuser) and ADMIN (is_staff, non-superuser) both get
    full administrative access -- Django's own is_staff field is reused as
    "Admin" rather than introducing a new field, matching the existing
    pattern already trusted by several other permission classes in this
    codebase (course/lesson editing, live batches). Privilege escalation is
    still guarded: see AdminUserSerializer.validate(), which blocks a
    non-superuser Admin from granting is_superuser/is_staff to anyone.
    """
    def has_permission(self, request, view):
        return bool(
            request.user and request.user.is_authenticated
            and (request.user.is_superuser or request.user.is_staff)
        )

class IsTeacher(permissions.BasePermission):
    """
    Allows access only to teachers.
    """
    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated and getattr(request.user, 'is_teacher', False))

class IsStudent(permissions.BasePermission):
    """
    Allows access only to students.
    """
    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated and getattr(request.user, 'is_student', False))

class IsMentor(permissions.BasePermission):
    """
    Allows access only to mentors. Deliberately separate from IsTeacher --
    a mentor must never be treated as a teacher just because both roles
    exist on the platform.
    """
    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated and getattr(request.user, 'is_mentor', False))

class IsSuperAdminOrTeacher(permissions.BasePermission):
    """
    Allows access to superusers or teachers.
    """
    def has_permission(self, request, view):
        return bool(
            request.user and request.user.is_authenticated and 
            (request.user.is_superuser or getattr(request.user, 'is_teacher', False))
        )

class IsSuperAdminOrTeacherOrReadOnly(permissions.BasePermission):
    """
    The request is authenticated as a superadmin or teacher, or is a read-only request.
    """
    def has_permission(self, request, view):
        if request.method in permissions.SAFE_METHODS:
            return True
        return bool(
            request.user and request.user.is_authenticated and
            (request.user.is_superuser or getattr(request.user, 'is_teacher', False))
        )

class IsSuperAdminOrReadOnlyMentorship(permissions.BasePermission):
    """
    Mentorship assignments are managed by admin/staff only (mirrors how
    CourseInstructor assignment is admin-managed). Mentors and students can
    read their own rows -- scoped in MentorshipViewSet.get_queryset, not
    here -- but never create/modify/delete an assignment themselves.
    """
    def has_permission(self, request, view):
        if not (request.user and request.user.is_authenticated):
            return False
        if request.method in permissions.SAFE_METHODS:
            return True
        return bool(request.user.is_superuser or request.user.is_staff)
