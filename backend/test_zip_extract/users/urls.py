from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import SendOTPView, VerifyOTPView, AdminUserViewSet, AdminStatsView

router = DefaultRouter()
router.register(r'admin-users', AdminUserViewSet, basename='admin-user')

urlpatterns = [
    path('send-otp/', SendOTPView.as_view(), name='send-otp'),
    path('verify-otp/', VerifyOTPView.as_view(), name='verify-otp'),
    path('admin-stats/', AdminStatsView.as_view(), name='admin-stats'),
    path('', include(router.urls)),
]
