import logging
from datetime import date, timedelta
from decimal import Decimal
from django.db import transaction, IntegrityError
from django.utils import timezone
from users.models import AdminAuditLog

logger = logging.getLogger('billing.lifecycle')
from ..models import (
    StudentBillingPlan, StudentInvoice, BillingPlanChangeLog,
    BillingAccessExtension, BillingType, PlanStatus, InvoiceStatus
)
from .calculation import (
    calculate_period_end, calculate_next_billing_date,
    calculate_due_date, calculate_issue_date,
    calculate_grace_until, calculate_access_restriction_date
)


def create_billing_plan(
    student,
    course,
    billing_type: str,
    amount: Decimal,
    start_date: date,
    currency: str = 'INR',
    enrollment=None,
    billing_interval_value: int = None,
    billing_interval_unit: str = None,
    advance_invoice_days: int = 7,
    grace_period_days: int = 7,
    auto_generate_invoice: bool = True,
    auto_restrict_access: bool = True,
    auto_send_reminders: bool = True,
    created_by=None,
    notes: str = '',
    issue_initial_invoice: bool = True,
    initial_invoice_status: str = InvoiceStatus.ISSUED
) -> StudentBillingPlan:
    """
    Creates a new StudentBillingPlan, computing all distinct cycle and restriction dates.
    Anchors period_start directly to the configured start_date.
    Optionally issues the initial cycle invoice.
    """
    current_period_start = start_date
    current_period_end = calculate_period_end(
        start_date, billing_type, billing_interval_value, billing_interval_unit
    )
    next_billing_date = calculate_next_billing_date(current_period_end, billing_type)
    due_date = start_date
    grace_until = calculate_grace_until(due_date, grace_period_days)
    access_restriction_date = calculate_access_restriction_date(grace_until)

    with transaction.atomic():
        plan = StudentBillingPlan.objects.create(
            student=student,
            course=course,
            enrollment=enrollment,
            billing_type=billing_type,
            amount=amount,
            currency=currency,
            billing_interval_value=billing_interval_value,
            billing_interval_unit=billing_interval_unit,
            start_date=start_date,
            current_period_start=current_period_start,
            current_period_end=current_period_end,
            next_billing_date=next_billing_date,
            due_date=due_date,
            advance_invoice_days=advance_invoice_days,
            grace_period_days=grace_period_days,
            grace_until=grace_until,
            access_restriction_date=access_restriction_date,
            status=PlanStatus.ACTIVE,
            auto_generate_invoice=auto_generate_invoice,
            auto_restrict_access=auto_restrict_access,
            auto_send_reminders=auto_send_reminders,
            is_active=True,
            created_by=created_by,
            notes=notes
        )

        if issue_initial_invoice:
            generate_cycle_invoice(
                plan=plan,
                period_start=current_period_start,
                period_end=current_period_end,
                due_date=due_date,
                issue_date=start_date,
                amount=amount,
                status=initial_invoice_status
            )

        AdminAuditLog.record(
            actor=created_by,
            action="STUDENT_BILLING_PLAN_CREATED",
            target_type="StudentBillingPlan",
            target_id=plan.id,
            description=f"Created {plan.get_billing_type_display()} billing plan for student {student.username} on course {course.title} (₹{amount})",
            metadata={
                "student_id": student.id,
                "course_id": course.id,
                "billing_type": billing_type,
                "amount": str(amount),
                "start_date": str(start_date)
            }
        )

    return plan


def generate_cycle_invoice(
    plan: StudentBillingPlan,
    period_start: date,
    period_end: date,
    due_date: date,
    issue_date: date = None,
    amount: Decimal = None,
    status: str = InvoiceStatus.ISSUED
) -> tuple[StudentInvoice, bool]:
    """
    Idempotently generates a StudentInvoice for a specific billing period.
    Returns (invoice, created_boolean). Never creates duplicates for the same period.
    """
    # 1. Check existing active/un-cancelled invoice for this period
    existing = StudentInvoice.objects.filter(
        billing_plan=plan,
        period_start=period_start,
        period_end=period_end
    ).exclude(status=InvoiceStatus.CANCELLED).first()

    if existing:
        return existing, False

    inv_amount = amount if amount is not None else plan.amount
    inv_issue_date = issue_date or date.today()

    try:
        with transaction.atomic():
            invoice = StudentInvoice.objects.create(
                billing_plan=plan,
                student=plan.student,
                course=plan.course,
                period_start=period_start,
                period_end=period_end,
                issue_date=inv_issue_date,
                due_date=due_date,
                amount=inv_amount,
                currency=plan.currency,
                status=status
            )
            return invoice, True
    except IntegrityError:
        # Concurrent creation won the race
        invoice = StudentInvoice.objects.get(
            billing_plan=plan,
            period_start=period_start,
            period_end=period_end
        )
        return invoice, False


