"""
backend/courses/management/commands/sync_zoom_host_accounts.py

Administrative command to reconcile and synchronize Zoom Workplace Pro licensed host accounts
into courses_zoomhostaccount under strict fail-closed safety invariants.

Default mode is strictly READ-ONLY (--dry-run).
Database mutations require BOTH --commit and --confirm=CONFIRM-ZOOM-HOST-SYNC.
"""
import sys
from collections import defaultdict
from typing import Dict, Any, List, Optional

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.contrib.auth import get_user_model
from django.db.models.functions import Lower

from courses.models import ZoomHostAccount
from courses.services.zoom import ZoomService, ZoomAPIError


CONFIRMATION_PHRASE = "CONFIRM-ZOOM-HOST-SYNC"
EXPECTED_LICENSED_SEATS = 40
EXCLUDED_TEACHER_IDS = frozenset({39})  # Permanently excluded test/admin accounts (Teacher #39: Vijay Pn)


class Command(BaseCommand):
    help = (
        "Reconcile and synchronize Zoom Workplace Pro host accounts into courses_zoomhostaccount. "
        "Default mode is read-only dry-run."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            default=True,
            help="Audit mode (default). Queries Zoom API and prints reconciliation report without modifying the DB."
        )
        parser.add_argument(
            "--commit",
            action="store_true",
            default=False,
            help=f"Execute database writes. Requires --confirm={CONFIRMATION_PHRASE}."
        )
        parser.add_argument(
            "--confirm",
            type=str,
            default="",
            help=f"Confirmation phrase required to commit writes: {CONFIRMATION_PHRASE}."
        )
        parser.add_argument(
            "--expected-seats",
            type=int,
            default=EXPECTED_LICENSED_SEATS,
            help=f"Expected number of licensed seats (default: {EXPECTED_LICENSED_SEATS})."
        )

    def handle(self, *args, **options):
        is_commit = options.get("commit", False)
        confirm_phrase = options.get("confirm", "").strip()
        expected_seats = options.get("expected_seats", EXPECTED_LICENSED_SEATS)

        if is_commit:
            if confirm_phrase != CONFIRMATION_PHRASE:
                raise CommandError(
                    f"Production safety violation: --commit requires exact confirmation phrase "
                    f"--confirm={CONFIRMATION_PHRASE} (got: '{confirm_phrase}'). Aborting without changes."
                )

        self.stdout.write("=" * 80)
        self.stdout.write("NATYA LMS — ZOOM HOST ACCOUNT RECONCILIATION & SYNCHRONIZATION")
        self.stdout.write("=" * 80)

        # ---------------------------------------------------------------------
        # 1. Inspect Existing Production Database State
        # ---------------------------------------------------------------------
        self.stdout.write("\n[1/4] Inspecting existing database records in courses_zoomhostaccount...")
        db_snapshot = self._inspect_database_hosts()

        # ---------------------------------------------------------------------
        # 2. Retrieve Zoom Tenant User Inventory via API (with pagination)
        # ---------------------------------------------------------------------
        self.stdout.write("\n[2/4] Querying Zoom API for active user inventory (paginated)...")
        zoom_inventory = self._fetch_zoom_inventory()

        # ---------------------------------------------------------------------
        # 3. Perform Multi-Key Reconciliation & Conflict Detection
        # ---------------------------------------------------------------------
        self.stdout.write("\n[3/4] Performing multi-key matching against LMS teacher accounts...")
        reconciliation = self._reconcile(db_snapshot, zoom_inventory, expected_seats)

        # ---------------------------------------------------------------------
        # 4. Generate Comprehensive Audit Report
        # ---------------------------------------------------------------------
        self._print_audit_report(db_snapshot, zoom_inventory, reconciliation, expected_seats)

        # ---------------------------------------------------------------------
        # 5. Database Write Boundary
        # ---------------------------------------------------------------------
        if not is_commit:
            self.stdout.write(self.style.WARNING(
                "\n>>> DRY-RUN COMPLETE: Zero database modifications were performed.\n"
                f"    To execute production synchronization, rerun with:\n"
                f"    python manage.py sync_zoom_host_accounts --commit --confirm={CONFIRMATION_PHRASE}\n"
            ))
            return

        self.stdout.write(self.style.NOTICE("\n[4/4] Executing atomic database synchronization..."))
        self._execute_sync(reconciliation)

    # -------------------------------------------------------------------------
    # Database Inspection
    # -------------------------------------------------------------------------
    def _inspect_database_hosts(self) -> Dict[str, Any]:
        hosts = list(ZoomHostAccount.objects.select_related("assigned_instructor").all())
        total = len(hosts)

        status_counts = defaultdict(int)
        type_counts = defaultdict(int)
        by_user_id = {}
        by_email = defaultdict(list)
        assigned_count = 0

        for h in hosts:
            status_counts[h.status] += 1
            type_counts[h.account_type] += 1
            if h.assigned_instructor_id:
                assigned_count += 1

            if h.zoom_user_id in by_user_id:
                # Should be prevented by DB unique constraint, but track for diagnostic completeness
                pass
            by_user_id[h.zoom_user_id] = h

            norm_email = (h.email or "").strip().lower()
            if norm_email:
                by_email[norm_email].append(h)

        return {
            "hosts": hosts,
            "total": total,
            "status_counts": dict(status_counts),
            "type_counts": dict(type_counts),
            "by_user_id": by_user_id,
            "by_email": dict(by_email),
            "assigned_count": assigned_count,
        }

    # -------------------------------------------------------------------------
    # Zoom API Fetch with Fail-Closed Validation
    # -------------------------------------------------------------------------
    def _fetch_zoom_inventory(self) -> Dict[str, Any]:
        try:
            raw_active = ZoomService.list_users(status="active", page_size=300)
        except Exception as exc:
            raise CommandError(f"Fail-Closed: Could not retrieve Zoom user inventory from API: {exc}") from exc

        raw_pending = []
        try:
            raw_pending = ZoomService.list_users(status="pending", page_size=300)
        except Exception as exc:
            # Diagnostic inspection of pending invitations is non-blocking
            pass

        licensed_users = []
        basic_users = []
        other_users = []
        email_counts = defaultdict(list)

        for u in raw_active:
            u_id = str(u.get("id") or "").strip()
            u_email = str(u.get("email") or "").strip().lower()
            u_type = int(u.get("type", 1))

            if not u_id or not u_email:
                continue

            record = {
                "id": u_id,
                "email": u_email,
                "first_name": u.get("first_name", ""),
                "last_name": u.get("last_name", ""),
                "display_name": u.get("display_name", ""),
                "type": u_type,
                "status": u.get("status", "active"),
            }

            email_counts[u_email].append(record)

            if u_type == 2:
                licensed_users.append(record)
            elif u_type == 1:
                basic_users.append(record)
            else:
                other_users.append(record)

        duplicate_zoom_emails = {e: recs for e, recs in email_counts.items() if len(recs) > 1}

        pending_users = []
        for u in raw_pending:
            u_id = str(u.get("id") or "").strip()
            u_email = str(u.get("email") or "").strip().lower()
            if u_id and u_email:
                pending_users.append({
                    "id": u_id,
                    "email": u_email,
                    "display_name": u.get("display_name", ""),
                    "type": int(u.get("type", 1)),
                    "status": "pending",
                })

        return {
            "total_active": len(raw_active),
            "total_raw": len(raw_active),
            "licensed_users": licensed_users,
            "basic_users": basic_users,
            "other_users": other_users,
            "duplicate_zoom_emails": duplicate_zoom_emails,
            "pending_users": pending_users,
        }

    # -------------------------------------------------------------------------
    # Multi-Key Reconciliation & Conflict Detection
    # -------------------------------------------------------------------------
    def _reconcile(
        self,
        db_snapshot: Dict[str, Any],
        zoom_inventory: Dict[str, Any],
        expected_seats: int
    ) -> Dict[str, Any]:
        User = get_user_model()

        # 1. Index LMS Teachers and detect duplicate emails (excluding EXCLUDED_TEACHER_IDS)
        raw_teachers = list(
            User.objects.filter(is_teacher=True)
            .exclude(email__isnull=True)
            .exclude(email__exact="")
        )
        total_lms_teachers_raw = len(raw_teachers)

        teachers = [t for t in raw_teachers if t.id not in EXCLUDED_TEACHER_IDS]

        lms_email_to_teachers = defaultdict(list)
        for t in teachers:
            norm_e = t.email.strip().lower()
            if norm_e and "@" in norm_e:
                lms_email_to_teachers[norm_e].append(t)

        duplicate_lms_teacher_emails = {
            e: t_list for e, t_list in lms_email_to_teachers.items() if len(t_list) > 1
        }

        # 2. Reconcile Licensed Zoom Users against DB & LMS
        existing_by_uid = db_snapshot["by_user_id"]
        existing_by_email = db_snapshot["by_email"]
        dup_zoom_emails = zoom_inventory["duplicate_zoom_emails"]

        # Track teachers already assigned to dedicated hosts in DB or planned in this run
        claimed_teacher_to_host = {
            h.assigned_instructor_id: h for h in db_snapshot["hosts"]
            if h.assigned_instructor_id and h.assigned_instructor_id not in EXCLUDED_TEACHER_IDS
        }

        proposed_creates = []
        proposed_updates = []
        conflicts = []
        unmatched_zoom_licensed = []
        matched_zoom_emails = set()

        for zu in zoom_inventory["licensed_users"]:
            z_uid = zu["id"]
            z_email = zu["email"]

            # Ambiguity Check: Duplicate Zoom email
            if z_email in dup_zoom_emails:
                conflicts.append({
                    "type": "AMBIGUOUS_DUPLICATE_ZOOM_EMAIL",
                    "zoom_user_id": z_uid,
                    "email": z_email,
                    "detail": f"Zoom API returned {len(dup_zoom_emails[z_email])} records for email '{z_email}'."
                })
                continue

            # Ambiguity Check: Duplicate LMS teacher email
            if z_email in duplicate_lms_teacher_emails:
                conflicts.append({
                    "type": "AMBIGUOUS_DUPLICATE_LMS_TEACHER_EMAIL",
                    "zoom_user_id": z_uid,
                    "email": z_email,
                    "detail": f"LMS has {len(duplicate_lms_teacher_emails[z_email])} teachers sharing email '{z_email}'."
                })
                continue

            # Check if matching LMS teacher exists
            lms_teachers = lms_email_to_teachers.get(z_email, [])
            target_teacher = lms_teachers[0] if lms_teachers else None

            # Hard safety invariant: Never allow an excluded teacher to be target
            if target_teacher and target_teacher.id in EXCLUDED_TEACHER_IDS:
                target_teacher = None

            # Dual-key lookup in existing database records
            existing_host_by_id = existing_by_uid.get(z_uid)
            existing_hosts_by_email = existing_by_email.get(z_email, [])

            # Safety Guard: Existing host bound to an excluded teacher (matched by Zoom ID OR email)
            excluded_host = None
            if existing_host_by_id and existing_host_by_id.assigned_instructor_id in EXCLUDED_TEACHER_IDS:
                excluded_host = existing_host_by_id
            elif existing_hosts_by_email and any(h.assigned_instructor_id in EXCLUDED_TEACHER_IDS for h in existing_hosts_by_email):
                excluded_host = next(h for h in existing_hosts_by_email if h.assigned_instructor_id in EXCLUDED_TEACHER_IDS)

            if excluded_host:
                conflicts.append({
                    "type": "CONFLICT_EXCLUDED_TEACHER",
                    "zoom_user_id": z_uid,
                    "email": z_email,
                    "host_id": excluded_host.id,
                    "detail": f"Existing host #{excluded_host.id} is bound to excluded Teacher #{excluded_host.assigned_instructor_id}; will not touch."
                })
                continue

            # Check for multi-key database integrity conflict: multiple hosts with same email
            if len(existing_hosts_by_email) > 1:
                conflicts.append({
                    "type": "CONFLICT_DUPLICATE_HOST_RECORDS",
                    "zoom_user_id": z_uid,
                    "email": z_email,
                    "detail": f"Database has {len(existing_hosts_by_email)} host records for email '{z_email}': {[h.id for h in existing_hosts_by_email]}."
                })
                continue

            # Check for multi-key mismatch: ID lookup and email lookup point to different records
            if existing_host_by_id and existing_hosts_by_email and existing_hosts_by_email[0].id != existing_host_by_id.id:
                conflicts.append({
                    "type": "CONFLICT_DUPLICATE_HOST_RECORDS",
                    "zoom_user_id": z_uid,
                    "email": z_email,
                    "detail": f"ID lookup matched host #{existing_host_by_id.id} ({existing_host_by_id.email}) but email lookup matched #{existing_hosts_by_email[0].id}."
                })
                continue

            existing_host = existing_host_by_id or (existing_hosts_by_email[0] if existing_hosts_by_email else None)

            if target_teacher:
                current_host_id = existing_host.id if existing_host else None

                # Conflict Check: Teacher already assigned to another host
                if target_teacher.id in claimed_teacher_to_host:
                    assigned_host = claimed_teacher_to_host[target_teacher.id]
                    if getattr(assigned_host, "id", None) != current_host_id:
                        conflicts.append({
                            "type": "CONFLICT_TEACHER_ALREADY_ASSIGNED",
                            "zoom_user_id": z_uid,
                            "email": z_email,
                            "detail": f"Teacher #{target_teacher.id} ({target_teacher.email}) is already assigned to Host #{getattr(assigned_host, 'id', 'new')} ({getattr(assigned_host, 'zoom_user_id', '')})."
                        })
                        continue

                matched_zoom_emails.add(z_email)

                if not existing_host:
                    # Clean new DEDICATED host creation
                    proposed_creates.append({
                        "zoom_user_id": z_uid,
                        "email": z_email,
                        "display_name": zu["display_name"],
                        "target_teacher": target_teacher,
                        "status": ZoomHostAccount.Status.ACTIVE,
                        "account_type": ZoomHostAccount.AccountType.DEDICATED,
                    })
                    # Mark teacher claimed in this run
                    claimed_teacher_to_host[target_teacher.id] = type(
                        "PlannedHost", (), {"id": None, "zoom_user_id": z_uid}
                    )()
                else:
                    # Existing host evaluation — strict state preservation rules
                    is_conflict = False
                    conflict_reasons = []

                    # Conflict 1: Assigned to a different instructor
                    if existing_host.assigned_instructor_id and existing_host.assigned_instructor_id != target_teacher.id:
                        is_conflict = True
                        conflict_reasons.append(
                            f"Assigned to instructor #{existing_host.assigned_instructor_id} ({existing_host.assigned_instructor.email}), cannot reassign to #{target_teacher.id} ({target_teacher.email})"
                        )

                    # Conflict 2: Inactive/Draining/Suspended preservation
                    # Rule: Never automatically reactivate non-ACTIVE hosts!
                    preserved_status = existing_host.status
                    status_note = ""
                    if existing_host.status != ZoomHostAccount.Status.ACTIVE:
                        status_note = f"Preserving non-ACTIVE status ({existing_host.status}); will NOT reactivate."

                    if is_conflict:
                        conflicts.append({
                            "type": "CONFLICT_ASSIGNMENT_MISMATCH",
                            "zoom_user_id": z_uid,
                            "email": z_email,
                            "host_id": existing_host.id,
                            "detail": "; ".join(conflict_reasons)
                        })
                        continue

                    # Plan safe update: preserve status, update assigned_instructor and account_type if unassigned
                    needs_update = False
                    changes = {}

                    if existing_host.zoom_user_id != z_uid:
                        changes["zoom_user_id"] = (existing_host.zoom_user_id, z_uid)
                        needs_update = True
                    if existing_host.email.lower() != z_email:
                        changes["email"] = (existing_host.email, z_email)
                        needs_update = True
                    if existing_host.account_type != ZoomHostAccount.AccountType.DEDICATED:
                        changes["account_type"] = (existing_host.account_type, ZoomHostAccount.AccountType.DEDICATED)
                        needs_update = True
                    if existing_host.assigned_instructor_id != target_teacher.id:
                        changes["assigned_instructor"] = (existing_host.assigned_instructor_id, target_teacher.id)
                        needs_update = True

                    if needs_update:
                        proposed_updates.append({
                            "host": existing_host,
                            "target_teacher": target_teacher,
                            "preserved_status": preserved_status,
                            "status_note": status_note,
                            "changes": changes,
                            "zoom_user_id": z_uid,
                            "email": z_email,
                            "display_name": zu["display_name"] or existing_host.display_name,
                        })
                    claimed_teacher_to_host[target_teacher.id] = existing_host
            else:
                # Licensed Zoom user with NO matching LMS teacher account
                unmatched_zoom_licensed.append({
                    "zoom_user_id": z_uid,
                    "email": z_email,
                    "display_name": zu["display_name"],
                    "existing_host": existing_host
                })

        # Identify LMS teachers with NO matching Zoom account
        missing_lms_teachers = []
        for norm_e, t_list in lms_email_to_teachers.items():
            if norm_e not in matched_zoom_emails and norm_e not in duplicate_lms_teacher_emails:
                missing_lms_teachers.append({
                    "id": t_list[0].id,
                    "name": t_list[0].get_full_name() or t_list[0].username,
                    "email": norm_e,
                })

        return {
            "proposed_creates": proposed_creates,
            "proposed_updates": proposed_updates,
            "conflicts": conflicts,
            "unmatched_zoom_licensed": unmatched_zoom_licensed,
            "missing_lms_teachers": missing_lms_teachers,
            "duplicate_lms_teacher_emails": duplicate_lms_teacher_emails,
            "total_lms_teachers_raw": total_lms_teachers_raw,
            "total_lms_teachers": len(teachers),
            "excluded_teacher_ids": sorted(list(EXCLUDED_TEACHER_IDS)),
            "matched_teacher_count": len(matched_zoom_emails),
        }

    # -------------------------------------------------------------------------
    # Comprehensive Audit Report Output
    # -------------------------------------------------------------------------
    def _print_audit_report(
        self,
        db_snapshot: Dict[str, Any],
        zoom_inventory: Dict[str, Any],
        recon: Dict[str, Any],
        expected_seats: int
    ):
        licensed_count = len(zoom_inventory["licensed_users"])
        basic_count = len(zoom_inventory["basic_users"])

        self.stdout.write("\n" + "-" * 80)
        self.stdout.write("AUDIT REPORT SUMMARY")
        self.stdout.write("-" * 80)
        self.stdout.write(f"Existing Host Records in DB:    {db_snapshot['total']}")
        self.stdout.write(f"  - Status:  {db_snapshot['status_counts']}")
        self.stdout.write(f"  - Types:   {db_snapshot['type_counts']}")
        self.stdout.write(f"  - Mapped:  {db_snapshot['assigned_count']}")
        self.stdout.write(f"\nZoom API User Inventory:")
        self.stdout.write(f"  - Total Active Users:         {zoom_inventory.get('total_active', zoom_inventory['total_raw'])}")
        self.stdout.write(f"  - Licensed (type=2):          {licensed_count} (Expected: ~{expected_seats})")
        self.stdout.write(f"  - Basic / Ineligible (type=1): {basic_count}")
        self.stdout.write(f"  - Pending Invitations:        {len(zoom_inventory.get('pending_users', []))} (Invited in Zoom, awaiting acceptance)")
        self.stdout.write(f"\nLMS Teachers:")
        self.stdout.write(f"  - Registered Teachers (Total): {recon.get('total_lms_teachers_raw', recon['total_lms_teachers'])}")
        self.stdout.write(f"  - Permanently Excluded:       {recon.get('excluded_teacher_ids', sorted(list(EXCLUDED_TEACHER_IDS)))} (Teacher ID 39: Vijay Pn)")
        self.stdout.write(f"  - Net Eligible Teachers:       {recon['total_lms_teachers']}")
        self.stdout.write(f"  - Exactly Matched with Zoom:  {recon['matched_teacher_count']}")
        self.stdout.write(f"  - Unmatched Eligible Teachers: {len(recon['missing_lms_teachers'])}")
        self.stdout.write(f"\nProposed Actions:")
        self.stdout.write(f"  - New Host Records to Create: {len(recon['proposed_creates'])}")
        self.stdout.write(f"  - Existing Records to Update: {len(recon['proposed_updates'])}")
        self.stdout.write(f"  - Conflicts Requiring Review: {len(recon['conflicts'])}")
        self.stdout.write(f"  - Unmatched Licensed Zoom:    {len(recon['unmatched_zoom_licensed'])}")
        self.stdout.write("-" * 80)

        # Seat Count Warning
        if licensed_count != expected_seats:
            self.stdout.write(self.style.WARNING(
                f"[NOTICE] Retrieved {licensed_count} licensed Zoom users (expected: {expected_seats}). "
                f"Sync will process only verified type=2 users."
            ))

        # Detail: Conflicts
        if recon["conflicts"]:
            self.stdout.write(self.style.ERROR("\n[!] CONFLICTS REQUIRING MANUAL INTERVENTION (Zero auto-overwrites):"))
            for c in recon["conflicts"]:
                self.stdout.write(self.style.ERROR(f"  * [{c['type']}] Email: {c['email']} | UserID: {c.get('zoom_user_id', '')} | Detail: {c['detail']}"))

        # Detail: Proposed Creates
        if recon["proposed_creates"]:
            self.stdout.write(self.style.SUCCESS(f"\n[+] PROPOSED CREATIONS ({len(recon['proposed_creates'])} DEDICATED hosts):"))
            for item in recon["proposed_creates"][:10]:
                self.stdout.write(f"  * CREATE {item['zoom_user_id']} -> {item['email']} -> Teacher #{item['target_teacher'].id} ({item['target_teacher'].get_full_name()})")
            if len(recon["proposed_creates"]) > 10:
                self.stdout.write(f"  ... and {len(recon['proposed_creates']) - 10} more.")

        # Detail: Proposed Updates
        if recon["proposed_updates"]:
            self.stdout.write(self.style.NOTICE(f"\n[*] PROPOSED UPDATES ({len(recon['proposed_updates'])} hosts):"))
            for item in recon["proposed_updates"]:
                note = f" [{item['status_note']}]" if item["status_note"] else ""
                self.stdout.write(f"  * UPDATE Host #{item['host'].id} ({item['email']}): changes={item['changes']}{note}")

        # Detail: Unmatched Zoom Accounts
        if recon["unmatched_zoom_licensed"]:
            self.stdout.write(self.style.WARNING(f"\n[?] LICENSED ZOOM USERS WITHOUT LMS TEACHER MATCH ({len(recon['unmatched_zoom_licensed'])}):"))
            for u in recon["unmatched_zoom_licensed"]:
                self.stdout.write(f"  * {u['zoom_user_id']} | {u['email']} | {u['display_name']} (Ineligible for dedicated mapping)")

        # Detail: Missing LMS Teachers
        if recon["missing_lms_teachers"]:
            self.stdout.write(self.style.WARNING(f"\n[-] LMS TEACHERS WITHOUT ZOOM LICENSED ACCOUNT ({len(recon['missing_lms_teachers'])}):"))
            for t in recon["missing_lms_teachers"][:10]:
                self.stdout.write(f"  * Teacher #{t['id']}: {t['name']} <{t['email']}>")
            if len(recon["missing_lms_teachers"]) > 10:
                self.stdout.write(f"  ... and {len(recon['missing_lms_teachers']) - 10} more.")

    # -------------------------------------------------------------------------
    # Execution (Atomic Database Writes)
    # -------------------------------------------------------------------------
    def _execute_sync(self, recon: Dict[str, Any]):
        created_count = 0
        updated_count = 0

        with transaction.atomic():
            # 1. Execute safe creates
            for item in recon["proposed_creates"]:
                target_teacher = item.get("target_teacher")
                target_id = target_teacher.id if target_teacher else item.get("assigned_instructor_id")
                if target_id in EXCLUDED_TEACHER_IDS:
                    self.stdout.write(self.style.ERROR(
                        f"BLOCKED Creation: Teacher #{target_id} is in EXCLUDED_TEACHER_IDS."
                    ))
                    continue
                host = ZoomHostAccount.objects.create(
                    zoom_user_id=item["zoom_user_id"],
                    email=item["email"],
                    display_name=item["display_name"],
                    status=item["status"],
                    account_type=item["account_type"],
                    assigned_instructor=item["target_teacher"],
                    max_concurrency=1
                )
                created_count += 1
                self.stdout.write(f"Created Host #{host.id}: {host.email} -> Teacher #{item['target_teacher'].id}")

            # 2. Execute safe updates
            for item in recon["proposed_updates"]:
                target_teacher = item.get("target_teacher")
                target_id = target_teacher.id if target_teacher else (item.get("new_assigned_instructor_id") or item.get("assigned_instructor_id"))
                if target_id in EXCLUDED_TEACHER_IDS:
                    self.stdout.write(self.style.ERROR(
                        f"BLOCKED Update: Teacher #{target_id} is in EXCLUDED_TEACHER_IDS."
                    ))
                    continue
                host = ZoomHostAccount.objects.select_for_update().get(id=item["host"].id)

                if host.assigned_instructor_id in EXCLUDED_TEACHER_IDS:
                    self.stdout.write(self.style.ERROR(
                        f"BLOCKED Update: Existing host #{host.id} is already bound to excluded Teacher #{host.assigned_instructor_id}."
                    ))
                    continue

                # Re-verify conflict under lock: Never overwrite conflicting instructor
                if host.assigned_instructor_id and host.assigned_instructor_id != item["target_teacher"].id:
                    self.stdout.write(self.style.ERROR(
                        f"SKIPPED Host #{host.id}: Assigned instructor changed concurrently to #{host.assigned_instructor_id}."
                    ))
                    continue

                # Preserve non-ACTIVE status
                if host.status != item["preserved_status"]:
                    pass  # status remains untouched

                host.zoom_user_id = item["zoom_user_id"]
                host.email = item["email"]
                host.display_name = item["display_name"]
                host.account_type = ZoomHostAccount.AccountType.DEDICATED
                host.assigned_instructor = item["target_teacher"]
                host.save(update_fields=["zoom_user_id", "email", "display_name", "account_type", "assigned_instructor", "updated_at"])
                updated_count += 1
                self.stdout.write(f"Updated Host #{host.id}: {host.email} -> Teacher #{item['target_teacher'].id}")

        self.stdout.write(self.style.SUCCESS(
            f"\nSYNCHRONIZATION COMPLETE: {created_count} host(s) created, {updated_count} host(s) safely updated."
        ))
