from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    CourseViewSet, ModuleViewSet, VideoLessonViewSet, AdminEnrollmentViewSet, LiveClassViewSet, LiveBatchViewSet,
    TeacherAvailabilityViewSet, AssessmentViewSet, AssessmentAttemptViewSet, CertificateViewSet, AssignmentViewSet,
    AssignmentSubmissionViewSet, AdminCertificateListView, AdminAssignmentListView,
)

router = DefaultRouter()
router.register(r'modules', ModuleViewSet, basename='module')
router.register(r'lessons', VideoLessonViewSet, basename='lesson')
router.register(r'enrollments-admin', AdminEnrollmentViewSet, basename='enrollments-admin')
router.register(r'live-classes', LiveClassViewSet, basename='live-class')
router.register(r'live-batches', LiveBatchViewSet, basename='live-batch')
router.register(r'availability', TeacherAvailabilityViewSet, basename='availability')
# Phase 4.2: no list/retrieve on AssessmentViewSet (only `start`); only
# `retrieve`/`submit`/`my` on AssessmentAttemptViewSet -- see each
# viewset's own docstring in courses/views.py for why.
router.register(r'assessments', AssessmentViewSet, basename='assessment')
router.register(r'assessment-attempts', AssessmentAttemptViewSet, basename='assessment-attempt')
# Phase 4.6: list/retrieve (owner-scoped) + `course/<id>` (lazy generate)
# + `verify/<verification_id>` (public) -- see CertificateViewSet's own
# docstring in courses/views.py.
router.register(r'certificates', CertificateViewSet, basename='certificate')
# Phase 4.7: no list/create/update/destroy on AssignmentViewSet (only
# `retrieve`/`submit`); `retrieve`/`my`/`assignment/<id>`/`grade`/
# `return-for-revision` on AssignmentSubmissionViewSet -- see each
# viewset's own docstring in courses/views.py.
router.register(r'assignments', AssignmentViewSet, basename='assignment')
router.register(r'assignment-submissions', AssignmentSubmissionViewSet, basename='assignment-submission')
router.register(r'', CourseViewSet, basename='course')

urlpatterns = [
    # Admin Dashboard Completion gap fix -- MUST come before the router
    # include below: CourseViewSet is registered at the router's root
    # (r''), so its own detail lookup (/<pk>/) would otherwise greedily
    # match "admin" as a course pk first (the exact same URL-shadowing
    # pitfall already hit and fixed elsewhere in this codebase, e.g.
    # notifications/urls.py's device-token/ override).
    path('admin/certificates/', AdminCertificateListView.as_view(), name='admin-certificates'),
    path('admin/assignments/', AdminAssignmentListView.as_view(), name='admin-assignments'),
    path('', include(router.urls)),
]
