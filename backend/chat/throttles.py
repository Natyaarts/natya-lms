"""
Rate limiting throttles for chat endpoints.
Enforces per-authenticated-user rate limits to prevent message spamming
and conversation flooding.
"""
from rest_framework.throttling import UserRateThrottle


class ChatMessageRateThrottle(UserRateThrottle):
    """Per-user throttle on sending messages (POST /api/chat/conversations/<id>/messages/)."""
    scope = 'chat_message'


class ChatConversationRateThrottle(UserRateThrottle):
    """Per-user throttle on initiating conversations (POST /api/chat/conversations/)."""
    scope = 'chat_conversation'
