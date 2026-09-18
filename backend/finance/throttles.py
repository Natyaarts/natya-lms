"""
General API rate limiting gap fix (final release audit). Mirrors
orders/throttles.py's own reasoning exactly -- a plain UserRateThrottle
subclass, keyed on the authenticated admin's own user id
(request.user.pk), not IP. CreateRefundView is already
IsSuperAdminOrAdmin-gated, so this is primarily a defense-in-depth /
accidental-double-submission layer rather than an anti-abuse one against
an untrusted caller -- but the task explicitly calls out "refund
attempts" as a high-value mutation worth bounding regardless of who can
reach it.

Rate itself lives in REST_FRAMEWORK['DEFAULT_THROTTLE_RATES']
(core/settings.py), not hardcoded here.
"""
from rest_framework.throttling import UserRateThrottle


class RefundCreationRateThrottle(UserRateThrottle):
    """Per-user throttle on CreateRefundView."""
    scope = 'refund_create'
