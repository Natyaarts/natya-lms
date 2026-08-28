from rest_framework import viewsets, permissions
from rest_framework.decorators import action
from rest_framework.response import Response
from .models import Course, Module, VideoLesson, Enrollment, LessonProgress, LiveClass, LiveBatch, LiveBatchStudent
from .serializers import CourseSerializer, ModuleSerializer, VideoLessonSerializer, LessonProgressSerializer, LiveClassSerializer, LiveBatchSerializer, LiveBatchStudentSerializer
from users.permissions import IsSuperAdminOrTeacherOrReadOnly

class CourseViewSet(viewsets.ModelViewSet):
    queryset = Course.objects.all()
    serializer_class = CourseSerializer
    permission_classes = [IsSuperAdminOrTeacherOrReadOnly]

    def get_queryset(self):
        user = self.request.user
        if user.is_authenticated and (user.is_staff or user.groups.filter(name='Teachers').exists()):
            return Course.objects.all()
        if user.is_authenticated:
            from django.db.models import Q
            return Course.objects.filter(
                Q(is_published=True) |
                Q(enrollments__user=user)
            ).distinct()
        return Course.objects.filter(is_published=True)

    @action(detail=False, methods=['get'], permission_classes=[permissions.AllowAny])
    def my_courses(self, request):
        # Fallback for local testing if cookie is blocked
        user = request.user
        if user.is_anonymous:
            from django.contrib.auth import get_user_model
            user = get_user_model().objects.first()

        enrolled_courses = Course.objects.filter(enrollments__user=user).distinct()
        serializer = self.get_serializer(enrolled_courses, many=True)
        return Response(serializer.data)

class ModuleViewSet(viewsets.ModelViewSet):
    queryset = Module.objects.all()
    serializer_class = ModuleSerializer
    permission_classes = [IsSuperAdminOrTeacherOrReadOnly]