def apply_plan_change(
    plan: StudentBillingPlan,
    new_billing_type: str,
    new_amount: Decimal,
    change_mode: str = 'NEXT_CYCLE',
    actor=None,
    reason: str = ''
) -> StudentBillingPlan:
    """
    Applies or schedules a billing plan modification.
    Default mode: NEXT_CYCLE (standard in Phase 2) — leaves current paid period and current invoice untouched.
    """
    with transaction.atomic():
        old_type = plan.billing_type
        old_amount = plan.amount

        if change_mode == 'NEXT_CYCLE':
            effective_date = plan.next_billing_date or date.today()
            plan.pending_billing_type = new_billing_type
            plan.pending_amount = new_amount
            plan.pending_effective_date = effective_date
            plan.save(update_fields=['pending_billing_type', 'pending_amount', 'pending_effective_date', 'updated_at'])

            BillingPlanChangeLog.objects.create(
                billing_plan=plan,
                actor=actor,
                change_mode='NEXT_CYCLE',
                old_billing_type=old_type,
                new_billing_type=new_billing_type,
                old_amount=old_amount,
                new_amount=new_amount,
                effective_date=effective_date,
                reason=reason
            )

        elif change_mode == 'IMMEDIATE':
            # Phase 2 safety protection: cancel unpaid current invoice if exists, reset cycle starting today
            today = date.today()
            effective_date = today

            # Cancel any unpaid invoice for the current period so student isn't double-charged
            unpaid_invoices = StudentInvoice.objects.filter(
                billing_plan=plan,
                status__in=[InvoiceStatus.ISSUED, InvoiceStatus.PENDING, InvoiceStatus.OVERDUE, InvoiceStatus.DRAFT]
            )
            for inv in unpaid_invoices:
                inv.status = InvoiceStatus.CANCELLED
                inv.notes = f"{inv.notes}\nCancelled due to immediate plan change to {new_billing_type}".strip()
                inv.save(update_fields=['status', 'notes', 'updated_at'])

            # Apply new terms immediately
            plan.billing_type = new_billing_type
            plan.amount = new_amount
            plan.current_period_start = today
            plan.current_period_end = calculate_period_end(
                today, new_billing_type, plan.billing_interval_value, plan.billing_interval_unit
            )
            plan.next_billing_date = calculate_next_billing_date(plan.current_period_end, new_billing_type)
            plan.due_date = today
            plan.grace_until = calculate_grace_until(today, plan.grace_period_days)
            plan.access_restriction_date = calculate_access_restriction_date(plan.grace_until)
            plan.status = PlanStatus.DUE
            plan.pending_billing_type = None
            plan.pending_amount = None
            plan.pending_effective_date = None
            plan.save()

            # Issue fresh invoice for new amount
            generate_cycle_invoice(
                plan=plan,
                period_start=plan.current_period_start,
                period_end=plan.current_period_end,
                due_date=plan.due_date,
                issue_date=today,
                amount=new_amount,
                status=InvoiceStatus.ISSUED
            )

            BillingPlanChangeLog.objects.create(
                billing_plan=plan,
                actor=actor,
                change_mode='IMMEDIATE',
                old_billing_type=old_type,
                new_billing_type=new_billing_type,
                old_amount=old_amount,
                new_amount=new_amount,
                effective_date=effective_date,
                reason=reason
            )

        AdminAuditLog.record(
            actor=actor,
            action="STUDENT_BILLING_PLAN_CHANGED",
            target_type="StudentBillingPlan",
            target_id=plan.id,
            description=f"Changed plan for student {plan.student.username} ({old_type} ₹{old_amount} -> {new_billing_type} ₹{new_amount}, mode={change_mode})",
            metadata={
                "change_mode": change_mode,
                "old_type": old_type,
                "new_type": new_billing_type,
                "old_amount": str(old_amount),
                "new_amount": str(new_amount),
                "effective_date": str(effective_date)
            }
        )

    return plan


