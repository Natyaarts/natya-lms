"""
Course content access security follow-up (post-3.4.4). Confirms
GET /api/courses/{id}/ (and every other endpoint that can expose the same
data -- /api/courses/, /api/courses/modules/{id}/, /api/courses/lessons/{id}/)
never returns video_file/transcript/timed_transcript/translated_audios to a
user who lacks a real access grant (permanent Enrollment, valid Subscription
covering the course, or an authoring relationship), while course/module/
lesson metadata stays publicly visible for the catalog experience, and
teacher/admin/CMS access is completely unaffected.

Reuses the Phase 3.4.4 centralized access helper (user_has_course_access /
accessible_course_ids_for_user) and its own approved subscription-validity
rules -- no second access implementation, no new migration.
"""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from courses.models import Course, Module, VideoLesson, TranslatedAudio, Enrollment, CourseInstructor
from orders.models import SubscriptionPlan, Subscription

User = get_user_model()


def make_subscription(user, plan, sub_status, current_period_end, razorpay_id):
    return Subscription.objects.create(
        user=user, plan=plan, status=sub_status,
        razorpay_subscription_id=razorpay_id,
        current_period_start=timezone.now() - timedelta(days=15),
        current_period_end=current_period_end,
    )


class ContentAccessSecurityTests(APITestCase):
    def setUp(self):
        self.future = timezone.now() + timedelta(days=15)
        self.past = timezone.now() - timedelta(days=1)

        self.superuser = User.objects.create_superuser(username="content_sec_super", password="password123", email="super@x.com")
        self.staff = User.objects.create_user(username="content_sec_staff", password="password123")
        self.staff.is_staff = True
        self.staff.save()

        self.teacher_owner = User.objects.create_user(username="content_sec_teacher_owner", password="password123")
        self.teacher_owner.is_teacher = True
        self.teacher_owner.save()

        self.teacher_other = User.objects.create_user(username="content_sec_teacher_other", password="password123")
        self.teacher_other.is_teacher = True
        self.teacher_other.save()

        self.legacy_teacher = User.objects.create_user(username="content_sec_legacy_teacher", password="password123")
        self.legacy_teacher.is_teacher = True
        self.legacy_teacher.save()

        self.enrolled_student = User.objects.create_user(username="content_sec_enrolled", password="password123")
        self.subscribed_student = User.objects.create_user(username="content_sec_subscribed", password="password123")
        self.wrong_course_subscriber = User.objects.create_user(username="content_sec_wrong_sub", password="password123")
        self.expired_subscriber = User.objects.create_user(username="content_sec_expired_sub", password="password123")
        self.plain_student = User.objects.create_user(username="content_sec_plain", password="password123")

        self.course = Course.objects.create(
            title="Security Test Course", description="A course about testing security.",
            price=999, is_published=True, course_type=Course.CourseType.RECORDED,
        )
        self.other_course = Course.objects.create(
            title="Other Course", description="x", price=500, is_published=True, course_type=Course.CourseType.RECORDED,
        )
        self.module = Module.objects.create(course=self.course, title="Module 1")
        self.lesson = VideoLesson.objects.create(
            module=self.module, title="Lesson 1", description="Lesson description",
            transcript="Secret transcript text.", timed_transcript="00:00:01 --> Secret timed line.",
            video_file="videos/lessons/secret.mp4",
        )
        self.audio = TranslatedAudio.objects.create(
            lesson=self.lesson, language_code="hi-IN", language_name="Hindi",
            audio_file="videos/audios/secret_hi.mp3", status="completed",
        )

        CourseInstructor.objects.create(course=self.course, user=self.teacher_owner, role=CourseInstructor.InstructorRole.TEACHER)
        Enrollment.objects.create(user=self.enrolled_student, course=self.course)
        Enrollment.objects.create(user=self.legacy_teacher, course=self.course)  # legacy fallback, no CourseInstructor row

        self.plan = SubscriptionPlan.objects.create(
            name="Content Security Plan", billing_interval="MONTHLY", price="999.00", razorpay_plan_id="plan_content_sec_1",
        )
        self.plan.courses.add(self.course)
        self.other_plan = SubscriptionPlan.objects.create(
            name="Other Plan", billing_interval="MONTHLY", price="500.00", razorpay_plan_id="plan_content_sec_other",
        )
        self.other_plan.courses.add(self.other_course)

        make_subscription(self.subscribed_student, self.plan, Subscription.Status.ACTIVE, self.future, "sub_content_sec_1")
        make_subscription(self.wrong_course_subscriber, self.other_plan, Subscription.Status.ACTIVE, self.future, "sub_content_sec_2")
        expired = make_subscription(self.expired_subscriber, self.plan, Subscription.Status.ACTIVE, self.future, "sub_content_sec_3")
        expired.current_period_end = self.past
        expired.status = Subscription.Status.EXPIRED
        expired.save()

        self.detail_url = reverse('course-detail', kwargs={'pk': self.course.pk})
        self.list_url = reverse('course-list')

    def _get_lesson(self, response_data):
        return response_data['modules'][0]['lessons'][0]

    def _assert_locked(self, response):
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        lesson = self._get_lesson(response.data)
        self.assertTrue(lesson['is_locked'])
        self.assertIsNone(lesson['video_file'])
        self.assertIsNone(lesson['transcript'])
        self.assertIsNone(lesson['timed_transcript'])
        self.assertEqual(lesson['translated_audios'], [])
        return lesson

    def _assert_unlocked(self, response):
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        lesson = self._get_lesson(response.data)
        self.assertFalse(lesson['is_locked'])
        self.assertIsNotNone(lesson['video_file'])
        self.assertIn('secret.mp4', lesson['video_file'])
        self.assertEqual(lesson['transcript'], "Secret transcript text.")
        self.assertEqual(lesson['timed_transcript'], "00:00:01 --> Secret timed line.")
        self.assertEqual(len(lesson['translated_audios']), 1)
        self.assertIn('secret_hi.mp3', lesson['translated_audios'][0]['audio_file'])
        return lesson

    # ---- 1-5: anonymous user ----

    def test_anonymous_user_can_retrieve_published_course_metadata(self):
        response = self.client.get(self.detail_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['title'], "Security Test Course")
        self.assertEqual(response.data['description'], "A course about testing security.")
        self.assertIn('price', response.data)
        self.assertIn('thumbnail', response.data)
        module = response.data['modules'][0]
        self.assertEqual(module['title'], "Module 1")
        lesson = module['lessons'][0]
        self.assertEqual(lesson['title'], "Lesson 1")
        self.assertEqual(lesson['description'], "Lesson description")  # description stays visible even locked

    def test_anonymous_user_does_not_receive_video_file(self):
        response = self.client.get(self.detail_url)
        lesson = self._get_lesson(response.data)
        self.assertIsNone(lesson['video_file'])

    def test_anonymous_user_does_not_receive_transcript(self):
        response = self.client.get(self.detail_url)
        lesson = self._get_lesson(response.data)
        self.assertIsNone(lesson['transcript'])

    def test_anonymous_user_does_not_receive_timed_transcript(self):
        response = self.client.get(self.detail_url)
        lesson = self._get_lesson(response.data)
        self.assertIsNone(lesson['timed_transcript'])

    def test_anonymous_user_does_not_receive_translated_audios(self):
        response = self.client.get(self.detail_url)
        lesson = self._get_lesson(response.data)
        self.assertEqual(lesson['translated_audios'], [])

    # ---- 6: authenticated non-owner ----

    def test_authenticated_non_owner_cannot_receive_protected_content(self):
        self.client.force_authenticate(user=self.plain_student)
        response = self.client.get(self.detail_url)
        self._assert_locked(response)

    # ---- 7: permanent Enrollment ----

    def test_permanent_enrollment_receives_full_content(self):
        self.client.force_authenticate(user=self.enrolled_student)
        response = self.client.get(self.detail_url)
        self._assert_unlocked(response)

    # ---- 8-10: subscription ----

    def test_valid_subscription_receives_full_content(self):
        self.client.force_authenticate(user=self.subscribed_student)
        response = self.client.get(self.detail_url)
        self._assert_unlocked(response)

    def test_subscription_for_another_course_does_not_grant_access(self):
        self.client.force_authenticate(user=self.wrong_course_subscriber)
        response = self.client.get(self.detail_url)
        self._assert_locked(response)

    def test_expired_subscription_does_not_receive_protected_content(self):
        self.client.force_authenticate(user=self.expired_subscriber)
        response = self.client.get(self.detail_url)
        self._assert_locked(response)

    # ---- 11-12: instructor ----

    def test_course_instructor_receives_full_content_for_assigned_course(self):
        self.client.force_authenticate(user=self.teacher_owner)
        response = self.client.get(self.detail_url)
        self._assert_unlocked(response)

    def test_unauthorized_teacher_does_not_receive_instructor_only_content(self):
        self.client.force_authenticate(user=self.teacher_other)
        response = self.client.get(self.detail_url)
        self._assert_locked(response)

    # ---- 13: superuser/staff ----

    def test_superuser_receives_full_content(self):
        self.client.force_authenticate(user=self.superuser)
        response = self.client.get(self.detail_url)
        self._assert_unlocked(response)

    def test_staff_receives_full_content(self):
        self.client.force_authenticate(user=self.staff)
        response = self.client.get(self.detail_url)
        self._assert_unlocked(response)

    # ---- 14: legacy teacher fallback ----

    def test_legacy_teacher_fallback_remains_functional(self):
        self.assertFalse(CourseInstructor.objects.filter(course=self.course, user=self.legacy_teacher).exists())
        self.client.force_authenticate(user=self.legacy_teacher)
        response = self.client.get(self.detail_url)
        self._assert_unlocked(response)

    # ---- 15: course list ----

    def test_course_list_does_not_leak_protected_lesson_content(self):
        response = self.client.get(self.list_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data if isinstance(response.data, list) else response.data.get('results', response.data)
        found = next(c for c in results if c['id'] == self.course.id)
        lesson = found['modules'][0]['lessons'][0]
        self.assertTrue(lesson['is_locked'])
        self.assertIsNone(lesson['video_file'])

    def test_course_list_unlocks_for_an_owning_user(self):
        self.client.force_authenticate(user=self.enrolled_student)
        response = self.client.get(self.list_url)
        results = response.data if isinstance(response.data, list) else response.data.get('results', response.data)
        found = next(c for c in results if c['id'] == self.course.id)
        lesson = found['modules'][0]['lessons'][0]
        self.assertFalse(lesson['is_locked'])

    # ---- 16-17: direct module/lesson endpoints ----

    def test_direct_lesson_endpoint_denied_for_anonymous(self):
        url = reverse('lesson-detail', kwargs={'pk': self.lesson.pk})
        response = self.client.get(url)
        self.assertIn(response.status_code, (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN, status.HTTP_404_NOT_FOUND))

    def test_direct_lesson_endpoint_denied_for_non_instructor_student(self):
        # Even an enrolled/subscribed student -- this endpoint is
        # authoring-only, never a student content-reading path.
        self.client.force_authenticate(user=self.enrolled_student)
        url = reverse('lesson-detail', kwargs={'pk': self.lesson.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_direct_lesson_endpoint_works_for_assigned_instructor(self):
        self.client.force_authenticate(user=self.teacher_owner)
        url = reverse('lesson-detail', kwargs={'pk': self.lesson.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('secret.mp4', response.data['video_file'])

    def test_direct_module_endpoint_denied_for_non_instructor(self):
        self.client.force_authenticate(user=self.plain_student)
        url = reverse('module-detail', kwargs={'pk': self.module.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_direct_module_endpoint_works_for_assigned_instructor(self):
        self.client.force_authenticate(user=self.teacher_owner)
        url = reverse('module-detail', kwargs={'pk': self.module.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    # ---- 18: no functional S3 URL ever leaks in a locked response ----

    def test_locked_response_contains_no_functional_media_url(self):
        response = self.client.get(self.detail_url)
        raw = str(response.content)
        self.assertNotIn('secret.mp4', raw)
        self.assertNotIn('secret_hi.mp3', raw)

    # 19 is covered by every _assert_unlocked() call above.

    # ---- 20: LessonProgress behavior unaffected ----

    def test_progress_action_still_works_via_subscription_after_this_change(self):
        self.client.force_authenticate(user=self.subscribed_student)
        url = reverse('lesson-progress', kwargs={'pk': self.lesson.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_progress_action_still_denies_user_without_access(self):
        self.client.force_authenticate(user=self.plain_student)
        url = reverse('lesson-progress', kwargs={'pk': self.lesson.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    # ---- 23: CMS/admin editing behavior unaffected ----

    def test_instructor_can_still_create_module(self):
        self.client.force_authenticate(user=self.teacher_owner)
        response = self.client.post(reverse('module-list'), {"title": "New Module", "course": self.course.id, "order": 2}, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_instructor_can_still_update_own_lesson(self):
        self.client.force_authenticate(user=self.teacher_owner)
        url = reverse('lesson-detail', kwargs={'pk': self.lesson.pk})
        response = self.client.patch(url, {"title": "Updated Title"}, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.lesson.refresh_from_db()
        self.assertEqual(self.lesson.title, "Updated Title")

    def test_unrelated_teacher_cannot_update_lesson(self):
        self.client.force_authenticate(user=self.teacher_other)
        url = reverse('lesson-detail', kwargs={'pk': self.lesson.pk})
        response = self.client.patch(url, {"title": "Hijacked"}, format='json')
        self.assertIn(response.status_code, (status.HTTP_403_FORBIDDEN, status.HTTP_404_NOT_FOUND))

    # ---- 24: subscription expiration never deletes Enrollment ----

    def test_subscription_expiration_does_not_delete_enrollment(self):
        Enrollment.objects.create(user=self.subscribed_student, course=self.other_course)
        sub = Subscription.objects.get(razorpay_subscription_id="sub_content_sec_1")
        sub.status = Subscription.Status.EXPIRED
        sub.current_period_end = self.past
        sub.save()
        self.assertTrue(Enrollment.objects.filter(user=self.subscribed_student, course=self.other_course).exists())


def _make_course_with_lessons(title, module_count, lessons_per_module):
    course = Course.objects.create(title=title, description="x", price=1, is_published=True)
    for m in range(module_count):
        module = Module.objects.create(course=course, title=f"Module {m}")
        for l in range(lessons_per_module):
            VideoLesson.objects.create(module=module, title=f"Lesson {m}-{l}", video_file="videos/lessons/x.mp4")
    return course


class ContentAccessQueryCountTests(TestCase):
    """
    Query-count regression: the content-lock decision (CourseViewSet.
    get_serializer_context()) must be computed ONCE per request, not once
    per module/lesson. The 3 queries below the small course's total for
    the large course are all pre-existing, unrelated-to-this-change
    nested-serializer fetches (Module/VideoLesson/TranslatedAudio queries
    -- CourseSerializer -> ModuleSerializer -> VideoLessonSerializer's
    lack of prefetch_related, unchanged by this security fix) -- what
    matters here is that the DELTA between a 1-lesson and a 4-lesson
    course, for the SAME authenticated non-owner, is exactly the
    pre-existing per-lesson fetch cost and nothing more -- i.e. the access
    check itself never runs per-lesson.
    """

    def setUp(self):
        self.student = User.objects.create_user(username="content_sec_query_student", password="password123")
        self.small_course = _make_course_with_lessons("Small Query Course", module_count=1, lessons_per_module=1)
        self.large_course = _make_course_with_lessons("Large Query Course", module_count=3, lessons_per_module=3)

    def test_anonymous_course_detail_query_count(self):
        # Anonymous requests short-circuit accessible_course_ids_for_user/
        # instructor_course_ids_for_user to set() with zero queries (see
        # their own "not user.is_authenticated" guard) -- so every query
        # here is the pre-existing nested-serializer fetch, none of it
        # from this security fix.
        from rest_framework.test import APIClient
        client = APIClient()
        url = reverse('course-detail', kwargs={'pk': self.large_course.pk})
        with self.assertNumQueries(14):
            response = client.get(url)
        self.assertEqual(response.status_code, 200)
        lessons = [l for m in response.data['modules'] for l in m['lessons']]
        self.assertEqual(len(lessons), 9)
        for lesson in lessons:
            self.assertTrue(lesson['is_locked'])

    def test_authenticated_non_owner_query_count_delta_matches_lesson_count_only(self):
        # Same non-owning, authenticated user against a 1-lesson course and
        # a 9-lesson course -- the fixed cost of computing access (course +
        # get_serializer_context()'s own queries) must be identical in
        # both; only the pre-existing per-module/per-lesson fetch queries
        # should differ.
        from rest_framework.test import APIClient
        client = APIClient()
        client.force_authenticate(user=self.student)

        # access context = 3 queries here: accessible_course_ids_for_user
        # short-circuits to 2 (enrolled ids + subscription-plan lookup,
        # skipping its own 3rd "resolve plan ids to courses" query since
        # this student has no matching subscription at all) +
        # instructor_course_ids_for_user's 1 (no is_teacher, so no 2nd
        # legacy-fallback query either).
        with self.assertNumQueries(1 + 1 + 1 + 1 + 3):  # course, modules, lessons, translated_audio, + access context
            small_response = client.get(reverse('course-detail', kwargs={'pk': self.small_course.pk}))
        self.assertEqual(small_response.status_code, 200)

        with self.assertNumQueries(1 + 1 + 3 + 9 + 3):  # same access-context cost, only fetch queries scale
            large_response = client.get(reverse('course-detail', kwargs={'pk': self.large_course.pk}))
        self.assertEqual(large_response.status_code, 200)
