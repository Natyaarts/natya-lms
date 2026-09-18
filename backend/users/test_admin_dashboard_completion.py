"""
Custom Admin Dashboard Completion phase.

Covers the two backend additions made in the `users` app:
1. AdminStatsView -- extended with dashboard metrics the Overview page
   needs (total_mentors, active_subscriptions_count, draft/approved
   payout counts, pending_refunds_count, pending_assignment_grading_count,
   upcoming_live_classes_count, recent_refunds, upcoming_live_classes).
   No new business logic -- every number is a plain count/list already
   derivable from existing models.
2. AdminAuditLogListView -- a new read-only, list-only endpoint over the
   existing AdminAuditLog model (which has been written to since Phase
   3.9 but was never readable through the API).

AdminCertificateListView/AdminAssignmentListView are tested in
courses/test_admin_dashboard_completion.py; AdminSubscriptionViewSet's
list/retrieve extension is tested in
orders/test_admin_dashboard_completion.py.
"""
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from datetime import timedelta
from rest_framework import status
from rest_framework.test import APITestCase

from courses.models import Assignment, AssignmentSubmission, Course, LiveClass, Module
from finance.models import Payout, Refund
from orders.models import Purchase, Subscription, SubscriptionPlan
from users.models import AdminAuditLog

User = get_user_model()


class AdminStatsNewMetricsTests(APITestCase):
    def setUp(self):
        cache.clear()  # rate-limiting gap fix: LocMemCache isn't reset between test methods.
        self.admin = User.objects.create_user(username="dash_admin", password="pw", is_staff=True, is_student=False)
        self.mentor = User.objects.create_user(username="dash_mentor", password="pw", is_mentor=True, is_student=False)

        self.plan = SubscriptionPlan.objects.create(
            name="Dash Plan", billing_interval="MONTHLY", price="999.00", razorpay_plan_id="plan_dash_1",
        )
        self.student = User.objects.create_user(username="dash_student", password="pw")
        Subscription.objects.create(
            user=self.student, plan=self.plan, status=Subscription.Status.ACTIVE,
            razorpay_subscription_id="sub_dash_1",
        )

        Payout.objects.create(
            recipient=self.mentor, period_start=timezone.now().date(), period_end=timezone.now().date(),
            gross_amount="100.00", commission_amount="10.00", net_amount="90.00", status=Payout.Status.DRAFT,
        )
        Payout.objects.create(
            recipient=self.mentor, period_start=timezone.now().date(), period_end=timezone.now().date(),
            gross_amount="200.00", commission_amount="20.00", net_amount="180.00", status=Payout.Status.APPROVED,
        )

        course = Course.objects.create(title="Dash Course", description="x", price=10, is_published=True)
        purchase = Purchase.objects.create(
            user=self.student, course=course, amount="10.00", status=Purchase.Status.SUCCESS,
            razorpay_order_id='order_dash_1', razorpay_payment_id='pay_dash_1',
        )
        Refund.objects.create(
            customer=self.student, purchase=purchase, requested_by=self.admin, amount="10.00",
            status=Refund.Status.REQUESTED, reason="test",
        )
        module = Module.objects.create(course=course, title="M1", order=1)
        assignment = Assignment.objects.create(module=module, title="A1", max_marks=10, order=1, is_published=True)
        AssignmentSubmission.objects.create(
            assignment=assignment, student=self.student, attempt_number=1,
            status=AssignmentSubmission.Status.SUBMITTED, content="text",
        )

        LiveClass.objects.create(
            course=course, title="Upcoming Class", scheduled_start=timezone.now() + timedelta(days=1),
            duration_minutes=60, status=LiveClass.ClassStatus.SCHEDULED, meeting_url="https://meet.example.com/dash",
        )

    def test_admin_sees_new_dashboard_metrics(self):
        self.client.force_authenticate(user=self.admin)
        res = self.client.get(reverse('admin-stats'))
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data['total_mentors'], 1)
        self.assertEqual(res.data['active_subscriptions_count'], 1)
        self.assertEqual(res.data['draft_payouts_count'], 1)
        self.assertEqual(res.data['approved_payouts_pending_count'], 1)
        self.assertEqual(res.data['pending_refunds_count'], 1)
        self.assertEqual(res.data['pending_assignment_grading_count'], 1)
        self.assertEqual(res.data['upcoming_live_classes_count'], 1)
        self.assertEqual(len(res.data['recent_refunds']), 1)
        self.assertEqual(res.data['recent_refunds'][0]['status'], Refund.Status.REQUESTED)
        self.assertEqual(len(res.data['upcoming_live_classes']), 1)
        self.assertEqual(res.data['upcoming_live_classes'][0]['title'], "Upcoming Class")

    def test_superuser_is_not_counted_as_mentor(self):
        User.objects.create_superuser(username="dash_super", password="pw", email="s@x.com", is_mentor=True)
        self.client.force_authenticate(user=self.admin)
        res = self.client.get(reverse('admin-stats'))
        # Superusers are excluded from total_mentors the same way they are
        # already excluded from total_teachers/total_students elsewhere in
        # this view.
        self.assertEqual(res.data['total_mentors'], 1)

    def test_non_admin_cannot_view_admin_stats(self):
        self.client.force_authenticate(user=self.student)
        res = self.client.get(reverse('admin-stats'))
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)


