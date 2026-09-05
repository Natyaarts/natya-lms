from django.contrib import admin, messages
from .models import Course, Module, VideoLesson, TranslatedAudio, LiveClass, LiveBatch, LiveBatchStudent, CourseInstructor, RecurrenceRule, TeacherAvailability, Attendance

class VideoLessonInline(admin.TabularInline):
    model = VideoLesson
    extra = 1

class ModuleInline(admin.TabularInline):
    model = Module
    extra = 1

@admin.register(Course)
class CourseAdmin(admin.ModelAdmin):
    list_display = ('title', 'price', 'course_type', 'created_at')
    list_filter = ('course_type',)
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

from .tasks import generate_dubbed_audio_task

@admin.action(description='Generate AI Dubbed Audio Tracks')
def generate_ai_audio(modeladmin, request, queryset):
    from django.db import transaction

    target_languages = ['hi', 'ta', 'ml']

    for lesson in queryset:
        if not lesson.transcript and not lesson.timed_transcript:
            messages.error(request, f"Skipped '{lesson.title}' - no transcript or timed transcript provided!")
            continue

        with transaction.atomic():
            existing_tracks = TranslatedAudio.objects.select_for_update().filter(
                lesson=lesson,
                language_code__in=target_languages
            )

            processing_langs = [t.language_code for t in existing_tracks if t.status == 'processing']
            if processing_langs:
                messages.warning(request, f"Skipped '{lesson.title}' - AI Audio generation is already in progress for: {processing_langs}.")
                continue

            langs_to_process = []
            for lang in target_languages:
                track = next((t for t in existing_tracks if t.language_code == lang), None)
                if track:
                    if track.status == 'completed':
                        continue
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
                messages.info(request, f"'{lesson.title}' already has all completed audio tracks.")
                continue

            # Trigger Celery task
            generate_dubbed_audio_task.delay(lesson.id, target_languages=langs_to_process)
            messages.success(request, f"AI Audio generation started in background for '{lesson.title}'.")

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


@admin.register(LiveClass)
class LiveClassAdmin(admin.ModelAdmin):
    list_display = (
        'title',
        'course',
        'instructor',
        'scheduled_start',
        'duration_minutes',
        'status',
        'meeting_provider'
    )
    list_filter = ('status', 'meeting_provider', 'course')
    search_fields = (
        'title',
        'description',
        'course__title',
        'instructor__username',
        'instructor__email'
    )


@admin.register(LiveBatch)
class LiveBatchAdmin(admin.ModelAdmin):
    list_display = ('course', 'instructor', 'batch_type', 'created_at')
    list_filter = ('batch_type', 'course', 'instructor')
    search_fields = ('course__title', 'instructor__username', 'instructor__email')


@admin.register(LiveBatchStudent)
class LiveBatchStudentAdmin(admin.ModelAdmin):
    list_display = ('batch', 'student', 'purchase', 'created_at')
    list_filter = ('batch__batch_type', 'batch__course')
    search_fields = ('student__username', 'student__email', 'batch__course__title')


@admin.register(CourseInstructor)
class CourseInstructorAdmin(admin.ModelAdmin):
    list_display = ('course', 'user', 'role', 'is_primary', 'created_at')
    list_filter = ('role', 'is_primary', 'course')
    search_fields = ('course__title', 'user__username', 'user__email')


@admin.register(RecurrenceRule)
class RecurrenceRuleAdmin(admin.ModelAdmin):
    list_display = ('id', 'frequency', 'end_date', 'occurrence_count', 'created_by', 'created_at')
    list_filter = ('frequency',)


@admin.register(TeacherAvailability)
class TeacherAvailabilityAdmin(admin.ModelAdmin):
    list_display = ('user', 'day_of_week', 'start_time', 'end_time', 'is_active')
    list_filter = ('day_of_week', 'is_active')
    search_fields = ('user__username', 'user__email')


@admin.register(Attendance)
class AttendanceAdmin(admin.ModelAdmin):
    list_display = ('live_class', 'student', 'status', 'marked_by', 'marked_at')
    list_filter = ('status',)
    search_fields = ('student__username', 'live_class__title')


from .models import Bundle

@admin.register(Bundle)
class BundleAdmin(admin.ModelAdmin):
    """
    Phase 3.3: the primary Bundle management surface -- deliberately
    Django's built-in admin, not a new custom Next.js CRUD page (see
    IsSuperAdminOrAdminOrReadOnly in orders/views.py for why). Covers every
    "Bundle Admin" requirement: create/edit (standard admin form),
    activate/deactivate (the is_active field, editable inline via
    list_editable), assign/remove courses (filter_horizontal gives a
    proper dual-list widget for the M2M instead of a bare multi-select),
    set price (plain field).
    """
    list_display = ('name', 'price', 'currency', 'is_active', 'course_count', 'is_purchasable', 'created_at')
    list_filter = ('is_active', 'currency')
    list_editable = ('is_active',)
    search_fields = ('name', 'slug', 'description')
    filter_horizontal = ('courses',)
    prepopulated_fields = {'slug': ('name',)}
    readonly_fields = ('created_at', 'updated_at')

    def course_count(self, obj):
        return obj.courses.count()
    course_count.short_description = 'Courses'

    def is_purchasable(self, obj):
        return obj.is_purchasable
    is_purchasable.boolean = True