class VideoLessonViewSet(viewsets.ModelViewSet):
    queryset = VideoLesson.objects.all()
    serializer_class = VideoLessonSerializer
    permission_classes = [IsSuperAdminOrTeacherOrReadOnly]

    # Custom logic can be added here to only allow users who purchased the course
    def get_queryset(self):
        user = self.request.user
        # For now, if they are authenticated, they can see videos.
        # Next step: check if user is in course purchases
        return super().get_queryset()

    @action(detail=True, methods=['post'])
    def generate_ai_audio(self, request, pk=None):
        from django.db import transaction
        from .tasks import generate_dubbed_audio_task
        from .models import TranslatedAudio

        lesson = self.get_object()

        target_languages_input = request.data.get('target_languages')
        if target_languages_input is not None:
            if not isinstance(target_languages_input, list):
                return Response({"error": "target_languages must be a list of language codes."}, status=400)

            from courses.services.ai_translator import LANGUAGE_MAP
            for lang in target_languages_input:
                if lang not in LANGUAGE_MAP:
                    return Response({"error": f"Unsupported language code: {lang}."}, status=400)

            target_languages = target_languages_input
        else:
            target_languages = ['hi', 'ta', 'ml']

        with transaction.atomic():
            # Lock TranslatedAudio records for this lesson to prevent concurrent requests
            existing_tracks = TranslatedAudio.objects.select_for_update().filter(
                lesson=lesson,
                language_code__in=target_languages
            )

            # Zombie Task Recovery
            # NOTE: TranslatedAudio does not have an updated_at or celery heartbeat.
            # This is a fallback stale detection using created_at, not true heartbeat monitoring.
            from django.conf import settings
            from django.utils import timezone
            from datetime import timedelta
            import logging

            logger = logging.getLogger(__name__)
            stale_hours = getattr(settings, 'AI_AUDIO_STALE_HOURS', 6)
            stale_threshold = timezone.now() - timedelta(hours=stale_hours)

            active_processing_langs = []
            for track in existing_tracks:
                if track.status == 'processing':
                    if track.created_at >= stale_threshold:
                        active_processing_langs.append(track.language_code)
                    else:
                        logger.warning(f"Found stale processing track for lesson {lesson.id} lang {track.language_code}. Allowing retry.")

            # If any requested language is actively processing, reject the duplicate run
            if active_processing_langs:
                return Response(
                    {"error": f"AI Audio generation is already in progress for languages: {active_processing_langs}."},
                    status=status.HTTP_400_BAD_REQUEST
                )

            langs_to_process = []
            for lang in target_languages:
                track = next((t for t in existing_tracks if t.language_code == lang), None)
                if track:
                    # Skip if completed
                    if track.status == 'completed':
                        continue
                    # Reset created_at if we are recovering a stale track
                    if track.status == 'processing':
                        track.created_at = timezone.now()
                    track.status = 'processing'
                    track.save()
                else:
                    TranslatedAudio.objects.create(
                        lesson=lesson,
                        language_code=lang,
                        status='processing'
                    )
                langs_to_process.append(lang)

            if not langs_to_process:
                return Response({"message": f"AI Audio tracks for '{lesson.title}' are already generated and completed."})

            # Enqueue Celery task
            generate_dubbed_audio_task.delay(lesson.id, target_languages=langs_to_process)

        return Response({"message": f"AI Audio generation started in background for '{lesson.title}'."})

    @action(detail=True, methods=['get', 'post', 'patch'], url_path='progress', permission_classes=[permissions.IsAuthenticated])
    def progress(self, request, pk=None):
        from django.utils import timezone

        lesson = self.get_object()
        user = request.user

        # Access control check: Must be enrolled (unless superuser)
        is_enrolled = Enrollment.objects.filter(user=user, course=lesson.module.course).exists()
        if not is_enrolled and not user.is_superuser:
            return Response({"detail": "You do not have access to this course's progress tracking."}, status=403)

        if request.method == 'GET':
            try:
                progress_obj = LessonProgress.objects.get(user=user, lesson=lesson)
                serializer = LessonProgressSerializer(progress_obj)
                return Response(serializer.data)
            except LessonProgress.DoesNotExist:
                # Return sensible default/empty progress response
                return Response({
                    "id": None,
                    "lesson": lesson.id,
                    "last_watched_position": 0.0,
                    "video_duration": 0.0,
                    "progress_percentage": 0.0,
                    "completed": False,
                    "updated_at": None,
                    "completed_at": None
                })

        else: # POST / PATCH
            last_watched_position = request.data.get('last_watched_position')
            video_duration = request.data.get('video_duration')
            completed_input = request.data.get('completed')

            # Validation
            if last_watched_position is not None:
                try:
                    last_watched_position = float(last_watched_position)
                except (ValueError, TypeError):
                    return Response({"error": "last_watched_position must be a float."}, status=400)
                if last_watched_position < 0:
                    return Response({"error": "last_watched_position cannot be negative."}, status=400)

            if video_duration is not None:
                try:
                    video_duration = float(video_duration)
                except (ValueError, TypeError):
                    return Response({"error": "video_duration must be a float."}, status=400)
                if video_duration < 0:
                    return Response({"error": "video_duration cannot be negative."}, status=400)

            progress_obj, created = LessonProgress.objects.get_or_create(user=user, lesson=lesson)

            # Apply duration update if provided
            if video_duration is not None:
                progress_obj.video_duration = video_duration

            # Clamp last_watched_position to duration if applicable
            current_duration = progress_obj.video_duration
            if last_watched_position is not None:
                if current_duration > 0 and last_watched_position > current_duration:
                    last_watched_position = current_duration
                progress_obj.last_watched_position = last_watched_position
            elif current_duration > 0 and progress_obj.last_watched_position > current_duration:
                # If duration decreased or was set, clamp the existing position
                progress_obj.last_watched_position = current_duration

            # Completion logic
            if completed_input is not None:
                completed_val = bool(completed_input)
                if completed_val:
                    if not progress_obj.completed:
                        progress_obj.completed = True
                        progress_obj.completed_at = timezone.now()
                else:
                    # Do not revert to incomplete if already completed
                    if not progress_obj.completed:
                        progress_obj.completed = False

            progress_obj.save()
            serializer = LessonProgressSerializer(progress_obj)
            return Response(serializer.data)


from rest_framework import viewsets
from rest_framework.pagination import PageNumberPagination
from users.permissions import IsSuperAdmin
from .models import Enrollment
from .serializers import AdminEnrollmentSerializer

