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
    
    class Meta:
        model = User
        fields = ('id', 'username', 'email', 'first_name', 'last_name', 'is_superuser', 'is_teacher', 'is_student', 'is_active', 'date_joined', 'parent_name', 'parent_phone', 'courses_count')
        read_only_fields = ('id', 'date_joined', 'courses_count')
        
    def get_courses_count(self, obj):
        from courses.models import Course
        return Course.objects.filter(enrollments__user=obj).distinct().count()
