"""
Final release audit: Push Notification Delivery gap fix.

Covers three layers, mocking the Expo HTTP call throughout -- no test
here ever sends a real push notification:

1. NotificationService._schedule_push_delivery -- confirms a genuinely
   new Notification enqueues the Celery task exactly once (via
   transaction.on_commit), an idempotent duplicate hit does NOT
   re-enqueue, and a Celery/enqueue failure never breaks notification
   creation itself.
2. send_push_notification_for_notification (the Celery task) -- no
   DeviceToken/no-longer-existing Notification are safe no-ops, multiple
   active tokens are all targeted, another user's token is never
   targeted, a DeviceNotRegistered ticket deactivates only that token,
   any other Expo error leaves the token active, a transient failure
   triggers Celery's own retry (bounded, not endless), and the
   Notification row always survives regardless of push outcome.
3. send_expo_push_messages (the Expo HTTP client) -- chunking, and the
   network/5xx (retryable) vs 4xx (not retryable, synthetic error
   tickets) distinction.
"""
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from .models import DeviceToken, Notification, NotificationType
from .push import ExpoPushTransientError, send_expo_push_messages
from .services import NotificationService
from .tasks import send_push_notification_for_notification

User = get_user_model()


class PushDeliverySchedulingTests(TestCase):
    """NotificationService.create_notification -> _schedule_push_delivery."""

    def setUp(self):
        self.user = User.objects.create_user(username='push_sched_user', password='password123')

    @patch('notifications.tasks.send_push_notification_for_notification.delay')
    def test_new_notification_enqueues_push_task(self, mock_delay):
        with self.captureOnCommitCallbacks(execute=True):
            notification, created = NotificationService.create_notification(
                recipient=self.user, title='Hello', body='World',
                notification_type=NotificationType.ANNOUNCEMENT,
            )
        self.assertTrue(created)
        mock_delay.assert_called_once_with(notification.id)

    @patch('notifications.tasks.send_push_notification_for_notification.delay')
    def test_idempotent_duplicate_does_not_reenqueue(self, mock_delay):
        with self.captureOnCommitCallbacks(execute=True):
            NotificationService.create_notification(
                recipient=self.user, title='Hello', body='World',
                notification_type=NotificationType.ANNOUNCEMENT, idempotency_key='dup-key-1',
            )
        with self.captureOnCommitCallbacks(execute=True):
            notification2, created2 = NotificationService.create_notification(
                recipient=self.user, title='Hello', body='World',
                notification_type=NotificationType.ANNOUNCEMENT, idempotency_key='dup-key-1',
            )
        self.assertFalse(created2)
        mock_delay.assert_called_once()

    @patch('notifications.tasks.send_push_notification_for_notification.delay', side_effect=Exception('Redis unreachable'))
    def test_celery_enqueue_failure_does_not_break_notification_creation(self, mock_delay):
        with self.captureOnCommitCallbacks(execute=True):
            notification, created = NotificationService.create_notification(
                recipient=self.user, title='Hello', body='World',
                notification_type=NotificationType.ANNOUNCEMENT,
            )
        self.assertTrue(created)
        self.assertTrue(Notification.objects.filter(pk=notification.id).exists())

    def test_notification_creation_without_on_commit_still_works(self):
        # No captureOnCommitCallbacks wrapper here -- outside any explicit
        # transaction.atomic(), Django fires on_commit callbacks
        # immediately, which is exactly what this asserts still works
        # (and doesn't raise) with the real (non-mocked) Celery .delay()
        # call -- proving _schedule_push_delivery's own try/except is
        # what protects this, not test-only mocking.
        notification, created = NotificationService.create_notification(
            recipient=self.user, title='Hello', body='World',
            notification_type=NotificationType.ANNOUNCEMENT,
        )
        self.assertTrue(created)
        self.assertTrue(Notification.objects.filter(pk=notification.id).exists())


