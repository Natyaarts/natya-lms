from django.contrib.auth import get_user_model
from rest_framework import serializers
from .models import Course, Module, VideoLesson, TranslatedAudio, LessonProgress, LiveClass, LiveBatch, LiveBatchStudent, CourseInstructor
from .languages import get_language_name

User = get_user_model()


class CourseInstructorSerializer(serializers.ModelSerializer):
    """
    Course "who is responsible for it" -- completely separate from
    Enrollment ("who has access to learn it"). See CourseInstructor model.
    """
    user_name = serializers.SerializerMethodField()
    user_email = serializers.EmailField(source='user.email', read_only=True)
    user_phone = serializers.CharField(source='user.phone_number', read_only=True)
    is_active_user = serializers.BooleanField(source='user.is_active', read_only=True)

    class Meta:
        model = CourseInstructor
        fields = (
            'id', 'course', 'user', 'user_name', 'user_email', 'user_phone',
            'is_active_user', 'role', 'is_primary', 'created_at'
        )
        read_only_fields = ('id', 'created_at')

    def get_user_name(self, obj):
        name = f"{obj.user.first_name} {obj.user.last_name}".strip()
        return name or obj.user.username

    def validate(self, attrs):
        user = attrs.get('user') or getattr(self.instance, 'user', None)
        course = attrs.get('course') or getattr(self.instance, 'course', None)
        role = attrs.get('role') or getattr(self.instance, 'role', CourseInstructor.InstructorRole.TEACHER)

        if user is not None:
            is_eligible = (
                getattr(user, 'is_teacher', False)
                or getattr(user, 'is_mentor', False)
                or user.is_staff
                or user.is_superuser
            )
            if not is_eligible:
                raise serializers.ValidationError(
                    {"user": "Only teacher, mentor, or admin accounts can be assigned as a course instructor."}
                )

        if user is not None and course is not None:
            qs = CourseInstructor.objects.filter(course=course, user=user, role=role)
            if self.instance:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                raise serializers.ValidationError(
                    "This user already has this role on this course."
                )

        return attrs

class TranslatedAudioSerializer(serializers.ModelSerializer):
    class Meta:
        model = TranslatedAudio
        fields = ['id', 'lesson', 'language_code', 'language_name', 'audio_file', 'status', 'created_at', 'updated_at']
        read_only_fields = ['id', 'lesson', 'status', 'created_at', 'updated_at']


class TranslatedAudioUploadSerializer(serializers.Serializer):
    """
    Input validation for the manual audio-upload endpoint
    (VideoLessonViewSet.upload_audio). Deliberately separate from
    TranslatedAudioSerializer, which represents the stored/output shape.
    """
    language_code = serializers.CharField(max_length=10, allow_blank=False)
    language_name = serializers.CharField(max_length=100, required=False, allow_blank=True)
    audio_file = serializers.FileField()

    def validate_language_code(self, value):
        code = value.strip()
        if not code:
            raise serializers.ValidationError("language_code is required.")
        if code.lower() == 'en':
            raise serializers.ValidationError(
                "English is the original lesson audio and cannot be uploaded as a translated track."
            )
        return code

    def validate_audio_file(self, value):
        allowed_extensions = ('.mp3', '.wav', '.m4a', '.aac', '.ogg', '.flac')
        name = (value.name or '').lower()
        if not name.endswith(allowed_extensions):
            raise serializers.ValidationError(
                f"Unsupported audio file type. Allowed: {', '.join(allowed_extensions)}"
            )
        return value

    def validate(self, attrs):
        if not attrs.get('language_name'):
            attrs['language_name'] = get_language_name(attrs['language_code'])
        else:
            attrs['language_name'] = attrs['language_name'].strip()
        return attrs

class VideoLessonSerializer(serializers.ModelSerializer):
    translated_audios = TranslatedAudioSerializer(many=True, read_only=True)

    class Meta:
        model = VideoLesson
        fields = ['id', 'title', 'description', 'transcript', 'timed_transcript', 'video_file', 'order', 'module', 'translated_audios']

