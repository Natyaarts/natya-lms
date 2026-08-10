from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import CourseViewSet, ModuleViewSet, VideoLessonViewSet, AdminEnrollmentViewSet

router = DefaultRouter()
router.register(r'modules', ModuleViewSet, basename='module')
router.register(r'lessons', VideoLessonViewSet, basename='lesson')
router.register(r'enrollments-admin', AdminEnrollmentViewSet, basename='enrollments-admin')
router.register(r'', CourseViewSet, basename='course')

urlpatterns = [
    path('', include(router.urls)),
]
