"""
Fill in missing names of migrated students from the old-LMS export.

Accounts imported from the old LMS are named legacy_<24-hex old student id>
and many have no first/last name, so admin screens (attendance, linking Zoom
joiners) show "legacy_69e3..." instead of the student's name. The old-LMS
export (data/natya_analytics.xlsx) lists every student with that same id.

Rules:
  * Matched ONLY by the exact old student id in the username -- never by name,
    phone or email.
  * Only accounts whose first AND last name are both empty are touched; a name
    someone already set is never overwritten.
  * Ids that appear with two different names in the export are skipped.
  * Staff/superusers and protected accounts are never touched.
  * Dry run by default; --apply writes. Output is counts only (no names).

Usage:
    python manage.py backfill_legacy_student_names            # dry run
    python manage.py backfill_legacy_student_names --apply
"""
import re
from collections import defaultdict
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

LEGACY_USERNAME = re.compile(r"^legacy_([0-9a-f]{24})$", re.IGNORECASE)
PROTECTED_USER_IDS = frozenset({1, 4, 25, 39, 96})
SHEETS = ("Student Fees", "Student Courses")


def _clean(name):
    return re.sub(r"\s+", " ", str(name or "")).strip()


def load_legacy_names(path):
    """{old_student_id: set(names)} from the export's student sheets."""
    try:
        import openpyxl
    except ImportError as exc:  # pragma: no cover
        raise CommandError("openpyxl is required.") from exc
    wb = openpyxl.load_workbook(path, read_only=True)
    names = defaultdict(set)
    for title in SHEETS:
        if title not in wb.sheetnames:
            continue
        rows = wb[title].iter_rows(values_only=True)
        header = [str(h or "").strip() for h in next(rows, [])]
        try:
            id_col, name_col = header.index("Student ID"), header.index("Student Name")
        except ValueError:
            continue
        for row in rows:
            sid = str(row[id_col] or "").strip().lower() if id_col < len(row) else ""
            name = _clean(row[name_col]) if name_col < len(row) else ""
            if re.fullmatch(r"[0-9a-f]{24}", sid) and name:
                names[sid].add(name)
    wb.close()
    return names


class Command(BaseCommand):
    help = "Fill empty names of legacy_<id> students from the old-LMS export (exact id match; dry run unless --apply)."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="Write the names (default: dry run).")
        parser.add_argument("--workbook", default=str(Path(settings.BASE_DIR) / "data" / "natya_analytics.xlsx"))

    def handle(self, *args, **opts):
        path = Path(opts["workbook"])
        if not path.exists():
            raise CommandError(f"Workbook not found: {path}")
        legacy = load_legacy_names(path)

        User = get_user_model()
        counts = defaultdict(int)
        updates = []
        for user in User.objects.filter(username__istartswith="legacy_").order_by("pk"):
            m = LEGACY_USERNAME.match(user.username or "")
            if not m:
                continue
            counts["legacy_accounts"] += 1
            if (user.first_name or "").strip() or (user.last_name or "").strip():
                counts["already_named"] += 1
                continue
            if user.pk in PROTECTED_USER_IDS or user.is_staff or user.is_superuser:
                counts["skipped_protected_or_staff"] += 1
                continue
            found = legacy.get(m.group(1).lower())
            if not found:
                counts["not_in_export"] += 1
                continue
            distinct = {n.casefold() for n in found}
            if len(distinct) > 1:
                counts["skipped_conflicting_names"] += 1
                continue
            user.first_name = sorted(found)[0][:150]
            user.last_name = ""
            updates.append(user)
        counts["to_update"] = len(updates)

        mode = "APPLY" if opts["apply"] else "DRY RUN"
        self.stdout.write(f"[{mode}] old-LMS student ids in export: {len(legacy)}")
        for key in ("legacy_accounts", "already_named", "skipped_protected_or_staff", "not_in_export",
                    "skipped_conflicting_names", "to_update"):
            self.stdout.write(f"  {key}: {counts[key]}")

        if not opts["apply"]:
            self.stdout.write("Dry run: nothing was changed. Re-run with --apply to write.")
            return
        with transaction.atomic():
            User.objects.bulk_update(updates, ["first_name", "last_name"], batch_size=500)
        from users.models import AdminAuditLog
        AdminAuditLog.record(
            actor=None, action="LEGACY_STUDENT_NAMES_BACKFILLED", target_type="User", target_id="",
            description=f"Filled names of {len(updates)} legacy student accounts from the old-LMS export.",
            metadata={k: counts[k] for k in counts},
        )
        self.stdout.write(self.style.SUCCESS(f"Updated {len(updates)} account(s)."))
