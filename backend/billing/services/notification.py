import logging
from datetime import date, timedelta
from django.conf import settings
from django.db import transaction, IntegrityError
from django.utils import timezone

from notifications.models import NotificationType
from notifications.services import NotificationService
from ..models import (
    StudentBillingPlan, StudentInvoice, BillingNotificationLog,
    NotificationEventType, InvoiceStatus, PlanStatus, BillingAccessExtension,
    BillingType
)

logger = logging.getLogger('billing.notifications')


def get_billing_notification_content(plan: StudentBillingPlan, notification_type: str, invoice: StudentInvoice = None) -> tuple[str, str]:
    """
    Returns (title, body) formatted copy for the given billing notification type.
    """
    course_title = plan.course.title
    amount = invoice.amount if invoice else plan.amount
    due_date = (invoice.due_date if invoice else plan.due_date)
    formatted_due = due_date.strftime('%d %b %Y') if due_date else ''
    formatted_grace = plan.grace_until.strftime('%d %b %Y') if plan.grace_until else ''

    if notification_type == NotificationEventType.UPCOMING:
        title = "Upcoming Course Payment Reminder"
        body = f"Your Natya course payment of ₹{amount} for {course_title} is due on {formatted_due}."

    elif notification_type == NotificationEventType.DUE_TODAY:
        title = "Course Payment Due Today"
        body = f"Your payment of ₹{amount} for {course_title} is due today ({formatted_due}). Please complete your payment to continue seamless access."

    elif notification_type == NotificationEventType.GRACE_STARTED:
        title = "Payment Overdue - Grace Period Active"
        body = f"Your payment for {course_title} is overdue. Please pay before {formatted_grace} to avoid course access restriction."

    elif notification_type == NotificationEventType.GRACE_FINAL_WARNING:
        title = "Urgent: Final Notice Before Course Access Restriction"
        body = f"Your course access for {course_title} will be restricted tomorrow if payment is not completed."

    elif notification_type == NotificationEventType.ACCESS_RESTRICTED:
        title = "Course Access Restricted"
        body = f"Your course access for {course_title} has been temporarily restricted due to an overdue payment."

    elif notification_type == NotificationEventType.PAYMENT_SUCCESS:
        title = "Payment Received Successfully"
        body = f"Your payment of ₹{amount} for {course_title} was received successfully and your course access is active."

    elif notification_type == NotificationEventType.ACCESS_RESTORED:
        title = "Course Access Restored"
        body = f"Your course access for {course_title} has been restored. Welcome back!"

    else:
        title = "Natya Billing Notice"
        body = f"Update regarding your billing plan for {course_title}."

    return title, body


