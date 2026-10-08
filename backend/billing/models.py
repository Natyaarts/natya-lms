import uuid
from decimal import Decimal
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone


class BillingType(models.TextChoices):
    ONE_TIME = 'ONE_TIME', 'One Time'
    MONTHLY = 'MONTHLY', 'Monthly'
    EVERY_2_MONTHS = 'EVERY_2_MONTHS', 'Every 2 Months'
    EVERY_3_MONTHS = 'EVERY_3_MONTHS', 'Every 3 Months'
    EVERY_6_MONTHS = 'EVERY_6_MONTHS', 'Every 6 Months'
    YEARLY = 'YEARLY', 'Yearly'
    CUSTOM = 'CUSTOM', 'Custom'


class IntervalUnit(models.TextChoices):
    DAYS = 'DAYS', 'Days'
    WEEKS = 'WEEKS', 'Weeks'
    MONTHS = 'MONTHS', 'Months'
    YEARS = 'YEARS', 'Years'


class PlanStatus(models.TextChoices):
    ACTIVE = 'ACTIVE', 'Active'
    UPCOMING = 'UPCOMING', 'Upcoming'
    DUE = 'DUE', 'Due'
    GRACE_PERIOD = 'GRACE_PERIOD', 'Grace Period'
    OVERDUE = 'OVERDUE', 'Overdue'
    RESTRICTED = 'RESTRICTED', 'Restricted'
    PAUSED = 'PAUSED', 'Paused'
    CANCELLED = 'CANCELLED', 'Cancelled'
    COMPLETED = 'COMPLETED', 'Completed'


class InvoiceStatus(models.TextChoices):
    DRAFT = 'DRAFT', 'Draft'
    ISSUED = 'ISSUED', 'Issued'
    PENDING = 'PENDING', 'Pending'
    PAID = 'PAID', 'Paid'
    OVERDUE = 'OVERDUE', 'Overdue'
    CANCELLED = 'CANCELLED', 'Cancelled'
    WAIVED = 'WAIVED', 'Waived'


class PaymentMethod(models.TextChoices):
    RAZORPAY = 'RAZORPAY', 'Razorpay'
    CASH = 'CASH', 'Cash'
    BANK_TRANSFER = 'BANK_TRANSFER', 'Bank Transfer'
    UPI = 'UPI', 'UPI'
    WAIVED = 'WAIVED', 'Waived'
    OTHER = 'OTHER', 'Other'