def extend_access(
    plan: StudentBillingPlan,
    extended_until: date,
    reason: str,
    actor=None
) -> BillingAccessExtension:
    """
    Extends student course content access through extended_until without modifying
    historical invoice dates. Audited via AdminAuditLog.
    """
    with transaction.atomic():
        # Deactivate any previous active extensions
        BillingAccessExtension.objects.filter(
            student=plan.student,
            course=plan.course,
            is_active=True
        ).update(is_active=False)

        extension = BillingAccessExtension.objects.create(
            student=plan.student,
            course=plan.course,
            billing_plan=plan,
            original_status=plan.status,
            extended_until=extended_until,
            reason=reason,
            is_active=True,
            created_by=actor
        )

        AdminAuditLog.record(
            actor=actor,
            action="BILLING_ACCESS_EXTENDED",
            target_type="BillingAccessExtension",
            target_id=extension.id,
            description=f"Extended access for student {plan.student.username} on course {plan.course.title} until {extended_until}. Reason: {reason}",
            metadata={
                "student_id": plan.student_id,
                "course_id": plan.course_id,
                "extended_until": str(extended_until),
                "original_status": plan.status
            }
        )

    return extension


def update_plan_status_based_on_dates(plan: StudentBillingPlan, as_of_date: date = None) -> str:
    """
    Evaluates and updates plan status based on the current date relative to
    due_date, grace_until, and access_restriction_date.
    Uses timezone-aware localdate by default.
    """
    check_date = as_of_date or timezone.localdate()

    if plan.status in [PlanStatus.PAUSED, PlanStatus.CANCELLED]:
        return plan.status

    if plan.billing_type == BillingType.ONE_TIME and check_date > plan.current_period_end:
        # Check if one-time invoice was paid
        paid_inv = StudentInvoice.objects.filter(billing_plan=plan, status=InvoiceStatus.PAID).exists()
        if paid_inv:
            plan.status = PlanStatus.COMPLETED
            plan.save(update_fields=['status', 'updated_at'])
            return plan.status

    # Find any pending/due/overdue invoices for the current period
    has_unpaid_invoice = StudentInvoice.objects.filter(
        billing_plan=plan,
        period_start=plan.current_period_start,
        status__in=[InvoiceStatus.ISSUED, InvoiceStatus.PENDING, InvoiceStatus.OVERDUE, InvoiceStatus.DRAFT]
    ).exists()

    new_status = plan.status

    if not has_unpaid_invoice:
        new_status = PlanStatus.ACTIVE
    else:
        if check_date < plan.due_date:
            new_status = PlanStatus.ACTIVE
        elif check_date == plan.due_date:
            new_status = PlanStatus.DUE
        elif plan.due_date < check_date <= plan.grace_until:
            new_status = PlanStatus.GRACE_PERIOD
        elif plan.access_restriction_date and check_date >= plan.access_restriction_date:
            new_status = PlanStatus.RESTRICTED if plan.auto_restrict_access else PlanStatus.OVERDUE

    if new_status != plan.status:
        plan.status = new_status
        plan.save(update_fields=['status', 'updated_at'])

    return new_status


