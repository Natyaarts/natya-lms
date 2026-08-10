from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import SendOTPView, VerifyOTPView, AdminUserViewSet, AdminStatsView, CurrentUserView, OnboardingFieldsView, SaveProfileView, MobileGoogleLoginView, OnboardingFieldViewSet

router = DefaultRouter()
router.register(r'admin-users', AdminUserViewSet, basename='admin-user')
router.register(r'onboarding-fields-admin', OnboardingFieldViewSet, basename='onboarding-fields-admin')

urlpatterns = [
    path('send-otp/', SendOTPView.as_view(), name='send-otp'),
    path('verify-otp/', VerifyOTPView.as_view(), name='verify-otp'),
    path('admin-stats/', AdminStatsView.as_view(), name='admin-stats'),
    path('me/', CurrentUserView.as_view(), name='current-user'),
    path('onboarding-fields/', OnboardingFieldsView.as_view(), name='onboarding-fields'),
    path('save-profile/', SaveProfileView.as_view(), name='save-profile'),
    path('', include(router.urls)),
    path('mobile-google-login/', MobileGoogleLoginView.as_view(), name='mobile-google-login'),
]
