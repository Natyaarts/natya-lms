from rest_framework import viewsets, permissions, status as drf_status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.exceptions import PermissionDenied as DRFPermissionDenied
from .models import Course, Module, VideoLesson, Enrollment, LessonProgress, LiveClass, LiveBatch, LiveBatchStudent, TranslatedAudio, CourseInstructor, RecurrenceRule, TeacherAvailability, Attendance
from .serializers import CourseSerializer, ModuleSerializer, VideoLessonSerializer, LessonProgressSerializer, LiveClassSerializer, LiveBatchSerializer, LiveBatchStudentSerializer, TranslatedAudioSerializer, TranslatedAudioUploadSerializer, CourseInstructorSerializer, RecurrenceRuleSerializer, TeacherAvailabilitySerializer, AttendanceSerializer
from users.permissions import IsSuperAdminOrTeacherOrReadOnly  # noqa: F401 -- retained for any other reuse; Course/Module/Lesson now use IsSuperAdminOrCourseInstructorOrReadOnly below


class IsSuperAdminOrCourseInstructorOrReadOnly(permissions.BasePermission):
    """
    Phase 1: replaces IsSuperAdminOrTeacherOrReadOnly on Course/Module/
    VideoLesson so a teacher can only manage courses they are actually
    assigned to via CourseInstructor -- not every course in the system
    (the old class had no has_object_permission at all, so any is_teacher
    user could previously write to any course).

    Mentors are deliberately NOT granted write access here even if they
    hold a CourseInstructor row -- "should NOT automatically receive full
    teacher permissions" (see ARCHITECTURE_PROPOSAL.md Phase 1). Only
    TEACHER/ASSISTANT roles grant write access; MENTOR is read-only.

    Backward compatibility: a legacy teacher who predates CourseInstructor
    and only has the old "enrolled in their own course" relationship still
    gets write access via a fallback check, so no existing teacher account
    loses access because of this change.
    """
    WRITE_ROLES = (CourseInstructor.InstructorRole.TEACHER, CourseInstructor.InstructorRole.ASSISTANT)

    def has_permission(self, request, view):
        if request.method in permissions.SAFE_METHODS:
            return True
        if not (request.user and request.user.is_authenticated):
            return False
        if request.user.is_superuser or request.user.is_staff:
            return True
        # List-level create still requires an is_teacher account (matches
        # existing admin/courses/new UI gating); object-level checks below
        # narrow further to the actual assigned course for update/delete.
        return bool(getattr(request.user, 'is_teacher', False))

    def has_object_permission(self, request, view, obj):
        if request.method in permissions.SAFE_METHODS:
            return True
        user = request.user
        if user.is_superuser or user.is_staff:
            return True

        course = obj if isinstance(obj, Course) else getattr(obj, 'course', None)
        if course is None:
            course = getattr(getattr(obj, 'module', None), 'course', None)
        if course is None:
            return False

        if CourseInstructor.objects.filter(course=course, user=user, role__in=self.WRITE_ROLES).exists():
            return True

        # Legacy fallback for teachers not yet captured by a CourseInstructor
        # row (e.g. assigned before Phase 0's backfill ran).
        if getattr(user, 'is_teacher', False) and course.enrollments.filter(user=user).exists():
            return True

        return False


class IsSuperAdminOrCourseTeacherOrReadOnly(IsSuperAdminOrCourseInstructorOrReadOnly):
    """
    Stricter variant for Course-level writes (title/description/price/
    thumbnail/publish-status/delete): only the TEACHER role -- not
    ASSISTANT -- may edit course metadata. Assistants may still manage
    lesson/module/audio content (ModuleViewSet/VideoLessonViewSet keep
    using the base class with its wider WRITE_ROLES) but not the course's
    own settings. Everything else (superuser/staff bypass, legacy
    self-enrollment fallback, read access) is inherited unchanged.
    """
    WRITE_ROLES = (CourseInstructor.InstructorRole.TEACHER,)


class CourseViewSet(viewsets.ModelViewSet):
    queryset = Course.objects.all()
    serializer_class = CourseSerializer
    permission_classes = [IsSuperAdminOrCourseTeacherOrReadOnly]

    def get_queryset(self):
        user = self.request.user
        if user.is_authenticated and (user.is_superuser or user.is_staff):
            return Course.objects.all()
        if user.is_authenticated:
            from django.db.models import Q
            return Course.objects.filter(
                Q(is_published=True) |
                Q(enrollments__user=user) |   # student access, and legacy teacher self-enrollment
                Q(instructors__user=user)      # real CourseInstructor assignment (teacher/mentor/assistant)
            ).distinct()
        return Course.objects.filter(is_published=True)

    def get_serializer_context(self):
        """
        Course-content security follow-up (post-3.4.4): computed ONCE per
        request (list or detail, any number of courses/modules/lessons),
        not once per course/lesson -- ModuleSerializer reads this to decide
        whether to redact a course's lesson content. This is deliberately
        the only place that computes it; ModuleSerializer/VideoLessonSerializer
        never call the database themselves for this decision.
        """
        context = super().get_serializer_context()
        user = self.request.user
        if user.is_authenticated and (user.is_superuser or user.is_staff):
            context['bypass_content_lock'] = True
        else:
            from .services.access import accessible_course_ids_for_user, instructor_course_ids_for_user
            context['course_content_full_access_ids'] = (
                accessible_course_ids_for_user(user) | instructor_course_ids_for_user(user)
            )

        # Phase 4.3: same "compute once per request" precedent as the two
        # context keys above -- ModuleSerializer.get_assessments needs this
        # user's own attempts to show per-assessment status, and fetching
        # ALL of it here in one query (there's no realistic number of
        # attempts a single student accrues that makes this expensive) means
        # the cost is O(1) regardless of how many courses/modules/
        # assessments end up in the response, never one query per
        # assessment. Anonymous requests skip this entirely -- there is
        # nothing to look up and no reason to touch the DB for them.
        if user.is_authenticated:
            from collections import defaultdict
            from .models import AssessmentAttempt
            attempts_by_assessment = defaultdict(list)
            for attempt in AssessmentAttempt.objects.filter(student=user):
                attempts_by_assessment[attempt.assessment_id].append(attempt)
            context['student_attempts_by_assessment_id'] = attempts_by_assessment
        else:
            context['student_attempts_by_assessment_id'] = {}

        # Phase 4.5: same "compute once per request" precedent as the two
        # context keys above -- module/course completion (see
        # courses/services/completion.py) needs to know which of THIS
        # user's lessons are complete, and one query for the whole
        # request (regardless of how many courses/modules/lessons appear
        # in the response) is the only way to avoid a query per lesson.
        if user.is_authenticated:
            from .models import LessonProgress
            context['completed_lesson_ids'] = set(
                LessonProgress.objects.filter(user=user, completed=True).values_list('lesson_id', flat=True)
            )
        else:
            context['completed_lesson_ids'] = set()

        # Phase 4.7: same "compute once per request" precedent as the
        # three context keys above -- ModuleSerializer.get_assignments
        # needs this user's own submissions to show per-assignment status.
        if user.is_authenticated:
            from collections import defaultdict
            from .models import AssignmentSubmission
            submissions_by_assignment = defaultdict(list)
            for submission in AssignmentSubmission.objects.filter(student=user):
                submissions_by_assignment[submission.assignment_id].append(submission)
            context['student_submissions_by_assignment_id'] = submissions_by_assignment
        else:
            context['student_submissions_by_assignment_id'] = {}
        return context

    @action(detail=False, methods=['get'], permission_classes=[permissions.IsAuthenticated])
    def my_courses(self, request):
        # RBAC fix: this previously allowed AllowAny and silently fell back
        # to get_user_model().objects.first() for anonymous requests, which
        # returned a real (usually the earliest-created) user's accessible
        # courses and progress to anyone with no authentication at all.
        # Authentication is now required; there is no fallback user.
        user = request.user

        # Phase 3.4.4: was Enrollment-only; now also includes courses
        # granted by a currently-valid Subscription, via the same
        # centralized helper the `progress` action uses -- e.g. lets
        # CheckoutButton's existing "already have access -> show 'Go to
        # Course Dashboard'" check (it reads this exact endpoint) correctly
        # recognize subscription-covered courses too, with no frontend
        # change needed.
        from .services.access import accessible_course_ids_for_user
        accessible_courses = Course.objects.filter(id__in=accessible_course_ids_for_user(user))
        serializer = self.get_serializer(accessible_courses, many=True)
        return Response(serializer.data)

    @action(detail=True, methods=['get', 'post'], url_path='instructors')
    def instructors(self, request, pk=None):
        """
        GET: list instructors assigned to this course (any authenticated
        user who can already see the course -- i.e. get_object() succeeds).
        POST: assign an instructor -- admin/staff only. Backend-enforced;
        the frontend hiding the "Add Instructor" button is not security.
        """
        course = self.get_object()

        if request.method == 'GET':
            qs = CourseInstructor.objects.filter(course=course).select_related('user')
            serializer = CourseInstructorSerializer(qs, many=True)
            return Response(serializer.data)

        # POST
        if not (request.user.is_superuser or request.user.is_staff):
            return Response(
                {"detail": "Only admins can assign course instructors."},
                status=drf_status.HTTP_403_FORBIDDEN
            )

        # request.data may be a QueryDict (form/multipart POST); spreading it
        # with ** wraps every value in a list ("['MENTOR']" is not a valid
        # choice"). .copy() preserves single-value semantics for both
        # QueryDict and plain dict (JSON) request bodies.
        data = request.data.copy()
        data['course'] = course.id
        serializer = CourseInstructorSerializer(data=data)
        serializer.is_valid(raise_exception=True)

        from django.db import transaction
        with transaction.atomic():
            if serializer.validated_data.get('is_primary'):
                CourseInstructor.objects.filter(course=course, is_primary=True).update(is_primary=False)
            instance = serializer.save(course=course)

        # Phase 3.9: audit trail for course-instructor assignment (affects
        # both access and, via is_primary/commission_rate, revenue
        # attribution -- see finance/services.py). Never blocks the
        # assignment itself if logging somehow fails (AdminAuditLog.record
        # never raises).
        from users.models import AdminAuditLog
        AdminAuditLog.record(
            actor=request.user, action='COURSE_INSTRUCTOR_ASSIGNED', target_type='CourseInstructor', target_id=instance.id,
            description=f"Assigned {instance.user.username} as {instance.role} on course #{course.id} ({course.title}).",
            metadata={'course_id': course.id, 'user_id': instance.user_id, 'role': instance.role, 'is_primary': instance.is_primary},
        )

        return Response(CourseInstructorSerializer(instance).data, status=drf_status.HTTP_201_CREATED)

    @action(detail=True, methods=['delete'], url_path=r'instructors/(?P<instructor_id>[^/.]+)')
    def remove_instructor(self, request, pk=None, instructor_id=None):
        course = self.get_object()
        if not (request.user.is_superuser or request.user.is_staff):
            return Response(
                {"detail": "Only admins can remove course instructors."},
                status=drf_status.HTTP_403_FORBIDDEN
            )
        try:
            ci = CourseInstructor.objects.get(pk=instructor_id, course=course)
        except (CourseInstructor.DoesNotExist, ValueError):
            return Response({"error": "Instructor assignment not found for this course."}, status=drf_status.HTTP_404_NOT_FOUND)

        removed_user_id, removed_role = ci.user_id, ci.role
        ci.delete()

        from users.models import AdminAuditLog
        AdminAuditLog.record(
            actor=request.user, action='COURSE_INSTRUCTOR_REMOVED', target_type='CourseInstructor', target_id=instructor_id,
            description=f"Removed instructor assignment (user #{removed_user_id}, role={removed_role}) from course #{course.id} ({course.title}).",
            metadata={'course_id': course.id, 'user_id': removed_user_id, 'role': removed_role},
        )

        return Response(status=drf_status.HTTP_204_NO_CONTENT)

