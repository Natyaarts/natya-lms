from django.contrib.auth import get_user_model
from rest_framework import serializers
from .models import Course, Module, VideoLesson, TranslatedAudio, LessonProgress, LiveClass, LiveBatch, LiveBatchStudent
from .languages import get_language_name

User = get_user_model()

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
    lessons = VideoLessonSerializer(many=True, read_only=True)

    class Meta:
        model = Module
        fields = ['id', 'title', 'order', 'lessons', 'course']

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
            'created_at',
            'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']

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

            # If teacher is creating/updating, verify they are the instructor of the batch
            user = self.context['request'].user
            if getattr(user, 'is_teacher', False) and not (user.is_superuser or user.is_staff):
                if batch.instructor != user:
                    raise serializers.ValidationError({"batch": "Teachers can only create/update sessions for their own batches."})

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
            elif getattr(user, 'is_teacher', False):
                # Teacher can see meeting_url only if they are the batch instructor
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
        return ret


class LiveBatchSerializer(serializers.ModelSerializer):
    student_count = serializers.IntegerField(read_only=True, default=0)
    course_title = serializers.CharField(source='course.title', read_only=True)
    instructor_username = serializers.CharField(source='instructor.username', read_only=True, allow_null=True)

    class Meta:
        model = LiveBatch
        fields = ['id', 'course', 'course_title', 'instructor', 'instructor_username', 'batch_type', 'student_count', 'created_at', 'updated_at']
        read_only_fields = ['id', 'created_at', 'updated_at']

    def validate(self, attrs):
        import django.core.exceptions
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
