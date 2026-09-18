"""
Phase 4.1: Assessment Engine Foundation -- model/relationship/constraint/
admin tests for Assessment, Question, QuestionOption. Deliberately does
NOT test a student-facing attempt/scoring flow -- those models don't
exist yet (a later phase). See courses/models.py's own Phase 4.1 section
docstring for the design rationale being tested here.
"""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse

from .models import Assessment, Course, Module, Question, QuestionOption, VideoLesson

User = get_user_model()


def _make_module(course_title="Assessment Course", module_title="Module 1"):
    course = Course.objects.create(title=course_title, price=100, is_published=True)
    return Module.objects.create(course=course, title=module_title, order=1)


def _make_assessment(module=None, **kwargs):
    module = module or _make_module()
    defaults = dict(title="Quiz 1", order=1)
    defaults.update(kwargs)
    return Assessment.objects.create(module=module, **defaults)


def _make_question(assessment=None, **kwargs):
    assessment = assessment or _make_assessment()
    defaults = dict(question_text="What is 2+2?", order=1)
    defaults.update(kwargs)
    return Question.objects.create(assessment=assessment, **defaults)


class AssessmentCreationTests(TestCase):
    def test_create_assessment_with_defaults(self):
        module = _make_module()
        assessment = Assessment.objects.create(module=module, title="Quiz 1", order=1)
        self.assertEqual(assessment.passing_percentage, Decimal("40.00"))
        self.assertEqual(assessment.max_attempts, 1)
        self.assertIsNone(assessment.time_limit_minutes)
        self.assertFalse(assessment.is_published)
        self.assertIsNotNone(assessment.created_at)
        self.assertIsNotNone(assessment.updated_at)

    def test_create_assessment_with_all_fields(self):
        module = _make_module()
        assessment = Assessment.objects.create(
            module=module, title="Final Exam", description="Covers all lessons",
            instructions="You have 30 minutes.", order=2, is_published=True,
            passing_percentage=Decimal("60.00"), max_attempts=3, time_limit_minutes=30,
        )
        assessment.refresh_from_db()
        self.assertEqual(assessment.title, "Final Exam")
        self.assertEqual(assessment.passing_percentage, Decimal("60.00"))
        self.assertEqual(assessment.max_attempts, 3)
        self.assertEqual(assessment.time_limit_minutes, 30)
        self.assertTrue(assessment.is_published)

    def test_str_representation(self):
        assessment = _make_assessment()
        self.assertIn(assessment.title, str(assessment))
        self.assertIn(assessment.module.title, str(assessment))


class AssessmentValidationTests(TestCase):
    def test_negative_passing_percentage_rejected(self):
        assessment = Assessment(module=_make_module(), title="Q", order=1, passing_percentage=Decimal("-1.00"))
        with self.assertRaises(ValidationError):
            assessment.full_clean()

    def test_passing_percentage_over_100_rejected(self):
        assessment = Assessment(module=_make_module(), title="Q", order=1, passing_percentage=Decimal("100.01"))
        with self.assertRaises(ValidationError):
            assessment.full_clean()

    def test_passing_percentage_of_exactly_0_and_100_allowed(self):
        module = _make_module()
        Assessment(module=module, title="Q0", order=1, passing_percentage=Decimal("0.00")).full_clean()
        Assessment(module=module, title="Q100", order=2, passing_percentage=Decimal("100.00")).full_clean()

    def test_max_attempts_of_zero_rejected(self):
        assessment = Assessment(module=_make_module(), title="Q", order=1, max_attempts=0)
        with self.assertRaises(ValidationError):
            assessment.full_clean()

    def test_negative_max_attempts_rejected(self):
        # PositiveIntegerField itself refuses negative values at the DB
        # level too, but full_clean should surface it as a ValidationError
        # before ever reaching the database.
        assessment = Assessment(module=_make_module(), title="Q", order=1, max_attempts=-1)
        with self.assertRaises(ValidationError):
            assessment.full_clean()

    def test_time_limit_of_zero_rejected(self):
        assessment = Assessment(module=_make_module(), title="Q", order=1, time_limit_minutes=0)
        with self.assertRaises(ValidationError):
            assessment.full_clean()

    def test_time_limit_none_is_valid(self):
        assessment = Assessment(module=_make_module(), title="Q", order=1, time_limit_minutes=None)
        assessment.full_clean()  # should not raise

    def test_time_limit_positive_is_valid(self):
        assessment = Assessment(module=_make_module(), title="Q", order=1, time_limit_minutes=45)
        assessment.full_clean()  # should not raise


