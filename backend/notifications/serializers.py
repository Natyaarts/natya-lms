from rest_framework import serializers
from .models import Notification, Announcement

class NotificationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Notification
        fields = ['id', 'title', 'body', 'notification_type', 'is_read', 'created_at', 'read_at', 'action_url']
        read_only_fields = ['id', 'is_read', 'read_at', 'created_at']


class AnnouncementSerializer(serializers.ModelSerializer):
    sender_name = serializers.SerializerMethodField()

    class Meta:
        model = Announcement
        fields = ['id', 'sender', 'sender_name', 'course', 'title', 'content', 'created_at', 'updated_at', 'is_published']
        read_only_fields = ['id', 'sender', 'sender_name', 'created_at', 'updated_at']

    def get_sender_name(self, obj):
        if obj.sender:
            if obj.sender.first_name or obj.sender.last_name:
                return f"{obj.sender.first_name} {obj.sender.last_name}".strip()
            return obj.sender.username
        return None
