from rest_framework import viewsets, mixins, permissions, status
from rest_framework.decorators import action
from rest_framework.response import Response
from django.utils import timezone
from django.db.models import Q
from courses.models import Enrollment
from .models import Notification, Announcement
from .serializers import NotificationSerializer, AnnouncementSerializer

class IsStaffOrReadOnly(permissions.BasePermission):
    """
    Allow read-only access for authenticated users,
    but write permissions are restricted to staff/admin users.
    """
    def has_permission(self, request, view):
        if request.method in permissions.SAFE_METHODS:
            return True
        return bool(request.user and request.user.is_authenticated and (request.user.is_staff or request.user.is_superuser))


class NotificationViewSet(mixins.ListModelMixin,
                          mixins.RetrieveModelMixin,
                          viewsets.GenericViewSet):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = NotificationSerializer

    def get_queryset(self):
        queryset = Notification.objects.filter(recipient=self.request.user)
        is_read = self.request.query_params.get('is_read')
        if is_read is not None:
            if is_read.lower() == 'true':
                queryset = queryset.filter(is_read=True)
            elif is_read.lower() == 'false':
                queryset = queryset.filter(is_read=False)
        return queryset

    @action(detail=True, methods=['post'], url_path='read')
    def mark_as_read(self, request, pk=None):
        notification = self.get_object()
        if not notification.is_read:
            notification.is_read = True
            notification.read_at = timezone.now()
            notification.save(update_fields=['is_read', 'read_at'])
        return Response(self.get_serializer(notification).data, status=status.HTTP_200_OK)

    @action(detail=False, methods=['post'], url_path='read-all')
    def mark_all_as_read(self, request):
        unread_notifications = Notification.objects.filter(recipient=request.user, is_read=False)
        now = timezone.now()
        updated_count = unread_notifications.update(is_read=True, read_at=now)
        return Response({"updated": updated_count}, status=status.HTTP_200_OK)

    @action(detail=False, methods=['get'], url_path='unread-count')
    def unread_count(self, request):
        count = Notification.objects.filter(recipient=request.user, is_read=False).count()
        return Response({"count": count}, status=status.HTTP_200_OK)


class AnnouncementViewSet(viewsets.ModelViewSet):
    permission_classes = [permissions.IsAuthenticated, IsStaffOrReadOnly]
    serializer_class = AnnouncementSerializer

    def get_queryset(self):
        user = self.request.user
        if not user.is_authenticated:
            return Announcement.objects.none()

        if user.is_staff or user.is_superuser:
            return Announcement.objects.all()

        enrolled_course_ids = Enrollment.objects.filter(user=user).values_list('course_id', flat=True)
        return Announcement.objects.filter(
            Q(course__isnull=True, is_published=True) |
            Q(course_id__in=enrolled_course_ids, is_published=True)
        ).distinct()

    def perform_create(self, serializer):
        serializer.save(sender=self.request.user)
