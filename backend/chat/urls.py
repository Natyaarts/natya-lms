from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import (
    AdminConversationDetailView,
    AdminConversationListView,
    AdminConversationMessagesView,
    AdminMessageModerateView,
    AttachmentDownloadView,
    AttachmentPresignedUrlView,
    ChatRecipientsView,
    ConversationViewSet,
    MessageListCreateView,
)

router = DefaultRouter()
router.register(r'conversations', ConversationViewSet, basename='conversation')

urlpatterns = [
    # Admin-only platform-wide conversation management & moderation
    path(
        'admin/conversations/',
        AdminConversationListView.as_view(),
        name='admin-chat-conversation-list'
    ),
    path(
        'admin/conversations/<uuid:conversation_id>/',
        AdminConversationDetailView.as_view(),
        name='admin-chat-conversation-detail'
    ),
    path(
        'admin/conversations/<uuid:conversation_id>/messages/',
        AdminConversationMessagesView.as_view(),
        name='admin-chat-conversation-messages'
    ),
    path(
        'admin/messages/<int:pk>/moderate/',
        AdminMessageModerateView.as_view(),
        name='admin-chat-message-moderate'
    ),

    # Message listing (with cursor pagination) and sending for a conversation
    path(
        'conversations/<uuid:conversation_id>/messages/',
        MessageListCreateView.as_view(),
        name='conversation-messages'
    ),
    # Secure participant-authorized attachment access (direct stream / redirect)
    path(
        'messages/<int:pk>/attachment/',
        AttachmentDownloadView.as_view(),
        name='message-attachment-download'
    ),
    # Secure participant-authorized attachment presigned URL (JSON for mobile)
    path(
        'messages/<int:pk>/attachment-url/',
        AttachmentPresignedUrlView.as_view(),
        name='message-attachment-presigned-url'
    ),
    # Direct chat recipients discovery
    path(
        'recipients/',
        ChatRecipientsView.as_view(),
        name='chat-recipients'
    ),
    path('', include(router.urls)),
]
