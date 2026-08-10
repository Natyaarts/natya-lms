from rest_framework import serializers
from django.contrib.auth import get_user_model
from dj_rest_auth.serializers import UserDetailsSerializer

User = get_user_model()

class CustomUserDetailsSerializer(UserDetailsSerializer):
    class Meta(UserDetailsSerializer.Meta):
        model = User
        fields = ('pk', 'username', 'email', 'first_name', 'last_name', 'is_superuser', 'is_teacher', 'is_student')
        read_only_fields = ('pk', 'email', 'is_superuser')

class AdminUserSerializer(serializers.ModelSerializer):
    courses_count = serializers.SerializerMethodField()
    password = serializers.CharField(write_only=True, required=False)
    
    class Meta:
        model = User
        fields = ('id', 'username', 'email', 'phone_number', 'first_name', 'last_name', 'is_superuser', 'is_teacher', 'is_student', 'is_active', 'date_joined', 'parent_name', 'parent_phone', 'courses_count', 'password')
        read_only_fields = ('id', 'date_joined', 'courses_count')
        
    def validate_phone_number(self, value):
        if value and not value.startswith('+'):
            raise serializers.ValidationError("Phone number must include country code prefix (e.g., +91).")
        return value
        
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

from .models import OnboardingField

class OnboardingFieldSerializer(serializers.ModelSerializer):
    class Meta:
        model = OnboardingField
        fields = '__all__'
