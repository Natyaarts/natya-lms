"""
Push delivery gap fix. Small, isolated, provider-specific Expo push
client -- deliberately NOT a generic push abstraction (no FCM/APNs code
here; expo-notifications on the mobile side already handles that
distinction for us, and every DeviceToken.token this project stores is an
Expo push token, not a raw platform token -- see
mobile/src/api/pushNotifications.ts's own use of
Notifications.getExpoPushTokenAsync).

Nothing in this module touches the database or Celery -- it only knows
how to turn a list of message dicts into a list of Expo "ticket" dicts
(or raise ExpoPushTransientError for anything that looks retryable). The
Celery task in notifications/tasks.py is the caller that interprets
those tickets against DeviceToken rows.
"""
import logging
import os

import requests

logger = logging.getLogger(__name__)

EXPO_PUSH_API_URL = "https://exp.host/--/api/v2/push/send"
# Expo's own documented per-request batch limit.
EXPO_PUSH_CHUNK_SIZE = 100


class ExpoPushTransientError(Exception):
    """Network-level failure, timeout, or 5xx talking to Expo -- safe (and
    expected) to retry the whole batch. Anything else is treated as
    permanent for that chunk (see send_expo_push_messages)."""


def _build_headers():
    headers = {
        "Accept": "application/json",
        "Accept-Encoding": "gzip, deflate",
        "Content-Type": "application/json",
    }
    # Expo's push API works with no credential at all for a normal
    # (non-"Enhanced Security") project -- this is optional, read from the
    # environment only, never hardcoded, and simply omitted if unset (see
    # this phase's final report for the exact production configuration
    # this does/doesn't require).
    access_token = os.environ.get("EXPO_ACCESS_TOKEN")
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"
    return headers


def send_expo_push_messages(messages, timeout=10):
    """
    Sends a batch of Expo push messages (each a dict with at least `to`,
    `title`, `body`), chunked into Expo's documented 100-message-per-
    request limit. Returns a flat list of "ticket" dicts, ONE PER INPUT
    MESSAGE, in the same order as `messages` -- so a caller can zip()
    the result directly against whatever list (e.g. DeviceToken rows) it
    built `messages` from.

    Raises ExpoPushTransientError for a network failure/timeout/5xx --
    the caller (the Celery task) is expected to catch this and retry the
    whole call. A non-200/non-5xx response (a structurally bad request)
    is NOT retried -- every message in that chunk gets a synthetic
    {"status": "error", ...} ticket instead, since retrying an
    already-malformed request would never succeed differently.
    """
    if not messages:
        return []

    tickets = []
    headers = _build_headers()
    for start in range(0, len(messages), EXPO_PUSH_CHUNK_SIZE):
        chunk = messages[start:start + EXPO_PUSH_CHUNK_SIZE]
        try:
            response = requests.post(EXPO_PUSH_API_URL, json=chunk, headers=headers, timeout=timeout)
        except requests.RequestException as e:
            raise ExpoPushTransientError(f"Network error calling Expo push API: {e}") from e

        if response.status_code >= 500:
            raise ExpoPushTransientError(f"Expo push API returned {response.status_code}: {response.text[:200]}")

        if response.status_code != 200:
            logger.error(f"Expo push API returned {response.status_code} for a chunk of {len(chunk)}: {response.text[:500]}")
            tickets.extend([{"status": "error", "message": f"HTTP {response.status_code}"}] * len(chunk))
            continue

        try:
            body = response.json()
        except ValueError:
            logger.error("Expo push API returned a non-JSON 200 response; treating chunk as failed.")
            tickets.extend([{"status": "error", "message": "invalid response body"}] * len(chunk))
            continue

        chunk_tickets = body.get("data") or []
        if len(chunk_tickets) != len(chunk):
            # Defensive only -- should never happen per Expo's documented
            # contract, but a shape mismatch must never silently misalign
            # a ticket against the wrong DeviceToken.
            logger.error(f"Expo push API returned {len(chunk_tickets)} tickets for {len(chunk)} messages; padding.")
            chunk_tickets = (list(chunk_tickets) + [{"status": "error", "message": "missing ticket"}] * len(chunk))[:len(chunk)]
        tickets.extend(chunk_tickets)

    return tickets
