from django.contrib import admin, messages
from django.core.exceptions import ValidationError
from django.forms import BaseInlineFormSet
from .models import Course, Module, VideoLesson, TranslatedAudio, LiveClass, LiveBatch, LiveBatchStudent, CourseInstructor, RecurrenceRule, TeacherAvailability, Attendance, Assessment, Question, QuestionOption, AssessmentAttempt, AssessmentAnswer

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
    # Phase 3.5.2: commission_rate is admin-editable (unlike
    # LedgerEntry/Payout) -- it's a rate CONFIGURATION input, not a
    # financial transaction record, matching how SubscriptionPlan.price/
    # is_active are already editable catalog/config data elsewhere in
    # this app. list_editable mirrors SubscriptionPlanAdmin's existing
    # is_active precedent for the same reason: a quick, common admin
    # action shouldn't require opening the full change form.
    list_display = ('course', 'user', 'role', 'is_primary', 'commission_rate', 'created_at')
    list_editable = ('commission_rate',)
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


# =============================================================================
# Phase 4.1: Assessment Engine Foundation admin
# =============================================================================

class QuestionOptionInlineFormSet(BaseInlineFormSet):
    """
    Cross-row validation that a single QuestionOption row can't express on
    its own: a SINGLE_CHOICE question must have exactly one is_correct=True
    option; a MULTIPLE_CHOICE question must have at least one. This is the
    one place all sibling options for a question are visible at once
    during a save, so it's the right layer for this check (see
    QuestionOption's own docstring in courses/models.py).
    """
    def clean(self):
        super().clean()
        if any(self.errors):
            return

        # By the time an inline formset is validated, Django admin has
        # already called form.save(commit=False) on the parent ModelForm,
        # so self.instance.question_type reflects the just-submitted value
        # (not a stale DB read) even though the parent hasn't been saved yet.
        question = self.instance
        question_type = getattr(question, 'question_type', None)

        correct_count = 0
        total_count = 0
        for form in self.forms:
            if not hasattr(form, 'cleaned_data') or not form.cleaned_data:
                continue
            if form.cleaned_data.get('DELETE'):
                continue
            total_count += 1
            if form.cleaned_data.get('is_correct'):
                correct_count += 1

        if total_count == 0:
            return  # allow saving a question before its options are added

        if question_type == Question.QuestionType.SINGLE_CHOICE and correct_count != 1:
            raise ValidationError("A single-choice question must have exactly one correct option.")
        if question_type == Question.QuestionType.MULTIPLE_CHOICE and correct_count < 1:
            raise ValidationError("A multiple-choice question must have at least one correct option.")


class QuestionOptionInline(admin.TabularInline):
    model = QuestionOption
    formset = QuestionOptionInlineFormSet
    extra = 2
    fields = ('option_text', 'order', 'is_correct')


@admin.register(Question)
class QuestionAdmin(admin.ModelAdmin):
    list_display = ('__str__', 'assessment', 'question_type', 'marks', 'order', 'is_required')
    list_filter = ('question_type', 'is_required', 'assessment__module__course')
    search_fields = ('question_text', 'assessment__title')
    inlines = [QuestionOptionInline]


class QuestionInline(admin.TabularInline):
    model = Question
    extra = 0
    fields = ('order', 'question_text', 'question_type', 'marks', 'is_required')
    show_change_link = True


@admin.register(Assessment)
class AssessmentAdmin(admin.ModelAdmin):
    list_display = ('title', 'module', 'is_published', 'passing_percentage', 'max_attempts', 'time_limit_minutes', 'order')
    list_filter = ('is_published', 'module__course')
    search_fields = ('title', 'module__title', 'module__course__title')
    readonly_fields = ('created_at', 'updated_at')
    inlines = [QuestionInline]