def send_billing_notification(
    plan: StudentBillingPlan,
    notification_type: str,
    invoice: StudentInvoice = None,
    as_of_date: date = None,
    force_retry: bool = False
) -> tuple[BillingNotificationLog | None, bool]:
    """
    Sends a billing notification with strict deduplication and idempotency protection.
    Dispatches via In-App (NotificationService), AWS SES (if configured), and Interakt WhatsApp (if configured).

    Never allows external notification delivery failures to raise or alter invoice/billing state.
    Returns (notification_log, sent_boolean).
    """
    check_date = as_of_date or timezone.localdate()
    period_start = invoice.period_start if invoice else plan.current_period_start

    # Determine canonical scheduled_date for idempotency key
    if notification_type == NotificationEventType.UPCOMING:
        scheduled_date = invoice.issue_date if invoice else check_date
    elif notification_type == NotificationEventType.DUE_TODAY:
        scheduled_date = invoice.due_date if invoice else (plan.due_date or check_date)
    elif notification_type == NotificationEventType.GRACE_STARTED:
        scheduled_date = (invoice.due_date + timedelta(days=1)) if invoice else ((plan.due_date + timedelta(days=1)) if plan.due_date else check_date)
    elif notification_type == NotificationEventType.GRACE_FINAL_WARNING:
        scheduled_date = plan.grace_until if plan.grace_until else check_date
    elif notification_type == NotificationEventType.ACCESS_RESTRICTED:
        scheduled_date = plan.access_restriction_date if plan.access_restriction_date else check_date
    else:
        scheduled_date = check_date

    idempotency_key = f"billing_notif:{plan.id}:{notification_type}:{period_start}:{scheduled_date}"

    # 1. Check existing log to prevent duplicate notifications
    existing_log = BillingNotificationLog.objects.filter(
        billing_plan=plan,
        notification_type=notification_type,
        period_start=period_start,
        scheduled_date=scheduled_date
    ).first()

    if existing_log and existing_log.status == 'SENT' and not force_retry:
        logger.info(
            "Billing reminder skipped because already sent: %s for student %s",
            idempotency_key, plan.student.username
        )
        return existing_log, False

    # Also check by invoice + notification_type + period_start if invoice provided
    if invoice and not force_retry:
        invoice_log = BillingNotificationLog.objects.filter(
            billing_plan=plan,
            invoice=invoice,
            notification_type=notification_type,
            period_start=period_start,
            status='SENT'
        ).first()
        if invoice_log:
            logger.info(
                "Billing reminder skipped because already sent for invoice %s: %s",
                invoice.invoice_number, notification_type
            )
            return invoice_log, False

    # 2. Prepare or acquire notification log atomically
    log = existing_log
    if not log:
        try:
            with transaction.atomic():
                log = BillingNotificationLog.objects.create(
                    billing_plan=plan,
                    invoice=invoice,
                    student=plan.student,
                    notification_type=notification_type,
                    period_start=period_start,
                    scheduled_date=scheduled_date,
                    status='SENT',
                    channel='ALL',
                    idempotency_key=idempotency_key
                )
        except IntegrityError:
            # Another concurrent worker created it
            log = BillingNotificationLog.objects.filter(idempotency_key=idempotency_key).first()
            if log and log.status == 'SENT' and not force_retry:
                logger.info(
                    "Billing reminder skipped because already sent (concurrent): %s",
                    idempotency_key
                )
                return log, False

    title, body = get_billing_notification_content(plan, notification_type, invoice)
    action_url = f"/student/billing?invoice_id={invoice.id}" if invoice else "/student/billing"
    channels_attempted = []
    delivery_errors = []

    # 3. Channel 1: In-App Notification (NotificationService)
    try:
        NotificationService.create_notification(
            recipient=plan.student,
            title=title,
            body=body,
            notification_type=NotificationType.PAYMENT,
            action_url=action_url,
            idempotency_key=f"inapp:{idempotency_key}"
        )
        channels_attempted.append("IN_APP")
    except Exception as e:
        logger.error(
            "In-app notification creation failed for student %s: %s",
            plan.student.username, e, exc_info=True
        )
        delivery_errors.append(f"IN_APP: {str(e)}")

    # 4. Channel 2: AWS SES Email (if configured)
    student_email = getattr(plan.student, 'email', None)
    aws_key = getattr(settings, 'AWS_ACCESS_KEY_ID', None)
    aws_secret = getattr(settings, 'AWS_SECRET_ACCESS_KEY', None)

    if student_email and aws_key and aws_secret:
        try:
            import boto3
            from botocore.exceptions import BotoCoreError, ClientError

            ses_client = boto3.client(
                'ses',
                region_name=getattr(settings, 'AWS_SES_REGION_NAME', 'ap-south-1'),
                aws_access_key_id=aws_key,
                aws_secret_access_key=aws_secret,
            )
            from_email = getattr(settings, 'DEFAULT_FROM_EMAIL', 'support@natyaarts.com')
            ses_client.send_email(
                Source=from_email,
                Destination={'ToAddresses': [student_email]},
                Message={
                    'Subject': {'Data': f"Natya Arts: {title}", 'Charset': 'UTF-8'},
                    'Body': {'Text': {'Data': body, 'Charset': 'UTF-8'}},
                },
            )
            channels_attempted.append("EMAIL")
            logger.info("Billing reminder email sent via SES to ending in %s", student_email[-6:])
        except Exception as e:
            logger.error("AWS SES email send failed for %s: %s", student_email, e)
            delivery_errors.append(f"EMAIL: {str(e)}")

    # 5. Channel 3: Interakt WhatsApp (if configured)
    student_phone = getattr(plan.student, 'phone_number', None)
    interakt_key = getattr(settings, 'INTERAKT_SECRET_KEY', None)

    if student_phone and interakt_key:
        try:
            import requests
            clean_phone = student_phone.replace('+', '').replace(' ', '').replace('-', '').strip()
            headers = {
                "Authorization": f"Basic {interakt_key}",
                "Content-Type": "application/json"
            }
            payload = {
                "phoneNumber": clean_phone,
                "event": f"billing_{notification_type.lower()}",
                "traits": {
                    "course_name": plan.course.title,
                    "amount": str(invoice.amount if invoice else plan.amount),
                    "due_date": str(invoice.due_date if invoice else plan.due_date),
                }
            }
            resp = requests.post(
                "https://api.interakt.ai/v1/public/message/",
                json=payload,
                headers=headers,
                timeout=8
            )
            if resp.status_code in (200, 201, 202):
                channels_attempted.append("WHATSAPP")
                logger.info("Interakt WhatsApp reminder dispatched to %s", clean_phone[-4:])
            else:
                delivery_errors.append(f"WHATSAPP: status={resp.status_code}")
        except Exception as e:
            logger.error("Interakt WhatsApp reminder send failed: %s", e)
            delivery_errors.append(f"WHATSAPP: {str(e)}")

    # 6. Update log status
    now = timezone.now()
    if log:
        if "IN_APP" in channels_attempted or "EMAIL" in channels_attempted or "WHATSAPP" in channels_attempted:
            log.status = 'SENT'
            log.sent_at = now
            log.channel = ', '.join(channels_attempted) or 'IN_APP'
            log.error_message = '; '.join(delivery_errors)
        else:
            log.status = 'FAILED'
            log.error_message = '; '.join(delivery_errors) or "No delivery channels succeeded"
        log.save()

    logger.info(
        "Billing reminder sent: %s for plan #%s to student %s (status=%s, channels=%s)",
        notification_type, plan.id, plan.student.username, log.status if log else 'UNKNOWN', log.channel if log else ''
    )
    return log, True


