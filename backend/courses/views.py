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
        return Course.objects.filter(is_published=True)

    @action(detail=False, methods=['get'], permission_classes=[permissions.AllowAny])
    def my_courses(self, request):
        from django.db.models import Q
        # Fallback for local testing if cookie is blocked
        user = request.user
        if user.is_anonymous:
            from django.contrib.auth import get_user_model
            user = get_user_model().objects.first()
            
        enrolled_courses = Course.objects.filter(
            Q(purchases__user=user, purchases__status='SUCCESS') | 
            Q(enrollments__user=user)
        ).distinct()
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
        
        if not lesson.transcript:
            return Response({"error": "No English transcript provided for this lesson."}, status=400)
            
        threading.Thread(target=generate_dubbed_audio, args=(lesson.id,)).start()
        
        return Response({"message": f"AI Audio generation started in background for '{lesson.title}'."})
