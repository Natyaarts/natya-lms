from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import (
    AttachmentDownloadView,
    AttachmentPresignedUrlView,
    ConversationViewSet,
    MessageListCreateView,
)

router = DefaultRouter()
router.register(r'conversations', ConversationViewSet, basename='conversation')

urlpatterns = [
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
    path('', include(router.urls)),
]
