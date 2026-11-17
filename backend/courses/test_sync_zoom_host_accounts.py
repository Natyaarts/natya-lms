"""
backend/courses/test_sync_zoom_host_accounts.py

Automated test suite verifying the administrative command `sync_zoom_host_accounts`
and ZoomService.list_users under strict fail-closed safety invariants:
- Read-only dry-run enforcement by default (zero DB mutations).
- Mandatory confirmation phrase guard (--confirm=CONFIRM-ZOOM-HOST-SYNC).
- State preservation: never reactivate INACTIVE, DRAINING, or SUSPENDED hosts.
- Multi-key conflict detection: dual-key (zoom_user_id + email) integrity checks.
- Ambiguity detection: duplicate LMS teacher emails and duplicate Zoom emails.
- Assignment protection: never overwrite conflicting instructor assignments.
- 1-to-1 bijection protection: prevents multiple hosts mapped to one teacher.
- Zoom API pagination via next_page_token and filtering for licensed users (type=2).
- Fail-closed behavior on Zoom API errors or malformed payloads.
"""
import datetime
from io import StringIO
from unittest import mock

import requests
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from courses.models import Course, LiveBatch, LiveClass, ZoomHostAccount, HostReservation, ZoomMeetingRecord
from courses.services.zoom import ZoomService, ZoomAPIError, ZoomTransientError
from courses.services.zoom_lifecycle import (
    select_and_reserve_host,
    cancel_zoom_meeting,
    HostCapacityExhaustedError,
    ZoomLifecycleError,
)
from courses.views import LiveClassViewSet
from courses.management.commands.sync_zoom_host_accounts import (
    Command,
    EXCLUDED_TEACHER_IDS,
    EXCLUDED_ZOOM_EMAILS,
    PROTECTED_EXISTING_TEACHER_IDS,
    EXPLICIT_TEACHER_ZOOM_MAPPINGS,
    BUILTIN_EXPLICIT_TEACHER_MAPPINGS,
)


User = get_user_model()

TEST_ZOOM_CREDS = {
    "ZOOM_ACCOUNT_ID": "acct_test",
    "ZOOM_CLIENT_ID": "client_test",
    "ZOOM_CLIENT_SECRET": "secret_test",
}


