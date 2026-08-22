from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import CourseViewSet, ModuleViewSet, VideoLessonViewSet, AdminEnrollmentViewSet, LiveClassViewSet, LiveBatchViewSet

router = DefaultRouter()
router.register(r'modules', ModuleViewSet, basename='module')
router.register(r'lessons', VideoLessonViewSet, basename='lesson')
router.register(r'enrollments-admin', AdminEnrollmentViewSet, basename='enrollments-admin')
router.register(r'live-classes', LiveClassViewSet, basename='live-class')
router.register(r'live-batches', LiveBatchViewSet, basename='live-batch')
router.register(r'', CourseViewSet, basename='course')

urlpatterns = [
    path('', include(router.urls)),
]
