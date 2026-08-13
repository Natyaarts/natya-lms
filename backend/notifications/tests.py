from django.test import TestCase
from django.contrib.auth import get_user_model
from django.utils import timezone
from django.urls import reverse
from rest_framework.test import APITestCase
from rest_framework import status
from datetime import timedelta
from courses.models import Course, Enrollment, Module, VideoLesson, LessonProgress
from orders.models import Purchase
from .models import Notification, Announcement

User = get_user_model()

class NotificationModelTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="testuser", password="password123")
        self.user2 = User.objects.create_user(username="anotheruser", password="password123")
        self.course = Course.objects.create(title="Test Course", price=10.00, is_published=True)

    def test_notification_creation_and_attributes(self):
        # 1. Notification can be created.
        notification = Notification.objects.create(
            recipient=self.user,
            title="Welcome",
            body="Welcome to the LMS!",
            notification_type="ANNOUNCEMENT",
            action_url="http://example.com"
        )
        self.assertEqual(notification.title, "Welcome")
        self.assertEqual(notification.body, "Welcome to the LMS!")
        self.assertEqual(notification.notification_type, "ANNOUNCEMENT")
        self.assertEqual(notification.action_url, "http://example.com")

        # 2. Notification defaults to unread.
        self.assertFalse(notification.is_read)

        # 3. read_at can be null.
        self.assertIsNone(notification.read_at)

        # 4. Notification belongs to the correct user.
        self.assertEqual(notification.recipient, self.user)
        self.assertIn(notification, self.user.notifications.all())
        self.assertNotIn(notification, self.user2.notifications.all())

    def test_global_and_course_specific_announcements(self):
        # 5. Global Announcement can have course=NULL.
        global_announcement = Announcement.objects.create(
            sender=self.user,
            course=None,
            title="Global News",
            content="This is site-wide.",
            is_published=True
        )
        self.assertEqual(global_announcement.title, "Global News")
        self.assertIsNone(global_announcement.course)
        self.assertEqual(global_announcement.sender, self.user)

        # 6. Course-specific Announcement can reference a Course.
        course_announcement = Announcement.objects.create(
            sender=self.user,
            course=self.course,
            title="Course Update",
            content="New lesson added.",
            is_published=True
        )
        self.assertEqual(course_announcement.title, "Course Update")
        self.assertEqual(course_announcement.course, self.course)
        self.assertIn(course_announcement, self.course.announcements.all())

    def test_notification_ordering(self):
        # 7. Notification ordering is newest first.
        n1 = Notification.objects.create(recipient=self.user, title="First Notification", body="N1")
        n2 = Notification.objects.create(recipient=self.user, title="Second Notification", body="N2")
        n3 = Notification.objects.create(recipient=self.user, title="Third Notification", body="N3")

        # Guarantee different created_at timestamps via update
        now = timezone.now()
        Notification.objects.filter(pk=n1.pk).update(created_at=now - timedelta(days=2))
        Notification.objects.filter(pk=n2.pk).update(created_at=now - timedelta(days=1))
        Notification.objects.filter(pk=n3.pk).update(created_at=now)

        # Refresh from database because ordering in Meta applies to DB query
        notifications = list(Notification.objects.filter(recipient=self.user))
        self.assertEqual(notifications[0].title, "Third Notification")
        self.assertEqual(notifications[1].title, "Second Notification")
        self.assertEqual(notifications[2].title, "First Notification")

    def test_announcement_ordering(self):
        # 8. Announcement ordering is newest first.
        a1 = Announcement.objects.create(title="First Announcement", content="A1")
        a2 = Announcement.objects.create(title="Second Announcement", content="A2")
        a3 = Announcement.objects.create(title="Third Announcement", content="A3")

        # Guarantee different created_at timestamps via update
        now = timezone.now()
        Announcement.objects.filter(pk=a1.pk).update(created_at=now - timedelta(days=2))
        Announcement.objects.filter(pk=a2.pk).update(created_at=now - timedelta(days=1))
        Announcement.objects.filter(pk=a3.pk).update(created_at=now)

        # Refresh from database because ordering in Meta applies to DB query
        announcements = list(Announcement.objects.all())
        self.assertEqual(announcements[0].title, "Third Announcement")
        self.assertEqual(announcements[1].title, "Second Announcement")
        self.assertEqual(announcements[2].title, "First Announcement")