class ModuleViewSet(viewsets.ModelViewSet):
    """
    Course-content security follow-up (post-3.4.4): this viewset's GET
    (list/retrieve) was previously fully unrestricted for ANY request --
    IsSuperAdminOrCourseInstructorOrReadOnly's has_permission() returns
    True unconditionally for SAFE_METHODS, and there was no get_queryset()
    override at all, so no filtering or even authentication was applied.
    Confirmed via a repo-wide frontend search that nothing legitimate
    reads through this endpoint (the admin course editor and the student
    learn page both read course/module/lesson data exclusively via the
    nested CourseSerializer response from CourseViewSet; this viewset is
    only ever POSTed/PATCHed to for authoring) -- so restricting GET to
    authoring access closes the gap with no known regression. Writes are
    unaffected: permission_classes below is untouched, and get_queryset()
    still includes every course a legitimate instructor (or the legacy
    self-enrolled-teacher fallback) is assigned to, so PATCH/DELETE via
    get_object() continues to work exactly as before for them.
    """
    queryset = Module.objects.all()
    serializer_class = ModuleSerializer
    permission_classes = [IsSuperAdminOrCourseInstructorOrReadOnly]

    def get_queryset(self):
        user = self.request.user
        if user.is_authenticated and (user.is_superuser or user.is_staff):
            return Module.objects.all()
        if not user.is_authenticated:
            return Module.objects.none()
        from .services.access import instructor_course_ids_for_user
        return Module.objects.filter(course_id__in=instructor_course_ids_for_user(user))

    def get_serializer_context(self):
        # Anyone reaching the serializer here already passed the
        # authoring-only queryset above -- always full content, no
        # per-course lock decision needed (unlike CourseViewSet, which
        # serves both owners and non-owners from the same queryset).
        context = super().get_serializer_context()
        context['bypass_content_lock'] = True
        return context

class VideoLessonViewSet(viewsets.ModelViewSet):
    """
    Course-content security follow-up (post-3.4.4): same reasoning as
    ModuleViewSet above -- GET here was previously fully unrestricted
    (the pre-existing get_queryset() override read `request.user` but
    never actually used it, always returning every VideoLesson in the
    database regardless of auth or course publish state -- confirmed by
    reading the code before this change, not assumed). Restricted to
    authoring access for the default list/retrieve/write actions, with
    ONE deliberate exception: the `progress` action (used by ENROLLED/
    SUBSCRIBED STUDENTS, not instructors) needs get_object() to find ANY
    lesson -- its own body already calls user_has_course_access() to do
    the actual authorization (see the `progress` method below, unchanged
    by this edit) -- so it gets the unrestricted queryset, exactly as
    before.
    """
    queryset = VideoLesson.objects.all()
    serializer_class = VideoLessonSerializer
    permission_classes = [IsSuperAdminOrCourseInstructorOrReadOnly]

    def get_queryset(self):
        user = self.request.user
        if user.is_authenticated and (user.is_superuser or user.is_staff):
            return VideoLesson.objects.all()
        if self.action == 'progress':
            return VideoLesson.objects.all()
        if not user.is_authenticated:
            return VideoLesson.objects.none()
        from .services.access import instructor_course_ids_for_user
        course_ids = instructor_course_ids_for_user(user)
        return VideoLesson.objects.filter(module__course_id__in=course_ids)

    def get_serializer_context(self):
        # Same reasoning as ModuleViewSet.get_serializer_context() -- the
        # queryset above is already the real gate for list/retrieve/write;
        # `progress` doesn't use VideoLessonSerializer at all, so this
        # flag is inert for it either way.
        context = super().get_serializer_context()
        context['bypass_content_lock'] = True
        return context

    @action(detail=True, methods=['post'])
    def generate_ai_audio(self, request, pk=None):
        from django.db import transaction
        from .tasks import generate_dubbed_audio_task
        from .models import TranslatedAudio

        lesson = self.get_object()

        if not lesson.transcript and not lesson.timed_transcript:
            return Response(
                {
                    "error": "Please fill in the 'Timing for Speaking' section (or a transcript) before generating AI Audio."
                },
                status=400,
            )

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

    @action(detail=True, methods=['post'], url_path='audio')
    def upload_audio(self, request, pk=None):
        """
        Manual audio-track upload for a lesson (POST /api/courses/lessons/{id}/audio/).

        This is the primary, supported workflow for translated/dubbed lesson
        audio: the client produces the audio externally (AI service, human
        voice artist, studio, etc.) and uploads the resulting file here. The
        LMS does not generate or care how the audio was produced -- it just
        stores it against (lesson, language_code), replacing any existing
        track for that language rather than creating a duplicate.

        This never triggers the AI dubbing pipeline (generate_ai_audio /
        generate_dubbed_audio_task) -- that remains a separate, unrelated
        workflow kept only for backward compatibility.
        """
        lesson = self.get_object()

        upload_serializer = TranslatedAudioUploadSerializer(data=request.data)
        upload_serializer.is_valid(raise_exception=True)
        validated = upload_serializer.validated_data

        language_code = validated['language_code']
        language_name = validated['language_name']
        audio_file = validated['audio_file']

        audio_obj, created = TranslatedAudio.objects.get_or_create(
            lesson=lesson,
            language_code=language_code,
            defaults={'language_name': language_name, 'status': 'completed'}
        )

        if not created:
            # Replacing an existing track for this language: drop the old
            # file from storage before attaching the new one.
            if audio_obj.audio_file:
                try:
                    audio_obj.audio_file.delete(save=False)
                except Exception:
                    pass
            audio_obj.language_name = language_name

        audio_obj.audio_file = audio_file
        audio_obj.status = 'completed'  # No background processing for manual uploads.
        audio_obj.save()

        from .services.audio_duration import check_duration_mismatch
        duration_warning = check_duration_mismatch(lesson, audio_obj)

        response_data = TranslatedAudioSerializer(audio_obj).data
        if duration_warning:
            response_data['duration_warning'] = duration_warning

        return Response(
            response_data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK
        )

    @action(detail=True, methods=['delete'], url_path=r'audio/(?P<audio_id>[^/.]+)')
    def delete_audio(self, request, pk=None, audio_id=None):
        """Delete a single translated audio track (DELETE /api/courses/lessons/{id}/audio/{audio_id}/)."""
        lesson = self.get_object()
        try:
            audio_obj = TranslatedAudio.objects.get(pk=audio_id, lesson=lesson)
        except (TranslatedAudio.DoesNotExist, ValueError):
            return Response({"error": "Audio track not found for this lesson."}, status=status.HTTP_404_NOT_FOUND)

        if audio_obj.audio_file:
            try:
                audio_obj.audio_file.delete(save=False)
            except Exception:
                pass
        audio_obj.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=['get', 'post', 'patch'], url_path='progress', permission_classes=[permissions.IsAuthenticated])
    def progress(self, request, pk=None):
        from django.utils import timezone

        lesson = self.get_object()
        user = request.user

        # Access control check: permanent Enrollment OR a currently-valid
        # Subscription covering this course (unless superuser). Phase
        # 3.4.4: was a raw Enrollment-only check; now goes through the
        # centralized helper so subscription access is additive here too,
        # without duplicating the subscription-validity logic.
        from .services.access import user_has_course_access
        has_access = user_has_course_access(user, lesson.module.course)
        if not has_access and not user.is_superuser:
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

            # Phase 4.6: eager certificate-issuance trigger -- fires
            # after the save above has committed (LessonProgress isn't
            # wrapped in an explicit transaction.atomic() here, so this
            # read of the just-saved row is never "in-flight" data).
            # Swallows its own errors entirely (see
            # maybe_issue_certificate_and_notify's own docstring) --
            # never breaks this response. A lazy fallback
            # (CertificateViewSet.by_course) still covers any missed case.
            if progress_obj.completed:
                from .services.certificates import maybe_issue_certificate_and_notify
                maybe_issue_certificate_and_notify(lesson.module.course, user)

            serializer = LessonProgressSerializer(progress_obj)
            return Response(serializer.data)


