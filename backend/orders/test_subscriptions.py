"""
Phase 3.4.1 -- SubscriptionPlan / Subscription / SubscriptionPayment model
tests. Database-foundation-only, matching the scope of the models
themselves: no Razorpay API calls, no webhooks, no checkout, no access-
control logic, no cancellation workflow are exercised here (none of that
exists yet) -- these tests verify the schema, constraints, and the one
piece of application-level logic this phase does include (the "one active
subscription per student" guard in Subscription.clean()/save()).
"""
from decimal import Decimal
from django.test import TestCase
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import ProtectedError

from courses.models import Course
from orders.models import SubscriptionPlan, Subscription, SubscriptionPayment

User = get_user_model()


class SubscriptionPlanModelTests(TestCase):
    def setUp(self):
        self.course1 = Course.objects.create(title="Kathak Level 1", description="x", price=1000, is_published=True)
        self.course2 = Course.objects.create(title="Kathak Level 2", description="x", price=1200, is_published=True)

    # 1. SubscriptionPlan creation
    def test_subscription_plan_creation(self):
        plan = SubscriptionPlan.objects.create(
            name="All Kathak Access", billing_interval=SubscriptionPlan.BillingInterval.MONTHLY, price="999.00"
        )
        self.assertIsNotNone(plan.pk)
        self.assertTrue(plan.slug)  # auto-generated
        self.assertEqual(plan.slug, "all-kathak-access")

    # 2. Monthly plan
    def test_monthly_plan(self):
        plan = SubscriptionPlan.objects.create(name="Monthly Plan", billing_interval="MONTHLY", price="500.00")
        self.assertEqual(plan.billing_interval, SubscriptionPlan.BillingInterval.MONTHLY)

    # 3. Yearly plan
    def test_yearly_plan(self):
        plan = SubscriptionPlan.objects.create(name="Yearly Plan", billing_interval="YEARLY", price="5000.00")
        self.assertEqual(plan.billing_interval, SubscriptionPlan.BillingInterval.YEARLY)

    def test_billing_interval_only_allows_monthly_or_yearly(self):
        plan = SubscriptionPlan(name="Bad Interval", billing_interval="WEEKLY", price="100.00")
        with self.assertRaises(ValidationError):
            plan.full_clean()

    # 4. INR default
    def test_currency_defaults_to_inr(self):
        plan = SubscriptionPlan.objects.create(name="Default Currency Plan", billing_interval="MONTHLY", price="100.00")
        self.assertEqual(plan.currency, "INR")

    # 5. Negative price rejected
    def test_negative_price_rejected(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                SubscriptionPlan.objects.create(name="Negative Price", billing_interval="MONTHLY", price="-1.00")

    # 6. Plan-course relationship
    def test_plan_course_relationship(self):
        plan = SubscriptionPlan.objects.create(name="Two Course Plan", billing_interval="MONTHLY", price="1500.00")
        plan.courses.add(self.course1, self.course2)
        self.assertEqual(set(plan.courses.values_list('id', flat=True)), {self.course1.id, self.course2.id})
        # Reverse relation, per courses M2M related_name.
        self.assertIn(plan, self.course1.subscription_plans.all())

    def test_plan_can_reference_published_courses(self):
        plan = SubscriptionPlan.objects.create(name="Published Only Plan", billing_interval="MONTHLY", price="100.00")
        plan.courses.add(self.course1)
        self.assertTrue(self.course1.is_published)
        self.assertIn(self.course1, plan.courses.all())

    def test_plan_slug_uniqueness_suffix_on_name_collision(self):
        SubscriptionPlan.objects.create(name="Same Name", billing_interval="MONTHLY", price="100.00")
        second = SubscriptionPlan.objects.create(name="Same Name", billing_interval="MONTHLY", price="200.00")
        self.assertNotEqual(second.slug, "same-name")
        self.assertTrue(second.slug.startswith("same-name-"))

    def test_razorpay_plan_id_is_nullable(self):
        # Existing plans (everything created before Phase 3.4.2's Razorpay
        # integration exists) must be able to have no Razorpay Plan linked
        # at all -- this must not be a required field.
        plan = SubscriptionPlan.objects.create(name="Unlinked Plan", billing_interval="MONTHLY", price="100.00")
        self.assertIsNone(plan.razorpay_plan_id)

    def test_multiple_plans_with_null_razorpay_plan_id_allowed(self):
        # Multiple NULLs must be allowed under the unique constraint --
        # SubscriptionPlan doesn't override save() to call full_clean() (no
        # model-level validation gate here, unlike Subscription), so this
        # also confirms the raw DB constraint itself tolerates multiple
        # NULLs, not just a Python-level check.
        SubscriptionPlan.objects.create(name="Plan A", billing_interval="MONTHLY", price="100.00")
        SubscriptionPlan.objects.create(name="Plan B", billing_interval="MONTHLY", price="200.00")
        self.assertEqual(SubscriptionPlan.objects.filter(razorpay_plan_id__isnull=True).count(), 2)

    def test_razorpay_plan_id_uniqueness(self):
        SubscriptionPlan.objects.create(
            name="Linked Plan One", billing_interval="MONTHLY", price="100.00", razorpay_plan_id="plan_dup_123"
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                SubscriptionPlan.objects.create(
                    name="Linked Plan Two", billing_interval="MONTHLY", price="200.00", razorpay_plan_id="plan_dup_123"
                )

    def test_razorpay_plan_id_can_be_set_and_retrieved(self):
        plan = SubscriptionPlan.objects.create(
            name="Real Linked Plan", billing_interval="YEARLY", price="5000.00", razorpay_plan_id="plan_real_456"
        )
        plan.refresh_from_db()
        self.assertEqual(plan.razorpay_plan_id, "plan_real_456")


class SubscriptionModelTests(TestCase):
    def setUp(self):
        self.student = User.objects.create_user(username="sub_student", password="password123")
        self.other_student = User.objects.create_user(username="sub_other_student", password="password123")
        self.plan = SubscriptionPlan.objects.create(name="Test Plan", billing_interval="MONTHLY", price="999.00")

    # 7. Subscription creation
    def test_subscription_creation(self):
        sub = Subscription.objects.create(user=self.student, plan=self.plan)
        self.assertEqual(sub.status, Subscription.Status.CREATED)
        self.assertIsNone(sub.razorpay_subscription_id)

    # 8. Razorpay subscription ID uniqueness
    def test_razorpay_subscription_id_uniqueness(self):
        Subscription.objects.create(
            user=self.student, plan=self.plan, status=Subscription.Status.ACTIVE,
            razorpay_subscription_id="sub_dup_123"
        )
        # Subscription.save() calls full_clean() (see the model's own
        # docstring), so the duplicate is caught by Python-level
        # validate_unique() as a ValidationError before it ever reaches the
        # DB -- the unique=True constraint is still what's actually being
        # exercised, just surfaced through the nicer, earlier error path.
        with self.assertRaises(ValidationError):
            Subscription.objects.create(
                user=self.other_student, plan=self.plan, status=Subscription.Status.ACTIVE,
                razorpay_subscription_id="sub_dup_123"
            )

    def test_multiple_subscriptions_with_null_razorpay_id_allowed(self):
        # Both freshly CREATED, neither has a razorpay id yet -- multiple
        # NULLs must be allowed under the unique constraint (standard SQL
        # behavior, matches Order.razorpay_order_id's existing precedent).
        Subscription.objects.create(user=self.student, plan=self.plan, status=Subscription.Status.CANCELLED)
        Subscription.objects.create(user=self.other_student, plan=self.plan, status=Subscription.Status.CANCELLED)
        self.assertEqual(Subscription.objects.filter(razorpay_subscription_id__isnull=True).count(), 2)

    # 9. One active subscription per student
    def test_one_active_subscription_per_student_application_level(self):
        Subscription.objects.create(user=self.student, plan=self.plan, status=Subscription.Status.ACTIVE)
        second = Subscription(user=self.student, plan=self.plan, status=Subscription.Status.CREATED)
        with self.assertRaises(ValidationError):
            second.full_clean()
        with self.assertRaises(ValidationError):
            second.save()  # save() calls full_clean() too

    def test_one_active_subscription_per_student_database_level(self):
        # Bypass clean()/save()'s application-level guard via bulk_create,
        # to prove the DB constraint is a real, independent safety net --
        # not just a second code path that happens to duplicate the same
        # Python check.
        Subscription.objects.create(user=self.student, plan=self.plan, status=Subscription.Status.ACTIVE)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Subscription.objects.bulk_create([
                    Subscription(user=self.student, plan=self.plan, status=Subscription.Status.PENDING)
                ])

    def test_different_students_can_each_have_an_active_subscription(self):
        Subscription.objects.create(user=self.student, plan=self.plan, status=Subscription.Status.ACTIVE)
        # Must not raise -- the constraint is per-user, not global.
        Subscription.objects.create(user=self.other_student, plan=self.plan, status=Subscription.Status.ACTIVE)
        self.assertEqual(Subscription.objects.filter(status=Subscription.Status.ACTIVE).count(), 2)

    # 10. Historical cancelled subscription allowed
    def test_historical_cancelled_subscription_allowed_alongside_new_active_one(self):
        Subscription.objects.create(user=self.student, plan=self.plan, status=Subscription.Status.CANCELLED)
        # A new ACTIVE subscription for the same student must be allowed --
        # the constraint only ever blocks a second NON-terminal subscription.
        active = Subscription.objects.create(user=self.student, plan=self.plan, status=Subscription.Status.ACTIVE)
        self.assertIsNotNone(active.pk)
        self.assertEqual(Subscription.objects.filter(user=self.student).count(), 2)

    def test_multiple_historical_cancelled_subscriptions_allowed(self):
        Subscription.objects.create(user=self.student, plan=self.plan, status=Subscription.Status.CANCELLED)
        Subscription.objects.create(user=self.student, plan=self.plan, status=Subscription.Status.EXPIRED)
        Subscription.objects.create(user=self.student, plan=self.plan, status=Subscription.Status.COMPLETED)
        self.assertEqual(Subscription.objects.filter(user=self.student).count(), 3)

    def test_all_nine_status_choices_are_valid(self):
        for value, _ in Subscription.Status.choices:
            sub = Subscription(user=self.student, plan=self.plan, status=value)
            sub.full_clean()  # must not raise for any real Razorpay status

    # 15. Subscription period/access fields
    def test_subscription_period_and_access_fields(self):
        from django.utils import timezone
        import datetime
        start = timezone.now()
        end = start + datetime.timedelta(days=30)
        access = end
        sub = Subscription.objects.create(
            user=self.student, plan=self.plan, status=Subscription.Status.ACTIVE,
            current_period_start=start, current_period_end=end, access_until=access,
        )
        sub.refresh_from_db()
        self.assertEqual(sub.current_period_start, start)
        self.assertEqual(sub.current_period_end, end)
        self.assertEqual(sub.access_until, access)

    def test_cancellation_fields(self):
        from django.utils import timezone
        sub = Subscription.objects.create(user=self.student, plan=self.plan, status=Subscription.Status.ACTIVE)
        sub.cancelled_at = timezone.now()
        sub.cancel_at_period_end = True
        sub.status = Subscription.Status.CANCELLED
        sub.save()
        sub.refresh_from_db()
        self.assertTrue(sub.cancel_at_period_end)
        self.assertIsNotNone(sub.cancelled_at)

    # 16. Model deletion protection (Subscription.plan)
    def test_plan_deletion_protected_when_subscription_exists(self):
        Subscription.objects.create(user=self.student, plan=self.plan, status=Subscription.Status.ACTIVE)
        with self.assertRaises(ProtectedError):
            self.plan.delete()


class SubscriptionPaymentModelTests(TestCase):
    def setUp(self):
        self.student = User.objects.create_user(username="payment_student", password="password123")
        self.plan = SubscriptionPlan.objects.create(name="Payment Test Plan", billing_interval="MONTHLY", price="750.00")
        self.subscription = Subscription.objects.create(
            user=self.student, plan=self.plan, status=Subscription.Status.ACTIVE,
            razorpay_subscription_id="sub_payment_test_1"
        )

    # 11. SubscriptionPayment creation
    def test_subscription_payment_creation(self):
        payment = SubscriptionPayment.objects.create(
            subscription=self.subscription, amount="750.00", status=SubscriptionPayment.Status.SUCCESS,
            razorpay_payment_id="pay_test_1", razorpay_subscription_id="sub_payment_test_1",
        )
        self.assertEqual(payment.currency, "INR")
        # amount is passed as a string to .create(); Django doesn't coerce
        # it to Decimal on the in-memory instance until it's re-read from
        # the DB -- compare via refresh_from_db(), matching the Decimal-
        # comparison idiom already used throughout this app's other tests.
        payment.refresh_from_db()
        self.assertEqual(payment.amount, Decimal("750.00"))

    # 12. Razorpay payment ID uniqueness
    def test_razorpay_payment_id_uniqueness(self):
        SubscriptionPayment.objects.create(
            subscription=self.subscription, amount="750.00", status=SubscriptionPayment.Status.SUCCESS,
            razorpay_payment_id="pay_dup_1"
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                SubscriptionPayment.objects.create(
                    subscription=self.subscription, amount="750.00", status=SubscriptionPayment.Status.SUCCESS,
                    razorpay_payment_id="pay_dup_1"
                )

    def test_multiple_payments_with_null_razorpay_id_allowed(self):
        SubscriptionPayment.objects.create(subscription=self.subscription, amount="750.00", status=SubscriptionPayment.Status.CREATED)
        SubscriptionPayment.objects.create(subscription=self.subscription, amount="750.00", status=SubscriptionPayment.Status.CREATED)
        self.assertEqual(SubscriptionPayment.objects.filter(razorpay_payment_id__isnull=True).count(), 2)

    # 13. Negative payment amount rejected
    def test_negative_payment_amount_rejected(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                SubscriptionPayment.objects.create(subscription=self.subscription, amount="-50.00", status=SubscriptionPayment.Status.FAILED)

    # 14. SubscriptionPayment status values
    def test_subscription_payment_status_values(self):
        for value, _ in SubscriptionPayment.Status.choices:
            payment = SubscriptionPayment(subscription=self.subscription, amount="10.00", status=value)
            payment.full_clean()  # CREATED/SUCCESS/FAILED/REFUNDED must all be valid

    def test_subscription_payment_traceability_fields(self):
        payment = SubscriptionPayment.objects.create(
            subscription=self.subscription, amount="750.00", status=SubscriptionPayment.Status.SUCCESS,
            razorpay_payment_id="pay_trace_1", razorpay_subscription_id="sub_payment_test_1",
        )
        self.assertEqual(payment.razorpay_subscription_id, self.subscription.razorpay_subscription_id)

    # 16. Model deletion protection (SubscriptionPayment.subscription)
    def test_subscription_deletion_protected_when_payment_exists(self):
        SubscriptionPayment.objects.create(subscription=self.subscription, amount="750.00", status=SubscriptionPayment.Status.SUCCESS)
        with self.assertRaises(ProtectedError):
            self.subscription.delete()

    def test_payment_ordering_is_by_creation_descending(self):
        p1 = SubscriptionPayment.objects.create(subscription=self.subscription, amount="10.00", status=SubscriptionPayment.Status.SUCCESS)
        p2 = SubscriptionPayment.objects.create(subscription=self.subscription, amount="20.00", status=SubscriptionPayment.Status.SUCCESS)
        payments = list(SubscriptionPayment.objects.filter(subscription=self.subscription))
        self.assertEqual(payments[0].pk, p2.pk)
        self.assertEqual(payments[1].pk, p1.pk)
