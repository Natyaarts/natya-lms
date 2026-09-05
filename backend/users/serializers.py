from rest_framework import serializers
from django.contrib.auth import get_user_model
from dj_rest_auth.serializers import UserDetailsSerializer

User = get_user_model()

class CustomUserDetailsSerializer(UserDetailsSerializer):
    class Meta(UserDetailsSerializer.Meta):
        model = User
        fields = ('pk', 'username', 'email', 'first_name', 'last_name', 'is_superuser', 'is_staff', 'is_teacher', 'is_student', 'is_mentor')
        read_only_fields = ('pk', 'email', 'is_superuser', 'is_staff')

class AdminUserSerializer(serializers.ModelSerializer):
    courses_count = serializers.SerializerMethodField()
    password = serializers.CharField(write_only=True, required=False)

    class Meta:
        model = User
        fields = ('id', 'username', 'email', 'phone_number', 'first_name', 'last_name', 'is_superuser', 'is_staff', 'is_teacher', 'is_student', 'is_mentor', 'is_active', 'date_joined', 'parent_name', 'parent_phone', 'courses_count', 'password')
        read_only_fields = ('id', 'date_joined', 'courses_count')

    def validate_phone_number(self, value):
        if value and not value.startswith('+'):
            raise serializers.ValidationError("Phone number must include country code prefix (e.g., +91).")
        return value

    def validate(self, attrs):
        # AdminUserViewSet is reachable by is_staff "Admin" accounts, not
        # just is_superuser (see IsSuperAdminOrAdmin). A non-superuser admin
        # must never be able to grant themselves or anyone else superuser/
        # staff status -- that would be a privilege escalation path this
        # widening would otherwise open up.
        request = self.context.get('request')
        acting_user = getattr(request, 'user', None)
        if acting_user and not acting_user.is_superuser:
            target_is_superuser = attrs.get('is_superuser', getattr(self.instance, 'is_superuser', False))
            target_is_staff = attrs.get('is_staff', getattr(self.instance, 'is_staff', False))
            if target_is_superuser or target_is_staff:
                raise serializers.ValidationError(
                    "Only a Super Admin can grant Super Admin or Admin (staff) status."
                )
        return attrs

    def create(self, validated_data):
        password = validated_data.pop('password', None)
        user = super().create(validated_data)
        if password:
            user.set_password(password)
            user.save()
        return user

    def update(self, instance, validated_data):
        password = validated_data.pop('password', None)
        user = super().update(instance, validated_data)
        if password:
            user.set_password(password)
            user.save()
        return user
        
    def get_courses_count(self, obj):
        from courses.models import Course
        from django.db.models import Q
        return Course.objects.filter(
            Q(purchases__user=obj, purchases__status='SUCCESS') | 
            Q(enrollments__user=obj)
        ).distinct().count()

from .models import OnboardingField, Mentorship

class OnboardingFieldSerializer(serializers.ModelSerializer):
    class Meta:
        model = OnboardingField
        fields = '__all__'


class MentorshipSerializer(serializers.ModelSerializer):
    """
    The single source of truth for student<->mentor assignment. Deliberately
    NOT derived from courses.Enrollment -- see Mentorship model docstring.
    """
    student_name = serializers.SerializerMethodField()
    mentor_name = serializers.SerializerMethodField()
    assigned_by_name = serializers.SerializerMethodField()

    class Meta:
        model = Mentorship
        fields = (
            'id', 'student', 'student_name', 'mentor', 'mentor_name',
            'assigned_by', 'assigned_by_name', 'status', 'start_date',
            'end_date', 'notes', 'assigned_at', 'updated_at'
        )
        read_only_fields = ('id', 'assigned_by', 'assigned_by_name', 'assigned_at', 'updated_at')

    def _display_name(self, user):
        if not user:
            return None
        name = f"{user.first_name} {user.last_name}".strip()
        return name or user.username

    def get_student_name(self, obj):
        return self._display_name(obj.student)

    def get_mentor_name(self, obj):
        return self._display_name(obj.mentor)

    def get_assigned_by_name(self, obj):
        return self._display_name(obj.assigned_by)

    def validate(self, attrs):
        student = attrs.get('student') or getattr(self.instance, 'student', None)
        mentor = attrs.get('mentor') or getattr(self.instance, 'mentor', None)
        status_value = attrs.get('status') or getattr(self.instance, 'status', Mentorship.Status.ACTIVE)

        if student is not None and not getattr(student, 'is_student', False):
            raise serializers.ValidationError({"student": "Selected user is not a student."})
        if mentor is not None and not getattr(mentor, 'is_mentor', False):
            raise serializers.ValidationError({"mentor": "Selected user is not a mentor."})
        if student is not None and mentor is not None and student.pk == mentor.pk:
            raise serializers.ValidationError("A user cannot be their own mentor.")

        if student is not None and mentor is not None and status_value == Mentorship.Status.ACTIVE:
            qs = Mentorship.objects.filter(student=student, mentor=mentor, status=Mentorship.Status.ACTIVE)
            if self.instance:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                raise serializers.ValidationError(
                    "This student already has an active mentorship with this mentor."
                )

        return attrs


from .models import TeacherProfile, MentorProfile


class TeacherProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = TeacherProfile
        fields = (
            'id', 'user', 'bio', 'profile_image', 'specialization', 'qualifications',
            'experience_years', 'languages', 'short_intro', 'is_public', 'is_active',
            'created_at', 'updated_at'
        )
        read_only_fields = ('id', 'user', 'created_at', 'updated_at')


class MentorProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = MentorProfile
        fields = (
            'id', 'user', 'bio', 'profile_image', 'specialization', 'qualifications',
            'experience_years', 'languages', 'availability_status', 'social_links',
            'is_public', 'is_active', 'created_at', 'updated_at'
        )
        read_only_fields = ('id', 'user', 'created_at', 'updated_at')