# =============================================================================
# Phase 4.2: learner-facing assessment attempt/scoring endpoints.
#
# Course-access is checked via the SAME centralized
# courses.services.access.user_has_course_access used by `progress` above
# -- no separate/duplicate access system. Ownership of an attempt is
# enforced by scoping every queryset to `student=request.user` (so a
# non-owned attempt id 404s exactly like a non-existent one, matching
# this codebase's established "Subscription not found"-style precedent
# in orders/views.py rather than a 403 that would confirm the id exists).
# =============================================================================

from decimal import Decimal

from django.db import transaction
from django.db.models import Prefetch
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import mixins
from rest_framework.pagination import PageNumberPagination

from .models import Assessment, AssessmentAttempt, AssessmentAnswerOption
from .serializers import (
    SafeAssessmentSerializer, SafeQuestionSerializer, SafeQuestionOptionSerializer, AssessmentAttemptListSerializer,
    ReviewQuestionOptionSerializer,
)
from .services.access import user_has_course_access
from .services.assessment_scoring import AnswerValidationError, score_attempt, validate_and_normalize_answers


def _attempt_deadline(attempt):
    """None if the assessment has no time limit."""
    minutes = attempt.assessment.time_limit_minutes
    if minutes is None:
        return None
    from datetime import timedelta
    return attempt.started_at + timedelta(minutes=minutes)


def _expire_if_overdue(attempt):
    """
    Lazy expiry check -- there is no background job flipping IN_PROGRESS
    attempts to TIMED_OUT the instant their deadline passes; instead,
    every read/write path that touches an attempt calls this first, so an
    overdue attempt is always corrected before it's acted on. Returns the
    (possibly updated) attempt.
    """
    if attempt.status != AssessmentAttempt.Status.IN_PROGRESS:
        return attempt
    deadline = _attempt_deadline(attempt)
    if deadline is not None and timezone.now() > deadline:
        attempt.status = AssessmentAttempt.Status.TIMED_OUT
        attempt.save(update_fields=['status', 'updated_at'])
    return attempt


def _serialize_in_progress_attempt(attempt):
    questions = attempt.assessment.questions.prefetch_related('options').all()
    return {
        "attempt_id": attempt.id,
        "attempt_number": attempt.attempt_number,
        "status": attempt.status,
        "started_at": attempt.started_at,
        "deadline": _attempt_deadline(attempt),
        "assessment": SafeAssessmentSerializer(attempt.assessment).data,
        "questions": SafeQuestionSerializer(questions, many=True).data,
    }


def _build_review_questions(attempt):
    """
    Phase 4.4 + CORRECTION. The per-question review list for a
    SUBMITTED/TIMED_OUT attempt.

    question_text/question_type/order/explanation are live -- descriptive
    facts about the question, never part of the frozen result (unchanged
    from the original Phase 4.4 design; the CORRECTION did not touch
    these, only marks/option-correctness/option-text, per the audit).

    For a question that WAS answered (has an AssessmentAnswer row --
    every question in the assessment at submission time, for a SUBMITTED
    attempt): "marks" is the answer's frozen `marks_possible`, and
    "options" comes from that answer's AssessmentAnswerOptionSnapshot
    rows -- frozen option_text/order/was_correct, never the live
    QuestionOption. This is what makes the review immune to a later
    admin editing Question.marks, QuestionOption.is_correct, or
    QuestionOption.option_text (see the Phase 4.4 CORRECTION report).

    A TIMED_OUT attempt has NO AssessmentAnswer rows at all (nothing was
    ever submitted under this project's final-submission-only design) --
    there is no historical snapshot to show, so these questions fall back
    to the CURRENT live question/options with NO correctness reveal
    (SafeQuestionOptionSerializer's shape -- no is_correct_answer key at
    all), never deriving anything from live QuestionOption.is_correct.
    This is a deliberate behavior change from the original Phase 4.4
    design (which showed live is_correct_answer for a timed-out review);
    the CORRECTION's "never show live correctness in review" rule applies
    here too, and a timed-out attempt simply has nothing frozen to show.

    Fixed query count regardless of question count (5, 20, or 50
    questions): one for the assessment's questions, one for their
    options (both via prefetch_related, used only for the TIMED_OUT
    fallback and marks display), one for this attempt's answers, one for
    those answers' selected-option links, one for those answers' option
    snapshots -- never one query per question.
    """
    questions = attempt.assessment.questions.prefetch_related('options').all()
    answers = attempt.answers.prefetch_related(
        Prefetch('option_links', queryset=AssessmentAnswerOption.objects.select_related('option')),
        'option_snapshots',
    )
    answers_by_question_id = {a.question_id: a for a in answers}

    review = []
    for question in questions:
        answer = answers_by_question_id.get(question.id)
        if answer is not None:
            selected_option_ids = [link.option_id for link in answer.option_links.all()]
            options_data = ReviewQuestionOptionSerializer(answer.option_snapshots.all(), many=True).data
            marks_shown = answer.marks_possible
            answered_correctly = answer.is_correct
            marks_awarded = answer.marks_awarded
        else:
            # TIMED_OUT, never scored -- no snapshot exists; show the
            # live question with NO correctness reveal at all.
            selected_option_ids = []
            options_data = SafeQuestionOptionSerializer(question.options.all(), many=True).data
            marks_shown = question.marks
            answered_correctly = False
            marks_awarded = Decimal("0.00")

        review.append({
            "id": question.id,
            "question_text": question.question_text,
            "question_type": question.question_type,
            "order": question.order,
            "marks": marks_shown,
            "explanation": question.explanation,
            "options": options_data,
            "selected_option_ids": selected_option_ids,
            "answered_correctly": answered_correctly,
            "marks_awarded": marks_awarded,
        })
    return review, answers_by_question_id