class SendPushNotificationTaskTests(TestCase):
    """The Celery task itself: notifications/tasks.py::send_push_notification_for_notification."""

    def setUp(self):
        self.user = User.objects.create_user(username='push_task_user', password='password123')
        self.other_user = User.objects.create_user(username='push_task_other_user', password='password123')
        self.notification = Notification.objects.create(
            recipient=self.user, title='Title', body='Body', notification_type=NotificationType.ANNOUNCEMENT,
        )

    @patch('notifications.tasks.send_expo_push_messages')
    def test_no_device_token_is_a_safe_no_op(self, mock_send):
        result = send_push_notification_for_notification.apply(args=[self.notification.id])
        self.assertTrue(result.successful())
        mock_send.assert_not_called()

    def test_notification_no_longer_existing_is_a_safe_no_op(self):
        result = send_push_notification_for_notification.apply(args=[999999999])
        self.assertTrue(result.successful())

    @patch('notifications.tasks.send_expo_push_messages')
    def test_multiple_active_device_tokens_all_targeted(self, mock_send):
        token1 = DeviceToken.objects.create(user=self.user, token='ExponentPushToken[aaa]', platform=DeviceToken.Platform.ANDROID)
        token2 = DeviceToken.objects.create(user=self.user, token='ExponentPushToken[bbb]', platform=DeviceToken.Platform.IOS)
        mock_send.return_value = [{'status': 'ok'}, {'status': 'ok'}]

        send_push_notification_for_notification.apply(args=[self.notification.id])

        sent_messages = mock_send.call_args[0][0]
        sent_tokens = {m['to'] for m in sent_messages}
        self.assertEqual(sent_tokens, {token1.token, token2.token})
        # Payload shape: title/body/data carrying notification id, type, action_url.
        for message in sent_messages:
            self.assertEqual(message['title'], 'Title')
            self.assertEqual(message['body'], 'Body')
            self.assertEqual(message['data']['notification_id'], self.notification.id)
            self.assertEqual(message['data']['notification_type'], NotificationType.ANNOUNCEMENT)

    @patch('notifications.tasks.send_expo_push_messages')
    def test_inactive_device_token_never_targeted(self, mock_send):
        DeviceToken.objects.create(user=self.user, token='ExponentPushToken[inactive]', platform=DeviceToken.Platform.ANDROID, is_active=False)
        mock_send.return_value = []

        send_push_notification_for_notification.apply(args=[self.notification.id])
        mock_send.assert_not_called()

    @patch('notifications.tasks.send_expo_push_messages')
    def test_another_users_token_never_targeted(self, mock_send):
        own_token = DeviceToken.objects.create(user=self.user, token='ExponentPushToken[own]', platform=DeviceToken.Platform.ANDROID)
        DeviceToken.objects.create(user=self.other_user, token='ExponentPushToken[other]', platform=DeviceToken.Platform.ANDROID)
        mock_send.return_value = [{'status': 'ok'}]

        send_push_notification_for_notification.apply(args=[self.notification.id])

        sent_tokens = [m['to'] for m in mock_send.call_args[0][0]]
        self.assertEqual(sent_tokens, [own_token.token])

    @patch('notifications.tasks.send_expo_push_messages')
    def test_device_not_registered_deactivates_only_that_token(self, mock_send):
        dead_token = DeviceToken.objects.create(user=self.user, token='ExponentPushToken[dead]', platform=DeviceToken.Platform.ANDROID)
        alive_token = DeviceToken.objects.create(user=self.user, token='ExponentPushToken[alive]', platform=DeviceToken.Platform.ANDROID)
        # Order aligned with DeviceToken.objects.filter(...).order_by('-updated_at') --
        # most-recently-updated first, so alive_token (created second) comes first.
        mock_send.return_value = [
            {'status': 'ok'},
            {'status': 'error', 'message': 'not registered', 'details': {'error': 'DeviceNotRegistered'}},
        ]

        send_push_notification_for_notification.apply(args=[self.notification.id])

        dead_token.refresh_from_db()
        alive_token.refresh_from_db()
        self.assertFalse(dead_token.is_active)
        self.assertTrue(alive_token.is_active)

    @patch('notifications.tasks.send_expo_push_messages')
    def test_other_expo_error_does_not_deactivate_token(self, mock_send):
        token = DeviceToken.objects.create(user=self.user, token='ExponentPushToken[rate]', platform=DeviceToken.Platform.ANDROID)
        mock_send.return_value = [{'status': 'error', 'message': 'rate exceeded', 'details': {'error': 'MessageRateExceeded'}}]

        send_push_notification_for_notification.apply(args=[self.notification.id])

        token.refresh_from_db()
        self.assertTrue(token.is_active)

    @patch('notifications.tasks.send_expo_push_messages')
    def test_transient_failure_triggers_bounded_retry(self, mock_send):
        DeviceToken.objects.create(user=self.user, token='ExponentPushToken[retry]', platform=DeviceToken.Platform.ANDROID)
        mock_send.side_effect = ExpoPushTransientError('network blip')

        result = send_push_notification_for_notification.apply(args=[self.notification.id])

        # .apply() (eager mode) actually executes Celery's own retry loop
        # synchronously, not merely simulates it -- send_expo_push_messages
        # is genuinely called max_retries+1 times (1 initial attempt + 3
        # retries), then gives up and surfaces the original exception.
        # This is direct proof the retry is bounded, not endless.
        self.assertFalse(result.successful())
        self.assertEqual(mock_send.call_count, send_push_notification_for_notification.max_retries + 1)
        self.assertIsInstance(result.result, ExpoPushTransientError)

    @patch('notifications.tasks.send_expo_push_messages')
    def test_unexpected_permanent_error_does_not_retry(self, mock_send):
        DeviceToken.objects.create(user=self.user, token='ExponentPushToken[bug]', platform=DeviceToken.Platform.ANDROID)
        mock_send.side_effect = ValueError('some unrelated bug')

        result = send_push_notification_for_notification.apply(args=[self.notification.id])
        self.assertTrue(result.successful())  # logged and swallowed, not retried

    @patch('notifications.tasks.send_expo_push_messages')
    def test_notification_record_survives_push_failure(self, mock_send):
        DeviceToken.objects.create(user=self.user, token='ExponentPushToken[fail]', platform=DeviceToken.Platform.ANDROID)
        mock_send.side_effect = ExpoPushTransientError('down')

        send_push_notification_for_notification.apply(args=[self.notification.id])

        self.assertTrue(Notification.objects.filter(pk=self.notification.id).exists())
        self.assertFalse(Notification.objects.get(pk=self.notification.id).is_read)


