import logging
from datetime import datetime, date
from celery import shared_task
from django.utils import timezone

from .services.lifecycle import (
    generate_upcoming_invoices,
    update_all_overdue_statuses,
    activate_scheduled_plan_changes
)
from .services.notification import send_scheduled_payment_reminders

logger = logging.getLogger('billing.tasks')


def _parse_as_of_date(as_of_date_str=None) -> date:
    if as_of_date_str:
        if isinstance(as_of_date_str, date):
            return as_of_date_str
        try:
            return datetime.strptime(str(as_of_date_str), "%Y-%m-%d").date()
        except ValueError:
            logger.warning("Invalid as_of_date_str '%s', defaulting to localdate()", as_of_date_str)
    return timezone.localdate()


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def billing_generate_upcoming_invoices(self, as_of_date_str=None):
    """
    Celery task: Evaluates recurring billing plans and issues upcoming invoices
    for the next cycle according to advance_invoice_days.
    Idempotent and retry-safe.
    """
    check_date = _parse_as_of_date(as_of_date_str)
    logger.info("Starting billing_generate_upcoming_invoices task (as_of_date=%s)", check_date)
    try:
        stats = generate_upcoming_invoices(as_of_date=check_date)
        logger.info("Completed billing_generate_upcoming_invoices task: %s", stats)
        return stats
    except Exception as exc:
        logger.error("billing_generate_upcoming_invoices encountered fatal error: %s", exc, exc_info=True)
        raise self.retry(exc=exc)


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def billing_update_overdue_statuses(self, as_of_date_str=None):
    """
    Celery task: Transitions unpaid invoices to OVERDUE and updates
    plan statuses through DUE, GRACE_PERIOD, and RESTRICTED/OVERDUE.
    Respects active BillingAccessExtension overrides.
    """
    check_date = _parse_as_of_date(as_of_date_str)
    logger.info("Starting billing_update_overdue_statuses task (as_of_date=%s)", check_date)
    try:
        stats = update_all_overdue_statuses(as_of_date=check_date)
        logger.info("Completed billing_update_overdue_statuses task: %s", stats)
        return stats
    except Exception as exc:
        logger.error("billing_update_overdue_statuses encountered fatal error: %s", exc, exc_info=True)
        raise self.retry(exc=exc)


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def billing_apply_scheduled_plan_changes(self, as_of_date_str=None):
    """
    Celery task: Automatically activates pending NEXT_CYCLE plan changes
    whose pending_effective_date has arrived.
    Never activates changes before effective date.
    """
    check_date = _parse_as_of_date(as_of_date_str)
    logger.info("Starting billing_apply_scheduled_plan_changes task (as_of_date=%s)", check_date)
    try:
        stats = activate_scheduled_plan_changes(as_of_date=check_date)
        logger.info("Completed billing_apply_scheduled_plan_changes task: %s", stats)
        return stats
    except Exception as exc:
        logger.error("billing_apply_scheduled_plan_changes encountered fatal error: %s", exc, exc_info=True)
        raise self.retry(exc=exc)


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def billing_send_payment_reminders(self, as_of_date_str=None):
    """
    Celery task: Dispatches upcoming, due, grace, final warning, and
    access restriction notifications across In-App, AWS SES, and Interakt WhatsApp.
    Protected by idempotency keys to guarantee no duplicate reminders.
    """
    check_date = _parse_as_of_date(as_of_date_str)
    logger.info("Starting billing_send_payment_reminders task (as_of_date=%s)", check_date)
    try:
        stats = send_scheduled_payment_reminders(as_of_date=check_date)
        logger.info("Completed billing_send_payment_reminders task: %s", stats)
        return stats
    except Exception as exc:
        logger.error("billing_send_payment_reminders encountered fatal error: %s", exc, exc_info=True)
        raise self.retry(exc=exc)


@shared_task(bind=True, max_retries=2, default_retry_delay=120)
def billing_daily_automation(self, as_of_date_str=None):
    """
    Master daily billing automation task.
    Sequentially runs:
      1. Apply scheduled NEXT_CYCLE plan changes
      2. Generate upcoming recurring invoices (advance_invoice_days)
      3. Update overdue invoices and plan statuses (DUE, GRACE, RESTRICTED)
      4. Send scheduled payment reminders
    Isolates errors across sub-steps so an error in one does not halt the others.
    """
    check_date = _parse_as_of_date(as_of_date_str)
    logger.info("================ STARTING DAILY BILLING AUTOMATION (%s) ================", check_date)
    results = {}

    # Step 1: Apply scheduled plan changes
    try:
        results["plan_changes"] = activate_scheduled_plan_changes(as_of_date=check_date)
    except Exception as e:
        logger.error("Daily automation: Step 1 (plan_changes) failed: %s", e, exc_info=True)
        results["plan_changes"] = {"error": str(e)}

    # Step 2: Generate upcoming invoices
    try:
        results["upcoming_invoices"] = generate_upcoming_invoices(as_of_date=check_date)
    except Exception as e:
        logger.error("Daily automation: Step 2 (upcoming_invoices) failed: %s", e, exc_info=True)
        results["upcoming_invoices"] = {"error": str(e)}

    # Step 3: Update overdue statuses and grace/restriction transitions
    try:
        results["overdue_statuses"] = update_all_overdue_statuses(as_of_date=check_date)
    except Exception as e:
        logger.error("Daily automation: Step 3 (overdue_statuses) failed: %s", e, exc_info=True)
        results["overdue_statuses"] = {"error": str(e)}

    # Step 4: Send payment reminders
    try:
        results["reminders"] = send_scheduled_payment_reminders(as_of_date=check_date)
    except Exception as e:
        logger.error("Daily automation: Step 4 (reminders) failed: %s", e, exc_info=True)
        results["reminders"] = {"error": str(e)}

    logger.info("================ FINISHED DAILY BILLING AUTOMATION: %s ================", results)
    return results
