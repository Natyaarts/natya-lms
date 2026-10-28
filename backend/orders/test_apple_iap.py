import base64
import datetime
import json
from decimal import Decimal

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


def generate_test_cert_chain(expired: bool = False):
    """
    Generates a genuine 2-level X.509 certificate chain (Root CA -> Leaf)
    using ECDSA secp256r1 for cryptographic verification testing.
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
    leaf_cert = (
        x509.CertificateBuilder()
        .subject_name(leaf_name)
        .issuer_name(root_name)
        .public_key(leaf_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(valid_from)
        .not_valid_after(valid_to)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .sign(root_key, hashes.SHA256(), default_backend())
    )

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
            "environment": "Sandbox"
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
