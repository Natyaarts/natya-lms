from rest_framework import permissions

from courses.models import CourseInstructor, Enrollment, LiveBatch, LiveBatchStudent
from users.models import Mentorship


class IsConversationParticipant(permissions.BasePermission):
    """
    Ensures the requesting user is an active participant in the conversation
    (or a SuperAdmin/Staff member with administrative oversight).
    """

    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated)

    def has_object_permission(self, request, view, obj):
        if not (request.user and request.user.is_authenticated):
            return False

        # Superusers and staff have administrative moderation access
        if request.user.is_superuser or request.user.is_staff:
            return True

        # Check if obj is a Conversation
        if hasattr(obj, 'participants'):
            return obj.participants.filter(user=request.user).exists()

        # Check if obj is a Message
        if hasattr(obj, 'conversation'):
            return obj.conversation.participants.filter(user=request.user).exists()

        return False


def can_initiate_direct_conversation(sender, recipient):
    """
    Validates whether sender is permitted to initiate a 1-on-1 direct conversation with recipient:
    1. SuperAdmin / Staff can message anyone.
    2. Any user can message Staff / SuperAdmin (support).
    3. Student <-> Mentor: Permitted if an ACTIVE Mentorship exists between them.
    4. Student <-> Teacher: Permitted if the student is actively enrolled in a course taught by the teacher.
    5. Teacher/Mentor <-> Student: Permitted if the teacher teaches a course the student is enrolled in or is their mentor.
    6. Random Student <-> Student: Disallowed by policy to protect student safety.
    """
    if sender.id == recipient.id:
        return False, "Cannot create a conversation with yourself."

    # Administrative bypass
    if sender.is_superuser or sender.is_staff or recipient.is_superuser or recipient.is_staff:
        return True, None

    # Check Active Mentorship (either direction)
    is_mentorship_active = (
        Mentorship.objects.filter(student=sender, mentor=recipient, status=Mentorship.Status.ACTIVE).exists()
        or Mentorship.objects.filter(student=recipient, mentor=sender, status=Mentorship.Status.ACTIVE).exists()
    )

    if is_mentorship_active:
        return True, None

    # Check Student <-> Teacher Course Relationship
    # If sender is student and recipient is teacher/mentor
    if getattr(sender, 'is_student', False) and (getattr(recipient, 'is_teacher', False) or getattr(recipient, 'is_mentor', False)):
        has_shared_course = Enrollment.objects.filter(
            user=sender,
            course__instructors__user=recipient
        ).exists()
        if has_shared_course:
            return True, None

    # If sender is teacher/mentor and recipient is student
    if (getattr(sender, 'is_teacher', False) or getattr(sender, 'is_mentor', False)) and getattr(recipient, 'is_student', False):
        has_shared_course = Enrollment.objects.filter(
            user=recipient,
            course__instructors__user=sender
        ).exists()
        if has_shared_course:
            return True, None

    # Student to student disallowed
    if getattr(sender, 'is_student', False) and getattr(recipient, 'is_student', False):
        return False, "Direct messaging between students is not permitted."

    return False, "You do not have permission to initiate a conversation with this user."
