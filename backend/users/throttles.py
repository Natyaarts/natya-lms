"""
Phase 3.9. DRF throttle classes for the OTP endpoints -- one layer of a
two-layer defense (the other layer is the per-identifier attempt/cooldown
tracking implemented directly in users/views.py against the OTPVerification
model itself). Neither layer alone is sufficient: IP-based throttling
alone doesn't stop an attacker spamming a single victim's phone/email from
many different IPs; per-identifier tracking alone doesn't stop one IP
cycling through many different identifiers.

Both scopes are configured via REST_FRAMEWORK['DEFAULT_THROTTLE_RATES'] in
core/settings.py, not hardcoded here, so the rate can be tuned without a
code change.
"""
from rest_framework.throttling import SimpleRateThrottle


class OTPRequestThrottle(SimpleRateThrottle):
    """Per-IP throttle on POST /api/users/send-otp/."""
    scope = 'otp_request'

    def get_cache_key(self, request, view):
        ident = self.get_ident(request)
        return self.cache_format % {'scope': self.scope, 'ident': ident}


class OTPVerifyThrottle(SimpleRateThrottle):
    """Per-IP throttle on POST /api/users/verify-otp/ -- a separate,
    slightly more generous scope from OTPRequestThrottle since a genuine
    user may legitimately retry a mistyped code a few times."""
    scope = 'otp_verify'

    def get_cache_key(self, request, view):
        ident = self.get_ident(request)
        return self.cache_format % {'scope': self.scope, 'ident': ident}


class LoginRateThrottle(SimpleRateThrottle):
    """
    Final release-blocker fix. Per-IP throttle on POST /api/auth/login/
    (superuser/staff/teacher/mentor password login -- see
    users.views.ThrottledLoginView, which is the only view this is ever
    attached to). Same exact shape as OTPRequestThrottle/OTPVerifyThrottle
    above -- rate itself lives in REST_FRAMEWORK['DEFAULT_THROTTLE_RATES']
    (core/settings.py), not hardcoded here.
    """
    scope = 'login'

    def get_cache_key(self, request, view):
        ident = self.get_ident(request)
        return self.cache_format % {'scope': self.scope, 'ident': ident}


class PasswordResetRequestThrottle(SimpleRateThrottle):
    """
    General API rate limiting gap fix. Per-IP throttle on
    POST /api/auth/password/reset/ (see users.views.ThrottledPasswordResetView,
    the only view this is ever attached to). dj-rest-auth's stock
    PasswordResetView sets throttle_scope = 'dj_rest_auth', but that only
    has any effect if ScopedRateThrottle is present in
    DEFAULT_THROTTLE_CLASSES -- it never was, so this endpoint was
    completely unthrottled before this phase, despite carrying the exact
    same "email-bomb a victim" risk shape as OTPRequestThrottle above (a
    password-reset request emails a link to whatever address is given,
    regardless of whether the requester owns it). Same per-IP shape and
    same 5/hour rate as otp_request, for the same reason.
    """
    scope = 'password_reset'

    def get_cache_key(self, request, view):
        ident = self.get_ident(request)
        return self.cache_format % {'scope': self.scope, 'ident': ident}
