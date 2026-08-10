from rest_framework import serializers
from .models import Purchase

class AdminPurchaseSerializer(serializers.ModelSerializer):
    student_name = serializers.SerializerMethodField()
    student_email = serializers.EmailField(source='user.email', read_only=True)
    course_title = serializers.CharField(source='course.title', read_only=True)

    class Meta:
        model = Purchase
        fields = (
            'id',
            'student_name',
            'student_email',
            'course_title',
            'amount',
            'status',
            'razorpay_order_id',
            'razorpay_payment_id',
            'created_at'
        )
        read_only_fields = fields

    def get_student_name(self, obj):
        if obj.user.first_name or obj.user.last_name:
            return f"{obj.user.first_name} {obj.user.last_name}".strip()
        return obj.user.username