class AssessmentModuleRelationshipTests(TestCase):
    def test_assessment_belongs_to_module(self):
        module = _make_module()
        assessment = _make_assessment(module=module)
        self.assertEqual(assessment.module, module)
        self.assertIn(assessment, module.assessments.all())

    def test_module_can_have_multiple_assessments(self):
        module = _make_module()
        a1 = _make_assessment(module=module, title="Quiz 1", order=1)
        a2 = _make_assessment(module=module, title="Quiz 2", order=2)
        self.assertEqual(list(module.assessments.order_by('order')), [a1, a2])

    def test_deleting_module_cascades_to_assessment(self):
        module = _make_module()
        assessment = _make_assessment(module=module)
        module.delete()
        self.assertFalse(Assessment.objects.filter(pk=assessment.pk).exists())

    def test_deleting_assessment_does_not_delete_module(self):
        module = _make_module()
        assessment = _make_assessment(module=module)
        assessment.delete()
        self.assertTrue(Module.objects.filter(pk=module.pk).exists())

    def test_existing_module_lesson_relationship_unaffected(self):
        """Sanity check: adding Assessment as a new Module child doesn't
        disturb the pre-existing Module -> VideoLesson relationship."""
        module = _make_module()
        lesson = VideoLesson.objects.create(module=module, title="L1", order=1)
        _make_assessment(module=module)
        self.assertIn(lesson, module.lessons.all())
        self.assertEqual(module.lessons.count(), 1)
        self.assertEqual(module.assessments.count(), 1)


class AssessmentOrderingConstraintTests(TestCase):
    def test_duplicate_order_within_same_module_rejected_at_db_level(self):
        module = _make_module()
        Assessment.objects.create(module=module, title="Quiz 1", order=1)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Assessment.objects.create(module=module, title="Quiz 2", order=1)

    def test_same_order_allowed_across_different_modules(self):
        module_a = _make_module(module_title="Module A")
        module_b = _make_module(course_title="Assessment Course", module_title="Module B")
        Assessment.objects.create(module=module_a, title="Quiz A", order=1)
        Assessment.objects.create(module=module_b, title="Quiz B", order=1)  # must not raise

    def test_assessments_ordered_deterministically(self):
        module = _make_module()
        a3 = Assessment.objects.create(module=module, title="Third", order=3)
        a1 = Assessment.objects.create(module=module, title="First", order=1)
        a2 = Assessment.objects.create(module=module, title="Second", order=2)
        self.assertEqual(list(Assessment.objects.filter(module=module)), [a1, a2, a3])


class AssessmentPublishedStateTests(TestCase):
    def test_default_unpublished(self):
        assessment = _make_assessment()
        self.assertFalse(assessment.is_published)

    def test_published_flag_filterable(self):
        module = _make_module()
        published = Assessment.objects.create(module=module, title="Published", order=1, is_published=True)
        draft = Assessment.objects.create(module=module, title="Draft", order=2, is_published=False)
        self.assertEqual(list(Assessment.objects.filter(is_published=True)), [published])
        self.assertEqual(list(Assessment.objects.filter(is_published=False)), [draft])