def generate_upcoming_invoices(as_of_date: date = None) -> dict:
    """
    Phase 4: Scans active recurring billing plans and issues upcoming invoices
    when the current date reaches advance_invoice_days before next_billing_date.
    
    Safe against concurrent execution and duplicate generation.
    Never generates recurring invoices for ONE_TIME, PAUSED, or CANCELLED plans.
    """
    check_date = as_of_date or timezone.localdate()
    stats = {"checked": 0, "generated": 0, "existing": 0, "errors": 0}

    plans = StudentBillingPlan.objects.filter(
        is_active=True,
        auto_generate_invoice=True
    ).exclude(
        status__in=[PlanStatus.PAUSED, PlanStatus.CANCELLED, PlanStatus.COMPLETED]
    ).exclude(
        billing_type=BillingType.ONE_TIME
    ).select_related('student', 'course')

    for plan in plans:
        stats["checked"] += 1
        try:
            if not plan.next_billing_date:
                continue

            advance_days = plan.advance_invoice_days if plan.advance_invoice_days is not None else 7
            issue_date = plan.next_billing_date - timedelta(days=advance_days)

            if check_date >= issue_date:
                period_start = plan.next_billing_date
                
                # Check if a pending NEXT_CYCLE plan change exists that will apply
                if plan.pending_billing_type and plan.pending_effective_date and plan.pending_effective_date <= period_start:
                    b_type = plan.pending_billing_type
                    inv_amount = plan.pending_amount
                else:
                    b_type = plan.billing_type
                    inv_amount = plan.amount

                period_end = calculate_period_end(
                    period_start, b_type, plan.billing_interval_value, plan.billing_interval_unit
                )
                due_date = period_start

                invoice, created = generate_cycle_invoice(
                    plan=plan,
                    period_start=period_start,
                    period_end=period_end,
                    due_date=due_date,
                    issue_date=check_date,
                    amount=inv_amount,
                    status=InvoiceStatus.ISSUED
                )

                if created:
                    stats["generated"] += 1
                    logger.info(
                        "Billing invoice generated: %s for student %s (%s, period %s to %s)",
                        invoice.invoice_number, plan.student.username, plan.course.title, period_start, period_end
                    )
                else:
                    stats["existing"] += 1
                    logger.info(
                        "Billing invoice already exists for plan #%s period %s to %s",
                        plan.id, period_start, period_end
                    )
        except Exception as e:
            stats["errors"] += 1
            logger.error(
                "Error generating upcoming invoice for plan #%s (student: %s): %s",
                plan.id, plan.student.username, e, exc_info=True
            )

    return stats


def update_all_overdue_statuses(as_of_date: date = None) -> dict:
    """
    Phase 4: Evaluates active billing plans, updates overdue invoices and plan statuses
    (ACTIVE -> DUE -> GRACE_PERIOD -> RESTRICTED/OVERDUE).
    Respects active BillingAccessExtension overrides.
    """
    check_date = as_of_date or timezone.localdate()
    stats = {"checked": 0, "status_changed": 0, "invoices_marked_overdue": 0, "errors": 0}

    plans = StudentBillingPlan.objects.filter(
        is_active=True
    ).exclude(
        status__in=[PlanStatus.CANCELLED, PlanStatus.PAUSED]
    ).select_related('student', 'course')

    for plan in plans:
        stats["checked"] += 1
        try:
            # 1. Update any unpaid invoices past due date to OVERDUE
            unpaid_invoices = StudentInvoice.objects.filter(
                billing_plan=plan,
                status__in=[InvoiceStatus.ISSUED, InvoiceStatus.PENDING]
            )
            for inv in unpaid_invoices:
                if check_date > inv.due_date:
                    inv.status = InvoiceStatus.OVERDUE
                    inv.save(update_fields=['status', 'updated_at'])
                    stats["invoices_marked_overdue"] += 1
                    logger.info("Invoice overdue: %s for student %s", inv.invoice_number, plan.student.username)

            # 2. Check active access extension
            has_active_extension = BillingAccessExtension.objects.filter(
                student=plan.student,
                course=plan.course,
                is_active=True,
                extended_until__gte=check_date
            ).exists()

            if has_active_extension:
                logger.info("Access extension active for student %s on course %s", plan.student.username, plan.course.title)

            # 3. Update plan status based on dates
            old_status = plan.status
            new_status = update_plan_status_based_on_dates(plan, as_of_date=check_date)
            if new_status != old_status:
                stats["status_changed"] += 1
                if new_status == PlanStatus.GRACE_PERIOD:
                    logger.info("Grace period started for student %s on plan #%s", plan.student.username, plan.id)
                elif new_status == PlanStatus.RESTRICTED:
                    logger.info("Access restricted for student %s on plan #%s", plan.student.username, plan.id)

        except Exception as e:
            stats["errors"] += 1
            logger.error(
                "Error updating overdue statuses for plan #%s (student: %s): %s",
                plan.id, plan.student.username, e, exc_info=True
            )

    return stats


