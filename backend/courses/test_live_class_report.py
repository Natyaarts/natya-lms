from django.test import TestCase
from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework import status
from courses.models import Course, LiveBatch, LiveClass, LiveBatchStudent, LiveClassReport

User = get_user_model()


class LiveClassReportWorkflowTestCase(TestCase):
    def setUp(self):
        self.client = APIClient()

        # Admin
        self.admin = User.objects.create_superuser(
            username='admin_user',
            email='admin@example.com',
            password='Password123!'
        )

        # Teacher
        self.teacher = User.objects.create_user(
            username='teacher_sita',
            email='teacher@example.com',
            password='Password123!',
            is_teacher=True
        )

        # Another teacher (not assigned)
        self.other_teacher = User.objects.create_user(
            username='teacher_radha',
            email='other_teacher@example.com',
            password='Password123!',
            is_teacher=True
        )

        # Mentor assigned to batch
        self.mentor = User.objects.create_user(
            username='mentor_anand',
            email='mentor@example.com',
            password='Password123!',
            is_mentor=True
        )

        # Another mentor (not assigned to this batch)
        self.other_mentor = User.objects.create_user(
            username='mentor_krishna',
            email='other_mentor@example.com',
            password='Password123!',
            is_mentor=True
        )

        # Student in batch
        self.student = User.objects.create_user(
            username='student_arjun',
            email='student@example.com',
            password='Password123!'
        )

        # Student not in batch
        self.other_student = User.objects.create_user(
            username='student_bhim',
            email='other_student@example.com',
            password='Password123!'
        )

        # Course and Batch
        self.course = Course.objects.create(
            title='Bharatanatyam Masterclass',
            description='Live Bharatanatyam training',
            course_type=Course.CourseType.LIVE
        )

        self.batch = LiveBatch.objects.create(
            course=self.course,
            instructor=self.teacher,
            mentor=self.mentor,
            batch_type=LiveBatch.BatchType.GROUP,
            max_participants=20
        )

        # Enroll student in batch
        LiveBatchStudent.objects.create(
            batch=self.batch,
            student=self.student
        )

        # Create Live Class
        self.live_class = LiveClass.objects.create(
            batch=self.batch,
            course=self.course,
            instructor=self.teacher,
            title='Session 1: Alarippu Foundations',
            meeting_url='https://zoom.us/j/1234567890',
            scheduled_start=timezone.now() - timezone.timedelta(hours=2),
            duration_minutes=60,
            status=LiveClass.ClassStatus.COMPLETED
        )

    def test_teacher_submits_report_and_visibility_workflow(self):
        # 1. Other teacher cannot submit report
        self.client.force_authenticate(user=self.other_teacher)
        res_fail = self.client.post(f'/api/courses/live-classes/{self.live_class.id}/report/', {
            'topics_covered': 'Intruder report',
            'student_engagement': 'GOOD'
        }, format='json')
        self.assertIn(res_fail.status_code, [status.HTTP_403_FORBIDDEN, status.HTTP_404_NOT_FOUND])

        # 2. Assigned teacher submits report
        self.client.force_authenticate(user=self.teacher)
        res_post = self.client.post(f'/api/courses/live-classes/{self.live_class.id}/report/', {
            'topics_covered': 'Basic Aramandi posture and Tattu Adavu steps 1-4.',
            'student_engagement': 'EXCELLENT',
            'engagement_notes': 'Arjun was very attentive and asked questions about rhythm timing.',
            'homework_instructions': 'Practice Tattu Adavu 3 speeds daily for 20 minutes.',
            'areas_of_improvement': 'Keep knees pressed outward during Aramandi posture.'
        }, format='json')
        self.assertEqual(res_post.status_code, status.HTTP_201_CREATED)
        self.assertFalse(res_post.data['is_visible_to_students'])
        self.assertEqual(res_post.data['student_engagement'], 'EXCELLENT')

        # 3. Student attempts to view report before mentor release -> 403 Forbidden
        self.client.force_authenticate(user=self.student)
        res_student_get = self.client.get(f'/api/courses/live-classes/{self.live_class.id}/report/')
        self.assertEqual(res_student_get.status_code, status.HTTP_403_FORBIDDEN)

        # Student checks live class detail -> report must be null
        res_class_detail = self.client.get(f'/api/courses/live-classes/{self.live_class.id}/')
        self.assertEqual(res_class_detail.status_code, status.HTTP_200_OK)
        self.assertIsNone(res_class_detail.data['report'])
        self.assertFalse(res_class_detail.data['report_visible_to_students'])

        # 4. Teacher attempts to toggle visibility -> 403 Forbidden (Only mentor/admin can toggle)
        self.client.force_authenticate(user=self.teacher)
        res_teacher_toggle = self.client.post(f'/api/courses/live-classes/{self.live_class.id}/toggle-report-visibility/', {
            'is_visible_to_students': True
        }, format='json')
        self.assertEqual(res_teacher_toggle.status_code, status.HTTP_403_FORBIDDEN)

        # 5. Non-assigned mentor attempts to toggle visibility -> 403/404
        self.client.force_authenticate(user=self.other_mentor)
        res_other_mentor_toggle = self.client.post(f'/api/courses/live-classes/{self.live_class.id}/toggle-report-visibility/', {
            'is_visible_to_students': True
        }, format='json')
        self.assertIn(res_other_mentor_toggle.status_code, [status.HTTP_403_FORBIDDEN, status.HTTP_404_NOT_FOUND])

        # 6. Assigned mentor views report -> 200 OK
        self.client.force_authenticate(user=self.mentor)
        res_mentor_get = self.client.get(f'/api/courses/live-classes/{self.live_class.id}/report/')
        self.assertEqual(res_mentor_get.status_code, status.HTTP_200_OK)
        self.assertEqual(res_mentor_get.data['topics_covered'], 'Basic Aramandi posture and Tattu Adavu steps 1-4.')

        # 7. Assigned mentor toggles visibility to students with mentor feedback
        res_mentor_toggle = self.client.post(f'/api/courses/live-classes/{self.live_class.id}/toggle-report-visibility/', {
            'is_visible_to_students': True,
            'mentor_feedback': 'Great progress noted. Approved to share with batch.'
        }, format='json')
        self.assertEqual(res_mentor_toggle.status_code, status.HTTP_200_OK)
        self.assertTrue(res_mentor_toggle.data['is_visible_to_students'])
        self.assertEqual(res_mentor_toggle.data['mentor_feedback'], 'Great progress noted. Approved to share with batch.')

        # 8. Student now views report -> 200 OK with full details
        self.client.force_authenticate(user=self.student)
        res_student_get_after = self.client.get(f'/api/courses/live-classes/{self.live_class.id}/report/')
        self.assertEqual(res_student_get_after.status_code, status.HTTP_200_OK)
        self.assertEqual(res_student_get_after.data['topics_covered'], 'Basic Aramandi posture and Tattu Adavu steps 1-4.')
        self.assertEqual(res_student_get_after.data['mentor_feedback'], 'Great progress noted. Approved to share with batch.')
        self.assertEqual(res_student_get_after.data['homework_instructions'], 'Practice Tattu Adavu 3 speeds daily for 20 minutes.')

        # Student checks class detail -> report is now populated
        res_class_detail_after = self.client.get(f'/api/courses/live-classes/{self.live_class.id}/')
        self.assertEqual(res_class_detail_after.status_code, status.HTTP_200_OK)
        self.assertIsNotNone(res_class_detail_after.data['report'])
        self.assertTrue(res_class_detail_after.data['report_visible_to_students'])

        # 9. Other student (not in batch) still cannot view report -> 403/404
        self.client.force_authenticate(user=self.other_student)
        res_other_student = self.client.get(f'/api/courses/live-classes/{self.live_class.id}/report/')
        self.assertIn(res_other_student.status_code, [status.HTTP_403_FORBIDDEN, status.HTTP_404_NOT_FOUND])
