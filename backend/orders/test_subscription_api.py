"""
Phase 3.4.6 -- subscription REST API layer: public plan catalog
(SubscriptionPlanViewSet), "my subscription" (SubscriptionMeView, extended
with the raw access_until field), and subscription payment history
(SubscriptionPaymentHistoryView). Purely additive, read-only API surface on
top of the already-approved 3.4.1-3.4.5 implementation -- no new model
field, no migration, no change to cancellation/webhook/access-control
behavior.
"""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from courses.models import Course
from orders.models import Subscription, SubscriptionPlan, SubscriptionPayment

User = get_user_model()


class SubscriptionPlanPublicAPITests(APITestCase):
    def setUp(self):
        self.course = Course.objects.create(title="Plan API Course", description="x", price=100, is_published=True)
        self.active_plan = SubscriptionPlan.objects.create(
            name="Active Plan", billing_interval="MONTHLY", price="999.00",
            razorpay_plan_id="plan_api_active_1", is_active=True,
        )
        self.active_plan.courses.add(self.course)
        self.inactive_plan = SubscriptionPlan.objects.create(
            name="Inactive Plan", billing_interval="MONTHLY", price="500.00",
            razorpay_plan_id="plan_api_inactive_1", is_active=False,
        )
        self.unlinked_plan = SubscriptionPlan.objects.create(
            name="Unlinked Plan", billing_interval="YEARLY", price="5000.00", is_active=True,
        )  # no razorpay_plan_id -- not actually purchasable yet
        self.list_url = reverse('subscription-plan-list')

    # A. Public plan list.
    def test_anonymous_can_list_plans(self):
        response = self.client.get(self.list_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data if isinstance(response.data, list) else response.data.get('results', response.data)
        ids = [p['id'] for p in results]
        self.assertIn(self.active_plan.id, ids)

    def test_list_only_returns_active_purchasable_plans(self):
        response = self.client.get(self.list_url)
        results = response.data if isinstance(response.data, list) else response.data.get('results', response.data)
        ids = [p['id'] for p in results]
        self.assertNotIn(self.inactive_plan.id, ids)  # inactive plan hidden
        self.assertNotIn(self.unlinked_plan.id, ids)  # no razorpay_plan_id -- hidden too

    def test_plan_list_includes_required_fields_and_courses(self):
        response = self.client.get(self.list_url)
        results = response.data if isinstance(response.data, list) else response.data.get('results', response.data)
        plan = next(p for p in results if p['id'] == self.active_plan.id)
        for field in ('id', 'name', 'slug', 'description', 'billing_interval', 'price', 'currency', 'courses'):
            self.assertIn(field, plan)
        self.assertEqual(len(plan['courses']), 1)
        self.assertEqual(plan['courses'][0]['id'], self.course.id)

    def test_plan_list_never_exposes_razorpay_plan_id(self):
        response = self.client.get(self.list_url)
        results = response.data if isinstance(response.data, list) else response.data.get('results', response.data)
        plan = next(p for p in results if p['id'] == self.active_plan.id)
        self.assertNotIn('razorpay_plan_id', plan)

    # B. Plan detail.
    def test_active_plan_detail_publicly_visible(self):
        url = reverse('subscription-plan-detail', kwargs={'pk': self.active_plan.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['name'], "Active Plan")
        self.assertNotIn('razorpay_plan_id', response.data)

    def test_inactive_plan_detail_hidden_from_public(self):
        url = reverse('subscription-plan-detail', kwargs={'pk': self.inactive_plan.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_staff_can_see_inactive_plans(self):
        staff = User.objects.create_user(username="plan_api_staff", password="password123")
        staff.is_staff = True
        staff.save()
        self.client.force_authenticate(user=staff)
        response = self.client.get(self.list_url)
        results = response.data if isinstance(response.data, list) else response.data.get('results', response.data)
        ids = [p['id'] for p in results]
        self.assertIn(self.inactive_plan.id, ids)
        self.assertIn(self.unlinked_plan.id, ids)

    def test_plan_viewset_is_read_only(self):
        staff = User.objects.create_user(username="plan_api_staff_write", password="password123")
        staff.is_staff = True
        staff.save()
        self.client.force_authenticate(user=staff)
        response = self.client.post(self.list_url, {"name": "New Plan", "billing_interval": "MONTHLY", "price": "1.00"}, format='json')
        self.assertEqual(response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)


class MySubscriptionAPITests(APITestCase):
    def setUp(self):
        self.student = User.objects.create_user(username="mysub_api_student", password="password123")
        self.other_student = User.objects.create_user(username="mysub_api_other", password="password123")
        self.plan = SubscriptionPlan.objects.create(
            name="My Sub API Plan", billing_interval="MONTHLY", price="999.00", razorpay_plan_id="plan_mysub_1",
        )
        self.subscription = Subscription.objects.create(
            user=self.student, plan=self.plan, status=Subscription.Status.ACTIVE,
            razorpay_subscription_id="sub_mysub_api_1",
            current_period_start=timezone.now() - timedelta(days=5),
            current_period_end=timezone.now() + timedelta(days=25),
        )
        self.url = reverse('subscription-me')

    def test_unauthenticated_denied(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_authenticated_owner_sees_own_subscription_with_required_fields(self):
        self.client.force_authenticate(user=self.student)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        for field in (
            'status', 'plan', 'current_period_start', 'current_period_end', 'access_until',
            'cancel_at_period_end', 'cancelled_at', 'created_at',
        ):
            self.assertIn(field, response.data)
        self.assertIn('billing_interval', response.data['plan'])
        self.assertIn('price', response.data['plan'])
        self.assertIn('currency', response.data['plan'])

    def test_no_razorpay_ids_in_my_subscription_response(self):
        self.client.force_authenticate(user=self.student)
        response = self.client.get(self.url)
        self.assertNotIn('razorpay_subscription_id', response.data)
        self.assertNotIn('razorpay_plan_id', response.data)
        self.assertNotIn('razorpay_plan_id', response.data['plan'])

    def test_user_without_subscription_gets_404(self):
        self.client.force_authenticate(user=self.other_student)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_terminal_subscription_not_returned_as_current(self):
        self.subscription.status = Subscription.Status.CANCELLED
        self.subscription.current_period_end = timezone.now() - timedelta(days=1)
        self.subscription.save()
        self.client.force_authenticate(user=self.student)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_a_users_subscription_is_never_returned_to_another_user(self):
        # There is no subscription-id parameter anywhere on this endpoint --
        # it is derived entirely from request.user, so there is nothing for
        # another user to supply to reach someone else's row.
        self.client.force_authenticate(user=self.other_student)
        response = self.client.get(self.url)
        self.assertNotEqual(response.status_code, status.HTTP_200_OK)


class SubscriptionPaymentHistoryAPITests(APITestCase):
    def setUp(self):
        self.student = User.objects.create_user(username="payhist_student", password="password123")
        self.other_student = User.objects.create_user(username="payhist_other", password="password123")
        self.plan = SubscriptionPlan.objects.create(
            name="Payment History Plan", billing_interval="MONTHLY", price="999.00", razorpay_plan_id="plan_payhist_1",
        )
        self.subscription = Subscription.objects.create(
            user=self.student, plan=self.plan, status=Subscription.Status.ACTIVE,
            razorpay_subscription_id="sub_payhist_1",
        )
        self.other_plan = SubscriptionPlan.objects.create(
            name="Other Payment History Plan", billing_interval="MONTHLY", price="500.00", razorpay_plan_id="plan_payhist_other",
        )
        self.other_subscription = Subscription.objects.create(
            user=self.other_student, plan=self.other_plan, status=Subscription.Status.ACTIVE,
            razorpay_subscription_id="sub_payhist_other_1",
        )
        self.url = reverse('subscription-payments')

    def test_unauthenticated_denied(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_empty_when_no_payments(self):
        self.client.force_authenticate(user=self.student)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data if isinstance(response.data, list) else response.data.get('results', response.data)
        self.assertEqual(len(results), 0)

    def test_user_sees_only_own_payments(self):
        SubscriptionPayment.objects.create(
            subscription=self.subscription, razorpay_payment_id="pay_mine_1",
            amount="999.00", currency="INR", status=SubscriptionPayment.Status.SUCCESS, paid_at=timezone.now(),
        )
        SubscriptionPayment.objects.create(
            subscription=self.other_subscription, razorpay_payment_id="pay_other_1",
            amount="500.00", currency="INR", status=SubscriptionPayment.Status.SUCCESS, paid_at=timezone.now(),
        )
        self.client.force_authenticate(user=self.student)
        response = self.client.get(self.url)
        results = response.data if isinstance(response.data, list) else response.data.get('results', response.data)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['amount'], '999.00')

    def test_no_razorpay_ids_in_payment_history_response(self):
        SubscriptionPayment.objects.create(
            subscription=self.subscription, razorpay_payment_id="pay_secret_1",
            razorpay_subscription_id="sub_payhist_1",
            amount="999.00", status=SubscriptionPayment.Status.SUCCESS, paid_at=timezone.now(),
        )
        self.client.force_authenticate(user=self.student)
        response = self.client.get(self.url)
        results = response.data if isinstance(response.data, list) else response.data.get('results', response.data)
        self.assertNotIn('razorpay_payment_id', results[0])
        self.assertNotIn('razorpay_subscription_id', results[0])

    # Historical (terminal) subscription's payments remain visible.
    def test_payment_history_spans_terminal_subscriptions(self):
        SubscriptionPayment.objects.create(
            subscription=self.subscription, razorpay_payment_id="pay_hist_1",
            amount="999.00", status=SubscriptionPayment.Status.SUCCESS, paid_at=timezone.now() - timedelta(days=60),
        )
        self.subscription.status = Subscription.Status.CANCELLED
        self.subscription.current_period_end = timezone.now() - timedelta(days=30)
        self.subscription.save()

        new_plan = SubscriptionPlan.objects.create(
            name="Resubscribed Plan", billing_interval="MONTHLY", price="999.00", razorpay_plan_id="plan_payhist_new",
        )
        new_subscription = Subscription.objects.create(
            user=self.student, plan=new_plan, status=Subscription.Status.ACTIVE,
            razorpay_subscription_id="sub_payhist_new_1",
        )
        SubscriptionPayment.objects.create(
            subscription=new_subscription, razorpay_payment_id="pay_hist_2",
            amount="999.00", status=SubscriptionPayment.Status.SUCCESS, paid_at=timezone.now(),
        )

        self.client.force_authenticate(user=self.student)
        response = self.client.get(self.url)
        results = response.data if isinstance(response.data, list) else response.data.get('results', response.data)
        self.assertEqual(len(results), 2)  # both the old (terminal) and new subscription's payments

    def test_plan_name_included_for_readability(self):
        SubscriptionPayment.objects.create(
            subscription=self.subscription, razorpay_payment_id="pay_name_1",
            amount="999.00", status=SubscriptionPayment.Status.SUCCESS, paid_at=timezone.now(),
        )
        self.client.force_authenticate(user=self.student)
        response = self.client.get(self.url)
        results = response.data if isinstance(response.data, list) else response.data.get('results', response.data)
        self.assertEqual(results[0]['plan_name'], "Payment History Plan")
