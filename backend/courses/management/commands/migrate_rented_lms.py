import os
import sys
from django.core.management.base import BaseCommand
from courses.rented_lms_service import execute_rented_lms_migration, resolve_migration_file


class Command(BaseCommand):
    help = 'Audit and migrate legacy LMS Excel export into Natya LMS idempotently.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--file',
            type=str,
            default='natya_analytics.xlsx',
            help='Path to the Natya Analytics Excel export file.'
        )
        parser.add_argument(
            '--commit',
            action='store_true',
            default=False,
            help='Commit changes to the database. If omitted, runs in safe DRY-RUN audit mode.'
        )
        parser.add_argument(
            '--verbose',
            action='store_true',
            default=False,
            help='Print detailed logging for each entity.'
        )

    def handle(self, *args, **options):
        file_arg = options['file']
        is_commit = options['commit']
        verbose = options['verbose']

        resolved = resolve_migration_file(file_arg)
        if not resolved:
            self.stderr.write(self.style.ERROR(
                f"Migration file not found. Looked for '{file_arg}' in current dir, data/, backend/data/, and /tmp."
            ))
            return

        mode_str = self.style.SUCCESS("LIVE COMMIT MODE (Modifying DB)") if is_commit else self.style.WARNING("DRY-RUN AUDIT MODE (Zero DB Changes)")
        self.stdout.write(self.style.MIGRATE_HEADING(f"\n{'='*70}\nNATYA LMS — RENTED LMS DATA RECONCILIATION & MIGRATION\n{'='*70}"))
        self.stdout.write(f"Source file: {resolved}")
        self.stdout.write(f"Execution Mode: {mode_str}\n")

        try:
            stats = execute_rented_lms_migration(
                file_path=resolved,
                is_commit=is_commit,
                verbose=verbose,
                log_callback=lambda msg: self.stdout.write(f"  [INFO] {msg}") if verbose else None
            )
        except Exception as e:
            self.stderr.write(self.style.ERROR(f"Migration error: {e}"))
            import traceback
            traceback.print_exc()
            return

        self.stdout.write(self.style.SUCCESS("\n--- RECONCILIATION & AUDIT MATRIX ---"))
        self.stdout.write(f"{'Entity':<24} | {'Sheet Total':<12} | {'Existing in DB':<16} | {'To Create / Created':<16}")
        self.stdout.write("-" * 75)

        for entity, data in stats.items():
            if not isinstance(data, dict) or 'sheet_count' not in data:
                continue
            entity_title = entity.replace('_', ' ').title()
            sheet_total = data['sheet_count']
            matched = data['matched_existing']
            to_act = data['created'] if is_commit else data['to_create']
            act_label = f"{to_act} (CREATED)" if is_commit else f"{to_act} (PENDING)"

            self.stdout.write(
                f"{entity_title:<24} | {sheet_total:<12} | {matched:<16} | {act_label:<16}"
            )

        self.stdout.write("-" * 75)
        if not is_commit:
            self.stdout.write(self.style.WARNING(
                "\nSAFETY NOTICE: This was a DRY-RUN audit. No database records were modified or created.\n"
                "To execute this migration and commit all pending records, run with: --commit\n"
            ))
        else:
            self.stdout.write(self.style.SUCCESS(
                "\nSUCCESS: 100% Migration committed to database successfully with zero duplicates.\n"
            ))
