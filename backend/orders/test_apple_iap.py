import base64
import datetime
import json
import uuid
from decimal import Decimal
from unittest.mock import patch

import jwt
from cryptography import x509
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from courses.models import Course, Enrollment
from orders.apple_service import AppleIAPService, AppleVerificationError
from orders.models import Purchase

User = get_user_model()


def make_dummy_jws(payload_dict: dict) -> str:
    """Helper to create a dummy JWS token (header.payload.signature) for testing without cert chain."""
    header = {"alg": "ES256"}
    header_b64 = base64.urlsafe_b64encode(json.dumps(header).encode()).decode().rstrip('=')
    payload_b64 = base64.urlsafe_b64encode(json.dumps(payload_dict).encode()).decode().rstrip('=')
    signature_b64 = base64.urlsafe_b64encode(b"dummy_test_signature").decode().rstrip('=')
    return f"{header_b64}.{payload_b64}.{signature_b64}"


def generate_test_cert_chain(expired: bool = False, include_marker: bool = True):
    """
    Generates a genuine 2-level X.509 certificate chain (Root CA -> Leaf)
    using ECDSA secp256r1 for cryptographic verification testing.
    Includes Apple StoreKit 2 custom extension (OID 1.2.840.113635.100.6.11.1).
    """
    now = timezone.now()
    valid_from = now - datetime.timedelta(days=1)
    valid_to = now - datetime.timedelta(minutes=1) if expired else now + datetime.timedelta(days=365)

    # 1. Generate Root CA Key & Self-Signed Cert
    root_key = ec.generate_private_key(ec.SECP256R1(), default_backend())
    root_name = x509.Name([
        x509.NameAttribute(NameOID.COUNTRY_NAME, "US"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Apple Inc."),
        x509.NameAttribute(NameOID.COMMON_NAME, "Apple Root CA"),
    ])
    root_cert = (
        x509.CertificateBuilder()
        .subject_name(root_name)
        .issuer_name(root_name)
        .public_key(root_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(valid_from)
        .not_valid_after(now + datetime.timedelta(days=3650))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(root_key, hashes.SHA256(), default_backend())
    )

    # 2. Generate Leaf Key & Cert Signed by Root
    leaf_key = ec.generate_private_key(ec.SECP256R1(), default_backend())
    leaf_name = x509.Name([
        x509.NameAttribute(NameOID.COUNTRY_NAME, "US"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Apple Inc."),
        x509.NameAttribute(NameOID.COMMON_NAME, "Mac App Store and iOS App Store Receipt Signing"),
    ])
    builder = (
        x509.CertificateBuilder()
        .subject_name(leaf_name)
        .issuer_name(root_name)
        .public_key(leaf_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(valid_from)
        .not_valid_after(valid_to)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
    )
    if include_marker:
        builder = builder.add_extension(
            x509.UnrecognizedExtension(
                x509.ObjectIdentifier("1.2.840.113635.100.6.11.1"),
                b"\x04\x00"
            ),
            critical=False
        )
    leaf_cert = builder.sign(root_key, hashes.SHA256(), default_backend())

    x5c = [
        base64.b64encode(leaf_cert.public_bytes(serialization.Encoding.DER)).decode('ascii'),
        base64.b64encode(root_cert.public_bytes(serialization.Encoding.DER)).decode('ascii'),
    ]
    return leaf_key, root_key, leaf_cert, root_cert, x5c


@override_settings(APPLE_IAP_TEST_MODE=True, APPLE_IAP_ALLOW_SANDBOX=True, APPLE_BUNDLE_ID='com.natyaarts.academy')
class AppleIAPTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            username="+919876500001",
            phone_number="+919876500001",
            password="testpassword123",
            is_student=True,
            is_onboarded=True
        )
        self.user2 = User.objects.create_user(
            username="+919876500002",
            phone_number="+919876500002",
            password="testpassword123",
            is_student=True,
            is_onboarded=True
        )
        self.course = Course.objects.create(
            title="Bharatanatyam Foundations",
            description="Fundamental adavus and postures",
            price=Decimal("1499.00"),
            is_published=True
        )
        self.verify_url = '/api/orders/apple/verify-purchase/'
        self.webhook_url = '/api/orders/webhook/apple/'

    def test_missing_signed_transaction_info_returns_400(self):
        self.client.force_authenticate(user=self.user)
        res = self.client.post(self.verify_url, {}, format='json')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("error", res.data)

    def test_bundle_id_mismatch_rejected(self):
        self.client.force_authenticate(user=self.user)
        payload = {
            "bundleId": "com.fraudulent.app",
            "transactionId": "1000000999",
            "productId": f"com.natyaarts.academy.course.{self.course.id}"
        }
        jws = make_dummy_jws(payload)
        res = self.client.post(self.verify_url, {"signed_transaction_info": jws}, format='json')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("Bundle ID mismatch", res.data["error"])

    def test_revoked_transaction_rejected(self):
        self.client.force_authenticate(user=self.user)
        payload = {
            "bundleId": "com.natyaarts.academy",
            "transactionId": "1000000998",
            "productId": f"com.natyaarts.academy.course.{self.course.id}",
            "revocationDate": 1730000000000
        }
        jws = make_dummy_jws(payload)
        res = self.client.post(self.verify_url, {"signed_transaction_info": jws}, format='json')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("revoked/refunded", res.data["error"])

    def test_course_not_found_returns_404(self):
        self.client.force_authenticate(user=self.user)
        payload = {
            "bundleId": "com.natyaarts.academy",
            "transactionId": "1000000997",
            "productId": "com.natyaarts.academy.course.999999"
        }
        jws = make_dummy_jws(payload)
        res = self.client.post(self.verify_url, {"signed_transaction_info": jws}, format='json')
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    def test_successful_purchase_unlocks_course_and_creates_records(self):
        self.client.force_authenticate(user=self.user)
        tx_id = "2000000123456789"
        payload = {
            "bundleId": "com.natyaarts.academy",
            "transactionId": tx_id,
            "originalTransactionId": tx_id,
            "productId": f"com.natyaarts.academy.course.{self.course.id}",
            "purchaseDate": 1730100000000,
            "environment": "Production"
        }
        jws = make_dummy_jws(payload)

        res = self.client.post(self.verify_url, {
            "signed_transaction_info": jws,
            "product_id": f"com.natyaarts.academy.course.{self.course.id}"
        }, format='json')

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data["status"], "SUCCESS")
        self.assertTrue(res.data["enrolled"])
        self.assertEqual(res.data["course_id"], self.course.id)

        # Check Purchase record in DB
        purchase = Purchase.objects.get(apple_transaction_id=tx_id)
        self.assertEqual(purchase.user, self.user)
        self.assertEqual(purchase.course, self.course)
        self.assertEqual(purchase.amount, self.course.price)
        self.assertEqual(purchase.payment_provider, Purchase.PaymentProvider.APPLE_IAP)
        self.assertEqual(purchase.status, Purchase.Status.SUCCESS)

        # Check Enrollment record in DB
        self.assertTrue(Enrollment.objects.filter(user=self.user, course=self.course).exists())

    def test_replay_protection_is_idempotent(self):
        self.client.force_authenticate(user=self.user)
        tx_id = "2000000777888999"
        payload = {
            "bundleId": "com.natyaarts.academy",
            "transactionId": tx_id,
            "originalTransactionId": tx_id,
            "productId": f"com.natyaarts.academy.course.{self.course.id}",
        }
        jws = make_dummy_jws(payload)

        # 1st call: Success
        res1 = self.client.post(self.verify_url, {"signed_transaction_info": jws}, format='json')
        self.assertEqual(res1.status_code, status.HTTP_200_OK)
        self.assertEqual(res1.data["status"], "SUCCESS")

        # 2nd call: Idempotent Already Processed
        res2 = self.client.post(self.verify_url, {"signed_transaction_info": jws}, format='json')
        self.assertEqual(res2.status_code, status.HTTP_200_OK)
        self.assertEqual(res2.data["status"], "ALREADY_PROCESSED")
        self.assertTrue(res2.data["enrolled"])

        # Exactly 1 Purchase and 1 Enrollment row created
        self.assertEqual(Purchase.objects.filter(apple_transaction_id=tx_id).count(), 1)
        self.assertEqual(Enrollment.objects.filter(user=self.user, course=self.course).count(), 1)

    def test_ownership_enforcement_rejects_second_user_claiming_transaction(self):
        """User B cannot claim a transaction previously processed for User A."""
        tx_id = "2000000333222111"
        payload = {
            "bundleId": "com.natyaarts.academy",
            "transactionId": tx_id,
            "originalTransactionId": tx_id,
            "productId": f"com.natyaarts.academy.course.{self.course.id}",
        }
        jws = make_dummy_jws(payload)

        # User A verifies transaction -> Success
        self.client.force_authenticate(user=self.user)
        res1 = self.client.post(self.verify_url, {"signed_transaction_info": jws}, format='json')
        self.assertEqual(res1.status_code, status.HTTP_200_OK)
        self.assertEqual(res1.data["status"], "SUCCESS")

        # User B tries to verify the same transaction -> 400 Bad Request
        self.client.force_authenticate(user=self.user2)
        res2 = self.client.post(self.verify_url, {"signed_transaction_info": jws}, format='json')
        self.assertEqual(res2.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("already associated with another user", res2.data["error"])
        # User B must NOT be enrolled
        self.assertFalse(Enrollment.objects.filter(user=self.user2, course=self.course).exists())

    def test_product_id_mismatch_rejected(self):
        """Client requesting Course A cannot pass a transaction token for Course B."""
        course2 = Course.objects.create(
            title="Carnatic Vocal Level 1",
            description="Vocal training",
            price=Decimal("2999.00"),
            is_published=True
        )
        self.client.force_authenticate(user=self.user)
        payload = {
            "bundleId": "com.natyaarts.academy",
            "transactionId": "2000000666111222",
            "productId": f"com.natyaarts.academy.course.{self.course.id}",
        }
        jws = make_dummy_jws(payload)

        # Client requested course2's product_id, but JWS is for course 1
        res = self.client.post(self.verify_url, {
            "signed_transaction_info": jws,
            "product_id": f"com.natyaarts.academy.course.{course2.id}"
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("Product ID mismatch", res.data["error"])

    def test_price_cannot_be_spoofed_by_client(self):
        self.client.force_authenticate(user=self.user)
        tx_id = "2000000555444333"
        payload = {
            "bundleId": "com.natyaarts.academy",
            "transactionId": tx_id,
            "originalTransactionId": tx_id,
            "productId": f"com.natyaarts.academy.course.{self.course.id}",
        }
        jws = make_dummy_jws(payload)

        res = self.client.post(self.verify_url, {
            "signed_transaction_info": jws,
            "price": "1.00"
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_200_OK)

        purchase = Purchase.objects.get(apple_transaction_id=tx_id)
        self.assertEqual(purchase.amount, Decimal("1499.00"))

    def test_refund_preserves_enrollment_if_other_valid_purchase_exists(self):
        """If a user has another active purchase for this course, refunding the Apple purchase preserves access."""
        # 1. Existing Razorpay purchase
        Purchase.objects.create(
            user=self.user,
            course=self.course,
            amount=self.course.price,
            status=Purchase.Status.SUCCESS,
            payment_provider=Purchase.PaymentProvider.RAZORPAY,
            razorpay_payment_id="pay_other_active_123"
        )
        # 2. Apple purchase
        tx_id = "2000000888999111"
        apple_purchase = Purchase.objects.create(
            user=self.user,
            course=self.course,
            amount=self.course.price,
            status=Purchase.Status.SUCCESS,
            payment_provider=Purchase.PaymentProvider.APPLE_IAP,
            apple_transaction_id=tx_id,
            apple_original_transaction_id=tx_id,
            apple_product_id=f"com.natyaarts.academy.course.{self.course.id}"
        )
        Enrollment.objects.create(user=self.user, course=self.course)

        # 3. Simulate Apple Refund notification
        tx_info_jws = make_dummy_jws({
            "bundleId": "com.natyaarts.academy",
            "transactionId": tx_id,
            "originalTransactionId": tx_id,
            "productId": f"com.natyaarts.academy.course.{self.course.id}"
        })
        notification_jws = make_dummy_jws({
            "notificationType": "REFUND",
            "subtype": None,
            "data": {
                "bundleId": "com.natyaarts.academy",
                "environment": "Sandbox",
                "signedTransactionInfo": tx_info_jws
            }
        })

        res = self.client.post(self.webhook_url, {"signedPayload": notification_jws}, format='json')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data["result"]["action_taken"], "PURCHASE_REFUNDED_ENROLLMENT_PRESERVED")

        # Enrollment is preserved because Razorpay purchase is still active
        apple_purchase.refresh_from_db()
        self.assertEqual(apple_purchase.status, Purchase.Status.REFUNDED)
        self.assertTrue(Enrollment.objects.filter(user=self.user, course=self.course).exists())

    def test_apple_webhook_refund_revokes_enrollment_when_sole_entitlement(self):
        tx_id = "2000000999111222"
        purchase = Purchase.objects.create(
            user=self.user,
            course=self.course,
            amount=self.course.price,
            status=Purchase.Status.SUCCESS,
            payment_provider=Purchase.PaymentProvider.APPLE_IAP,
            apple_transaction_id=tx_id,
            apple_original_transaction_id=tx_id,
            apple_product_id=f"com.natyaarts.academy.course.{self.course.id}"
        )
        Enrollment.objects.create(user=self.user, course=self.course)

        tx_info_jws = make_dummy_jws({
            "bundleId": "com.natyaarts.academy",
            "transactionId": tx_id,
            "originalTransactionId": tx_id,
            "productId": f"com.natyaarts.academy.course.{self.course.id}"
        })
        notification_jws = make_dummy_jws({
            "notificationType": "REFUND",
            "subtype": None,
            "data": {
                "bundleId": "com.natyaarts.academy",
                "environment": "Sandbox",
                "signedTransactionInfo": tx_info_jws
            }
        })

        res = self.client.post(self.webhook_url, {"signedPayload": notification_jws}, format='json')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data["result"]["action_taken"], "REVOKED_ENROLLMENT")

        purchase.refresh_from_db()
        self.assertEqual(purchase.status, Purchase.Status.REFUNDED)
        self.assertFalse(Enrollment.objects.filter(user=self.user, course=self.course).exists())

    def test_genuine_ecdsa_x509_cert_chain_verification(self):
        """
        Tests the actual cryptographic verification of an X.509 cert chain and JWS ES256 signature.
        """
        leaf_key, root_key, leaf_cert, root_cert, x5c = generate_test_cert_chain(expired=False)

        claims = {
            "bundleId": "com.natyaarts.academy",
            "transactionId": "2000000999888777",
            "originalTransactionId": "2000000999888777",
            "productId": f"com.natyaarts.academy.course.{self.course.id}",
            "purchaseDate": 1730100000000,
            "environment": "Sandbox"
        }

        # Sign token with leaf private key, embedding x5c chain in header
        token = jwt.encode(
            claims,
            leaf_key,
            algorithm="ES256",
            headers={"alg": "ES256", "x5c": x5c}
        )

        # Cryptographically verify token and chain
        verified_payload = AppleIAPService.verify_jws_token(token)
        self.assertEqual(verified_payload["transactionId"], "2000000999888777")
        self.assertEqual(verified_payload["productId"], f"com.natyaarts.academy.course.{self.course.id}")

    def test_expired_x509_certificate_fails_verification(self):
        """An expired certificate in the x5c chain must be rejected."""
        leaf_key, root_key, leaf_cert, root_cert, x5c = generate_test_cert_chain(expired=True)

        claims = {
            "bundleId": "com.natyaarts.academy",
            "transactionId": "2000000999888666",
            "productId": f"com.natyaarts.academy.course.{self.course.id}"
        }

        token = jwt.encode(
            claims,
            leaf_key,
            algorithm="ES256",
            headers={"alg": "ES256", "x5c": x5c}
        )

        with self.assertRaises(AppleVerificationError) as ctx:
            AppleIAPService.verify_jws_token(token)
        self.assertIn("expired or not yet valid", str(ctx.exception))

    def test_leaf_cert_missing_storekit_marker_rejected(self):
        """Leaf certificate missing Apple StoreKit 2 custom extension marker must be rejected."""
        leaf_key, root_key, leaf_cert, root_cert, x5c = generate_test_cert_chain(expired=False, include_marker=False)

        claims = {
            "bundleId": "com.natyaarts.academy",
            "transactionId": "2000000999888555",
            "productId": f"com.natyaarts.academy.course.{self.course.id}",
            "environment": "Sandbox"
        }

        token = jwt.encode(
            claims,
            leaf_key,
            algorithm="ES256",
            headers={"alg": "ES256", "x5c": x5c}
        )

        with self.assertRaises(AppleVerificationError) as ctx:
            AppleIAPService.verify_jws_token(token)
        self.assertIn("missing required StoreKit 2 extension", str(ctx.exception))

    def test_custom_apple_product_id_resolution_and_purchase(self):
        """Course with custom apple_product_id maps correctly and fulfills purchase."""
        custom_sku = "com.natyaarts.academy.course.masterclass.pro"
        custom_course = Course.objects.create(
            title="Masterclass Pro",
            description="Advanced masterclass",
            price=Decimal("4999.00"),
            is_published=True,
            apple_product_id=custom_sku
        )

        # 1. Direct resolver check
        resolved = AppleIAPService.resolve_course_for_product(custom_sku)
        self.assertEqual(resolved.id, custom_course.id)

        # 2. End-to-end verification via API
        self.client.force_authenticate(user=self.user)
        tx_id = "3000000111222333"
        payload = {
            "bundleId": "com.natyaarts.academy",
            "transactionId": tx_id,
            "originalTransactionId": tx_id,
            "productId": custom_sku,
            "environment": "Production"
        }
        jws = make_dummy_jws(payload)
        res = self.client.post(self.verify_url, {
            "signed_transaction_info": jws,
            "product_id": custom_sku
        }, format='json')

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data["status"], "SUCCESS")
        self.assertEqual(res.data["course_id"], custom_course.id)

        # Purchase recorded with custom SKU and correct catalog price
        p = Purchase.objects.get(apple_transaction_id=tx_id)
        self.assertEqual(p.apple_product_id, custom_sku)
        self.assertEqual(p.amount, Decimal("4999.00"))
        self.assertTrue(Enrollment.objects.filter(user=self.user, course=custom_course).exists())

    def test_default_product_id_rejected_when_course_has_different_custom_sku(self):
        """If a course has configured a custom apple_product_id, default pattern is rejected as ambiguous."""
        custom_course = Course.objects.create(
            title="Custom Sku Course",
            description="Testing ambiguity guard",
            price=Decimal("1999.00"),
            is_published=True,
            apple_product_id="com.natyaarts.academy.course.exclusive"
        )
        default_sku = f"com.natyaarts.academy.course.{custom_course.id}"

        with self.assertRaises(AppleVerificationError) as ctx:
            AppleIAPService.resolve_course_for_product(default_sku)
        self.assertIn("ambiguous or inactive", str(ctx.exception))

    def test_unknown_or_invalid_product_ids_fail_safely(self):
        """Unknown product IDs or foreign bundles fail safely with Course.DoesNotExist."""
        with self.assertRaises(Course.DoesNotExist):
            AppleIAPService.resolve_course_for_product("com.otherapp.item.42")

        with self.assertRaises(Course.DoesNotExist):
            AppleIAPService.resolve_course_for_product("com.natyaarts.academy.course.notanumber")

        with self.assertRaises(Course.DoesNotExist):
            AppleIAPService.resolve_course_for_product("")

    def test_sandbox_transaction_rejected_when_sandbox_setting_is_false(self):
        """When APPLE_IAP_ALLOW_SANDBOX=False, Sandbox transactions and notifications are rejected."""
        with override_settings(APPLE_IAP_ALLOW_SANDBOX=False, APPLE_IAP_TEST_MODE=False):
            # Transaction claims check
            tx_payload = {
                "bundleId": "com.natyaarts.academy",
                "transactionId": "999888111",
                "productId": f"com.natyaarts.academy.course.{self.course.id}",
                "environment": "Sandbox"
            }
            with self.assertRaises(AppleVerificationError) as ctx1:
                AppleIAPService._validate_transaction_claims(tx_payload)
            self.assertIn("Sandbox environment is not allowed", str(ctx1.exception))

            # Notification claims check
            notif_payload = {
                "notificationType": "TEST",
                "environment": "Sandbox",
                "data": {"bundleId": "com.natyaarts.academy", "environment": "Sandbox"}
            }
            with self.assertRaises(AppleVerificationError) as ctx2:
                AppleIAPService._validate_notification_claims(notif_payload)
            self.assertIn("Sandbox environment is not allowed", str(ctx2.exception))

    def test_sandbox_transaction_accepted_when_sandbox_setting_is_true(self):
        """When APPLE_IAP_ALLOW_SANDBOX=True, Sandbox transactions pass validation."""
        with override_settings(APPLE_IAP_ALLOW_SANDBOX=True, APPLE_IAP_TEST_MODE=False):
            tx_payload = {
                "bundleId": "com.natyaarts.academy",
                "transactionId": "999888222",
                "productId": f"com.natyaarts.academy.course.{self.course.id}",
                "environment": "Sandbox"
            }
            # Should not raise
            AppleIAPService._validate_transaction_claims(tx_payload)

    def test_invalid_storekit_environment_rejected(self):
        """Environments other than 'Production' or 'Sandbox' must be rejected."""
        payload = {
            "bundleId": "com.natyaarts.academy",
            "transactionId": "999888333",
            "productId": f"com.natyaarts.academy.course.{self.course.id}",
            "environment": "StagingInvalid"
        }
        with self.assertRaises(AppleVerificationError) as ctx:
            AppleIAPService._validate_transaction_claims(payload)
        self.assertIn("Invalid Apple StoreKit environment: 'StagingInvalid'", str(ctx.exception))

    def test_currency_and_catalog_price_accounting_compatibility(self):
        """
        StoreKit payload containing international currency (e.g. USD) and milliunit price
        still records the canonical catalog price (INR) on the Purchase for Razorpay ledger compatibility.
        """
        self.client.force_authenticate(user=self.user)
        tx_id = "4000000999111222"
        payload = {
            "bundleId": "com.natyaarts.academy",
            "transactionId": tx_id,
            "originalTransactionId": tx_id,
            "productId": f"com.natyaarts.academy.course.{self.course.id}",
            "currency": "USD",
            "price": 19990,
            "environment": "Production"
        }
        jws = make_dummy_jws(payload)
        res = self.client.post(self.verify_url, {
            "signed_transaction_info": jws,
            "product_id": f"com.natyaarts.academy.course.{self.course.id}"
        }, format='json')

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        purchase = Purchase.objects.get(apple_transaction_id=tx_id)
        # Stored in base currency INR matching catalog price
        self.assertEqual(purchase.amount, self.course.price)

    def test_course_serializer_returns_custom_or_default_apple_product_id(self):
        """CourseSerializer exposes custom apple_product_id when present, else default bundle pattern."""
        from courses.serializers import CourseSerializer
        data = CourseSerializer(self.course).data
        self.assertEqual(data["apple_product_id"], f"com.natyaarts.academy.course.{self.course.id}")

        self.course.apple_product_id = "com.natyaarts.academy.course.custom.special"
        self.course.save(update_fields=['apple_product_id'])
        data_custom = CourseSerializer(self.course).data
        self.assertEqual(data_custom["apple_product_id"], "com.natyaarts.academy.course.custom.special")

    @patch.object(AppleIAPService, 'verify_jws_token')
    def test_sandbox_transaction_isolates_accounting_and_grants_access(self, mock_verify):
        """StoreKit Sandbox transaction grants enrollment but isolates amount (0.00) and skips ledger/invoicing."""
        from finance.models import LedgerEntry, Invoice
        from courses.models import CourseInstructor

        # Setup primary instructor with commission to verify ledger isolation
        CourseInstructor.objects.create(
            course=self.course,
            user=self.user,
            is_primary=True,
            commission_rate=Decimal("20.00")
        )

        tx_id = f"sandbox_tx_{uuid.uuid4().hex[:12]}"
        mock_verify.return_value = {
            'bundleId': 'com.natyaarts.academy',
            'transactionId': tx_id,
            'originalTransactionId': tx_id,
            'productId': f"com.natyaarts.academy.course.{self.course.id}",
            'environment': 'Sandbox',
            'currency': 'INR',
            'price': 1499000
        }

        self.client.force_authenticate(user=self.user)
        with self.settings(APPLE_IAP_ALLOW_SANDBOX=True):
            res = self.client.post(self.verify_url, {
                'signed_transaction_info': 'mock_sandbox_jwt'
            }, format='json')

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertTrue(Enrollment.objects.filter(user=self.user, course=self.course).exists())

        purchase = Purchase.objects.get(apple_transaction_id=tx_id)
        # Sandbox purchases record zero accounting value
        self.assertEqual(purchase.amount, Decimal('0.00'))

        # Confirms complete accounting isolation
        self.assertFalse(LedgerEntry.objects.filter(purchase=purchase).exists())
        self.assertFalse(Invoice.objects.filter(purchase=purchase).exists())

    def test_untrusted_root_with_apple_common_name_rejected_when_test_mode_false(self):
        """
        Security Fix Regression: A certificate chain whose root contains 'Apple' in the Common Name
        but whose SHA-256 fingerprint is not in TRUSTED_APPLE_ROOT_FINGERPRINTS must be rejected.
        """
        _, _, _, root_cert, x5c = generate_test_cert_chain()
        with override_settings(APPLE_IAP_TEST_MODE=False):
            with self.assertRaises(AppleVerificationError) as ctx:
                AppleIAPService.verify_cert_chain(x5c)
            self.assertIn("does not root to an authorized Apple Root CA", str(ctx.exception))

    def test_trusted_root_fingerprint_accepted_when_test_mode_false(self):
        """A certificate chain whose root matches TRUSTED_APPLE_ROOT_FINGERPRINTS passes validation."""
        from orders.apple_service import TRUSTED_APPLE_ROOT_FINGERPRINTS
        _, _, leaf_cert, root_cert, x5c = generate_test_cert_chain()
        root_fp = root_cert.fingerprint(hashes.SHA256()).hex().lower()

        with patch.dict(TRUSTED_APPLE_ROOT_FINGERPRINTS, {root_fp: "Test Root"}):
            with override_settings(APPLE_IAP_TEST_MODE=False):
                verified_leaf = AppleIAPService.verify_cert_chain(x5c)
                self.assertEqual(
                    verified_leaf.public_bytes(serialization.Encoding.DER),
                    leaf_cert.public_bytes(serialization.Encoding.DER)
                )

    @patch.object(AppleIAPService, 'verify_jws_token')
    def test_race_condition_unique_constraint_handled_gracefully(self, mock_verify):
        """
        Security Fix Regression: When a concurrent duplicate transaction causes an IntegrityError,
        the service catches the unique-constraint conflict, verifies ownership & course,
        and grants enrollment without duplicate fulfillment.
        """
        tx_id = f"race_tx_{uuid.uuid4().hex[:12]}"
        mock_verify.return_value = {
            'bundleId': 'com.natyaarts.academy',
            'transactionId': tx_id,
            'originalTransactionId': tx_id,
            'productId': f"com.natyaarts.academy.course.{self.course.id}",
            'environment': 'Production',
        }

        # Pre-create the purchase as if another thread inserted it concurrently
        existing = Purchase.objects.create(
            user=self.user,
            course=self.course,
            amount=self.course.price,
            status=Purchase.Status.SUCCESS,
            payment_provider=Purchase.PaymentProvider.APPLE_IAP,
            apple_transaction_id=tx_id,
            apple_original_transaction_id=tx_id,
            apple_product_id=f"com.natyaarts.academy.course.{self.course.id}",
        )

        with patch.object(Purchase.objects, 'select_for_update') as mock_sfu:
            mock_qs = mock_sfu.return_value.filter.return_value
            # First call (check) returns None; Second call (after IntegrityError) returns existing
            mock_qs.first.side_effect = [None, existing]

            with patch('orders.apple_service.fulfill_purchase') as mock_fulfill:
                purchase, created = AppleIAPService.process_purchase_verification(
                    user=self.user,
                    signed_transaction_info='mock_jws',
                    client_product_id=f"com.natyaarts.academy.course.{self.course.id}"
                )

                self.assertFalse(created)
                self.assertEqual(purchase.id, existing.id)
                self.assertTrue(Enrollment.objects.filter(user=self.user, course=self.course).exists())
                # Must not re-fulfill purchase
                mock_fulfill.assert_not_called()

    @patch.object(AppleIAPService, 'verify_jws_token')
    def test_race_condition_conflicting_user_ownership_rejected(self, mock_verify):
        """
        Security Fix Regression: If a race conflict reveals the transaction was created by a different user,
        it must reject with AppleVerificationError and not grant enrollment.
        """
        tx_id = f"race_user_tx_{uuid.uuid4().hex[:12]}"
        mock_verify.return_value = {
            'bundleId': 'com.natyaarts.academy',
            'transactionId': tx_id,
            'originalTransactionId': tx_id,
            'productId': f"com.natyaarts.academy.course.{self.course.id}",
            'environment': 'Production',
        }

        existing_user2 = Purchase.objects.create(
            user=self.user2,
            course=self.course,
            amount=self.course.price,
            status=Purchase.Status.SUCCESS,
            payment_provider=Purchase.PaymentProvider.APPLE_IAP,
            apple_transaction_id=tx_id,
            apple_original_transaction_id=tx_id,
            apple_product_id=f"com.natyaarts.academy.course.{self.course.id}",
        )

        with patch.object(Purchase.objects, 'select_for_update') as mock_sfu:
            mock_qs = mock_sfu.return_value.filter.return_value
            mock_qs.first.side_effect = [None, existing_user2]

            with self.assertRaises(AppleVerificationError) as ctx:
                AppleIAPService.process_purchase_verification(
                    user=self.user,
                    signed_transaction_info='mock_jws',
                    client_product_id=f"com.natyaarts.academy.course.{self.course.id}"
                )
            self.assertIn("already associated with another user account", str(ctx.exception))
            self.assertFalse(Enrollment.objects.filter(user=self.user, course=self.course).exists())

    @patch.object(AppleIAPService, 'verify_jws_token')
    def test_race_condition_conflicting_course_mismatch_rejected(self, mock_verify):
        """
        Security Fix Regression: If a race conflict reveals the transaction belongs to a different course,
        it must reject with AppleVerificationError and not grant enrollment.
        """
        course2 = Course.objects.create(
            title="Kathak Foundations",
            price=Decimal("1999.00"),
            is_published=True
        )
        tx_id = f"race_course_tx_{uuid.uuid4().hex[:12]}"
        mock_verify.return_value = {
            'bundleId': 'com.natyaarts.academy',
            'transactionId': tx_id,
            'originalTransactionId': tx_id,
            'productId': f"com.natyaarts.academy.course.{self.course.id}",
            'environment': 'Production',
        }

        existing_course2 = Purchase.objects.create(
            user=self.user,
            course=course2,
            amount=course2.price,
            status=Purchase.Status.SUCCESS,
            payment_provider=Purchase.PaymentProvider.APPLE_IAP,
            apple_transaction_id=tx_id,
            apple_original_transaction_id=tx_id,
            apple_product_id=f"com.natyaarts.academy.course.{course2.id}",
        )

        with patch.object(Purchase.objects, 'select_for_update') as mock_sfu:
            mock_qs = mock_sfu.return_value.filter.return_value
            mock_qs.first.side_effect = [None, existing_course2]

            with self.assertRaises(AppleVerificationError) as ctx:
                AppleIAPService.process_purchase_verification(
                    user=self.user,
                    signed_transaction_info='mock_jws',
                    client_product_id=f"com.natyaarts.academy.course.{self.course.id}"
                )
            self.assertIn("already associated with another course", str(ctx.exception))
            self.assertFalse(Enrollment.objects.filter(user=self.user, course=self.course).exists())

    def test_replay_with_different_course_rejected(self):
        """Non-race replay check: claiming an existing transaction ID for a different course is rejected."""
        course2 = Course.objects.create(
            title="Odissi Foundations",
            price=Decimal("2499.00"),
            is_published=True
        )
        tx_id = "2000000999888777"
        Purchase.objects.create(
            user=self.user,
            course=self.course,
            amount=self.course.price,
            status=Purchase.Status.SUCCESS,
            payment_provider=Purchase.PaymentProvider.APPLE_IAP,
            apple_transaction_id=tx_id,
            apple_original_transaction_id=tx_id,
            apple_product_id=f"com.natyaarts.academy.course.{self.course.id}",
        )

        with patch.object(AppleIAPService, 'verify_jws_token') as mock_verify:
            mock_verify.return_value = {
                'bundleId': 'com.natyaarts.academy',
                'transactionId': tx_id,
                'originalTransactionId': tx_id,
                'productId': f"com.natyaarts.academy.course.{course2.id}",
                'environment': 'Production',
            }
            with self.assertRaises(AppleVerificationError) as ctx:
                AppleIAPService.process_purchase_verification(
                    user=self.user,
                    signed_transaction_info='mock_jws',
                    client_product_id=f"com.natyaarts.academy.course.{course2.id}"
                )
            self.assertIn("already associated with another course", str(ctx.exception))
            self.assertFalse(Enrollment.objects.filter(user=self.user, course=course2).exists())