def _serialize_finished_attempt(attempt, include_review=True):
    """
    Phase 4.4: extended with the assessment summary, frozen total_marks,
    and (by default) the full per-question review -- see
    _build_review_questions for why this is safe to include unconditionally
    here: this function is only ever called for a SUBMITTED or TIMED_OUT
    attempt (never IN_PROGRESS -- that path uses
    _serialize_in_progress_attempt, which has no correctness data at
    all), so "answer keys must never be exposed before submission" is
    enforced by WHICH function gets called, not by a flag on this one.
    `include_review=False` exists only for the `submit` response, which
    doesn't need to re-fetch what it just built in the same request.
    """
    data = {
        "attempt_id": attempt.id,
        "attempt_number": attempt.attempt_number,
        "status": attempt.status,
        "started_at": attempt.started_at,
        "submitted_at": attempt.submitted_at,
        "assessment": SafeAssessmentSerializer(attempt.assessment).data,
        "score": attempt.score,
        "percentage": attempt.percentage,
        "passed": attempt.passed,
        "total_marks": attempt.total_marks,
    }
    if not include_review:
        return data

    review, answers_by_question_id = _build_review_questions(attempt)
    data["questions"] = review
    # Frozen for a SUBMITTED attempt (one AssessmentAnswer row exists per
    # question that existed in the assessment at submission time, and
    # PROTECT means that row can never silently disappear); a TIMED_OUT
    # attempt has none, so this falls back to the assessment's CURRENT
    # question count -- purely informational, since nothing was ever
    # actually scored for a timed-out attempt.
    data["question_count"] = len(answers_by_question_id) or attempt.assessment.questions.count()
    data["correct_count"] = sum(1 for a in answers_by_question_id.values() if a.is_correct)
    return data


