"""
ZoomRecordingService.auto_sync_live_class_recording() runs in a manually
spawned thread, outside Django's request cycle. It must close that thread's
DB connections after every attempt -- on success, failure, exception and
early return -- and always before the retry sleep, so idle connections do
not pile up on PostgreSQL.

No DB, Zoom or S3 access: the LiveClass lookup, the Zoom sync, the sleep and
the connection calls are all mocked and recorded in order.
"""
import types
from unittest import mock

import django.db
from django.test import SimpleTestCase

from courses.models import LiveClass
from courses.services.recording import ZoomRecordingService


class AutoSyncDbCleanupTests(SimpleTestCase):
    def _run(self, sync_side_effect, recording_url="", max_retries=3, close_all_side_effect=None):
        log = []
        live_class = types.SimpleNamespace(id=42, recording_url=recording_url)

        fake_objects = mock.Mock()
        fake_objects.filter.return_value.first.side_effect = lambda: (log.append("query"), live_class)[1]

        def sync(lc):
            log.append("sync")
            return sync_side_effect(lc)

        def close_all():
            log.append("close_all")
            if close_all_side_effect:
                raise close_all_side_effect

        with mock.patch.object(LiveClass, "objects", fake_objects), \
                mock.patch.object(ZoomRecordingService, "sync_meeting_recordings", side_effect=sync), \
                mock.patch.object(django.db, "close_old_connections", side_effect=lambda: log.append("close_old")), \
                mock.patch.object(django.db.connections, "close_all", side_effect=close_all), \
                mock.patch("time.sleep", side_effect=lambda s: log.append("sleep")):
            thread = ZoomRecordingService.auto_sync_live_class_recording(42, max_retries=max_retries, delay_seconds=0)
            thread.join(timeout=5)
        self.assertFalse(thread.is_alive())
        return log

    def test_failed_attempts_close_before_each_sleep(self):
        log = self._run(lambda lc: (False, "not ready"))
        one = ["close_old", "query", "sync", "close_all"]
        self.assertEqual(log, one + ["sleep"] + one + ["sleep"] + one)

    def test_success_closes_connections(self):
        log = self._run(lambda lc: (True, "https://s3/x.mp4"))
        self.assertEqual(log, ["close_old", "query", "sync", "close_all"])

    def test_exception_closes_connections_then_retries(self):
        def boom(lc):
            raise RuntimeError("zoom down")
        log = self._run(boom, max_retries=2)
        self.assertEqual(log, ["close_old", "query", "sync", "close_all", "sleep",
                               "close_old", "query", "sync", "close_all"])

    def test_already_synced_early_return_closes_connections(self):
        log = self._run(lambda lc: (False, "unused"), recording_url="https://s3/existing.mp4")
        self.assertEqual(log, ["close_old", "query", "close_all"])

    def test_close_failure_does_not_break_retry_loop(self):
        log = self._run(lambda lc: (False, "not ready"), max_retries=2, close_all_side_effect=RuntimeError("x"))
        self.assertEqual(log, ["close_old", "query", "sync", "close_all", "sleep",
                               "close_old", "query", "sync", "close_all"])
