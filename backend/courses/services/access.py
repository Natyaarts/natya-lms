"""
Phase 3.4.4 -- the single source of truth for "does this user have access to
this course's recorded content." Before this existed, every call site
(courses/views.py's `progress` action, CourseViewSet.my_courses, and
several others across orders/users/notifications -- see the repo-wide grep
done before writing this file) did its own raw `Enrollment.objects.filter(
user=user, course=course).exists()` inline, with no shared abstraction and
no way for a second, additive access grant (a Subscription) to plug in
without touching every call site individually. This module is that shared
abstraction, mirroring orders/services.py's own "one place, not four"
precedent for fulfillment.

Access is ADDITIVE, never a replacement:

    permanent Enrollment (Purchase/Order-granted, or admin-assigned --
    never expires, never touched by anything in this module)
        OR
    a currently-valid Subscription whose plan explicitly includes the
    course (time-bound, re-evaluated on every read -- no Enrollment row is
    ever created or deleted for subscription-based access; see
    _valid_subscription_filter's docstring for exactly what "currently
    valid" means)

Deliberately NOT touched here: LiveClass/LiveBatch access (governed
entirely by its own existing assignment/batch/permission rules -- see the
course_type guard in user_has_course_access), course *visibility*
(CourseViewSet.get_queryset's existing is_published gate is untouched and
this module doesn't re-derive it), and anything about *granting* Enrollment
(that remains orders/services.py's job for Purchase/Order; subscriptions
never create or delete an Enrollment row, by design).
"""
from django.db.models import Q
from django.utils import timezone

from courses.models import Course, Enrollment
from orders.models import Subscription


def _valid_subscription_filter(now=None):
    """
    A Subscription queryset filter (Q object) matching "this subscription
    currently grants access" -- the one place that understands what
    "currently valid" means, reused by both the single-course and bulk
    access checks below so they can never silently disagree.

    EXPIRED is excluded unconditionally: Razorpay's own definition of this
    status is a subscription that never successfully activated in time, so
    no real paid period was ever established for it to grant. Every other
    status -- including CANCELLED and COMPLETED -- is evaluated purely on
    whether its paid access period has actually ended yet, per the
    approved business rule ("a cancelled subscription's access remains
    valid until the paid period ends", not until the status itself
    changes) -- CANCELLED/COMPLETED are correctly NOT treated as an
    immediate hard cutoff here, only as "this subscription will not renew
    further", which is a different thing from "access ended right now".

    access_until (when set) is authoritative over current_period_end: it
    is the field a later grace-period phase is designed to populate (see
    its own field docstring on the Subscription model). No code as of this
    phase ever writes to it -- confirmed via a fresh repo-wide search
    before writing this -- so in practice this always falls back to
    current_period_end, which the Phase 3.4.3.1/3.4.3.2 webhook work keeps
    synchronized with Razorpay's own reported billing period. Checking
    access_until first, with no extra code, is what makes this function
    correct on day one of a future grace-period phase without needing to
    change this module at all.

    A subscription with neither field set (CREATED/AUTHENTICATED -- no
    successful charge has ever landed yet) naturally fails both halves of
    the OR and grants nothing, with no separate case needed for it.
    """
    now = now or timezone.now()
    return (
        ~Q(status=Subscription.Status.EXPIRED)
        & (Q(access_until__gt=now) | (Q(access_until__isnull=True) & Q(current_period_end__gt=now)))
    )