def activate_scheduled_plan_changes(as_of_date: date = None) -> dict:
    """
    Phase 4: Automatically activates pending NEXT_CYCLE plan changes
    once the current date reaches or passes pending_effective_date.
    
    Guarantees no early application. Updates plan terms, period anchors,
    re-computes future dates, and generates the new cycle invoice.
    """
    check_date = as_of_date or timezone.localdate()
    stats = {"checked": 0, "activated": 0, "skipped": 0, "errors": 0}

    pending_plans = StudentBillingPlan.objects.filter(
        is_active=True,
        pending_billing_type__isnull=False,
        pending_effective_date__lte=check_date
    ).exclude(
        status__in=[PlanStatus.PAUSED, PlanStatus.CANCELLED]
    ).select_related('student', 'course')

    for plan in pending_plans:
        stats["checked"] += 1
        try:
            with transaction.atomic():
                locked_plan = StudentBillingPlan.objects.select_for_update().get(id=plan.id)
                
                # Double check under lock
                if not (locked_plan.pending_billing_type and locked_plan.pending_effective_date):
                    stats["skipped"] += 1
                    continue

                if check_date < locked_plan.pending_effective_date:
                    # Do not apply early!
                    stats["skipped"] += 1
                    continue

                old_type = locked_plan.billing_type
                old_amount = locked_plan.amount
                new_type = locked_plan.pending_billing_type
                new_amount = locked_plan.pending_amount
                effective_date = locked_plan.pending_effective_date

                # Clear pending fields
                locked_plan.billing_type = new_type
                locked_plan.amount = new_amount
                locked_plan.pending_billing_type = None
                locked_plan.pending_amount = None
                locked_plan.pending_effective_date = None

                # Re-anchor current period to effective_date
                locked_plan.current_period_start = effective_date
                locked_plan.current_period_end = calculate_period_end(
                    effective_date, new_type, locked_plan.billing_interval_value, locked_plan.billing_interval_unit
                )
                locked_plan.next_billing_date = calculate_next_billing_date(locked_plan.current_period_end, new_type)
                locked_plan.due_date = effective_date
                locked_plan.grace_until = calculate_grace_until(effective_date, locked_plan.grace_period_days)
                locked_plan.access_restriction_date = calculate_access_restriction_date(locked_plan.grace_until)
                
                # Check status: if new cycle starts today and unpaid, status is DUE
                if check_date >= effective_date:
                    locked_plan.status = PlanStatus.DUE
                locked_plan.save()

                # Generate new cycle invoice if auto_generate_invoice
                if locked_plan.auto_generate_invoice:
                    generate_cycle_invoice(
                        plan=locked_plan,
                        period_start=locked_plan.current_period_start,
                        period_end=locked_plan.current_period_end,
                        due_date=locked_plan.due_date,
                        issue_date=effective_date,
                        amount=new_amount,
                        status=InvoiceStatus.ISSUED
                    )

                AdminAuditLog.record(
                    actor=None,
                    action="SCHEDULED_PLAN_CHANGE_ACTIVATED",
                    target_type="StudentBillingPlan",
                    target_id=locked_plan.id,
                    description=f"Plan change activated for student {locked_plan.student.username}: {old_type} -> {new_type} (₹{new_amount})",
                    metadata={
                        "plan_id": locked_plan.id,
                        "old_type": old_type,
                        "new_type": new_type,
                        "old_amount": str(old_amount),
                        "new_amount": str(new_amount),
                        "effective_date": str(effective_date)
                    }
                )

                logger.info(
                    "Plan change activated: student %s on plan #%s (%s -> %s, ₹%s)",
                    locked_plan.student.username, locked_plan.id, old_type, new_type, new_amount
                )
                stats["activated"] += 1

        except Exception as e:
            stats["errors"] += 1
            logger.error(
                "Error activating plan change for plan #%s (student: %s): %s",
                plan.id, plan.student.username, e, exc_info=True
            )

    return stats

