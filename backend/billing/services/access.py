from datetime import date
from ..models import StudentBillingPlan, BillingAccessExtension, PlanStatus


def student_has_billing_access(student, course, as_of_date: date = None) -> bool | None:
    """
    Evaluates whether a student has course content access under their configured billing arrangement.
    Returns:
        True: Access granted under billing plan or active access extension.
        False: Access restricted (payment overdue past grace, or cancelled).
        None: No billing plan configured for this student+course (defer to legacy access rules).
    """
    plan = StudentBillingPlan.objects.filter(
        student=student,
        course=course,
        is_active=True
    ).first()

    if not plan:
        return None

    check_date = as_of_date or date.today()

    # 1. Check for active admin access extension override
    has_active_extension = BillingAccessExtension.objects.filter(
        student=student,
        course=course,
        is_active=True,
        extended_until__gte=check_date
    ).exists()

    if has_active_extension:
        return True

    # 2. Check canonical access restriction date
    if plan.auto_restrict_access and plan.access_restriction_date and check_date >= plan.access_restriction_date:
        # Check if latest invoice for current period was paid
        from ..models import StudentInvoice, InvoiceStatus
        unpaid = StudentInvoice.objects.filter(
            billing_plan=plan,
            period_start=plan.current_period_start,
            status__in=[InvoiceStatus.ISSUED, InvoiceStatus.PENDING, InvoiceStatus.OVERDUE, InvoiceStatus.DRAFT]
        ).exists()
        if unpaid:
            return False

    # 3. Status-based evaluation
    if plan.status in [PlanStatus.ACTIVE, PlanStatus.UPCOMING, PlanStatus.DUE, PlanStatus.GRACE_PERIOD]:
        return True
    elif plan.status == PlanStatus.COMPLETED:
        return check_date <= plan.current_period_end
    elif plan.status in [PlanStatus.OVERDUE, PlanStatus.RESTRICTED, PlanStatus.PAUSED, PlanStatus.CANCELLED]:
        return False

    return False
