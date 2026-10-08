from datetime import date, timedelta
from dateutil.relativedelta import relativedelta
from ..models import BillingType, IntervalUnit


def calculate_period_end(start_date: date, billing_type: str, interval_value: int = None, interval_unit: str = None) -> date:
    """
    Computes the last calendar day of course coverage for a period anchored at start_date.
    Uses leap-safe, month-end-aware dateutil.relativedelta.
    Example: 10 Oct -> 9 Nov (Monthly)
    """
    if billing_type == BillingType.MONTHLY:
        return start_date + relativedelta(months=1) - timedelta(days=1)
    elif billing_type == BillingType.EVERY_2_MONTHS:
        return start_date + relativedelta(months=2) - timedelta(days=1)
    elif billing_type == BillingType.EVERY_3_MONTHS:
        return start_date + relativedelta(months=3) - timedelta(days=1)
    elif billing_type == BillingType.EVERY_6_MONTHS:
        return start_date + relativedelta(months=6) - timedelta(days=1)
    elif billing_type == BillingType.YEARLY:
        return start_date + relativedelta(years=1) - timedelta(days=1)
    elif billing_type == BillingType.ONE_TIME:
        # One-time access defaults to 1 year coverage from start_date
        return start_date + relativedelta(years=1) - timedelta(days=1)
    elif billing_type == BillingType.CUSTOM:
        val = interval_value or 30
        unit = (interval_unit or IntervalUnit.DAYS).upper()
        if unit == IntervalUnit.DAYS:
            return start_date + timedelta(days=max(1, val) - 1)
        elif unit == IntervalUnit.WEEKS:
            return start_date + relativedelta(weeks=val) - timedelta(days=1)
        elif unit == IntervalUnit.MONTHS:
            return start_date + relativedelta(months=val) - timedelta(days=1)
        elif unit == IntervalUnit.YEARS:
            return start_date + relativedelta(years=val) - timedelta(days=1)
        else:
            return start_date + timedelta(days=val - 1)
    return start_date + relativedelta(months=1) - timedelta(days=1)


def calculate_next_billing_date(period_end: date, billing_type: str) -> date | None:
    """
    Computes the start date of the subsequent billing cycle.
    For ONE_TIME plans, returns None (no recurring cycle).
    """
    if billing_type == BillingType.ONE_TIME:
        return None
    return period_end + timedelta(days=1)


def calculate_due_date(period_start: date) -> date:
    """
    Computes the target due date for a cycle, defaulted to period_start.
    """
    return period_start


def calculate_issue_date(target_due_date: date, advance_invoice_days: int = 7) -> date:
    """
    Computes the date an upcoming cycle invoice is generated and delivered to the student.
    Example: 10 Nov due date with 7 advance days -> 3 Nov issue date.
    """
    return target_due_date - timedelta(days=max(0, advance_invoice_days))


def calculate_grace_until(due_date: date, grace_period_days: int = 7) -> date:
    """
    Computes the final inclusive day of the grace window.
    Example: 10 Nov due date + 7 grace days -> 17 Nov.
    """
    return due_date + timedelta(days=max(0, grace_period_days))


def calculate_access_restriction_date(grace_until: date) -> date:
    """
    Computes the exact date content locks if payment is not received.
    Example: grace_until = 17 Nov -> access_restriction_date = 18 Nov.
    """
    return grace_until + timedelta(days=1)
