import logging
from datetime import timedelta
from decimal import Decimal
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from users.models import AdminAuditLog
from ..models import (
    StudentInvoice, StudentBillingPlan, ManualPaymentRecord,
    InvoiceStatus, PlanStatus, PaymentMethod, BillingType
)
from .calculation import (
    calculate_period_end, calculate_next_billing_date,
    calculate_grace_until, calculate_access_restriction_date
)

logger = logging.getLogger('billing.payment')


def record_manual_payment(
    invoice: StudentInvoice,
    amount: Decimal,
    payment_method: str = PaymentMethod.CASH,
    reference_number: str = '',
    actor=None,
    notes: str = '',
    receipt_file=None
) -> tuple[StudentInvoice, bool]:
    """
    Records an offline/manual full payment for an invoice.
    Strictly validates amount >= invoice.amount.
    Advances the billing cycle transactionally and restores active access.
    Returns (invoice, processed_boolean).
    """
    if amount < invoice.amount:
        raise ValidationError(
            f"Payment amount (₹{amount}) is less than outstanding invoice amount (₹{invoice.amount}). "
            "Full payment is required."
        )

    with transaction.atomic():
        locked_invoice = StudentInvoice.objects.select_for_update().get(id=invoice.id)
        plan = StudentBillingPlan.objects.select_for_update().get(id=locked_invoice.billing_plan_id)

        # Idempotency guard: if already paid, return safely without double-advancing
        if locked_invoice.status == InvoiceStatus.PAID:
            return locked_invoice, False

        now = timezone.now()
        previous_plan_status = plan.status
        locked_invoice.status = InvoiceStatus.PAID
        locked_invoice.paid_at = now
        locked_invoice.payment_method = payment_method
        locked_invoice.save(update_fields=['status', 'paid_at', 'payment_method', 'updated_at'])

        ManualPaymentRecord.objects.create(
            invoice=locked_invoice,
            amount=amount,
            payment_method=payment_method,
            payment_date=now,
            reference_number=reference_number,
            receipt_file=receipt_file,
            recorded_by=actor,
            notes=notes
        )

        _advance_plan_after_payment(plan, locked_invoice)

        # Dispatch notifications safely (failure does not alter billing or payment state)
        try:
            from .notification import send_billing_notification, NotificationEventType
            send_billing_notification(
                plan=plan,
                notification_type=NotificationEventType.PAYMENT_SUCCESS,
                invoice=locked_invoice
            )
            if previous_plan_status in [PlanStatus.RESTRICTED, PlanStatus.OVERDUE, PlanStatus.GRACE_PERIOD]:
                send_billing_notification(
                    plan=plan,
                    notification_type=NotificationEventType.ACCESS_RESTORED,
                    invoice=locked_invoice
                )
        except Exception as e:
            logger.error("Failed to send manual payment confirmation notification for invoice %s: %s", locked_invoice.id, e)

        AdminAuditLog.record(
            actor=actor,
            action="MANUAL_PAYMENT_RECORDED",
            target_type="StudentInvoice",
            target_id=locked_invoice.id,
            description=f"Recorded manual payment of ₹{amount} ({payment_method}) for invoice {locked_invoice.invoice_number}",
            metadata={
                "invoice_number": locked_invoice.invoice_number,
                "amount": str(amount),
                "payment_method": payment_method,
                "reference_number": reference_number,
                "student_id": locked_invoice.student_id,
                "course_id": locked_invoice.course_id
            }
        )

    return locked_invoice, True