@override_settings(**TEST_ZOOM_CREDS)
class SyncZoomHostAccountsCommandTests(TestCase):
    def setUp(self):
        super().setUp()
        self.teacher1 = User.objects.create_user(
            username="teacher1",
            email="teacher1@natyaarts.com",
            first_name="Radha",
            last_name="Rao",
            is_teacher=True
        )
        self.teacher2 = User.objects.create_user(
            username="teacher2",
            email="teacher2@natyaarts.com",
            first_name="Krishna",
            last_name="Menon",
            is_teacher=True
        )
        self.teacher3 = User.objects.create_user(
            username="teacher3",
            email="teacher3@natyaarts.com",
            first_name="Meera",
            last_name="Devi",
            is_teacher=True
        )

    def _sample_zoom_users(self):
        return [
            {
                "id": "zoom_t1",
                "email": "teacher1@natyaarts.com",
                "first_name": "Radha",
                "last_name": "Rao",
                "display_name": "Radha Rao",
                "type": 2,
                "status": "active",
            },
            {
                "id": "zoom_t2",
                "email": "teacher2@natyaarts.com",
                "first_name": "Krishna",
                "last_name": "Menon",
                "display_name": "Krishna Menon",
                "type": 2,
                "status": "active",
            },
            {
                "id": "zoom_t3",
                "email": "teacher3@natyaarts.com",
                "first_name": "Meera",
                "last_name": "Devi",
                "display_name": "Meera Devi",
                "type": 2,
                "status": "active",
            },
        ]

    # -------------------------------------------------------------------------
    # 1. Safety & Execution Boundary Guards
    # -------------------------------------------------------------------------
    @mock.patch.object(ZoomService, "list_users")
    def test_dry_run_is_default_and_performs_no_database_writes(self, mock_list_users):
        mock_list_users.return_value = self._sample_zoom_users()
        out = StringIO()

        call_command("sync_zoom_host_accounts", stdout=out)

        self.assertIn("DRY-RUN COMPLETE: Zero database modifications were performed", out.getvalue())
        self.assertEqual(ZoomHostAccount.objects.count(), 0)

    @mock.patch.object(ZoomService, "list_users")
    def test_commit_without_confirmation_phrase_fails_closed(self, mock_list_users):
        mock_list_users.return_value = self._sample_zoom_users()

        with self.assertRaises(CommandError) as ctx:
            call_command("sync_zoom_host_accounts", commit=True)

        self.assertIn("CONFIRM-ZOOM-HOST-SYNC", str(ctx.exception))
        self.assertEqual(ZoomHostAccount.objects.count(), 0)

    @mock.patch.object(ZoomService, "list_users")
    def test_commit_with_invalid_confirmation_phrase_fails_closed(self, mock_list_users):
        mock_list_users.return_value = self._sample_zoom_users()

        with self.assertRaises(CommandError) as ctx:
            call_command("sync_zoom_host_accounts", commit=True, confirm="WRONG_PHRASE")

        self.assertIn("Production safety violation", str(ctx.exception))
        self.assertEqual(ZoomHostAccount.objects.count(), 0)

    @mock.patch.object(ZoomService, "list_users")
    def test_commit_with_exact_confirmation_phrase_creates_dedicated_hosts(self, mock_list_users):
        mock_list_users.return_value = self._sample_zoom_users()
        out = StringIO()

        call_command(
            "sync_zoom_host_accounts",
            commit=True,
            confirm="CONFIRM-ZOOM-HOST-SYNC",
            stdout=out
        )

        self.assertIn("SYNCHRONIZATION COMPLETE: 3 host(s) created, 0 host(s) safely updated", out.getvalue())
        self.assertEqual(ZoomHostAccount.objects.count(), 3)

        h1 = ZoomHostAccount.objects.get(zoom_user_id="zoom_t1")
        self.assertEqual(h1.email, "teacher1@natyaarts.com")
        self.assertEqual(h1.status, ZoomHostAccount.Status.ACTIVE)
        self.assertEqual(h1.account_type, ZoomHostAccount.AccountType.DEDICATED)
        self.assertEqual(h1.assigned_instructor, self.teacher1)

        h2 = ZoomHostAccount.objects.get(zoom_user_id="zoom_t2")
        self.assertEqual(h2.assigned_instructor, self.teacher2)

    # -------------------------------------------------------------------------
    # 2. License Type Filtering (Workplace Pro type=2 only)
    # -------------------------------------------------------------------------
    @mock.patch.object(ZoomService, "list_users")
    def test_basic_zoom_users_type_1_are_filtered_out(self, mock_list_users):
        users = self._sample_zoom_users()
        users[1]["type"] = 1  # teacher 2 has Basic license
        mock_list_users.return_value = users
        out = StringIO()

        call_command(
            "sync_zoom_host_accounts",
            commit=True,
            confirm="CONFIRM-ZOOM-HOST-SYNC",
            stdout=out
        )

        # Only teacher 1 and teacher 3 (type=2) should be created
        self.assertEqual(ZoomHostAccount.objects.count(), 2)
        self.assertTrue(ZoomHostAccount.objects.filter(zoom_user_id="zoom_t1").exists())
        self.assertFalse(ZoomHostAccount.objects.filter(zoom_user_id="zoom_t2").exists())
        self.assertTrue(ZoomHostAccount.objects.filter(zoom_user_id="zoom_t3").exists())

    # -------------------------------------------------------------------------
    # 3. Status Preservation (Never reactivate INACTIVE, DRAINING, SUSPENDED)
    # -------------------------------------------------------------------------
    @mock.patch.object(ZoomService, "list_users")
    def test_preserves_non_active_status_inactive_draining_suspended(self, mock_list_users):
        h_inactive = ZoomHostAccount.objects.create(
            zoom_user_id="zoom_t1",
            email="teacher1@natyaarts.com",
            status=ZoomHostAccount.Status.INACTIVE,
            account_type=ZoomHostAccount.AccountType.DEDICATED,
            assigned_instructor=self.teacher1
        )
        h_draining = ZoomHostAccount.objects.create(
            zoom_user_id="zoom_t2",
            email="teacher2@natyaarts.com",
            status=ZoomHostAccount.Status.DRAINING,
            account_type=ZoomHostAccount.AccountType.DEDICATED,
            assigned_instructor=self.teacher2
        )
        h_suspended = ZoomHostAccount.objects.create(
            zoom_user_id="zoom_t3",
            email="teacher3@natyaarts.com",
            status=ZoomHostAccount.Status.SUSPENDED,
            account_type=ZoomHostAccount.AccountType.DEDICATED,
            assigned_instructor=self.teacher3
        )

        mock_list_users.return_value = self._sample_zoom_users()
        out = StringIO()

        call_command(
            "sync_zoom_host_accounts",
            commit=True,
            confirm="CONFIRM-ZOOM-HOST-SYNC",
            stdout=out
        )

        h_inactive.refresh_from_db()
        h_draining.refresh_from_db()
        h_suspended.refresh_from_db()

        self.assertEqual(h_inactive.status, ZoomHostAccount.Status.INACTIVE)
        self.assertEqual(h_draining.status, ZoomHostAccount.Status.DRAINING)
        self.assertEqual(h_suspended.status, ZoomHostAccount.Status.SUSPENDED)

    # -------------------------------------------------------------------------
    # 4. Ambiguity Detection (Duplicate Teacher Emails & Duplicate Zoom Emails)
    # -------------------------------------------------------------------------
    @mock.patch.object(ZoomService, "list_users")
    def test_detects_duplicate_lms_teacher_emails_and_flags_ambiguity(self, mock_list_users):
        # Create a second teacher sharing teacher1's email
        User.objects.create_user(
            username="teacher1_clone",
            email="teacher1@natyaarts.com",
            is_teacher=True
        )

        mock_list_users.return_value = self._sample_zoom_users()
        out = StringIO()

        call_command(
            "sync_zoom_host_accounts",
            commit=True,
            confirm="CONFIRM-ZOOM-HOST-SYNC",
            stdout=out
        )

        # teacher1 has ambiguous LMS identity -> skipped with conflict!
        self.assertIn("AMBIGUOUS_DUPLICATE_LMS_TEACHER_EMAIL", out.getvalue())
        self.assertFalse(ZoomHostAccount.objects.filter(zoom_user_id="zoom_t1").exists())
        # Other unambiguous teachers should still succeed
        self.assertTrue(ZoomHostAccount.objects.filter(zoom_user_id="zoom_t2").exists())
        self.assertTrue(ZoomHostAccount.objects.filter(zoom_user_id="zoom_t3").exists())

    @mock.patch.object(ZoomService, "list_users")
    def test_detects_duplicate_zoom_emails_and_flags_ambiguity(self, mock_list_users):
        users = self._sample_zoom_users()
        # Add duplicate Zoom user with same email but different user id
        users.append({
            "id": "zoom_t1_dupe",
            "email": "teacher1@natyaarts.com",
            "first_name": "Radha",
            "last_name": "Rao 2",
            "display_name": "Radha Rao 2",
            "type": 2,
            "status": "active",
        })
        mock_list_users.return_value = users
        out = StringIO()

        call_command(
            "sync_zoom_host_accounts",
            commit=True,
            confirm="CONFIRM-ZOOM-HOST-SYNC",
            stdout=out
        )

        self.assertIn("AMBIGUOUS_DUPLICATE_ZOOM_EMAIL", out.getvalue())
        # Both duplicate zoom users skipped
        self.assertFalse(ZoomHostAccount.objects.filter(zoom_user_id="zoom_t1").exists())
        self.assertFalse(ZoomHostAccount.objects.filter(zoom_user_id="zoom_t1_dupe").exists())
        # Others created
        self.assertTrue(ZoomHostAccount.objects.filter(zoom_user_id="zoom_t2").exists())
        self.assertTrue(ZoomHostAccount.objects.filter(zoom_user_id="zoom_t3").exists())

    # -------------------------------------------------------------------------
    # 5. Conflict Detection (Instructor Mismatch & Dual-Key Integrity)
    # -------------------------------------------------------------------------
    @mock.patch.object(ZoomService, "list_users")
    def test_conflicting_assigned_instructor_is_not_overwritten(self, mock_list_users):
        # Existing host is bound to teacher1 in DB, but Zoom user email matches teacher2
        h = ZoomHostAccount.objects.create(
            zoom_user_id="zoom_t2",
            email="teacher2@natyaarts.com",
            status=ZoomHostAccount.Status.ACTIVE,
            account_type=ZoomHostAccount.AccountType.DEDICATED,
            assigned_instructor=self.teacher1  # conflicting!
        )

        mock_list_users.return_value = self._sample_zoom_users()
        out = StringIO()

        call_command(
            "sync_zoom_host_accounts",
            commit=True,
            confirm="CONFIRM-ZOOM-HOST-SYNC",
            stdout=out
        )

        self.assertIn("CONFLICT_ASSIGNMENT_MISMATCH", out.getvalue())
        h.refresh_from_db()
        self.assertEqual(h.assigned_instructor, self.teacher1)  # Preserved!

    @mock.patch.object(ZoomService, "list_users")
    def test_teacher_already_assigned_to_another_host_flags_conflict(self, mock_list_users):
        # Teacher 1 already has Host 1 in DB
        ZoomHostAccount.objects.create(
            zoom_user_id="old_host_1",
            email="old_email@natyaarts.com",
            status=ZoomHostAccount.Status.ACTIVE,
            account_type=ZoomHostAccount.AccountType.DEDICATED,
            assigned_instructor=self.teacher1
        )

        # Zoom user t1 also maps to teacher1
        mock_list_users.return_value = self._sample_zoom_users()
        out = StringIO()

        call_command(
            "sync_zoom_host_accounts",
            commit=True,
            confirm="CONFIRM-ZOOM-HOST-SYNC",
            stdout=out
        )

        self.assertIn("CONFLICT_TEACHER_ALREADY_ASSIGNED", out.getvalue())
        # Should not create a second host for teacher 1
        self.assertFalse(ZoomHostAccount.objects.filter(zoom_user_id="zoom_t1").exists())

    @mock.patch.object(ZoomService, "list_users")
    def test_dual_key_lookup_id_and_email_reconciled(self, mock_list_users):
        # Pre-existing host has unlinked email and temporary ID
        h = ZoomHostAccount.objects.create(
            zoom_user_id="temp_id_for_t1",
            email="teacher1@natyaarts.com",
            status=ZoomHostAccount.Status.ACTIVE,
            account_type=ZoomHostAccount.AccountType.SHARED_POOL,
            assigned_instructor=None
        )

        mock_list_users.return_value = self._sample_zoom_users()
        out = StringIO()

        call_command(
            "sync_zoom_host_accounts",
            commit=True,
            confirm="CONFIRM-ZOOM-HOST-SYNC",
            stdout=out
        )

        h.refresh_from_db()
        self.assertEqual(h.zoom_user_id, "zoom_t1")
        self.assertEqual(h.account_type, ZoomHostAccount.AccountType.DEDICATED)
        self.assertEqual(h.assigned_instructor, self.teacher1)

    @mock.patch.object(ZoomService, "list_users")
    def test_duplicate_host_records_in_db_flags_conflict(self, mock_list_users):
        # Database has two hosts sharing normalized email (casing variant)
        h1 = ZoomHostAccount.objects.create(
            zoom_user_id="host_dup_1",
            email="teacher1@natyaarts.com",
            status=ZoomHostAccount.Status.ACTIVE,
        )
        h2 = ZoomHostAccount.objects.create(
            zoom_user_id="host_dup_2",
            email="TEACHER1@NATYAARTS.COM",
            status=ZoomHostAccount.Status.ACTIVE,
        )

        mock_list_users.return_value = self._sample_zoom_users()
        out = StringIO()

        call_command(
            "sync_zoom_host_accounts",
            commit=True,
            confirm="CONFIRM-ZOOM-HOST-SYNC",
            stdout=out
        )

        self.assertIn("CONFLICT_DUPLICATE_HOST_RECORDS", out.getvalue())
        # Neither record was mutated
        h1.refresh_from_db()
        h2.refresh_from_db()
        self.assertIsNone(h1.assigned_instructor)
        self.assertIsNone(h2.assigned_instructor)

    @mock.patch.object(ZoomService, "list_users")
    def test_split_id_and_email_in_db_flags_conflict(self, mock_list_users):
        # Host 1 has zoom_user_id "zoom_t1" but different email
        ZoomHostAccount.objects.create(
            zoom_user_id="zoom_t1",
            email="other@natyaarts.com",
            status=ZoomHostAccount.Status.ACTIVE,
        )
        # Host 2 has "teacher1@natyaarts.com" but different zoom_user_id
        ZoomHostAccount.objects.create(
            zoom_user_id="other_id",
            email="teacher1@natyaarts.com",
            status=ZoomHostAccount.Status.ACTIVE,
        )

        mock_list_users.return_value = self._sample_zoom_users()
        out = StringIO()

        call_command(
            "sync_zoom_host_accounts",
            commit=True,
            confirm="CONFIRM-ZOOM-HOST-SYNC",
            stdout=out
        )

        self.assertIn("CONFLICT_DUPLICATE_HOST_RECORDS", out.getvalue())

    # -------------------------------------------------------------------------
    # 6. Unmatched Zoom Users & Missing LMS Teachers
    # -------------------------------------------------------------------------
    @mock.patch.object(ZoomService, "list_users")
    def test_unmatched_licensed_zoom_users_reported_separately(self, mock_list_users):
        users = self._sample_zoom_users()
        # Add a licensed Zoom user with no LMS teacher
        users.append({
            "id": "zoom_unmatched",
            "email": "unregistered@natyaarts.com",
            "first_name": "External",
            "last_name": "Instructor",
            "display_name": "External Instructor",
            "type": 2,
            "status": "active",
        })
        mock_list_users.return_value = users
        out = StringIO()

        call_command(
            "sync_zoom_host_accounts",
            commit=True,
            confirm="CONFIRM-ZOOM-HOST-SYNC",
            stdout=out
        )

        self.assertIn("LICENSED ZOOM USERS WITHOUT LMS TEACHER MATCH (1)", out.getvalue())
        self.assertIn("unregistered@natyaarts.com", out.getvalue())
        # Unmatched user is NOT created as dedicated host
        self.assertFalse(ZoomHostAccount.objects.filter(zoom_user_id="zoom_unmatched").exists())

    # -------------------------------------------------------------------------
    # 7. Fail-Closed Zoom API Errors
    # -------------------------------------------------------------------------
    @mock.patch.object(ZoomService, "list_users")
    def test_zoom_api_error_fails_closed(self, mock_list_users):
        mock_list_users.side_effect = ZoomTransientError("Connection timed out to Zoom API")

        with self.assertRaises(CommandError) as ctx:
            call_command("sync_zoom_host_accounts", commit=True, confirm="CONFIRM-ZOOM-HOST-SYNC")

        self.assertIn("Fail-Closed: Could not retrieve Zoom user inventory", str(ctx.exception))
        self.assertEqual(ZoomHostAccount.objects.count(), 0)

    # -------------------------------------------------------------------------
    # 8. Permanent Exclusion of Personal/Test Accounts (User IDs 1, 25, 96 - Vijay Pn)
    # -------------------------------------------------------------------------
    @mock.patch.object(ZoomService, "list_users")
    def test_personal_test_accounts_1_25_96_are_permanently_excluded(self, mock_list_users):
        u1 = User.objects.create_user(
            id=1, username="vijay_1", email="vijay_test_1@natyaarts.com",
            first_name="Vijay", last_name="Pn", is_teacher=True
        )
        u25 = User.objects.create_user(
            id=25, username="vijay_25", email="vijay_test_25@natyaarts.com",
            first_name="Vijay", last_name="Pn", is_teacher=True
        )
        u96 = User.objects.create_user(
            id=96, username="vijay_96", email="vijay_test_96@natyaarts.com",
            first_name="Vijay", last_name="Pn", is_teacher=True
        )

        users = self._sample_zoom_users()
        users.extend([
            {"id": "zoom_u1", "email": "vijay_test_1@natyaarts.com", "display_name": "Vijay 1", "type": 2, "status": "active"},
            {"id": "zoom_u25", "email": "vijay_test_25@natyaarts.com", "display_name": "Vijay 25", "type": 2, "status": "active"},
            {"id": "zoom_u96", "email": "vijay_test_96@natyaarts.com", "display_name": "Vijay 96", "type": 2, "status": "active"},
        ])

        def side_effect(status="active", page_size=300):
            if status == "active":
                return users
            return []
        mock_list_users.side_effect = side_effect

        out = StringIO()
        call_command("sync_zoom_host_accounts", commit=True, confirm="CONFIRM-ZOOM-HOST-SYNC", stdout=out)

        output = out.getvalue()
        # 1. Output clearly states IDs 1, 25, 96 are permanently excluded
        self.assertIn("Permanently Excluded:", output)
        self.assertIn("1", output)
        self.assertIn("25", output)
        self.assertIn("96", output)

        # 2. Excluded IDs are NOT in missing LMS teachers
        self.assertNotIn("Teacher #1:", output)
        self.assertNotIn("Teacher #25:", output)
        self.assertNotIn("Teacher #96:", output)

        # 3. Excluded IDs are NOT in proposed creates
        self.assertNotIn("Teacher #1 ", output)
        self.assertNotIn("Teacher #25 ", output)
        self.assertNotIn("Teacher #96 ", output)

        # 4. Zoom users for excluded teachers are treated as unmatched
        self.assertIn("vijay_test_1@natyaarts.com", output)
        self.assertIn("vijay_test_25@natyaarts.com", output)
        self.assertIn("vijay_test_96@natyaarts.com", output)
        self.assertIn("Ineligible for dedicated mapping", output)

        # 5. Zero host accounts created for IDs 1, 25, 96
        self.assertFalse(ZoomHostAccount.objects.filter(assigned_instructor_id__in=[1, 25, 96]).exists())
        self.assertFalse(ZoomHostAccount.objects.filter(email__in=[
            "vijay_test_1@natyaarts.com", "vijay_test_25@natyaarts.com", "vijay_test_96@natyaarts.com"
        ]).exists())

    @mock.patch.object(ZoomService, "list_users")
    def test_excluded_accounts_cannot_be_matched_via_explicit_mapping(self, mock_list_users):
        u1 = User.objects.create_user(
            id=1, username="vijay_1", email="teacher_1@natya.internal", is_teacher=True
        )
        users = self._sample_zoom_users()
        users.append({
            "id": "zoom_u1_explicit",
            "email": "vijay_explicit@gmail.com",
            "display_name": "Vijay Explicit",
            "type": 2,
            "status": "active"
        })

        def side_effect(status="active", page_size=300):
            if status == "active":
                return users
            return []
        mock_list_users.side_effect = side_effect

        out = StringIO()
        call_command(
            "sync_zoom_host_accounts",
            commit=True,
            confirm="CONFIRM-ZOOM-HOST-SYNC",
            stdout=out,
            explicit_mappings={1: "vijay_explicit@gmail.com"}
        )
        output = out.getvalue()
        self.assertIn("CONFLICT_EXCLUDED_TEACHER", output)
        self.assertFalse(ZoomHostAccount.objects.filter(assigned_instructor_id=1).exists())
        self.assertFalse(ZoomHostAccount.objects.filter(email="vijay_explicit@gmail.com").exists())

    @mock.patch.object(ZoomService, "list_users")
    def test_excluded_accounts_cannot_be_updated_in_existing_hosts(self, mock_list_users):
        u25 = User.objects.create_user(
            id=25, username="vijay_25", email="vijay_test_25@natyaarts.com", is_teacher=True
        )
        h = ZoomHostAccount.objects.create(
            zoom_user_id="zoom_old_25",
            email="vijay_test_25@natyaarts.com",
            status=ZoomHostAccount.Status.ACTIVE,
            account_type=ZoomHostAccount.AccountType.DEDICATED,
            assigned_instructor=u25
        )
        users = self._sample_zoom_users()
        users.append({
            "id": "zoom_new_25",
            "email": "vijay_test_25@natyaarts.com",
            "display_name": "Vijay 25",
            "type": 2,
            "status": "active"
        })

        def side_effect(status="active", page_size=300):
            if status == "active":
                return users
            return []
        mock_list_users.side_effect = side_effect

        out = StringIO()
        call_command("sync_zoom_host_accounts", commit=True, confirm="CONFIRM-ZOOM-HOST-SYNC", stdout=out)
        output = out.getvalue()
        self.assertIn("CONFLICT_EXCLUDED_TEACHER", output)
        h.refresh_from_db()
        self.assertEqual(h.assigned_instructor_id, 25)
        self.assertEqual(h.zoom_user_id, "zoom_old_25")  # untouched

    # -------------------------------------------------------------------------
    # 9. Teacher ID 39 (Kalakshetra Jisma K K) is Eligible and Matched
    # -------------------------------------------------------------------------
    @mock.patch.object(ZoomService, "list_users")
    def test_teacher_39_jisma_is_eligible_and_matched(self, mock_list_users):
        teacher_39 = User.objects.create_user(
            id=39,
            username="jismakk",
            email="jismakk@gmail.com",
            first_name="Jisma",
            last_name="K K",
            is_teacher=True
        )
        users = self._sample_zoom_users()
        users.append({
            "id": "zoom_jisma_39",
            "email": "jismakk@gmail.com",
            "first_name": "Jisma",
            "last_name": "K K",
            "display_name": "Kalakshetra Jisma K K",
            "type": 2,
            "status": "active"
        })

        def side_effect(status="active", page_size=300):
            if status == "active":
                return users
            return []
        mock_list_users.side_effect = side_effect

        out = StringIO()
        call_command("sync_zoom_host_accounts", commit=True, confirm="CONFIRM-ZOOM-HOST-SYNC", stdout=out)
        output = out.getvalue()

        # Proves ID 39 is NOT in excluded list
        self.assertNotIn("39: Vijay", output)
        # Proves Teacher 39 is created
        self.assertIn("Created Host", output)
        self.assertIn("jismakk@gmail.com -> Teacher #39", output)

        host = ZoomHostAccount.objects.get(assigned_instructor_id=39)
        self.assertEqual(host.email, "jismakk@gmail.com")
        self.assertEqual(host.zoom_user_id, "zoom_jisma_39")
        self.assertEqual(host.account_type, ZoomHostAccount.AccountType.DEDICATED)
        self.assertEqual(host.max_concurrency, 1)

    # -------------------------------------------------------------------------
    # 10. Explicit Synthetic-Email Teacher Mappings & Identity Fingerprints
    # -------------------------------------------------------------------------
    @mock.patch.object(ZoomService, "list_users")
    def test_explicit_mappings_not_applied_by_default(self, mock_list_users):
        """
        Verify that running sync_zoom_host_accounts without --apply-explicit-mappings
        does NOT apply built-in explicit mappings, logs an opt-in notice, and creates zero explicit hosts.
        """
        data189 = BUILTIN_EXPLICIT_TEACHER_MAPPINGS[189]
        User.objects.create_user(
            id=189, username="t189", email="teacher_189@natya.internal",
            first_name=data189["expected_name"], phone_number=data189["expected_phone"],
            is_teacher=True
        )
        users = self._sample_zoom_users()
        users.append({
            "id": "zoom_189",
            "email": data189["email"],
            "display_name": data189["expected_name"],
            "type": 2,
            "status": "active"
        })
        mock_list_users.return_value = users

        out = StringIO()
        call_command("sync_zoom_host_accounts", stdout=out)
        output = out.getvalue()

        self.assertIn("Built-in explicit mappings are OPT-IN and NOT applied by default", output)
        self.assertIn("OPT-IN DISABLED (0 applied", output)
        self.assertFalse(ZoomHostAccount.objects.filter(assigned_instructor_id=189).exists())

    @mock.patch.object(ZoomService, "list_users")
    def test_all_fourteen_explicit_synthetic_teacher_mappings_matched_by_instructor_id(self, mock_list_users):
        self.assertEqual(len(BUILTIN_EXPLICIT_TEACHER_MAPPINGS), 14)
        created_teachers = {}
        for tid, data in BUILTIN_EXPLICIT_TEACHER_MAPPINGS.items():
            t = User.objects.create_user(
                id=tid,
                username=f"teacher_{tid}",
                email=f"teacher_{tid}@natya.internal",
                first_name=data["expected_name"],
                phone_number=data["expected_phone"],
                is_teacher=True
            )
            created_teachers[tid] = t

        users = self._sample_zoom_users()
        for tid, data in BUILTIN_EXPLICIT_TEACHER_MAPPINGS.items():
            users.append({
                "id": f"zoom_uid_{tid}",
                "email": data["email"],
                "first_name": data["expected_name"],
                "last_name": "Test",
                "display_name": f"{data['expected_name']} Display",
                "type": 2,
                "status": "active"
            })

        def side_effect(status="active", page_size=300):
            if status == "active":
                return users
            return []
        mock_list_users.side_effect = side_effect

        out = StringIO()
        call_command(
            "sync_zoom_host_accounts",
            apply_explicit_mappings=True,
            commit=True,
            confirm="CONFIRM-ZOOM-HOST-SYNC",
            stdout=out
        )
        output = out.getvalue()

        # Verify all 14 host accounts created with max_concurrency = 1
        for tid, data in BUILTIN_EXPLICIT_TEACHER_MAPPINGS.items():
            host = ZoomHostAccount.objects.get(assigned_instructor_id=tid)
            self.assertEqual(host.email, data["email"])
            self.assertEqual(host.zoom_user_id, f"zoom_uid_{tid}")
            self.assertEqual(host.account_type, ZoomHostAccount.AccountType.DEDICATED)
            self.assertEqual(host.max_concurrency, 1)

            # Assert LMS user's email and username were NOT altered
            lms_user = User.objects.get(id=tid)
            self.assertEqual(lms_user.email, f"teacher_{tid}@natya.internal")
            self.assertEqual(lms_user.username, f"teacher_{tid}")

    @mock.patch.object(ZoomService, "list_users")
    def test_identity_fingerprint_name_mismatch_fails_closed(self, mock_list_users):
        """
        Verify that if the LMS user has a different name than expected,
        validation fails closed with CONFLICT_EXPLICIT_MAPPING_NAME_MISMATCH.
        """
        data189 = BUILTIN_EXPLICIT_TEACHER_MAPPINGS[189]
        User.objects.create_user(
            id=189, username="t189_wrong_name", email="teacher_189@natya.internal",
            first_name="Completely Wrong Name", phone_number=data189["expected_phone"],
            is_teacher=True
        )
        users = self._sample_zoom_users()
        users.append({
            "id": "zoom_189",
            "email": data189["email"],
            "display_name": "Abhinanda",
            "type": 2,
            "status": "active"
        })
        mock_list_users.return_value = users

        out = StringIO()
        call_command(
            "sync_zoom_host_accounts",
            apply_explicit_mappings=True,
            commit=True,
            confirm="CONFIRM-ZOOM-HOST-SYNC",
            stdout=out
        )
        output = out.getvalue()

        self.assertIn("CONFLICT_EXPLICIT_MAPPING_NAME_MISMATCH", output)
        self.assertFalse(ZoomHostAccount.objects.filter(assigned_instructor_id=189).exists())
        self.assertFalse(ZoomHostAccount.objects.filter(email=data189["email"]).exists())

    @mock.patch.object(ZoomService, "list_users")
    def test_identity_fingerprint_phone_mismatch_fails_closed(self, mock_list_users):
        """
        Verify that if the LMS user has a different phone number than expected,
        validation fails closed with CONFLICT_EXPLICIT_MAPPING_PHONE_MISMATCH.
        """
        data189 = BUILTIN_EXPLICIT_TEACHER_MAPPINGS[189]
        User.objects.create_user(
            id=189, username="t189_wrong_phone", email="teacher_189@natya.internal",
            first_name=data189["expected_name"], phone_number="+910000000000",
            is_teacher=True
        )
        users = self._sample_zoom_users()
        users.append({
            "id": "zoom_189",
            "email": data189["email"],
            "display_name": "Abhinanda",
            "type": 2,
            "status": "active"
        })
        mock_list_users.return_value = users

        out = StringIO()
        call_command(
            "sync_zoom_host_accounts",
            apply_explicit_mappings=True,
            commit=True,
            confirm="CONFIRM-ZOOM-HOST-SYNC",
            stdout=out
        )
        output = out.getvalue()

        self.assertIn("CONFLICT_EXPLICIT_MAPPING_PHONE_MISMATCH", output)
        self.assertFalse(ZoomHostAccount.objects.filter(assigned_instructor_id=189).exists())
        self.assertFalse(ZoomHostAccount.objects.filter(email=data189["email"]).exists())

    @mock.patch.object(ZoomService, "list_users")
    def test_local_id_189_cannot_accidentally_map_to_production_abhinanda_account(self, mock_list_users):
        """
        Simulate local database environment where ID 189 is student MRIDULA (+919846405525, is_teacher=False).
        Prove that running --apply-explicit-mappings detects multiple fatal conflicts and
        CANNOT accidentally bind or provision abhinandapadman@gmail.com to local user 189.
        """
        User.objects.create_user(
            id=189,
            username="kmridula1997@gmail.com",
            email="kmridula1997@gmail.com",
            first_name="MRIDULA",
            phone_number="+919846405525",
            is_teacher=False,
            is_active=True
        )
        users = self._sample_zoom_users()
        users.append({
            "id": "zoom_abhinanda_pro",
            "email": "abhinandapadman@gmail.com",
            "display_name": "Abhinanda Padman",
            "type": 2,
            "status": "active"
        })
        mock_list_users.return_value = users

        out = StringIO()
        call_command(
            "sync_zoom_host_accounts",
            apply_explicit_mappings=True,
            commit=True,
            confirm="CONFIRM-ZOOM-HOST-SYNC",
            stdout=out
        )
        output = out.getvalue()

        # Fails closed on not teacher
        self.assertIn("CONFLICT_EXPLICIT_MAPPING_NOT_TEACHER", output)
        self.assertFalse(ZoomHostAccount.objects.filter(assigned_instructor_id=189).exists())
        self.assertFalse(ZoomHostAccount.objects.filter(email="abhinandapadman@gmail.com").exists())

    @mock.patch.object(ZoomService, "list_users")
    def test_missing_explicit_zoom_account_fails_closed(self, mock_list_users):
        # Teacher 189 exists with valid fingerprints, but Zoom API does NOT return abhinandapadman@gmail.com
        data189 = BUILTIN_EXPLICIT_TEACHER_MAPPINGS[189]
        User.objects.create_user(
            id=189,
            username="teacher_189",
            email="teacher_189@natya.internal",
            first_name=data189["expected_name"],
            phone_number=data189["expected_phone"],
            is_teacher=True
        )
        mock_list_users.return_value = self._sample_zoom_users()

        out = StringIO()
        call_command("sync_zoom_host_accounts", apply_explicit_mappings=True, stdout=out)
        output = out.getvalue()

        self.assertIn("CONFLICT_EXPLICIT_ZOOM_ACCOUNT_MISSING", output)
        self.assertIn("abhinandapadman@gmail.com", output)
        self.assertFalse(ZoomHostAccount.objects.filter(assigned_instructor_id=189).exists())

    @mock.patch.object(ZoomService, "list_users")
    def test_duplicate_explicit_zoom_mapping_fails_closed(self, mock_list_users):
        t101 = User.objects.create_user(id=101, username="t101", email="t101@natya.internal", is_teacher=True)
        t102 = User.objects.create_user(id=102, username="t102", email="t102@natya.internal", is_teacher=True)

        users = self._sample_zoom_users()
        users.append({
            "id": "zoom_shared_101_102",
            "email": "shared_duplicate@gmail.com",
            "display_name": "Shared Account",
            "type": 2,
            "status": "active"
        })
        mock_list_users.return_value = users

        out = StringIO()
        call_command(
            "sync_zoom_host_accounts",
            stdout=out,
            explicit_mappings={101: "shared_duplicate@gmail.com", 102: "shared_duplicate@gmail.com"}
        )
        output = out.getvalue()

        self.assertIn("CONFLICT_DUPLICATE_EXPLICIT_ZOOM_MAPPING", output)
        self.assertFalse(ZoomHostAccount.objects.filter(email="shared_duplicate@gmail.com").exists())

    @mock.patch.object(ZoomService, "list_users")
    def test_explicit_mapping_to_non_teacher_account_fails_closed(self, mock_list_users):
        student_user = User.objects.create_user(
            id=201, username="student_201", email="student201@natya.internal",
            first_name="Student", last_name="User", is_teacher=False, is_active=True
        )
        # Pre-create a host bound to teacher1 to verify no reassignment occurs
        h1 = ZoomHostAccount.objects.create(
            zoom_user_id="zoom_h1",
            email="non_teacher_zoom@gmail.com",
            status=ZoomHostAccount.Status.ACTIVE,
            account_type=ZoomHostAccount.AccountType.DEDICATED,
            assigned_instructor=self.teacher1
        )
        users = self._sample_zoom_users()
        users.append({
            "id": "zoom_h1",
            "email": "non_teacher_zoom@gmail.com",
            "display_name": "Non Teacher Zoom",
            "type": 2,
            "status": "active"
        })
        mock_list_users.return_value = users

        out = StringIO()
        call_command(
            "sync_zoom_host_accounts",
            commit=True,
            confirm="CONFIRM-ZOOM-HOST-SYNC",
            stdout=out,
            explicit_mappings={201: "non_teacher_zoom@gmail.com"}
        )
        output = out.getvalue()

        # 1. Clear conflict reported
        self.assertIn("CONFLICT_EXPLICIT_MAPPING_NOT_TEACHER", output)
        # 2. Host is NOT reassigned to user 201
        h1.refresh_from_db()
        self.assertEqual(h1.assigned_instructor_id, self.teacher1.id)
        # 3. No host created for user 201
        self.assertFalse(ZoomHostAccount.objects.filter(assigned_instructor_id=201).exists())

    @mock.patch.object(ZoomService, "list_users")
    def test_explicit_mapping_to_inactive_teacher_fails_closed(self, mock_list_users):
        inactive_teacher = User.objects.create_user(
            id=202, username="inactive_202", email="inactive202@natya.internal",
            first_name="Inactive", last_name="Teacher", is_teacher=True, is_active=False
        )
        users = self._sample_zoom_users()
        users.append({
            "id": "zoom_inactive_202",
            "email": "inactive_zoom@gmail.com",
            "display_name": "Inactive Teacher Zoom",
            "type": 2,
            "status": "active"
        })
        mock_list_users.return_value = users

        out = StringIO()
        call_command(
            "sync_zoom_host_accounts",
            commit=True,
            confirm="CONFIRM-ZOOM-HOST-SYNC",
            stdout=out,
            explicit_mappings={202: "inactive_zoom@gmail.com"}
        )
        output = out.getvalue()

        # 1. Clear conflict reported
        self.assertIn("CONFLICT_EXPLICIT_MAPPING_USER_INACTIVE", output)
        # 2. No host account created for inactive teacher
        self.assertFalse(ZoomHostAccount.objects.filter(assigned_instructor_id=202).exists())
        self.assertFalse(ZoomHostAccount.objects.filter(email="inactive_zoom@gmail.com").exists())

    @mock.patch.object(ZoomService, "list_users")
    def test_explicit_mapping_json_cli_string_parsing_and_error_handling(self, mock_list_users):
        t203 = User.objects.create_user(
            id=203, username="teacher_203", email="t203@natya.internal",
            is_teacher=True, is_active=True
        )
        users = self._sample_zoom_users()
        users.append({
            "id": "zoom_t203",
            "email": "t203_cli@gmail.com",
            "display_name": "Teacher 203",
            "type": 2,
            "status": "active"
        })
        mock_list_users.return_value = users

        # Valid JSON string
        out = StringIO()
        call_command(
            "sync_zoom_host_accounts",
            commit=True,
            confirm="CONFIRM-ZOOM-HOST-SYNC",
            stdout=out,
            explicit_mappings='{"203": "t203_cli@gmail.com"}'
        )
        output = out.getvalue()
        self.assertIn("t203_cli@gmail.com -> Teacher #203", output)
        self.assertTrue(ZoomHostAccount.objects.filter(assigned_instructor_id=203).exists())

        # Invalid JSON string raises CommandError
        with self.assertRaises(CommandError) as ctx:
            call_command("sync_zoom_host_accounts", explicit_mappings="not valid json")
        self.assertIn("Invalid --explicit-mappings format", str(ctx.exception))

    @mock.patch.object(ZoomService, "list_users")
    def test_existing_eight_host_assignments_preserved_without_alteration(self, mock_list_users):
        """
        Verify that all eight protected dedicated host assignments (Teacher IDs: 54, 57, 58, 59, 60, 61, 62, 63)
        are never overwritten, reassigned, deleted, or deactivated.
        """
        self.assertEqual(PROTECTED_EXISTING_TEACHER_IDS, frozenset({54, 57, 58, 59, 60, 61, 62, 63}))
        for tid in sorted(list(PROTECTED_EXISTING_TEACHER_IDS)):
            t = User.objects.create_user(
                id=tid,
                username=f"teacher_{tid}",
                email=f"teacher_{tid}@natyaarts.com",
                first_name=f"Teacher{tid}",
                is_teacher=True
            )
            ZoomHostAccount.objects.create(
                zoom_user_id=f"zoom_protected_{tid}",
                email=f"teacher_{tid}@natyaarts.com",
                display_name=f"Teacher {tid} Display",
                status=ZoomHostAccount.Status.ACTIVE,
                account_type=ZoomHostAccount.AccountType.DEDICATED,
                assigned_instructor=t,
                max_concurrency=1
            )

        # Mock list_users returning existing protected hosts + sample users
        users = self._sample_zoom_users()
        for tid in sorted(list(PROTECTED_EXISTING_TEACHER_IDS)):
            users.append({
                "id": f"zoom_protected_{tid}",
                "email": f"teacher_{tid}@natyaarts.com",
                "display_name": f"Teacher {tid} Display",
                "type": 2,
                "status": "active"
            })
        mock_list_users.return_value = users

        out = StringIO()
        call_command(
            "sync_zoom_host_accounts",
            commit=True,
            confirm="CONFIRM-ZOOM-HOST-SYNC",
            stdout=out
        )
        output = out.getvalue()

        # All 8 protected hosts remain intact
        for tid in sorted(list(PROTECTED_EXISTING_TEACHER_IDS)):
            host = ZoomHostAccount.objects.get(assigned_instructor_id=tid)
            self.assertEqual(host.zoom_user_id, f"zoom_protected_{tid}")
            self.assertEqual(host.email, f"teacher_{tid}@natyaarts.com")
            self.assertEqual(host.status, ZoomHostAccount.Status.ACTIVE)
            self.assertEqual(host.account_type, ZoomHostAccount.AccountType.DEDICATED)
            self.assertEqual(host.max_concurrency, 1)

    @mock.patch.object(ZoomService, "list_users")
    def test_reassignment_of_protected_host_fails_closed(self, mock_list_users):
        """
        Verify that if an existing host is bound to a protected teacher (e.g. #54),
        it CANNOT be reassigned to a different teacher (#182), failing closed with CONFLICT_PROTECTED_HOST_REASSIGNMENT.
        """
        t54 = User.objects.create_user(
            id=54, username="teacher_54", email="t54@natyaarts.com", is_teacher=True
        )
        host54 = ZoomHostAccount.objects.create(
            zoom_user_id="zoom_54",
            email="shared_protected@gmail.com",
            status=ZoomHostAccount.Status.ACTIVE,
            account_type=ZoomHostAccount.AccountType.DEDICATED,
            assigned_instructor=t54,
            max_concurrency=1
        )
        t182 = User.objects.create_user(
            id=182, username="teacher_182", email="t182@natya.internal", is_teacher=True,
            first_name="Kalamandalam Vaishnavi", phone_number="+916235528825"
        )

        # Incoming Zoom user has email 'shared_protected@gmail.com' and attempts to map to 182
        users = self._sample_zoom_users()
        users.append({
            "id": "zoom_54",
            "email": "shared_protected@gmail.com",
            "display_name": "Shared Protected",
            "type": 2,
            "status": "active"
        })
        mock_list_users.return_value = users

        out = StringIO()
        call_command(
            "sync_zoom_host_accounts",
            stdout=out,
            explicit_mappings={182: "shared_protected@gmail.com"}
        )
        output = out.getvalue()

        self.assertIn("CONFLICT_PROTECTED_HOST_REASSIGNMENT", output)
        host54.refresh_from_db()
        self.assertEqual(host54.assigned_instructor_id, 54)

    @mock.patch.object(ZoomService, "list_users")
    def test_zoom_owner_account_and_non_teachers_40_49_rejected(self, mock_list_users):
        """
        Verify:
        - Tenant owner account 'vijay.pn@natyaarts.com' is excluded from host creation and explicit mappings.
        - Non-teacher IDs 40 and 49 are excluded in EXCLUDED_TEACHER_IDS and cannot be mapped.
        """
        self.assertIn("vijay.pn@natyaarts.com", EXCLUDED_ZOOM_EMAILS)
        self.assertIn(40, EXCLUDED_TEACHER_IDS)
        self.assertIn(49, EXCLUDED_TEACHER_IDS)

        # 1. Attempt explicit mapping with tenant owner email
        t182 = User.objects.create_user(
            id=182, username="t182", email="t182@natya.internal", is_teacher=True
        )
        out = StringIO()
        call_command(
            "sync_zoom_host_accounts",
            stdout=out,
            explicit_mappings={182: "vijay.pn@natyaarts.com"}
        )
        output = out.getvalue()
        self.assertIn("CONFLICT_EXCLUDED_ZOOM_OWNER", output)

        # 2. Attempt explicit mapping with excluded non-teachers 40 and 49
        u40 = User.objects.create_user(id=40, username="user_40", email="u40@natya.internal", is_teacher=False)
        u49 = User.objects.create_user(id=49, username="user_49", email="u49@natya.internal", is_teacher=False)

        out2 = StringIO()
        call_command(
            "sync_zoom_host_accounts",
            stdout=out2,
            explicit_mappings={40: "u40@gmail.com", 49: "u49@gmail.com"}
        )
        output2 = out2.getvalue()
        self.assertIn("CONFLICT_EXCLUDED_TEACHER", output2)
        self.assertFalse(ZoomHostAccount.objects.filter(assigned_instructor_id__in=[40, 49]).exists())

        # 3. Verify owner email in Zoom inventory is placed in owner_users and not created
        users = self._sample_zoom_users()
        users.append({
            "id": "zoom_owner_vijay",
            "email": "vijay.pn@natyaarts.com",
            "display_name": "Vijay PN Owner",
            "type": 2,
            "status": "active"
        })
        mock_list_users.return_value = users

        out3 = StringIO()
        call_command(
            "sync_zoom_host_accounts",
            commit=True,
            confirm="CONFIRM-ZOOM-HOST-SYNC",
            stdout=out3
        )
        output3 = out3.getvalue()
        self.assertIn("Excluded Owner Accounts:    1", output3)
        self.assertFalse(ZoomHostAccount.objects.filter(email="vijay.pn@natyaarts.com").exists())

    @mock.patch.object(ZoomService, "list_users")
    def test_pending_invitation_zoom_account_not_assigned_as_active_host(self, mock_list_users):
        """
        Verify that pending Zoom invitations (status='pending') are never allocated as active hosts.
        """
        t183 = User.objects.create_user(
            id=183, username="t183", email="teacher_183@natya.internal", is_teacher=True,
            first_name="AMRITHA", phone_number="+917356597816"
        )
        def side_effect(status="active", page_size=300):
            if status == "active":
                return self._sample_zoom_users()
            elif status == "pending":
                return [{
                    "id": "zoom_pending_amritha",
                    "email": "amrithasivakumar3@gmail.com",
                    "first_name": "AMRITHA",
                    "type": 2,
                    "status": "pending"
                }]
            return []
        mock_list_users.side_effect = side_effect

        out = StringIO()
        call_command(
            "sync_zoom_host_accounts",
            apply_explicit_mappings=True,
            commit=True,
            confirm="CONFIRM-ZOOM-HOST-SYNC",
            stdout=out
        )
        output = out.getvalue()
        self.assertIn("CONFLICT_EXPLICIT_ZOOM_ACCOUNT_MISSING", output)
        self.assertIn("invitation must be accepted", output)
        self.assertFalse(ZoomHostAccount.objects.filter(email="amrithasivakumar3@gmail.com").exists())
        self.assertFalse(ZoomHostAccount.objects.filter(assigned_instructor_id=183).exists())

    @mock.patch.object(ZoomService, "list_users")
    def test_execution_loop_skips_proposed_update_for_protected_host_even_if_target_teacher_same(self, mock_list_users):
        """
        Safety Correction 1:
        For teacher IDs in PROTECTED_EXISTING_TEACHER_IDS ({54, 57, 58, 59, 60, 61, 62, 63}),
        the execution loop must skip every proposed update whose existing host is assigned
        to one of these IDs, even when the proposed target teacher is the same.
        Preserve all existing fields unchanged.
        """
        t54 = User.objects.create_user(
            id=54,
            username="teacher_54",
            email="yathivandyaks@gmail.com",
            first_name="Yathivandya K.S",
            is_teacher=True
        )
        orig_host = ZoomHostAccount.objects.create(
            id=54,
            zoom_user_id="orig_zoom_54",
            email="yathivandyaks@gmail.com",
            display_name="Yathivandya K.S Original",
            status=ZoomHostAccount.Status.ACTIVE,
            account_type=ZoomHostAccount.AccountType.DEDICATED,
            assigned_instructor=t54,
            max_concurrency=1
        )

        # Zoom API returns a user for yathivandyaks@gmail.com with modified attributes
        users = self._sample_zoom_users()
        users.append({
            "id": "new_zoom_id_54",
            "email": "yathivandyaks@gmail.com",
            "display_name": "Yathivandya Changed Display",
            "type": 2,
            "status": "active"
        })
        mock_list_users.return_value = users

        out = StringIO()
        call_command(
            "sync_zoom_host_accounts",
            commit=True,
            confirm="CONFIRM-ZOOM-HOST-SYNC",
            stdout=out
        )
        output = out.getvalue()

        self.assertIn("SKIPPED Update: Existing host #54 is bound to protected Teacher #54; preserved unchanged.", output)
        orig_host.refresh_from_db()
        self.assertEqual(orig_host.zoom_user_id, "orig_zoom_54")
        self.assertEqual(orig_host.display_name, "Yathivandya K.S Original")
        self.assertEqual(orig_host.email, "yathivandyaks@gmail.com")
        self.assertEqual(orig_host.assigned_instructor_id, 54)

    @mock.patch.object(ZoomService, "list_users")
    def test_failed_explicit_mapping_teacher_id_blocked_from_ordinary_email_fallback(self, mock_list_users):
        """
        Safety Correction 2:
        Failed explicit mapping teacher IDs are tracked in blocked_explicit_teacher_ids.
        They must be excluded from ordinary email fallback matching as well as excluding invalid Zoom emails.
        A failed explicit mapping must never be silently reassigned through fallback.
        """
        # Teacher #189 exists with legitimate LMS email and name
        t189 = User.objects.create_user(
            id=189,
            username="teacher_189",
            email="abhinanda.lms@natyaarts.com",
            first_name="ABHINANDA",
            phone_number="+918921902179",
            is_teacher=True
        )

        # Explicit mapping configured with mismatched phone -> fails validation
        mismatched_explicit_mappings = {
            189: {
                "email": "abhinandapadman@gmail.com",
                "expected_name": "ABHINANDA",
                "expected_phone": "+910000000000"  # Deliberate mismatch
            }
        }

        # Zoom API returns TWO licensed users:
        # User A: abhinandapadman@gmail.com (the explicit Zoom email that failed validation)
        # User B: abhinanda.lms@natyaarts.com (matches Teacher #189's LMS email directly!)
        users = self._sample_zoom_users()
        users.append({
            "id": "zoom_explicit_189",
            "email": "abhinandapadman@gmail.com",
            "display_name": "Abhinanda Pro",
            "type": 2,
            "status": "active"
        })
        users.append({
            "id": "zoom_fallback_189",
            "email": "abhinanda.lms@natyaarts.com",
            "display_name": "Abhinanda LMS Match",
            "type": 2,
            "status": "active"
        })
        mock_list_users.return_value = users

        out = StringIO()
        call_command(
            "sync_zoom_host_accounts",
            explicit_mappings=mismatched_explicit_mappings,
            commit=True,
            confirm="CONFIRM-ZOOM-HOST-SYNC",
            stdout=out
        )
        output = out.getvalue()

        # Both the explicit Zoom email and the fallback LMS email must NOT be assigned to Teacher #189
        self.assertIn("CONFLICT_EXPLICIT_MAPPING_PHONE_MISMATCH", output)
        self.assertFalse(ZoomHostAccount.objects.filter(assigned_instructor_id=189).exists())
        self.assertFalse(ZoomHostAccount.objects.filter(email="abhinandapadman@gmail.com").exists())
        self.assertFalse(ZoomHostAccount.objects.filter(email="abhinanda.lms@natyaarts.com").exists())

    @mock.patch.object(ZoomService, "list_users")
    def test_all_validation_failure_reasons_add_teacher_to_blocked_explicit_ids(self, mock_list_users):
        """
        Verify that all failure conditions (missing user, inactive, non-teacher, name mismatch,
        phone mismatch, excluded teacher, duplicate explicit mapping) populate blocked_explicit_teacher_ids.
        """
        # User 201: Non-teacher
        User.objects.create_user(id=201, username="u201", email="u201@natya.internal", is_teacher=False)
        # User 202: Inactive teacher
        User.objects.create_user(id=202, username="u202", email="u202@natya.internal", is_teacher=True, is_active=False)
        # User 203: Name mismatch
        User.objects.create_user(id=203, username="u203", email="u203@natya.internal", first_name="Real Name", is_teacher=True)
        # User 204: Phone mismatch
        User.objects.create_user(id=204, username="u204", email="u204@natya.internal", first_name="Valid Name", phone_number="+911111111111", is_teacher=True)
        # User 205 & 206: Duplicate explicit zoom email
        User.objects.create_user(id=205, username="u205", email="u205@natya.internal", is_teacher=True)
        User.objects.create_user(id=206, username="u206", email="u206@natya.internal", is_teacher=True)

        explicit_dict = {
            1: "excluded@gmail.com",
            9999: "missing@gmail.com",
            201: "nonteacher@gmail.com",
            202: "inactive@gmail.com",
            203: {"email": "namemismatch@gmail.com", "expected_name": "Wrong Name"},
            204: {"email": "phonemismatch@gmail.com", "expected_name": "Valid Name", "expected_phone": "+919999999999"},
            205: "duplicate@gmail.com",
            206: "duplicate@gmail.com",
        }

        mock_list_users.return_value = self._sample_zoom_users()

        cmd = Command()
        recon = cmd._reconcile(
            db_snapshot={"hosts": [], "by_user_id": {}, "by_email": {}},
            zoom_inventory={"licensed_users": [], "duplicate_zoom_emails": {}, "basic_users": [], "pending_users": []},
            expected_seats=40,
            explicit_mappings=explicit_dict
        )

        blocked_ids = recon["blocked_explicit_teacher_ids"]
        self.assertIn(1, blocked_ids)      # Excluded
        self.assertIn(9999, blocked_ids)   # Missing
        self.assertIn(201, blocked_ids)    # Non-teacher
        self.assertIn(202, blocked_ids)    # Inactive
        self.assertIn(203, blocked_ids)    # Name mismatch
        self.assertIn(204, blocked_ids)    # Phone mismatch
        self.assertIn(205, blocked_ids)    # Duplicate explicit
        self.assertIn(206, blocked_ids)    # Duplicate explicit

    # -------------------------------------------------------------------------
    # 11. Dedicated Host Concurrency, Overlap Rejection, and Isolation
    # -------------------------------------------------------------------------
    def test_dedicated_host_concurrency_is_strictly_one_and_rejects_overlap(self):
        t189 = User.objects.create_user(
            id=189, username="t189", email="teacher_189@natya.internal", is_teacher=True
        )
        host189 = ZoomHostAccount.objects.create(
            zoom_user_id="zoom_189",
            email="abhinandapadman@gmail.com",
            status=ZoomHostAccount.Status.ACTIVE,
            account_type=ZoomHostAccount.AccountType.DEDICATED,
            assigned_instructor=t189,
            max_concurrency=5  # Deliberately misconfigured: code must enforce 1 for DEDICATED
        )

        course = Course.objects.create(title="Bharathanatyam Advanced", is_published=True)
        batch = LiveBatch.objects.create(course=course, instructor=t189, batch_type=LiveBatch.BatchType.GROUP)

        now = timezone.now() + datetime.timedelta(days=1)
        lc1 = LiveClass.objects.create(
            course=course, batch=batch, title="Class 1",
            scheduled_start=now, duration_minutes=60, meeting_provider="ZOOM"
        )
        # Class 2 scheduled at a future day in DB, but tested against overlapping timeslot
        lc2 = LiveClass.objects.create(
            course=course, batch=batch, title="Class 2",
            scheduled_start=now + datetime.timedelta(days=2), duration_minutes=60, meeting_provider="ZOOM"
        )

        # First reservation on dedicated host succeeds
        res1 = select_and_reserve_host(lc1, now, 60)
        self.assertEqual(res1.host_account_id, host189.id)
        self.assertEqual(res1.status, HostReservation.Status.PROVISIONAL)

        # Overlapping timeslot reservation for same dedicated host must raise HostCapacityExhaustedError
        with self.assertRaises(HostCapacityExhaustedError) as ctx:
            select_and_reserve_host(lc2, now + datetime.timedelta(minutes=30), 60)
        self.assertIn("full concurrency", str(ctx.exception))

    def test_cross_teacher_host_stealing_is_strictly_rejected(self):
        t189 = User.objects.create_user(
            id=189, username="t189", email="teacher_189@natya.internal", is_teacher=True
        )
        t51 = User.objects.create_user(
            id=51, username="t51", email="teacher_51@natya.internal", is_teacher=True
        )
        host189 = ZoomHostAccount.objects.create(
            zoom_user_id="zoom_189",
            email="abhinandapadman@gmail.com",
            status=ZoomHostAccount.Status.ACTIVE,
            account_type=ZoomHostAccount.AccountType.DEDICATED,
            assigned_instructor=t189,
            max_concurrency=1
        )
        host51 = ZoomHostAccount.objects.create(
            zoom_user_id="zoom_51",
            email="akshayakalamandalam64@gmail.com",
            status=ZoomHostAccount.Status.ACTIVE,
            account_type=ZoomHostAccount.AccountType.DEDICATED,
            assigned_instructor=t51,
            max_concurrency=1
        )

        course = Course.objects.create(title="Mohiniyattam", is_published=True)
        batch51 = LiveBatch.objects.create(course=course, instructor=t51, batch_type=LiveBatch.BatchType.GROUP)

        now = timezone.now() + datetime.timedelta(days=2)
        lc_t51 = LiveClass.objects.create(
            course=course, batch=batch51, title="Akshaya Class",
            scheduled_start=now, duration_minutes=60, meeting_provider="ZOOM"
        )

        # Teacher 51 attempting to select Teacher 189's dedicated host must raise HostCapacityExhaustedError
        with self.assertRaises(HostCapacityExhaustedError) as ctx:
            select_and_reserve_host(lc_t51, now, 60, preferred_host_id=host189.id)
        self.assertIn("Unauthorized host selection", str(ctx.exception))

    def test_cancellation_release_and_scheduling_rollback_frees_dedicated_slot(self):
        t189 = User.objects.create_user(
            id=189, username="t189", email="teacher_189@natya.internal", is_teacher=True
        )
        host189 = ZoomHostAccount.objects.create(
            zoom_user_id="zoom_189",
            email="abhinandapadman@gmail.com",
            status=ZoomHostAccount.Status.ACTIVE,
            account_type=ZoomHostAccount.AccountType.DEDICATED,
            assigned_instructor=t189,
            max_concurrency=1
        )
        course = Course.objects.create(title="Kathakali", is_published=True)
        batch = LiveBatch.objects.create(course=course, instructor=t189, batch_type=LiveBatch.BatchType.GROUP)

        now = timezone.now() + datetime.timedelta(days=3)
        lc1 = LiveClass.objects.create(
            course=course, batch=batch, title="Class To Cancel",
            scheduled_start=now, duration_minutes=60, meeting_provider="ZOOM"
        )
        res1 = select_and_reserve_host(lc1, now, 60)
        self.assertEqual(res1.host_account_id, host189.id)

        # Cancel class and release the reservation (e.g., cancellation or failure compensation)
        lc1.status = LiveClass.ClassStatus.CANCELLED
        lc1.save(update_fields=['status'])
        res1.status = HostReservation.Status.RELEASED
        res1.save(update_fields=['status'])

        # Now scheduling another class in the exact same timeslot succeeds!
        lc2 = LiveClass.objects.create(
            course=course, batch=batch, title="Replacement Class",
            scheduled_start=now, duration_minutes=60, meeting_provider="ZOOM"
        )
        res2 = select_and_reserve_host(lc2, now, 60)
        self.assertEqual(res2.host_account_id, host189.id)
        self.assertEqual(res2.status, HostReservation.Status.PROVISIONAL)

    @mock.patch.object(ZoomService, "list_users")
    def test_zoom_pending_invitations_are_reported_in_audit_separately(self, mock_list_users):
        active_users = self._sample_zoom_users()
        pending_users = [
            {
                "id": "pending_zoom_1",
                "email": "pending_teacher@natyaarts.com",
                "first_name": "Pending",
                "last_name": "Teacher",
                "display_name": "Pending Teacher",
                "type": 2,
                "status": "pending",
            }
        ]

        def side_effect(status="active", page_size=300):
            if status == "active":
                return active_users
            elif status == "pending":
                return pending_users
            return []
        mock_list_users.side_effect = side_effect

        out = StringIO()
        call_command("sync_zoom_host_accounts", stdout=out)

        output = out.getvalue()
        self.assertIn("Pending Invitations:        1", output)
        # Pending user is NOT created as host
        self.assertFalse(ZoomHostAccount.objects.filter(zoom_user_id="pending_zoom_1").exists())