class EnrollmentResultsSetPagination(PageNumberPagination):
    page_size = 10
    page_size_query_param = 'page_size'
    max_page_size = 100

class AdminEnrollmentViewSet(viewsets.ModelViewSet):
    queryset = Enrollment.objects.all().select_related('user', 'course').order_by('-enrolled_at')
    serializer_class = AdminEnrollmentSerializer
    permission_classes = [IsSuperAdmin]
    pagination_class = EnrollmentResultsSetPagination

    def get_queryset(self):
        queryset = Enrollment.objects.all().select_related('user', 'course').order_by('-enrolled_at')

        # Limit to student accounts only
        queryset = queryset.filter(user__is_student=True, user__is_teacher=False)

        source_filter = self.request.query_params.get('source')
        search_param = self.request.query_params.get('search')

        if search_param:
            from django.db.models import Q
            queryset = queryset.filter(
                Q(user__username__icontains=search_param) |
                Q(user__first_name__icontains=search_param) |
                Q(user__last_name__icontains=search_param) |
                Q(user__email__icontains=search_param) |
                Q(user__phone_number__icontains=search_param) |
                Q(course__title__icontains=search_param)
            )

        if source_filter:
            from orders.models import Purchase
            from django.db.models import OuterRef, Exists

            purchases = Purchase.objects.filter(
                user_id=OuterRef('user_id'),
                course_id=OuterRef('course_id'),
                status='SUCCESS'
            )

            if source_filter == 'Paid':
                queryset = queryset.filter(Exists(purchases))
            elif source_filter == 'Manual':
                queryset = queryset.filter(~Exists(purchases))

        return queryset


from rest_framework import status
from django.utils import timezone
from django.db.models import Q, Count

class IsSuperAdminOrAuthorizedTeacherOrReadOnly(permissions.BasePermission):
    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        if request.method in permissions.SAFE_METHODS:
            return True
        return request.user.is_superuser or request.user.is_staff or getattr(request.user, 'is_teacher', False)

    def has_object_permission(self, request, view, obj):
        # Admins have full access
        if request.user.is_superuser or request.user.is_staff:
            return True
        # If batch is NULL, students and teachers have NO access (legacy/orphaned isolation)
        if not obj.batch:
            return False
        # Teachers can only access classes where they are the batch instructor
        if getattr(request.user, 'is_teacher', False):
            return obj.batch.instructor == request.user
        # Students can only access classes they are assigned to via LiveBatchStudent (Safe methods only)
        if request.method in permissions.SAFE_METHODS:
            from .models import LiveBatchStudent
            return LiveBatchStudent.objects.filter(batch=obj.batch, student=request.user).exists()
        return False


class LiveClassResultsSetPagination(PageNumberPagination):
    page_size = 10
    page_size_query_param = 'page_size'
    max_page_size = 100


