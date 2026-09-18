from django.urls import path, include
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import TokenRefreshView
from .views import SendOTPView, VerifyOTPView, AdminUserViewSet, AdminStatsView, CurrentUserView, OnboardingFieldsView, SaveProfileView, MobileGoogleLoginView, OnboardingFieldViewSet, MentorshipViewSet, MyStudentsView, MyTeacherProfileView, MyMentorProfileView, AdminAuditLogListView

router = DefaultRouter()
router.register(r'admin-users', AdminUserViewSet, basename='admin-user')
router.register(r'onboarding-fields-admin', OnboardingFieldViewSet, basename='onboarding-fields-admin')
router.register(r'mentorships', MentorshipViewSet, basename='mentorship')

urlpatterns = [
    path('send-otp/', SendOTPView.as_view(), name='send-otp'),
    path('verify-otp/', VerifyOTPView.as_view(), name='verify-otp'),
    path('admin-stats/', AdminStatsView.as_view(), name='admin-stats'),
    path('admin/audit-logs/', AdminAuditLogListView.as_view(), name='admin-audit-logs'),
    path('me/', CurrentUserView.as_view(), name='current-user'),
    path('me/students/', MyStudentsView.as_view(), name='my-students'),
    path('me/teacher-profile/', MyTeacherProfileView.as_view(), name='my-teacher-profile'),
    path('me/mentor-profile/', MyMentorProfileView.as_view(), name='my-mentor-profile'),
    path('onboarding-fields/', OnboardingFieldsView.as_view(), name='onboarding-fields'),
    path('save-profile/', SaveProfileView.as_view(), name='save-profile'),
    path('', include(router.urls)),
    path('mobile-google-login/', MobileGoogleLoginView.as_view(), name='mobile-google-login'),
    # Phase 4.9: the mobile app stores raw access/refresh tokens (bearer-
    # token auth, no cookie jar) -- it cannot use api/auth/token/refresh/
    # (dj_rest_auth's CookieTokenRefreshSerializer/get_refresh_view), which
    # is deliberately cookie-oriented: with SIMPLE_JWT's ROTATE_REFRESH_TOKENS
    # + BLACKLIST_AFTER_ROTATION both on (core/settings.py), that endpoint
    # blacklists the OLD refresh token on every call but returns the NEW
    # one only as a Set-Cookie header, stripping it from the JSON body
    # (see dj_rest_auth/jwt_auth.py's RefreshViewWithCookieSupport) -- a
    # bearer-token mobile client would lose its refresh token after
    # exactly one use, unable to persist a cookie it never receives as
    # readable JSON. This is the stock, unwrapped simplejwt TokenRefreshView
    # instead: same underlying validation, but its JSON response includes
    # BOTH the new access AND (since rotation is on) the new refresh token,
    # exactly what a bearer-token client needs to keep refreshing
    # indefinitely. Mobile-only; the web frontend's cookie-based flow
    # (api/auth/token/refresh/) is completely untouched by this addition.
    path('mobile-token-refresh/', TokenRefreshView.as_view(), name='mobile-token-refresh'),
]