class ModuleSerializer(serializers.ModelSerializer):
    """
    Course-content security follow-up (post-3.4.4): this is where locked
    lesson content actually gets redacted, not VideoLessonSerializer
    itself. The access decision needs a course id -- Module already has
    one directly (`course_id`, a plain FK field, zero extra queries) --
    whereas a VideoLesson only has it via `.module.course_id`, an extra
    hop that would cost one query PER LESSON unless the modules were
    prefetched specially for this. Deciding "locked or not" once per
    Module (reusing the same course_id for every lesson under it) and
    post-processing the already-serialized lesson dicts keeps this at
    zero additional queries beyond the one-time context computation in
    CourseViewSet.get_serializer_context() (see there for what populates
    `course_content_full_access_ids`/`bypass_content_lock`).
    """
    lessons = VideoLessonSerializer(many=True, read_only=True)

    class Meta:
        model = Module
        fields = ['id', 'title', 'order', 'lessons', 'course']

    def _has_full_content_access(self, obj):
        if self.context.get('bypass_content_lock'):
            return True
        full_access_ids = self.context.get('course_content_full_access_ids')
        if full_access_ids is None:
            # No context populated at all -- fail CLOSED, never open. A
            # caller that forgets to populate this context should get a
            # locked response, not an accidentally-public one.
            return False
        return obj.course_id in full_access_ids

    def to_representation(self, instance):
        data = super().to_representation(instance)
        has_access = self._has_full_content_access(instance)
        for lesson_data in data['lessons']:
            lesson_data['is_locked'] = not has_access
            if not has_access:
                # video_file/transcript/timed_transcript/translated_audios
                # (and each translated_audios entry's own audio_file) are
                # the only fields ever redacted -- title/description/order/
                # module stay visible, matching the approved "outline
                # visible, content locked" contract; nulling them here
                # (rather than not fetching them at all) means no
                # functional S3 URL is ever included in the response, per
                # the security requirement, without needing to change how
                # VideoLessonSerializer/TranslatedAudioSerializer fetch
                # their data.
                lesson_data['transcript'] = None
                lesson_data['timed_transcript'] = None
                lesson_data['video_file'] = None
                lesson_data['translated_audios'] = []
        return data

class CourseSerializer(serializers.ModelSerializer):
    modules = ModuleSerializer(many=True, read_only=True)

    class Meta:
        model = Course
        fields = ['id', 'title', 'description', 'price', 'thumbnail', 'is_published', 'created_at', 'course_type', 'modules']

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


class LessonProgressSerializer(serializers.ModelSerializer):
    progress_percentage = serializers.ReadOnlyField()

    class Meta:
        model = LessonProgress
        fields = [
            'id',
            'lesson',
            'last_watched_position',
            'video_duration',
            'progress_percentage',
            'completed',
            'updated_at',
            'completed_at'
        ]
        read_only_fields = ['id', 'lesson', 'progress_percentage', 'updated_at', 'completed_at']


