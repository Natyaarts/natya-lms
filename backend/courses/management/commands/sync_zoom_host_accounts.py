"""
backend/courses/management/commands/sync_zoom_host_accounts.py

Administrative command to reconcile and synchronize Zoom Workplace Pro licensed host accounts
into courses_zoomhostaccount under strict fail-closed safety invariants.

Default mode is strictly READ-ONLY (--dry-run).
Database mutations require BOTH --commit and --confirm=CONFIRM-ZOOM-HOST-SYNC.
"""
import sys
import re
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

# Excluded LMS User IDs: personal/test accounts (1, 25, 96) and non-teachers (40, 49)
EXCLUDED_TEACHER_IDS = frozenset({1, 25, 96, 40, 49})

# Excluded Zoom tenant owner/administrative accounts
EXCLUDED_ZOOM_EMAILS = frozenset({"vijay.pn@natyaarts.com"})

# Protected existing dedicated host assignments (Teacher IDs whose host records must NEVER be altered, reassigned, or deactivated)
PROTECTED_EXISTING_TEACHER_IDS = frozenset({54, 57, 58, 59, 60, 61, 62, 63})

# Authoritative Built-in Production Explicit Mappings with Identity Fingerprints.
# These teachers hold legitimate Zoom Pro licenses under personal emails, but hold synthetic placeholder
# emails (teacher_X@natya.internal) or distinct verified IDs in the production LMS.
# Mapped strictly by production LMS User ID with identity fingerprint validation (expected full name and expected phone).
BUILTIN_EXPLICIT_TEACHER_MAPPINGS = {
    182: {
        "email": "vaishnavimukundan14@gmail.com",
        "expected_name": "Kalamandalam Vaishnavi",
        "expected_phone": "+916235528825",
    },
    183: {
        "email": "amrithasivakumar3@gmail.com",
        "expected_name": "AMRITHA",
        "expected_phone": "+917356597816",
    },
    184: {
        "email": "panchamibhavadasan@gmail.com",
        "expected_name": "PANCHAMI",
        "expected_phone": "+917736032611",
    },
    186: {
        "email": "devinandanahs@gmail.com",
        "expected_name": "DEVINANDHANA",
        "expected_phone": "+917306266584",
    },
    187: {
        "email": "remitharoshin02@gmail.com",
        "expected_name": "Remitha Roshin",
        "expected_phone": "+919188250451",
    },
    188: {
        "email": "akshayaraveendran4@gmail.com",
        "expected_name": "Akshaya Raveendran",
        "expected_phone": "+919497243775",
    },
    189: {
        "email": "abhinandapadman@gmail.com",
        "expected_name": "ABHINANDA",
        "expected_phone": "+918921902179",
    },
    191: {
        "email": "parvathyks38@gmail.com",
        "expected_name": "Kalamandalam Parvathy Panicker",
        "expected_phone": "+917012019877",
    },
    192: {
        "email": "vidyamnair7@gmail.com",
        "expected_name": "Kalamandalam Vidya M Nair",
        "expected_phone": "+917907139213",
    },
    193: {
        "email": "namithabiju19@gmail.com",
        "expected_name": "Kalakshetra Namitha",
        "expected_phone": "+918921798994",
    },
    194: {
        "email": "anupamamofficial@gmail.com",
        "expected_name": "Kalamandalam Anupama",
        "expected_phone": "+919544530618",
    },
    195: {
        "email": "saranyamurali95@gmail.com",
        "expected_name": "Saranya",
        "expected_phone": "+919645265292",
    },
    196: {
        "email": "jayalakshmiharikumar5060@gmail.com",
        "expected_name": "Kalamandalam Jayalakshmi",
        "expected_phone": "+919544060298",
    },
    53: {
        "email": "sandracheriyakath2003@gmail.com",
        "expected_name": "Sandra",
        "expected_phone": "+919539403570",
    },
}

# Backward compatibility alias
EXPLICIT_TEACHER_ZOOM_MAPPINGS = {
    tid: m["email"] for tid, m in BUILTIN_EXPLICIT_TEACHER_MAPPINGS.items()
}


def _normalize_phone(phone: Optional[str]) -> str:
    """
    Normalizes a phone number for unambiguous identity fingerprint comparison.
    Extracts the 10-digit subscriber number (handling +91 country code and leading zeros).
    """
    if not phone:
        return ""
    digits = re.sub(r"\D", "", str(phone))
    if digits.startswith("91") and len(digits) == 12:
        return digits[2:]
    if digits.startswith("0") and len(digits) == 11:
        return digits[1:]
    return digits


