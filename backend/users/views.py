import logging
import re
import secrets

from rest_framework.views import APIView
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework import status
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from datetime import timedelta
import random
from .models import OTPVerification, User, AccountDeletionRequest
from .throttles import OTPRequestThrottle, OTPVerifyThrottle, LoginRateThrottle, PasswordResetRequestThrottle
from rest_framework_simplejwt.tokens import RefreshToken
from django.shortcuts import render, get_object_or_404
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import ensure_csrf_cookie
from dj_rest_auth.views import LoginView, PasswordResetView

import requests

logger = logging.getLogger('users.otp')


def _normalize_phone_identifier(raw: str) -> str:
    """
    Safely canonicalize phone or email identifiers.

    - For emails (contains '@'): strips whitespace, validates basic RFC structure,
      and returns lowercased email. Does not convert emails into phone numbers.
    - For phone numbers:
      - Rejects malformed input containing letters, scripts, or invalid characters.
      - Strips benign formatting characters (spaces, dashes, parentheses, dots).
      - Handles international dial prefix ('00').
      - Validates E.164 digit length (7 to 15 digits) and non-zero country code prefix.
      - Returns canonical E.164 string with leading '+' prefix (e.g., '+919999900001').
    - Returns empty string '' for invalid or malformed identifiers so callers can reject safely.
    """
    if not raw:
        return ''
    cleaned = str(raw).strip()
    if not cleaned:
        return ''

    # Email handling: preserve email identity, lowercase
    if '@' in cleaned:
        email_pattern = r'^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$'
        if re.match(email_pattern, cleaned):
            return cleaned.lower()
        return ''

    # Phone handling:
    # 1. Reject if it contains letters or disallowed punctuation
    if re.search(r'[^\d\s\+\-\(\)\.]', cleaned):
        return ''

    # 2. Check for international prefix '00' (e.g., '00919999900001')
    digits_raw = cleaned
    if digits_raw.startswith('00'):
        digits_raw = digits_raw[2:]
    elif digits_raw.startswith('+00'):
        digits_raw = digits_raw[3:]

    # 3. Extract only digits
    digits = re.sub(r'[^\d]', '', digits_raw)

    # 4. Validate E.164 constraints:
    # Standard ITU-T E.164 allows 7 to 15 digits
    if len(digits) < 7 or len(digits) > 15:
        return ''

    # E.164 country codes never start with '0'
    if digits.startswith('0'):
        return ''

    return f"+{digits}"


def _is_reviewer_request(identifier: str) -> bool:
    """
    Check if reviewer access is enabled and identifier matches the configured reviewer phone number.
    Strictly gated by APP_REVIEW_ENABLED and requires non-empty APP_REVIEW_PHONE_NUMBER and APP_REVIEW_STATIC_OTP.
    """
    if not getattr(settings, 'APP_REVIEW_ENABLED', False):
        return False
    configured_number = getattr(settings, 'APP_REVIEW_PHONE_NUMBER', '')
    static_otp = getattr(settings, 'APP_REVIEW_STATIC_OTP', '')
    if not configured_number or not static_otp:
        return False
    norm_ident = _normalize_phone_identifier(identifier)
    norm_conf = _normalize_phone_identifier(configured_number)
    return bool(norm_ident and norm_ident == norm_conf)


# Phase 3.9: how many OTPs a single identifier may request within the
# cooldown window below -- the per-IP OTPRequestThrottle (users/throttles.py)
# alone doesn't stop an attacker who controls many IPs from spamming one
# victim's phone/email with unlimited OTP requests; this is the
# complementary per-identifier layer.
MAX_OTP_REQUESTS_PER_WINDOW = 5
OTP_REQUEST_WINDOW_MINUTES = 10


def _generate_otp():
    """Cryptographically secure 6-digit OTP. Previously `random.randint`
    (Python's Mersenne Twister PRNG) -- not a CSPRNG, and its output is
    predictable if an attacker ever recovers enough of its internal state.
    secrets.randbelow is Python's own recommended CSPRNG-backed choice for
    exactly this kind of security-sensitive random value."""
    return str(secrets.randbelow(1_000_000)).zfill(6)


class ThrottledLoginView(LoginView):
    """
    Final release-blocker fix. The one and only change from dj-rest-auth's
    stock LoginView (core/urls.py routes api/auth/login/ to THIS class
    instead, overriding dj_rest_auth.urls' own 'login/' pattern -- see
    that file's own comment) is the added throttle_classes below --
    everything else (credential validation, JWT cookie issuance, response
    shape) is entirely inherited, unchanged. Password-based login for
    superuser/staff/teacher/mentor accounts previously had no rate
    limiting at all; LoginRateThrottle (users/throttles.py) is the exact
    same per-IP, Redis-backed SimpleRateThrottle pattern already proven by
    OTPRequestThrottle/OTPVerifyThrottle above, scoped via
    REST_FRAMEWORK['DEFAULT_THROTTLE_RATES']['login'] (core/settings.py)
    -- never a hardcoded rate here.

    Deliberately NOT a global DEFAULT_THROTTLE_CLASSES change: this
    targets only the one endpoint identified as the release blocker,
    exactly as scoped -- OTP, JWT refresh, and every other endpoint's
    throttling behavior is untouched.
    """
    throttle_classes = [LoginRateThrottle]


class ThrottledPasswordResetView(PasswordResetView):
    """
    General API rate limiting gap fix. The one and only change from
    dj-rest-auth's stock PasswordResetView (core/urls.py routes
    api/auth/password/reset/ to THIS class instead, overriding
    dj_rest_auth.urls' own 'password/reset/' pattern -- same override
    technique as ThrottledLoginView above) is the added throttle_classes
    below -- everything else (email lookup, token generation, email
    send) is entirely inherited, unchanged. This request-a-reset-email
    step previously had no EFFECTIVE rate limiting at all: dj-rest-auth's
    own throttle_scope = 'dj_rest_auth' attribute requires
    ScopedRateThrottle to be in DEFAULT_THROTTLE_CLASSES, which it never
    was in this project. PasswordResetRequestThrottle (users/throttles.py)
    is the exact same per-IP pattern as OTPRequestThrottle, scoped via
    REST_FRAMEWORK['DEFAULT_THROTTLE_RATES']['password_reset']
    (core/settings.py) -- never a hardcoded rate here.
    """
    throttle_classes = [PasswordResetRequestThrottle]