class LiveClassSerializer(serializers.ModelSerializer):
    course = serializers.PrimaryKeyRelatedField(queryset=Course.objects.all(), required=False)
    instructor = serializers.PrimaryKeyRelatedField(queryset=User.objects.all(), required=False, allow_null=True)

    class Meta:
        model = LiveClass
        fields = [
            'id',
            'batch',
            'course',
            'instructor',
            'title',
            'description',
            'scheduled_start',
            'duration_minutes',
            'meeting_provider',
            'meeting_url',
            'status',
            'cancellation_reason',
            'recording_url',
            'recording_uploaded_at',
            'recurrence_rule',
            'created_at',
            'updated_at'
        ]
        read_only_fields = ['id', 'cancellation_reason', 'recording_uploaded_at', 'recurrence_rule', 'created_at', 'updated_at']

    def validate(self, attrs):
        import django.core.exceptions
        from django.utils import timezone
        from django.db.models import Q

        batch = attrs.get('batch')
        if not batch:
            if self.instance:
                batch = self.instance.batch

        # Validate that batch is required for new creation
        if not self.instance and not batch:
            raise serializers.ValidationError({"batch": "batch is required for new LiveClass creation."})

        if batch:
            # Verify batch.course is LIVE
            if batch.course.course_type != 'LIVE':
                raise serializers.ValidationError({"batch": "A LiveClass can only be created for a Course with type LIVE."})

            # Verify batch has a valid instructor
            if not batch.instructor:
                raise serializers.ValidationError({"batch": "The batch must have an assigned instructor."})

            # Check for conflicting course/instructor values if provided in request.data
            request = self.context.get('request')
            if request and request.data:
                client_course = request.data.get('course')
                if client_course is not None and int(client_course) != batch.course.id:
                    raise serializers.ValidationError({"course": "Derived course and batch course do not match."})

                client_instructor = request.data.get('instructor')
                if client_instructor is not None and int(client_instructor) != batch.instructor.id:
                    raise serializers.ValidationError({"instructor": "Derived instructor and batch instructor do not match."})

            # If a teacher or mentor is creating/updating, verify they are
            # the instructor of the batch. Phase 2 fix: this previously only
            # checked is_teacher, so a mentor (who passes the view-level
            # has_permission check) could create a class under a batch they
            # don't actually own -- has_object_permission never runs for a
            # brand-new object on POST, so this serializer check was the
            # only real gate for mentors.
            user = self.context['request'].user
            if (getattr(user, 'is_teacher', False) or getattr(user, 'is_mentor', False)) and not (user.is_superuser or user.is_staff):
                if batch.instructor != user:
                    raise serializers.ValidationError({"batch": "Teachers/mentors can only create/update sessions for their own batches."})

        duration_minutes = attrs.get('duration_minutes')
        if duration_minutes is None and self.instance:
            duration_minutes = self.instance.duration_minutes

        if duration_minutes is not None and duration_minutes <= 0:
            raise serializers.ValidationError({"duration_minutes": "Duration must be greater than 0."})

        # Phase D: Scheduling Conflict Detection
        scheduled_start = attrs.get('scheduled_start')
        if not scheduled_start and self.instance:
            scheduled_start = self.instance.scheduled_start

        if scheduled_start and duration_minutes:
            # 1. Past check (only when creating or modifying scheduled_start)
            if 'scheduled_start' in attrs and scheduled_start < timezone.now():
                raise serializers.ValidationError({"scheduled_start": ["Live classes cannot be scheduled in the past."]})

            # 2. Conflict check
            end = scheduled_start + timezone.timedelta(minutes=duration_minutes)

            instructor = attrs.get('instructor')
            if not instructor and self.instance:
                instructor = self.instance.instructor
            if not instructor and batch:
                instructor = batch.instructor

            # Query relevant active sessions within a 24-hour bounding box for efficiency
            qs = LiveClass.objects.filter(status__in=[LiveClass.ClassStatus.SCHEDULED, LiveClass.ClassStatus.LIVE])
            if self.instance:
                qs = qs.exclude(pk=self.instance.pk)

            min_start = scheduled_start - timezone.timedelta(hours=24)
            max_start = end

            potential_conflicts = qs.filter(
                scheduled_start__lt=max_start,
                scheduled_start__gte=min_start
            ).filter(Q(batch=batch) | Q(instructor=instructor))

            for pc in potential_conflicts:
                pc_end = pc.scheduled_start + timezone.timedelta(minutes=pc.duration_minutes)
                if pc.scheduled_start < end and pc_end > scheduled_start:
                    if batch and pc.batch_id == batch.id:
                        raise serializers.ValidationError({"scheduled_start": ["This batch already has another live class scheduled during this time."]})
                    if instructor and pc.instructor_id == instructor.id:
                        raise serializers.ValidationError({"scheduled_start": ["This instructor already has another live class scheduled during this time."]})

            # 3. Availability check (Phase 2) -- opt-in: only enforced if the
            # instructor has defined TeacherAvailability windows at all, so
            # this never restricts scheduling for an instructor who hasn't
            # configured availability (backward compatible).
            if instructor:
                from .models import TeacherAvailability
                windows = TeacherAvailability.objects.filter(user=instructor, is_active=True)
                if windows.exists():
                    weekday = scheduled_start.weekday()
                    local_start = timezone.localtime(scheduled_start).time()
                    local_end = timezone.localtime(end).time()
                    day_windows = windows.filter(day_of_week=weekday)
                    fits_a_window = any(
                        w.start_time <= local_start and local_end <= w.end_time and local_end > local_start
                        for w in day_windows
                    )
                    if not fits_a_window:
                        raise serializers.ValidationError({
                            "scheduled_start": ["This time falls outside the instructor's configured availability."]
                        })

        return attrs

    def create(self, validated_data):
        batch = validated_data.get('batch')
        if batch:
            validated_data['course'] = batch.course
            validated_data['instructor'] = batch.instructor
        return super().create(validated_data)

    def update(self, instance, validated_data):
        batch = validated_data.get('batch')
        if batch:
            validated_data['course'] = batch.course
            validated_data['instructor'] = batch.instructor
        return super().update(instance, validated_data)

    def to_representation(self, instance):
        ret = super().to_representation(instance)
        request = self.context.get('request')
        if request and request.user:
            user = request.user
            if user.is_superuser or user.is_staff:
                pass
            elif getattr(user, 'is_teacher', False) or getattr(user, 'is_mentor', False):
                # Phase 2 fix: this previously only checked is_teacher, so a
                # mentor conducting their own batch fell through to the
                # student branch below and had their own meeting_url hidden
                # (they're never a LiveBatchStudent on their own batch).
                # Teacher/mentor can see meeting_url only if they are the
                # batch instructor.
                if not instance.batch or instance.batch.instructor != user:
                    ret['meeting_url'] = None
            else:
                # Student can see meeting_url only if assigned to this batch
                if instance.batch:
                    from courses.models import LiveBatchStudent
                    is_assigned = LiveBatchStudent.objects.filter(batch=instance.batch, student=user).exists()
                    if not is_assigned:
                        ret['meeting_url'] = None
                else:
                    ret['meeting_url'] = None
        else:
            ret['meeting_url'] = None

        # Nest the recurrence rule (instead of a bare id) so the frontend can
        # render a "recurring series" badge without an extra request per class.
        if instance.recurrence_rule_id:
            ret['recurrence_rule'] = RecurrenceRuleSerializer(instance.recurrence_rule).data
        return ret