class AssessmentAnswerInline(admin.TabularInline):
    """Read-only -- see AssessmentAttemptAdmin's own docstring for why.
    is_correct/marks_awarded/marks_possible are the Phase 4.4 (+
    CORRECTION) frozen per-answer fields -- shown here for admin
    visibility into what a student's review page actually displays."""
    model = AssessmentAnswer
    extra = 0
    fields = ('question', 'selected_options', 'is_correct', 'marks_awarded', 'marks_possible')
    readonly_fields = ('question', 'selected_options', 'is_correct', 'marks_awarded', 'marks_possible')
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(AssessmentAttempt)
class AssessmentAttemptAdmin(admin.ModelAdmin):
    """
    Phase 4.2. Read-only -- same reasoning as every other system-of-record
    admin in this codebase (LedgerEntry/Payout/Refund in finance/admin.py,
    AdminAuditLog in users/admin.py): a student's scored result must not
    be hand-editable, even by accident. AssessmentViewSet.start/submit are
    the only code paths that ever create or finalize a row here.
    """
    list_display = ('student', 'assessment', 'attempt_number', 'status', 'score', 'percentage', 'passed', 'started_at', 'submitted_at')
    list_filter = ('status', 'passed', 'assessment__module__course')
    search_fields = ('student__username', 'student__email', 'assessment__title')
    readonly_fields = (
        'assessment', 'student', 'attempt_number', 'status', 'started_at',
        'submitted_at', 'score', 'percentage', 'passed', 'created_at', 'updated_at',
    )
    inlines = [AssessmentAnswerInline]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


from .models import Certificate


@admin.register(Certificate)
class CertificateAdmin(admin.ModelAdmin):
    """
    Phase 4.6. Read-only -- same reasoning as AssessmentAttemptAdmin/
    LedgerEntry/AdminAuditLog elsewhere in this codebase: a certificate is
    a historical, immutable record. It is never hand-created or hand-
    edited here; services.certificates.get_or_create_certificate is the
    only code path that ever creates one. No manual admin issuance is
    implemented in this phase (not requested, no existing requirement
    found during the audit).
    """
    list_display = ('verification_id', 'learner_name_snapshot', 'course_title_snapshot', 'student', 'issued_at')
    list_filter = ('course',)
    search_fields = ('verification_id', 'learner_name_snapshot', 'course_title_snapshot', 'student__username', 'student__email')
    readonly_fields = ('student', 'course', 'verification_id', 'learner_name_snapshot', 'course_title_snapshot', 'issued_at')

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


from .models import Assignment, AssignmentSubmission


@admin.register(Assignment)
class AssignmentAdmin(admin.ModelAdmin):
    """
    Phase 4.7. Authoring surface for Assignment -- editable, matching
    AssessmentAdmin's exact precedent (a teacher/admin writes the
    assignment brief here; no API exists for creating an Assignment,
    same as Assessment/Question in Phase 4.1).
    """
    list_display = ('title', 'module', 'is_published', 'max_marks', 'order')
    list_filter = ('is_published', 'module__course')
    search_fields = ('title', 'module__title', 'module__course__title')
    readonly_fields = ('created_at', 'updated_at')


@admin.register(AssignmentSubmission)
class AssignmentSubmissionAdmin(admin.ModelAdmin):
    """
    Phase 4.7. Read-only -- same reasoning as AssessmentAttemptAdmin: a
    student's submission and its grade are a historical record. Grading
    happens via AssignmentSubmissionViewSet.grade/return_for_revision
    (API + the teacher/admin frontend), never hand-edited here.
    """
    list_display = ('student', 'assignment', 'attempt_number', 'status', 'marks_awarded', 'graded_by', 'submitted_at')
    list_filter = ('status', 'assignment__module__course')
    search_fields = ('student__username', 'student__email', 'assignment__title')
    readonly_fields = (
        'assignment', 'student', 'attempt_number', 'status', 'content', 'submitted_file',
        'marks_awarded', 'feedback', 'graded_by', 'graded_at', 'submitted_at', 'updated_at',
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
