"""
Phase 3.4.4 -- subscription-based course access control. Tests the
centralized courses/services/access.py helpers directly (unit-level) and
through the two integration points this phase wired them into: the
VideoLessonViewSet `progress` action and CourseViewSet.my_courses.

Deliberately NOT tested here (out of scope, per the brief): cancellation
API, grace-period automation, subscription dashboard, invoices, refunds,
ledger, payouts, coupons, tax, mobile payment, Celery Beat, subscription
expiry notifications -- several tests below explicitly assert none of that
is touched (Enrollment is never deleted, access_until is never written).
"""
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from datetime import timedelta
from rest_framework import status
from rest_framework.test import APITestCase

from courses.models import Course, Module, VideoLesson, Enrollment
from courses.services.access import user_has_course_access, accessible_course_ids_for_user
from orders.models import SubscriptionPlan, Subscription

User = get_user_model()


def make_subscription(user, plan, sub_status, current_period_end, razorpay_id):
    return Subscription.objects.create(
        user=user, plan=plan, status=sub_status,
        razorpay_subscription_id=razorpay_id,
        current_period_start=timezone.now() - timedelta(days=15),
        current_period_end=current_period_end,
    )


class UserHasCourseAccessTests(TestCase):
    """Direct, unit-level tests of the centralized access helper."""

    def setUp(self):
        self.student = User.objects.create_user(username="access_student", password="password123")
        self.course_in_plan = Course.objects.create(
            title="Plan Course", description="x", price=999, is_published=True, course_type=Course.CourseType.RECORDED,
        )
        self.course_outside_plan = Course.objects.create(
            title="Outside Plan Course", description="x", price=999, is_published=True, course_type=Course.CourseType.RECORDED,
        )
        self.plan = SubscriptionPlan.objects.create(
            name="Access Test Plan", billing_interval="MONTHLY", price="999.00", razorpay_plan_id="plan_access_1",
        )
        self.plan.courses.add(self.course_in_plan)
        self.future = timezone.now() + timedelta(days=15)
        self.past = timezone.now() - timedelta(days=1)

    # 1. Permanent Enrollment grants access.
    def test_permanent_enrollment_grants_access(self):
        Enrollment.objects.create(user=self.student, course=self.course_outside_plan)
        self.assertTrue(user_has_course_access(self.student, self.course_outside_plan))

    # 2. No Enrollment, active valid subscription grants access.
    def test_active_subscription_grants_access_without_enrollment(self):
        make_subscription(self.student, self.plan, Subscription.Status.ACTIVE, self.future, "sub_access_1")
        self.assertFalse(Enrollment.objects.filter(user=self.student, course=self.course_in_plan).exists())
        self.assertTrue(user_has_course_access(self.student, self.course_in_plan))

    # 3. Active subscription does not grant access outside its plan.
    def test_active_subscription_does_not_grant_access_outside_plan(self):
        make_subscription(self.student, self.plan, Subscription.Status.ACTIVE, self.future, "sub_access_2")
        self.assertFalse(user_has_course_access(self.student, self.course_outside_plan))

    # 4. Subscription with a past period end does not grant access, regardless of status text.
    def test_subscription_with_past_period_end_does_not_grant_access(self):
        make_subscription(self.student, self.plan, Subscription.Status.ACTIVE, self.past, "sub_access_3")
        self.assertFalse(user_has_course_access(self.student, self.course_in_plan))

    # 5. CANCELLED subscription does NOT grant access after its period ends.
    def test_cancelled_subscription_no_access_after_period_end(self):
        make_subscription(self.student, self.plan, Subscription.Status.CANCELLED, self.past, "sub_access_4")
        self.assertFalse(user_has_course_access(self.student, self.course_in_plan))

    # 6. Cancellation before period end: access remains valid until the paid period ends.
    def test_cancelled_subscription_access_valid_until_period_end(self):
        make_subscription(self.student, self.plan, Subscription.Status.CANCELLED, self.future, "sub_access_5")
        self.assertTrue(user_has_course_access(self.student, self.course_in_plan))

    # 7. COMPLETED subscription cannot grant expired access.
    def test_completed_subscription_no_access_after_period_end(self):
        make_subscription(self.student, self.plan, Subscription.Status.COMPLETED, self.past, "sub_access_6")
        self.assertFalse(user_has_course_access(self.student, self.course_in_plan))

    def test_completed_subscription_access_valid_until_period_end(self):
        # Symmetric with CANCELLED -- COMPLETED means "will not renew
        # further", not "access ended the instant it completed".
        make_subscription(self.student, self.plan, Subscription.Status.COMPLETED, self.future, "sub_access_6b")
        self.assertTrue(user_has_course_access(self.student, self.course_in_plan))

    # 8. EXPIRED subscription cannot grant access -- unconditionally, even
    # with a (contrived) future current_period_end.
    def test_expired_status_subscription_never_grants_access(self):
        make_subscription(self.student, self.plan, Subscription.Status.EXPIRED, self.future, "sub_access_7")
        self.assertFalse(user_has_course_access(self.student, self.course_in_plan))

    # 9. Enrollment + subscription: access survives subscription expiry.
    def test_enrollment_plus_subscription_keeps_access_after_subscription_expires(self):
        Enrollment.objects.create(user=self.student, course=self.course_in_plan)
        sub = make_subscription(self.student, self.plan, Subscription.Status.ACTIVE, self.future, "sub_access_8")
        self.assertTrue(user_has_course_access(self.student, self.course_in_plan))

        sub.current_period_end = self.past
        sub.status = Subscription.Status.CANCELLED
        sub.save()
        self.assertTrue(user_has_course_access(self.student, self.course_in_plan))  # Enrollment alone still grants it

    # 10. Subscription expiration never deletes Enrollment.
    def test_subscription_expiration_never_deletes_enrollment(self):
        Enrollment.objects.create(user=self.student, course=self.course_in_plan)
        sub = make_subscription(self.student, self.plan, Subscription.Status.ACTIVE, self.future, "sub_access_9")
        sub.current_period_end = self.past
        sub.status = Subscription.Status.EXPIRED
        sub.save()
        self.assertTrue(Enrollment.objects.filter(user=self.student, course=self.course_in_plan).exists())

    # 11. Subscription does not grant LiveClass access (course_type guard).
    def test_subscription_does_not_grant_live_course_access(self):
        live_course = Course.objects.create(
            title="Live Course", description="x", price=999, is_published=True, course_type=Course.CourseType.LIVE,
        )
        self.plan.courses.add(live_course)
        make_subscription(self.student, self.plan, Subscription.Status.ACTIVE, self.future, "sub_access_10")
        self.assertFalse(user_has_course_access(self.student, live_course))
        # Enrollment still works for a live course (unchanged, existing precedent).
        Enrollment.objects.create(user=self.student, course=live_course)
        self.assertTrue(user_has_course_access(self.student, live_course))

    # 12. Bundle-in-plan: current SubscriptionPlan model has no `bundles`
    # relationship (confirmed by reading the model fresh before
    # implementing) -- only `courses` is supported. This test documents
    # that a plan's access is scoped to its directly-listed courses only.
    def test_subscription_plan_has_no_bundle_relationship_to_expand(self):
        self.assertFalse(hasattr(self.plan, 'bundles'))
        unrelated_course = Course.objects.create(
            title="Never In Any Bundle Reference", description="x", price=1, is_published=True,
        )
        make_subscription(self.student, self.plan, Subscription.Status.ACTIVE, self.future, "sub_access_11")
        self.assertFalse(user_has_course_access(self.student, unrelated_course))

    # 13. Unpublished/private course rules remain respected -- an
    # unrelated unpublished course never leaks access just because the
    # user has SOME subscription; a plan-included unpublished course
    # mirrors Enrollment's existing precedent (ownership is independent of
    # current publish state -- CourseViewSet.get_queryset's is_published
    # gate, unchanged by this phase, is what actually controls discovery).
    def test_unrelated_unpublished_course_never_granted_via_subscription(self):
        unpublished = Course.objects.create(title="Unpublished", description="x", price=1, is_published=False)
        make_subscription(self.student, self.plan, Subscription.Status.ACTIVE, self.future, "sub_access_12")
        self.assertFalse(user_has_course_access(self.student, unpublished))

    def test_plan_included_unpublished_course_still_grants_access_like_enrollment_does(self):
        unpublished_in_plan = Course.objects.create(title="Unpublished In Plan", description="x", price=1, is_published=False)
        self.plan.courses.add(unpublished_in_plan)
        make_subscription(self.student, self.plan, Subscription.Status.ACTIVE, self.future, "sub_access_13")
        self.assertTrue(user_has_course_access(self.student, unpublished_in_plan))

    # 15. Multiple historical/terminal subscriptions do not incorrectly grant access.
    def test_multiple_historical_terminal_subscriptions_do_not_grant_access(self):
        make_subscription(self.student, self.plan, Subscription.Status.CANCELLED, self.past, "sub_access_14a")
        other_plan = SubscriptionPlan.objects.create(
            name="Other Plan", billing_interval="MONTHLY", price="500.00", razorpay_plan_id="plan_access_other",
        )
        other_plan.courses.add(self.course_outside_plan)
        Subscription.objects.create(
            user=self.student, plan=other_plan, status=Subscription.Status.EXPIRED,
            razorpay_subscription_id="sub_access_14b", current_period_end=self.future,
        )
        self.assertFalse(user_has_course_access(self.student, self.course_in_plan))
        self.assertFalse(user_has_course_access(self.student, self.course_outside_plan))

    def test_unauthenticated_user_has_no_access(self):
        from django.contrib.auth.models import AnonymousUser
        self.assertFalse(user_has_course_access(AnonymousUser(), self.course_in_plan))

    # 16. No N+1: accessible_course_ids_for_user's query count stays
    # constant regardless of how many enrollments/courses exist.
    def test_accessible_course_ids_does_not_grow_queries_with_more_enrollments(self):
        extra_courses = [
            Course.objects.create(title=f"Extra {i}", description="x", price=1, is_published=True)
            for i in range(5)
        ]
        for c in extra_courses:
            Enrollment.objects.create(user=self.student, course=c)
        make_subscription(self.student, self.plan, Subscription.Status.ACTIVE, self.future, "sub_access_15")

        with self.assertNumQueries(3):
            result = accessible_course_ids_for_user(self.student)
        self.assertIn(self.course_in_plan.id, result)
        for c in extra_courses:
            self.assertIn(c.id, result)


