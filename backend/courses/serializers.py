from rest_framework import serializers
from .models import Course, Module, VideoLesson, TranslatedAudio

class TranslatedAudioSerializer(serializers.ModelSerializer):
    class Meta:
        model = TranslatedAudio
        fields = ['id', 'language_code', 'audio_file', 'status', 'created_at']

class VideoLessonSerializer(serializers.ModelSerializer):
    translated_audios = TranslatedAudioSerializer(many=True, read_only=True)

    class Meta:
        model = VideoLesson
        fields = ['id', 'title', 'description', 'transcript', 'timed_transcript', 'video_file', 'order', 'module', 'translated_audios']

class ModuleSerializer(serializers.ModelSerializer):
    lessons = VideoLessonSerializer(many=True, read_only=True)

    class Meta:
        model = Module
        fields = ['id', 'title', 'order', 'lessons', 'course']

class CourseSerializer(serializers.ModelSerializer):
    modules = ModuleSerializer(many=True, read_only=True)

    class Meta:
        model = Course
        fields = ['id', 'title', 'description', 'price', 'thumbnail', 'is_published', 'created_at', 'modules']

from .models import Enrollment

class AdminEnrollmentSerializer(serializers.ModelSerializer):
    student_name = serializers.SerializerMethodField()
    student_email = serializers.EmailField(source='user.email', read_only=True)
    student_phone = serializers.CharField(source='user.phone_number', read_only=True)
    course_title = serializers.CharField(source='course.title', read_only=True)
    source = serializers.SerializerMethodField()

    class Meta:
        model = Enrollment
        fields = (
            'id',
            'student_name',
            'student_email',
            'student_phone',
            'course_title',
            'enrolled_at',
            'source'
        )
        read_only_fields = fields

    def get_student_name(self, obj):
        if obj.user.first_name or obj.user.last_name:
            return f"{obj.user.first_name} {obj.user.last_name}".strip()
        return obj.user.username

    def get_source(self, obj):
        from orders.models import Purchase
        if Purchase.objects.filter(user=obj.user, course=obj.course, status='SUCCESS').exists():
            return 'Paid'
        return 'Manual / Free'