def user_has_course_access(user, course):
    """
    THE single centralized check for "can this user access this course's
    recorded content" -- permanent Enrollment OR a currently-valid
    Subscription whose plan includes this course. Use this instead of a
    raw Enrollment.objects.filter(...).exists() anywhere new access-gating
    is needed; existing call sites are extended to use it in this same
    phase rather than left to duplicate the logic.

    Subscription-based access is deliberately restricted to RECORDED
    courses (course.course_type == Course.CourseType.RECORDED) -- live
    classes remain governed entirely by their own existing batch/session
    assignment rules (LiveBatchStudent etc.), never by blanket subscription
    coverage, even if a plan were ever misconfigured to reference a LIVE
    course. Enrollment-based access is NOT restricted this way (unchanged,
    existing precedent: Enrollment already covers both recorded and live
    courses, e.g. via LiveBatchService.assign_student).

    Does not re-derive course *visibility* (is_published) -- that remains
    CourseViewSet.get_queryset's job, unchanged by this function. A
    SubscriptionPlan may in principle reference an unpublished course (the
    model itself enforces no such restriction, mirroring Bundle's own
    precedent) -- exactly as an admin-created Enrollment already could
    before this phase -- so this function can return True for an
    unpublished course a user is legitimately entitled to; it simply never
    makes that course *discoverable* to anyone who doesn't already have
    some access route to it, since nothing here changes what's visible in
    course listings.
    """
    if not user or not getattr(user, 'is_authenticated', False):
        return False
    if Enrollment.objects.filter(user=user, course=course).exists():
        return True
    if course.course_type != Course.CourseType.RECORDED:
        return False
    return Subscription.objects.filter(
        _valid_subscription_filter(), user=user, plan__courses=course,
    ).exists()


def accessible_course_ids_for_user(user):
    """
    Bulk counterpart to user_has_course_access(), for listing views (e.g.
    CourseViewSet.my_courses) that need "which courses can this user
    access" without one query per course. Three queries total regardless
    of how many courses/subscriptions/enrollments exist (no N+1): one for
    enrolled course ids, one for currently-valid subscription plan ids,
    one for the RECORDED courses those plans grant. Returns a set of
    Course ids.

    A SubscriptionPlan's `courses` M2M is the only relationship consulted
    here -- SubscriptionPlan has no `bundles` field (confirmed by reading
    the current model fresh: its own docstring is explicit that bundle
    expansion was deliberately left out of scope in Phase 3.4.1 and never
    added since), so there is no bundle-to-courses expansion to perform;
    a plan only ever grants the courses it lists directly.
    """
    if not user or not getattr(user, 'is_authenticated', False):
        return set()

    enrolled_ids = set(Enrollment.objects.filter(user=user).values_list('course_id', flat=True))

    plan_ids = list(
        Subscription.objects.filter(_valid_subscription_filter(), user=user).values_list('plan_id', flat=True)
    )
    if not plan_ids:
        return enrolled_ids

    subscription_course_ids = set(
        Course.objects.filter(
            subscription_plans__id__in=plan_ids, course_type=Course.CourseType.RECORDED,
        ).values_list('id', flat=True)
    )
    return enrolled_ids | subscription_course_ids


def instructor_course_ids_for_user(user):
    """
    Course-content security follow-up (post-3.4.4). Course ids this user
    can view FULL, unredacted lesson content for via an AUTHORING
    relationship -- not a learner one. Two queries, no N+1 regardless of
    how many courses exist:

    - Any CourseInstructor row for this user, ANY role (TEACHER, ASSISTANT,
      *and* MENTOR) -- broader than IsSuperAdminOrCourseInstructorOrReadOnly's
      own WRITE_ROLES (TEACHER/ASSISTANT only), deliberately: viewing
      content is a different, less sensitive permission than editing it,
      and a mentor overseeing a course needs to see what they're mentoring.
      Write access itself is untouched by this function or this module --
      it remains that permission class's job alone.
    - The exact same legacy self-enrolled-teacher fallback
      IsSuperAdminOrCourseInstructorOrReadOnly.has_object_permission
      already uses for writes (`is_teacher` + an Enrollment row on that
      course) -- replicated here verbatim (including its known imprecision
      of not distinguishing "enrolled to teach" from "enrolled as a
      student") specifically so a legacy teacher's VIEW access can never
      be stricter than their already-existing EDIT access.
    """
    if not user or not getattr(user, 'is_authenticated', False):
        return set()

    from ..models import CourseInstructor

    ids = set(CourseInstructor.objects.filter(user=user).values_list('course_id', flat=True))
    if getattr(user, 'is_teacher', False):
        ids |= set(Enrollment.objects.filter(user=user).values_list('course_id', flat=True))
    return ids
