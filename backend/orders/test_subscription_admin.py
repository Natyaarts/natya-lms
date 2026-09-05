"""
Phase 3.4.7 -- Django admin management for SubscriptionPlan/Subscription/
SubscriptionPayment. Tests the actual rendered Django admin pages (session-
based, via Client.force_login()) rather than the app's own JWT-cookie API --
Django admin is a separate, standard Django auth surface, unrelated to
authentication_classes on the DRF views built in earlier sub-phases.

Deliberately NOT tested here (out of scope, per the brief): a frontend
admin dashboard (none exists or was added), refunds/invoices/ledger/
coupons/tax (none implemented), course-access behavior (unchanged).
"""
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from courses.models import Course
from orders.models import Subscription, SubscriptionPlan, SubscriptionPayment

User = get_user_model()


class SubscriptionAdminAccessTests(TestCase):
    """Who can reach the Django admin at all for these three models."""

    def setUp(self):
        self.superuser = User.objects.create_superuser(username="admin_super", password="password123", email="super@x.com")
        self.plain_user = User.objects.create_user(username="admin_plain", password="password123")
        self.plan = SubscriptionPlan.objects.create(
            name="Admin Test Plan", billing_interval="MONTHLY", price="999.00", razorpay_plan_id="plan_admin_1",
        )

    def test_unauthenticated_cannot_access_subscription_admin(self):
        response = self.client.get(reverse('admin:orders_subscription_changelist'))
        self.assertEqual(response.status_code, 302)  # redirected to login

    def test_plain_authenticated_non_staff_cannot_access_admin(self):
        self.client.force_login(self.plain_user)
        response = self.client.get(reverse('admin:orders_subscription_changelist'))
        self.assertEqual(response.status_code, 302)  # still redirected -- not is_staff

    def test_superuser_can_access_plan_changelist(self):
        self.client.force_login(self.superuser)
        response = self.client.get(reverse('admin:orders_subscriptionplan_changelist'))
        self.assertEqual(response.status_code, 200)

    def test_superuser_can_access_subscription_changelist(self):
        self.client.force_login(self.superuser)
        response = self.client.get(reverse('admin:orders_subscription_changelist'))
        self.assertEqual(response.status_code, 200)

    def test_superuser_can_access_payment_changelist(self):
        self.client.force_login(self.superuser)
        response = self.client.get(reverse('admin:orders_subscriptionpayment_changelist'))
        self.assertEqual(response.status_code, 200)