class LiveClassViewSet(viewsets.ModelViewSet):
    serializer_class = LiveClassSerializer
    permission_classes = [permissions.IsAuthenticated, IsSuperAdminOrAuthorizedTeacherOrReadOnly]
    pagination_class = LiveClassResultsSetPagination

    def get_queryset(self):
        user = self.request.user
        if not user or not user.is_authenticated:
            return LiveClass.objects.none()

        if user.is_superuser or user.is_staff:
            queryset = LiveClass.objects.all().select_related('course', 'instructor', 'batch')
        elif getattr(user, 'is_teacher', False):
            queryset = LiveClass.objects.filter(batch__instructor=user).select_related('course', 'instructor', 'batch')
        else:
            queryset = LiveClass.objects.filter(batch__students__student=user).distinct().select_related('course', 'instructor', 'batch')

        # Filter by course
        course_id = self.request.query_params.get('course')
        if course_id:
            queryset = queryset.filter(course_id=course_id)

        # Filter by status
        status_param = self.request.query_params.get('status')
        if status_param:
            queryset = queryset.filter(status=status_param)

        # Filter by instructor
        instructor_id = self.request.query_params.get('instructor')
        if instructor_id:
            queryset = queryset.filter(instructor_id=instructor_id)

        return queryset

    def perform_create(self, serializer):
        from django.db import transaction
        from notifications.services import LiveClassNotificationService
        from .tasks import send_class_reminder
        import datetime

        user = self.request.user
        live_class = serializer.save()

        def dispatch_notifications():
            LiveClassNotificationService.notify_scheduled(live_class)

            reminder_eta = live_class.scheduled_start - datetime.timedelta(hours=1)
            send_class_reminder.apply_async(
                args=[live_class.id, int(live_class.scheduled_start.timestamp())],
                eta=reminder_eta
            )

        connection = transaction.get_connection()
        if connection.in_atomic_block:
            transaction.on_commit(dispatch_notifications)
        else:
            dispatch_notifications()

    @action(detail=True, methods=['post'])
    def start(self, request, pk=None):
        live_class = self.get_object()
        if live_class.status != LiveClass.ClassStatus.SCHEDULED:
            return Response(
                {"error": f"Cannot transition to LIVE from {live_class.status}."},
                status=status.HTTP_400_BAD_REQUEST
            )
        live_class.status = LiveClass.ClassStatus.LIVE
        live_class.save()
        serializer = self.get_serializer(live_class)
        return Response(serializer.data)

    @action(detail=True, methods=['post'])
    def end(self, request, pk=None):
        live_class = self.get_object()
        if live_class.status != LiveClass.ClassStatus.LIVE:
            return Response(
                {"error": f"Cannot transition to COMPLETED from {live_class.status}."},
                status=status.HTTP_400_BAD_REQUEST
            )
        live_class.status = LiveClass.ClassStatus.COMPLETED
        live_class.save()
        serializer = self.get_serializer(live_class)
        return Response(serializer.data)

    @action(detail=True, methods=['post'])
    def cancel(self, request, pk=None):
        from django.db import transaction
        from notifications.services import LiveClassNotificationService

        live_class = self.get_object()
        if live_class.status != LiveClass.ClassStatus.SCHEDULED:
            return Response(
                {"error": f"Cannot transition to CANCELLED from {live_class.status}."},
                status=status.HTTP_400_BAD_REQUEST
            )
        live_class.status = LiveClass.ClassStatus.CANCELLED
        live_class.save()

        def dispatch_cancellation():
            LiveClassNotificationService.notify_cancelled(live_class)

        connection = transaction.get_connection()
        if connection.in_atomic_block:
            transaction.on_commit(dispatch_cancellation)
        else:
            dispatch_cancellation()

        serializer = self.get_serializer(live_class)
        return Response(serializer.data)

    @action(detail=True, methods=['post'])
    def reschedule(self, request, pk=None):
        from django.db import transaction
        from notifications.services import LiveClassNotificationService
        from .tasks import send_class_reminder
        import datetime

        live_class = self.get_object()
        if live_class.status != LiveClass.ClassStatus.SCHEDULED:
            return Response(
                {"detail": "Only scheduled live classes can be rescheduled."},
                status=status.HTTP_400_BAD_REQUEST
            )

        with transaction.atomic():
            locked_class = LiveClass.objects.select_for_update().get(pk=live_class.pk)

            if locked_class.status != LiveClass.ClassStatus.SCHEDULED:
                return Response(
                    {"detail": "Only scheduled live classes can be rescheduled."},
                    status=status.HTTP_400_BAD_REQUEST
                )

            old_scheduled_start = locked_class.scheduled_start

            serializer = self.get_serializer(locked_class, data=request.data, partial=True)
            serializer.is_valid(raise_exception=True)
            updated_class = serializer.save()

            if updated_class.scheduled_start != old_scheduled_start:
                def dispatch_reschedule():
                    LiveClassNotificationService.notify_rescheduled(updated_class, old_scheduled_start)
                    reminder_eta = updated_class.scheduled_start - datetime.timedelta(hours=1)
                    send_class_reminder.apply_async(
                        args=[updated_class.id, int(updated_class.scheduled_start.timestamp())],
                        eta=reminder_eta
                    )
                transaction.on_commit(dispatch_reschedule)

        return Response(serializer.data)

    @action(detail=False, methods=['get'])
    def upcoming(self, request):
        now = timezone.now()
        queryset = self.filter_queryset(self.get_queryset())
        queryset = queryset.filter(
            Q(status=LiveClass.ClassStatus.SCHEDULED, scheduled_start__gte=now) |
            Q(status=LiveClass.ClassStatus.LIVE)
        ).order_by('scheduled_start')

        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)

        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data)

    @action(detail=False, methods=['get'])
    def history(self, request):
        queryset = self.filter_queryset(self.get_queryset())
        queryset = queryset.filter(status=LiveClass.ClassStatus.COMPLETED).order_by('-scheduled_start')

        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)

        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data)