class LiveBatchSerializer(serializers.ModelSerializer):
    student_count = serializers.IntegerField(read_only=True, default=0)
    course_title = serializers.CharField(source='course.title', read_only=True)
    instructor_username = serializers.CharField(source='instructor.username', read_only=True, allow_null=True)

    class Meta:
        model = LiveBatch
        fields = ['id', 'course', 'course_title', 'instructor', 'instructor_username', 'batch_type', 'max_participants', 'student_count', 'created_at', 'updated_at']
        read_only_fields = ['id', 'created_at', 'updated_at']

    def validate(self, attrs):
        import django.core.exceptions

        # Phase 2: a non-admin teacher/mentor may only create/manage a batch
        # where THEY are the instructor -- has_object_permission never runs
        # for a brand-new object on POST, so this is the real gate for
        # "teachers/mentors can create their own sessions" without letting
        # one teacher create a batch on another's behalf.
        request = self.context.get('request')
        user = getattr(request, 'user', None)
        if user and not (user.is_superuser or user.is_staff):
            instructor = attrs.get('instructor')
            if instructor is None and not self.instance:
                attrs['instructor'] = user
            elif instructor is not None and instructor != user:
                raise serializers.ValidationError({"instructor": "You can only create/manage sessions for yourself."})
            elif self.instance and self.instance.instructor != user:
                raise serializers.ValidationError({"instructor": "You can only manage your own batches."})

        # Handle validation for both create and update instances
        instance = self.instance or LiveBatch()
        for field, value in attrs.items():
            setattr(instance, field, value)
        try:
            instance.clean()
        except django.core.exceptions.ValidationError as e:
            raise serializers.ValidationError(e.message_dict if hasattr(e, 'message_dict') else e.messages)
        return attrs