def _dispatch_otp(identifier: str, otp: str):
    """
    Deliver OTP via email or WhatsApp Interakt.
    Returns (success: bool, error_response: Response | None).
    """
    if '@' in identifier:
        from .email_utils import OTPEmailDeliveryError, send_otp_email
        try:
            if settings.DEBUG and not (settings.AWS_ACCESS_KEY_ID and settings.AWS_SECRET_ACCESS_KEY):
                logger.info("DEBUG mode, no AWS credentials configured -- OTP for %s: %s", identifier, otp)
            else:
                send_otp_email(identifier, otp)
        except OTPEmailDeliveryError as e:
            logger.error("Failed to send OTP email to %s: %s", identifier, e)
            return False, Response(
                {"error": "Failed to send OTP email. Please try again shortly."},
                status=status.HTTP_502_BAD_GATEWAY
            )
    else:
        # --- INTERAKT WHATSAPP INTEGRATION ---
        INTERAKT_SECRET_KEY = settings.INTERAKT_SECRET_KEY
        TEMPLATE_NAME = settings.INTERAKT_TEMPLATE_NAME

        headers = {
            "Authorization": f"Basic {INTERAKT_SECRET_KEY}",
            "Content-Type": "application/json"
        }

        # Interakt requires the phone number without the '+' sign
        formatted_number = identifier.lstrip('+')
        payload = {
            "fullPhoneNumber": formatted_number,
            "type": "Template",
            "template": {
                "name": TEMPLATE_NAME,
                "languageCode": "en",
                "bodyValues": [otp],
                "buttonValues": {"0": [otp]}
            }
        }

        try:
            response = requests.post("https://api.interakt.ai/v1/public/message/", json=payload, headers=headers, timeout=10)
            logger.info("Interakt WhatsApp OTP send status=%s for identifier ending in %s", response.status_code, identifier[-4:])
        except requests.RequestException as e:
            logger.error("Interakt WhatsApp OTP send failed: %s", e, exc_info=True)
            return False, Response(
                {"error": "Failed to send OTP via WhatsApp. Please try again shortly."},
                status=status.HTTP_502_BAD_GATEWAY
            )

    return True, None