class AssessmentViewSet(mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    """
    No list on purpose -- there is no generic "browse assessments" API in
    this phase (Django admin remains the authoring surface, per Phase
    4.1). `retrieve` DOES exist, added specifically because the frontend
    "start assessment" screen (Phase 4.2's own explicit product
    requirement) needs to show title/instructions/passing_percentage/
    max_attempts/time_limit_minutes BEFORE the student commits to
    starting -- and `start` can't serve that purpose itself, since
    calling it creates the attempt and starts the time-limit clock. This
    is intentionally the same SafeAssessmentSerializer data `start`
    already returns, with zero side effects, and the exact same
    access/published checks -- not a general-purpose browsing endpoint.
    """
    queryset = Assessment.objects.all()
    permission_classes = [permissions.IsAuthenticated]

    def get_throttles(self):
        # General API rate limiting gap fix -- only `start` gets the
        # dedicated scope; `retrieve` (the preview screen, read-only) stays
        # on the general 'user' baseline.
        if self.action == 'start':
            return [AssessmentStartRateThrottle()]
        return super().get_throttles()

    def retrieve(self, request, pk=None):
        assessment, error_response = self._get_accessible_published_assessment(request, pk)
        if error_response:
            return error_response

        existing = list(AssessmentAttempt.objects.filter(assessment=assessment, student=request.user))
        active = next((a for a in existing if a.status == AssessmentAttempt.Status.IN_PROGRESS), None)
        if active is not None:
            active = _expire_if_overdue(active)
            if active.status != AssessmentAttempt.Status.IN_PROGRESS:
                active = None

        data = SafeAssessmentSerializer(assessment).data
        data["question_count"] = assessment.questions.count()
        data["attempts_used"] = len(existing)
        data["attempts_remaining"] = max(assessment.max_attempts - len(existing), 0)
        data["active_attempt_id"] = active.id if active else None
        data["can_start"] = active is not None or len(existing) < assessment.max_attempts
        return Response(data)

    def _get_accessible_published_assessment(self, request, pk):
        """Shared by retrieve/start: 404 for unpublished/nonexistent (never
        confirms existence), 403 for published-but-no-course-access."""
        try:
            assessment = Assessment.objects.select_related('module__course').get(pk=pk)
        except Assessment.DoesNotExist:
            return None, Response({"error": "Assessment not found."}, status=drf_status.HTTP_404_NOT_FOUND)

        user = request.user
        if not assessment.is_published and not user.is_superuser:
            return None, Response({"error": "Assessment not found."}, status=drf_status.HTTP_404_NOT_FOUND)

        course = assessment.module.course
        if not user_has_course_access(user, course) and not user.is_superuser:
            return None, Response({"error": "You do not have access to this course."}, status=drf_status.HTTP_403_FORBIDDEN)

        return assessment, None

    @action(detail=True, methods=['post'], url_path='start')
    def start(self, request, pk=None):
        assessment, error_response = self._get_accessible_published_assessment(request, pk)
        if error_response:
            return error_response
        user = request.user

        if not assessment.questions.exists():
            return Response({"error": "This assessment has no questions yet."}, status=drf_status.HTTP_400_BAD_REQUEST)

        with transaction.atomic():
            # Lock any existing rows for this (student, assessment) pair
            # before counting/creating, so two concurrent `start` calls
            # can't both compute the same attempt_number or both slip
            # past a max_attempts check that's about to be exceeded --
            # the UniqueConstraints on the model are the final backstop.
            existing = list(
                AssessmentAttempt.objects.select_for_update().filter(assessment=assessment, student=user)
            )

            active = next((a for a in existing if a.status == AssessmentAttempt.Status.IN_PROGRESS), None)
            if active is not None:
                active = _expire_if_overdue(active)
                if active.status == AssessmentAttempt.Status.IN_PROGRESS:
                    return Response(_serialize_in_progress_attempt(active), status=drf_status.HTTP_200_OK)
                # else: it just expired -- fall through and (if attempts remain) start a new one.
                existing = list(
                    AssessmentAttempt.objects.select_for_update().filter(assessment=assessment, student=user)
                )

            if len(existing) >= assessment.max_attempts:
                return Response(
                    {"error": f"Maximum attempts ({assessment.max_attempts}) reached for this assessment."},
                    status=drf_status.HTTP_403_FORBIDDEN,
                )

            attempt = AssessmentAttempt.objects.create(
                assessment=assessment, student=user, attempt_number=len(existing) + 1,
            )

        return Response(_serialize_in_progress_attempt(attempt), status=drf_status.HTTP_201_CREATED)


class AssessmentAttemptViewSet(mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    """
    retrieve: GET /assessment-attempts/<id>/ -- owner only.
    submit:   POST /assessment-attempts/<id>/submit/
    my:       GET /assessment-attempts/my/ -- this student's own history.

    No list/create/update/destroy -- attempts are only ever created via
    AssessmentViewSet.start and finalized via `submit`.
    """
    permission_classes = [permissions.IsAuthenticated]

    def get_throttles(self):
        # General API rate limiting gap fix -- only `submit` gets the
        # dedicated scope; `retrieve`/`my` (read-only) stay on the general
        # 'user' baseline.
        if self.action == 'submit':
            return [AssessmentSubmitRateThrottle()]
        return super().get_throttles()

    def get_queryset(self):
        # Ownership boundary: every lookup is pre-scoped to the
        # requesting user, so a non-owned id 404s like a nonexistent one.
        return AssessmentAttempt.objects.filter(student=self.request.user).select_related('assessment')

    def retrieve(self, request, pk=None):
        attempt = self.get_object()
        attempt = _expire_if_overdue(attempt)
        if attempt.status == AssessmentAttempt.Status.IN_PROGRESS:
            return Response(_serialize_in_progress_attempt(attempt))
        return Response(_serialize_finished_attempt(attempt))

    @action(detail=True, methods=['post'], url_path='submit')
    def submit(self, request, pk=None):
        with transaction.atomic():
            attempt = get_object_or_404(
                AssessmentAttempt.objects.select_for_update().filter(student=request.user), pk=pk
            )
            attempt = _expire_if_overdue(attempt)

            if attempt.status == AssessmentAttempt.Status.TIMED_OUT:
                return Response(
                    {"error": "This attempt has expired and cannot be submitted.", "status": attempt.status},
                    status=drf_status.HTTP_400_BAD_REQUEST,
                )
            if attempt.status != AssessmentAttempt.Status.IN_PROGRESS:
                # Duplicate submission -- idempotent/safe rejection, not
                # a re-score. The first submission's result stands.
                return Response(
                    {"error": "This attempt has already been submitted.", "status": attempt.status},
                    status=drf_status.HTTP_409_CONFLICT,
                )

            try:
                normalized, questions_by_id = validate_and_normalize_answers(attempt.assessment, request.data.get('answers', []))
            except AnswerValidationError as e:
                return Response({"error": str(e)}, status=drf_status.HTTP_400_BAD_REQUEST)

            score_attempt(attempt, normalized, questions=questions_by_id.values())
            attempt.status = AssessmentAttempt.Status.SUBMITTED
            attempt.submitted_at = timezone.now()
            attempt.save()

        # Phase 4.6: eager certificate-issuance trigger -- deliberately
        # OUTSIDE the `with transaction.atomic():` block above, so it only
        # ever runs after this attempt's passing result has actually
        # committed (Phase 4.6 Section 16's explicit "avoid race
        # conditions... before the transaction commits"). Never breaks
        # this response; see maybe_issue_certificate_and_notify's own
        # docstring.
        if attempt.passed:
            from .services.certificates import maybe_issue_certificate_and_notify
            maybe_issue_certificate_and_notify(attempt.assessment.module.course, request.user)

        return Response(_serialize_finished_attempt(attempt))

    @action(detail=False, methods=['get'], url_path='my')
    def my(self, request):
        queryset = self.get_queryset().order_by('-started_at')
        assessment_id = request.query_params.get('assessment_id')
        if assessment_id is not None:
            try:
                queryset = queryset.filter(assessment_id=int(assessment_id))
            except (TypeError, ValueError):
                return Response({"error": "assessment_id must be an integer."}, status=drf_status.HTTP_400_BAD_REQUEST)

        paginator = AssessmentAttemptResultsSetPagination()
        page = paginator.paginate_queryset(queryset, request)
        serializer = AssessmentAttemptListSerializer(page, many=True)
        return paginator.get_paginated_response(serializer.data)


class AssessmentAttemptResultsSetPagination(PageNumberPagination):
    page_size = 10
    page_size_query_param = 'page_size'
    max_page_size = 100


from rest_framework import viewsets
from rest_framework.pagination import PageNumberPagination
from users.permissions import IsSuperAdminOrAdmin
from .models import Enrollment
from .serializers import AdminEnrollmentSerializer

class EnrollmentResultsSetPagination(PageNumberPagination):
    page_size = 10
    page_size_query_param = 'page_size'
    max_page_size = 100

class AdminEnrollmentViewSet(viewsets.ModelViewSet):
    queryset = Enrollment.objects.all().select_related('user', 'course').order_by('-enrolled_at')
    serializer_class = AdminEnrollmentSerializer
    permission_classes = [IsSuperAdminOrAdmin]
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
    """
    Phase 1: extended to also recognize is_mentor, alongside the existing
    (already-correct) per-instructor scoping via LiveBatch/LiveClass.instructor
    -- a real FK, not a hack. This class already prevented a teacher from
    accessing another teacher's class; mentors now get the same treatment
    for classes they are actually assigned to as instructor.
    """
    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        if request.method in permissions.SAFE_METHODS:
            return True
        return (
            request.user.is_superuser or request.user.is_staff
            or getattr(request.user, 'is_teacher', False)
            or getattr(request.user, 'is_mentor', False)
        )

    def has_object_permission(self, request, view, obj):
        # Admins have full access
        if request.user.is_superuser or request.user.is_staff:
            return True
        # If batch is NULL, students/teachers/mentors have NO access (legacy/orphaned isolation)
        if not obj.batch:
            return False
        # Teachers/mentors can only access classes where they are the batch instructor
        if getattr(request.user, 'is_teacher', False) or getattr(request.user, 'is_mentor', False):
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


def _schedule_class_reminders(live_class):
    """
    Phase 2: schedules all three reminders (24h/1h/15m before) for a
    LiveClass, reusing the single existing `send_class_reminder` Celery task
    (parameterized by reminder_type) -- no second notification/scheduling
    system. A reminder whose ETA has already passed (e.g. a class created
    less than 24h out) is simply not scheduled for that window; the task
    itself also no-ops if the class was cancelled/rescheduled by the time it fires.
    """
    from .tasks import send_class_reminder
    from django.utils import timezone
    import datetime

    now = timezone.now()
    start_timestamp = int(live_class.scheduled_start.timestamp())
    for reminder_type, delta in (('24h', datetime.timedelta(hours=24)), ('1h', datetime.timedelta(hours=1)), ('15m', datetime.timedelta(minutes=15))):
        eta = live_class.scheduled_start - delta
        if eta <= now:
            continue
        send_class_reminder.apply_async(
            args=[live_class.id, start_timestamp, reminder_type],
            eta=eta
        )


def _generate_occurrence_datetimes(start_dt, frequency, weekdays, end_date, occurrence_count):
    """
    Phase 2: pure date-math for a recurring series -- returns a list of
    datetimes (same time-of-day as start_dt) per the rule, capped at
    RecurrenceRule.MAX_OCCURRENCES as a safety bound.
    """
    import datetime as dt

    limit = RecurrenceRule.MAX_OCCURRENCES
    if occurrence_count:
        limit = min(int(occurrence_count), limit)

    if frequency == RecurrenceRule.Frequency.ONE_TIME:
        return [start_dt]

    if frequency == RecurrenceRule.Frequency.DAILY:
        dates = []
        cur = start_dt
        while len(dates) < limit:
            if end_date and cur.date() > end_date:
                break
            dates.append(cur)
            cur = cur + dt.timedelta(days=1)
        return dates

    if frequency == RecurrenceRule.Frequency.WEEKLY:
        target_weekdays = weekdays or [start_dt.weekday()]
        dates = []
        cur = start_dt
        scanned = 0
        max_scan_days = 400  # safety bound (~1 year) against an unreachable weekday combo with no end_date
        while len(dates) < limit and scanned < max_scan_days:
            if cur.weekday() in target_weekdays:
                if end_date and cur.date() > end_date:
                    break
                dates.append(cur)
            cur = cur + dt.timedelta(days=1)
            scanned += 1
        return dates

    return [start_dt]


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
        elif getattr(user, 'is_teacher', False) or getattr(user, 'is_mentor', False):
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

        # Filter by assigned student (admin/staff use, e.g. viewing a
        # specific student's session history -- students themselves are
        # already scoped to only their own classes above, this just lets an
        # admin narrow the "see everything" queryset the same way `instructor` does).
        student_id = self.request.query_params.get('student')
        if student_id:
            queryset = queryset.filter(batch__students__student_id=student_id).distinct()

        return queryset

    def create(self, request, *args, **kwargs):
        """
        Phase 2: a `recurrence` block in the payload (frequency/weekdays/
        end_date/occurrence_count) creates a series instead of a single
        class. Reuses the same per-occurrence validation (conflict/
        availability/past-date checks all run individually per date) and
        the same notification/reminder dispatch as a normal single create --
        nothing about the underlying LiveClass creation path is duplicated
        or rebuilt, just looped inside one transaction.
        """
        recurrence_payload = request.data.get('recurrence')
        if not recurrence_payload:
            return super().create(request, *args, **kwargs)

        from django.db import transaction
        from django.utils.dateparse import parse_datetime, parse_date

        base_data = {k: v for k, v in request.data.items() if k != 'recurrence'}
        scheduled_start_str = base_data.get('scheduled_start')
        start_dt = parse_datetime(scheduled_start_str) if scheduled_start_str else None
        if not start_dt:
            return Response({"scheduled_start": ["A valid scheduled_start is required for a recurring series."]}, status=drf_status.HTTP_400_BAD_REQUEST)

        frequency = recurrence_payload.get('frequency', RecurrenceRule.Frequency.ONE_TIME)
        weekdays = recurrence_payload.get('weekdays') or []
        end_date_str = recurrence_payload.get('end_date')
        end_date = parse_date(end_date_str) if end_date_str else None
        occurrence_count = recurrence_payload.get('occurrence_count')

        occurrence_dates = _generate_occurrence_datetimes(start_dt, frequency, weekdays, end_date, occurrence_count)
        if not occurrence_dates:
            return Response({"recurrence": ["No occurrences could be generated from the given rule."]}, status=drf_status.HTTP_400_BAD_REQUEST)

        created_instances = []
        with transaction.atomic():
            rule = RecurrenceRule.objects.create(
                frequency=frequency, weekdays=weekdays, end_date=end_date,
                occurrence_count=occurrence_count, created_by=request.user
            )
            for occ_dt in occurrence_dates:
                occ_data = dict(base_data)
                occ_data['scheduled_start'] = occ_dt.isoformat()
                serializer = self.get_serializer(data=occ_data)
                serializer.is_valid(raise_exception=True)  # any failure rolls back the whole series
                instance = serializer.save()
                instance.recurrence_rule = rule
                instance.save(update_fields=['recurrence_rule'])
                created_instances.append(instance)

        for instance in created_instances:
            self._dispatch_scheduled_notifications(instance)

        out_serializer = self.get_serializer(created_instances, many=True)
        return Response(out_serializer.data, status=drf_status.HTTP_201_CREATED)

    def _dispatch_scheduled_notifications(self, live_class):
        from django.db import transaction
        from notifications.services import LiveClassNotificationService

        def dispatch():
            LiveClassNotificationService.notify_scheduled(live_class)
            _schedule_class_reminders(live_class)

        connection = transaction.get_connection()
        if connection.in_atomic_block:
            transaction.on_commit(dispatch)
        else:
            dispatch()

    def perform_create(self, serializer):
        live_class = serializer.save()
        self._dispatch_scheduled_notifications(live_class)

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
        live_class.cancellation_reason = request.data.get('reason', '')
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

    @action(detail=True, methods=['post'], url_path='cancel-series')
    def cancel_series(self, request, pk=None):
        """
        Phase 2: cancels every still-SCHEDULED occurrence sharing this
        class's RecurrenceRule (including this one). One-off classes (no
        recurrence_rule) simply cancel themselves, same as `cancel`.
        """
        from django.db import transaction
        from notifications.services import LiveClassNotificationService

        live_class = self.get_object()
        reason = request.data.get('reason', '')

        if not live_class.recurrence_rule_id:
            siblings = LiveClass.objects.filter(pk=live_class.pk)
        else:
            siblings = LiveClass.objects.filter(
                recurrence_rule_id=live_class.recurrence_rule_id,
                status=LiveClass.ClassStatus.SCHEDULED
            )

        cancelled = []
        with transaction.atomic():
            for occ in siblings.select_for_update():
                if occ.status != LiveClass.ClassStatus.SCHEDULED:
                    continue
                occ.status = LiveClass.ClassStatus.CANCELLED
                occ.cancellation_reason = reason
                occ.save()
                cancelled.append(occ)

        def dispatch():
            for occ in cancelled:
                LiveClassNotificationService.notify_cancelled(occ)

        connection = transaction.get_connection()
        if connection.in_atomic_block:
            transaction.on_commit(dispatch)
        else:
            dispatch()

        serializer = self.get_serializer(cancelled, many=True)
        return Response(serializer.data)

    @action(detail=True, methods=['post'])
    def reschedule(self, request, pk=None):
        from django.db import transaction
        from notifications.services import LiveClassNotificationService

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
                    _schedule_class_reminders(updated_class)
                transaction.on_commit(dispatch_reschedule)

        return Response(serializer.data)

    @action(detail=False, methods=['get'])
    def today(self, request):
        import datetime

        today_local = timezone.localdate()
        start_of_day = timezone.make_aware(datetime.datetime.combine(today_local, datetime.time.min))
        end_of_day = timezone.make_aware(datetime.datetime.combine(today_local, datetime.time.max))

        queryset = self.filter_queryset(self.get_queryset())
        queryset = queryset.filter(
            scheduled_start__gte=start_of_day, scheduled_start__lte=end_of_day
        ).order_by('scheduled_start')

        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)

        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data)

    @action(detail=True, methods=['get', 'post'], url_path='attendance')
    def attendance(self, request, pk=None):
        """
        GET: instructor/admin see every student's record; a student sees
        only their own row (backend-enforced, not just hidden client-side).
        POST: instructor/admin only, mark one or many records (a plain
        object or a list of {student, status, notes}); upserts so re-marking
        is safe.
        """
        live_class = self.get_object()
        user = request.user
        is_manager = bool(user.is_superuser or user.is_staff or (live_class.batch and live_class.batch.instructor == user))

        if request.method == 'GET':
            qs = Attendance.objects.filter(live_class=live_class).select_related('student')
            if not is_manager:
                qs = qs.filter(student=user)
            return Response(AttendanceSerializer(qs, many=True).data)

        if not is_manager:
            return Response({"detail": "Only the instructor or admin can mark attendance."}, status=drf_status.HTTP_403_FORBIDDEN)

        records = request.data if isinstance(request.data, list) else [request.data]
        results = []
        for rec in records:
            serializer = AttendanceSerializer(data=rec, context={'live_class': live_class})
            serializer.is_valid(raise_exception=True)
            student = serializer.validated_data['student']
            obj, _ = Attendance.objects.update_or_create(
                live_class=live_class, student=student,
                defaults={
                    'status': serializer.validated_data.get('status', Attendance.Status.ABSENT),
                    'marked_by': user,
                    'notes': serializer.validated_data.get('notes', ''),
                }
            )
            results.append(AttendanceSerializer(obj).data)
        return Response(results, status=drf_status.HTTP_200_OK)

    @action(detail=True, methods=['post'], url_path='recording')
    def recording(self, request, pk=None):
        """Attach a recording URL -- instructor/admin only. Notifies assigned students, reusing NotificationService."""
        live_class = self.get_object()
        user = request.user
        if not (user.is_superuser or user.is_staff or (live_class.batch and live_class.batch.instructor == user)):
            return Response({"detail": "Only the instructor or admin can attach a recording."}, status=drf_status.HTTP_403_FORBIDDEN)

        recording_url = request.data.get('recording_url')
        if not recording_url:
            return Response({"error": "recording_url is required."}, status=drf_status.HTTP_400_BAD_REQUEST)

        live_class.recording_url = recording_url
        live_class.recording_uploaded_at = timezone.now()
        live_class.save(update_fields=['recording_url', 'recording_uploaded_at'])

        from notifications.services import NotificationService
        from notifications.models import NotificationType
        if live_class.batch:
            for lbs in live_class.batch.students.filter(student__is_active=True).select_related('student'):
                try:
                    NotificationService.create_notification(
                        recipient=lbs.student,
                        title=f"Recording available: {live_class.title}",
                        body=f"The recording for your live class in {live_class.course.title} is now available.",
                        notification_type=NotificationType.LIVE_CLASS,
                        action_url=f"/live-classes",
                        idempotency_key=f"liveclass:{live_class.id}:recording:{lbs.student.id}"
                    )
                except Exception:
                    pass

        return Response(self.get_serializer(live_class).data)

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
    """
    Phase 2: widened so a teacher/mentor can create/manage their OWN
    batches -- "Teacher can create/manage classes assigned to them" /
    "Mentor can create/manage their own mentor sessions". Object-level
    write access for a non-admin is still strictly limited to a batch
    where they are the instructor (LiveBatchSerializer.validate() also
    enforces this at creation time, since has_object_permission never runs
    for a brand-new object on POST).
    """
    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        if request.method in permissions.SAFE_METHODS:
            return True
        return bool(
            request.user.is_superuser or request.user.is_staff
            or getattr(request.user, 'is_teacher', False)
            or getattr(request.user, 'is_mentor', False)
        )

    def has_object_permission(self, request, view, obj):
        if request.user.is_superuser or request.user.is_staff:
            return True
        if getattr(request.user, 'is_teacher', False) or getattr(request.user, 'is_mentor', False):
            if request.method in permissions.SAFE_METHODS:
                return obj.instructor == request.user
            # Write access to an existing batch: only its own instructor.
            return obj.instructor == request.user
        if request.method in permissions.SAFE_METHODS:
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
        elif getattr(user, 'is_teacher', False) or getattr(user, 'is_mentor', False):
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
            # Admin, or the batch's own instructor (Phase 2: teacher/mentor
            # self-service scheduling), can assign students.
            is_owner_instructor = batch.instructor_id == request.user.id
            if not (request.user.is_superuser or request.user.is_staff or is_owner_instructor):
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
        # Admin, or the batch's own instructor, can remove students.
        is_owner_instructor = batch.instructor_id == request.user.id
        if not (request.user.is_superuser or request.user.is_staff or is_owner_instructor):
            return Response({"detail": "You do not have permission to remove students from a batch."}, status=status.HTTP_403_FORBIDDEN)

        try:
            assignment = LiveBatchStudent.objects.get(batch=batch, student_id=student_id)
            assignment.delete()
            return Response({"message": "Student successfully removed from the batch."}, status=status.HTTP_204_NO_CONTENT)
        except LiveBatchStudent.DoesNotExist:
            return Response({"error": "Student assignment not found in this batch."}, status=status.HTTP_404_NOT_FOUND)