class StudentBillingPlan(models.Model):
    """
    Represents the recurring or one-time fee billing arrangement configured
    for a specific Student + Course pair. Independent of Course Enrollment.
    """
    student = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name='billing_plans',
        on_delete=models.CASCADE,
        db_index=True
    )
    course = models.ForeignKey(
        'courses.Course',
        related_name='student_billing_plans',
        on_delete=models.CASCADE,
        db_index=True
    )
    enrollment = models.ForeignKey(
        'courses.Enrollment',
        related_name='billing_plans',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        help_text="Optional link to course enrollment. Enrollment is retained even if billing is overdue."
    )

    billing_type = models.CharField(
        max_length=30,
        choices=BillingType.choices,
        default=BillingType.MONTHLY
    )
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=3, default='INR')
    billing_interval_value = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Interval count for CUSTOM billing (e.g. 45)."
    )
    billing_interval_unit = models.CharField(
        max_length=20,
        choices=IntervalUnit.choices,
        null=True,
        blank=True,
        help_text="Interval unit for CUSTOM billing (e.g. DAYS)."
    )

    # Configured Effective Cycle Dates (Anchored to actual configured start date)
    start_date = models.DateField(
        help_text="Configured initial effective start date anchoring all billing periods."
    )
    current_period_start = models.DateField(
        help_text="Start date of current fee coverage period."
    )
    current_period_end = models.DateField(
        help_text="End date of current fee coverage period."
    )
    next_billing_date = models.DateField(
        null=True,
        blank=True,
        help_text="Anchor start date of next billing cycle. None for ONE_TIME."
    )
    due_date = models.DateField(
        help_text="Target payment due date."
    )

    # Timing Parameters
    advance_invoice_days = models.PositiveIntegerField(
        default=7,
        help_text="Days prior to next_billing_date when upcoming invoice is generated."
    )
    grace_period_days = models.PositiveIntegerField(
        default=7,
        help_text="Grace days granted after due_date before content access is restricted."
    )
    grace_until = models.DateField(
        null=True,
        blank=True,
        help_text="due_date + grace_period_days."
    )
    access_restriction_date = models.DateField(
        null=True,
        blank=True,
        help_text="Canonical date content locks if unpaid (grace_until + 1 day)."
    )

    # Status & Automation Flags
    status = models.CharField(
        max_length=30,
        choices=PlanStatus.choices,
        default=PlanStatus.ACTIVE,
        db_index=True
    )
    auto_generate_invoice = models.BooleanField(
        default=True,
        help_text="Automatically generate upcoming invoices via Celery schedule."
    )
    auto_restrict_access = models.BooleanField(
        default=True,
        help_text="Automatically restrict course content access after grace_until."
    )
    auto_send_reminders = models.BooleanField(
        default=True,
        help_text="Automatically send payment and grace period notifications."
    )
    is_active = models.BooleanField(
        default=True,
        db_index=True,
        help_text="True if this is the active billing arrangement for this student+course."
    )

    # Scheduled Plan Change (NEXT_CYCLE Default)
    pending_billing_type = models.CharField(
        max_length=30,
        choices=BillingType.choices,
        null=True,
        blank=True,
        help_text="Scheduled new billing type taking effect on next_billing_date."
    )
    pending_amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Scheduled new amount taking effect on next_billing_date."
    )
    pending_effective_date = models.DateField(
        null=True,
        blank=True,
        help_text="Target date when pending plan change activates."
    )

    # Lifecycle tracking
    paused_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='created_billing_plans'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Student Billing Plan'
        verbose_name_plural = 'Student Billing Plans'
        constraints = [
            models.UniqueConstraint(
                fields=['student', 'course'],
                condition=models.Q(is_active=True),
                name='unique_active_billing_plan_per_student_course'
            ),
            models.CheckConstraint(
                condition=models.Q(amount__gte=0),
                name='billing_plan_amount_non_negative'
            ),
            models.CheckConstraint(
                condition=models.Q(current_period_start__lte=models.F('current_period_end')),
                name='billing_plan_period_valid'
            )
        ]

    def clean(self):
        super().clean()
        if self.billing_type == BillingType.CUSTOM:
            if not self.billing_interval_value or self.billing_interval_value <= 0:
                raise ValidationError({"billing_interval_value": "Interval value must be positive for custom billing."})
            if not self.billing_interval_unit:
                raise ValidationError({"billing_interval_unit": "Interval unit is required for custom billing."})

    def __str__(self):
        return f"{self.student.username} - {self.course.title} ({self.get_billing_type_display()} ₹{self.amount} - {self.status})"


