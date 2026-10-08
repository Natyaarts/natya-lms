from datetime import date
from django.test import SimpleTestCase
from billing.models import BillingType, IntervalUnit
from billing.services.calculation import (
    calculate_period_end,
    calculate_next_billing_date,
    calculate_due_date,
    calculate_issue_date,
    calculate_grace_until,
    calculate_access_restriction_date,
)


class PeriodCalculationTests(SimpleTestCase):
    """
    Tests for leap-safe and interval-safe date calculators.
    Anchored to actual configured effective start dates.
    """

    def test_monthly_period_calculation(self):
        # 10 Oct 2026 -> 9 Nov 2026
        start = date(2026, 10, 10)
        period_end = calculate_period_end(start, BillingType.MONTHLY)
        self.assertEqual(period_end, date(2026, 11, 9))

        next_billing = calculate_next_billing_date(period_end, BillingType.MONTHLY)
        self.assertEqual(next_billing, date(2026, 11, 10))

    def test_every_2_months_calculation(self):
        # 10 Oct 2026 -> 9 Dec 2026
        start = date(2026, 10, 10)
        period_end = calculate_period_end(start, BillingType.EVERY_2_MONTHS)
        self.assertEqual(period_end, date(2026, 12, 9))

        next_billing = calculate_next_billing_date(period_end, BillingType.EVERY_2_MONTHS)
        self.assertEqual(next_billing, date(2026, 12, 10))

    def test_every_3_months_calculation(self):
        # 10 Oct 2026 -> 9 Jan 2027
        start = date(2026, 10, 10)
        period_end = calculate_period_end(start, BillingType.EVERY_3_MONTHS)
        self.assertEqual(period_end, date(2027, 1, 9))

        next_billing = calculate_next_billing_date(period_end, BillingType.EVERY_3_MONTHS)
        self.assertEqual(next_billing, date(2027, 1, 10))

    def test_every_6_months_calculation(self):
        # 10 Oct 2026 -> 9 Apr 2027
        start = date(2026, 10, 10)
        period_end = calculate_period_end(start, BillingType.EVERY_6_MONTHS)
        self.assertEqual(period_end, date(2027, 4, 9))

        next_billing = calculate_next_billing_date(period_end, BillingType.EVERY_6_MONTHS)
        self.assertEqual(next_billing, date(2027, 4, 10))

    def test_yearly_calculation(self):
        # 10 Oct 2026 -> 9 Oct 2027
        start = date(2026, 10, 10)
        period_end = calculate_period_end(start, BillingType.YEARLY)
        self.assertEqual(period_end, date(2027, 10, 9))

        next_billing = calculate_next_billing_date(period_end, BillingType.YEARLY)
        self.assertEqual(next_billing, date(2027, 10, 10))

    def test_one_time_calculation(self):
        # 10 Oct 2026 -> 9 Oct 2027; next_billing_date is None
        start = date(2026, 10, 10)
        period_end = calculate_period_end(start, BillingType.ONE_TIME)
        self.assertEqual(period_end, date(2027, 10, 9))

        next_billing = calculate_next_billing_date(period_end, BillingType.ONE_TIME)
        self.assertIsNone(next_billing)

    def test_custom_days_calculation(self):
        # 45 days starting 1 Oct 2026 -> 14 Nov 2026
        start = date(2026, 10, 1)
        period_end = calculate_period_end(
            start, BillingType.CUSTOM, interval_value=45, interval_unit=IntervalUnit.DAYS
        )
        self.assertEqual(period_end, date(2026, 11, 14))

        next_billing = calculate_next_billing_date(period_end, BillingType.CUSTOM)
        self.assertEqual(next_billing, date(2026, 11, 15))

    def test_custom_weeks_calculation(self):
        # 2 weeks starting 1 Oct 2026 -> 14 Oct 2026
        start = date(2026, 10, 1)
        period_end = calculate_period_end(
            start, BillingType.CUSTOM, interval_value=2, interval_unit=IntervalUnit.WEEKS
        )
        self.assertEqual(period_end, date(2026, 10, 14))

    def test_month_end_clamping_and_leap_year(self):
        # 31 Jan 2024 (leap year) + 1 month -> 29 Feb 2024 - 1 day = 28 Feb 2024
        start_leap = date(2024, 1, 31)
        end_leap = calculate_period_end(start_leap, BillingType.MONTHLY)
        self.assertEqual(end_leap, date(2024, 2, 28))

        # 31 Jan 2026 (non-leap) + 1 month -> 28 Feb 2026 - 1 day = 27 Feb 2026
        start_non_leap = date(2026, 1, 31)
        end_non_leap = calculate_period_end(start_non_leap, BillingType.MONTHLY)
        self.assertEqual(end_non_leap, date(2026, 2, 27))

    def test_timing_separation_calculations(self):
        due_date = date(2026, 11, 10)

        # Issue date 7 days before due date
        issue_date = calculate_issue_date(due_date, advance_invoice_days=7)
        self.assertEqual(issue_date, date(2026, 11, 3))

        # Grace until 7 days after due date
        grace_until = calculate_grace_until(due_date, grace_period_days=7)
        self.assertEqual(grace_until, date(2026, 11, 17))

        # Access locks the day after grace ends
        lock_date = calculate_access_restriction_date(grace_until)
        self.assertEqual(lock_date, date(2026, 11, 18))