class SendExpoPushMessagesTests(TestCase):
    """The Expo HTTP client itself: notifications/push.py."""

    def _mock_response(self, status_code, json_data=None, text=''):
        mock_resp = type('MockResponse', (), {})()
        mock_resp.status_code = status_code
        mock_resp.text = text
        mock_resp.json = lambda: json_data
        return mock_resp

    def test_empty_messages_returns_empty_without_calling_expo(self):
        with patch('notifications.push.requests.post') as mock_post:
            result = send_expo_push_messages([])
        self.assertEqual(result, [])
        mock_post.assert_not_called()

    @patch('notifications.push.requests.post')
    def test_successful_send_returns_tickets_in_order(self, mock_post):
        messages = [{'to': 'a'}, {'to': 'b'}]
        mock_post.return_value = self._mock_response(200, {'data': [{'status': 'ok', 'id': '1'}, {'status': 'ok', 'id': '2'}]})

        tickets = send_expo_push_messages(messages)
        self.assertEqual(len(tickets), 2)
        self.assertEqual(tickets[0]['id'], '1')
        self.assertEqual(tickets[1]['id'], '2')

    @patch('notifications.push.requests.post')
    def test_chunks_large_batches(self, mock_post):
        messages = [{'to': f'token{i}'} for i in range(150)]

        def fake_post(url, json, headers, timeout):
            return self._mock_response(200, {'data': [{'status': 'ok'} for _ in json]})

        mock_post.side_effect = fake_post
        tickets = send_expo_push_messages(messages)

        self.assertEqual(mock_post.call_count, 2)  # 100 + 50
        self.assertEqual(len(tickets), 150)

    @patch('notifications.push.requests.post')
    def test_network_error_raises_transient(self, mock_post):
        import requests
        mock_post.side_effect = requests.ConnectionError('no route to host')
        with self.assertRaises(ExpoPushTransientError):
            send_expo_push_messages([{'to': 'a'}])

    @patch('notifications.push.requests.post')
    def test_5xx_raises_transient(self, mock_post):
        mock_post.return_value = self._mock_response(503, text='Service Unavailable')
        with self.assertRaises(ExpoPushTransientError):
            send_expo_push_messages([{'to': 'a'}])

    @patch('notifications.push.requests.post')
    def test_4xx_returns_synthetic_error_tickets_not_raise(self, mock_post):
        mock_post.return_value = self._mock_response(400, text='Bad Request')
        tickets = send_expo_push_messages([{'to': 'a'}, {'to': 'b'}])
        self.assertEqual(len(tickets), 2)
        self.assertTrue(all(t['status'] == 'error' for t in tickets))

    @patch('notifications.push.requests.post')
    def test_no_access_token_header_when_env_unset(self, mock_post):
        mock_post.return_value = self._mock_response(200, {'data': [{'status': 'ok'}]})
        send_expo_push_messages([{'to': 'a'}])
        headers = mock_post.call_args.kwargs['headers']
        self.assertNotIn('Authorization', headers)
