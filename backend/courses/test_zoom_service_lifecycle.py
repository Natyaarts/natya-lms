import time
import unittest
import pickle
from unittest.mock import patch, MagicMock
from django.core.exceptions import ImproperlyConfigured
from django.core.cache import cache
import requests
from requests.exceptions import ChunkedEncodingError, ConnectionError as ReqConnectionError, Timeout as ReqTimeout

from courses.services.zoom import (
    ZoomService,
    ZoomAPIError,
    ZoomAuthError,
    ZoomClientError,
    ZoomNotFoundError,
    ZoomRateLimitError,
    ZoomTransientError,
    ZoomAmbiguousOutcomeError,
    validate_host_id,
    validate_meeting_id,
    _calculate_cache_ttl,
    _sanitize_error_payload
)


class ZoomServiceLifecycleTests(unittest.TestCase):
    def setUp(self):
        cache.clear()

    def tearDown(self):
        cache.clear()

    # -------------------------------------------------------------------------
    # 1. Credentials Verification
    # -------------------------------------------------------------------------
    def test_get_credentials_success(self):
        with patch('courses.services.zoom.settings') as mock_settings:
            mock_settings.ZOOM_ACCOUNT_ID = 'acc_test_123'
            mock_settings.ZOOM_CLIENT_ID = 'cli_test_456'
            mock_settings.ZOOM_CLIENT_SECRET = 'sec_test_789'

            acc, cli, sec = ZoomService.get_credentials()
            self.assertEqual(acc, 'acc_test_123')
            self.assertEqual(cli, 'cli_test_456')
            self.assertEqual(sec, 'sec_test_789')

    def test_get_credentials_missing_raises_improperly_configured(self):
        with patch('courses.services.zoom.settings') as mock_settings, \
             patch.dict('os.environ', {}, clear=True):
            mock_settings.ZOOM_ACCOUNT_ID = ''
            mock_settings.ZOOM_CLIENT_ID = 'cli_123'
            mock_settings.ZOOM_CLIENT_SECRET = 'sec_123'

            with self.assertRaises(ImproperlyConfigured):
                ZoomService.get_credentials()

    # -------------------------------------------------------------------------
    # 2. Host ID Validation
    # -------------------------------------------------------------------------
    def test_validate_host_id_valid_formats(self):
        self.assertEqual(validate_host_id("instructor.ramya@natya.in"), "instructor.ramya@natya.in")
        self.assertEqual(validate_host_id("test.user+tag@domain.co.uk"), "test.user+tag@domain.co.uk")
        self.assertEqual(validate_host_id("abCD123456_-Xyz"), "abCD123456_-Xyz")

    def test_validate_host_id_rejects_me_and_case_variants(self):
        for invalid in ["me", "ME", "Me", " me ", "\nme\t"]:
            with self.assertRaises(ValueError) as ctx:
                validate_host_id(invalid)
            self.assertIn("Routing to '/users/me/' is strictly prohibited", str(ctx.exception))

    def test_validate_host_id_rejects_empty_and_whitespace(self):
        for invalid in [None, "", "   ", "\t\n"]:
            with self.assertRaises(ValueError) as ctx:
                validate_host_id(invalid)
            self.assertIn("host_id is required", str(ctx.exception))

    def test_validate_host_id_rejects_traversal_and_separators(self):
        for invalid in ["../admin", "host/user", "host\\user", "host%2Fuser", "user\x00root"]:
            with self.assertRaises(ValueError) as ctx:
                validate_host_id(invalid)
            self.assertIn("Invalid host_id format", str(ctx.exception))

    # -------------------------------------------------------------------------
    # 3. Meeting ID Validation (Numeric IDs & Zoom Base64 UUIDs)
    # -------------------------------------------------------------------------
    def test_validate_meeting_id_numeric_lengths_8_to_15(self):
        # 8 to 15 digits are valid numeric meeting IDs
        self.assertEqual(validate_meeting_id("12345678"), "12345678")
        self.assertEqual(validate_meeting_id(123456789), "123456789")
        self.assertEqual(validate_meeting_id("123456789"), "123456789")
        self.assertEqual(validate_meeting_id("1234567890"), "1234567890")
        self.assertEqual(validate_meeting_id("98765432101"), "98765432101")
        self.assertEqual(validate_meeting_id("123456789012"), "123456789012")
        self.assertEqual(validate_meeting_id("123456789012345"), "123456789012345")

    def test_validate_meeting_id_rejects_invalid_numeric_lengths(self):
        # Less than 8 digits or more than 15 digits rejected
        for invalid in ["12345", "1234567", "1234567890123456"]:
            with self.assertRaises(ValueError) as ctx:
                validate_meeting_id(invalid)
            self.assertIn("Invalid meeting_id format", str(ctx.exception))

    def test_validate_meeting_id_standard_uuid(self):
        uuid_str = "441b4b2c-29b1-4f9c-b1d5-88f57f49552d"
        self.assertEqual(validate_meeting_id(uuid_str), uuid_str)

    def test_validate_meeting_id_base64_uuid_single_encoded(self):
        # Zoom base64 UUID with internal single slashes and plus/equals signs
        raw_uuid = "HGk+s/8vR/6V1V+G3y8xLg=="
        encoded = validate_meeting_id(raw_uuid)
        self.assertEqual(encoded, "HGk%2Bs%2F8vR%2F6V1V%2BG3y8xLg%3D%3D")

    def test_validate_meeting_id_base64_uuid_with_leading_slash_double_encoded(self):
        # Zoom API requirement: UUID beginning with '/' MUST be double URL-encoded
        raw_uuid = "/HGks8vR/6V1V+G3y8xLg=="
        encoded = validate_meeting_id(raw_uuid)
        self.assertEqual(encoded, "%252FHGks8vR%252F6V1V%252BG3y8xLg%253D%253D")

    def test_validate_meeting_id_base64_uuid_with_double_slash_double_encoded(self):
        # Zoom API requirement: UUID containing '//' MUST be double URL-encoded
        raw_uuid = "HGks//8vR+6V1V/G3y8xLg=="
        encoded = validate_meeting_id(raw_uuid)
        self.assertEqual(encoded, "HGks%252F%252F8vR%252B6V1V%252FG3y8xLg%253D%253D")

    def test_validate_meeting_id_rejects_empty_and_whitespace(self):
        for invalid in [None, "", "   ", "\t"]:
            with self.assertRaises(ValueError) as ctx:
                validate_meeting_id(invalid)
            self.assertIn("meeting_id is required", str(ctx.exception))

    def test_validate_meeting_id_rejects_traversal_and_separators(self):
        for invalid in ["../123456789", "98765/4321", "98765\\4321", "98765432101\n", "uuid\x00injection"]:
            with self.assertRaises(ValueError) as ctx:
                validate_meeting_id(invalid)
            self.assertIn("Invalid meeting_id format", str(ctx.exception))

    @patch.object(ZoomService, 'get_access_token', return_value='valid_tok')
    @patch('requests.get')
    def test_crud_endpoints_accept_valid_uuids(self, mock_get, mock_tok):
        mock_response = MagicMock(status_code=200)
        mock_response.json.return_value = {"id": 123456789, "topic": "UUID Class"}
        mock_get.return_value = mock_response

        # Call get_meeting with a UUID containing a slash
        res = ZoomService.get_meeting("HGk+s/8vR/6V1V+G3y8xLg==")
        self.assertEqual(res["topic"], "UUID Class")
        # Verify URL interpolation encoded the UUID safely
        args, _ = mock_get.call_args
        self.assertEqual(args[0], "https://api.zoom.us/v2/meetings/HGk%2Bs%2F8vR%2F6V1V%2BG3y8xLg%3D%3D")

    # -------------------------------------------------------------------------
    # 4. Token TTL Calculation & Proportional Buffering
    # -------------------------------------------------------------------------
    def test_calculate_cache_ttl_proportional_and_boundaries(self):
        self.assertEqual(_calculate_cache_ttl(3600), 3300)
        self.assertEqual(_calculate_cache_ttl(36000), 35700)
        self.assertEqual(_calculate_cache_ttl(300), 240)
        self.assertEqual(_calculate_cache_ttl(60), 30)
        self.assertEqual(_calculate_cache_ttl(10), 0)
        self.assertEqual(_calculate_cache_ttl(4), 0)
        self.assertEqual(_calculate_cache_ttl(0), 0)
        self.assertEqual(_calculate_cache_ttl(-100), 0)
        self.assertEqual(_calculate_cache_ttl(None), 0)
        self.assertEqual(_calculate_cache_ttl("invalid"), 0)

    @patch.object(ZoomService, 'get_credentials', return_value=('acc_id', 'cli_id', 'sec_key'))
    @patch('requests.post')
    def test_get_access_token_skips_cache_on_nonpositive_expires_in(self, mock_post, mock_creds):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "access_token": "uncached_token_999",
            "expires_in": 0
        }
        mock_post.return_value = mock_response

        token = ZoomService.get_access_token()
        self.assertEqual(token, "uncached_token_999")
        self.assertIsNone(cache.get(ZoomService.CACHE_KEY_ACCESS_TOKEN))

    # -------------------------------------------------------------------------
    # 5. Token Caching, Distributed Locking & Concurrency
    # -------------------------------------------------------------------------
    @patch.object(ZoomService, 'get_credentials', return_value=('acc_id', 'cli_id', 'sec_key'))
    @patch('requests.post')
    def test_get_access_token_success_caches_in_redis(self, mock_post, mock_creds):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "access_token": "token_mock_abc123",
            "expires_in": 3600
        }
        mock_post.return_value = mock_response

        token = ZoomService.get_access_token()
        self.assertEqual(token, "token_mock_abc123")
        self.assertEqual(cache.get(ZoomService.CACHE_KEY_ACCESS_TOKEN), "token_mock_abc123")
        mock_post.assert_called_once()

        # Second call should read from cache without HTTP post
        cached_token = ZoomService.get_access_token()
        self.assertEqual(cached_token, "token_mock_abc123")
        self.assertEqual(mock_post.call_count, 1)

    @patch.object(ZoomService, 'get_credentials', return_value=('acc_id', 'cli_id', 'sec_key'))
    @patch('requests.post')
    def test_get_access_token_force_refresh_bypasses_cache(self, mock_post, mock_creds):
        cache.set(ZoomService.CACHE_KEY_ACCESS_TOKEN, "old_stale_token", timeout=3600)

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "access_token": "fresh_token_xyz999",
            "expires_in": 3600
        }
        mock_post.return_value = mock_response

        token = ZoomService.get_access_token(force_refresh=True)
        self.assertEqual(token, "fresh_token_xyz999")
        self.assertEqual(cache.get(ZoomService.CACHE_KEY_ACCESS_TOKEN), "fresh_token_xyz999")
        mock_post.assert_called_once()

    @patch.object(ZoomService, 'get_credentials', return_value=('acc_id', 'cli_id', 'sec_key'))
    @patch('requests.post')
    def test_get_access_token_handles_auth_failure_401(self, mock_post, mock_creds):
        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_response.text = '{"error": "invalid_client"}'
        mock_response.json.return_value = {"error": "invalid_client"}
        mock_post.return_value = mock_response

        with self.assertRaises(ZoomAuthError):
            ZoomService.get_access_token()

    @patch.object(ZoomService, 'get_credentials', return_value=('acc_id', 'cli_id', 'sec_key'))
    @patch('requests.post')
    def test_get_access_token_handles_rate_limit_429(self, mock_post, mock_creds):
        mock_response = MagicMock()
        mock_response.status_code = 429
        mock_response.headers = {"Retry-After": "30"}
        mock_response.text = '{"message": "Rate limit exceeded"}'
        mock_response.json.return_value = {"message": "Rate limit exceeded"}
        mock_post.return_value = mock_response

        with self.assertRaises(ZoomRateLimitError) as ctx:
            ZoomService.get_access_token()
        self.assertEqual(ctx.exception.retry_after, "30")

    @patch.object(ZoomService, 'get_credentials', return_value=('acc_id', 'cli_id', 'sec_key'))
    @patch('requests.post', side_effect=requests.Timeout("Connection timed out"))
    def test_get_access_token_network_timeout_raises_transient_error(self, mock_post, mock_creds):
        with self.assertRaises(ZoomTransientError):
            ZoomService.get_access_token()

    def test_raw_redis_client_resolution_and_prefixed_key(self):
        """
        Verifies that when a Redis cache backend is configured, ZoomService correctly resolves
        the underlying redis.Redis connection and applies Django cache key prefixing.
        """
        mock_redis_client = MagicMock()
        mock_cache_backend = MagicMock()
        mock_cache_backend.get_client.return_value = mock_redis_client

        with patch.object(cache, '_cache', mock_cache_backend):
            prefixed = cache.make_key(ZoomService.CACHE_KEY_REFRESH_LOCK)
            client = ZoomService._get_raw_redis_client(prefixed, write=True)
            self.assertEqual(client, mock_redis_client)
            mock_cache_backend.get_client.assert_called_with(prefixed, write=True)

    def test_atomic_redis_locking_and_lua_release(self):
        """
        Verifies that lock acquisition calls SET NX EX with raw bytes, and
        release calls EVAL with atomic Lua script and matching raw bytes.
        """
        mock_redis_client = MagicMock()
        mock_cache_backend = MagicMock()
        mock_cache_backend.get_client.return_value = mock_redis_client
        mock_redis_client.set.return_value = True

        lock_key = ZoomService.CACHE_KEY_REFRESH_LOCK
        owner_token = "worker_uuid_777"
        prefixed = cache.make_key(lock_key)

        with patch.object(cache, '_cache', mock_cache_backend):
            # 1. Acquire
            acquired = ZoomService._acquire_lock(lock_key, owner_token)
            self.assertTrue(acquired)
            mock_redis_client.set.assert_called_with(
                prefixed,
                owner_token.encode('utf-8'),
                ex=ZoomService.LOCK_TIMEOUT_SECONDS,
                nx=True
            )

            # 2. Release
            ZoomService._release_lock(lock_key, owner_token)
            mock_redis_client.eval.assert_called_with(
                ZoomService.LUA_COMPARE_AND_DELETE,
                1,
                prefixed,
                owner_token.encode('utf-8')
            )

    def test_distributed_lock_owner_isolation_prevents_stale_release(self):
        lock_key = ZoomService.CACHE_KEY_LOCK_TOKEN_REFRESH
        token_a = "worker_a_uuid_111"
        token_b = "worker_b_uuid_222"

        cache.set(lock_key, token_b, timeout=25)
        ZoomService._release_lock(lock_key, token_a)
        self.assertEqual(cache.get(lock_key), token_b)

        ZoomService._release_lock(lock_key, token_b)
        self.assertIsNone(cache.get(lock_key))

    @patch.object(ZoomService, 'get_credentials', return_value=('acc_id', 'cli_id', 'sec_key'))
    @patch('requests.post')
    def test_concurrent_waiter_retrieves_token_cached_by_lock_holder(self, mock_post, mock_creds):
        lock_key = ZoomService.CACHE_KEY_LOCK_TOKEN_REFRESH
        token_key = ZoomService.CACHE_KEY_ACCESS_TOKEN

        cache.set(lock_key, "other_worker_uuid", timeout=25)

        def fake_sleep(duration):
            cache.set(token_key, "token_from_concurrent_worker", timeout=3600)
            cache.delete(lock_key)

        with patch('time.sleep', side_effect=fake_sleep):
            token = ZoomService.get_access_token()

        self.assertEqual(token, "token_from_concurrent_worker")
        mock_post.assert_not_called()

    @patch.object(ZoomService, 'get_credentials', return_value=('acc_id', 'cli_id', 'sec_key'))
    @patch('requests.post')
    def test_concurrent_forced_refresh_waiter_waits_for_holder_and_reuses_token(self, mock_post, mock_creds):
        """
        When force_refresh=True is passed with rejected_token:
        Worker 2 fails lock acquisition because Worker 1 holds it.
        Worker 2 waits in the waiter loop.
        Worker 1 caches a fresh token.
        Worker 2 retrieves the fresh token without issuing a secondary OAuth request.
        """
        lock_key = ZoomService.CACHE_KEY_LOCK_TOKEN_REFRESH
        token_key = ZoomService.CACHE_KEY_ACCESS_TOKEN
        rejected_token = "stale_rejected_token_000"
        fresh_token = "newly_refreshed_token_111"

        cache.set(lock_key, "worker_1_uuid", timeout=25)

        def fake_sleep(duration):
            cache.set(token_key, fresh_token, timeout=3600)
            cache.delete(lock_key)

        with patch('time.sleep', side_effect=fake_sleep):
            token = ZoomService.get_access_token(force_refresh=True, rejected_token=rejected_token)

        self.assertEqual(token, fresh_token)
        mock_post.assert_not_called()

    def test_redis_outage_raises_transient_error_without_direct_oauth_fallback(self):
        """
        When Redis lock acquisition raises an exception, get_access_token() must NOT
        fall back to direct uncoordinated OAuth exchange; it must raise ZoomTransientError.
        """
        with patch.object(ZoomService, '_acquire_lock', side_effect=Exception("Redis connection refused")):
            with self.assertRaises(ZoomTransientError) as ctx:
                ZoomService.get_access_token()
            self.assertIn("Redis distributed locking is unavailable", str(ctx.exception))

    def test_waiter_timeout_raises_transient_error(self):
        """
        If a waiter thread polls for LOCK_WAIT_TIMEOUT and no token is populated,
        it must fail safely and raise ZoomTransientError.
        """
        lock_key = ZoomService.CACHE_KEY_LOCK_TOKEN_REFRESH
        cache.set(lock_key, "holding_worker_uuid", timeout=25)

        with patch.object(ZoomService, 'LOCK_WAIT_TIMEOUT', 0.1):
            with self.assertRaises(ZoomTransientError) as ctx:
                ZoomService.get_access_token()
            self.assertIn("Timed out waiting for concurrent token refresh", str(ctx.exception))

    # -------------------------------------------------------------------------
    # 6. Token Invalidation Safety
    # -------------------------------------------------------------------------
    def test_invalidate_cached_token_removes_matching_rejected_token(self):
        token_key = ZoomService.CACHE_KEY_ACCESS_TOKEN
        cache.set(token_key, "token_to_invalidate", timeout=3600)

        ZoomService._invalidate_cached_token("token_to_invalidate")
        self.assertIsNone(cache.get(token_key))

    def test_invalidate_cached_token_preserves_newer_token_from_peer(self):
        """
        If a delayed 401 arrives for token_v1, but another worker already refreshed
        and cached token_v2, invalidating token_v1 must NOT delete token_v2.
        """
        token_key = ZoomService.CACHE_KEY_ACCESS_TOKEN
        cache.set(token_key, "newer_token_v2", timeout=3600)

        ZoomService._invalidate_cached_token("older_rejected_token_v1")
        self.assertEqual(cache.get(token_key), "newer_token_v2")

    @patch.object(ZoomService, 'get_credentials', return_value=('acc_id', 'cli_id', 'sec_key'))
    @patch('requests.post', side_effect=ZoomAuthError("Bad client credentials"))
    def test_failed_refresh_leaves_rejected_token_invalidated(self, mock_post, mock_creds):
        """
        If a refresh request fails, the old rejected token has already been removed
        so ordinary callers will not continue reusing it.
        """
        token_key = ZoomService.CACHE_KEY_ACCESS_TOKEN
        cache.set(token_key, "rejected_token_xyz", timeout=3600)

        with self.assertRaises(ZoomAuthError):
            ZoomService.get_access_token(force_refresh=True, rejected_token="rejected_token_xyz")

        self.assertIsNone(cache.get(token_key))

    # -------------------------------------------------------------------------
    # 7. CREATE Meeting: Ambiguous Outcomes & Diagnostic Sanitization
    # -------------------------------------------------------------------------
    @patch.object(ZoomService, 'get_access_token', return_value='mock_valid_token')
    @patch('requests.post')
    def test_create_meeting_success_formats_payload_and_headers(self, mock_post, mock_token):
        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.json.return_value = {
            "id": 98765432101,
            "uuid": "mock-zoom-uuid-123",
            "host_id": "host_user_42",
            "join_url": "https://zoom.us/j/98765432101",
            "start_url": "https://zoom.us/s/98765432101?zak=secret_zak_token",
            "password": "class_password_123"
        }
        mock_post.return_value = mock_response

        res = ZoomService.create_meeting(
            host_id="host_user_42",
            topic="Bharatanatyam Adavu Basics",
            start_time_iso="2026-11-15T10:00:00Z",
            duration_minutes=90,
            alternative_hosts="teacher.priya@natya.in",
            agenda="NATYA_OP:1234:signed_token"
        )

        self.assertEqual(res["meeting_id"], "98765432101")
        self.assertEqual(res["uuid"], "mock-zoom-uuid-123")
        self.assertEqual(res["host_id"], "host_user_42")
        self.assertEqual(res["join_url"], "https://zoom.us/j/98765432101")
        self.assertEqual(res["host_url"], "https://zoom.us/s/98765432101?zak=secret_zak_token")
        self.assertEqual(res["password"], "class_password_123")

        mock_post.assert_called_once()
        args, kwargs = mock_post.call_args
        self.assertEqual(args[0], "https://api.zoom.us/v2/users/host_user_42/meetings")
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer mock_valid_token")
        payload = kwargs["json"]
        self.assertEqual(payload["topic"], "Bharatanatyam Adavu Basics")
        self.assertEqual(payload["start_time"], "2026-11-15T10:00:00Z")
        self.assertEqual(payload["duration"], 90)
        self.assertEqual(payload["settings"]["alternative_hosts"], "teacher.priya@natya.in")
        self.assertEqual(payload["agenda"], "NATYA_OP:1234:signed_token")
        self.assertFalse(payload["settings"]["join_before_host"])

    @patch.object(ZoomService, 'get_access_token', return_value='mock_valid_token')
    @patch('requests.post')
    def test_create_meeting_500_502_503_504_raise_ambiguous_outcome_error_with_sanitized_diagnostics(self, mock_post, mock_token):
        """
        HTTP 500-504 during CREATE must raise ZoomAmbiguousOutcomeError and sanitize
        any sensitive credentials in the response body.
        """
        for status_code in [500, 502, 503, 504]:
            mock_response = MagicMock()
            mock_response.status_code = status_code
            mock_response.text = f'{{"message": "Gateway Error {status_code}", "client_secret": "sensitive_sec", "zak": "sensitive_zak"}}'
            mock_post.return_value = mock_response

            with self.assertRaises(ZoomAmbiguousOutcomeError) as ctx:
                ZoomService.create_meeting(host_id="host_user_42", topic="Test Gateway Class")

            err = ctx.exception
            self.assertEqual(err.status_code, status_code)
            self.assertEqual(err.host_id, "host_user_42")
            self.assertEqual(err.topic, "Test Gateway Class")
            # Verify credentials in response_data and raw_text are redacted
            self.assertIn("caller must NOT blindly repeat creation without reconciliation", str(err))
            sanitized_text = err.response_data.get("sanitized_text", "")
            self.assertNotIn("sensitive_sec", sanitized_text)
            self.assertNotIn("sensitive_zak", sanitized_text)
            self.assertIn("[REDACTED]", sanitized_text)

    @patch.object(ZoomService, 'get_access_token', return_value='mock_valid_token')
    @patch('requests.post', side_effect=ReqTimeout("Socket read timeout"))
    def test_create_meeting_timeout_raises_ambiguous_outcome_error(self, mock_post, mock_token):
        with self.assertRaises(ZoomAmbiguousOutcomeError) as ctx:
            ZoomService.create_meeting(host_id="host_user_42", topic="Test Timeout Class")
        err = ctx.exception
        self.assertIn("Network error during meeting creation", str(err))
        self.assertEqual(err.host_id, "host_user_42")

    @patch.object(ZoomService, 'get_access_token', return_value='mock_valid_token')
    @patch('requests.post', side_effect=ReqConnectionError("Connection reset by peer"))
    def test_create_meeting_connection_error_raises_ambiguous_outcome_error(self, mock_post, mock_token):
        with self.assertRaises(ZoomAmbiguousOutcomeError) as ctx:
            ZoomService.create_meeting(host_id="host_user_42", topic="Test Conn Reset Class")
        err = ctx.exception
        self.assertIn("Network error during meeting creation", str(err))
        self.assertEqual(err.host_id, "host_user_42")

    @patch.object(ZoomService, 'get_access_token', return_value='mock_valid_token')
    @patch('requests.post', side_effect=ChunkedEncodingError("Connection broken: chunked stream truncated"))
    def test_create_meeting_chunked_encoding_error_raises_ambiguous_outcome_error(self, mock_post, mock_token):
        with self.assertRaises(ZoomAmbiguousOutcomeError) as ctx:
            ZoomService.create_meeting(host_id="host_user_42", topic="Test Chunked Stream Class")
        err = ctx.exception
        self.assertIn("Network error during meeting creation", str(err))
        self.assertEqual(err.host_id, "host_user_42")

    @patch.object(ZoomService, 'get_access_token', return_value='mock_valid_token')
    @patch('requests.post')
    def test_create_meeting_rate_limit_429_raises_zoom_rate_limit_error(self, mock_post, mock_token):
        mock_response = MagicMock()
        mock_response.status_code = 429
        mock_response.headers = {"Retry-After": "60"}
        mock_response.text = '{"message": "Daily meeting creation limit reached"}'
        mock_response.json.return_value = {"message": "Daily meeting creation limit reached"}
        mock_post.return_value = mock_response

        with self.assertRaises(ZoomRateLimitError) as ctx:
            ZoomService.create_meeting(host_id="host_user_42", topic="Test Rate Limit")
        self.assertEqual(ctx.exception.retry_after, "60")

    @patch.object(ZoomService, 'get_access_token', return_value='mock_valid_token')
    @patch('requests.post')
    def test_create_meeting_client_error_400_raises_zoom_client_error(self, mock_post, mock_token):
        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.text = '{"code": 300, "message": "Invalid topic"}'
        mock_response.json.return_value = {"code": 300, "message": "Invalid topic"}
        mock_post.return_value = mock_response

        with self.assertRaises(ZoomClientError) as ctx:
            ZoomService.create_meeting(host_id="host_user_42", topic="Invalid Topic Class")
        self.assertEqual(ctx.exception.status_code, 400)

    @patch.object(ZoomService, 'get_access_token', return_value='mock_valid_token')
    @patch('requests.post')
    def test_create_meeting_permission_403_raises_zoom_client_error(self, mock_post, mock_token):
        mock_response = MagicMock()
        mock_response.status_code = 403
        mock_response.text = '{"code": 200, "message": "No permission to create meeting on host"}'
        mock_response.json.return_value = {"code": 200, "message": "No permission to create meeting on host"}
        mock_post.return_value = mock_response

        with self.assertRaises(ZoomClientError) as ctx:
            ZoomService.create_meeting(host_id="host_user_42", topic="Forbidden Class")
        self.assertEqual(ctx.exception.status_code, 403)

    # -------------------------------------------------------------------------
    # 8. Read / Update / Delete / End Operations
    # -------------------------------------------------------------------------
    @patch.object(ZoomService, 'get_access_token', return_value='mock_valid_token')
    @patch('requests.get')
    def test_get_meeting_success(self, mock_get, mock_token):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "id": 98765432101,
            "uuid": "uuid-xyz-123",
            "host_id": "host_user_42",
            "topic": "Live Performance Class",
            "status": "waiting",
            "start_time": "2026-11-15T12:00:00Z",
            "duration": 60,
            "agenda": "NATYA_OP:5555:valid_hmac",
            "join_url": "https://zoom.us/j/98765432101"
        }
        mock_get.return_value = mock_response

        data = ZoomService.get_meeting("98765432101")
        self.assertEqual(data["meeting_id"], "98765432101")
        self.assertEqual(data["host_id"], "host_user_42")
        self.assertEqual(data["agenda"], "NATYA_OP:5555:valid_hmac")

        args, kwargs = mock_get.call_args
        self.assertEqual(args[0], "https://api.zoom.us/v2/meetings/98765432101")
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer mock_valid_token")

    @patch.object(ZoomService, 'get_access_token', return_value='mock_valid_token')
    @patch('requests.get')
    def test_get_meeting_404_raises_zoom_not_found_error(self, mock_get, mock_token):
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_response.text = '{"message": "Meeting not found"}'
        mock_response.json.return_value = {"message": "Meeting not found"}
        mock_get.return_value = mock_response

        with self.assertRaises(ZoomNotFoundError):
            ZoomService.get_meeting("99999999999")

    @patch.object(ZoomService, 'get_access_token', return_value='mock_valid_token')
    @patch('requests.get')
    def test_get_meeting_500_raises_transient_error(self, mock_get, mock_token):
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = '{"message": "Internal Zoom Server Error"}'
        mock_response.json.return_value = {"message": "Internal Zoom Server Error"}
        mock_get.return_value = mock_response

        with self.assertRaises(ZoomTransientError):
            ZoomService.get_meeting("98765432101")

    @patch.object(ZoomService, 'get_access_token', return_value='mock_valid_token')
    @patch('requests.patch')
    def test_update_meeting_success_204(self, mock_patch, mock_token):
        mock_response = MagicMock()
        mock_response.status_code = 204
        mock_patch.return_value = mock_response

        res = ZoomService.update_meeting(
            meeting_id="98765432101",
            topic="Rescheduled Bharatanatyam Session",
            start_time_iso="2026-11-16T14:00:00Z",
            duration_minutes=75,
            alternative_hosts="substitute.dancer@natya.in"
        )

        self.assertEqual(res["status"], "updated")
        self.assertEqual(res["meeting_id"], "98765432101")

        args, kwargs = mock_patch.call_args
        self.assertEqual(args[0], "https://api.zoom.us/v2/meetings/98765432101")
        payload = kwargs["json"]
        self.assertEqual(payload["topic"], "Rescheduled Bharatanatyam Session")
        self.assertEqual(payload["start_time"], "2026-11-16T14:00:00Z")
        self.assertEqual(payload["duration"], 75)
        self.assertEqual(payload["settings"]["alternative_hosts"], "substitute.dancer@natya.in")

    @patch.object(ZoomService, 'get_access_token', return_value='mock_valid_token')
    @patch('requests.patch')
    def test_update_meeting_404_raises_zoom_not_found_error(self, mock_patch, mock_token):
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_response.text = '{"message": "Meeting not found"}'
        mock_response.json.return_value = {"message": "Meeting not found"}
        mock_patch.return_value = mock_response

        with self.assertRaises(ZoomNotFoundError):
            ZoomService.update_meeting(meeting_id="98765432101", topic="Title")

    @patch.object(ZoomService, 'get_access_token', return_value='mock_valid_token')
    @patch('requests.patch')
    def test_update_meeting_500_raises_transient_error(self, mock_patch, mock_token):
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = '{"message": "Internal Zoom Server Error"}'
        mock_response.json.return_value = {"message": "Internal Zoom Server Error"}
        mock_patch.return_value = mock_response

        with self.assertRaises(ZoomTransientError):
            ZoomService.update_meeting(meeting_id="98765432101", topic="Title")

    @patch.object(ZoomService, 'get_access_token', return_value='mock_valid_token')
    @patch('requests.delete')
    def test_delete_meeting_success_204(self, mock_delete, mock_token):
        mock_response = MagicMock()
        mock_response.status_code = 204
        mock_delete.return_value = mock_response

        res = ZoomService.delete_meeting("98765432101")
        self.assertEqual(res["status"], "deleted")
        self.assertEqual(res["meeting_id"], "98765432101")

        args, kwargs = mock_delete.call_args
        self.assertEqual(args[0], "https://api.zoom.us/v2/meetings/98765432101")
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer mock_valid_token")

    @patch.object(ZoomService, 'get_access_token', return_value='mock_valid_token')
    @patch('requests.delete')
    def test_delete_meeting_404_raises_zoom_not_found_error(self, mock_delete, mock_token):
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_response.text = '{"message": "Meeting does not exist"}'
        mock_response.json.return_value = {"message": "Meeting does not exist"}
        mock_delete.return_value = mock_response

        with self.assertRaises(ZoomNotFoundError):
            ZoomService.delete_meeting("98765432101")

    @patch.object(ZoomService, 'get_access_token', return_value='mock_valid_token')
    @patch('requests.delete')
    def test_delete_meeting_500_raises_transient_error(self, mock_delete, mock_token):
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = '{"message": "Internal Zoom Server Error"}'
        mock_response.json.return_value = {"message": "Internal Zoom Server Error"}
        mock_delete.return_value = mock_response

        with self.assertRaises(ZoomTransientError):
            ZoomService.delete_meeting("98765432101")

    @patch.object(ZoomService, 'get_access_token', return_value='mock_valid_token')
    @patch('requests.put')
    def test_end_meeting_put_status_success_204(self, mock_put, mock_token):
        mock_response = MagicMock()
        mock_response.status_code = 204
        mock_put.return_value = mock_response

        res = ZoomService.end_meeting("98765432101")
        self.assertEqual(res["status"], "ended")
        self.assertEqual(res["meeting_id"], "98765432101")

        args, kwargs = mock_put.call_args
        self.assertEqual(args[0], "https://api.zoom.us/v2/meetings/98765432101/status")
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer mock_valid_token")
        self.assertEqual(kwargs["json"], {"action": "end"})

    @patch.object(ZoomService, 'get_access_token', return_value='mock_valid_token')
    @patch('requests.put')
    def test_end_meeting_404_raises_zoom_not_found_error(self, mock_put, mock_token):
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_response.text = '{"message": "Meeting does not exist"}'
        mock_response.json.return_value = {"message": "Meeting does not exist"}
        mock_put.return_value = mock_response

        with self.assertRaises(ZoomNotFoundError):
            ZoomService.end_meeting("98765432101")

    @patch.object(ZoomService, 'get_access_token', return_value='mock_valid_token')
    @patch('requests.put')
    def test_end_meeting_500_raises_transient_error(self, mock_put, mock_token):
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = '{"message": "Internal Zoom Server Error"}'
        mock_response.json.return_value = {"message": "Internal Zoom Server Error"}
        mock_put.return_value = mock_response

        with self.assertRaises(ZoomTransientError):
            ZoomService.end_meeting("98765432101")

    # -------------------------------------------------------------------------
    # 9. 401 Retry & Refresh Mechanism
    # -------------------------------------------------------------------------
    @patch.object(ZoomService, 'get_access_token')
    @patch('requests.get')
    def test_request_with_retry_on_401_refreshes_token_and_retries_once(self, mock_get, mock_get_token):
        mock_get_token.side_effect = ['old_expired_token', 'new_fresh_token']

        resp_401 = MagicMock()
        resp_401.status_code = 401
        resp_401.text = '{"code": 124, "message": "Access token is expired"}'
        resp_401.json.return_value = {"code": 124, "message": "Access token is expired"}

        resp_200 = MagicMock()
        resp_200.status_code = 200
        resp_200.json.return_value = {"id": 98765432101, "topic": "Live Performance Class"}

        mock_get.side_effect = [resp_401, resp_200]

        res = ZoomService.get_meeting("98765432101")
        self.assertEqual(res["meeting_id"], "98765432101")
        self.assertEqual(mock_get.call_count, 2)
        mock_get_token.assert_any_call(force_refresh=True, rejected_token='old_expired_token')

    # -------------------------------------------------------------------------
    # 10. Secret & Response Sanitization
    # -------------------------------------------------------------------------
    def test_sanitize_meeting_response_strips_privileged_credentials(self):
        raw_meeting = {
            "meeting_id": "98765432101",
            "join_url": "https://zoom.us/j/98765432101",
            "host_url": "https://zoom.us/s/98765432101?zak=sensitive_token",
            "start_url": "https://zoom.us/s/98765432101?zak=sensitive_token",
            "password": "super_secret_password",
            "nested_list": [
                {
                    "start_url": "https://zoom.us/s/nested",
                    "password": "nested_password",
                    "public_field": "safe_value"
                }
            ],
            "raw_response": {
                "id": 98765432101,
                "join_url": "https://zoom.us/j/98765432101",
                "start_url": "https://zoom.us/s/98765432101?zak=sensitive_token",
                "password": "super_secret_password"
            }
        }

        sanitized = ZoomService.sanitize_meeting_response(raw_meeting)

        self.assertIn("meeting_id", sanitized)
        self.assertIn("join_url", sanitized)
        self.assertNotIn("host_url", sanitized)
        self.assertNotIn("start_url", sanitized)
        self.assertNotIn("password", sanitized)

        raw = sanitized["raw_response"]
        self.assertNotIn("start_url", raw)
        self.assertNotIn("password", raw)
        self.assertIn("join_url", raw)

        item = sanitized["nested_list"][0]
        self.assertNotIn("start_url", item)
        self.assertNotIn("password", item)
        self.assertEqual(item["public_field"], "safe_value")

    def test_sanitize_error_payload_redacts_credentials(self):
        sensitive_data = {
            "client_secret": "zoom_super_secret_xyz",
            "access_token": "bearer_token_12345",
            "refresh_token": "refresh_token_67890",
            "password": "meeting_secret_pass",
            "zak": "zak_token_abc",
            "start_url": "https://zoom.us/s/secret",
            "host_url": "https://zoom.us/s/secret2",
            "safe_detail": "rate limit reached for account"
        }
        sanitized = _sanitize_error_payload(sensitive_data)
        self.assertEqual(sanitized["client_secret"], "[REDACTED]")
        self.assertEqual(sanitized["access_token"], "[REDACTED]")
        self.assertEqual(sanitized["refresh_token"], "[REDACTED]")
        self.assertEqual(sanitized["password"], "[REDACTED]")
        self.assertEqual(sanitized["zak"], "[REDACTED]")
        self.assertEqual(sanitized["start_url"], "[REDACTED]")
        self.assertEqual(sanitized["host_url"], "[REDACTED]")
        self.assertEqual(sanitized["safe_detail"], "rate limit reached for account")


if __name__ == '__main__':
    unittest.main()