class TeacherAvailabilityViewSet(viewsets.ModelViewSet):
    """
    Phase 2: a teacher/mentor's own weekly availability windows. Self-
    service by default (get_queryset scopes to the caller); admin can view/
    manage anyone's via ?user=<id>. Feeds LiveClassSerializer's opt-in
    availability check. Ownership enforcement lives in
    TeacherAvailabilitySerializer.validate() (mirrors CourseInstructor/
    Mentorship serializer-level validation elsewhere in this codebase).
    """
    serializer_class = TeacherAvailabilitySerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        qs = TeacherAvailability.objects.all()
        if user.is_superuser or user.is_staff:
            user_id = self.request.query_params.get('user')
            return qs.filter(user_id=user_id) if user_id else qs
        return qs.filter(user=user)

    def perform_create(self, serializer):
        target_user = serializer.validated_data.get('user') or self.request.user
        serializer.save(user=target_user)

    def perform_destroy(self, instance):
        request_user = self.request.user
        if not (request_user.is_superuser or request_user.is_staff) and instance.user != request_user:
            raise DRFPermissionDenied("You can only delete your own availability.")
        instance.delete()


# =============================================================================
# Phase 4.6: Course Completion Certificates.
# =============================================================================

from .models import Certificate
from .serializers import AdminCertificateSerializer, CertificateSerializer, PublicCertificateVerificationSerializer
from .services.certificates import get_or_create_certificate
from .throttles import (
    CertificateVerificationThrottle, AssessmentStartRateThrottle,
    AssessmentSubmitRateThrottle, AssignmentSubmitRateThrottle,
)


class CertificateViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    """
    Learner-facing certificate access, plus one public/unauthenticated
    verification action. No `create`/`update`/`destroy` -- a certificate
    is only ever produced by services.certificates.get_or_create_certificate
    (called from `by_course` below, or eagerly from
    VideoLessonViewSet.progress/AssessmentAttemptViewSet.submit), never
    directly POSTed by a client.

    list/retrieve are both scoped to `student=request.user` -- the exact
    same ownership convention every other learner-facing viewset in this
    module already uses (a non-owned id 404s, matching the established
    "don't confirm existence" precedent), so this never needs to check
    `request.user` against `instance.student` by hand.
    """
    serializer_class = CertificateSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return Certificate.objects.filter(student=self.request.user).select_related('course')

    @action(detail=False, methods=['get'], url_path=r'course/(?P<course_id>[^/.]+)')
    def by_course(self, request, course_id=None):
        """
        GET /api/courses/certificates/course/<course_id>/ -- the lazy-
        generation entry point (Phase 4.6 Section 5's "generated when the
        learner explicitly requests it"), and the self-healing fallback
        for the two eager trigger points. Eligibility is re-checked here
        every time via get_or_create_certificate -- never trusts that a
        certificate row existing means the course is STILL complete
        (it always is, since nothing in this codebase ever un-completes a
        lesson/attempt, but this function still never assumes it).
        """
        course = get_object_or_404(Course, pk=course_id)
        certificate, _created = get_or_create_certificate(course, request.user)
        if certificate is None:
            return Response(
                {"error": "This course is not yet complete. Finish all lessons and pass all required assessments to earn a certificate."},
                status=drf_status.HTTP_404_NOT_FOUND,
            )
        return Response(CertificateSerializer(certificate).data)

    @action(detail=False, methods=['get'], url_path=r'verify/(?P<verification_id>[^/.]+)', permission_classes=[permissions.AllowAny], throttle_classes=[CertificateVerificationThrottle])
    def verify(self, request, verification_id=None):
        """
        GET /api/courses/certificates/verify/<verification_id>/ -- public,
        unauthenticated, throttled. Returns only
        PublicCertificateVerificationSerializer's narrow whitelist (no
        internal id, no student/course FK, no way to reach the owning
        account) -- see that serializer's own docstring. A nonexistent or
        malformed id gets the exact same 404 either way, so this endpoint
        can never be used to distinguish "wrong format" from "right
        format, no such certificate" while probing.
        """
        try:
            certificate = Certificate.objects.get(verification_id=verification_id)
        except Certificate.DoesNotExist:
            return Response({"valid": False, "error": "No certificate found for this verification ID."}, status=drf_status.HTTP_404_NOT_FOUND)
        data = PublicCertificateVerificationSerializer(certificate).data
        data['valid'] = True
        return Response(data)


from rest_framework import generics


class CertificateResultsSetPagination(PageNumberPagination):
    page_size = 10
    page_size_query_param = 'page_size'
    max_page_size = 100


class AdminCertificateListView(generics.ListAPIView):
    """
    Admin Dashboard Completion gap fix. GET /api/courses/admin/certificates/
    -- admin-only, platform-wide list of every issued certificate.
    CertificateViewSet's own list/retrieve are both hard-scoped to
    student=request.user with no admin bypass (unlike e.g. OrderViewSet,
    which does have an "if staff: return all" branch) -- this genuinely
    did not exist before. Read-only by construction (ListAPIView, no
    create/update/destroy anywhere) -- a certificate is only ever
    produced by get_or_create_certificate, never by an admin action, and
    that is unchanged here. Filterable by student_id/course_id, the same
    explicit query-param whitelisting convention every other admin list
    endpoint in this codebase already uses (AdminLedgerEntryListView,
    AdminInvoiceListView, etc.) -- never a raw filter-string passthrough.
    """
    serializer_class = AdminCertificateSerializer
    permission_classes = [IsSuperAdminOrAdmin]
    pagination_class = CertificateResultsSetPagination

    def get_queryset(self):
        qs = Certificate.objects.select_related('student', 'course').order_by('-issued_at')
        params = self.request.query_params

        student_id = params.get('student_id')
        if student_id:
            qs = qs.filter(student_id=student_id)

        course_id = params.get('course_id')
        if course_id:
            qs = qs.filter(course_id=course_id)

        return qs


# =============================================================================
# Phase 4.7: Assignments & Grading.
#
# Deliberately NOT wired into courses/services/completion.py -- explicit
# product decision, assignments do not affect Phase 4.5 module/course
# completion or Phase 4.6 certificates in this phase.
# =============================================================================

from .models import Assignment, AssignmentSubmission
from .serializers import AdminAssignmentSerializer, AssignmentSerializer, AssignmentSubmissionSerializer
from .services.assignments import GradingValidationError, SubmissionNotAllowedError, create_submission, grade_submission, return_for_revision
from .validators import ALLOWED_SUBMISSION_FILE_EXTENSIONS


class IsSuperAdminOrAssignmentCourseInstructor(permissions.BasePermission):
    """
    Grading/return-for-revision authorization. Mirrors
    IsSuperAdminOrCourseInstructorOrReadOnly's exact WRITE_ROLES check
    (reused directly, not duplicated) -- just resolves the course via
    `submission.assignment.module.course` (one hop further than that
    class's generic obj.course/obj.module.course resolution, since the
    object here is a SUBMISSION, not the Assignment/Course itself).
    """
    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated)

    def has_object_permission(self, request, view, obj):
        user = request.user
        if user.is_superuser or user.is_staff:
            return True
        course = obj.assignment.module.course
        return CourseInstructor.objects.filter(
            course=course, user=user, role__in=IsSuperAdminOrCourseInstructorOrReadOnly.WRITE_ROLES,
        ).exists()


