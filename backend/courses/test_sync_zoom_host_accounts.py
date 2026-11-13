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
from io import StringIO
from unittest import mock

import requests
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from courses.models import ZoomHostAccount
from courses.services.zoom import ZoomService, ZoomAPIError, ZoomTransientError


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