def send_scheduled_payment_reminders(as_of_date: date = None) -> dict:
    """
    Evaluates and dispatches daily scheduled billing reminders across active billing plans.
    Idempotent and isolated: errors for one student do not stop processing for others.
    """
    check_date = as_of_date or timezone.localdate()
    stats = {"checked": 0, "sent": 0, "skipped": 0, "errors": 0}

    # Query plans eligible for reminders
    eligible_plans = StudentBillingPlan.objects.filter(
        is_active=True,
        auto_send_reminders=True
    ).exclude(
        status__in=[PlanStatus.PAUSED, PlanStatus.CANCELLED, PlanStatus.COMPLETED]
    ).select_related('student', 'course')

    for plan in eligible_plans:
        stats["checked"] += 1
        try:
            # Skip one-time plans if already paid
            if plan.billing_type == BillingType.ONE_TIME:
                has_unpaid = StudentInvoice.objects.filter(
                    billing_plan=plan,
                    status__in=[InvoiceStatus.ISSUED, InvoiceStatus.PENDING, InvoiceStatus.OVERDUE]
                ).exists()
                if not has_unpaid:
                    stats["skipped"] += 1
                    continue

            # Find unpaid invoices for the current/upcoming periods
            unpaid_invoices = list(StudentInvoice.objects.filter(
                billing_plan=plan,
                status__in=[InvoiceStatus.ISSUED, InvoiceStatus.PENDING, InvoiceStatus.OVERDUE]
            ).order_by('due_date'))

            if not unpaid_invoices:
                stats["skipped"] += 1
                continue

            for invoice in unpaid_invoices:
                # 1. UPCOMING REMINDER: Issued and before due date
                if invoice.issue_date <= check_date < invoice.due_date:
                    log, sent = send_billing_notification(
                        plan=plan,
                        notification_type=NotificationEventType.UPCOMING,
                        invoice=invoice,
                        as_of_date=check_date
                    )
                    if sent:
                        stats["sent"] += 1
                    else:
                        stats["skipped"] += 1

                # 2. DUE TODAY REMINDER: Exactly on due date
                elif check_date == invoice.due_date:
                    log, sent = send_billing_notification(
                        plan=plan,
                        notification_type=NotificationEventType.DUE_TODAY,
                        invoice=invoice,
                        as_of_date=check_date
                    )
                    if sent:
                        stats["sent"] += 1
                    else:
                        stats["skipped"] += 1

                # 3. GRACE PERIOD REMINDER: Unpaid past due date, but before restriction
                elif invoice.due_date < check_date < (plan.access_restriction_date or (plan.grace_until + timedelta(days=1))):
                    # Check if final grace warning date (day before restriction)
                    is_final_warning_day = (
                        (plan.access_restriction_date and check_date == (plan.access_restriction_date - timedelta(days=1))) or
                        (plan.grace_until and check_date == plan.grace_until)
                    )

                    if is_final_warning_day:
                        # 4. FINAL GRACE WARNING
                        log, sent = send_billing_notification(
                            plan=plan,
                            notification_type=NotificationEventType.GRACE_FINAL_WARNING,
                            invoice=invoice,
                            as_of_date=check_date
                        )
                        if sent:
                            stats["sent"] += 1
                        else:
                            stats["skipped"] += 1
                    else:
                        # 3. Regular Grace Started reminder
                        log, sent = send_billing_notification(
                            plan=plan,
                            notification_type=NotificationEventType.GRACE_STARTED,
                            invoice=invoice,
                            as_of_date=check_date
                        )
                        if sent:
                            stats["sent"] += 1
                        else:
                            stats["skipped"] += 1

                # 5. ACCESS RESTRICTED: Reached access_restriction_date
                elif plan.access_restriction_date and check_date >= plan.access_restriction_date:
                    # Check if active extension exists
                    has_extension = BillingAccessExtension.objects.filter(
                        student=plan.student,
                        course=plan.course,
                        is_active=True,
                        extended_until__gte=check_date
                    ).exists()

                    if not has_extension and plan.auto_restrict_access:
                        log, sent = send_billing_notification(
                            plan=plan,
                            notification_type=NotificationEventType.ACCESS_RESTRICTED,
                            invoice=invoice,
                            as_of_date=check_date
                        )
                        if sent:
                            stats["sent"] += 1
                        else:
                            stats["skipped"] += 1

        except Exception as e:
            stats["errors"] += 1
            logger.error(
                "Error sending billing reminders for plan #%s (student: %s): %s",
                plan.id, plan.student.username, e, exc_info=True
            )

    logger.info("Billing reminders sweep finished: %s", stats)
    return stats
