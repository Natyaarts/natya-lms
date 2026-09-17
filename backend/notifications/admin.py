from django.contrib import admin
from .models import Notification, Announcement, DeviceToken

@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ('recipient', 'title', 'notification_type', 'is_read', 'created_at')
    list_filter = ('notification_type', 'is_read', 'created_at')
    search_fields = ('recipient__username', 'recipient__email', 'title', 'body', 'idempotency_key')


@admin.register(DeviceToken)
class DeviceTokenAdmin(admin.ModelAdmin):
    list_display = ('user', 'platform', 'is_active', 'created_at', 'updated_at')
    list_filter = ('platform', 'is_active')
    search_fields = ('user__username', 'user__email', 'token')


@admin.register(Announcement)
class AnnouncementAdmin(admin.ModelAdmin):
    list_display = ('title', 'course', 'sender', 'is_published', 'created_at')
    list_filter = ('is_published', 'course', 'created_at')
    search_fields = ('title', 'content')
