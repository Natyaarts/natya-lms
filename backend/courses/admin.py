from django.contrib import admin
from .models import Course, Module, VideoLesson, TranslatedAudio

class VideoLessonInline(admin.TabularInline):
    model = VideoLesson
    extra = 1

class ModuleInline(admin.TabularInline):
    model = Module
    extra = 1

@admin.register(Course)
class CourseAdmin(admin.ModelAdmin):
    list_display = ('title', 'price', 'created_at')
    search_fields = ('title',)
    inlines = [ModuleInline]

@admin.register(Module)
class ModuleAdmin(admin.ModelAdmin):
    list_display = ('title', 'course', 'order')
    list_filter = ('course',)
    inlines = [VideoLessonInline]

class TranslatedAudioInline(admin.TabularInline):
    model = TranslatedAudio
    extra = 0

from django.contrib import messages
import threading
from .services.ai_translator import generate_dubbed_audio

@admin.action(description='Generate AI Dubbed Audio Tracks')
def generate_ai_audio(modeladmin, request, queryset):
    for lesson in queryset:
        if lesson.transcript:
            # Run in a background thread to prevent blocking the admin UI
            threading.Thread(target=generate_dubbed_audio, args=(lesson.id,)).start()
            messages.success(request, f"AI Audio generation started in background for '{lesson.title}'. Refresh page in 30 seconds.")
        else:
            messages.error(request, f"Skipped '{lesson.title}' - no English transcript provided!")

@admin.register(VideoLesson)
class VideoLessonAdmin(admin.ModelAdmin):
    list_display = ('title', 'module', 'order')
    list_filter = ('module__course',)
    inlines = [TranslatedAudioInline]
    actions = [generate_ai_audio]

from .models import Enrollment, LessonProgress
@admin.register(Enrollment)
class EnrollmentAdmin(admin.ModelAdmin):
    list_display = ('user', 'course', 'enrolled_at')
    list_filter = ('course',)
    search_fields = ('user__username', 'course__title')

@admin.register(LessonProgress)
class LessonProgressAdmin(admin.ModelAdmin):
    list_display = ('user', 'lesson', 'last_watched_position', 'video_duration', 'completed', 'updated_at')
    list_filter = ('completed', 'lesson__module__course')
    search_fields = ('user__username', 'user__first_name', 'user__last_name', 'lesson__title')
