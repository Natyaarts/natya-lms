"""
Phase 3.5.7. `python manage.py reconcile_finance` -- a thin CLI wrapper
around finance/reconciliation.py's run_reconciliation(). Prints the
summary and every detected issue grouped by severity, and exits non-zero
when any CRITICAL issue was found (a natural hook for a staging/CI/cron
health check). This command NEVER modifies a record -- it calls the same
strictly read-only service the admin API views call, nothing more.
"""
import sys

from django.core.management.base import BaseCommand

from finance.reconciliation import Severity, run_reconciliation


class Command(BaseCommand):
    help = (
        "Read-only finance reconciliation: detects inconsistencies between "
        "successful payments, ledger entries, invoices, refunds, clawbacks, "
        "and payouts. Never creates, updates, or deletes any record."
    )

    def handle(self, *args, **options):
        report = run_reconciliation()

        self.stdout.write("Finance Reconciliation")
        self.stdout.write("-" * 23)
        self.stdout.write(f"Healthy checks: {report.healthy_checks}")
        self.stdout.write(f"Warnings: {report.warning_count}")
        self.stdout.write(f"Critical: {report.critical_count}")

        for label, sev in (("CRITICAL", Severity.CRITICAL), ("WARNING", Severity.WARNING), ("INFO", Severity.INFO)):
            matching = [issue for issue in report.issues if issue.severity == sev]
            if not matching:
                continue
            self.stdout.write("")
            self.stdout.write(f"{label}:")
            for issue in matching:
                self.stdout.write(f"  {issue.message}")

        if report.critical_count > 0:
            self.stdout.write("")
            self.stdout.write(self.style.ERROR(f"{report.critical_count} CRITICAL issue(s) found."))
            sys.exit(1)

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("No CRITICAL issues found."))
