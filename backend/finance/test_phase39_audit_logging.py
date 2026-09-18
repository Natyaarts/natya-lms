"""
Phase 3.9: audit-log wiring for refund creation and payout approval.
Purely additive/observational -- these tests exist to confirm the audit
call sites work, NOT to re-test refund/payout business logic (already
exhaustively covered in test_refunds.py/test_payout_settlement.py, and
explicitly out of scope to modify or re-verify in Phase 3.9).
"""
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from courses.models import Course
from users.models import AdminAuditLog

from .models import LedgerEntry, Payout
from .services import create_payout_batch
from .test_refunds import _make_course_instructor, _paid_purchase

User = get_user_model()


class RefundAuditLogTests(APITestCase):
    def setUp(self):
        self.student = User.objects.create_user(username='audit_refund_student', password='pw')
        self.admin = User.objects.create_user(username='audit_refund_admin', password='pw', is_staff=True)
        self.course = Course.objects.create(title='Audit Refund Course', description='x', price=Decimal('500.00'))

    @patch('orders.views.client')
    def test_refund_creation_logs_audit_entry(self, mock_client):
        mock_client.payment.refund.return_value = {"id": "rfnd_audit_1", "status": "processed"}
        purchase = _paid_purchase(self.student, self.course, Decimal('500.00'), razorpay_payment_id='pay_audit_refund_1')

        self.client.force_authenticate(self.admin)
        response = self.client.post(reverse('finance-refund-create'), {
            'purchase_id': purchase.id, 'amount': '500.00', 'reason': 'test',
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        log = AdminAuditLog.objects.filter(action='REFUND_CREATED', target_type='Refund').first()
        self.assertIsNotNone(log)
        self.assertEqual(log.actor_id, self.admin.id)
        self.assertEqual(log.metadata['amount'], '500.00')

    def test_failed_refund_request_creates_no_audit_log(self):
        self.client.force_authenticate(self.admin)
        response = self.client.post(reverse('finance-refund-create'), {
            'purchase_id': 999999, 'amount': '100.00',
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertFalse(AdminAuditLog.objects.filter(action='REFUND_CREATED').exists())


class PayoutApprovalAuditLogTests(APITestCase):
    def setUp(self):
        self.student = User.objects.create_user(username='audit_payout_student', password='pw')
        self.admin = User.objects.create_user(username='audit_payout_admin', password='pw', is_staff=True)
        self.teacher = User.objects.create_user(username='audit_payout_teacher', password='pw', is_teacher=True)
        self.course = Course.objects.create(title='Audit Payout Course', description='x', price=Decimal('500.00'))
        self.instructor = _make_course_instructor(self.course, self.teacher, commission_rate=Decimal('30.00'))

    def test_approval_logs_audit_entry(self):
        purchase = _paid_purchase(self.student, self.course, Decimal('500.00'), razorpay_payment_id='pay_audit_payout_1')
        entry = LedgerEntry.objects.get(purchase=purchase)
        payout = create_payout_batch(recipient=self.teacher, ledger_entry_ids=[entry.id], period_start='2026-01-01', period_end='2026-01-31')

        self.client.force_authenticate(self.admin)
        response = self.client.post(reverse('finance-payout-approve', kwargs={'pk': payout.pk}))
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        log = AdminAuditLog.objects.filter(action='PAYOUT_APPROVED', target_type='Payout', target_id=str(payout.id)).first()
        self.assertIsNotNone(log)
        self.assertEqual(log.actor_id, self.admin.id)
