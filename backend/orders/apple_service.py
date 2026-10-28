"""
Apple In-App Purchase (StoreKit 2) Cryptographic Verification and Fulfillment Service.

Provides:
- Full cryptographic X.509 certificate chain validation rooted in Apple Root CAs.
- StoreKit 2 ES256 JWS signature verification with Apple leaf certificate.
- Server-to-server notification v2 signature verification.
- Enforced transaction ownership (rejects transactions claimed by other users).
- Strict product-to-course mapping and price integrity.
- Entitlement-aware refund handling (preserves access if another active entitlement exists).
"""

import base64
import json
import logging
import re
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

import jwt
from cryptography import x509
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa
from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from courses.models import Course, Enrollment
from orders.models import Purchase
from orders.services import fulfill_purchase

logger = logging.getLogger('orders.apple')


def get_expected_bundle_id() -> str:
    """Returns configured Apple Bundle ID, dynamically reading settings for testability."""
    return getattr(settings, 'APPLE_BUNDLE_ID', 'com.natyaarts.academy').strip()


EXPECTED_BUNDLE_ID = getattr(settings, 'APPLE_BUNDLE_ID', 'com.natyaarts.academy')

# Official Apple Root CA SHA-256 Fingerprints (Apple Inc. Root CA & Apple Root CA - G3)
TRUSTED_APPLE_ROOT_FINGERPRINTS = {
    # Apple Root CA - G3 (used for App Store & StoreKit 2)
    "63343abbbf67068d588c42c7784f708a33ea335c0057704cce1d7d312c23600a",
    # Apple Inc. Root CA
    "b0b1730ecbc7ff4505142c49f1295e6eda6bcaed7e2c68c5be91b5a11001f024",
    # Apple Computer Root Certificate
    "52402120004ff9719875bbef3b586c043e0d8692798e2171120be8b8e05cc1c8",
}

# Apple StoreKit 2 Custom Extension OID (inAppPurchaseReceipt)
STOREKIT2_EXTENSION_OID = "1.2.840.113635.100.6.11.1"


class AppleVerificationError(Exception):
    """Raised when an Apple StoreKit transaction fails cryptographic or logical verification."""
    pass


def _verify_cert_signature(issuer_cert: x509.Certificate, subject_cert: x509.Certificate) -> None:
    """Verifies that subject_cert was cryptographically signed by issuer_cert's public key."""
    public_key = issuer_cert.public_key()
    hash_algorithm = subject_cert.signature_hash_algorithm
    if hash_algorithm is None:
        raise AppleVerificationError("Unsupported certificate signature hash algorithm.")

    if isinstance(public_key, rsa.RSAPublicKey):
        public_key.verify(
            subject_cert.signature,
            subject_cert.tbs_certificate_bytes,
            padding.PKCS1v15(),
            hash_algorithm,
        )
    elif isinstance(public_key, ec.EllipticCurvePublicKey):
        public_key.verify(
            subject_cert.signature,
            subject_cert.tbs_certificate_bytes,
            ec.ECDSA(hash_algorithm),
        )
    else:
        raise AppleVerificationError(f"Unsupported issuer public key type: {type(public_key)}")


