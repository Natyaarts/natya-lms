from rest_framework import serializers
from .models import Course, Module, VideoLesson

class VideoLessonSerializer(serializers.ModelSerializer):
    class Meta:
        model = VideoLesson
        fields = ['id', 'title', 'description', 'video_url', 'audio_hi_url', 'audio_ta_url', 'audio_ml_url', 'order', 'module']

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
