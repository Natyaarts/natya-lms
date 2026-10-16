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