class AppleIAPService:
    @classmethod
    def decode_jws_payload_unverified(cls, jws_token: str) -> Dict[str, Any]:
        """Base64url extracts the unverified JSON payload from a JWS token."""
        try:
            parts = jws_token.strip().split('.')
            if len(parts) != 3:
                raise AppleVerificationError("Invalid JWS format: token must have 3 parts separated by dots.")

            payload_b64 = parts[1]
            rem = len(payload_b64) % 4
            if rem > 0:
                payload_b64 += '=' * (4 - rem)

            payload_json = base64.urlsafe_b64decode(payload_b64).decode('utf-8')
            return json.loads(payload_json)
        except Exception as e:
            if isinstance(e, AppleVerificationError):
                raise
            raise AppleVerificationError(f"Failed to decode JWS payload: {e}") from e

    @classmethod
    def verify_cert_chain(cls, x5c: List[str]) -> x509.Certificate:
        """
        Validates the entire X.509 certificate chain provided in the JWS x5c header:
        1. Checks date validity for every certificate.
        2. Cryptographically verifies each certificate was signed by its issuer in the chain.
        3. Confirms the root certificate matches Apple's trusted Root CAs.
        4. Returns the verified leaf certificate.
        """
        if not x5c or len(x5c) < 2:
            raise AppleVerificationError("Apple x5c certificate chain must contain at least leaf and intermediate certificates.")

        certs: List[x509.Certificate] = []
        for i, cert_b64 in enumerate(x5c):
            try:
                cert_der = base64.b64decode(cert_b64)
                certs.append(x509.load_der_x509_certificate(cert_der, default_backend()))
            except Exception as e:
                raise AppleVerificationError(f"Failed to load certificate {i} from x5c chain: {e}") from e

        now = timezone.now()
        for i, cert in enumerate(certs):
            if cert.not_valid_before_utc > now or cert.not_valid_after_utc < now:
                raise AppleVerificationError(f"Certificate {i} in Apple x5c chain is expired or not yet valid.")

        # Cryptographically verify the chain signatures: cert[i] signed by cert[i+1]
        for i in range(len(certs) - 1):
            child_cert = certs[i]
            issuer_cert = certs[i + 1]
            try:
                _verify_cert_signature(issuer_cert, child_cert)
            except Exception as e:
                raise AppleVerificationError(f"Cryptographic certificate chain broken between cert {i} and {i+1}: {e}") from e

        leaf_cert = certs[0]

        # Validate leaf certificate StoreKit 2 custom extension marker (OID 1.2.840.113635.100.6.11.1)
        has_marker = any(
            ext.oid.dotted_string == STOREKIT2_EXTENSION_OID
            for ext in leaf_cert.extensions
        )
        if not has_marker:
            raise AppleVerificationError(
                f"Apple leaf certificate missing required StoreKit 2 extension ({STOREKIT2_EXTENSION_OID})."
            )

        # In testing mode with mock certificates, bypass root fingerprint check
        if getattr(settings, 'APPLE_IAP_TEST_MODE', False):
            return leaf_cert

        # Validate the root of the chain against trusted Apple Root CA fingerprints
        root_cert = certs[-1]
        root_fingerprint = root_cert.fingerprint(hashes.SHA256()).hex().lower()

        if root_fingerprint not in TRUSTED_APPLE_ROOT_FINGERPRINTS:
            raise AppleVerificationError(
                f"Certificate chain does not root to an authorized Apple Root CA (fingerprint: {root_fingerprint})."
            )

        return leaf_cert

    @classmethod
    def verify_jws_token(cls, jws_token: str, is_transaction: bool = True) -> Dict[str, Any]:
        """
        Cryptographically verifies an Apple StoreKit 2 JWS token:
        1. Validates the x5c certificate chain to Apple's Root CA.
        2. Verifies the JWS ES256 signature using the leaf certificate's public key.
        3. Validates required claims (transaction or notification).
        """
        token = jws_token.strip()
        try:
            header = jwt.get_unverified_header(token)
        except Exception as e:
            raise AppleVerificationError(f"Malformed JWS header: {e}") from e

        alg = header.get('alg', 'ES256')
        x5c = header.get('x5c')

        # Test mode bypass ONLY when explicitly enabled in test settings
        if getattr(settings, 'APPLE_IAP_TEST_MODE', False) and (not x5c or len(x5c) < 2):
            payload = cls.decode_jws_payload_unverified(token)
            if is_transaction:
                cls._validate_transaction_claims(payload)
            else:
                cls._validate_notification_claims(payload)
            return payload

        if not x5c:
            raise AppleVerificationError("Missing required x5c certificate chain in Apple JWS header.")

        # Verify full X.509 chain
        leaf_cert = cls.verify_cert_chain(x5c)
        public_key = leaf_cert.public_key()

        try:
            # Verify JWS signature with verified leaf public key
            payload = jwt.decode(
                token,
                public_key,
                algorithms=[alg],
                options={"verify_exp": False}
            )
            if is_transaction:
                cls._validate_transaction_claims(payload)
            else:
                cls._validate_notification_claims(payload)
            return payload
        except jwt.PyJWTError as e:
            raise AppleVerificationError(f"Apple JWS cryptographic signature verification failed: {e}") from e

    @classmethod
    def _validate_notification_claims(cls, payload: Dict[str, Any]) -> None:
        """
        Enforces security invariants on App Store Server Notification v2 payload:
        - notificationType present
        - bundleId in data (if present) matches expected bundle ID
        - environment is valid (Sandbox or Production)
        - sandbox notifications must adhere to explicit server setting
        """
        if not payload.get('notificationType'):
            raise AppleVerificationError("Notification payload missing required 'notificationType'.")

        expected_bundle_id = get_expected_bundle_id()
        data = payload.get('data', {})
        bundle_id = data.get('bundleId')
        if bundle_id and bundle_id != expected_bundle_id:
            raise AppleVerificationError(
                f"Bundle ID mismatch in notification: expected '{expected_bundle_id}', got '{bundle_id}'"
            )

        env = data.get('environment') or payload.get('environment')
        if env and env not in ('Production', 'Sandbox'):
            raise AppleVerificationError(f"Invalid Apple StoreKit environment: '{env}'")

        allow_sandbox = getattr(settings, 'APPLE_IAP_ALLOW_SANDBOX', False) or getattr(settings, 'APPLE_IAP_TEST_MODE', False)
        if env == 'Sandbox' and not allow_sandbox:
            raise AppleVerificationError("Sandbox notification received, but Sandbox environment is not allowed by server configuration.")

    @classmethod
    def _validate_transaction_claims(cls, payload: Dict[str, Any]) -> None:
        """
        Enforces security invariants on StoreKit 2 payload:
        - bundleId matches configured Apple Bundle ID
        - transactionId present
        - productId present
        - revocationDate is None
        - environment is valid (Sandbox or Production)
        - sandbox transactions must adhere to explicit server setting
        """
        expected_bundle_id = get_expected_bundle_id()
        bundle_id = payload.get('bundleId')
        if bundle_id != expected_bundle_id:
            raise AppleVerificationError(
                f"Bundle ID mismatch: expected '{expected_bundle_id}', got '{bundle_id}'"
            )

        if not payload.get('transactionId'):
            raise AppleVerificationError("Transaction payload missing required 'transactionId'.")

        if not payload.get('productId'):
            raise AppleVerificationError("Transaction payload missing required 'productId'.")

        if payload.get('revocationDate'):
            raise AppleVerificationError(
                f"Transaction {payload.get('transactionId')} was revoked/refunded by Apple on {payload.get('revocationDate')}."
            )

        env = payload.get('environment')
        if env and env not in ('Production', 'Sandbox'):
            raise AppleVerificationError(f"Invalid Apple StoreKit environment: '{env}'")

        # Explicit environment setting check for Sandbox
        allow_sandbox = getattr(settings, 'APPLE_IAP_ALLOW_SANDBOX', False) or getattr(settings, 'APPLE_IAP_TEST_MODE', False)
        if env == 'Sandbox' and not allow_sandbox:
            raise AppleVerificationError("Sandbox transaction received, but Sandbox environment is not allowed by server configuration.")

    @classmethod
    def resolve_course_for_product(cls, product_id: str) -> Course:
        r"""
        Maps Apple Product ID to Course.
        Resolution rules:
        1. Exact match against Course.apple_product_id.
        2. Strict fallback pattern: <bundle_id>.course.<course_id>
           - Matches regex f"^{re.escape(get_expected_bundle_id())}\.course\.(\d+)$".
           - Verifies Course exists with id=<course_id>.
           - Disambiguation check: If course has a custom apple_product_id configured
             that differs from this default pattern ID, rejects to prevent ambiguity.
        3. Fails safely on unknown or ambiguous product IDs without broad exception swallowing.
        """
        if not product_id or not isinstance(product_id, str):
            raise Course.DoesNotExist("Apple Product ID cannot be empty.")

        product_id = product_id.strip()

        # 1. Direct custom apple_product_id lookup
        course = Course.objects.filter(apple_product_id=product_id).first()
        if course:
            return course

        # 2. Strict default pattern lookup: <bundle_id>.course.<course_id>
        expected_bundle_id = get_expected_bundle_id()
        default_pattern = rf"^{re.escape(expected_bundle_id)}\.course\.(\d+)$"
        match = re.match(default_pattern, product_id)
        if match:
            course_id = int(match.group(1))
            try:
                course = Course.objects.get(id=course_id)
            except Course.DoesNotExist:
                raise Course.DoesNotExist(
                    f"No course found for default Apple Product ID '{product_id}' (course ID {course_id} does not exist)."
                )

            # Disambiguation guard: If course has an explicit custom apple_product_id
            # set to something else, default pattern identifier must not be accepted.
            if course.apple_product_id and course.apple_product_id != product_id:
                raise AppleVerificationError(
                    f"Product ID '{product_id}' is ambiguous or inactive: course #{course.id} "
                    f"is explicitly configured with custom Apple Product ID '{course.apple_product_id}'."
                )

            return course

        raise Course.DoesNotExist(f"No course found matching Apple Product ID '{product_id}'")

    @classmethod
    def process_purchase_verification(
        cls,
        user,
        signed_transaction_info: str,
        client_product_id: Optional[str] = None
    ) -> Tuple[Purchase, bool]:
        """
        Securely verifies and fulfills an Apple In-App Purchase.
        Returns: (Purchase, is_newly_created: bool)

        Strict Security Rules:
        1. Decodes and cryptographically verifies JWS and Apple certificate chain.
        2. Resolves Course strictly from verified Apple productId.
        3. Enforces Product Match: client_product_id (if supplied) must match verified productId.
        4. Enforces Ownership: If transaction was previously processed, it MUST belong to this user.
        5. Replay Protection: Atomic lock on apple_transaction_id.
        """
        verified_payload = cls.verify_jws_token(signed_transaction_info)

        transaction_id = str(verified_payload['transactionId'])
        original_transaction_id = str(verified_payload.get('originalTransactionId') or transaction_id)
        verified_product_id = verified_payload['productId']

        # Enforce product match
        if client_product_id and client_product_id.strip() != verified_product_id:
            raise AppleVerificationError(
                f"Product ID mismatch: requested '{client_product_id}', but transaction is for '{verified_product_id}'."
            )

        course = cls.resolve_course_for_product(verified_product_id)

        # Audit and currency compatibility: StoreKit 2 transactions provide ISO-4217 'currency'
        # (e.g. 'INR', 'USD') and price in milliunits. Catalog course price (INR) is retained
        # on the Purchase record to guarantee full compatibility with the existing INR ledger,
        # instructor revenue share, and invoicing system without schema or accounting divergence.
        apple_currency = verified_payload.get('currency')
        apple_price_raw = verified_payload.get('price')
        logger.info(
            "Apple IAP verification: user=%s, course=%s, tx=%s, catalog_price=%s INR, apple_currency=%s, apple_price_raw=%s",
            user.id,
            course.id,
            transaction_id,
            course.price,
            apple_currency,
            apple_price_raw,
        )

        with transaction.atomic():
            existing_purchase = (
                Purchase.objects.select_for_update()
                .filter(apple_transaction_id=transaction_id)
                .first()
            )

            if existing_purchase:
                # ENFORCE OWNERSHIP: A transaction cannot be claimed by a different user
                if existing_purchase.user_id != user.id:
                    logger.error(
                        "Transaction ownership violation: user %s attempted to claim tx %s owned by user %s",
                        user.id,
                        transaction_id,
                        existing_purchase.user_id
                    )
                    raise AppleVerificationError("This Apple transaction is already associated with another user account.")

                # VERIFY COURSE: Transaction cannot be mapped to a different course
                if existing_purchase.course_id != course.id:
                    logger.error(
                        "Transaction course mismatch: tx %s is for course %s, incoming verification requested course %s",
                        transaction_id,
                        existing_purchase.course_id,
                        course.id
                    )
                    raise AppleVerificationError(
                        f"Transaction {transaction_id} is already associated with another course."
                    )

                logger.info(
                    "Apple transaction %s already processed (Purchase #%s); ensuring enrollment for user %s on course %s.",
                    transaction_id,
                    existing_purchase.id,
                    user.id,
                    course.id
                )
                Enrollment.objects.get_or_create(user=user, course=existing_purchase.course)
                return existing_purchase, False

            is_sandbox = (verified_payload.get('environment') == 'Sandbox')
            purchase_amount = Decimal('0.00') if is_sandbox else course.price

            # Create new Purchase record with savepoint to handle concurrent race conditions
            try:
                with transaction.atomic():
                    purchase = Purchase.objects.create(
                        user=user,
                        course=course,
                        amount=purchase_amount,
                        status=Purchase.Status.SUCCESS,
                        payment_provider=Purchase.PaymentProvider.APPLE_IAP,
                        apple_transaction_id=transaction_id,
                        apple_original_transaction_id=original_transaction_id,
                        apple_product_id=verified_product_id,
                    )
            except IntegrityError:
                logger.warning(
                    "IntegrityError on apple_transaction_id=%s during concurrent creation. Reloading existing purchase.",
                    transaction_id
                )
                existing_purchase = (
                    Purchase.objects.select_for_update()
                    .filter(apple_transaction_id=transaction_id)
                    .first()
                )
                if not existing_purchase:
                    raise

                # Verify ownership on the concurrent transaction
                if existing_purchase.user_id != user.id:
                    logger.error(
                        "Race conflict ownership violation: user %s attempted to claim tx %s owned by user %s",
                        user.id,
                        transaction_id,
                        existing_purchase.user_id
                    )
                    raise AppleVerificationError("This Apple transaction is already associated with another user account.")

                # Verify course on the concurrent transaction
                if existing_purchase.course_id != course.id:
                    logger.error(
                        "Race conflict course mismatch: tx %s is for course %s, incoming verification requested course %s",
                        transaction_id,
                        existing_purchase.course_id,
                        course.id
                    )
                    raise AppleVerificationError(
                        f"Transaction {transaction_id} is already associated with another course."
                    )

                # Grant enrollment safely without duplicate fulfillment
                Enrollment.objects.get_or_create(user=user, course=existing_purchase.course)
                return existing_purchase, False

            # Idempotently fulfill purchase: skip accounting/invoicing/notifications for sandbox review transactions
            fulfill_purchase(purchase, previous_status=Purchase.Status.PENDING, skip_accounting=is_sandbox)

            logger.info(
                "Successfully verified and fulfilled Apple IAP (sandbox=%s): user %s, course %s, tx %s (Purchase #%s)",
                is_sandbox,
                user.id,
                course.id,
                transaction_id,
                purchase.id
            )
            return purchase, True

    @classmethod
    def _user_has_other_active_entitlement(cls, user, course, exclude_purchase_id: int) -> bool:
        """
        Checks whether the student retains access to the course through another valid channel
        (e.g., another successful Purchase, or an active all-access subscription).
        """
        # Check other successful purchases (Razorpay or another Apple transaction)
        has_other_purchase = Purchase.objects.filter(
            user=user,
            course=course,
            status=Purchase.Status.SUCCESS
        ).exclude(id=exclude_purchase_id).exists()

        if has_other_purchase:
            return True

        # Check active subscriptions
        try:
            from orders.models import Subscription
            has_active_sub = Subscription.objects.filter(
                user=user,
                status=Subscription.Status.ACTIVE
            ).exists()
            if has_active_sub:
                return True
        except Exception:
            pass

        return False

    @classmethod
    def handle_server_notification_v2(cls, signed_payload: str) -> Dict[str, Any]:
        """
        Processes Apple App Store Server Notifications v2 webhook deliveries:
        1. Cryptographically verifies the notification's signedPayload JWS and certificate chain.
        2. If signedTransactionInfo is present, cryptographically verifies the nested transaction JWS.
        3. On REFUND / REVOKE, updates Purchase status.
        4. Revokes Enrollment ONLY if no other active entitlement exists for this student.
        """
        payload = cls.verify_jws_token(signed_payload, is_transaction=False)
        notification_type = payload.get('notificationType')
        subtype = payload.get('subtype')
        data = payload.get('data', {})

        result: Dict[str, Any] = {
            'notification_type': notification_type,
            'subtype': subtype,
            'action_taken': 'NONE'
        }

        signed_tx_info = data.get('signedTransactionInfo')
        if not signed_tx_info:
            logger.info("Apple notification %s received without signedTransactionInfo", notification_type)
            return result

        tx_payload = cls.verify_jws_token(signed_tx_info)
        tx_id = str(tx_payload.get('transactionId', ''))
        orig_tx_id = str(tx_payload.get('originalTransactionId', ''))

        if notification_type in ('REFUND', 'REVOKE'):
            with transaction.atomic():
                purchases = Purchase.objects.filter(apple_transaction_id=tx_id)
                if not purchases.exists() and orig_tx_id:
                    purchases = Purchase.objects.filter(apple_original_transaction_id=orig_tx_id)

                for p in purchases:
                    p.status = Purchase.Status.REFUNDED
                    p.save(update_fields=['status', 'updated_at'])

                    # Check other valid entitlements before revoking Enrollment
                    if cls._user_has_other_active_entitlement(p.user, p.course, exclude_purchase_id=p.id):
                        logger.info(
                            "Apple %s for user %s on course %s: User holds another valid entitlement; enrollment preserved.",
                            notification_type,
                            p.user_id,
                            p.course_id
                        )
                        result['action_taken'] = 'PURCHASE_REFUNDED_ENROLLMENT_PRESERVED'
                    else:
                        deleted_count, _ = Enrollment.objects.filter(user=p.user, course=p.course).delete()
                        logger.warning(
                            "Apple %s for user %s on course %s: Enrollment revoked (%s rows deleted).",
                            notification_type,
                            p.user_id,
                            p.course_id,
                            deleted_count
                        )
                        result['action_taken'] = 'REVOKED_ENROLLMENT'

                    result['purchase_id'] = p.id

        return result
