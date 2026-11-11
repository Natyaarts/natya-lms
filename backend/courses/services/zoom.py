import os
import logging
import requests
import base64
import time
import uuid
import re
import urllib.parse
import pickle
from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import ImproperlyConfigured

logger = logging.getLogger(__name__)


class ZoomAPIError(Exception):
    """Base exception for all Zoom API errors."""
    def __init__(self, message, status_code=None, response_data=None):
        super().__init__(message)
        self.status_code = status_code
        self.response_data = response_data


class ZoomClientError(ZoomAPIError):
    """Client request error (HTTP 4xx)."""
    pass


class ZoomAuthError(ZoomClientError):
    """Authentication or authorization failed (HTTP 401 or 403)."""
    pass


class ZoomNotFoundError(ZoomClientError):
    """Target resource (meeting or user) not found on Zoom (HTTP 404)."""
    pass


class ZoomRateLimitError(ZoomClientError):
    """Rate limit exceeded (HTTP 429)."""
    def __init__(self, message, status_code=429, response_data=None, retry_after=None):
        super().__init__(message, status_code=status_code, response_data=response_data)
        self.retry_after = retry_after


class ZoomTransientError(ZoomAPIError):
    """Transient gateway, network, or server error (HTTP 500, 502, 503, 504)."""
    pass


class ZoomAmbiguousOutcomeError(ZoomAPIError):
    """
    Network timeout, connection drop, chunked transfer failure, or gateway 5xx occurred
    during a non-idempotent operation (such as meeting creation).
    Outcome on Zoom cloud is unknown; caller must NOT blindly repeat without reconciliation.
    """
    def __init__(self, message, status_code=None, response_data=None, host_id=None, topic=None):
        super().__init__(message, status_code=status_code, response_data=response_data)
        self.host_id = host_id
        self.topic = topic


