"""
Custom Admin Dashboard Completion phase.

Covers the one backend addition made in the `orders` app:
AdminSubscriptionViewSet gained list/retrieve (via ListModelMixin/
RetrieveModelMixin added to what was previously a bare GenericViewSet
exposing only the `cancel-immediate` action) -- an admin previously had
no way to see subscriptions across all users through the API at all.
cancel-immediate itself is completely unchanged and not retested here
(already covered elsewhere).
"""
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from orders.models import Subscription, SubscriptionPlan

User = get_user_model()


class AdminSubscriptionListViewTests(APITestCase):
    def setUp(self):
        cache.clear()  # rate-limiting gap fix: LocMemCache isn't reset between test methods.
        self.admin = User.objects.create_user(username="sub_dash_admin", password="pw", is_staff=True, is_student=False)
        self.student_a = User.objects.create_user(username="sub_dash_student_a", password="pw")
        self.student_b = User.objects.create_user(username="sub_dash_student_b", password="pw")

        self.plan_monthly = SubscriptionPlan.objects.create(
            name="Dash Monthly", billing_interval="MONTHLY", price="999.00", razorpay_plan_id="plan_dash_monthly",
        )
        self.plan_yearly = SubscriptionPlan.objects.create(
            name="Dash Yearly", billing_interval="YEARLY", price="9999.00", razorpay_plan_id="plan_dash_yearly",
        )

        self.sub_active = Subscription.objects.create(
            user=self.student_a, plan=self.plan_monthly, status=Subscription.Status.ACTIVE,
            razorpay_subscription_id="sub_dash_active",
        )
        self.sub_cancelled = Subscription.objects.create(
            user=self.student_b, plan=self.plan_yearly, status=Subscription.Status.CANCELLED,
            razorpay_subscription_id="sub_dash_cancelled",
        )

    def test_admin_lists_subscriptions_across_all_users(self):
        self.client.force_authenticate(user=self.admin)
        res = self.client.get(reverse('subscription-admin-list'))
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data['count'], 2)

    def test_user_field_rendered_as_nested_object(self):
        self.client.force_authenticate(user=self.admin)
        res = self.client.get(reverse('subscription-admin-list'))
        row = next(r for r in res.data['results'] if r['id'] == self.sub_active.id)
        self.assertEqual(row['user'], {'id': self.student_a.id, 'username': 'sub_dash_student_a'})

    def test_retrieve_single_subscription(self):
        self.client.force_authenticate(user=self.admin)
        res = self.client.get(reverse('subscription-admin-detail', kwargs={'pk': self.sub_active.pk}))
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data['id'], self.sub_active.id)

    def test_filter_by_status(self):
        self.client.force_authenticate(user=self.admin)
        res = self.client.get(reverse('subscription-admin-list'), {'status': 'CANCELLED'})
        self.assertEqual(res.data['count'], 1)
        self.assertEqual(res.data['results'][0]['id'], self.sub_cancelled.id)

    def test_invalid_status_filter_ignored_not_500(self):
        self.client.force_authenticate(user=self.admin)
        res = self.client.get(reverse('subscription-admin-list'), {'status': 'NOT_A_REAL_STATUS'})
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data['count'], 2)

    def test_filter_by_plan_id(self):
        self.client.force_authenticate(user=self.admin)
        res = self.client.get(reverse('subscription-admin-list'), {'plan_id': self.plan_yearly.id})
        self.assertEqual(res.data['count'], 1)
        self.assertEqual(res.data['results'][0]['id'], self.sub_cancelled.id)

    def test_filter_by_user_id(self):
        self.client.force_authenticate(user=self.admin)
        res = self.client.get(reverse('subscription-admin-list'), {'user_id': self.student_a.id})
        self.assertEqual(res.data['count'], 1)
        self.assertEqual(res.data['results'][0]['id'], self.sub_active.id)

    def test_student_cannot_access_admin_subscription_list(self):
        self.client.force_authenticate(user=self.student_a)
        res = self.client.get(reverse('subscription-admin-list'))
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)
