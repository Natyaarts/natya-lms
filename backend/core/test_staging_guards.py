from django.test import TestCase
from django.core.exceptions import ImproperlyConfigured
import os
import sys

# Import the validator module directly
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from validate_target_db import evaluate_staging_target, REQUIRED_STAGING_DB_NAME, RECOGNIZED_ENVIRONMENTS


class StagingSecurityGuardsTestCase(TestCase):
    """
    Validates fail-closed security guards, environment identity requirements,
    and strict pre-migration target checks.
    """

    def setUp(self):
        self.valid_staging_env = {
            'ENVIRONMENT': 'staging',
            'EXPECTED_STAGING_DB_HOST': 'natyalms-staging-db.cxxxx.ap-south-1.rds.amazonaws.com',
            'PROD_DB_HOST': 'natyalms-prod-db.cxxxx.ap-south-1.rds.amazonaws.com',
        }
        self.valid_staging_params = {
            'environment_vars': self.valid_staging_env,
            'active_db_name': 'natyalms_staging_db',
            'active_db_host': 'natyalms-staging-db.cxxxx.ap-south-1.rds.amazonaws.com',
            'active_bucket': 'natyalms-media-staging-2026',
            'rzp_key_id': 'rzp_test_1234567890ABC',
            'rzp_webhook_secret': 'test_staging_webhook_secret_123',
            'connection_test_fn': lambda: True,
        }

    # ------------------------------------------------------------------------
    # 1. Environment Identity & No-Bypass Tests
    # ------------------------------------------------------------------------
    def test_missing_environment_fails_closed(self):
        """
        Verify that missing, None, or empty ENVIRONMENT variable fails closed immediately.
        Under no circumstances may an empty environment bypass target validation.
        """
        for empty_val in ['', '   ', None]:
            env = dict(self.valid_staging_env)
            if empty_val is None:
                env.pop('ENVIRONMENT', None)
            else:
                env['ENVIRONMENT'] = empty_val

            params = dict(self.valid_staging_params, environment_vars=env)
            is_ok, msg = evaluate_staging_target(**params)
            self.assertFalse(is_ok, f"Expected empty environment {empty_val!r} to fail closed")
            self.assertIn("ENVIRONMENT environment variable is missing", msg)

    def test_misspelled_or_unrecognized_environment_fails_closed(self):
        """
        Verify that misspelled or unexpected environment identities fail closed.
        """
        misspelled = ['stagging', 'stg', 'stagee', 'prod', 'production_test', 'random_env', 'preprod', 'preview']
        for bad_env in misspelled:
            env = dict(self.valid_staging_env, ENVIRONMENT=bad_env)
            params = dict(self.valid_staging_params, environment_vars=env)
            is_ok, msg = evaluate_staging_target(**params)
            self.assertFalse(is_ok, f"Expected bad environment '{bad_env}' to fail closed")
            self.assertIn("Unrecognized or misspelled ENVIRONMENT identity", msg)

    # ------------------------------------------------------------------------
    # 2. Strict Staging Target Verification Tests
    # ------------------------------------------------------------------------
    def test_evaluate_staging_target_valid_success(self):
        """
        Verify that a completely valid staging environment passes all checks.
        """
        is_ok, msg = evaluate_staging_target(**self.valid_staging_params)
        self.assertTrue(is_ok, f"Expected success but got error: {msg}")
        self.assertIn("PASSED", msg)

    def test_evaluate_staging_target_missing_host_env(self):
        """
        Verify fail-closed behavior when EXPECTED_STAGING_DB_HOST is absent or empty.
        """
        env = dict(self.valid_staging_env, EXPECTED_STAGING_DB_HOST='')
        params = dict(self.valid_staging_params, environment_vars=env)
        is_ok, msg = evaluate_staging_target(**params)
        self.assertFalse(is_ok)
        self.assertIn("EXPECTED_STAGING_DB_HOST is missing", msg)

    def test_evaluate_staging_target_missing_s3_bucket(self):
        """
        Verify fail-closed behavior when AWS_STORAGE_BUCKET_NAME is missing or empty.
        """
        params = dict(self.valid_staging_params, active_bucket='')
        is_ok, msg = evaluate_staging_target(**params)
        self.assertFalse(is_ok)
        self.assertIn("AWS_STORAGE_BUCKET_NAME is missing", msg)

    def test_evaluate_staging_target_missing_razorpay_credentials(self):
        """
        Verify fail-closed behavior when RAZORPAY_KEY_ID or WEBHOOK_SECRET is missing.
        """
        params1 = dict(self.valid_staging_params, rzp_key_id='')
        is_ok1, msg1 = evaluate_staging_target(**params1)
        self.assertFalse(is_ok1)
        self.assertIn("RAZORPAY_KEY_ID is missing", msg1)

        params2 = dict(self.valid_staging_params, rzp_webhook_secret='')
        is_ok2, msg2 = evaluate_staging_target(**params2)
        self.assertFalse(is_ok2)
        self.assertIn("RAZORPAY_WEBHOOK_SECRET is missing", msg2)

    def test_evaluate_staging_target_strict_db_name_rejection(self):
        """
        Verify that ONLY exact 'natyalms_staging_db' is accepted, and all other names are rejected.
        Permissive checks that previously accepted anything containing 'staging' must fail.
        """
        rejected_names = [
            'natyalms',
            'natyalms_prod',
            'natyalms_production',
            'production',
            'staging_db',
            'natya_staging',
            'stage_lms',
            'natyalms_staging',
            'my_staging_db',
        ]
        for name in rejected_names:
            params = dict(self.valid_staging_params, active_db_name=name)
            is_ok, msg = evaluate_staging_target(**params)
            self.assertFalse(is_ok, f"Expected '{name}' to be rejected but it was accepted!")

        # Exactly 'natyalms_staging_db' must pass
        params_exact = dict(self.valid_staging_params, active_db_name=REQUIRED_STAGING_DB_NAME)
        is_ok, msg = evaluate_staging_target(**params_exact)
        self.assertTrue(is_ok, f"Exact name '{REQUIRED_STAGING_DB_NAME}' should be accepted: {msg}")

    def test_evaluate_staging_target_endpoint_mismatch(self):
        """
        Verify that active host differing from EXPECTED_STAGING_DB_HOST fails closed.
        """
        params = dict(self.valid_staging_params, active_db_host='rogue-host.rds.amazonaws.com')
        is_ok, msg = evaluate_staging_target(**params)
        self.assertFalse(is_ok)
        self.assertIn("Strict endpoint mismatch", msg)

    def test_evaluate_staging_target_production_host_rejection(self):
        """
        Verify that active host matching PROD_DB_HOST is rejected with security violation.
        """
        prod_host = self.valid_staging_env['PROD_DB_HOST']
        env = dict(self.valid_staging_env, EXPECTED_STAGING_DB_HOST=prod_host)
        params = dict(self.valid_staging_params, environment_vars=env, active_db_host=prod_host)
        is_ok, msg = evaluate_staging_target(**params)
        self.assertFalse(is_ok)
        self.assertIn("PROD_DB_HOST", msg)

    def test_evaluate_staging_target_connection_failure(self):
        """
        Verify that failure of the live database connection function aborts deployment.
        """
        def failing_connection():
            raise ConnectionError("FATAL: database connection refused on port 5432")

        params = dict(self.valid_staging_params, connection_test_fn=failing_connection)
        is_ok, msg = evaluate_staging_target(**params)
        self.assertFalse(is_ok)
        self.assertIn("Live connection verification", msg)
        self.assertIn("connection refused", msg)

    def test_evaluate_staging_target_production_s3_bucket(self):
        """
        Verify that active bucket 'natyalms-media-2026' is rejected with security violation.
        """
        params = dict(self.valid_staging_params, active_bucket='natyalms-media-2026')
        is_ok, msg = evaluate_staging_target(**params)
        self.assertFalse(is_ok)
        self.assertIn("CRITICAL S3 VIOLATION", msg)

    def test_evaluate_staging_target_live_razorpay_keys(self):
        """
        Verify that live Razorpay keys (rzp_live_...) are strictly rejected.
        """
        params = dict(self.valid_staging_params, rzp_key_id='rzp_live_realproductionkey123')
        is_ok, msg = evaluate_staging_target(**params)
        self.assertFalse(is_ok)
        self.assertIn("CRITICAL PAYMENT HAZARD", msg)

    # ------------------------------------------------------------------------
    # 3. Production Target Verification Tests
    # ------------------------------------------------------------------------
    def test_production_environment_rejects_staging_resources(self):
        """
        Verify that if ENVIRONMENT=production, it strictly rejects staging database,
        staging host, staging bucket, or test Razorpay keys.
        """
        prod_env = {'ENVIRONMENT': 'production', 'EXPECTED_STAGING_DB_HOST': 'staging-db.rds.amazonaws.com'}

        # Rejects staging database name
        params_db = {
            'environment_vars': prod_env,
            'active_db_name': 'natyalms_staging_db',
            'active_db_host': 'prod-db.rds.amazonaws.com',
            'active_bucket': 'natyalms-media-2026',
            'rzp_key_id': 'rzp_live_123',
            'connection_test_fn': lambda: True,
        }
        is_ok, msg = evaluate_staging_target(**params_db)
        self.assertFalse(is_ok)
        self.assertIn("staging database", msg)

        # Rejects staging host
        params_host = dict(params_db, active_db_name='natyalms_prod_db', active_db_host='staging-db.rds.amazonaws.com')
        is_ok, msg = evaluate_staging_target(**params_host)
        self.assertFalse(is_ok)
        self.assertIn("staging DB host", msg)

        # Rejects staging bucket
        params_bucket = dict(params_db, active_db_name='natyalms_prod_db', active_bucket='natyalms-media-staging-2026')
        is_ok, msg = evaluate_staging_target(**params_bucket)
        self.assertFalse(is_ok)
        self.assertIn("staging S3 bucket", msg)

        # Rejects test Razorpay key in production
        params_rzp = dict(params_db, active_db_name='natyalms_prod_db', rzp_key_id='rzp_test_mock123')
        is_ok, msg = evaluate_staging_target(**params_rzp)
        self.assertFalse(is_ok)
        self.assertIn("Test Razorpay credentials", msg)

        # Valid production target passes
        params_valid_prod = dict(params_db, active_db_name='natyalms_prod_db')
        is_ok, msg = evaluate_staging_target(**params_valid_prod)
        self.assertTrue(is_ok)
        self.assertIn("Production pre-migration target checks PASSED", msg)