class AssignmentViewSet(mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    """
    No list/create/update/destroy via API -- authoring an Assignment is a
    Django-admin job (matching Assessment/Question's own Phase 4.1
    precedent: no API existed for creating those either). `retrieve` is
    the student-facing assignment detail/submit screen's data source,
    with the exact same access/published checks AssessmentViewSet.retrieve
    already uses.
    """
    queryset = Assignment.objects.all()
    serializer_class = AssignmentSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_throttles(self):
        # General API rate limiting gap fix -- only `submit` gets the
        # dedicated scope; retrieve/my/grade/return-for-revision (reads,
        # or already separately permission-gated to instructors) stay on
        # the general 'user' baseline.
        if self.action == 'submit':
            return [AssignmentSubmitRateThrottle()]
        return super().get_throttles()

    def _get_accessible_published_assignment(self, request, pk):
        try:
            assignment = Assignment.objects.select_related('module__course').get(pk=pk)
        except Assignment.DoesNotExist:
            return None, Response({"error": "Assignment not found."}, status=drf_status.HTTP_404_NOT_FOUND)

        user = request.user
        if not assignment.is_published and not user.is_superuser:
            return None, Response({"error": "Assignment not found."}, status=drf_status.HTTP_404_NOT_FOUND)

        course = assignment.module.course
        if not user_has_course_access(user, course) and not user.is_superuser:
            return None, Response({"error": "You do not have access to this course."}, status=drf_status.HTTP_403_FORBIDDEN)

        return assignment, None

    def retrieve(self, request, pk=None):
        assignment, error_response = self._get_accessible_published_assignment(request, pk)
        if error_response:
            return error_response
        return Response(AssignmentSerializer(assignment).data)

    @action(detail=True, methods=['post'], url_path='submit')
    def submit(self, request, pk=None):
        """
        POST /api/courses/assignments/<id>/submit/ -- creates the next
        submission attempt (first submission, or a resubmission after
        RETURNED_FOR_REVISION). Never trusts a client-supplied student
        id -- always request.user. File upload via multipart 'file',
        text via 'content'; at least one is required.
        """
        assignment, error_response = self._get_accessible_published_assignment(request, pk)
        if error_response:
            return error_response

        content = request.data.get('content', '') or ''
        submitted_file = request.FILES.get('file')
        if not content.strip() and not submitted_file:
            return Response({"error": "Provide submission text or a file."}, status=drf_status.HTTP_400_BAD_REQUEST)

        try:
            submission = create_submission(assignment, request.user, content, submitted_file)
        except SubmissionNotAllowedError as e:
            return Response({"error": str(e)}, status=drf_status.HTTP_409_CONFLICT)
        except Exception as e:
            # File validators (FileExtensionValidator/validate_submission_file_size)
            # raise django.core.exceptions.ValidationError on invalid files.
            from django.core.exceptions import ValidationError as DjangoValidationError
            if isinstance(e, DjangoValidationError):
                return Response({"error": "; ".join(e.messages) if hasattr(e, 'messages') else str(e)}, status=drf_status.HTTP_400_BAD_REQUEST)
            raise

        return Response(AssignmentSubmissionSerializer(submission).data, status=drf_status.HTTP_201_CREATED)


class AdminAssignmentResultsSetPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = 'page_size'
    max_page_size = 100


class AdminAssignmentListView(generics.ListAPIView):
    """
    Admin Dashboard Completion gap fix. GET /api/courses/admin/assignments/
    -- admin-only, platform-wide list of every Assignment (across every
    course/module), each annotated with how many of its submissions are
    still SUBMITTED (awaiting grading) -- so an admin/teacher can see
    where grading is backed up without already knowing an assignment_id.
    Authoring (create/update/publish) is unchanged and stays Django-admin
    -only; this is read-only, and the actual grading action remains
    exactly where it already was (AssignmentSubmissionViewSet.grade/
    return-for-revision, still gated by IsSuperAdminOrAssignmentCourseInstructor)
    -- this view only helps an admin FIND which assignment to open next.
    Filterable by course_id, and by needs_grading=true to show only
    assignments with at least one pending submission.
    """
    serializer_class = AdminAssignmentSerializer
    permission_classes = [IsSuperAdminOrAdmin]
    pagination_class = AdminAssignmentResultsSetPagination

    def get_queryset(self):
        from django.db.models import Count, Q
        qs = (
            Assignment.objects.select_related('module', 'module__course')
            .annotate(pending_submission_count=Count(
                'submissions', filter=Q(submissions__status=AssignmentSubmission.Status.SUBMITTED),
            ))
            .order_by('-id')
        )
        params = self.request.query_params

        course_id = params.get('course_id')
        if course_id:
            qs = qs.filter(module__course_id=course_id)

        if params.get('needs_grading') == 'true':
            qs = qs.filter(pending_submission_count__gt=0)

        return qs


class AssignmentSubmissionResultsSetPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = 'page_size'
    max_page_size = 100


class AssignmentSubmissionViewSet(mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    """
    retrieve: GET /assignment-submissions/<id>/ -- owner (student) or the
    course's own instructor/admin (see get_object override).
    my: GET /assignment-submissions/my/?assignment_id=<id> -- this
    student's own submission history.
    by_assignment: GET /assignment-submissions/assignment/<id>/ -- the
    teacher/admin grading queue for one assignment (every student's
    latest-and-historical submissions).
    grade / return_for_revision: POST actions, instructor/admin only.

    No create/list/update/destroy -- submissions are only ever created
    via AssignmentViewSet.submit and finalized via grade/return_for_revision.
    """
    serializer_class = AssignmentSubmissionSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return AssignmentSubmission.objects.select_related('student', 'assignment__module__course', 'graded_by')

    def get_object(self):
        """
        Owner-or-instructor object lookup -- a plain get_queryset()
        scoped to student=request.user (like every other learner
        endpoint in this module) would 404 a submission for the
        instructor trying to grade it, so this checks both: the owning
        student, OR IsSuperAdminOrAssignmentCourseInstructor. Either way
        a non-owned, non-gradable-by-this-user submission 404s -- never
        a 403 that would confirm it exists.
        """
        submission = get_object_or_404(self.get_queryset(), pk=self.kwargs['pk'])
        user = self.request.user
        is_owner = submission.student_id == user.id
        is_grader = IsSuperAdminOrAssignmentCourseInstructor().has_object_permission(self.request, self, submission)
        if not (is_owner or is_grader):
            from django.http import Http404
            raise Http404
        return submission

    @action(detail=False, methods=['get'], url_path='my')
    def my(self, request):
        queryset = self.get_queryset().filter(student=request.user).order_by('-submitted_at')
        assignment_id = request.query_params.get('assignment_id')
        if assignment_id is not None:
            try:
                queryset = queryset.filter(assignment_id=int(assignment_id))
            except (TypeError, ValueError):
                return Response({"error": "assignment_id must be an integer."}, status=drf_status.HTTP_400_BAD_REQUEST)

        paginator = AssignmentSubmissionResultsSetPagination()
        page = paginator.paginate_queryset(queryset, request)
        serializer = AssignmentSubmissionSerializer(page, many=True)
        return paginator.get_paginated_response(serializer.data)

    @action(detail=False, methods=['get'], url_path=r'assignment/(?P<assignment_id>[^/.]+)')
    def by_assignment(self, request, assignment_id=None):
        """Teacher/admin grading queue -- every submission (every
        student, every attempt) for one assignment, newest first."""
        assignment = get_object_or_404(Assignment.objects.select_related('module__course'), pk=assignment_id)
        course = assignment.module.course
        user = request.user
        if not (user.is_superuser or user.is_staff or CourseInstructor.objects.filter(
            course=course, user=user, role__in=IsSuperAdminOrCourseInstructorOrReadOnly.WRITE_ROLES,
        ).exists()):
            return Response({"error": "You do not have permission to view submissions for this assignment."}, status=drf_status.HTTP_403_FORBIDDEN)

        queryset = self.get_queryset().filter(assignment=assignment).order_by('-submitted_at')
        paginator = AssignmentSubmissionResultsSetPagination()
        page = paginator.paginate_queryset(queryset, request)
        serializer = AssignmentSubmissionSerializer(page, many=True)
        return paginator.get_paginated_response(serializer.data)

    @action(detail=True, methods=['post'], url_path='grade', permission_classes=[IsSuperAdminOrAssignmentCourseInstructor])
    def grade(self, request, pk=None):
        submission = get_object_or_404(self.get_queryset(), pk=pk)
        self.check_object_permissions(request, submission)

        if submission.status != AssignmentSubmission.Status.SUBMITTED:
            return Response({"error": f"Only a SUBMITTED submission can be graded (current status: {submission.status})."}, status=drf_status.HTTP_409_CONFLICT)

        marks_awarded = request.data.get('marks_awarded')
        feedback = request.data.get('feedback', '')
        try:
            grade_submission(submission, request.user, marks_awarded, feedback)
        except GradingValidationError as e:
            return Response({"error": str(e)}, status=drf_status.HTTP_400_BAD_REQUEST)
        return Response(AssignmentSubmissionSerializer(submission).data)

    @action(detail=True, methods=['post'], url_path='return-for-revision', url_name='return-for-revision', permission_classes=[IsSuperAdminOrAssignmentCourseInstructor])
    def return_for_revision_action(self, request, pk=None):
        submission = get_object_or_404(self.get_queryset(), pk=pk)
        self.check_object_permissions(request, submission)

        if submission.status != AssignmentSubmission.Status.SUBMITTED:
            return Response({"error": f"Only a SUBMITTED submission can be returned for revision (current status: {submission.status})."}, status=drf_status.HTTP_409_CONFLICT)

        feedback = request.data.get('feedback', '')
        return_for_revision(submission, request.user, feedback)
        return Response(AssignmentSubmissionSerializer(submission).data)