class LiveBatchStudentSerializer(serializers.ModelSerializer):
    student_username = serializers.CharField(source='student.username', read_only=True)
    purchase_status = serializers.CharField(source='purchase.status', read_only=True, allow_null=True)

    class Meta:
        model = LiveBatchStudent
        fields = ['id', 'batch', 'student', 'student_username', 'purchase', 'purchase_status', 'created_at', 'updated_at']
        read_only_fields = ['id', 'created_at', 'updated_at']


from .models import RecurrenceRule, TeacherAvailability, Attendance


class RecurrenceRuleSerializer(serializers.ModelSerializer):
    class Meta:
        model = RecurrenceRule
        fields = ['id', 'frequency', 'weekdays', 'end_date', 'occurrence_count', 'created_by', 'created_at']
        read_only_fields = ['id', 'created_by', 'created_at']


class TeacherAvailabilitySerializer(serializers.ModelSerializer):
    day_of_week_display = serializers.CharField(source='get_day_of_week_display', read_only=True)
    # Explicitly required=False: the model FK itself is required (every row
    # must belong to someone), but a self-service teacher/mentor POST omits
    # `user` entirely and relies on validate()/perform_create() defaulting it
    # to the caller -- only an admin needs to pass it, to act on someone
    # else's behalf.
    user = serializers.PrimaryKeyRelatedField(queryset=User.objects.all(), required=False)

    class Meta:
        model = TeacherAvailability
        fields = ['id', 'user', 'day_of_week', 'day_of_week_display', 'start_time', 'end_time', 'is_active', 'created_at', 'updated_at']
        read_only_fields = ['id', 'created_at', 'updated_at']

    def validate(self, attrs):
        import django.core.exceptions

        request = self.context.get('request')
        acting_user = getattr(request, 'user', None)
        target_user = attrs.get('user') or getattr(self.instance, 'user', None) or acting_user
        if acting_user and not (acting_user.is_superuser or acting_user.is_staff) and target_user != acting_user:
            raise serializers.ValidationError({"user": "You can only manage your own availability."})

        instance = self.instance or TeacherAvailability(user=target_user)
        for field, value in attrs.items():
            setattr(instance, field, value)
        try:
            instance.clean()
        except django.core.exceptions.ValidationError as e:
            raise serializers.ValidationError(e.message_dict if hasattr(e, 'message_dict') else e.messages)
        return attrs


class AttendanceSerializer(serializers.ModelSerializer):
    student_name = serializers.SerializerMethodField()

    class Meta:
        model = Attendance
        fields = ['id', 'live_class', 'student', 'student_name', 'status', 'marked_by', 'marked_at', 'notes']
        read_only_fields = ['id', 'live_class', 'marked_by', 'marked_at']

    def get_student_name(self, obj):
        name = f"{obj.student.first_name} {obj.student.last_name}".strip()
        return name or obj.student.username

    def validate_student(self, value):
        live_class = self.context.get('live_class')
        if live_class and live_class.batch:
            from .models import LiveBatchStudent
            if not LiveBatchStudent.objects.filter(batch=live_class.batch, student=value).exists():
                raise serializers.ValidationError("This student is not assigned to this class's batch.")
        return value