from unittest.mock import patch, MagicMock
from django.conf import settings
from courses.services.recording import ZoomRecordingService
from chat.views import get_attachment_presigned_url
from courses.views import VideoLessonViewSet


class StagingS3IAMRoleStorageTests(TestCase):
    """
    Validates S3 storage settings and fail-closed behavior for IAM role usage
    in staging vs explicit credential requirement in production.
    """

    def tearDown(self):
        # Restore default test settings state
        import importlib
        from core import settings as reloaded_settings
        importlib.reload(reloaded_settings)

    def test_staging_s3_storage_configured_with_iam_role_when_static_keys_absent(self):
        """
        Verify that in staging (IS_STAGING=True), S3 storage is enabled when
        AWS_STORAGE_BUCKET_NAME is set even if static keys are absent (None),
        allowing boto3 to use the EC2 instance profile IAM role.
        """
        import importlib
        with patch('dotenv.load_dotenv'):
            test_env = {
                'DEBUG': 'False',
                'ENVIRONMENT': 'staging',
                'SECRET_KEY': 'test-secret-staging',
                'ALLOWED_HOSTS': 'example.com',
                'RDS_DB_NAME': 'natyalms_staging_db',
                'RDS_USERNAME': 'user',
                'RDS_PASSWORD': 'pwd',
                'RDS_HOSTNAME': 'staging-db.rds.amazonaws.com',
                'RDS_PORT': '5432',
                'RAZORPAY_KEY_ID': 'rzp_test_mock123',
                'RAZORPAY_KEY_SECRET': 'test_secret',
                'RAZORPAY_WEBHOOK_SECRET': 'test_webhook_sec',
                'AWS_STORAGE_BUCKET_NAME': 'natyalms-staging-media-2026',
                'CELERY_BROKER_URL': 'redis://staging-redis:6379/0',
                'CELERY_RESULT_BACKEND': 'redis://staging-redis:6379/0',
            }
            with patch.dict(os.environ, test_env, clear=True):
                from core import settings as reloaded_settings
                importlib.reload(reloaded_settings)
                self.assertTrue(reloaded_settings.IS_STAGING)
                self.assertIsNone(reloaded_settings.AWS_ACCESS_KEY_ID)
                self.assertIsNone(reloaded_settings.AWS_SECRET_ACCESS_KEY)
                self.assertEqual(reloaded_settings.AWS_STORAGE_BUCKET_NAME, 'natyalms-staging-media-2026')
                self.assertTrue(reloaded_settings.ALLOW_IAM_ROLE)
                self.assertTrue(reloaded_settings.AWS_S3_ENABLED)
                self.assertEqual(
                    reloaded_settings.STORAGES['default']['BACKEND'],
                    'storages.backends.s3boto3.S3Boto3Storage'
                )

    def test_production_s3_storage_fails_closed_when_static_keys_absent(self):
        """
        Verify that in production (ENVIRONMENT=production, DEBUG=False),
        missing AWS static credentials raises ImproperlyConfigured unless
        AWS_USE_IAM_ROLE is explicitly enabled.
        """
        import importlib
        with patch('dotenv.load_dotenv'):
            prod_env = {
                'DEBUG': 'False',
                'ENVIRONMENT': 'production',
                'SECRET_KEY': 'test-secret-prod',
                'ALLOWED_HOSTS': 'example.com',
                'RDS_DB_NAME': 'natyalms_prod_db',
                'RDS_USERNAME': 'user',
                'RDS_PASSWORD': 'pwd',
                'RDS_HOSTNAME': 'prod-db.rds.amazonaws.com',
                'RDS_PORT': '5432',
                'INTERAKT_SECRET_KEY': 'test_interakt',
                'RAZORPAY_KEY_ID': 'rzp_live_real123',
                'RAZORPAY_KEY_SECRET': 'test_secret',
                'AWS_STORAGE_BUCKET_NAME': 'natyalms-media-2026',
            }
            with patch.dict(os.environ, prod_env, clear=True):
                from core import settings as reloaded_settings
                with self.assertRaises(ImproperlyConfigured) as ctx:
                    importlib.reload(reloaded_settings)
                self.assertIn("AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY must be set in production", str(ctx.exception))

    def test_missing_bucket_fails_closed_in_staging_and_production(self):
        """
        Verify that missing AWS_STORAGE_BUCKET_NAME raises ImproperlyConfigured
        when DEBUG=False.
        """
        import importlib
        with patch('dotenv.load_dotenv'):
            staging_env = {
                'DEBUG': 'False',
                'ENVIRONMENT': 'staging',
                'SECRET_KEY': 'test-secret-staging',
                'ALLOWED_HOSTS': 'example.com',
                'RDS_DB_NAME': 'natyalms_staging_db',
                'RDS_USERNAME': 'user',
                'RDS_PASSWORD': 'pwd',
                'RDS_HOSTNAME': 'staging-db.rds.amazonaws.com',
                'RDS_PORT': '5432',
                'RAZORPAY_KEY_ID': 'rzp_test_mock123',
                'RAZORPAY_KEY_SECRET': 'test_secret',
                'RAZORPAY_WEBHOOK_SECRET': 'test_webhook_sec',
            }
            with patch.dict(os.environ, staging_env, clear=True):
                from core import settings as reloaded_settings
                with self.assertRaises(ImproperlyConfigured) as ctx:
                    importlib.reload(reloaded_settings)
                self.assertIn("AWS_STORAGE_BUCKET_NAME must be set in production/staging", str(ctx.exception))

    def test_staging_s3_storage_preserves_static_credentials_when_provided(self):
        """
        Verify that if static AWS keys are explicitly provided in staging,
        they are preserved on AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY.
        """
        import importlib
        with patch('dotenv.load_dotenv'):
            staging_env = {
                'DEBUG': 'False',
                'ENVIRONMENT': 'staging',
                'SECRET_KEY': 'test-secret-staging',
                'ALLOWED_HOSTS': 'example.com',
                'RDS_DB_NAME': 'natyalms_staging_db',
                'RDS_USERNAME': 'user',
                'RDS_PASSWORD': 'pwd',
                'RDS_HOSTNAME': 'staging-db.rds.amazonaws.com',
                'RDS_PORT': '5432',
                'RAZORPAY_KEY_ID': 'rzp_test_mock123',
                'RAZORPAY_KEY_SECRET': 'test_secret',
                'RAZORPAY_WEBHOOK_SECRET': 'test_webhook_sec',
                'AWS_STORAGE_BUCKET_NAME': 'natyalms-staging-media-2026',
                'AWS_ACCESS_KEY_ID': 'explicit_staging_key',
                'AWS_SECRET_ACCESS_KEY': 'explicit_staging_secret',
                'CELERY_BROKER_URL': 'redis://staging-redis:6379/0',
                'CELERY_RESULT_BACKEND': 'redis://staging-redis:6379/0',
            }
            with patch.dict(os.environ, staging_env, clear=True):
                from core import settings as reloaded_settings
                importlib.reload(reloaded_settings)
                self.assertEqual(reloaded_settings.AWS_ACCESS_KEY_ID, 'explicit_staging_key')
                self.assertEqual(reloaded_settings.AWS_SECRET_ACCESS_KEY, 'explicit_staging_secret')
                self.assertTrue(reloaded_settings.HAS_AWS_KEYS)