class NotificationAPITests(APITestCase):
    def setUp(self):
        # Users
        self.student = User.objects.create_user(username="student", password="password123")
        self.other_student = User.objects.create_user(username="other_student", password="password123")
        self.staff_user = User.objects.create_user(username="staff", password="password123", is_staff=True)

        # Courses
        self.enrolled_course = Course.objects.create(title="Enrolled Course", price=10.00, is_published=True)
        self.unenrolled_course = Course.objects.create(title="Unenrolled Course", price=20.00, is_published=True)

        # Enroll student
        self.enrollment = Enrollment.objects.create(user=self.student, course=self.enrolled_course)

        # Pre-populate Notifications for student
        self.notification_unread = Notification.objects.create(
            recipient=self.student,
            title="Unread Notif",
            body="Notif body 1",
            is_read=False
        )
        self.notification_read = Notification.objects.create(
            recipient=self.student,
            title="Read Notif",
            body="Notif body 2",
            is_read=True,
            read_at=timezone.now() - timedelta(hours=1)
        )

        # Pre-populate Notification for other_student
        self.other_notification = Notification.objects.create(
            recipient=self.other_student,
            title="Other Student Notif",
            body="Notif body 3",
            is_read=False
        )

    # ==========================================
    # NOTIFICATIONS TESTS
    # ==========================================

    def test_authenticated_user_can_list_own_notifications(self):
        # 1. Authenticated user can list own notifications.
        self.client.force_authenticate(user=self.student)
        url = reverse('notification-list')
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # Should return both unread and read notification of student
        self.assertEqual(len(response.data), 2)
        titles = [n['title'] for n in response.data]
        self.assertIn("Unread Notif", titles)
        self.assertIn("Read Notif", titles)

    def test_unauthenticated_user_cannot_list_notifications(self):
        # 2. Unauthenticated user cannot list notifications.
        url = reverse('notification-list')
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_user_cannot_see_another_users_notifications(self):
        # 3. User cannot see another user's notifications.
        self.client.force_authenticate(user=self.student)
        # Attempt to retrieve other student's notification via detail view
        url = reverse('notification-detail', kwargs={'pk': self.other_notification.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

        # Attempt to list (other student's notification must not be in list)
        list_url = reverse('notification-list')
        list_response = self.client.get(list_url)
        ids = [n['id'] for n in list_response.data]
        self.assertNotIn(self.other_notification.pk, ids)

    def test_unread_filtering_works(self):
        # 4. Unread filtering works.
        self.client.force_authenticate(user=self.student)
        url = reverse('notification-list')

        # Filter ?is_read=false
        response_unread = self.client.get(url, {'is_read': 'false'})
        self.assertEqual(response_unread.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response_unread.data), 1)
        self.assertEqual(response_unread.data[0]['title'], "Unread Notif")

        # Filter ?is_read=true
        response_read = self.client.get(url, {'is_read': 'true'})
        self.assertEqual(response_read.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response_read.data), 1)
        self.assertEqual(response_read.data[0]['title'], "Read Notif")

    def test_user_can_mark_own_notification_as_read(self):
        # 5. User can mark own notification as read.
        self.client.force_authenticate(user=self.student)
        url = reverse('notification-mark-as-read', kwargs={'pk': self.notification_unread.pk})
        response = self.client.post(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data['is_read'])
        self.assertIsNotNone(response.data['read_at'])

        # Verify DB is updated
        self.notification_unread.refresh_from_db()
        self.assertTrue(self.notification_unread.is_read)

    def test_user_cannot_mark_another_users_notification_as_read(self):
        # 6. User cannot mark another user's notification as read.
        self.client.force_authenticate(user=self.student)
        url = reverse('notification-mark-as-read', kwargs={'pk': self.other_notification.pk})
        response = self.client.post(url)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_read_at_is_populated_when_marking_as_read(self):
        # 7. read_at is populated when marking as read.
        self.client.force_authenticate(user=self.student)
        url = reverse('notification-mark-as-read', kwargs={'pk': self.notification_unread.pk})
        response = self.client.post(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsNotNone(response.data['read_at'])

    def test_already_read_notification_remains_safely_readable(self):
        # 8. Already-read notification remains safely readable.
        self.client.force_authenticate(user=self.student)
        original_read_at = self.notification_read.read_at
        url = reverse('notification-mark-as-read', kwargs={'pk': self.notification_read.pk})
        response = self.client.post(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.notification_read.refresh_from_db()
        self.assertEqual(self.notification_read.read_at, original_read_at)

    def test_mark_all_read_only_affects_authenticated_users_notifications(self):
        # 9. Mark-all-read only affects the authenticated user's notifications.
        self.client.force_authenticate(user=self.student)
        url = reverse('notification-mark-all-as-read')
        response = self.client.post(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['updated'], 1)  # Only 1 unread notification of student

        self.notification_unread.refresh_from_db()
        self.assertTrue(self.notification_unread.is_read)

        # Other student's notification remains unread
        self.other_notification.refresh_from_db()
        self.assertFalse(self.other_notification.is_read)

    def test_unread_count_is_correct_and_user_specific(self):
        # 10. Unread count is correct and user-specific.
        self.client.force_authenticate(user=self.student)
        url = reverse('notification-unread-count')
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['count'], 1)

        # Check other student unread count
        self.client.force_authenticate(user=self.other_student)
        response_other = self.client.get(url)
        self.assertEqual(response_other.data['count'], 1)

    # ==========================================
    # ANNOUNCEMENTS TESTS
    # ==========================================

    def test_authenticated_user_can_view_published_global_announcements(self):
        # 11. Authenticated user can view published global announcements.
        Announcement.objects.create(
            sender=self.staff_user,
            course=None,
            title="Global Published",
            content="Hello world",
            is_published=True
        )
        self.client.force_authenticate(user=self.student)
        url = reverse('announcement-list')
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['title'], "Global Published")

    def test_user_cannot_see_unpublished_announcements(self):
        # 12. User cannot see unpublished announcements.
        Announcement.objects.create(
            sender=self.staff_user,
            course=None,
            title="Global Unpublished",
            content="Secret info",
            is_published=False
        )
        self.client.force_authenticate(user=self.student)
        url = reverse('announcement-list')
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 0)

    def test_user_can_see_published_announcements_for_courses_they_are_enrolled_in(self):
        # 13. User can see published announcements for courses they are enrolled in.
        Announcement.objects.create(
            sender=self.staff_user,
            course=self.enrolled_course,
            title="Enrolled Course Notif",
            content="Enrolled course is starting",
            is_published=True
        )
        self.client.force_authenticate(user=self.student)
        url = reverse('announcement-list')
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['title'], "Enrolled Course Notif")

    def test_user_cannot_see_announcements_for_unrelated_courses(self):
        # 14. User cannot see announcements for unrelated courses.
        Announcement.objects.create(
            sender=self.staff_user,
            course=self.unenrolled_course,
            title="Unenrolled Course Notif",
            content="Unrelated course is starting",
            is_published=True
        )
        self.client.force_authenticate(user=self.student)
        url = reverse('announcement-list')
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 0)

    def test_staff_admin_can_create_announcements(self):
        # 15. Staff/admin can create announcements.
        self.client.force_authenticate(user=self.staff_user)
        url = reverse('announcement-list')
        payload = {
            "title": "Staff Announcement",
            "content": "Staff body text",
            "course": self.enrolled_course.id,
            "is_published": True
        }
        response = self.client.post(url, payload)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['title'], "Staff Announcement")
        self.assertEqual(response.data['sender'], self.staff_user.id)

    def test_student_cannot_create_announcements(self):
        # 16. Student cannot create announcements.
        self.client.force_authenticate(user=self.student)
        url = reverse('announcement-list')
        payload = {
            "title": "Student Announcement Attempt",
            "content": "Fail text",
            "is_published": True
        }
        response = self.client.post(url, payload)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_sender_is_automatically_assigned_to_authenticated_staff_user(self):
        # 17. Sender is automatically assigned to the authenticated staff user (client cannot spoof).
        self.client.force_authenticate(user=self.staff_user)
        url = reverse('announcement-list')
        payload = {
            "title": "Staff Spoof Attempt",
            "content": "Check sender",
            "sender": self.student.id,  # Client tries to set sender to student
            "is_published": True
        }
        response = self.client.post(url, payload)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        # Sender must be staff_user.id, not student.id
        self.assertEqual(response.data['sender'], self.staff_user.id)

    def test_staff_admin_can_update_announcements(self):
        # 18. Staff/admin can update announcements.
        ann = Announcement.objects.create(
            sender=self.staff_user,
            title="Initial Title",
            content="Initial Content"
        )
        self.client.force_authenticate(user=self.staff_user)
        url = reverse('announcement-detail', kwargs={'pk': ann.pk})
        payload = {
            "title": "Updated Title"
        }
        response = self.client.patch(url, payload)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['title'], "Updated Title")

    def test_staff_admin_can_delete_announcements(self):
        # 19. Staff/admin can delete announcements.
        ann = Announcement.objects.create(
            sender=self.staff_user,
            title="Delete Me",
            content="Content"
        )
        self.client.force_authenticate(user=self.staff_user)
        url = reverse('announcement-detail', kwargs={'pk': ann.pk})
        response = self.client.delete(url)
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Announcement.objects.filter(pk=ann.pk).exists())

    # ==========================================
    # LMS SANITY TESTS
    # ==========================================

    def test_lms_functionality_remains_unaffected(self):
        # Verify that Course, Module, VideoLesson, Enrollment, Purchase and LessonProgress query/creation works fine.
        module = Module.objects.create(course=self.enrolled_course, title="Module 1", order=1)
        lesson = VideoLesson.objects.create(module=module, title="Lesson 1", order=1)
        progress = LessonProgress.objects.create(user=self.student, lesson=lesson, last_watched_position=1.0)
        purchase = Purchase.objects.create(user=self.student, course=self.enrolled_course, amount=10.00, status="SUCCESS")

        self.assertEqual(progress.lesson, lesson)
        self.assertEqual(purchase.status, "SUCCESS")
        self.assertEqual(self.enrollment.course, self.enrolled_course)
