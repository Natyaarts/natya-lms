"""
General API rate limiting gap fix (final release audit). Every one of
these mirrors users/throttles.py's own OTPRequestThrottle-style
docstring conventions, but unlike those (per-IP, for anonymous/pre-auth
endpoints), these are all plain DRF UserRateThrottle subclasses -- every
view they're attached to is already IsAuthenticated-only, so the correct
identity to key a rate limit on is the authenticated user
(request.user.pk, DRF's own built-in UserRateThrottle.get_cache_key
behavior), never IP (which a single abusive account could trivially
rotate) and never any client-supplied id (none of these views ever read
one for ownership anyway).

Rates themselves live in REST_FRAMEWORK['DEFAULT_THROTTLE_RATES']
(core/settings.py), not hardcoded here -- each class only names its scope.
"""
from rest_framework.throttling import UserRateThrottle


class OrderCreationRateThrottle(UserRateThrottle):
    """Per-user throttle on order/purchase creation -- CreateOrderView
    (legacy single-course flow) and OrderViewSet.create() (multi-item/
    bundle flow) both attach this."""
    scope = 'order_create'


class PaymentVerificationRateThrottle(UserRateThrottle):
    """Per-user throttle on payment verification -- VerifyPaymentView
    (legacy flow) and OrderViewSet.verify() action (multi-item flow) both
    attach this."""
    scope = 'payment_verify'


class SubscriptionCreationRateThrottle(UserRateThrottle):
    """Per-user throttle on CreateSubscriptionView."""
    scope = 'subscription_create'


class SubscriptionVerificationRateThrottle(UserRateThrottle):
    """Per-user throttle on VerifySubscriptionPaymentView."""
    scope = 'subscription_verify'