class QuestionCreationTests(TestCase):
    def test_create_question_with_defaults(self):
        assessment = _make_assessment()
        question = Question.objects.create(assessment=assessment, question_text="2+2=?", order=1)
        self.assertEqual(question.question_type, Question.QuestionType.SINGLE_CHOICE)
        self.assertEqual(question.marks, Decimal("1.00"))
        self.assertTrue(question.is_required)
        self.assertEqual(question.explanation, "")

    def test_supported_question_types(self):
        assessment = _make_assessment()
        q1 = Question.objects.create(assessment=assessment, question_text="Pick one", order=1, question_type=Question.QuestionType.SINGLE_CHOICE)
        q2 = Question.objects.create(assessment=assessment, question_text="Pick many", order=2, question_type=Question.QuestionType.MULTIPLE_CHOICE)
        self.assertEqual(q1.question_type, "SINGLE_CHOICE")
        self.assertEqual(q2.question_type, "MULTIPLE_CHOICE")

    def test_invalid_question_type_rejected(self):
        question = Question(assessment=_make_assessment(), question_text="Bad", order=1, question_type="ESSAY")
        with self.assertRaises(ValidationError):
            question.full_clean()


class QuestionValidationTests(TestCase):
    def test_zero_marks_rejected(self):
        question = Question(assessment=_make_assessment(), question_text="Q", order=1, marks=Decimal("0.00"))
        with self.assertRaises(ValidationError):
            question.full_clean()

    def test_negative_marks_rejected(self):
        question = Question(assessment=_make_assessment(), question_text="Q", order=1, marks=Decimal("-1.00"))
        with self.assertRaises(ValidationError):
            question.full_clean()

    def test_positive_decimal_marks_allowed(self):
        question = Question(assessment=_make_assessment(), question_text="Q", order=1, marks=Decimal("2.50"))
        question.full_clean()  # should not raise
        question.save()
        question.refresh_from_db()
        self.assertEqual(question.marks, Decimal("2.50"))


class QuestionAssessmentRelationshipTests(TestCase):
    def test_question_belongs_to_assessment(self):
        assessment = _make_assessment()
        question = _make_question(assessment=assessment)
        self.assertEqual(question.assessment, assessment)
        self.assertIn(question, assessment.questions.all())

    def test_deleting_assessment_cascades_to_questions(self):
        assessment = _make_assessment()
        question = _make_question(assessment=assessment)
        assessment.delete()
        self.assertFalse(Question.objects.filter(pk=question.pk).exists())

    def test_duplicate_order_within_same_assessment_rejected(self):
        assessment = _make_assessment()
        Question.objects.create(assessment=assessment, question_text="Q1", order=1)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Question.objects.create(assessment=assessment, question_text="Q2", order=1)

    def test_same_order_allowed_across_different_assessments(self):
        module = _make_module()
        a1 = Assessment.objects.create(module=module, title="A1", order=1)
        a2 = Assessment.objects.create(module=module, title="A2", order=2)
        Question.objects.create(assessment=a1, question_text="Q", order=1)
        Question.objects.create(assessment=a2, question_text="Q", order=1)  # must not raise


class QuestionOptionCreationTests(TestCase):
    def test_create_option(self):
        question = _make_question()
        option = QuestionOption.objects.create(question=question, option_text="4", order=1, is_correct=True)
        self.assertTrue(option.is_correct)
        self.assertEqual(option.option_text, "4")

    def test_default_is_correct_false(self):
        option = QuestionOption.objects.create(question=_make_question(), option_text="3", order=1)
        self.assertFalse(option.is_correct)


