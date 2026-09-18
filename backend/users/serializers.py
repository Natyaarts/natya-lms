from rest_framework import serializers
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from dj_rest_auth.serializers import UserDetailsSerializer

User = get_user_model()

# Phase 3.9: the exact role/privilege fields AdminAuditLog needs to
# compare before/after on every AdminUserSerializer.update() -- see that
# method below.
_ROLE_FIELDS = ('is_superuser', 'is_staff', 'is_teacher', 'is_mentor', 'is_active')

class CustomUserDetailsSerializer(UserDetailsSerializer):
    """
    Backs dj-rest-auth's stock UserDetailsView (GET/PUT/PATCH
    /api/auth/user/) -- profile-editing gap fix reuses this EXISTING,
    already-live endpoint rather than adding a new one (nothing in the
    web or mobile app called it before this phase, so extending it here
    is safe).

    Profile-editing gap fix, security correction: is_teacher/is_student/
    is_mentor were NOT previously in read_only_fields, meaning any
    authenticated user could self-escalate their own role via
    PATCH {"is_teacher": true} -- a pre-existing hole, unrelated to and
    predating this phase's own actual feature work, but one this phase's
    own security requirements ("reject role/permission manipulation")
    require closing before this endpoint can be safely reused for
    self-service editing. Fixed by moving all three into
    read_only_fields, alongside the pk/email/is_superuser/is_staff
    protection already there.

    phone_number/parent_name/parent_phone added: phone_number is
    READ-ONLY (it doubles as the OTP login identity for phone-based
    accounts -- users/views.py's SendOTPView/VerifyOTPView `get_or_create`
    on it directly -- and no verification-on-change mechanism exists for
    it, so per the "keep unverifiable fields read-only" rule it's
    included here only so a profile UI can display it, never change it
    through this endpoint). parent_name/parent_phone ARE writable --
    plain guardian-contact information, not identity/login/privilege
    fields, previously only admin-editable (AdminUserSerializer) despite
    being ordinary self-service-appropriate profile data.
    """
    class Meta(UserDetailsSerializer.Meta):
        model = User
        fields = (
            'pk', 'username', 'email', 'phone_number', 'first_name', 'last_name',
            'is_superuser', 'is_staff', 'is_teacher', 'is_student', 'is_mentor',
            'parent_name', 'parent_phone',
        )
        read_only_fields = (
            'pk', 'email', 'phone_number', 'is_superuser', 'is_staff',
            'is_teacher', 'is_student', 'is_mentor',
        )

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

    def validate_password(self, value):
        """
        Phase 3.9: previously create()/update() called user.set_password(
        password) directly, completely bypassing AUTH_PASSWORD_VALIDATORS
        (that validation normally only runs via Django's own
        UserCreationForm/allauth registration flow, never via a plain
        ModelSerializer field). This meant an admin could set an
        arbitrarily weak (even empty-after-strip) password for any user
        through this API. Runs the SAME validators
        (core/settings.py's AUTH_PASSWORD_VALIDATORS) Django's own
        password-reset/registration flows already enforce, using a
        same-shaped instance (existing user on update, an unsaved User
        with the submitted fields on create) so UserAttributeSimilarityValidator
        can meaningfully compare against username/email/names either way.
        """
        instance = self.instance or User(**{
            field: self.initial_data.get(field, '') for field in ('username', 'email', 'first_name', 'last_name')
            if field in self.initial_data
        })
        try:
            validate_password(value, user=instance)
        except DjangoValidationError as e:
            raise serializers.ValidationError(list(e.messages))
        return value

    def create(self, validated_data):
        password = validated_data.pop('password', None)
        user = super().create(validated_data)
        if password:
            user.set_password(password)
            user.save()
        return user

    def update(self, instance, validated_data):
        # Phase 3.9: snapshot role/privilege fields BEFORE the update so
        # AdminAuditLog.record can report exactly what changed -- taken
        # from `instance` (the pre-update row), not validated_data, since
        # a PATCH may only submit a subset of fields.
        before = {field: getattr(instance, field) for field in _ROLE_FIELDS}

        password = validated_data.pop('password', None)
        user = super().update(instance, validated_data)
        if password:
            user.set_password(password)
            user.save()

        after = {field: getattr(user, field) for field in _ROLE_FIELDS}
        changed = {field: {'from': before[field], 'to': after[field]} for field in _ROLE_FIELDS if before[field] != after[field]}
        if changed:
            from .models import AdminAuditLog
            request = self.context.get('request')
            AdminAuditLog.record(
                actor=getattr(request, 'user', None),
                action='ROLE_CHANGE',
                target_type='User',
                target_id=user.id,
                description=f"Role/privilege fields changed for user #{user.id} ({user.username}).",
                metadata={'changed_fields': changed},
            )
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


from .models import AdminAuditLog


class AdminAuditLogSerializer(serializers.ModelSerializer):
    """
    Admin Dashboard Completion gap fix. AdminAuditLog has been written to
    since Phase 3.9 (role changes, course-instructor assignment, refund
    creation, payout approval/mark-paid) but was never readable through
    the API -- Django admin's read-only AdminAuditLogAdmin was the only
    place to see it. This is a plain, read-only reflection of the model
    -- no new logging logic, no new action points; AdminAuditLog.record()
    itself is completely unchanged.
    """
    actor = serializers.SerializerMethodField()

    class Meta:
        model = AdminAuditLog
        fields = ['id', 'actor', 'action', 'target_type', 'target_id', 'description', 'metadata', 'created_at']
        read_only_fields = fields

    def get_actor(self, obj):
        if obj.actor_id is None:
            return None
        return {'id': obj.actor_id, 'username': obj.actor.username}