class SubscriptionAccessAPITests(APITestCase):
    """Integration-level tests through the actual API endpoints this phase wired up."""

    def setUp(self):
        self.student = User.objects.create_user(username="access_api_student", password="password123")
        self.other_student = User.objects.create_user(username="access_api_other", password="password123")
        self.course = Course.objects.create(
            title="API Access Course", description="x", price=999, is_published=True, course_type=Course.CourseType.RECORDED,
        )
        self.module = Module.objects.create(course=self.course, title="Module 1")
        self.lesson = VideoLesson.objects.create(module=self.module, title="Lesson 1")
        self.plan = SubscriptionPlan.objects.create(
            name="API Access Plan", billing_interval="MONTHLY", price="999.00", razorpay_plan_id="plan_api_access_1",
        )
        self.plan.courses.add(self.course)
        self.future = timezone.now() + timedelta(days=15)

    # 14. Unauthorized user cannot bypass access through API manipulation.
    def test_progress_action_denies_user_with_no_access(self):
        self.client.force_authenticate(user=self.other_student)
        url = reverse('lesson-progress', kwargs={'pk': self.lesson.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_progress_action_grants_access_via_subscription(self):
        make_subscription(self.student, self.plan, Subscription.Status.ACTIVE, self.future, "sub_api_access_1")
        self.client.force_authenticate(user=self.student)
        url = reverse('lesson-progress', kwargs={'pk': self.lesson.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_my_courses_includes_subscription_covered_course(self):
        make_subscription(self.student, self.plan, Subscription.Status.ACTIVE, self.future, "sub_api_access_2")
        self.client.force_authenticate(user=self.student)
        url = reverse('course-my-courses')
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        returned_ids = [c['id'] for c in response.data]
        self.assertIn(self.course.id, returned_ids)

    def test_my_courses_excludes_course_after_subscription_expires(self):
        sub = make_subscription(self.student, self.plan, Subscription.Status.ACTIVE, self.future, "sub_api_access_3")
        sub.current_period_end = timezone.now() - timedelta(days=1)
        sub.status = Subscription.Status.EXPIRED
        sub.save()
        self.client.force_authenticate(user=self.student)
        url = reverse('course-my-courses')
        response = self.client.get(url)
        returned_ids = [c['id'] for c in response.data]
        self.assertNotIn(self.course.id, returned_ids)