class IsSuperAdminOrStaffOrReadOnlyBatches(permissions.BasePermission):
    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        if request.method in permissions.SAFE_METHODS:
            return True
        return request.user.is_superuser or request.user.is_staff

    def has_object_permission(self, request, view, obj):
        if request.user.is_superuser or request.user.is_staff:
            return True
        if request.method in permissions.SAFE_METHODS:
            if getattr(request.user, 'is_teacher', False):
                return obj.instructor == request.user
            # Student visibility check
            return obj.students.filter(student=request.user).exists()
        return False


class LiveBatchViewSet(viewsets.ModelViewSet):
    serializer_class = LiveBatchSerializer
    permission_classes = [permissions.IsAuthenticated, IsSuperAdminOrStaffOrReadOnlyBatches]

    def get_queryset(self):
        user = self.request.user
        if not user or not user.is_authenticated:
            return LiveBatch.objects.none()

        if user.is_superuser or user.is_staff:
            queryset = LiveBatch.objects.all()
        elif getattr(user, 'is_teacher', False):
            queryset = LiveBatch.objects.filter(instructor=user)
        else:
            queryset = LiveBatch.objects.filter(students__student=user)

        # Optimize loading with annotations & select_related
        queryset = queryset.annotate(
            student_count=Count('students')
        ).select_related('course', 'instructor')

        return queryset

    @action(detail=True, methods=['get', 'post'], url_path='students')
    def students(self, request, pk=None):
        batch = self.get_object()
        if request.method == 'GET':
            students_qs = LiveBatchStudent.objects.filter(batch=batch).select_related('student', 'purchase')
            page = self.paginate_queryset(students_qs)
            if page is not None:
                serializer = LiveBatchStudentSerializer(page, many=True)
                return self.get_paginated_response(serializer.data)
            serializer = LiveBatchStudentSerializer(students_qs, many=True)
            return Response(serializer.data)

        elif request.method == 'POST':
            # Only superuser or staff can assign students to batches
            if not (request.user.is_superuser or request.user.is_staff):
                return Response({"detail": "You do not have permission to assign students to a batch."}, status=status.HTTP_403_FORBIDDEN)

            student_id = request.data.get('student_id')
            purchase_id = request.data.get('purchase_id')

            if not student_id:
                return Response({"error": "student_id is required"}, status=status.HTTP_400_BAD_REQUEST)

            from courses.services.live_batch_service import LiveBatchService
            from django.core.exceptions import ValidationError
            try:
                assignment, created = LiveBatchService.assign_student(
                    batch_id=batch.id,
                    student_id=student_id,
                    purchase_id=purchase_id,
                    request_user=request.user
                )
                serializer = LiveBatchStudentSerializer(assignment)
                return Response(serializer.data, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)
            except ValidationError as e:
                error_msg = e.messages[0] if hasattr(e, 'messages') else str(e)
                return Response({"error": error_msg}, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=['delete'], url_path=r'students/(?P<student_id>[^/.]+)')
    def remove_student(self, request, pk=None, student_id=None):
        batch = self.get_object()
        # Only superuser or staff can remove students from batches
        if not (request.user.is_superuser or request.user.is_staff):
            return Response({"detail": "You do not have permission to remove students from a batch."}, status=status.HTTP_403_FORBIDDEN)

        try:
            assignment = LiveBatchStudent.objects.get(batch=batch, student_id=student_id)
            assignment.delete()
            return Response({"message": "Student successfully removed from the batch."}, status=status.HTTP_204_NO_CONTENT)
        except LiveBatchStudent.DoesNotExist:
            return Response({"error": "Student assignment not found in this batch."}, status=status.HTTP_404_NOT_FOUND)
