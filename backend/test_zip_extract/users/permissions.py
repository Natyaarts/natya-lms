from rest_framework import permissions

class IsSuperAdmin(permissions.BasePermission):
    """
    Allows access only to superusers.
    """
    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated and request.user.is_superuser)

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
