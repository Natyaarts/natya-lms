"""
Administrative entry point for Phase B.5 crash recovery and quarantine resolution.

    python manage.py zoom_provisioning_watchdog            # one sweep (same as the beat task)
    python manage.py zoom_provisioning_watchdog --recover-dop 42
    python manage.py zoom_provisioning_watchdog --resolve-dop 42

Every path goes through recover_crashed_provision_dop / verify_and_resolve_quarantine:
a host is released only after a confirmed Zoom teardown or verified absence. There is
no option to bypass the quarantine grace period or force a release.
"""
import json

from django.core.management.base import BaseCommand, CommandError

from courses.services.zoom_lifecycle import (
    run_provisioning_watchdog, recover_crashed_provision_dop, ZoomQuarantineService,
)


class Command(BaseCommand):
    help = "Recover crashed Zoom provisioning operations and resolve elapsed quarantines (fail-closed)."

    def add_arguments(self, parser):
        group = parser.add_mutually_exclusive_group()
        group.add_argument('--recover-dop', type=int, help="Run crash recovery for one durable operation.")
        group.add_argument('--resolve-dop', type=int, help="Run quarantine resolution for one durable operation.")
        parser.add_argument('--limit', type=int, default=50, help="Maximum operations per stage in a sweep.")

    def handle(self, *args, **options):
        try:
            if options['recover_dop']:
                result = recover_crashed_provision_dop(options['recover_dop'])
            elif options['resolve_dop']:
                result = ZoomQuarantineService.verify_and_resolve_quarantine(options['resolve_dop'])
            else:
                result = run_provisioning_watchdog(limit=options['limit'])
        except Exception as exc:
            raise CommandError(f"Held (nothing released): {type(exc).__name__}: {exc}") from exc
        self.stdout.write(json.dumps(result, indent=2, default=str))