class DirectS3ClientCredentialOmissionTests(TestCase):
    """
    Validates that direct S3 client instantiation passes credentials when explicit keys
    are present, and strictly omits aws_access_key_id / aws_secret_access_key when absent,
    allowing boto3's default credential provider chain (IAM role) to activate.
    """

    def test_recording_service_omits_credentials_when_absent(self):
        with patch('courses.services.recording.boto3.client') as mock_boto:
            with patch.object(settings, 'AWS_ACCESS_KEY_ID', None), patch.object(settings, 'AWS_SECRET_ACCESS_KEY', None):
                ZoomRecordingService.get_s3_client()
                mock_boto.assert_called_once()
                kwargs = mock_boto.call_args.kwargs
                self.assertNotIn('aws_access_key_id', kwargs)
                self.assertNotIn('aws_secret_access_key', kwargs)
                self.assertEqual(kwargs['region_name'], 'ap-south-1')

    def test_recording_service_passes_credentials_when_present(self):
        with patch('courses.services.recording.boto3.client') as mock_boto:
            with patch.object(settings, 'AWS_ACCESS_KEY_ID', 'test-key'), patch.object(settings, 'AWS_SECRET_ACCESS_KEY', 'test-secret'):
                ZoomRecordingService.get_s3_client()
                mock_boto.assert_called_once()
                kwargs = mock_boto.call_args.kwargs
                self.assertEqual(kwargs.get('aws_access_key_id'), 'test-key')
                self.assertEqual(kwargs.get('aws_secret_access_key'), 'test-secret')

    def test_chat_presigned_url_omits_credentials_when_absent(self):
        msg = MagicMock()
        msg.attachment.name = 'chat/file.pdf'
        msg.attachment_name = 'file.pdf'

        with patch('chat.views.boto3.client') as mock_boto:
            with patch.object(settings, 'AWS_STORAGE_BUCKET_NAME', 'natyalms-staging-media-2026'), \
                 patch.object(settings, 'AWS_ACCESS_KEY_ID', None), \
                 patch.object(settings, 'AWS_SECRET_ACCESS_KEY', None), \
                 patch.object(settings, 'IS_STAGING', True):
                get_attachment_presigned_url(msg)
                mock_boto.assert_called_once()
                kwargs = mock_boto.call_args.kwargs
                self.assertNotIn('aws_access_key_id', kwargs)
                self.assertNotIn('aws_secret_access_key', kwargs)

    def test_chat_presigned_url_passes_credentials_when_present(self):
        msg = MagicMock()
        msg.attachment.name = 'chat/file.pdf'
        msg.attachment_name = 'file.pdf'

        with patch('chat.views.boto3.client') as mock_boto:
            with patch.object(settings, 'AWS_STORAGE_BUCKET_NAME', 'natyalms-staging-media-2026'), \
                 patch.object(settings, 'AWS_ACCESS_KEY_ID', 'test-key'), \
                 patch.object(settings, 'AWS_SECRET_ACCESS_KEY', 'test-secret'), \
                 patch.object(settings, 'IS_STAGING', True):
                get_attachment_presigned_url(msg)
                mock_boto.assert_called_once()
                kwargs = mock_boto.call_args.kwargs
                self.assertEqual(kwargs.get('aws_access_key_id'), 'test-key')
                self.assertEqual(kwargs.get('aws_secret_access_key'), 'test-secret')

    def test_lesson_upload_url_omits_credentials_when_absent(self):
        request = MagicMock()
        request.user = MagicMock(is_authenticated=True, is_superuser=True)
        request.data = {'filename': 'lesson1.mp4'}

        view = VideoLessonViewSet()
        view.request = request

        with patch('boto3.client') as mock_boto:
            with patch.object(settings, 'AWS_STORAGE_BUCKET_NAME', 'natyalms-staging-media-2026'), \
                 patch.object(settings, 'AWS_ACCESS_KEY_ID', None), \
                 patch.object(settings, 'AWS_SECRET_ACCESS_KEY', None):
                view.get_upload_url(request)
                mock_boto.assert_called_once()
                kwargs = mock_boto.call_args.kwargs
                self.assertNotIn('aws_access_key_id', kwargs)
                self.assertNotIn('aws_secret_access_key', kwargs)

    def test_lesson_upload_url_passes_credentials_when_present(self):
        request = MagicMock()
        request.user = MagicMock(is_authenticated=True, is_superuser=True)
        request.data = {'filename': 'lesson1.mp4'}

        view = VideoLessonViewSet()
        view.request = request

        with patch('boto3.client') as mock_boto:
            with patch.object(settings, 'AWS_STORAGE_BUCKET_NAME', 'natyalms-staging-media-2026'), \
                 patch.object(settings, 'AWS_ACCESS_KEY_ID', 'prod-key'), \
                 patch.object(settings, 'AWS_SECRET_ACCESS_KEY', 'prod-secret'):
                view.get_upload_url(request)
                mock_boto.assert_called_once()
                kwargs = mock_boto.call_args.kwargs
                self.assertEqual(kwargs.get('aws_access_key_id'), 'prod-key')
                self.assertEqual(kwargs.get('aws_secret_access_key'), 'prod-secret')