class QuestionOptionRelationshipTests(TestCase):
    def test_option_belongs_to_question(self):
        question = _make_question()
        option = QuestionOption.objects.create(question=question, option_text="4", order=1)
        self.assertEqual(option.question, question)
        self.assertIn(option, question.options.all())

    def test_deleting_question_cascades_to_options(self):
        question = _make_question()
        option = QuestionOption.objects.create(question=question, option_text="4", order=1)
        question.delete()
        self.assertFalse(QuestionOption.objects.filter(pk=option.pk).exists())

    def test_duplicate_order_within_same_question_rejected(self):
        question = _make_question()
        QuestionOption.objects.create(question=question, option_text="3", order=1)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                QuestionOption.objects.create(question=question, option_text="4", order=1)

    def test_same_order_allowed_across_different_questions(self):
        assessment = _make_assessment()
        q1 = Question.objects.create(assessment=assessment, question_text="Q1", order=1)
        q2 = Question.objects.create(assessment=assessment, question_text="Q2", order=2)
        QuestionOption.objects.create(question=q1, option_text="A", order=1)
        QuestionOption.objects.create(question=q2, option_text="A", order=1)  # must not raise

    def test_options_ordered_deterministically(self):
        question = _make_question()
        o3 = QuestionOption.objects.create(question=question, option_text="Third", order=3)
        o1 = QuestionOption.objects.create(question=question, option_text="First", order=1)
        o2 = QuestionOption.objects.create(question=question, option_text="Second", order=2)
        self.assertEqual(list(QuestionOption.objects.filter(question=question)), [o1, o2, o3])


class FullHierarchyDeletionTests(TestCase):
    def test_deleting_course_cascades_through_entire_assessment_tree(self):
        module = _make_module()
        course = module.course
        assessment = _make_assessment(module=module)
        question = _make_question(assessment=assessment)
        option = QuestionOption.objects.create(question=question, option_text="4", order=1)

        course.delete()

        self.assertFalse(Assessment.objects.filter(pk=assessment.pk).exists())
        self.assertFalse(Question.objects.filter(pk=question.pk).exists())
        self.assertFalse(QuestionOption.objects.filter(pk=option.pk).exists())


class ExistingCourseFunctionalityUnaffectedTests(TestCase):
    """Confirms Phase 4.1 is purely additive -- pre-existing course/module/
    lesson/progress behavior is untouched."""

    def test_course_and_module_creation_still_works(self):
        course = Course.objects.create(title="Untouched Course", price=50, is_published=True)
        module = Module.objects.create(course=course, title="M1", order=1)
        lesson = VideoLesson.objects.create(module=module, title="L1", order=1)
        self.assertEqual(module.course, course)
        self.assertEqual(lesson.module, module)

    def test_course_serializer_progress_percentage_unaffected(self):
        from rest_framework.test import APIClient
        student = User.objects.create_user(username='assess_phase41_student', password='pw')
        course = Course.objects.create(title="Progress Course", price=10, is_published=True)
        module = Module.objects.create(course=course, title="M1", order=1)
        VideoLesson.objects.create(module=module, title="L1", order=1)
        from .models import Enrollment
        Enrollment.objects.create(user=student, course=course)

        api = APIClient()
        api.force_authenticate(student)
        response = api.get(reverse('course-my-courses'))
        self.assertEqual(response.status_code, 200)
        data = next(c for c in response.data if c['id'] == course.id)
        self.assertEqual(data['progress_percentage'], 0)
        # Assessment fields must not leak into the existing course payload.
        self.assertNotIn('assessments', data)


class AssessmentAdminAccessTests(TestCase):
    """Correct-answer fields (QuestionOption.is_correct) must stay
    admin-only, and only staff/superusers can reach the admin at all."""

    def setUp(self):
        self.superuser = User.objects.create_superuser(username='assess_admin', password='pw')
        self.student = User.objects.create_user(username='assess_student_admin_test', password='pw')

    def test_anonymous_cannot_access_assessment_admin(self):
        response = self.client.get(reverse('admin:courses_assessment_changelist'))
        self.assertEqual(response.status_code, 302)  # redirected to login

    def test_non_staff_cannot_access_assessment_admin(self):
        self.client.force_login(self.student)
        response = self.client.get(reverse('admin:courses_assessment_changelist'))
        self.assertEqual(response.status_code, 302)  # redirected, not staff

    def test_superuser_can_access_assessment_admin(self):
        self.client.force_login(self.superuser)
        response = self.client.get(reverse('admin:courses_assessment_changelist'))
        self.assertEqual(response.status_code, 200)

    def test_superuser_can_access_question_admin_with_option_inline(self):
        self.client.force_login(self.superuser)
        question = _make_question()
        response = self.client.get(reverse('admin:courses_question_change', args=[question.pk]))
        self.assertEqual(response.status_code, 200)
        # The inline option formset's is_correct checkbox must be present
        # on the page -- i.e. reachable only through this admin-only view.
        self.assertContains(response, 'is_correct')


