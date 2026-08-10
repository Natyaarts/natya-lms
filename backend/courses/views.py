from rest_framework import viewsets, permissions
from rest_framework.decorators import action
from rest_framework.response import Response
from .models import Course, Module, VideoLesson
from .serializers import CourseSerializer, ModuleSerializer, VideoLessonSerializer
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
        import threading
        from .services.ai_translator import generate_dubbed_audio
        
        lesson = self.get_object()
        
        if not lesson.transcript and not lesson.timed_transcript:
            return Response({"error": "Please fill in the 'Timing for Speaking' section (or a transcript) before generating AI Audio."}, status=400)
            
        threading.Thread(target=generate_dubbed_audio, args=(lesson.id,)).start()
        
        return Response({"message": f"AI Audio generation started in background for '{lesson.title}'."})

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