@override_settings(**TEST_ZOOM_CREDS)
class ZoomServiceListUsersPaginationTests(TestCase):
    """
    Direct unit tests for ZoomService.list_users ensuring next_page_token pagination,
    transient error handling, and response schema enforcement.
    """

    @mock.patch.object(ZoomService, "get_access_token", return_value="fake_token")
    @mock.patch("requests.get")
    def test_zoom_api_pagination_fetches_multiple_pages_using_next_page_token(self, mock_get, mock_token):
        page1_resp = mock.MagicMock()
        page1_resp.status_code = 200
        page1_resp.json.return_value = {
            "page_count": 2,
            "page_size": 2,
            "total_records": 3,
            "next_page_token": "page_2_token",
            "users": [
                {
                    "id": "u1",
                    "email": "u1@natyaarts.com",
                    "first_name": "U1",
                    "last_name": "One",
                    "type": 2,
                    "status": "active",
                },
                {
                    "id": "u2",
                    "email": "u2@natyaarts.com",
                    "first_name": "U2",
                    "last_name": "Two",
                    "type": 2,
                    "status": "active",
                },
            ]
        }

        page2_resp = mock.MagicMock()
        page2_resp.status_code = 200
        page2_resp.json.return_value = {
            "page_count": 2,
            "page_size": 2,
            "total_records": 3,
            "next_page_token": "",  # End of pages
            "users": [
                {
                    "id": "u3",
                    "email": "u3@natyaarts.com",
                    "first_name": "U3",
                    "last_name": "Three",
                    "type": 1,
                    "status": "active",
                }
            ]
        }

        mock_get.side_effect = [page1_resp, page2_resp]

        users = ZoomService.list_users(page_size=2)

        self.assertEqual(len(users), 3)
        self.assertEqual(mock_get.call_count, 2)

        # Verify page 2 included next_page_token param
        second_call_params = mock_get.call_args_list[1][1]["params"]
        self.assertEqual(second_call_params.get("next_page_token"), "page_2_token")

    @mock.patch.object(ZoomService, "get_access_token", return_value="fake_token")
    @mock.patch("requests.get")
    def test_list_users_network_timeout_raises_transient_error(self, mock_get, mock_token):
        mock_get.side_effect = requests.Timeout("Network timeout")

        with self.assertRaises(ZoomTransientError) as ctx:
            ZoomService.list_users()

        self.assertIn("Network error listing users", str(ctx.exception))

    @mock.patch.object(ZoomService, "get_access_token", return_value="fake_token")
    @mock.patch("requests.get")
    def test_list_users_malformed_json_raises_lifecycle_error(self, mock_get, mock_token):
        resp = mock.MagicMock()
        resp.status_code = 200
        resp.json.return_value = "invalid non-dict payload"
        mock_get.return_value = resp

        with self.assertRaises(ZoomAPIError) as ctx:
            ZoomService.list_users()

        self.assertIn("Invalid JSON response listing users", str(ctx.exception))


