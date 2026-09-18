"""
Phase 4.6. Mirrors users/throttles.py's exact OTPRequestThrottle/
OTPVerifyThrottle pattern -- a per-IP SimpleRateThrottle subclass, kept
in the app it belongs to rather than a shared/central throttles module.

General API rate limiting gap fix (final release audit) adds three more
below -- these are plain UserRateThrottle subclasses (per-authenticated-
user, not per-IP), since assessment/assignment endpoints are already
IsAuthenticated-only and a per-user key is what actually bounds a single
abusive account regardless of which IP it calls from.
"""
from rest_framework.throttling import SimpleRateThrottle, UserRateThrottle


class CertificateVerificationThrottle(SimpleRateThrottle):
    """
    Per-IP throttle on the public, unauthenticated
    GET /api/courses/certificates/verify/<verification_id>/ endpoint --
    without this, an attacker could use the endpoint itself to brute-force
    scan for valid verification IDs (Phase 4.6 Section 9's explicit
    "verification must not become an enumeration vulnerability").
    """
    scope = 'certificate_verify'

    def get_cache_key(self, request, view):
        ident = self.get_ident(request)
        return self.cache_format % {'scope': self.scope, 'ident': ident}


class AssessmentStartRateThrottle(UserRateThrottle):
    """Per-user throttle on AssessmentViewSet's `start` action. Total
    attempts are already bounded by Assessment.max_attempts (a business
    rule, unaffected by this) -- this only bounds how fast the endpoint
    itself can be hit."""
    scope = 'assessment_start'


class AssessmentSubmitRateThrottle(UserRateThrottle):
    """Per-user throttle on AssessmentAttemptViewSet's `submit` action.
    Each individual attempt can only ever be submitted once (the state
    machine itself already prevents resubmission) -- this bounds rapid-
    fire submit calls across many different attempts/assessments."""
    scope = 'assessment_submit'


class AssignmentSubmitRateThrottle(UserRateThrottle):
    """Per-user throttle on AssignmentViewSet's `submit` action. Generous
    enough to cover a legitimate resubmission after "needs revision"."""
    scope = 'assignment_submit'
