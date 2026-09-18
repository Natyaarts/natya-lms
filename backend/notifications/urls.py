from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import NotificationViewSet, AnnouncementViewSet, DeviceTokenView

router = DefaultRouter()
router.register(r'notifications', NotificationViewSet, basename='notification')
router.register(r'announcements', AnnouncementViewSet, basename='announcement')

urlpatterns = [
    # Phase 4.10: plain APIView path (not a router-registered ViewSet
    # action), same "checkout-style endpoint alongside a router" pattern
    # orders/urls.py already uses for subscriptions/* -- registration-only,
    # see DeviceTokenView's own docstring for scope. Must come BEFORE the
    # router include below: DefaultRouter's own notifications/<pk>/
    # pattern (`[^/.]+`) would otherwise greedily match
    # "notifications/device-token/" first, treating "device-token" as a
    # pk and routing to NotificationViewSet.retrieve (GET-only, hence a
    # 405 on POST/DELETE) instead of this view -- confirmed by hitting
    # exactly that failure before reordering these two lines.
    path('notifications/device-token/', DeviceTokenView.as_view(), name='device-token'),
    path('', include(router.urls)),
]
