from django.test import TestCase
from django.contrib.auth import get_user_model
from decimal import Decimal
from courses.models import Course, CourseInstructor
from users.models import Mentorship
from courses.mentor_migration_service import execute_mentor_migration

User = get_user_model()


class MentorMigrationTests(TestCase):
    def setUp(self):
        self.mentor_user = User.objects.create_user(
            username="mentor_test",
            email="sreelakshmiedathara555@gmail.com",
            first_name="Kalakshetra Sreelakshmi",
            phone_number="+916382992948",
            is_teacher=True,
            is_student=False
        )
        self.course = Course.objects.create(
            title="Aiswarya B Babu",
            description="Diploma Course",
            price=Decimal("1500.00"),
            is_published=True
        )

    def test_mentor_migration_dry_run(self):
        stats = execute_mentor_migration(
            file_path='natya_analytics.xlsx',
            default_password='Mentor123',
            is_commit=False
        )
        self.assertEqual(stats['mode'], 'dry_run')
        self.assertGreaterEqual(stats['mentors']['sheet_count'], 1)
        # Database unchanged in dry run
        self.mentor_user.refresh_from_db()
        self.assertFalse(self.mentor_user.is_mentor)

    def test_mentor_migration_commit(self):
        stats = execute_mentor_migration(
            file_path='natya_analytics.xlsx',
            default_password='Mentor123',
            is_commit=True
        )
        self.assertEqual(stats['mode'], 'commit')
        self.mentor_user.refresh_from_db()
        # Verify mentor flag
        self.assertTrue(self.mentor_user.is_mentor)
        # Verify password check
        self.assertTrue(self.mentor_user.check_password('Mentor123'))
        # Verify CourseInstructor role='MENTOR' created
        mentor_ci = CourseInstructor.objects.filter(user=self.mentor_user, role=CourseInstructor.InstructorRole.MENTOR)
        self.assertTrue(mentor_ci.exists())