class ZoomService:
    """
    Handles Zoom Server-to-Server OAuth authentication, Redis token caching with
    atomic owner-verified locking, explicit host routing, and lifecycle operations (create, get, update, delete, end).
    """

    CACHE_KEY_ACCESS_TOKEN = "zoom:s2s:access_token"
    CACHE_KEY_REFRESH_LOCK = "zoom:s2s:token_refresh_lock"
    CACHE_KEY_LOCK_TOKEN_REFRESH = CACHE_KEY_REFRESH_LOCK
    DEFAULT_TIMEOUT_SECONDS = 12
    LOCK_TIMEOUT_SECONDS = 25  # Safely exceeds DEFAULT_TIMEOUT_SECONDS
    LOCK_WAIT_TIMEOUT = 10     # Max seconds a waiter thread polls cache

    EMAIL_REGEX = re.compile(r'^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$')
    USER_ID_REGEX = re.compile(r'^[a-zA-Z0-9_-]{10,64}$')
    NUMERIC_MEETING_REGEX = re.compile(r'^\d{8,15}$')
    UUID_MEETING_REGEX = re.compile(r'^[a-zA-Z0-9+/=_-]{16,128}$')

    LUA_COMPARE_AND_DELETE = """
    if redis.call('get', KEYS[1]) == ARGV[1] then
        return redis.call('del', KEYS[1])
    else
        return 0
    end
    """

    @classmethod
    def get_credentials(cls):
        """
        Retrieves Zoom Server-to-Server OAuth credentials from settings or environment.
        Raises ImproperlyConfigured if any credential is missing or blank.
        """
        account_id = (getattr(settings, 'ZOOM_ACCOUNT_ID', '') or os.environ.get('ZOOM_ACCOUNT_ID', '')).strip()
        client_id = (getattr(settings, 'ZOOM_CLIENT_ID', '') or os.environ.get('ZOOM_CLIENT_ID', '')).strip()
        client_secret = (getattr(settings, 'ZOOM_CLIENT_SECRET', '') or os.environ.get('ZOOM_CLIENT_SECRET', '')).strip()
        if not (account_id and client_id and client_secret):
            raise ImproperlyConfigured(
                "Zoom credentials (ZOOM_ACCOUNT_ID, ZOOM_CLIENT_ID, ZOOM_CLIENT_SECRET) must be explicitly configured in the environment."
            )
        return account_id, client_id, client_secret

    @classmethod
    def _sanitize_string(cls, text):
        """
        Redacts sensitive tokens, passwords, and client secrets from strings.
        """
        if not text:
            return ""
        return re.sub(
            r'("?(?:client_secret|access_token|refresh_token|password|start_url|host_url|zak)"?\s*[:=]\s*"?)[^",\s}]+("?)',
            r'\1[REDACTED]\2',
            str(text)
        )

    @classmethod
    def validate_host_id(cls, host_id):
        """
        Validates host identifier. Rejects 'me', control characters, path separators, and traversal.
        Accepts valid email or alphanumeric/hyphenated Zoom user IDs. Returns URL-encoded identifier.
        """
        if not host_id or not str(host_id).strip():
            raise ValueError("host_id is required and cannot be empty. Silent fallback to '/users/me/' is strictly prohibited.")

        raw_host = str(host_id)
        clean_host_id = raw_host.strip()
        if clean_host_id.lower() == "me":
            raise ValueError("Explicit host identifier required. Routing to '/users/me/' is strictly prohibited.")

        if any(c in raw_host for c in ['\0', '\n', '\r', '\t']):
            raise ValueError(f"Invalid host_id format: '{raw_host}'. Contains control characters.")

        if any(c in clean_host_id for c in ['/', '\\', '..', ' ']):
            raise ValueError(f"Invalid host_id format: '{clean_host_id}'. Contains path separators, traversal, or control characters.")

        if not (cls.EMAIL_REGEX.match(clean_host_id) or cls.USER_ID_REGEX.match(clean_host_id)):
            raise ValueError(f"Invalid host_id format: '{clean_host_id}'. Must be a valid Zoom email address or alphanumeric user ID.")

        return urllib.parse.quote(clean_host_id, safe='@._+-')

    @classmethod
    def validate_meeting_id(cls, meeting_id):
        """
        Validates Zoom numeric meeting ID or UUID.
        Rejects control characters, path traversal and malicious path injection.
        Applies single or double URL encoding per Zoom developer documentation.
        """
        if not meeting_id or not str(meeting_id).strip():
            raise ValueError("meeting_id is required.")

        raw_str = str(meeting_id)
        if any(c in raw_str for c in ['\0', '\n', '\r', '\t', ' ']) or '..' in raw_str or '\\' in raw_str:
            raise ValueError(f"Invalid meeting_id format: '{raw_str}'. Contains path traversal or control characters.")

        clean_meeting_id = raw_str.strip()
        if clean_meeting_id.isdigit():
            if not cls.NUMERIC_MEETING_REGEX.match(clean_meeting_id):
                raise ValueError(f"Invalid meeting_id format: '{clean_meeting_id}'. Numeric meeting ID must be 8-15 digits.")
            return clean_meeting_id

        if not cls.UUID_MEETING_REGEX.match(clean_meeting_id):
            raise ValueError(f"Invalid meeting_id format: '{clean_meeting_id}'. Must be numeric (8-15 digits) or valid Zoom UUID.")

        # Zoom API requirement: If meeting UUID begins with '/' or contains '//', it must be double URL-encoded.
        if clean_meeting_id.startswith('/') or '//' in clean_meeting_id:
            return urllib.parse.quote(urllib.parse.quote(clean_meeting_id, safe=''), safe='')

        return urllib.parse.quote(clean_meeting_id, safe='')

    @classmethod
    def _calculate_cache_ttl(cls, expires_in):
        """
        Calculates safe Redis cache TTL for an access token.
        Guarantees the token is never cached beyond its actual lifetime.
        """
        try:
            expires_in_int = int(expires_in)
        except (ValueError, TypeError):
            return 0

        if expires_in_int <= 10:
            return 0

        if expires_in_int > 600:
            # For standard tokens (e.g. 3600s), refresh 5 minutes (300s) early
            return expires_in_int - 300
        elif expires_in_int > 60:
            # For short tokens (61-600s, e.g. 300s), buffer 20% -> cache 80%
            return int(expires_in_int * 0.8)
        else:
            # For very short tokens (11-60s, e.g. 60s), buffer 50%
            return int(expires_in_int * 0.5)

    @classmethod
    def _is_redis_configured(cls):
        """
        Returns True if the default cache backend configured in settings is Redis.
        """
        default_cache_cfg = getattr(settings, 'CACHES', {}).get('default', {})
        backend_str = default_cache_cfg.get('BACKEND', '')
        return 'redis' in backend_str.lower()

    @classmethod
    def _get_raw_redis_client(cls, prefixed_key=None, write=True):
        """
        Resolves the underlying redis.Redis client from Django's cache backend.
        Supports Django's built-in RedisCache (Django 4.0+) and django-redis.
        """
        client = getattr(cache, 'client', None)
        if client and hasattr(client, 'get_client'):
            try:
                return client.get_client(write=write)
            except Exception:
                pass

        cache_backend = getattr(cache, '_cache', None)
        if cache_backend and hasattr(cache_backend, 'get_client'):
            try:
                return cache_backend.get_client(prefixed_key, write=write)
            except Exception:
                pass

        return None

    @classmethod
    def _acquire_lock(cls, lock_key, owner_token):
        """
        Acquires distributed lock atomically.
        In production with Redis, executes SET NX EX with raw bytes against redis.Redis.
        If Redis is configured but fails/cannot connect, raises ZoomTransientError.
        In testing/development with LocMemCache, uses cache.add.
        """
        prefixed_key = cache.make_key(lock_key)
        raw_client = cls._get_raw_redis_client(prefixed_key, write=True)

        if raw_client is not None:
            raw_token = owner_token.encode('utf-8') if isinstance(owner_token, str) else owner_token
            return bool(raw_client.set(prefixed_key, raw_token, ex=cls.LOCK_TIMEOUT_SECONDS, nx=True))

        if cls._is_redis_configured():
            raise ZoomTransientError(
                "Distributed locking unavailable: safe token refresh requires a working Redis cache backend in production."
            )

        # Fallback for isolated test/dev environments using LocMemCache
        return bool(cache.add(lock_key, owner_token, timeout=cls.LOCK_TIMEOUT_SECONDS))

    @classmethod
    def _release_lock(cls, lock_key, owner_token):
        """
        Safely releases distributed lock only if current owner matches owner_token.
        Uses atomic Lua compare-and-delete on the underlying Redis client with raw bytes.
        """
        if not owner_token:
            return

        prefixed_key = cache.make_key(lock_key)
        raw_client = cls._get_raw_redis_client(prefixed_key, write=True)

        if raw_client is not None:
            try:
                raw_token = owner_token.encode('utf-8') if isinstance(owner_token, str) else owner_token
                raw_client.eval(cls.LUA_COMPARE_AND_DELETE, 1, prefixed_key, raw_token)
            except Exception as e:
                logger.warning(f"[ZoomService] Error evaluating Lua lock release for {lock_key}: {e}")
            return

        if cls._is_redis_configured():
            logger.error("[ZoomService] Cannot release lock: Redis client unavailable in production.")
            return

        # Test/dev fallback for LocMemCache
        try:
            if cache.get(lock_key) == owner_token:
                cache.delete(lock_key)
        except Exception:
            pass

    @classmethod
    def _invalidate_cached_token(cls, rejected_token):
        """
        Atomically removes rejected_token from cache only if the cached value
        matches rejected_token. Does not delete a newer token written by another worker.
        """
        if not rejected_token:
            return

        prefixed_key = cache.make_key(cls.CACHE_KEY_ACCESS_TOKEN)
        raw_client = cls._get_raw_redis_client(prefixed_key, write=True)

        if raw_client is not None:
            try:
                pickled_token = pickle.dumps(rejected_token, protocol=pickle.HIGHEST_PROTOCOL)
                raw_client.eval(cls.LUA_COMPARE_AND_DELETE, 1, prefixed_key, pickled_token)
            except Exception as e:
                logger.warning(f"[ZoomService] Error in Lua token invalidation: {e}")
            return

        try:
            if cache.get(cls.CACHE_KEY_ACCESS_TOKEN) == rejected_token:
                cache.delete(cls.CACHE_KEY_ACCESS_TOKEN)
        except Exception:
            pass

    @classmethod
    def get_access_token(cls, force_refresh=False, rejected_token=None):
        """
        Retrieves an active Zoom Server-to-Server OAuth access token.
        Utilizes Redis-backed caching with atomic owner-verified distributed locking,
        bounded waiter loop, and safe token invalidation.
        """
        # 1. Invalidate rejected token upfront if provided; otherwise clear cache on force refresh
        if force_refresh:
            if rejected_token:
                cls._invalidate_cached_token(rejected_token)
            else:
                try:
                    cache.delete(cls.CACHE_KEY_ACCESS_TOKEN)
                except Exception:
                    pass

        # 2. Check cache if not forcing refresh
        if not force_refresh:
            try:
                cached_token = cache.get(cls.CACHE_KEY_ACCESS_TOKEN)
                if cached_token:
                    return cached_token
            except Exception as cache_err:
                logger.warning(f"[ZoomService] Cache read error for access token: {cache_err}")

        # 3. If force_refresh was called with a rejected_token, check if another worker already refreshed
        if force_refresh and rejected_token:
            try:
                cached_token = cache.get(cls.CACHE_KEY_ACCESS_TOKEN)
                if cached_token and cached_token != rejected_token:
                    logger.info("[ZoomService] Reusing newly refreshed token from concurrent worker.")
                    return cached_token
            except Exception:
                pass

        # 4. Acquire distributed lock
        owner_token = str(uuid.uuid4())
        try:
            lock_acquired = cls._acquire_lock(cls.CACHE_KEY_REFRESH_LOCK, owner_token)
        except Exception as lock_err:
            clean_err = cls._sanitize_string(str(lock_err))
            logger.error(f"[ZoomService] Redis distributed locking failure: {clean_err}")
            raise ZoomTransientError(
                f"Redis distributed locking is unavailable ({clean_err}); cannot coordinate safe token refresh across workers."
            ) from lock_err

        # 5. If lock not acquired, wait for the holder to refresh the token
        if not lock_acquired:
            poll_start = time.time()
            while (time.time() - poll_start) < cls.LOCK_WAIT_TIMEOUT:
                time.sleep(0.5)
                try:
                    cached_token = cache.get(cls.CACHE_KEY_ACCESS_TOKEN)
                    if cached_token:
                        if rejected_token and cached_token == rejected_token:
                            continue
                        return cached_token
                except Exception:
                    pass

            raise ZoomTransientError(
                "Timed out waiting for concurrent token refresh to complete."
            )

        # 6. Holder of the lock performs token exchange
        try:
            account_id, client_id, client_secret = cls.get_credentials()
            auth_str = f"{client_id}:{client_secret}"
            b64_auth = base64.b64encode(auth_str.encode()).decode()

            url = f"https://zoom.us/oauth/token?grant_type=account_credentials&account_id={account_id}"
            headers = {
                "Authorization": f"Basic {b64_auth}",
                "Content-Type": "application/x-www-form-urlencoded"
            }

            try:
                res = requests.post(url, headers=headers, timeout=cls.DEFAULT_TIMEOUT_SECONDS)
            except (requests.Timeout, requests.ConnectionError) as conn_err:
                clean_err = cls._sanitize_string(str(conn_err))
                logger.error(f"[ZoomService] Network error during OAuth token exchange: {clean_err}")
                raise ZoomTransientError(f"OAuth connection error: {clean_err}") from conn_err

            if res.status_code != 200:
                cls._handle_error_response(res, context="get_access_token (OAuth token exchange)")

            data = res.json()
            token = data.get('access_token')
            if not token:
                raise ZoomAuthError("Zoom OAuth response did not contain an access_token.")

            expires_in = data.get('expires_in', 3600)
            cache_ttl = cls._calculate_cache_ttl(expires_in)

            if cache_ttl > 0:
                try:
                    cache.set(cls.CACHE_KEY_ACCESS_TOKEN, token, timeout=cache_ttl)
                except Exception as cache_err:
                    logger.warning(f"[ZoomService] Cache write error for access token: {cache_err}")

            return token
        finally:
            cls._release_lock(cls.CACHE_KEY_REFRESH_LOCK, owner_token)

    @classmethod
    def create_meeting(cls, host_id=None, topic=None, start_time_iso=None, duration_minutes=60,
                       alternative_hosts=None, agenda=None, settings_override=None):
        """
        Creates a new Zoom meeting on behalf of an explicit host account.
        Silent fallback to '/users/me/' is strictly prohibited.
        Treats 5xx, timeouts, connection drops, and chunked encoding errors as ZoomAmbiguousOutcomeError.
        """
        encoded_host_id = cls.validate_host_id(host_id)

        token = cls.get_access_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }
        payload = {
            "topic": topic or "Natya Arts Academy Live Class",
            "type": 2 if start_time_iso else 1,
            "duration": int(duration_minutes) if duration_minutes else 60,
            "timezone": "Asia/Kolkata",
            "settings": {
                "host_video": True,
                "participant_video": True,
                "join_before_host": False,
                "jbh_time": 0,
                "mute_upon_entry": True,
                "waiting_room": False,
                "auto_recording": "cloud"
            }
        }
        if start_time_iso:
            payload["start_time"] = start_time_iso
        if alternative_hosts:
            payload["settings"]["alternative_hosts"] = str(alternative_hosts).strip()
        if agenda:
            payload["agenda"] = str(agenda)[:2000]
        if settings_override and isinstance(settings_override, dict):
            payload["settings"].update(settings_override)

        url = f"https://api.zoom.us/v2/users/{encoded_host_id}/meetings"

        try:
            res = requests.post(url, headers=headers, json=payload, timeout=cls.DEFAULT_TIMEOUT_SECONDS)
        except (requests.Timeout, requests.ConnectionError, requests.exceptions.ChunkedEncodingError) as conn_err:
            clean_err = cls._sanitize_string(str(conn_err))
            logger.error(f"[ZoomService] Network error during meeting creation on host {encoded_host_id}: {clean_err}")
            raise ZoomAmbiguousOutcomeError(
                f"Network error during meeting creation on host {encoded_host_id}: {clean_err}. "
                "Outcome on Zoom cloud is unknown; caller must NOT blindly repeat creation without reconciliation.",
                response_data={"error": clean_err, "host_id": encoded_host_id, "topic": payload.get("topic")},
                host_id=encoded_host_id,
                topic=payload.get("topic")
            ) from conn_err

        # Treat all 5xx responses (500, 502, 503, 504) as ZoomAmbiguousOutcomeError
        if 500 <= res.status_code < 600:
            sanitized_body = cls._sanitize_string(res.text)
            logger.error(f"[ZoomService] Ambiguous 5xx response from Zoom during create_meeting [HTTP {res.status_code}]: {sanitized_body}")
            raise ZoomAmbiguousOutcomeError(
                f"Zoom returned HTTP {res.status_code} during meeting creation on host {encoded_host_id}. "
                "The meeting may have been created on Zoom cloud before the gateway error; caller must NOT blindly repeat creation without reconciliation.",
                status_code=res.status_code,
                response_data={
                    "raw_text": sanitized_body,
                    "sanitized_text": sanitized_body,
                    "status_code": res.status_code,
                    "host_id": encoded_host_id,
                    "topic": payload.get("topic")
                },
                host_id=encoded_host_id,
                topic=payload.get("topic")
            )

        if res.status_code == 401:
            logger.info(f"[ZoomService] Received 401 on create_meeting for host {encoded_host_id}; refreshing token and retrying once")
            cls._invalidate_cached_token(token)
            token = cls.get_access_token(force_refresh=True, rejected_token=token)
            headers["Authorization"] = f"Bearer {token}"
            try:
                res = requests.post(url, headers=headers, json=payload, timeout=cls.DEFAULT_TIMEOUT_SECONDS)
            except (requests.Timeout, requests.ConnectionError, requests.exceptions.ChunkedEncodingError) as conn_err:
                clean_err = cls._sanitize_string(str(conn_err))
                logger.error(f"[ZoomService] Network error during retry of meeting creation on host {encoded_host_id}: {clean_err}")
                raise ZoomAmbiguousOutcomeError(
                    f"Network error during meeting creation on host {encoded_host_id}: {clean_err}. "
                    "Outcome on Zoom cloud is unknown; caller must NOT blindly repeat creation without reconciliation.",
                    response_data={"error": clean_err, "host_id": encoded_host_id, "topic": payload.get("topic")},
                    host_id=encoded_host_id,
                    topic=payload.get("topic")
                ) from conn_err

        if res.status_code != 201:
            logger.error(f"[ZoomService] Definite failure during create_meeting for host {encoded_host_id}: {res.status_code}")
            cls._handle_error_response(res, context=f"create_meeting for host {encoded_host_id}")

        data = res.json()
        return {
            "meeting_id": str(data.get("id")),
            "uuid": data.get("uuid", ""),
            "host_id": data.get("host_id", encoded_host_id),
            "join_url": data.get("join_url", ""),
            "host_url": data.get("start_url", ""),
            "password": data.get("password", ""),
            "raw_response": data
        }

    @classmethod
    def get_meeting(cls, meeting_id):
        """
        Retrieves Zoom meeting details by validated numeric meeting ID or UUID.
        """
        encoded_meeting_id = cls.validate_meeting_id(meeting_id)
        token = cls.get_access_token()
        headers = {"Authorization": f"Bearer {token}"}
        url = f"https://api.zoom.us/v2/meetings/{encoded_meeting_id}"

        try:
            res = requests.get(url, headers=headers, timeout=cls.DEFAULT_TIMEOUT_SECONDS)
        except (requests.Timeout, requests.ConnectionError) as conn_err:
            clean_err = cls._sanitize_string(str(conn_err))
            logger.error(f"[ZoomService] Network error fetching meeting {encoded_meeting_id}: {clean_err}")
            raise ZoomTransientError(f"Network error fetching meeting {encoded_meeting_id}: {clean_err}") from conn_err

        if res.status_code == 401:
            logger.info(f"[ZoomService] Received 401 on get_meeting {encoded_meeting_id}; refreshing token and retrying once")
            cls._invalidate_cached_token(token)
            token = cls.get_access_token(force_refresh=True, rejected_token=token)
            headers["Authorization"] = f"Bearer {token}"
            try:
                res = requests.get(url, headers=headers, timeout=cls.DEFAULT_TIMEOUT_SECONDS)
            except (requests.Timeout, requests.ConnectionError) as conn_err:
                clean_err = cls._sanitize_string(str(conn_err))
                logger.error(f"[ZoomService] Network error fetching meeting {encoded_meeting_id}: {clean_err}")
                raise ZoomTransientError(f"Network error fetching meeting {encoded_meeting_id}: {clean_err}") from conn_err

        if res.status_code != 200:
            cls._handle_error_response(res, context=f"get_meeting {encoded_meeting_id}")

        data = res.json()
        return {
            "meeting_id": str(data.get("id")),
            "uuid": data.get("uuid", ""),
            "host_id": data.get("host_id", ""),
            "topic": data.get("topic", ""),
            "status": data.get("status", ""),
            "start_time": data.get("start_time", ""),
            "duration": data.get("duration", 0),
            "timezone": data.get("timezone", ""),
            "agenda": data.get("agenda", ""),
            "join_url": data.get("join_url", ""),
            "host_url": data.get("start_url", ""),
            "settings": data.get("settings", {}),
            "raw_response": data
        }

    @classmethod
    def update_meeting(cls, meeting_id, topic=None, start_time_iso=None, duration_minutes=None,
                       alternative_hosts=None, agenda=None, settings_override=None):
        """
        Updates meeting details on Zoom cloud via PATCH. Supports sparse updates.
        """
        encoded_meeting_id = cls.validate_meeting_id(meeting_id)
        token = cls.get_access_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }

        payload = {}
        if topic is not None:
            payload["topic"] = topic
        if start_time_iso is not None:
            payload["start_time"] = start_time_iso
        if duration_minutes is not None:
            payload["duration"] = int(duration_minutes)
        if agenda is not None:
            payload["agenda"] = str(agenda)[:2000]
        if alternative_hosts is not None:
            payload.setdefault("settings", {})["alternative_hosts"] = str(alternative_hosts).strip()
        if settings_override and isinstance(settings_override, dict):
            payload.setdefault("settings", {}).update(settings_override)

        url = f"https://api.zoom.us/v2/meetings/{encoded_meeting_id}"

        try:
            res = requests.patch(url, headers=headers, json=payload, timeout=cls.DEFAULT_TIMEOUT_SECONDS)
        except (requests.Timeout, requests.ConnectionError) as conn_err:
            clean_err = cls._sanitize_string(str(conn_err))
            logger.error(f"[ZoomService] Network error updating meeting {encoded_meeting_id}: {clean_err}")
            raise ZoomTransientError(f"Network error updating meeting {encoded_meeting_id}: {clean_err}") from conn_err

        if res.status_code == 401:
            logger.info(f"[ZoomService] Received 401 on update_meeting {encoded_meeting_id}; refreshing token and retrying once")
            cls._invalidate_cached_token(token)
            token = cls.get_access_token(force_refresh=True, rejected_token=token)
            headers["Authorization"] = f"Bearer {token}"
            try:
                res = requests.patch(url, headers=headers, json=payload, timeout=cls.DEFAULT_TIMEOUT_SECONDS)
            except (requests.Timeout, requests.ConnectionError) as conn_err:
                clean_err = cls._sanitize_string(str(conn_err))
                logger.error(f"[ZoomService] Network error updating meeting {encoded_meeting_id}: {clean_err}")
                raise ZoomTransientError(f"Network error updating meeting {encoded_meeting_id}: {clean_err}") from conn_err

        if res.status_code != 204:
            cls._handle_error_response(res, context=f"update_meeting {encoded_meeting_id}")

        return {"status": "updated", "meeting_id": encoded_meeting_id}

    @classmethod
    def delete_meeting(cls, meeting_id, schedule_for_reminder=False):
        """
        Deletes a meeting from Zoom cloud via DELETE.
        Returns confirmation dict on 204. Raises ZoomNotFoundError on 404.
        """
        encoded_meeting_id = cls.validate_meeting_id(meeting_id)
        token = cls.get_access_token()
        headers = {"Authorization": f"Bearer {token}"}
        url = f"https://api.zoom.us/v2/meetings/{encoded_meeting_id}"
        params = {}
        if schedule_for_reminder:
            params["schedule_for_reminder"] = "true"

        try:
            res = requests.delete(url, headers=headers, params=params, timeout=cls.DEFAULT_TIMEOUT_SECONDS)
        except (requests.Timeout, requests.ConnectionError) as conn_err:
            clean_err = cls._sanitize_string(str(conn_err))
            logger.error(f"[ZoomService] Network error deleting meeting {encoded_meeting_id}: {clean_err}")
            raise ZoomTransientError(f"Network error deleting meeting {encoded_meeting_id}: {clean_err}") from conn_err

        if res.status_code == 401:
            logger.info(f"[ZoomService] Received 401 on delete_meeting {encoded_meeting_id}; refreshing token and retrying once")
            cls._invalidate_cached_token(token)
            token = cls.get_access_token(force_refresh=True, rejected_token=token)
            headers["Authorization"] = f"Bearer {token}"
            try:
                res = requests.delete(url, headers=headers, params=params, timeout=cls.DEFAULT_TIMEOUT_SECONDS)
            except (requests.Timeout, requests.ConnectionError) as conn_err:
                clean_err = cls._sanitize_string(str(conn_err))
                logger.error(f"[ZoomService] Network error deleting meeting {encoded_meeting_id}: {clean_err}")
                raise ZoomTransientError(f"Network error deleting meeting {encoded_meeting_id}: {clean_err}") from conn_err

        if res.status_code != 204:
            cls._handle_error_response(res, context=f"delete_meeting {encoded_meeting_id}")

        return {"status": "deleted", "meeting_id": encoded_meeting_id}

    @classmethod
    def end_meeting(cls, meeting_id):
        """
        Terminates an active Zoom live session via PUT /v2/meetings/{id}/status.
        """
        encoded_meeting_id = cls.validate_meeting_id(meeting_id)
        token = cls.get_access_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }
        url = f"https://api.zoom.us/v2/meetings/{encoded_meeting_id}/status"
        payload = {"action": "end"}

        try:
            res = requests.put(url, headers=headers, json=payload, timeout=cls.DEFAULT_TIMEOUT_SECONDS)
        except (requests.Timeout, requests.ConnectionError) as conn_err:
            clean_err = cls._sanitize_string(str(conn_err))
            logger.error(f"[ZoomService] Network error ending meeting {encoded_meeting_id}: {clean_err}")
            raise ZoomTransientError(f"Network error ending meeting {encoded_meeting_id}: {clean_err}") from conn_err

        if res.status_code == 401:
            logger.info(f"[ZoomService] Received 401 on end_meeting {encoded_meeting_id}; refreshing token and retrying once")
            cls._invalidate_cached_token(token)
            token = cls.get_access_token(force_refresh=True, rejected_token=token)
            headers["Authorization"] = f"Bearer {token}"
            try:
                res = requests.put(url, headers=headers, json=payload, timeout=cls.DEFAULT_TIMEOUT_SECONDS)
            except (requests.Timeout, requests.ConnectionError) as conn_err:
                clean_err = cls._sanitize_string(str(conn_err))
                logger.error(f"[ZoomService] Network error ending meeting {encoded_meeting_id}: {clean_err}")
                raise ZoomTransientError(f"Network error ending meeting {encoded_meeting_id}: {clean_err}") from conn_err

        if res.status_code != 204:
            cls._handle_error_response(res, context=f"end_meeting {encoded_meeting_id}")

        return {"status": "ended", "meeting_id": encoded_meeting_id}

    @classmethod
    def list_user_meetings(cls, host_id, meeting_type="scheduled", page_size=300):
        """
        Lists meetings for a host user across all pages using pagination tokens.
        Validates host_id to prevent path traversal or /users/me/ routing.
        Returns a list of meeting summary dictionaries.
        """
        encoded_host_id = cls.validate_host_id(host_id)
        token = cls.get_access_token()
        headers = {"Authorization": f"Bearer {token}"}

        all_meetings = []
        next_page_token = None

        while True:
            params = {
                "type": meeting_type,
                "page_size": min(page_size, 300)
            }
            if next_page_token:
                params["next_page_token"] = next_page_token

            url = f"https://api.zoom.us/v2/users/{encoded_host_id}/meetings"
            try:
                res = requests.get(url, headers=headers, params=params, timeout=cls.DEFAULT_TIMEOUT_SECONDS)
            except (requests.Timeout, requests.ConnectionError) as conn_err:
                clean_err = cls._sanitize_string(str(conn_err))
                logger.error(f"[ZoomService] Network error listing meetings for {encoded_host_id}: {clean_err}")
                raise ZoomTransientError(f"Network error listing meetings for {encoded_host_id}: {clean_err}") from conn_err

            if res.status_code == 401:
                logger.info(f"[ZoomService] Received 401 listing meetings for {encoded_host_id}; refreshing token and retrying")
                cls._invalidate_cached_token(token)
                token = cls.get_access_token(force_refresh=True, rejected_token=token)
                headers["Authorization"] = f"Bearer {token}"
                try:
                    res = requests.get(url, headers=headers, params=params, timeout=cls.DEFAULT_TIMEOUT_SECONDS)
                except (requests.Timeout, requests.ConnectionError) as conn_err:
                    clean_err = cls._sanitize_string(str(conn_err))
                    logger.error(f"[ZoomService] Network error listing meetings for {encoded_host_id}: {clean_err}")
                    raise ZoomTransientError(f"Network error listing meetings for {encoded_host_id}: {clean_err}") from conn_err

            if res.status_code != 200:
                cls._handle_error_response(res, context=f"list_user_meetings {encoded_host_id}")

            data = res.json()
            meetings = data.get("meetings", [])
            for m in meetings:
                all_meetings.append({
                    "meeting_id": str(m.get("id")),
                    "uuid": m.get("uuid", ""),
                    "host_id": m.get("host_id", encoded_host_id),
                    "topic": m.get("topic", ""),
                    "type": m.get("type", 2),
                    "start_time": m.get("start_time", ""),
                    "duration": m.get("duration", 0),
                    "timezone": m.get("timezone", ""),
                    "created_at": m.get("created_at", "")
                })

            next_page_token = data.get("next_page_token")
            if not next_page_token:
                break

        return all_meetings

    @classmethod
    def sanitize_meeting_response(cls, meeting_data):
        """
        Recursively strips host_url, start_url, password, and zak tokens from meeting
        dictionaries and lists for safe student-facing and public API serialization.
        """
        if isinstance(meeting_data, dict):
            sanitized = {}
            for k, v in meeting_data.items():
                if k in ("host_url", "start_url", "password", "zak"):
                    continue
                sanitized[k] = cls.sanitize_meeting_response(v)
            return sanitized
        elif isinstance(meeting_data, list):
            return [cls.sanitize_meeting_response(item) for item in meeting_data]
        return meeting_data

    @classmethod
    def _handle_error_response(cls, response, context=""):
        """
        Maps HTTP error status codes and headers to specific Zoom exceptions.
        Sanitizes sensitive tokens/passwords from response strings before raising.
        """
        status_code = response.status_code
        text = response.text
        sanitized_text = cls._sanitize_string(text)

        try:
            data = response.json()
        except Exception:
            data = {"raw_text": sanitized_text}

        message = f"Zoom error during {context} [HTTP {status_code}]: {sanitized_text}"

        if status_code in (401, 403):
            raise ZoomAuthError(message, status_code=status_code, response_data=data)
        elif status_code == 404:
            raise ZoomNotFoundError(message, status_code=status_code, response_data=data)
        elif status_code == 429:
            retry_after = response.headers.get("Retry-After")
            raise ZoomRateLimitError(message, status_code=status_code, response_data=data, retry_after=retry_after)
        elif 400 <= status_code < 500:
            raise ZoomClientError(message, status_code=status_code, response_data=data)
        elif 500 <= status_code < 600:
            raise ZoomTransientError(message, status_code=status_code, response_data=data)
        else:
            raise ZoomAPIError(message, status_code=status_code, response_data=data)


def _sanitize_error_payload(data):
    """Recursively redacts sensitive keys from error/logging payloads."""
    if isinstance(data, dict):
        sanitized = {}
        for k, v in data.items():
            if k in ("client_secret", "access_token", "refresh_token", "password", "start_url", "host_url", "zak"):
                sanitized[k] = "[REDACTED]"
            else:
                sanitized[k] = _sanitize_error_payload(v)
        return sanitized
    elif isinstance(data, list):
        return [_sanitize_error_payload(item) for item in data]
    elif isinstance(data, str):
        return ZoomService._sanitize_string(data)
    return data


# Module-level convenience aliases
validate_host_id = ZoomService.validate_host_id
validate_meeting_id = ZoomService.validate_meeting_id
_calculate_cache_ttl = ZoomService._calculate_cache_ttl