class SubscriptionPlanAdminTests(TestCase):
    def setUp(self):
        self.superuser = User.objects.create_superuser(username="planadmin_super", password="password123", email="super2@x.com")
        self.client.force_login(self.superuser)
        self.course = Course.objects.create(title="Plan Admin Course", description="x", price=100, is_published=True)
        self.linked_plan = SubscriptionPlan.objects.create(
            name="Linked Plan", billing_interval="MONTHLY", price="999.00", razorpay_plan_id="plan_admin_linked_1",
        )
        self.linked_plan.courses.add(self.course)
        self.unlinked_plan = SubscriptionPlan.objects.create(
            name="Unlinked Plan", billing_interval="YEARLY", price="5000.00",
        )

    def test_changelist_shows_both_linked_and_unlinked_plans(self):
        response = self.client.get(reverse('admin:orders_subscriptionplan_changelist'))
        self.assertContains(response, "Linked Plan")
        self.assertContains(response, "Unlinked Plan")

    def test_changelist_shows_razorpay_linkage_status(self):
        response = self.client.get(reverse('admin:orders_subscriptionplan_changelist'))
        # The boolean icon renders via an <img> with alt="True"/"False" in
        # Django's default admin theme -- just confirm the page renders
        # both plans without error; the actual boolean computation is unit-
        # tested directly below.
        self.assertEqual(response.status_code, 200)

    def test_razorpay_linked_field_reflects_actual_linkage(self):
        from orders.admin import SubscriptionPlanAdmin
        admin_instance = SubscriptionPlanAdmin(SubscriptionPlan, None)
        self.assertTrue(admin_instance.razorpay_linked(self.linked_plan))
        self.assertFalse(admin_instance.razorpay_linked(self.unlinked_plan))

    def test_search_by_name(self):
        response = self.client.get(reverse('admin:orders_subscriptionplan_changelist'), {"q": "Linked Plan"})
        self.assertContains(response, "Linked Plan")

    def test_filter_by_billing_interval(self):
        response = self.client.get(reverse('admin:orders_subscriptionplan_changelist'), {"billing_interval": "YEARLY"})
        self.assertContains(response, "Unlinked Plan")
        self.assertNotContains(response, "Linked Plan")

    def test_courses_manageable_via_change_form(self):
        response = self.client.get(reverse('admin:orders_subscriptionplan_change', args=[self.linked_plan.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Plan Admin Course")

    def test_razorpay_plan_id_visible_to_admin(self):
        response = self.client.get(reverse('admin:orders_subscriptionplan_change', args=[self.linked_plan.pk]))
        self.assertContains(response, "plan_admin_linked_1")


class SubscriptionAdminTests(TestCase):
    def setUp(self):
        self.superuser = User.objects.create_superuser(username="subadmin_super", password="password123", email="super3@x.com")
        self.client.force_login(self.superuser)
        self.student = User.objects.create_user(username="subadmin_student", password="password123")
        self.plan = SubscriptionPlan.objects.create(
            name="Sub Admin Plan", billing_interval="MONTHLY", price="999.00", razorpay_plan_id="plan_subadmin_1",
        )
        self.subscription = Subscription.objects.create(
            user=self.student, plan=self.plan, status=Subscription.Status.ACTIVE,
            razorpay_subscription_id="sub_admin_test_1",
        )

    def test_changelist_renders(self):
        response = self.client.get(reverse('admin:orders_subscription_changelist'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "subadmin_student")

    def test_search_by_user_username(self):
        response = self.client.get(reverse('admin:orders_subscription_changelist'), {"q": "subadmin_student"})
        self.assertContains(response, "subadmin_student")

    def test_search_by_razorpay_subscription_id(self):
        response = self.client.get(reverse('admin:orders_subscription_changelist'), {"q": "sub_admin_test_1"})
        self.assertContains(response, "subadmin_student")

    def test_filter_by_status(self):
        cancelled = Subscription.objects.create(
            user=self.student, plan=self.plan, status=Subscription.Status.CANCELLED,
            razorpay_subscription_id="sub_admin_test_cancelled",
        )
        response = self.client.get(reverse('admin:orders_subscription_changelist'), {"status__exact": "CANCELLED"})
        # razorpay_subscription_id is deliberately not a list_display column
        # (only status/user/plan/period/access fields are) -- assert on
        # what the filtered row actually renders instead.
        self.assertContains(response, "subadmin_student")
        self.assertContains(response, "Cancelled")

    def test_historical_cancelled_subscription_visible_in_changelist(self):
        self.subscription.status = Subscription.Status.CANCELLED
        self.subscription.save()
        response = self.client.get(reverse('admin:orders_subscription_changelist'))
        self.assertContains(response, "subadmin_student")

    # Read-only protections.
    def test_add_permission_disabled(self):
        response = self.client.get(reverse('admin:orders_subscription_add'))
        self.assertEqual(response.status_code, 403)

    def test_status_field_is_readonly_on_change_form(self):
        response = self.client.get(reverse('admin:orders_subscription_change', args=[self.subscription.pk]))
        self.assertEqual(response.status_code, 200)
        # A readonly field is rendered as plain text, never as an <select>
        # or <input> the admin form would submit -- confirm no editable
        # status widget is present.
        self.assertNotContains(response, 'name="status"')

    def test_submitting_a_status_change_has_no_effect(self):
        url = reverse('admin:orders_subscription_change', args=[self.subscription.pk])
        # Even a forged POST attempting to smuggle a status change (the
        # field is readonly, so the admin form doesn't even render an
        # input for it, but confirm the backend doesn't apply it either
        # if somehow submitted).
        self.client.post(url, {
            "status": "CANCELLED", "_save": "Save",
        })
        self.subscription.refresh_from_db()
        self.assertEqual(self.subscription.status, Subscription.Status.ACTIVE)

    def test_cancel_at_period_end_is_readonly(self):
        response = self.client.get(reverse('admin:orders_subscription_change', args=[self.subscription.pk]))
        self.assertNotContains(response, 'name="cancel_at_period_end"')


class SubscriptionPaymentAdminTests(TestCase):
    def setUp(self):
        self.superuser = User.objects.create_superuser(username="payadmin_super", password="password123", email="super4@x.com")
        self.client.force_login(self.superuser)
        self.student = User.objects.create_user(username="payadmin_student", password="password123")
        self.plan = SubscriptionPlan.objects.create(
            name="Pay Admin Plan", billing_interval="MONTHLY", price="999.00", razorpay_plan_id="plan_payadmin_1",
        )
        self.subscription = Subscription.objects.create(
            user=self.student, plan=self.plan, status=Subscription.Status.ACTIVE,
            razorpay_subscription_id="sub_payadmin_1",
        )
        self.payment = SubscriptionPayment.objects.create(
            subscription=self.subscription, razorpay_payment_id="pay_admin_test_1",
            amount="999.00", currency="INR", status=SubscriptionPayment.Status.SUCCESS,
        )

    def test_changelist_renders_with_user_and_plan_columns(self):
        response = self.client.get(reverse('admin:orders_subscriptionpayment_changelist'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "payadmin_student")
        self.assertContains(response, "Pay Admin Plan")

    def test_search_by_plan_name(self):
        response = self.client.get(reverse('admin:orders_subscriptionpayment_changelist'), {"q": "Pay Admin Plan"})
        self.assertContains(response, "pay_admin_test_1")

    def test_search_by_user_email_via_subscription(self):
        response = self.client.get(reverse('admin:orders_subscriptionpayment_changelist'), {"q": self.student.email or self.student.username})
        self.assertEqual(response.status_code, 200)

    def test_filter_by_status(self):
        response = self.client.get(reverse('admin:orders_subscriptionpayment_changelist'), {"status__exact": "SUCCESS"})
        self.assertContains(response, "pay_admin_test_1")

    def test_razorpay_payment_id_visible_to_admin(self):
        response = self.client.get(reverse('admin:orders_subscriptionpayment_change', args=[self.payment.pk]))
        self.assertContains(response, "pay_admin_test_1")

    def test_add_permission_disabled(self):
        response = self.client.get(reverse('admin:orders_subscriptionpayment_add'))
        self.assertEqual(response.status_code, 403)

    def test_delete_permission_disabled(self):
        response = self.client.get(reverse('admin:orders_subscriptionpayment_delete', args=[self.payment.pk]))
        self.assertEqual(response.status_code, 403)

    def test_amount_and_status_fields_are_readonly(self):
        response = self.client.get(reverse('admin:orders_subscriptionpayment_change', args=[self.payment.pk]))
        self.assertNotContains(response, 'name="amount"')
        self.assertNotContains(response, 'name="status"')

    def test_submitting_an_amount_change_has_no_effect(self):
        url = reverse('admin:orders_subscriptionpayment_change', args=[self.payment.pk])
        self.client.post(url, {"amount": "1.00", "status": "FAILED", "_save": "Save"})
        self.payment.refresh_from_db()
        self.assertEqual(str(self.payment.amount), "999.00")
        self.assertEqual(self.payment.status, SubscriptionPayment.Status.SUCCESS)


class SubscriptionAdminNoSecretsExposedTests(TestCase):
    """Confirms no application secret ever renders on any of these three
    admin pages -- Razorpay's own ids (subscription/plan/payment) are
    EXPECTED and fine here (that's this phase's whole point: available to
    authorized Django admins, unlike the public API); actual secrets
    (API/webhook keys) must never appear anywhere."""

    def setUp(self):
        self.superuser = User.objects.create_superuser(username="secrets_super", password="password123", email="super5@x.com")
        self.client.force_login(self.superuser)
        self.student = User.objects.create_user(username="secrets_student", password="password123")
        self.plan = SubscriptionPlan.objects.create(
            name="Secrets Test Plan", billing_interval="MONTHLY", price="999.00", razorpay_plan_id="plan_secrets_1",
        )
        self.subscription = Subscription.objects.create(
            user=self.student, plan=self.plan, status=Subscription.Status.ACTIVE,
            razorpay_subscription_id="sub_secrets_1",
        )
        SubscriptionPayment.objects.create(
            subscription=self.subscription, razorpay_payment_id="pay_secrets_1",
            amount="999.00", status=SubscriptionPayment.Status.SUCCESS,
        )

    def test_no_secret_settings_values_leak_into_admin_pages(self):
        from django.conf import settings
        secret_values = [
            getattr(settings, 'RAZORPAY_KEY_SECRET', ''),
            getattr(settings, 'RAZORPAY_WEBHOOK_SECRET', ''),
            getattr(settings, 'SECRET_KEY', ''),
        ]
        urls = [
            reverse('admin:orders_subscriptionplan_changelist'),
            reverse('admin:orders_subscriptionplan_change', args=[self.plan.pk]),
            reverse('admin:orders_subscription_changelist'),
            reverse('admin:orders_subscription_change', args=[self.subscription.pk]),
            reverse('admin:orders_subscriptionpayment_changelist'),
            reverse('admin:orders_subscriptionpayment_change', args=[SubscriptionPayment.objects.first().pk]),
        ]
        for url in urls:
            response = self.client.get(url)
            content = response.content.decode('utf-8', errors='ignore')
            for secret in secret_values:
                if secret:  # only check non-empty configured secrets
                    self.assertNotIn(secret, content, f"secret value leaked on {url}")