def process_invoice_payment_success(
    invoice: StudentInvoice,
    razorpay_payment_id: str,
    payment_method: str = PaymentMethod.RAZORPAY
) -> tuple[StudentInvoice, bool]:
    """
    Transactional and idempotent handler when an invoice is successfully paid via Razorpay.
    Guarantees no duplicate extensions, no duplicate advances, no duplicate invoices.
    """
    with transaction.atomic():
        locked_invoice = StudentInvoice.objects.select_for_update().get(id=invoice.id)
        plan = StudentBillingPlan.objects.select_for_update().get(id=locked_invoice.billing_plan_id)

        # Idempotency guard: duplicate webhooks/callbacks abort here
        if locked_invoice.status == InvoiceStatus.PAID:
            return locked_invoice, False

        now = timezone.now()
        previous_plan_status = plan.status
        locked_invoice.status = InvoiceStatus.PAID
        locked_invoice.paid_at = now
        locked_invoice.razorpay_payment_id = razorpay_payment_id
        locked_invoice.payment_method = payment_method
        locked_invoice.save(update_fields=['status', 'paid_at', 'razorpay_payment_id', 'payment_method', 'updated_at'])

        _advance_plan_after_payment(plan, locked_invoice)

        # Dispatch notifications safely (failure does not alter billing or payment state)
        try:
            from .notification import send_billing_notification, NotificationEventType
            send_billing_notification(
                plan=plan,
                notification_type=NotificationEventType.PAYMENT_SUCCESS,
                invoice=locked_invoice
            )
            if previous_plan_status in [PlanStatus.RESTRICTED, PlanStatus.OVERDUE, PlanStatus.GRACE_PERIOD]:
                send_billing_notification(
                    plan=plan,
                    notification_type=NotificationEventType.ACCESS_RESTORED,
                    invoice=locked_invoice
                )
        except Exception as e:
            logger.error("Failed to send online payment confirmation notification for invoice %s: %s", locked_invoice.id, e)

        AdminAuditLog.record(
            actor=None,
            action="ONLINE_INVOICE_PAID",
            target_type="StudentInvoice",
            target_id=locked_invoice.id,
            description=f"Invoice {locked_invoice.invoice_number} paid via Razorpay ({razorpay_payment_id})",
            metadata={
                "invoice_number": locked_invoice.invoice_number,
                "amount": str(locked_invoice.amount),
                "razorpay_payment_id": razorpay_payment_id,
                "student_id": locked_invoice.student_id
            }
        )

    return locked_invoice, True


def _advance_plan_after_payment(plan: StudentBillingPlan, invoice: StudentInvoice):
    """
    Helper to advance the plan cycle, apply scheduled plan changes, restore ACTIVE status,
    and prepare the next invoice according to advance_invoice_days if eligible.
    """
    # 1. Check if a scheduled NEXT_CYCLE plan change is pending and should activate
    if plan.pending_billing_type and plan.pending_effective_date:
        if plan.next_billing_date and plan.pending_effective_date <= plan.next_billing_date:
            plan.billing_type = plan.pending_billing_type
            plan.amount = plan.pending_amount
            plan.pending_billing_type = None
            plan.pending_amount = None
            plan.pending_effective_date = None

    # 2. Advance recurring periods if the paid invoice covered the current period
    if invoice.period_start == plan.current_period_start and plan.billing_type != BillingType.ONE_TIME:
        if plan.next_billing_date:
            next_start = plan.next_billing_date
            next_end = calculate_period_end(
                next_start, plan.billing_type, plan.billing_interval_value, plan.billing_interval_unit
            )
            plan.current_period_start = next_start
            plan.current_period_end = next_end
            plan.next_billing_date = calculate_next_billing_date(next_end, plan.billing_type)
            plan.due_date = next_start
            plan.grace_until = calculate_grace_until(plan.due_date, plan.grace_period_days)
            plan.access_restriction_date = calculate_access_restriction_date(plan.grace_until)

    # 3. Always restore ACTIVE status on payment
    plan.status = PlanStatus.ACTIVE
    plan.save()

    # 4. Prepare next invoice according to advance_invoice_days if eligible
    if plan.auto_generate_invoice and plan.billing_type != BillingType.ONE_TIME and plan.next_billing_date:
        advance_days = plan.advance_invoice_days if plan.advance_invoice_days is not None else 7
        issue_threshold = plan.next_billing_date - timedelta(days=advance_days)
        if timezone.localdate() >= issue_threshold:
            from .lifecycle import generate_cycle_invoice
            next_period_start = plan.next_billing_date
            next_period_end = calculate_period_end(
                next_period_start, plan.billing_type, plan.billing_interval_value, plan.billing_interval_unit
            )
            generate_cycle_invoice(
                plan=plan,
                period_start=next_period_start,
                period_end=next_period_end,
                due_date=next_period_start,
                issue_date=timezone.localdate(),
                amount=plan.amount,
                status=InvoiceStatus.ISSUED
            )

