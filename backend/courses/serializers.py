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