class AdminAuditLogListViewTests(APITestCase):
    def setUp(self):
        cache.clear()  # rate-limiting gap fix: LocMemCache isn't reset between test methods.
        self.superadmin = User.objects.create_superuser(username="audit_super", password="pw", email="a@x.com")
        self.admin = User.objects.create_user(username="audit_admin", password="pw", is_staff=True, is_student=False)
        self.student = User.objects.create_user(username="audit_student", password="pw")
        self.target = User.objects.create_user(username="audit_target", password="pw")

        AdminAuditLog.record(
            actor=self.superadmin, action='ROLE_CHANGE', target_type='User', target_id=self.target.id,
            description="Role changed.", metadata={'changed_fields': {'is_teacher': {'from': False, 'to': True}}},
        )
        AdminAuditLog.record(
            actor=self.admin, action='REFUND_CREATED', target_type='Refund', target_id=1,
            description="Refund created.",
        )

    def test_admin_can_list_audit_logs(self):
        self.client.force_authenticate(user=self.admin)
        res = self.client.get(reverse('admin-audit-logs'))
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data['count'], 2)

    def test_entries_ordered_most_recent_first(self):
        self.client.force_authenticate(user=self.admin)
        res = self.client.get(reverse('admin-audit-logs'))
        actions = [row['action'] for row in res.data['results']]
        self.assertEqual(actions, ['REFUND_CREATED', 'ROLE_CHANGE'])

    def test_actor_rendered_as_nested_object(self):
        self.client.force_authenticate(user=self.admin)
        res = self.client.get(reverse('admin-audit-logs'))
        role_change_row = next(r for r in res.data['results'] if r['action'] == 'ROLE_CHANGE')
        self.assertEqual(role_change_row['actor'], {'id': self.superadmin.id, 'username': 'audit_super'})

    def test_filter_by_action(self):
        self.client.force_authenticate(user=self.admin)
        res = self.client.get(reverse('admin-audit-logs'), {'action': 'ROLE_CHANGE'})
        self.assertEqual(res.data['count'], 1)
        self.assertEqual(res.data['results'][0]['action'], 'ROLE_CHANGE')

    def test_filter_by_target_type(self):
        self.client.force_authenticate(user=self.admin)
        res = self.client.get(reverse('admin-audit-logs'), {'target_type': 'Refund'})
        self.assertEqual(res.data['count'], 1)
        self.assertEqual(res.data['results'][0]['target_type'], 'Refund')

    def test_filter_by_actor_id(self):
        self.client.force_authenticate(user=self.admin)
        res = self.client.get(reverse('admin-audit-logs'), {'actor_id': self.superadmin.id})
        self.assertEqual(res.data['count'], 1)
        self.assertEqual(res.data['results'][0]['action'], 'ROLE_CHANGE')

    def test_actor_null_after_account_deletion_renders_as_none(self):
        entry = AdminAuditLog.objects.get(action='REFUND_CREATED')
        deleted_actor_id = self.admin.id
        self.admin.delete()
        entry.refresh_from_db()
        self.assertIsNone(entry.actor_id)  # SET_NULL confirmed
        self.client.force_authenticate(user=self.superadmin)
        res = self.client.get(reverse('admin-audit-logs'))
        row = next(r for r in res.data['results'] if r['action'] == 'REFUND_CREATED')
        self.assertIsNone(row['actor'])

    def test_non_admin_cannot_list_audit_logs(self):
        self.client.force_authenticate(user=self.student)
        res = self.client.get(reverse('admin-audit-logs'))
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_unauthenticated_cannot_list_audit_logs(self):
        res = self.client.get(reverse('admin-audit-logs'))
        self.assertEqual(res.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_log_is_read_only_no_write_methods_allowed(self):
        self.client.force_authenticate(user=self.superadmin)
        res = self.client.post(reverse('admin-audit-logs'), {'action': 'FORGED'})
        self.assertEqual(res.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)