class QuestionOptionAdminFormsetValidationTests(TestCase):
    """Exercises QuestionOptionInlineFormSet's cross-row validation via a
    real admin POST -- the only place that currently mutates these rows."""

    def setUp(self):
        self.superuser = User.objects.create_superuser(username='assess_formset_admin', password='pw')
        self.client.force_login(self.superuser)

    def _post_question_change(self, question, question_type, option_correct_flags):
        url = reverse('admin:courses_question_change', args=[question.pk])
        data = {
            'assessment': question.assessment_id,
            'question_text': question.question_text,
            'question_type': question_type,
            'order': question.order,
            'marks': '1.00',
            'explanation': '',
            'is_required': 'on',
            'options-TOTAL_FORMS': str(len(option_correct_flags)),
            'options-INITIAL_FORMS': '0',
            'options-MIN_NUM_FORMS': '0',
            'options-MAX_NUM_FORMS': '1000',
        }
        for i, is_correct in enumerate(option_correct_flags):
            data[f'options-{i}-option_text'] = f'Option {i}'
            data[f'options-{i}-order'] = str(i + 1)
            data[f'options-{i}-question'] = question.pk
            if is_correct:
                data[f'options-{i}-is_correct'] = 'on'
        return self.client.post(url, data)

    def test_single_choice_with_exactly_one_correct_option_saves(self):
        question = _make_question(question_type=Question.QuestionType.SINGLE_CHOICE)
        response = self._post_question_change(question, 'SINGLE_CHOICE', [True, False, False])
        self.assertEqual(response.status_code, 302)  # redirect on success
        self.assertEqual(QuestionOption.objects.filter(question=question, is_correct=True).count(), 1)

    def test_single_choice_with_zero_correct_options_rejected(self):
        question = _make_question(question_type=Question.QuestionType.SINGLE_CHOICE)
        response = self._post_question_change(question, 'SINGLE_CHOICE', [False, False, False])
        self.assertEqual(response.status_code, 200)  # re-rendered with error, not redirected
        self.assertContains(response, "exactly one correct option")
        self.assertEqual(QuestionOption.objects.filter(question=question).count(), 0)

    def test_single_choice_with_two_correct_options_rejected(self):
        question = _make_question(question_type=Question.QuestionType.SINGLE_CHOICE)
        response = self._post_question_change(question, 'SINGLE_CHOICE', [True, True, False])
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "exactly one correct option")

    def test_multiple_choice_with_one_correct_option_saves(self):
        question = _make_question(question_type=Question.QuestionType.MULTIPLE_CHOICE)
        response = self._post_question_change(question, 'MULTIPLE_CHOICE', [True, False, False])
        self.assertEqual(response.status_code, 302)

    def test_multiple_choice_with_multiple_correct_options_saves(self):
        question = _make_question(question_type=Question.QuestionType.MULTIPLE_CHOICE)
        response = self._post_question_change(question, 'MULTIPLE_CHOICE', [True, True, False])
        self.assertEqual(response.status_code, 302)
        self.assertEqual(QuestionOption.objects.filter(question=question, is_correct=True).count(), 2)

    def test_multiple_choice_with_zero_correct_options_rejected(self):
        question = _make_question(question_type=Question.QuestionType.MULTIPLE_CHOICE)
        response = self._post_question_change(question, 'MULTIPLE_CHOICE', [False, False, False])
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "at least one correct option")