@override_settings(**TEST_ZOOM_CREDS)
class ZoomCancellationRealWorkflowTests(TestCase):
    """
    Local integration tests verifying actual cancellation behaviour across the real
    service (cancel_zoom_meeting) and API endpoint (LiveClassViewSet.cancel):
    - Confirmed Zoom deletion transitions HostReservation to CANCELLED.
    - CANCELLED reservations immediately free host capacity (no longer consume capacity).
    - Ambiguous Zoom deletion (e.g. timeout) holds HostReservation in MANUAL_REVIEW.
    - Reservations in MANUAL_REVIEW continue to consume capacity, blocking replacement.
    - Replacement booking in the exact same timeslot succeeds after confirmed cancellation.
    - Real LiveClassViewSet.cancel endpoint releases host reservation.
    """
    def setUp(self):
        super().setUp()
        self.teacher = User.objects.create_user(
            username="cancel_teacher_01",
            email="cancel_teacher01@natyaarts.com",
            first_name="Radha",
            last_name="Instructor",
            phone_number="+919876543210",
            is_teacher=True
        )
        self.course = Course.objects.create(
            title="Cancellation Test Course"
        )
        self.batch = LiveBatch.objects.create(
            course=self.course,
            instructor=self.teacher,
            batch_type=LiveBatch.BatchType.GROUP
        )
        self.host = ZoomHostAccount.objects.create(
            zoom_user_id="zoom_cancel_host_01",
            email="cancel_dedicated_host@gmail.com",
            status=ZoomHostAccount.Status.ACTIVE,
            account_type=ZoomHostAccount.AccountType.DEDICATED,
            assigned_instructor=self.teacher,
            max_concurrency=1
        )
        self.start_time = timezone.now() + datetime.timedelta(days=1, hours=2)
        self.duration = 60

        self.live_class = LiveClass.objects.create(
            course=self.course,
            batch=self.batch,
            title="Live Class To Be Cancelled",
            scheduled_start=self.start_time,
            duration_minutes=self.duration,
            meeting_provider="ZOOM",
            status=LiveClass.ClassStatus.SCHEDULED,
            zoom_meeting_id="99988877766",
            zoom_uuid="uuid_test_cancel_99"
        )
        self.reservation = select_and_reserve_host(
            live_class=self.live_class,
            start_time=self.start_time,
            duration_minutes=self.duration
        )
        self.reservation.status = HostReservation.Status.ACTIVE
        self.reservation.save()

        self.zmr = ZoomMeetingRecord.objects.create(
            live_class=self.live_class,
            host_account=self.host,
            zoom_meeting_id="99988877766",
            state=ZoomMeetingRecord.State.ASSIGNED,
            is_current=True
        )

    @mock.patch.object(ZoomService, "delete_meeting")
    def test_successful_zoom_deletion_cancels_reservation_and_frees_capacity(self, mock_delete):
        mock_delete.return_value = None  # Confirmed 204 No Content

        # Update LiveClass to CANCELLED (as views.py does in step 1 of cancel)
        self.live_class.status = LiveClass.ClassStatus.CANCELLED
        self.live_class.save(update_fields=['status'])

        result = cancel_zoom_meeting(self.live_class.id, reason="Teacher requested cancellation")

        self.assertEqual(result["status"], "SUCCESS")
        self.assertEqual(result["cancelled_meeting_id"], "99988877766")
        mock_delete.assert_called_once_with("99988877766")

        self.reservation.refresh_from_db()
        self.assertEqual(self.reservation.status, HostReservation.Status.CANCELLED)
        self.assertEqual(self.reservation.release_reason, "Teacher requested cancellation")

        self.zmr.refresh_from_db()
        self.assertEqual(self.zmr.state, ZoomMeetingRecord.State.RELEASED)
        self.assertFalse(self.zmr.is_current)

        # Replacement booking in the exact same timeslot now succeeds
        lc_replacement = LiveClass.objects.create(
            course=self.course,
            batch=self.batch,
            title="Replacement Class",
            scheduled_start=self.start_time,
            duration_minutes=self.duration,
            meeting_provider="ZOOM",
            status=LiveClass.ClassStatus.SCHEDULED
        )
        new_res = select_and_reserve_host(lc_replacement, self.start_time, self.duration)
        self.assertEqual(new_res.host_account_id, self.host.id)
        self.assertEqual(new_res.status, HostReservation.Status.PROVISIONAL)

    @mock.patch.object(ZoomService, "delete_meeting")
    def test_ambiguous_zoom_deletion_holds_reservation_in_manual_review_and_blocks_capacity(self, mock_delete):
        mock_delete.side_effect = requests.Timeout("Network timeout while calling Zoom delete API")

        self.live_class.status = LiveClass.ClassStatus.CANCELLED
        self.live_class.save(update_fields=['status'])

        with self.assertRaises(ZoomLifecycleError) as ctx:
            cancel_zoom_meeting(self.live_class.id, reason="Timeout during cancellation")

        self.assertIn("ambiguous outcome deleting meeting", str(ctx.exception))
        self.assertIn("Host held in MANUAL_REVIEW", str(ctx.exception))

        self.reservation.refresh_from_db()
        self.assertEqual(self.reservation.status, HostReservation.Status.MANUAL_REVIEW)
        self.assertIn("Ambiguous delete outcome on Zoom", self.reservation.release_reason)

        self.zmr.refresh_from_db()
        self.assertEqual(self.zmr.state, ZoomMeetingRecord.State.COMPENSATING)

        # Attempt replacement booking in same timeslot - must fail because MANUAL_REVIEW holds capacity
        lc_replacement = LiveClass.objects.create(
            course=self.course,
            batch=self.batch,
            title="Blocked Replacement Class",
            scheduled_start=self.start_time,
            duration_minutes=self.duration,
            meeting_provider="ZOOM",
            status=LiveClass.ClassStatus.SCHEDULED
        )
        with self.assertRaises(HostCapacityExhaustedError) as ctx:
            select_and_reserve_host(lc_replacement, self.start_time, self.duration)

        self.assertIn("at full concurrency", str(ctx.exception))

    @mock.patch.object(ZoomService, "delete_meeting")
    def test_real_liveclass_api_cancellation_endpoint_releases_host(self, mock_delete):
        mock_delete.return_value = None
        factory = APIRequestFactory()

        request = factory.post(
            f"/api/courses/live-classes/{self.live_class.id}/cancel/",
            {"reason": "Cancelled via admin modal"},
            format="json"
        )
        force_authenticate(request, user=self.teacher)

        view = LiveClassViewSet.as_view({"post": "cancel"})
        response = view(request, pk=self.live_class.id)

        self.assertEqual(response.status_code, 200)
        self.live_class.refresh_from_db()
        self.assertEqual(self.live_class.status, LiveClass.ClassStatus.CANCELLED)
        self.assertEqual(self.live_class.cancellation_reason, "Cancelled via admin modal")

        self.reservation.refresh_from_db()
        self.assertEqual(self.reservation.status, HostReservation.Status.CANCELLED)
        self.assertEqual(self.reservation.release_reason, "Cancelled via admin modal")

        self.zmr.refresh_from_db()
        self.assertEqual(self.zmr.state, ZoomMeetingRecord.State.RELEASED)

        # Confirm capacity is freed for replacement class
        lc_replacement = LiveClass.objects.create(
            course=self.course,
            batch=self.batch,
            title="API Replacement Class",
            scheduled_start=self.start_time,
            duration_minutes=self.duration,
            meeting_provider="ZOOM",
            status=LiveClass.ClassStatus.SCHEDULED
        )
        new_res = select_and_reserve_host(lc_replacement, self.start_time, self.duration)
        self.assertEqual(new_res.host_account_id, self.host.id)