class StudentInvoice(models.Model):
    """
    Represents a pre-payment billing/due invoice issued to a student for a specific
    course coverage period. Distinct from finance.Invoice (customer receipt).
    """
    invoice_number = models.CharField(
        max_length=32,
        unique=True,
        editable=False,
        db_index=True
    )
    billing_plan = models.ForeignKey(
        StudentBillingPlan,
        related_name='invoices',
        on_delete=models.CASCADE,
        db_index=True
    )
    student = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name='billing_invoices',
        on_delete=models.PROTECT,
        db_index=True
    )
    course = models.ForeignKey(
        'courses.Course',
        related_name='billing_invoices',
        on_delete=models.PROTECT,
        db_index=True
    )

    period_start = models.DateField(
        help_text="Start date of fee coverage period."
    )
    period_end = models.DateField(
        help_text="End date of fee coverage period."
    )
    issue_date = models.DateField(
        help_text="Date invoice was issued."
    )
    due_date = models.DateField(
        help_text="Payment deadline."
    )

    amount = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=3, default='INR')
    status = models.CharField(
        max_length=30,
        choices=InvoiceStatus.choices,
        default=InvoiceStatus.ISSUED,
        db_index=True
    )

    # Razorpay Gateway Tracking
    razorpay_order_id = models.CharField(
        max_length=255,
        blank=True,
        null=True,
        db_index=True
    )
    razorpay_payment_id = models.CharField(
        max_length=255,
        blank=True,
        null=True
    )
    payment_method = models.CharField(
        max_length=50,
        blank=True,
        help_text="Method used when payment completed (RAZORPAY, CASH, UPI, etc.)."
    )
    paid_at = models.DateTimeField(null=True, blank=True)

    notes = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-due_date', '-created_at']
        verbose_name = 'Student Invoice'
        verbose_name_plural = 'Student Invoices'
        constraints = [
            # Guarantees no duplicate active invoice for the exact same coverage period
            models.UniqueConstraint(
                fields=['billing_plan', 'period_start', 'period_end'],
                condition=~models.Q(status='CANCELLED'),
                name='unique_invoice_per_billing_period'
            ),
            models.CheckConstraint(
                condition=models.Q(amount__gte=0),
                name='student_invoice_amount_non_negative'
            )
        ]

    def save(self, *args, **kwargs):
        if not self.invoice_number:
            year = timezone.now().year
            unique_hex = uuid.uuid4().hex[:8].upper()
            self.invoice_number = f"INV-NATYA-{year}-{unique_hex}"
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.invoice_number} ({self.student.username} - {self.course.title}: ₹{self.amount} - {self.status})"


class ManualPaymentRecord(models.Model):
    """
    Offline/manual payment transaction recorded by an administrator.
    Requires amount >= invoice.amount before marking the invoice PAID.
    """
    invoice = models.ForeignKey(
        StudentInvoice,
        related_name='manual_payments',
        on_delete=models.PROTECT
    )
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    payment_method = models.CharField(
        max_length=50,
        choices=PaymentMethod.choices,
        default=PaymentMethod.CASH
    )
    payment_date = models.DateTimeField()
    reference_number = models.CharField(
        max_length=255,
        blank=True,
        help_text="Bank UTR, UPI transaction ID, cheque number, or offline receipt ID."
    )
    receipt_file = models.FileField(
        upload_to='billing/receipts/',
        blank=True,
        null=True
    )
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        on_delete=models.SET_NULL,
        related_name='recorded_manual_payments'
    )
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-payment_date']
        verbose_name = 'Manual Payment Record'
        verbose_name_plural = 'Manual Payment Records'
        constraints = [
            models.CheckConstraint(
                condition=models.Q(amount__gt=0),
                name='manual_payment_amount_positive'
            )
        ]

    def __str__(self):
        return f"Payment ₹{self.amount} for {self.invoice.invoice_number} ({self.payment_method})"


class BillingPlanChangeLog(models.Model):
    """
    Immutable audit record for every plan upgrade/downgrade/frequency adjustment.
    """
    billing_plan = models.ForeignKey(
        StudentBillingPlan,
        related_name='change_logs',
        on_delete=models.CASCADE
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        on_delete=models.SET_NULL,
        related_name='plan_changes_made'
    )
    change_mode = models.CharField(
        max_length=30,
        choices=[('NEXT_CYCLE', 'Next Cycle (Standard)'), ('IMMEDIATE', 'Immediate')]
    )
    old_billing_type = models.CharField(max_length=30)
    new_billing_type = models.CharField(max_length=30)
    old_amount = models.DecimalField(max_digits=10, decimal_places=2)
    new_amount = models.DecimalField(max_digits=10, decimal_places=2)
    effective_date = models.DateField(
        help_text="Anchor date when new plan terms take effect."
    )
    reason = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Billing Plan Change Log'
        verbose_name_plural = 'Billing Plan Change Logs'

    def __str__(self):
        return f"Change on Plan #{self.billing_plan_id}: {self.old_billing_type} -> {self.new_billing_type} effective {self.effective_date}"