def _normalize_name(name: Optional[str]) -> str:
    """
    Normalizes a teacher's full name by collapsing whitespace and standardizing case.
    """
    if not name:
        return ""
    clean = re.sub(r"[^\w\s]", " ", str(name))
    return " ".join(clean.strip().lower().split())


def _names_match(name1: Optional[str], name2: Optional[str]) -> bool:
    """
    Matches names accounting for case, punctuation (e.g. Sandra.C), and token subsetting.
    """
    n1 = _normalize_name(name1)
    n2 = _normalize_name(name2)
    if not n1 or not n2:
        return False
    if n1 == n2:
        return True
    tokens1 = set(n1.split())
    tokens2 = set(n2.split())
    if tokens1.issubset(tokens2) or tokens2.issubset(tokens1):
        return True
    return False


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
        parser.add_argument(
            "--apply-explicit-mappings",
            action="store_true",
            default=False,
            help="Explicitly opt-in to applying built-in production teacher-to-Zoom mappings with identity fingerprint verification."
        )
        parser.add_argument(
            "--explicit-mappings",
            dest="explicit_mappings",
            default=None,
            help="Optional JSON string (CLI) or dict (tests) of {instructor_id: zoom_email} or {instructor_id: {'email': ..., 'expected_name': ...}} overriding default mappings."
        )

    def handle(self, *args, **options):
        is_commit = options.get("commit", False)
        confirm_phrase = options.get("confirm", "").strip()
        expected_seats = options.get("expected_seats", EXPECTED_LICENSED_SEATS)
        apply_explicit_mappings = options.get("apply_explicit_mappings", False)
        explicit_mappings = options.get("explicit_mappings")

        if is_commit:
            if confirm_phrase != CONFIRMATION_PHRASE:
                raise CommandError(
                    f"Production safety violation: --commit requires exact confirmation phrase "
                    f"--confirm={CONFIRMATION_PHRASE} (got: '{confirm_phrase}'). Aborting without changes."
                )

        self.stdout.write("=" * 80)
        self.stdout.write("NATYA LMS — ZOOM HOST ACCOUNT RECONCILIATION & SYNCHRONIZATION")
        self.stdout.write("=" * 80)

        if not apply_explicit_mappings and explicit_mappings is None:
            self.stdout.write(self.style.NOTICE(
                "[Safety Policy] Built-in explicit mappings are OPT-IN and NOT applied by default.\n"
                "                Run with --apply-explicit-mappings or pass --explicit-mappings='<json>' to enable."
            ))
        elif apply_explicit_mappings and explicit_mappings is None:
            self.stdout.write(self.style.SUCCESS(
                f"[Safety Policy] --apply-explicit-mappings enabled: loading {len(BUILTIN_EXPLICIT_TEACHER_MAPPINGS)} "
                "built-in production teacher mappings with strict identity fingerprint verification."
            ))

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
        reconciliation = self._reconcile(
            db_snapshot=db_snapshot,
            zoom_inventory=zoom_inventory,
            expected_seats=expected_seats,
            explicit_mappings=explicit_mappings,
            apply_builtin_explicit=apply_explicit_mappings,
        )

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
        owner_users = []
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

            if u_email in EXCLUDED_ZOOM_EMAILS:
                owner_users.append(record)
                continue

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
            "owner_users": owner_users,
            "duplicate_zoom_emails": duplicate_zoom_emails,
            "pending_users": pending_users,
        }

    # -------------------------------------------------------------------------
    # Explicit Mapping Parser & Validator
    # -------------------------------------------------------------------------
    def _parse_explicit_mappings(
        self,
        raw_input: Any,
        apply_builtin: bool = False
    ) -> Dict[int, Dict[str, Any]]:
        """
        Parses and normalizes explicit mappings with identity fingerprints.
        Returns a dict of:
          {instructor_id: {'email': str, 'expected_name': Optional[str], 'expected_phone': Optional[str]}}
        """
        if raw_input is None:
            if not apply_builtin:
                return {}
            raw_dict = BUILTIN_EXPLICIT_TEACHER_MAPPINGS
        elif isinstance(raw_input, dict):
            raw_dict = raw_input
        elif isinstance(raw_input, str):
            import json
            try:
                parsed = json.loads(raw_input)
                if not isinstance(parsed, dict):
                    raise ValueError("JSON must decode to an object/dict")
                raw_dict = parsed
            except Exception as exc:
                raise CommandError(
                    f"Invalid --explicit-mappings format. Must be a JSON object mapping instructor IDs to emails/fingerprints: {exc}"
                )
        else:
            raise CommandError(
                f"Invalid --explicit-mappings type ({type(raw_input).__name__}). Must be a dict or JSON string."
            )

        norm_mappings = {}
        for k, v in raw_dict.items():
            try:
                tid = int(k)
            except (ValueError, TypeError):
                raise CommandError(f"Invalid instructor ID in explicit mappings: {k!r}. Must be an integer.")

            if isinstance(v, str):
                if "@" not in v:
                    raise CommandError(f"Invalid Zoom email in explicit mappings for instructor #{tid}: {v!r}.")
                norm_mappings[tid] = {
                    "email": v.strip().lower(),
                    "expected_name": None,
                    "expected_phone": None,
                }
            elif isinstance(v, dict):
                email = v.get("email")
                if not isinstance(email, str) or "@" not in email:
                    raise CommandError(f"Invalid Zoom email in explicit mapping dict for instructor #{tid}: {email!r}.")
                norm_mappings[tid] = {
                    "email": email.strip().lower(),
                    "expected_name": v.get("expected_name"),
                    "expected_phone": v.get("expected_phone"),
                }
            else:
                raise CommandError(
                    f"Invalid mapping value for instructor #{tid} ({type(v).__name__}). Must be email string or dict."
                )

        return norm_mappings

    # -------------------------------------------------------------------------
    # Multi-Key Reconciliation & Conflict Detection
    # -------------------------------------------------------------------------
    def _reconcile(
        self,
        db_snapshot: Dict[str, Any],
        zoom_inventory: Dict[str, Any],
        expected_seats: int,
        explicit_mappings: Optional[Any] = None,
        apply_builtin_explicit: bool = False
    ) -> Dict[str, Any]:
        User = get_user_model()

        # Parse and normalize explicit mappings safely
        norm_explicit_mappings = self._parse_explicit_mappings(
            explicit_mappings,
            apply_builtin=apply_builtin_explicit
        )

        conflicts = []
        valid_explicit_teachers = {}
        explicit_zoom_to_tids = defaultdict(list)
        invalid_explicit_zoom_emails = set()
        blocked_explicit_teacher_ids = set()

        # Validate explicit mappings targets against strict invariants:
        for tid, m_data in norm_explicit_mappings.items():
            z_mail = m_data["email"]
            exp_name = m_data.get("expected_name")
            exp_phone = m_data.get("expected_phone")

            # Invariant 1: Excluded teacher IDs must NEVER be in explicit mappings
            if tid in EXCLUDED_TEACHER_IDS:
                conflicts.append({
                    "type": "CONFLICT_EXCLUDED_TEACHER",
                    "zoom_user_id": "",
                    "email": z_mail,
                    "instructor_id": tid,
                    "detail": f"Teacher #{tid} is permanently excluded in EXCLUDED_TEACHER_IDS; cannot map to Zoom email '{z_mail}'."
                })
                invalid_explicit_zoom_emails.add(z_mail)
                blocked_explicit_teacher_ids.add(tid)
                continue

            # Invariant 1a: Protected existing-host teacher IDs must NEVER be altered or reassigned via explicit mappings
            if tid in PROTECTED_EXISTING_TEACHER_IDS:
                conflicts.append({
                    "type": "CONFLICT_PROTECTED_HOST_REASSIGNMENT",
                    "zoom_user_id": "",
                    "email": z_mail,
                    "instructor_id": tid,
                    "detail": f"Teacher #{tid} is a protected existing-host teacher in PROTECTED_EXISTING_TEACHER_IDS; cannot alter or reassign."
                })
                invalid_explicit_zoom_emails.add(z_mail)
                blocked_explicit_teacher_ids.add(tid)
                continue

            # Invariant 1b: Excluded Zoom emails (such as tenant owner) must NEVER be in explicit mappings
            if z_mail in EXCLUDED_ZOOM_EMAILS:
                conflicts.append({
                    "type": "CONFLICT_EXCLUDED_ZOOM_OWNER",
                    "zoom_user_id": "",
                    "email": z_mail,
                    "instructor_id": tid,
                    "detail": f"Zoom email '{z_mail}' is the protected tenant owner account; cannot be allocated as a teacher host."
                })
                invalid_explicit_zoom_emails.add(z_mail)
                blocked_explicit_teacher_ids.add(tid)
                continue

            # Invariant 2: Target LMS User must exist in database
            try:
                user_obj = User.objects.get(id=tid)
            except User.DoesNotExist:
                conflicts.append({
                    "type": "CONFLICT_EXPLICIT_MAPPING_USER_NOT_FOUND",
                    "zoom_user_id": "",
                    "email": z_mail,
                    "instructor_id": tid,
                    "detail": f"Explicitly mapped user #{tid} does not exist in LMS database."
                })
                invalid_explicit_zoom_emails.add(z_mail)
                blocked_explicit_teacher_ids.add(tid)
                continue

            # Invariant 3: Target LMS User must be a teacher (is_teacher == True)
            if not getattr(user_obj, "is_teacher", False):
                conflicts.append({
                    "type": "CONFLICT_EXPLICIT_MAPPING_NOT_TEACHER",
                    "zoom_user_id": "",
                    "email": z_mail,
                    "instructor_id": tid,
                    "detail": f"Explicitly mapped user #{tid} ({user_obj.username}) is not a teacher (is_teacher=False)."
                })
                invalid_explicit_zoom_emails.add(z_mail)
                blocked_explicit_teacher_ids.add(tid)
                continue

            # Invariant 4: Target LMS Teacher must be active (is_active == True)
            if not getattr(user_obj, "is_active", True):
                conflicts.append({
                    "type": "CONFLICT_EXPLICIT_MAPPING_USER_INACTIVE",
                    "zoom_user_id": "",
                    "email": z_mail,
                    "instructor_id": tid,
                    "detail": f"Explicitly mapped teacher #{tid} ({user_obj.username}) is inactive (is_active=False)."
                })
                invalid_explicit_zoom_emails.add(z_mail)
                blocked_explicit_teacher_ids.add(tid)
                continue

            # Invariant 5: Expected Teacher Name Fingerprint Match
            if exp_name:
                user_full_name = user_obj.get_full_name().strip() or f"{user_obj.first_name} {user_obj.last_name}".strip()
                if not _names_match(user_full_name, exp_name):
                    conflicts.append({
                        "type": "CONFLICT_EXPLICIT_MAPPING_NAME_MISMATCH",
                        "zoom_user_id": "",
                        "email": z_mail,
                        "instructor_id": tid,
                        "detail": f"Explicit mapping identity fingerprint mismatch for teacher #{tid}: expected name '{exp_name}', but found '{user_full_name}'."
                    })
                    invalid_explicit_zoom_emails.add(z_mail)
                    blocked_explicit_teacher_ids.add(tid)
                    continue

            # Invariant 6: Expected Registered Phone Fingerprint Match
            if exp_phone:
                user_phone = getattr(user_obj, "phone_number", "") or ""
                if not user_phone:
                    conflicts.append({
                        "type": "CONFLICT_EXPLICIT_MAPPING_PHONE_MISMATCH",
                        "zoom_user_id": "",
                        "email": z_mail,
                        "instructor_id": tid,
                        "detail": f"Explicit mapping identity fingerprint mismatch for teacher #{tid}: expected phone '{exp_phone}', but user has no phone number recorded."
                    })
                    invalid_explicit_zoom_emails.add(z_mail)
                    blocked_explicit_teacher_ids.add(tid)
                    continue
                if _normalize_phone(user_phone) != _normalize_phone(exp_phone):
                    conflicts.append({
                        "type": "CONFLICT_EXPLICIT_MAPPING_PHONE_MISMATCH",
                        "zoom_user_id": "",
                        "email": z_mail,
                        "instructor_id": tid,
                        "detail": f"Explicit mapping identity fingerprint mismatch for teacher #{tid}: expected phone '{exp_phone}', but found '{user_phone}'."
                    })
                    invalid_explicit_zoom_emails.add(z_mail)
                    blocked_explicit_teacher_ids.add(tid)
                    continue

            valid_explicit_teachers[tid] = user_obj
            explicit_zoom_to_tids[z_mail].append(tid)

        # Invariant 7: Zoom email uniqueness in explicit mappings (never map the same Zoom account to multiple teachers)
        duplicate_explicit_zoom_emails = {
            z_mail: tids for z_mail, tids in explicit_zoom_to_tids.items() if len(tids) > 1
        }
        for z_mail, tids in duplicate_explicit_zoom_emails.items():
            conflicts.append({
                "type": "CONFLICT_DUPLICATE_EXPLICIT_ZOOM_MAPPING",
                "zoom_user_id": "",
                "email": z_mail,
                "detail": f"Multiple LMS teachers {tids} are mapped to the same Zoom email '{z_mail}'."
            })
            invalid_explicit_zoom_emails.add(z_mail)
            for dup_tid in tids:
                blocked_explicit_teacher_ids.add(dup_tid)
                valid_explicit_teachers.pop(dup_tid, None)

        # 1. Index active LMS Teachers and construct consistent, non-overlapping teacher populations
        raw_teachers_qs = list(
            User.objects.filter(is_teacher=True, is_active=True).order_by("id")
        )
        raw_active_by_id = {t.id: t for t in raw_teachers_qs}
        for tid, t_obj in valid_explicit_teachers.items():
            if getattr(t_obj, "is_teacher", False) and getattr(t_obj, "is_active", True):
                raw_active_by_id.setdefault(tid, t_obj)

        raw_teachers = list(raw_active_by_id.values())
        total_lms_teachers_raw = len(raw_teachers)

        excluded_active_teachers = [
            t for t in raw_teachers if t.id in EXCLUDED_TEACHER_IDS
        ]
        protected_active_teachers = [
            t for t in raw_teachers
            if t.id not in EXCLUDED_TEACHER_IDS and t.id in PROTECTED_EXISTING_TEACHER_IDS
        ]
        existing_protected_hosts = [
            h for h in db_snapshot["hosts"]
            if h.assigned_instructor_id in PROTECTED_EXISTING_TEACHER_IDS
        ]

        # Build deduplicated eligible teacher set (keyed by teacher ID to prevent double-counting)
        eligible_by_id = {
            t.id: t
            for t in raw_teachers
            if t.id not in EXCLUDED_TEACHER_IDS and t.id not in PROTECTED_EXISTING_TEACHER_IDS
        }
        # Merge verified valid explicit teachers without double-counting
        for tid, t_obj in valid_explicit_teachers.items():
            if (
                tid not in EXCLUDED_TEACHER_IDS
                and tid not in PROTECTED_EXISTING_TEACHER_IDS
                and tid not in blocked_explicit_teacher_ids
            ):
                eligible_by_id[tid] = t_obj

        teachers = list(eligible_by_id.values())
        teacher_by_id = dict(eligible_by_id)

        lms_email_to_teachers = defaultdict(list)
        for t in teachers:
            if t.id in blocked_explicit_teacher_ids or t.id in valid_explicit_teachers:
                continue
            norm_e = (t.email or "").strip().lower()
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
        unmatched_zoom_licensed = []
        matched_zoom_emails = set()
        seen_licensed_zoom_emails = set()
        matched_teacher_ids = set()

        for zu in zoom_inventory["licensed_users"]:
            z_uid = zu["id"]
            z_email = zu["email"]
            seen_licensed_zoom_emails.add(z_email)

            # Ambiguity Check: Duplicate Zoom email
            if z_email in dup_zoom_emails:
                conflicts.append({
                    "type": "AMBIGUOUS_DUPLICATE_ZOOM_EMAIL",
                    "zoom_user_id": z_uid,
                    "email": z_email,
                    "detail": f"Zoom API returned {len(dup_zoom_emails[z_email])} records for email '{z_email}'."
                })
                continue

            # Hard safety invariant: If this Zoom email belongs to an explicit mapping that failed validation,
            # FAIL CLOSED! Never allow fallback matching (e.g. email matching) to bypass the explicit mapping failure.
            if z_email in invalid_explicit_zoom_emails:
                unmatched_zoom_licensed.append({
                    "zoom_user_id": z_uid,
                    "email": z_email,
                    "display_name": zu["display_name"],
                    "existing_host": existing_by_uid.get(z_uid) or (existing_by_email.get(z_email, [None])[0]),
                    "reason": "Explicit mapping rejected due to validation conflict"
                })
                continue

            # Ambiguity Check: Duplicate LMS teacher email (applies only if not explicitly mapped)
            if z_email not in explicit_zoom_to_tids and z_email in duplicate_lms_teacher_emails:
                conflicts.append({
                    "type": "AMBIGUOUS_DUPLICATE_LMS_TEACHER_EMAIL",
                    "zoom_user_id": z_uid,
                    "email": z_email,
                    "detail": f"LMS has {len(duplicate_lms_teacher_emails[z_email])} teachers sharing email '{z_email}'."
                })
                continue

            # Check if matching LMS teacher exists:
            # Priority A: Explicit synthetic-email mapping by production LMS instructor ID
            target_teacher = None
            if z_email in explicit_zoom_to_tids:
                if z_email in duplicate_explicit_zoom_emails:
                    continue  # already flagged in conflicts
                mapped_tid = explicit_zoom_to_tids[z_email][0]
                if (
                    mapped_tid in EXCLUDED_TEACHER_IDS
                    or mapped_tid in PROTECTED_EXISTING_TEACHER_IDS
                    or mapped_tid in blocked_explicit_teacher_ids
                ):
                    continue  # already flagged in conflicts
                target_teacher = valid_explicit_teachers.get(mapped_tid)
            else:
                # Priority B: Exact email match
                lms_teachers = lms_email_to_teachers.get(z_email, [])
                target_teacher = lms_teachers[0] if lms_teachers else None

            # Hard safety invariant: Never allow an excluded, protected, blocked, non-teacher, or inactive teacher to be target
            if target_teacher:
                if (target_teacher.id in EXCLUDED_TEACHER_IDS or
                    target_teacher.id in PROTECTED_EXISTING_TEACHER_IDS or
                    target_teacher.id in blocked_explicit_teacher_ids or
                    not getattr(target_teacher, "is_teacher", False) or
                    not getattr(target_teacher, "is_active", True)):
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

            # Safety Guard: Existing host bound to a protected teacher (54, 57, 58, 59, 60, 61, 62, 63)
            # NEVER alter, reassign, or deactivate!
            protected_host = None
            if existing_host_by_id and existing_host_by_id.assigned_instructor_id in PROTECTED_EXISTING_TEACHER_IDS:
                protected_host = existing_host_by_id
            elif existing_hosts_by_email and any(h.assigned_instructor_id in PROTECTED_EXISTING_TEACHER_IDS for h in existing_hosts_by_email):
                protected_host = next(h for h in existing_hosts_by_email if h.assigned_instructor_id in PROTECTED_EXISTING_TEACHER_IDS)

            if protected_host:
                if target_teacher and target_teacher.id != protected_host.assigned_instructor_id:
                    conflicts.append({
                        "type": "CONFLICT_PROTECTED_HOST_REASSIGNMENT",
                        "zoom_user_id": z_uid,
                        "email": z_email,
                        "host_id": protected_host.id,
                        "detail": f"Existing host #{protected_host.id} is bound to protected Teacher #{protected_host.assigned_instructor_id}; cannot alter or reassign."
                    })
                    continue
                # Protected existing host belongs to this protected teacher; preserve completely unchanged.
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

                if not existing_host:
                    matched_zoom_emails.add(z_email)
                    matched_teacher_ids.add(target_teacher.id)
                    # Clean new DEDICATED host creation
                    proposed_creates.append({
                        "zoom_user_id": z_uid,
                        "email": z_email,
                        "display_name": zu["display_name"],
                        "target_teacher": target_teacher,
                        "status": ZoomHostAccount.Status.ACTIVE,
                        "account_type": ZoomHostAccount.AccountType.DEDICATED,
                        "max_concurrency": 1,
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
                            f"Assigned to instructor #{existing_host.assigned_instructor_id} ({getattr(existing_host.assigned_instructor, 'email', '')}), cannot reassign to #{target_teacher.id} ({target_teacher.email})"
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

                    matched_zoom_emails.add(z_email)
                    matched_teacher_ids.add(target_teacher.id)

                    # Plan safe update: preserve status, update assigned_instructor, account_type, max_concurrency
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
                    if existing_host.max_concurrency != 1:
                        changes["max_concurrency"] = (existing_host.max_concurrency, 1)
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
                            "max_concurrency": 1,
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

        # Check for missing explicit Zoom accounts:
        # Every verified valid teacher in explicit mappings MUST match an active licensed user in Zoom
        for tid, m_data in norm_explicit_mappings.items():
            z_mail = m_data["email"]
            if tid in valid_explicit_teachers and z_mail not in seen_licensed_zoom_emails:
                basic_match = next((u for u in zoom_inventory.get("basic_users", []) if u["email"] == z_mail), None)
                pending_match = next((u for u in zoom_inventory.get("pending_users", []) if u["email"] == z_mail), None)
                if basic_match:
                    reason = f"Zoom user '{z_mail}' exists but has Basic (type=1) license; requires Workplace Pro (type=2)."
                elif pending_match:
                    reason = f"Zoom user '{z_mail}' exists but is in Pending invitation status; invitation must be accepted."
                else:
                    reason = f"Zoom user '{z_mail}' was not found in Zoom tenant user inventory."

                conflicts.append({
                    "type": "CONFLICT_EXPLICIT_ZOOM_ACCOUNT_MISSING",
                    "zoom_user_id": "",
                    "email": z_mail,
                    "instructor_id": tid,
                    "detail": f"Explicitly mapped Zoom account for Teacher #{tid} ({z_mail}) is unavailable: {reason}"
                })

        # Identify eligible LMS teachers with NO matching Zoom account (using the exact same eligible_teachers population)
        missing_lms_teachers = [
            {
                "id": t.id,
                "name": t.get_full_name() or t.username,
                "email": t.email,
            }
            for t in teachers
            if t.id not in matched_teacher_ids
        ]

        return {
            "proposed_creates": proposed_creates,
            "proposed_updates": proposed_updates,
            "conflicts": conflicts,
            "unmatched_zoom_licensed": unmatched_zoom_licensed,
            "missing_lms_teachers": missing_lms_teachers,
            "duplicate_lms_teacher_emails": duplicate_lms_teacher_emails,
            "raw_active_teachers_count": total_lms_teachers_raw,
            "total_lms_teachers_raw": total_lms_teachers_raw,
            "excluded_teachers_count": len(excluded_active_teachers),
            "excluded_teacher_ids": sorted(list(EXCLUDED_TEACHER_IDS)),
            "protected_existing_host_teachers_count": len(protected_active_teachers),
            "protected_teacher_ids": sorted(list(PROTECTED_EXISTING_TEACHER_IDS)),
            "blocked_explicit_teacher_ids": sorted(list(blocked_explicit_teacher_ids)),
            "eligible_teachers_count": len(teachers),
            "total_lms_teachers": len(teachers),
            "eligible_teachers_matched_count": len(matched_teacher_ids),
            "matched_teacher_count": len(matched_teacher_ids),
            "eligible_teachers_unmatched_count": len(missing_lms_teachers),
            "existing_protected_hosts_count": len(existing_protected_hosts),
            "proposed_new_host_creations_count": len(proposed_creates),
            "explicit_mappings_count": len(norm_explicit_mappings),
            "explicit_mappings_enabled": bool(explicit_mappings is not None or apply_builtin_explicit),
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
        raw_active_count = recon.get("raw_active_teachers_count", recon.get("total_lms_teachers_raw", recon["total_lms_teachers"]))
        excluded_count = recon.get("excluded_teachers_count", 0)
        excluded_ids = recon.get("excluded_teacher_ids", sorted(list(EXCLUDED_TEACHER_IDS)))
        protected_teachers_count = recon.get("protected_existing_host_teachers_count", 0)
        protected_ids = recon.get("protected_teacher_ids", sorted(list(PROTECTED_EXISTING_TEACHER_IDS)))
        eligible_count = recon.get("eligible_teachers_count", recon["total_lms_teachers"])
        matched_count = recon.get("eligible_teachers_matched_count", recon["matched_teacher_count"])
        unmatched_count = recon.get("eligible_teachers_unmatched_count", len(recon["missing_lms_teachers"]))
        existing_protected_hosts_count = recon.get(
            "existing_protected_hosts_count",
            sum(1 for h in db_snapshot.get("hosts", []) if h.assigned_instructor_id in PROTECTED_EXISTING_TEACHER_IDS)
        )
        proposed_creates_count = recon.get("proposed_new_host_creations_count", len(recon["proposed_creates"]))

        self.stdout.write("\n" + "-" * 80)
        self.stdout.write("AUDIT REPORT SUMMARY")
        self.stdout.write("-" * 80)
        self.stdout.write(f"Existing Host Records in DB:    {db_snapshot['total']}")
        self.stdout.write(f"  - Status:  {db_snapshot['status_counts']}")
        self.stdout.write(f"  - Types:   {db_snapshot['type_counts']}")
        self.stdout.write(f"  - Mapped:  {db_snapshot['assigned_count']}")
        self.stdout.write(f"  - Existing Protected Hosts:   {existing_protected_hosts_count} (Teacher IDs: {protected_ids})")
        self.stdout.write(f"\nZoom API User Inventory:")
        self.stdout.write(f"  - Total Active Users:         {zoom_inventory.get('total_active', zoom_inventory['total_raw'])}")
        self.stdout.write(f"  - Licensed (type=2):          {licensed_count} (Expected: ~{expected_seats})")
        self.stdout.write(f"  - Excluded Owner Accounts:    {len(zoom_inventory.get('owner_users', []))} (Tenant owner: vijay.pn@natyaarts.com)")
        self.stdout.write(f"  - Basic / Ineligible (type=1): {basic_count}")
        self.stdout.write(f"  - Pending Invitations:        {len(zoom_inventory.get('pending_users', []))} (Invited in Zoom, awaiting acceptance)")
        self.stdout.write(f"\nLMS Teachers:")
        self.stdout.write(f"  - Raw Active Teachers:        {raw_active_count}")
        self.stdout.write(f"  - Permanently Excluded:       {excluded_count} (IDs: {excluded_ids} - Personal test accounts: 1, 25, 96; non-teachers: 40, 49)")
        self.stdout.write(f"  - Protected Existing-Host Teachers: {protected_teachers_count} (IDs: {protected_ids})")
        
        explicit_enabled = recon.get("explicit_mappings_enabled", False)
        explicit_count = recon.get("explicit_mappings_count", 0)
        if explicit_enabled:
            explicit_desc = f"{explicit_count} applied (opt-in enabled)"
        else:
            explicit_desc = "OPT-IN DISABLED (0 applied; pass --apply-explicit-mappings to enable)"
        self.stdout.write(f"  - Explicit Synthetic Mappings:{explicit_desc}")

        self.stdout.write(f"  - Eligible Teachers:          {eligible_count}")
        self.stdout.write(f"  - Eligible Teachers Matched:  {matched_count}")
        self.stdout.write(f"  - Eligible Teachers Without Zoom Accounts: {unmatched_count}")
        self.stdout.write(f"\nProposed Actions:")
        self.stdout.write(f"  - Existing Protected Hosts:   {existing_protected_hosts_count} (Preserved unchanged)")
        self.stdout.write(f"  - Proposed New Host Creations:{proposed_creates_count}")
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
                if (not target_teacher or
                    target_id in EXCLUDED_TEACHER_IDS or
                    target_id in PROTECTED_EXISTING_TEACHER_IDS or
                    target_id in recon.get("blocked_explicit_teacher_ids", []) or
                    not getattr(target_teacher, "is_teacher", False) or
                    not getattr(target_teacher, "is_active", True)):
                    self.stdout.write(self.style.ERROR(
                        f"BLOCKED Creation: Teacher #{target_id} is excluded, protected, blocked, inactive, or not a teacher."
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
                if (not target_teacher or
                    target_id in EXCLUDED_TEACHER_IDS or
                    target_id in recon.get("blocked_explicit_teacher_ids", []) or
                    not getattr(target_teacher, "is_teacher", False) or
                    not getattr(target_teacher, "is_active", True)):
                    self.stdout.write(self.style.ERROR(
                        f"BLOCKED Update: Teacher #{target_id} is excluded, blocked, inactive, or not a teacher."
                    ))
                    continue
                host = ZoomHostAccount.objects.select_for_update().get(id=item["host"].id)

                if host.assigned_instructor_id in EXCLUDED_TEACHER_IDS:
                    self.stdout.write(self.style.ERROR(
                        f"BLOCKED Update: Existing host #{host.id} is already bound to excluded Teacher #{host.assigned_instructor_id}."
                    ))
                    continue

                # Protect all eight existing host records completely.
                # In the execution loop, skip every proposed update whose existing host is assigned to one of these IDs,
                # even when the proposed target teacher is the same. Preserve all existing fields unchanged.
                if host.assigned_instructor_id in PROTECTED_EXISTING_TEACHER_IDS:
                    self.stdout.write(self.style.NOTICE(
                        f"SKIPPED Update: Existing host #{host.id} is bound to protected Teacher #{host.assigned_instructor_id}; preserved unchanged."
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
                host.max_concurrency = 1
                host.save(update_fields=["zoom_user_id", "email", "display_name", "account_type", "assigned_instructor", "max_concurrency", "updated_at"])
                updated_count += 1
                self.stdout.write(f"Updated Host #{host.id}: {host.email} -> Teacher #{item['target_teacher'].id}")

        self.stdout.write(self.style.SUCCESS(
            f"\nSYNCHRONIZATION COMPLETE: {created_count} host(s) created, {updated_count} host(s) safely updated."
        ))
