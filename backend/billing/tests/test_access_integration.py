from datetime import date, timedelta
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.test import TestCase
from courses.models import Course, Enrollment
from courses.services.access import user_has_course_access, accessible_course_ids_for_user
from billing.models import (
    StudentBillingPlan,
    StudentInvoice,
    BillingAccessExtension,
    BillingType,
    PlanStatus,
    InvoiceStatus,
    PaymentMethod,
)
from billing.services.lifecycle import create_billing_plan, extend_access
from billing.services.access import student_has_billing_access
from billing.services.payment import record_manual_payment

User = get_user_model()


class AccessIntegrationTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(
            username="admin_access", email="admin@example.com", password="password123"
        )
        self.student = User.objects.create_user(
            username="student_access", email="student@example.com", password="password123"
        )
        self.course = Course.objects.create(
            title="Kuchipudi Masterclass", price=3000.00, is_published=True, course_type=Course.CourseType.RECORDED
        )
        self.enrollment = Enrollment.objects.create(
            user=self.student, course=self.course
        )

    def test_legacy_enrollment_without_billing_plan_retains_access(self):
        # A student with Enrollment but NO StudentBillingPlan retains full access
        self.assertIsNone(student_has_billing_access(self.student, self.course))
        self.assertTrue(user_has_course_access(self.student, self.course))
        self.assertIn(self.course.id, accessible_course_ids_for_user(self.student))

    def test_active_and_grace_period_plan_grants_access(self):
        start = date(2026, 10, 10)
        plan = create_billing_plan(
            student=self.student,
            course=self.course,
            enrollment=self.enrollment,
            billing_type=BillingType.MONTHLY,
            amount=Decimal('3000.00'),
            start_date=start,
            grace_period_days=7,
            issue_initial_invoice=True
        )

        # 1. Active status
        self.assertTrue(student_has_billing_access(self.student, self.course, as_of_date=date(2026, 10, 9)))
        self.assertTrue(user_has_course_access(self.student, self.course))

        # 2. In Grace period
        plan.status = PlanStatus.GRACE_PERIOD
        plan.save()
        self.assertTrue(student_has_billing_access(self.student, self.course, as_of_date=date(2026, 10, 15)))
        self.assertTrue(user_has_course_access(self.student, self.course))

    def test_restricted_plan_denies_access_while_preserving_enrollment(self):
        start = date(2026, 10, 10)
        plan = create_billing_plan(
            student=self.student,
            course=self.course,
            enrollment=self.enrollment,
            billing_type=BillingType.MONTHLY,
            amount=Decimal('3000.00'),
            start_date=start,
            grace_period_days=7,
            issue_initial_invoice=True
        )

        # Fast-forward past access_restriction_date (2026-10-18)
        plan.status = PlanStatus.RESTRICTED
        plan.save()

        # Enrollment MUST still exist in database
        self.assertTrue(Enrollment.objects.filter(user=self.student, course=self.course).exists())

        # Access check should be False
        self.assertFalse(student_has_billing_access(self.student, self.course, as_of_date=date(2026, 10, 19)))
        self.assertFalse(user_has_course_access(self.student, self.course))
        self.assertNotIn(self.course.id, accessible_course_ids_for_user(self.student))

    def test_admin_access_extension_overrides_restriction(self):
        start = date(2026, 10, 10)
        plan = create_billing_plan(
            student=self.student,
            course=self.course,
            enrollment=self.enrollment,
            billing_type=BillingType.MONTHLY,
            amount=Decimal('3000.00'),
            start_date=start,
            grace_period_days=7,
            issue_initial_invoice=True
        )
        plan.status = PlanStatus.RESTRICTED
        plan.save()

        # Admin grants extension until 25 Oct 2026
        extend_access(
            plan=plan,
            extended_until=date(2026, 10, 25),
            reason="Student requested 7 days extension due to medical emergency",
            actor=self.admin
        )

        # Before/on 25 Oct 2026 -> Access granted
        self.assertTrue(student_has_billing_access(self.student, self.course, as_of_date=date(2026, 10, 20)))
        self.assertTrue(student_has_billing_access(self.student, self.course, as_of_date=date(2026, 10, 25)))

        # After 25 Oct 2026 -> Access restricted again
        self.assertFalse(student_has_billing_access(self.student, self.course, as_of_date=date(2026, 10, 26)))

    def test_payment_restores_restricted_access(self):
        start = date(2026, 10, 10)
        plan = create_billing_plan(
            student=self.student,
            course=self.course,
            enrollment=self.enrollment,
            billing_type=BillingType.MONTHLY,
            amount=Decimal('3000.00'),
            start_date=start,
            grace_period_days=7,
            issue_initial_invoice=True
        )
        plan.status = PlanStatus.RESTRICTED
        plan.save()

        # Student pays the overdue invoice
        invoice = StudentInvoice.objects.get(billing_plan=plan)
        record_manual_payment(
            invoice=invoice,
            amount=Decimal('3000.00'),
            payment_method=PaymentMethod.BANK_TRANSFER,
            reference_number="NEFT/123456",
            actor=self.admin
        )

        plan.refresh_from_db()
        self.assertEqual(plan.status, PlanStatus.ACTIVE)
        self.assertTrue(student_has_billing_access(self.student, self.course))
        self.assertTrue(user_has_course_access(self.student, self.course))
        self.assertIn(self.course.id, accessible_course_ids_for_user(self.student))