class BillingAccessExtension(models.Model):
    """
    Explicit, audited admin override extending student content access
    without altering historical invoice dates.
    """
    student = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name='billing_access_extensions',
        on_delete=models.CASCADE,
        db_index=True
    )
    course = models.ForeignKey(
        'courses.Course',
        related_name='billing_access_extensions',
        on_delete=models.CASCADE,
        db_index=True
    )
    billing_plan = models.ForeignKey(
        StudentBillingPlan,
        related_name='access_extensions',
        on_delete=models.CASCADE
    )
    original_status = models.CharField(
        max_length=30,
        help_text="Status of plan at time of override (e.g. OVERDUE, RESTRICTED)."
    )
    extended_until = models.DateField(
        help_text="Access guaranteed through this date regardless of invoice state."
    )
    reason = models.TextField(
        help_text="Mandatory admin justification for audit compliance."
    )
    is_active = models.BooleanField(
        default=True,
        db_index=True
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        on_delete=models.SET_NULL,
        related_name='access_extensions_granted'
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Billing Access Extension'
        verbose_name_plural = 'Billing Access Extensions'
        constraints = [
            models.UniqueConstraint(
                fields=['student', 'course'],
                condition=models.Q(is_active=True),
                name='unique_active_access_extension_per_student_course'
            )
        ]

    def __str__(self):
        return f"Access extended for {self.student.username} on {self.course.title} until {self.extended_until}"


class NotificationEventType(models.TextChoices):
    UPCOMING = 'UPCOMING', 'Upcoming Payment'
    DUE_TODAY = 'DUE_TODAY', 'Payment Due Today'
    GRACE_STARTED = 'GRACE_STARTED', 'Grace Period Started'
    GRACE_FINAL_WARNING = 'GRACE_FINAL_WARNING', 'Grace Period Final Warning'
    ACCESS_RESTRICTED = 'ACCESS_RESTRICTED', 'Access Restricted'
    PAYMENT_SUCCESS = 'PAYMENT_SUCCESS', 'Payment Successful'
    ACCESS_RESTORED = 'ACCESS_RESTORED', 'Access Restored'


class BillingNotificationLog(models.Model):
    """
    Idempotent audit record for billing reminders and notifications.
    Guarantees no duplicate reminders are sent across Celery retries or concurrent workers.
    """
    billing_plan = models.ForeignKey(
        StudentBillingPlan,
        related_name='notification_logs',
        on_delete=models.CASCADE,
        db_index=True
    )
    invoice = models.ForeignKey(
        StudentInvoice,
        related_name='notification_logs',
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        db_index=True
    )
    student = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name='billing_notification_logs',
        on_delete=models.CASCADE,
        db_index=True
    )
    notification_type = models.CharField(
        max_length=50,
        choices=NotificationEventType.choices,
        db_index=True
    )
    period_start = models.DateField(
        help_text="Start date of fee coverage period this notification is for."
    )
    scheduled_date = models.DateField(
        help_text="Date when this notification was eligible/scheduled."
    )
    sent_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(
        max_length=20,
        choices=[('SENT', 'Sent'), ('FAILED', 'Failed'), ('SKIPPED', 'Skipped')],
        default='SENT',
        db_index=True
    )
    channel = models.CharField(
        max_length=50,
        default='ALL',
        help_text="Channels attempted (e.g. IN_APP, EMAIL, WHATSAPP, ALL)"
    )
    idempotency_key = models.CharField(
        max_length=255,
        unique=True,
        db_index=True,
        help_text="Unique key: billing_notif:{plan_id}:{type}:{period_start}:{scheduled_date}"
    )
    error_message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Billing Notification Log'
        verbose_name_plural = 'Billing Notification Logs'
        constraints = [
            models.UniqueConstraint(
                fields=['billing_plan', 'notification_type', 'period_start', 'scheduled_date'],
                name='unique_billing_reminder_per_cycle_event'
            )
        ]

    def __str__(self):
        return f"{self.notification_type} for {self.student.username} ({self.status}) - {self.scheduled_date}"