class SendOTPView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [OTPRequestThrottle]

    def post(self, request):
        raw_identifier = request.data.get('identifier')
        if not raw_identifier:
            return Response({"error": "Email or Mobile Number is required"}, status=status.HTTP_400_BAD_REQUEST)

        identifier = _normalize_phone_identifier(raw_identifier)
        if not identifier:
            return Response(
                {"error": "A valid mobile number with country code or email is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Phase 3.9: per-identifier cooldown, independent of the per-IP
        # throttle above -- see MAX_OTP_REQUESTS_PER_WINDOW's own comment.
        window_start = timezone.now() - timedelta(minutes=OTP_REQUEST_WINDOW_MINUTES)
        recent_count = OTPVerification.objects.filter(
            identifier=identifier,
            purpose=OTPVerification.Purpose.LOGIN,
            created_at__gte=window_start
        ).count()
        if recent_count >= MAX_OTP_REQUESTS_PER_WINDOW:
            logger.warning("OTP request rate limit hit for identifier ending in %s", str(identifier)[-6:])
            return Response(
                {"error": "Too many OTP requests for this identifier. Please wait a few minutes and try again."},
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )

        if _is_reviewer_request(identifier):
            # Apple App Store reviewer path (Stage 2D Step 1):
            # Bypass external WhatsApp/email dispatch only when reviewer access is explicitly
            # enabled and identifier matches the server-configured review phone number.
            # Stores the configured static OTP in the standard OTPVerification table, where
            # the standard 5-minute expiry and attempt limits apply identically.
            # Never logs or returns the OTP value.
            otp = settings.APP_REVIEW_STATIC_OTP
            logger.warning(
                "App Reviewer login OTP requested for configured review account (IP: %s)",
                request.META.get('REMOTE_ADDR')
            )
        else:
            otp = _generate_otp()
            success, err_resp = _dispatch_otp(identifier, otp)
            if not success:
                return err_resp

        OTPVerification.objects.create(
            identifier=identifier,
            otp=otp,
            purpose=OTPVerification.Purpose.LOGIN
        )
        return Response({"message": "OTP sent successfully"})


class VerifyOTPView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [OTPVerifyThrottle]

    def post(self, request):
        raw_identifier = request.data.get('identifier')
        otp = request.data.get('otp')

        if not raw_identifier or not otp:
            return Response({"error": "Identifier and OTP required"}, status=status.HTTP_400_BAD_REQUEST)

        identifier = _normalize_phone_identifier(raw_identifier)
        if not identifier:
            return Response({"error": "Invalid or expired OTP"}, status=status.HTTP_400_BAD_REQUEST)

        # Phase 3.9: the hardcoded "+919999999999"/"123456" bypass that
        # used to live here has been REMOVED entirely -- see SendOTPView's
        # own comment above.
        with transaction.atomic():
            # Check if OTP is valid, purpose matches LOGIN, and not expired (5 minutes). Locked so
            # two simultaneous verify attempts against the same record
            # can't both read attempts=4 and both proceed past the check
            # below.
            time_threshold = timezone.now() - timedelta(minutes=5)
            otp_record = (
                OTPVerification.objects.select_for_update()
                .filter(
                    identifier=identifier,
                    purpose=OTPVerification.Purpose.LOGIN,
                    is_verified=False,
                    created_at__gte=time_threshold
                )
                .order_by('-created_at')
                .first()
            )

            if not otp_record or otp_record.attempts >= OTPVerification.MAX_VERIFY_ATTEMPTS:
                return Response({"error": "Invalid or expired OTP"}, status=status.HTTP_400_BAD_REQUEST)

            if otp_record.otp != otp:
                otp_record.attempts += 1
                otp_record.save(update_fields=['attempts'])
                return Response({"error": "Invalid or expired OTP"}, status=status.HTTP_400_BAD_REQUEST)

            otp_record.is_verified = True
            otp_record.save(update_fields=['is_verified'])

        # Get or create user
        if '@' in identifier:
            user, created = User.objects.get_or_create(email=identifier, defaults={'username': identifier.split('@')[0] + str(random.randint(1000, 9999))})
        else:
            user, created = User.objects.get_or_create(phone_number=identifier, defaults={'username': identifier})
            
        # Generate JWT Tokens
        refresh = RefreshToken.for_user(user)
        
        response = Response({
            "message": "Login successful",
            "user_id": user.id,
            "created": created,
            "is_onboarded": user.is_onboarded,
            "tokens": {
                "access": str(refresh.access_token),
                "refresh": str(refresh)
            }
        })
        
        # Set JWT Cookies for dj-rest-auth
        from django.conf import settings
        access_cookie_key = getattr(settings, 'REST_AUTH', {}).get('JWT_AUTH_COOKIE', 'natya-auth')
        refresh_cookie_key = getattr(settings, 'REST_AUTH', {}).get('JWT_AUTH_REFRESH_COOKIE', 'natya-refresh')
        
        # Use COOKIE_DOMAIN (None on localhost, '.natyaarts.com' in prod) --
        # NOT SESSION_COOKIE_DOMAIN directly. A cookie's Domain attribute has
        # to match the request host or a parent of it, so hardcoding the
        # production domain here made the browser silently drop this cookie
        # on localhost: the API call looked successful but the user was
        # never actually logged in.
        cookie_domain = getattr(settings, 'COOKIE_DOMAIN', None)
        response.set_cookie(
            access_cookie_key,
            str(refresh.access_token),
            httponly=True,
            samesite='None',
            secure=True,
            domain=cookie_domain
        )
        response.set_cookie(
            refresh_cookie_key,
            str(refresh),
            httponly=True,
            samesite='None',
            secure=True,
            domain=cookie_domain
        )
        
        return response


class RequestAccountDeletionOTPView(APIView):
    """
    Stage 2D Step 2B: Initiates re-authentication for account deletion by dispatching
    a purpose-bound ACCOUNT_DELETION OTP.
    - Requires authenticated user.
    - Throttled per IP via OTPRequestThrottle.
    - Cooldown enforced per identifier.
    - Resolves and verifies identifier against request.user.
    - Never returns or logs the OTP value.
    """
    permission_classes = [IsAuthenticated]
    throttle_classes = [OTPRequestThrottle]

    def post(self, request):
        user = request.user

        # Prevent duplicate OTP generation if user's request is already in processing
        if AccountDeletionRequest.objects.filter(
            user=user,
            status=AccountDeletionRequest.Status.PROCESSING
        ).exists():
            return Response(
                {"error": "Account deletion is already being processed."},
                status=status.HTTP_409_CONFLICT
            )

        raw_identifier = request.data.get('identifier')
        user_phone = _normalize_phone_identifier(user.phone_number) if user.phone_number else ''
        user_email = user.email.strip().lower() if user.email else ''

        if raw_identifier:
            identifier = _normalize_phone_identifier(raw_identifier)
            if not identifier:
                return Response(
                    {"error": "A valid mobile number with country code or email is required"},
                    status=status.HTTP_400_BAD_REQUEST
                )
            if identifier != user_phone and identifier != user_email:
                return Response(
                    {"error": "Provided identifier does not match your account."},
                    status=status.HTTP_400_BAD_REQUEST
                )
        else:
            if user_phone:
                identifier = user_phone
            elif user_email:
                identifier = user_email
            else:
                return Response(
                    {"error": "No verified phone number or email found on your account to send OTP."},
                    status=status.HTTP_400_BAD_REQUEST
                )

        # Per-identifier cooldown check for ACCOUNT_DELETION OTPs
        window_start = timezone.now() - timedelta(minutes=OTP_REQUEST_WINDOW_MINUTES)
        recent_count = OTPVerification.objects.filter(
            identifier=identifier,
            purpose=OTPVerification.Purpose.ACCOUNT_DELETION,
            created_at__gte=window_start
        ).count()
        if recent_count >= MAX_OTP_REQUESTS_PER_WINDOW:
            logger.warning("Account deletion OTP rate limit hit for identifier ending in %s", str(identifier)[-6:])
            return Response(
                {"error": "Too many OTP requests for this identifier. Please wait a few minutes and try again."},
                status=status.HTTP_429_TOO_MANY_REQUESTS
            )

        otp = _generate_otp()
        success, err_resp = _dispatch_otp(identifier, otp)
        if not success:
            return err_resp

        OTPVerification.objects.create(
            identifier=identifier,
            otp=otp,
            purpose=OTPVerification.Purpose.ACCOUNT_DELETION
        )
        return Response({"message": "Account deletion OTP sent successfully"})


class VerifyAccountDeletionOTPView(APIView):
    """
    Stage 2D Step 2B: Verifies an ACCOUNT_DELETION OTP and executes the account deletion lifecycle.
    - Requires authenticated user.
    - Throttled per IP via OTPVerifyThrottle.
    - Requires 5-minute expiry, max attempts (5) lockout, single-use enforcement.
    - Rejects LOGIN OTPs.
    - Executes AccountDeletionService (immediate access cutoff, JWT blacklisting, PII anonymization).
    """
    permission_classes = [IsAuthenticated]
    throttle_classes = [OTPVerifyThrottle]

    def post(self, request):
        user = request.user
        otp = request.data.get('otp')
        reason = request.data.get('reason', '')

        if not otp:
            return Response({"error": "OTP is required"}, status=status.HTTP_400_BAD_REQUEST)

        raw_identifier = request.data.get('identifier')
        user_phone = _normalize_phone_identifier(user.phone_number) if user.phone_number else ''
        user_email = user.email.strip().lower() if user.email else ''

        if raw_identifier:
            identifier = _normalize_phone_identifier(raw_identifier)
            if not identifier:
                return Response({"error": "Invalid or expired OTP"}, status=status.HTTP_400_BAD_REQUEST)
            if identifier != user_phone and identifier != user_email:
                return Response(
                    {"error": "Provided identifier does not match your account."},
                    status=status.HTTP_400_BAD_REQUEST
                )
        else:
            if user_phone:
                identifier = user_phone
            elif user_email:
                identifier = user_email
            else:
                return Response({"error": "Invalid or expired OTP"}, status=status.HTTP_400_BAD_REQUEST)

        with transaction.atomic():
            time_threshold = timezone.now() - timedelta(minutes=5)
            otp_record = (
                OTPVerification.objects.select_for_update()
                .filter(
                    identifier=identifier,
                    purpose=OTPVerification.Purpose.ACCOUNT_DELETION,
                    is_verified=False,
                    created_at__gte=time_threshold
                )
                .order_by('-created_at')
                .first()
            )

            if not otp_record or otp_record.attempts >= OTPVerification.MAX_VERIFY_ATTEMPTS:
                return Response({"error": "Invalid or expired OTP"}, status=status.HTTP_400_BAD_REQUEST)

            if otp_record.otp != otp:
                otp_record.attempts += 1
                otp_record.save(update_fields=['attempts'])
                return Response({"error": "Invalid or expired OTP"}, status=status.HTTP_400_BAD_REQUEST)

            otp_record.is_verified = True
            otp_record.save(update_fields=['is_verified'])

            # Idempotent creation/retrieval of AccountDeletionRequest
            active_request = (
                AccountDeletionRequest.objects.select_for_update()
                .filter(user=user)
                .exclude(status__in=AccountDeletionRequest.TERMINAL_STATUSES)
                .first()
            )

            if active_request:
                if reason and not active_request.reason:
                    active_request.reason = str(reason).strip()
                active_request.confirmed_at = timezone.now()
                active_request.save(update_fields=['confirmed_at', 'reason', 'updated_at'])
                deletion_request = active_request
                created = False
            else:
                deletion_request = AccountDeletionRequest.objects.create(
                    user=user,
                    status=AccountDeletionRequest.Status.PENDING,
                    reason=str(reason).strip() if reason else "",
                    confirmed_at=timezone.now(),
                )
                created = True

        # Stage 2D Step 2B, Phase 2: Execute account deletion lifecycle
        from users.services.deletion import AccountDeletionService
        deletion_request = AccountDeletionService.execute_deletion(deletion_request.id)

        return Response({
            "message": "Account deletion completed successfully",
            "request_id": deletion_request.id,
            "status": deletion_request.status,
            "confirmed_at": deletion_request.confirmed_at,
            "completed_at": deletion_request.completed_at,
            "created": created,
        }, status=status.HTTP_200_OK if not created else status.HTTP_201_CREATED)


class AccountDeletionStatusView(APIView):
    """
    Stage 2D Step 2B: Check the status of the authenticated user's deletion request.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        active_request = (
            AccountDeletionRequest.objects.filter(user=request.user)
            .exclude(status__in=AccountDeletionRequest.TERMINAL_STATUSES)
            .first()
        )

        if active_request:
            return Response({
                "has_active_request": True,
                "request": {
                    "id": active_request.id,
                    "status": active_request.status,
                    "reason": active_request.reason,
                    "confirmed_at": active_request.confirmed_at,
                    "created_at": active_request.created_at,
                    "updated_at": active_request.updated_at,
                }
            })

        latest_request = AccountDeletionRequest.objects.filter(user=request.user).first()
        if latest_request:
            return Response({
                "has_active_request": False,
                "latest_request": {
                    "id": latest_request.id,
                    "status": latest_request.status,
                    "error_message": latest_request.error_message,
                    "completed_at": latest_request.completed_at,
                    "created_at": latest_request.created_at,
                    "updated_at": latest_request.updated_at,
                }
            })

        return Response({
            "has_active_request": False,
            "latest_request": None,
        })


class CancelAccountDeletionRequestView(APIView):
    """
    Stage 2D Step 2B: Cancel an existing PENDING account deletion request.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        with transaction.atomic():
            active_request = (
                AccountDeletionRequest.objects.select_for_update()
                .filter(user=request.user, status=AccountDeletionRequest.Status.PENDING)
                .first()
            )

            if not active_request:
                # Check if there is already a processing or completed request
                in_flight_or_done = (
                    AccountDeletionRequest.objects.filter(user=request.user)
                    .filter(status__in=[AccountDeletionRequest.Status.PROCESSING, AccountDeletionRequest.Status.COMPLETED])
                    .exists()
                )
                if in_flight_or_done:
                    return Response(
                        {"error": "Cannot cancel an account deletion request that is already processing or completed."},
                        status=status.HTTP_400_BAD_REQUEST
                    )
                return Response(
                    {"error": "No pending deletion request found to cancel."},
                    status=status.HTTP_404_NOT_FOUND
                )

            active_request.status = AccountDeletionRequest.Status.CANCELLED
            active_request.save(update_fields=['status', 'updated_at'])

        return Response({
            "message": "Account deletion request cancelled successfully.",
            "request_id": active_request.id,
            "status": active_request.status,
        })


from rest_framework import status, viewsets, permissions
from rest_framework.decorators import action
from .serializers import AdminUserSerializer
from .permissions import IsSuperAdminOrAdmin

class AdminUserViewSet(viewsets.ModelViewSet):
    queryset = User.objects.all().order_by('-date_joined')
    serializer_class = AdminUserSerializer
    permission_classes = [IsSuperAdminOrAdmin]

    @action(detail=True, methods=['get'])
    def courses(self, request, pk=None):
        user = self.get_object()
        from orders.models import Purchase
        from courses.models import Enrollment
        
        purchases = Purchase.objects.filter(user=user, status='SUCCESS').select_related('course')
        enrollments = Enrollment.objects.filter(user=user).select_related('course')
        
        data = []
        for p in purchases:
            data.append({
                "id": f"p_{p.id}",
                "course_id": p.course.id,
                "title": p.course.title,
                "thumbnail": request.build_absolute_uri(p.course.thumbnail.url) if p.course.thumbnail else None,
                "assigned_at": p.created_at
            })
        for e in enrollments:
            data.append({
                "id": f"e_{e.id}",
                "course_id": e.course.id,
                "title": e.course.title,
                "thumbnail": request.build_absolute_uri(e.course.thumbnail.url) if e.course.thumbnail else None,
                "assigned_at": e.enrolled_at
            })
            
        unique_courses = { c['course_id']: c for c in data }.values()
        return Response(list(unique_courses))

    @action(detail=True, methods=['get'])
    def purchases(self, request, pk=None):
        user = self.get_object()
        from orders.models import Purchase
        purchases = Purchase.objects.filter(user=user).select_related('course').order_by('-created_at')
        data = []
        for p in purchases:
            data.append({
                "id": p.id,
                "course_title": p.course.title,
                "amount": p.amount,
                "status": p.status,
                "created_at": p.created_at
            })
        return Response(data)

    @action(detail=True, methods=['post'])
    def assign_course(self, request, pk=None):
        user = self.get_object()
        course_id = request.data.get('course_id')
        payment_status = request.data.get('payment_status', 'SUCCESS') # SUCCESS or PENDING
        
        if not course_id:
            return Response({"error": "course_id is required"}, status=status.HTTP_400_BAD_REQUEST)
        
        from courses.models import Course
        from orders.models import Purchase
        
        try:
            course = Course.objects.get(id=course_id)
        except Course.DoesNotExist:
            return Response({"error": "Course not found"}, status=status.HTTP_404_NOT_FOUND)
            
        # Check if already purchased
        if Purchase.objects.filter(user=user, course=course, status='SUCCESS').exists():
            return Response({"error": "User already has this course"}, status=status.HTTP_400_BAD_REQUEST)

        amount = request.data.get('amount', course.price)

        from django.db import transaction
        from orders.services import fulfill_purchase
        # Phase 3.1: wrapped in atomic() so the Purchase row and its
        # fulfillment (Enrollment + notification) either both happen or
        # neither does -- no change to the existing behavior otherwise.
        with transaction.atomic():
            purchase = Purchase.objects.create(
                user=user,
                course=course,
                amount=amount,
                status=payment_status
            )

            # If created directly as SUCCESS (the default), this must grant
            # access the same way every other "successful payment" path does --
            # previously this silently created a paid-looking Purchase with no
            # Enrollment and no notification. See orders/services.py.
            fulfill_purchase(purchase, previous_status='PENDING')

        return Response({"message": f"Successfully assigned {course.title} to {user.username}"})

    @action(detail=True, methods=['post'])
    def mark_purchase_paid(self, request, pk=None):
        user = self.get_object()
        purchase_id = request.data.get('purchase_id')
        
        if not purchase_id:
            return Response({"error": "purchase_id is required"}, status=status.HTTP_400_BAD_REQUEST)
            
        from django.db import transaction
        from orders.models import Purchase
        from orders.services import fulfill_purchase
        try:
            # Phase 3.1: same select_for_update()+atomic() treatment as
            # AdminPurchaseViewSet.mark_paid -- this is the second of the
            # two separate "mark paid" endpoints/frontends the Phase 3 audit
            # found, so it needs the identical concurrency fix.
            with transaction.atomic():
                purchase = Purchase.objects.select_for_update().get(id=purchase_id, user=user)
                previous_status = purchase.status
                purchase.status = Purchase.Status.SUCCESS
                purchase.save()

                # Same fulfillment path as every other "mark paid" action -- this
                # previously did NOT enroll the user, unlike
                # AdminPurchaseViewSet.mark_paid, which did. See
                # orders/services.py for the single source of truth.
                fulfill_purchase(purchase, previous_status)

            return Response({"message": "Successfully marked as paid!"})
        except Purchase.DoesNotExist:
            return Response({"error": "Purchase record not found"}, status=status.HTTP_404_NOT_FOUND)

    @action(detail=True, methods=['post'])
    def unassign_course(self, request, pk=None):
        user = self.get_object()
        course_id = request.data.get('course_id')
        
        if not course_id:
            return Response({"error": "course_id is required"}, status=status.HTTP_400_BAD_REQUEST)
            
        from courses.models import Enrollment
        
        # Delete only enrollment records (keep purchase log intact)
        deleted_enrollments, _ = Enrollment.objects.filter(user=user, course_id=course_id).delete()
        
        if deleted_enrollments:
            return Response({"message": "Successfully unassigned the course."})
        else:
            return Response({"error": "The user is not assigned to this course."}, status=status.HTTP_404_NOT_FOUND)

    @action(detail=True, methods=['post'])
    def enroll_course(self, request, pk=None):
        user = self.get_object()
        course_id = request.data.get('course_id')
        
        if not course_id:
            return Response({"error": "course_id is required"}, status=status.HTTP_400_BAD_REQUEST)
            
        from courses.models import Course, Enrollment
        course = get_object_or_404(Course, id=course_id)
        
        Enrollment.objects.get_or_create(user=user, course=course)
        return Response({"message": f"Successfully enrolled {user.username} in {course.title}."})

    @action(detail=True, methods=['get'])
    def teacher_students(self, request, pk=None):
        teacher = self.get_object()
        if not teacher.is_teacher:
            return Response({"error": "User is not a teacher"}, status=status.HTTP_400_BAD_REQUEST)

        from courses.models import Course
        from django.db.models import Q

        # CourseInstructor is now the canonical "who teaches this course"
        # relationship; the Enrollment condition is kept only as a fallback
        # for legacy teachers not yet captured by a CourseInstructor row
        # (see courses.CourseViewSet.get_queryset for the same pattern).
        teacher_courses = Course.objects.filter(
            Q(instructors__user=teacher, instructors__role='TEACHER') |
            Q(enrollments__user=teacher)
        ).distinct()

        # Get students enrolled in these courses
        students = User.objects.filter(
            is_student=True,
            is_teacher=False,
            is_superuser=False
        ).filter(
            Q(enrollments__course__in=teacher_courses)
        ).distinct().order_by('-date_joined')

        serializer = self.get_serializer(students, many=True)
        return Response(serializer.data)

    @action(detail=True, methods=['get', 'patch'], url_path='teacher-profile')
    def teacher_profile(self, request, pk=None):
        """Admin view/edit of any user's TeacherProfile."""
        target = self.get_object()
        if not target.is_teacher:
            return Response({"error": "User is not a teacher."}, status=status.HTTP_400_BAD_REQUEST)
        profile, _ = TeacherProfile.objects.get_or_create(user=target)
        if request.method == 'GET':
            return Response(TeacherProfileSerializer(profile).data)
        serializer = TeacherProfileSerializer(profile, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)

    @action(detail=True, methods=['get', 'patch'], url_path='mentor-profile')
    def mentor_profile(self, request, pk=None):
        """Admin view/edit of any user's MentorProfile."""
        target = self.get_object()
        if not target.is_mentor:
            return Response({"error": "User is not a mentor."}, status=status.HTTP_400_BAD_REQUEST)
        profile, _ = MentorProfile.objects.get_or_create(user=target)
        if request.method == 'GET':
            return Response(MentorProfileSerializer(profile).data)
        serializer = MentorProfileSerializer(profile, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)

from django.db.models import Sum
from courses.models import Course
from orders.models import Purchase

class AdminStatsView(APIView):
    permission_classes = [IsSuperAdminOrAdmin]

    def get(self, request):
        from django.utils import timezone
        import datetime
        from django.db.models import Sum, Count, OuterRef, Exists
        from django.db.models.functions import TruncMonth
        from courses.models import Course, Enrollment, AssignmentSubmission, LiveClass
        from orders.models import Purchase, Subscription
        from finance.models import Payout, Refund

        now = timezone.now()
        start_of_week = now - datetime.timedelta(days=7)
        start_of_month = now - datetime.timedelta(days=30)

        # Users
        total_students = User.objects.filter(is_student=True, is_teacher=False, is_superuser=False).count()
        new_students_week = User.objects.filter(is_student=True, is_teacher=False, is_superuser=False, date_joined__gte=start_of_week).count()
        new_students_month = User.objects.filter(is_student=True, is_teacher=False, is_superuser=False, date_joined__gte=start_of_month).count()
        active_students = User.objects.filter(is_student=True, is_teacher=False, is_superuser=False, is_active=True).count()
        inactive_students = User.objects.filter(is_student=True, is_teacher=False, is_superuser=False, is_active=False).count()
        total_teachers = User.objects.filter(is_teacher=True, is_superuser=False).count()
        # Admin Dashboard Completion gap fix: nav wants "Teachers/Mentors"
        # together, and the overview previously reported teachers only.
        total_mentors = User.objects.filter(is_mentor=True, is_superuser=False).count()

        # Courses
        total_courses = Course.objects.count()
        active_courses = Course.objects.filter(is_published=True).count()
        draft_courses = Course.objects.filter(is_published=False).count()
        
        top_courses_qs = Course.objects.annotate(enrollment_count=Count('enrollments')).order_by('-enrollment_count')[:5]
        top_courses = []
        for c in top_courses_qs:
            top_courses.append({
                "id": c.id,
                "title": c.title,
                "enrollments": c.enrollment_count
            })

        # Payments & Revenue
        revenue = Purchase.objects.filter(status='SUCCESS').aggregate(total=Sum('amount'))['total'] or 0.00
        current_month_revenue = Purchase.objects.filter(status='SUCCESS', created_at__gte=start_of_month).aggregate(total=Sum('amount'))['total'] or 0.00
        
        success_payments = Purchase.objects.filter(status='SUCCESS').count()
        pending_payments = Purchase.objects.filter(status='PENDING').count()
        failed_payments = Purchase.objects.filter(status='FAILED').count()

        # Monthly breakdown
        monthly_rev = Purchase.objects.filter(status='SUCCESS') \
            .annotate(month=TruncMonth('created_at')) \
            .values('month') \
            .annotate(total=Sum('amount')) \
            .order_by('month')
        revenue_breakdown = []
        for item in monthly_rev:
            month_date = item['month']
            month_str = month_date.strftime("%B %Y") if month_date else "Unknown"
            revenue_breakdown.append({
                "month": month_str,
                "total": float(item['total'] or 0.00)
            })

        # Enrollments
        total_enrollments = Enrollment.objects.count()
        new_enrollments_month = Enrollment.objects.filter(enrolled_at__gte=start_of_month).count()
        
        purchases = Purchase.objects.filter(
            user_id=OuterRef('user_id'),
            course_id=OuterRef('course_id'),
            status='SUCCESS'
        )
        paid_enrollments_count = Enrollment.objects.filter(Exists(purchases)).count()
        manual_enrollments_count = Enrollment.objects.filter(~Exists(purchases)).count()

        # Recent Activity
        recent_reg_qs = User.objects.filter(is_student=True, is_teacher=False, is_superuser=False).order_by('-date_joined')[:5]
        recent_registrations = []
        for u in recent_reg_qs:
            name = f"{u.first_name} {u.last_name}".strip() or u.username
            recent_registrations.append({
                "username": u.username,
                "name": name,
                "email": u.email,
                "date_joined": u.date_joined
            })

        recent_pay_qs = Purchase.objects.select_related('user', 'course').order_by('-created_at')[:5]
        recent_payments = []
        for p in recent_pay_qs:
            name = f"{p.user.first_name} {p.user.last_name}".strip() or p.user.username
            recent_payments.append({
                "id": p.id,
                "student_name": name,
                "course_title": p.course.title,
                "amount": float(p.amount),
                "status": p.status,
                "created_at": p.created_at
            })

        recent_enroll_qs = Enrollment.objects.select_related('user', 'course').order_by('-enrolled_at')[:5]
        recent_enrollments = []
        for e in recent_enroll_qs:
            name = f"{e.user.first_name} {e.user.last_name}".strip() or e.user.username
            recent_enrollments.append({
                "id": e.id,
                "student_name": name,
                "course_title": e.course.title,
                "enrolled_at": e.enrolled_at
            })

        # Admin Dashboard Completion gap fix: the operational counters the
        # improved Overview page needs that this endpoint didn't compute
        # before -- each a single .count() over an already-existing
        # queryset, no new business logic, matching every metric above.
        active_subscriptions_count = Subscription.objects.filter(status=Subscription.Status.ACTIVE).count()
        draft_payouts_count = Payout.objects.filter(status=Payout.Status.DRAFT).count()
        approved_payouts_pending_count = Payout.objects.filter(status=Payout.Status.APPROVED).count()
        pending_refunds_count = Refund.objects.filter(status__in=[Refund.Status.REQUESTED, Refund.Status.PROCESSING]).count()
        pending_assignment_grading_count = AssignmentSubmission.objects.filter(status=AssignmentSubmission.Status.SUBMITTED).count()
        upcoming_live_classes_count = LiveClass.objects.filter(
            status=LiveClass.ClassStatus.SCHEDULED, scheduled_start__gte=now,
        ).count()

        recent_refunds_qs = Refund.objects.select_related('customer').order_by('-requested_at')[:5]
        recent_refunds = []
        for r in recent_refunds_qs:
            name = f"{r.customer.first_name} {r.customer.last_name}".strip() or r.customer.username if r.customer else "Unknown"
            recent_refunds.append({
                "id": r.id,
                "customer_name": name,
                "amount": float(r.amount),
                "status": r.status,
                "requested_at": r.requested_at,
            })

        upcoming_live_classes_qs = LiveClass.objects.select_related('course').filter(
            status=LiveClass.ClassStatus.SCHEDULED, scheduled_start__gte=now,
        ).order_by('scheduled_start')[:5]
        upcoming_live_classes = []
        for lc in upcoming_live_classes_qs:
            upcoming_live_classes.append({
                "id": lc.id,
                "title": lc.title,
                "course_title": lc.course.title if lc.course else None,
                "scheduled_start": lc.scheduled_start,
            })

        return Response({
            "total_students": total_students,
            "new_students_week": new_students_week,
            "new_students_month": new_students_month,
            "active_students": active_students,
            "inactive_students": inactive_students,
            "total_teachers": total_teachers,
            "total_mentors": total_mentors,

            "total_courses": total_courses,
            "active_courses": active_courses,  # Published courses
            "draft_courses": draft_courses,
            "top_courses": top_courses,

            "total_revenue": float(revenue),
            "current_month_revenue": float(current_month_revenue),
            "success_payments": success_payments,
            "pending_payments": pending_payments,
            "failed_payments": failed_payments,
            "revenue_breakdown": revenue_breakdown,

            "total_enrollments": total_enrollments,
            "new_enrollments_month": new_enrollments_month,
            "paid_enrollments_count": paid_enrollments_count,
            "manual_enrollments_count": manual_enrollments_count,

            "recent_registrations": recent_registrations,
            "recent_payments": recent_payments,
            "recent_enrollments": recent_enrollments,

            # Admin Dashboard Completion gap fix.
            "active_subscriptions_count": active_subscriptions_count,
            "draft_payouts_count": draft_payouts_count,
            "approved_payouts_pending_count": approved_payouts_pending_count,
            "pending_refunds_count": pending_refunds_count,
            "pending_assignment_grading_count": pending_assignment_grading_count,
            "upcoming_live_classes_count": upcoming_live_classes_count,
            "recent_refunds": recent_refunds,
            "upcoming_live_classes": upcoming_live_classes,
        })

@method_decorator(ensure_csrf_cookie, name='get')
class CurrentUserView(APIView):
    """
    Payment/subscription CSRF hardening (final release audit finding):
    ensure_csrf_cookie added here -- this is the single most universally-
    called endpoint across the whole authenticated frontend (every page's
    own auth/onboarding check hits GET api/users/me/), so it's the most
    reliable place to guarantee the browser has picked up a csrftoken
    cookie before it ever reaches a payment/subscription action that now
    requires one (see orders/views.py's CSRFEnforcedJWTCookieAuthentication
    and SubscriptionMeView's own copy of this same decorator). Purely
    additive -- does not change this view's response body/logic, and is a
    no-op for mobile (no cookie jar to read a Set-Cookie header into, so
    mobile's own bearer-token calls to this same endpoint are unaffected).
    """
    def get(self, request):
        if not request.user.is_authenticated:
            return Response({"error": "Not authenticated"}, status=status.HTTP_401_UNAUTHORIZED)
        return Response({
            "id": request.user.id,
            "username": request.user.username,
            "email": request.user.email,
            "phone_number": request.user.phone_number,
            "is_student": getattr(request.user, 'is_student', False),
            "is_teacher": getattr(request.user, 'is_teacher', False),
            "is_mentor": getattr(request.user, 'is_mentor', False),
            "is_superuser": request.user.is_superuser,
            "is_staff": request.user.is_staff,
            "is_admin": bool(request.user.is_staff and not request.user.is_superuser),
            "is_onboarded": getattr(request.user, 'is_onboarded', False)
        })


class MyStudentsView(APIView):
    """
    Self-service "my students" for a teacher or mentor -- unlike
    AdminUserViewSet.teacher_students (IsSuperAdmin-only, admin looking up
    *any* teacher's roster), this lets the caller see their own, with each
    role's students coming from the correct, separate relationship:

    - Teacher: students enrolled in courses the teacher is assigned to via
      CourseInstructor (role=TEACHER), plus the legacy self-enrollment
      fallback for teachers predating that model.
    - Mentor: students explicitly assigned via Mentorship (status=ACTIVE).
      Deliberately NOT derived from course enrollment.
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        user = request.user

        if getattr(user, 'is_mentor', False):
            from .models import Mentorship
            mentorships = Mentorship.objects.filter(
                mentor=user, status=Mentorship.Status.ACTIVE
            ).select_related('student').order_by('-assigned_at')
            students = [m.student for m in mentorships]
        elif getattr(user, 'is_teacher', False):
            from courses.models import Course, Enrollment
            from django.db.models import Q
            teacher_courses = Course.objects.filter(
                Q(instructors__user=user, instructors__role='TEACHER') |
                Q(enrollments__user=user)  # legacy fallback, see CourseViewSet.get_queryset
            ).distinct()
            students = User.objects.filter(
                is_student=True, is_teacher=False, is_superuser=False,
                enrollments__course__in=teacher_courses
            ).distinct().order_by('-date_joined')
        else:
            return Response(
                {"error": "Only teacher or mentor accounts have a student roster."},
                status=status.HTTP_403_FORBIDDEN
            )

        data = [{
            "id": s.id,
            "username": s.username,
            "first_name": s.first_name,
            "last_name": s.last_name,
            "email": s.email,
            "phone_number": s.phone_number,
            "is_active": s.is_active,
            "date_joined": s.date_joined,
        } for s in students]
        return Response(data)

class OnboardingFieldsView(APIView):
    permission_classes = [permissions.AllowAny]

    def get(self, request):
        from .models import OnboardingField
        fields = OnboardingField.objects.all()
        data = []
        for f in fields:
            data.append({
                "name": f.name,
                "label": f.label,
                "type": f.field_type,
                "required": f.is_required,
                "options": f.options
            })
        return Response(data)

class SaveProfileView(APIView):
    def post(self, request):
        if not request.user.is_authenticated:
            return Response({"error": "Not authenticated"}, status=status.HTTP_401_UNAUTHORIZED)
            
        data = request.data
        user = request.user
        
        # We could validate against OnboardingField here, but for flexibility we just save it
        user.onboarding_data = data
        user.is_onboarded = True
        
        # Map common fields directly to User model if they exist
        if 'first_name' in data: user.first_name = data['first_name']
        if 'last_name' in data: user.last_name = data['last_name']
        if 'phone_number' in data: user.phone_number = data['phone_number']
        
        user.save()
        return Response({"message": "Profile saved successfully"})

from google.oauth2 import id_token
from google.auth.transport import requests as google_requests


def google_oauth_audiences():
    """
    Production Environment Verification follow-up. Builds the list of
    Google OAuth client ids MobileGoogleLoginView will accept as a token's
    `aud` claim, read fresh from settings on every call (deliberately NOT
    a precomputed module-level constant in core/settings.py -- that would
    be fixed at process start and wouldn't respond to Django's
    override_settings() test helper patching these individual variables).
    See GOOGLE_MOBILE_CLIENT_ID's own comment in core/settings.py for what
    each of the three settings is for. Deduplicated, order-preserving,
    empty entries dropped.
    """
    candidates = [settings.GOOGLE_MOBILE_CLIENT_ID, settings.GOOGLE_OAUTH_CLIENT_ID, settings.GOOGLE_ANDROID_CLIENT_ID]
    return [client_id for client_id in dict.fromkeys(candidates) if client_id]


class MobileGoogleLoginView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request):
        token = request.data.get('token')
        if not token:
            return Response({"error": "No token provided"}, status=status.HTTP_400_BAD_REQUEST)

        # Production Environment Verification follow-up: at least one
        # acceptable audience must be configured for this endpoint to
        # safely accept any login. Degrades in isolation (this one
        # endpoint returns an error) rather than crashing the whole app at
        # boot, the same precedent RAZORPAY_WEBHOOK_SECRET already
        # established for a not-yet-configured feature.
        audiences = google_oauth_audiences()
        if not audiences:
            logger.error("MobileGoogleLoginView called but no Google OAuth client ID is configured.")
            return Response(
                {"error": "Google Sign-In is not fully configured on the server."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        try:
            # Phase 3.9: `audience=` was previously omitted entirely --
            # verify_oauth2_token only checked the token's signature and
            # expiry, not WHICH OAuth client it was issued for. That meant
            # a validly-signed Google ID token from ANY Google OAuth
            # client (not just this app's own) would be accepted here.
            #
            # Production Environment Verification follow-up: `audience`
            # now accepts a LIST of acceptable client ids (google-auth's
            # underlying jwt.decode supports `str or list`, confirmed
            # against the installed package) rather than exactly one --
            # this app legitimately issues Google ID tokens whose `aud`
            # claim may be either the Web-application client (reused as
            # mobile's `webClientId`, see LoginScreen.tsx) or, in future,
            # a separate Android-type client. The token is accepted if its
            # audience matches ANY entry in this list.
            idinfo = id_token.verify_oauth2_token(
                token, google_requests.Request(), audience=audiences, clock_skew_in_seconds=10,
            )

            email = idinfo.get('email')
            if not email:
                return Response({"error": "Google token did not contain an email"}, status=status.HTTP_400_BAD_REQUEST)

            # Get or create user
            user, created = User.objects.get_or_create(email=email, defaults={
                'username': email.split('@')[0] + str(random.randint(1000, 9999)),
                'first_name': idinfo.get('given_name', ''),
                'last_name': idinfo.get('family_name', '')
            })

            # Generate JWT Tokens
            refresh = RefreshToken.for_user(user)
            
            return Response({
                "message": "Login successful",
                "user_id": user.id,
                "created": created,
                "is_onboarded": user.is_onboarded,
                "tokens": {
                    "access": str(refresh.access_token),
                    "refresh": str(refresh)
                }
            })

        except ValueError as e:
            # Invalid token
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)

from .models import OnboardingField
from .serializers import OnboardingFieldSerializer

class OnboardingFieldViewSet(viewsets.ModelViewSet):
    queryset = OnboardingField.objects.all().order_by('order')
    serializer_class = OnboardingFieldSerializer
    permission_classes = [IsSuperAdminOrAdmin]


from .models import Mentorship
from .serializers import MentorshipSerializer
from .permissions import IsSuperAdminOrReadOnlyMentorship


class MentorshipViewSet(viewsets.ModelViewSet):
    """
    The explicit, persistent student<->mentor relationship (see Mentorship
    model). Admin/staff manage assignments; a mentor sees only their own
    students, a student sees only their own mentors -- enforced here, not
    just hidden in the frontend.
    """
    serializer_class = MentorshipSerializer
    permission_classes = [permissions.IsAuthenticated, IsSuperAdminOrReadOnlyMentorship]

    def get_queryset(self):
        user = self.request.user
        qs = Mentorship.objects.all().select_related('student', 'mentor', 'assigned_by')
        if user.is_superuser or user.is_staff:
            # Admin use, e.g. a specific user's detail page: ?student= or
            # ?mentor= narrows the "see everything" queryset, same pattern
            # as LiveClassViewSet's ?instructor=/?student= filters.
            student_id = self.request.query_params.get('student')
            if student_id:
                qs = qs.filter(student_id=student_id)
            mentor_id = self.request.query_params.get('mentor')
            if mentor_id:
                qs = qs.filter(mentor_id=mentor_id)
            return qs
        if getattr(user, 'is_mentor', False):
            return qs.filter(mentor=user)
        return qs.filter(student=user)

    def perform_create(self, serializer):
        serializer.save(assigned_by=self.request.user)


from .models import TeacherProfile, MentorProfile
from .serializers import TeacherProfileSerializer, MentorProfileSerializer


class MyTeacherProfileView(APIView):
    """
    Self-service Teacher profile (professional info, kept separate from
    User -- see TeacherProfile model). Created lazily on first access so
    every existing teacher account keeps working without a data migration.
    """
    permission_classes = [permissions.IsAuthenticated]

    def _get_profile_or_403(self, request):
        if not getattr(request.user, 'is_teacher', False):
            return None, Response({"error": "Only teacher accounts have a teacher profile."}, status=status.HTTP_403_FORBIDDEN)
        profile, _ = TeacherProfile.objects.get_or_create(user=request.user)
        return profile, None

    def get(self, request):
        profile, error = self._get_profile_or_403(request)
        if error:
            return error
        return Response(TeacherProfileSerializer(profile).data)

    def patch(self, request):
        profile, error = self._get_profile_or_403(request)
        if error:
            return error
        serializer = TeacherProfileSerializer(profile, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)


class MyMentorProfileView(APIView):
    """Self-service Mentor profile -- see MentorProfile model."""
    permission_classes = [permissions.IsAuthenticated]

    def _get_profile_or_403(self, request):
        if not getattr(request.user, 'is_mentor', False):
            return None, Response({"error": "Only mentor accounts have a mentor profile."}, status=status.HTTP_403_FORBIDDEN)
        profile, _ = MentorProfile.objects.get_or_create(user=request.user)
        return profile, None

    def get(self, request):
        profile, error = self._get_profile_or_403(request)
        if error:
            return error
        return Response(MentorProfileSerializer(profile).data)

    def patch(self, request):
        profile, error = self._get_profile_or_403(request)
        if error:
            return error
        serializer = MentorProfileSerializer(profile, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)


from rest_framework import generics
from rest_framework.pagination import PageNumberPagination
from .models import AdminAuditLog
from .serializers import AdminAuditLogSerializer


class AdminAuditLogResultsSetPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = 'page_size'
    max_page_size = 100


class AdminAuditLogListView(generics.ListAPIView):
    """
    Admin Dashboard Completion gap fix. AdminAuditLog.record() has been
    writing role-change/course-instructor/refund/payout audit entries
    since Phase 3.9, but nothing has ever exposed them through the API --
    Django admin's read-only AdminAuditLogAdmin was the only way to see
    them. Read-only, list-only (matches the model's own admin lock:
    no add/change/delete permission there either).
    """
    serializer_class = AdminAuditLogSerializer
    permission_classes = [IsSuperAdminOrAdmin]
    pagination_class = AdminAuditLogResultsSetPagination

    def get_queryset(self):
        qs = AdminAuditLog.objects.select_related('actor').order_by('-created_at')
        params = self.request.query_params
        action_param = params.get('action')
        if action_param:
            qs = qs.filter(action=action_param)
        target_type = params.get('target_type')
        if target_type:
            qs = qs.filter(target_type=target_type)
        actor_id = params.get('actor_id')
        if actor_id:
            qs = qs.filter(actor_id=actor_id)
        return qs
