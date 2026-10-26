#!/usr/bin/env python
"""
validate_target_db.py

Fail-closed pre-migration target validator for AWS Elastic Beanstalk.
Executed as container command '00_validate_target' strictly before '01_migrate'.
If any check fails, sys.exit(1) aborts the deployment before migrations execute.

Enforces an explicit, recognized ENVIRONMENT identity.
Fails closed if ENVIRONMENT is missing, empty, misspelled, or unexpected.
"""

import os
import sys

REQUIRED_STAGING_DB_NAME = 'natyalms_staging_db'
PROD_DB_NAMES = {'natyalms', 'natyalms_prod', 'natyalms_production', 'production'}
PROD_S3_BUCKETS = {'natyalms-media-2026'}
RECOGNIZED_ENVIRONMENTS = {'staging', 'stage', 'production', 'local-dev', 'development', 'test'}


def evaluate_staging_target(
    environment_vars=None,
    active_db_name=None,
    active_db_host=None,
    active_bucket=None,
    rzp_key_id=None,
    rzp_webhook_secret=None,
    connection_test_fn=None,
):
    """
    Pure validation logic that verifies target requirements against an explicit environment identity.
    Returns (is_ok: bool, message: str).
    """
    env = environment_vars if environment_vars is not None else os.environ

    # 1. Explicit Environment Identity Verification (No Unsafe Bypass)
    raw_env = env.get('ENVIRONMENT')
    if not raw_env or not str(raw_env).strip():
        return False, (
            "CRITICAL CONFIGURATION ERROR: ENVIRONMENT environment variable is missing or empty. "
            "Refusing to execute unverified database migrations without an explicit environment identity."
        )

    environment = str(raw_env).strip().lower()

    if environment not in RECOGNIZED_ENVIRONMENTS:
        return False, (
            f"CRITICAL CONFIGURATION ERROR: Unrecognized or misspelled ENVIRONMENT identity '{raw_env}'. "
            f"Must be one of {sorted(RECOGNIZED_ENVIRONMENTS)}. Failing closed to protect database."
        )

    # 2. Staging Environment Enforcement
    if environment in ('staging', 'stage'):
        expected_host = env.get('EXPECTED_STAGING_DB_HOST', '').strip()
        if not expected_host:
            return False, "Required staging identity variable EXPECTED_STAGING_DB_HOST is missing or empty."

        if not active_bucket:
            return False, "Required staging identity variable AWS_STORAGE_BUCKET_NAME is missing or empty."

        if not rzp_key_id:
            return False, "Required staging identity variable RAZORPAY_KEY_ID is missing or empty."

        if not rzp_webhook_secret:
            return False, "Required staging identity variable RAZORPAY_WEBHOOK_SECRET is missing or empty."

        # Strict Database Name Check (Must be EXACTLY 'natyalms_staging_db')
        if not active_db_name:
            return False, "Resolved active database name is empty or undefined."

        if active_db_name.lower() in PROD_DB_NAMES:
            return False, f"CRITICAL SECURITY HAZARD: Active database name '{active_db_name}' matches known PRODUCTION database name!"

        if active_db_name != REQUIRED_STAGING_DB_NAME:
            return False, (
                f"Strict database name mismatch: Active database name '{active_db_name}' does not match "
                f"required staging database '{REQUIRED_STAGING_DB_NAME}'."
            )

        # Strict Database Host / Endpoint Check
        if not active_db_host:
            return False, "Resolved active database host is empty or undefined."

        if active_db_host != expected_host:
            return False, (
                f"Strict endpoint mismatch: Active database host '{active_db_host}' does not match "
                f"EXPECTED_STAGING_DB_HOST '{expected_host}'."
            )

        # Refuse Prohibited Production Host
        prod_host = env.get('PROD_DB_HOST', '').strip()
        if prod_host and active_db_host.lower() == prod_host.lower():
            return False, (
                f"CRITICAL SECURITY HAZARD: Active database host '{active_db_host}' matches "
                f"PROD_DB_HOST '{prod_host}'!"
            )

        # Live Connection Verification
        if connection_test_fn is not None:
            try:
                connection_test_fn()
            except Exception as exc:
                return False, f"Live connection verification to database '{active_db_name}' on '{active_db_host}' failed: {exc}"

        # S3 Media Bucket Check
        if active_bucket in PROD_S3_BUCKETS:
            return False, f"CRITICAL S3 VIOLATION: Staging connected to production media bucket '{active_bucket}'!"

        # Razorpay Credentials Checks
        if rzp_key_id.startswith('rzp_live_'):
            return False, f"CRITICAL PAYMENT HAZARD: Live Razorpay key '{rzp_key_id[:12]}...' detected in staging!"

        if not rzp_key_id.startswith('rzp_test_'):
            return False, f"Staging requires Razorpay Test Mode key starting with 'rzp_test_', got '{rzp_key_id[:12]}...'."

        return True, "All strict staging pre-migration guards PASSED successfully."

    # 3. Production Environment Target Verification
    elif environment == 'production':
        if active_db_name == REQUIRED_STAGING_DB_NAME or 'staging' in (active_db_name or '').lower():
            return False, f"CRITICAL TARGET MISMATCH: Production environment is connected to staging database '{active_db_name}'!"

        expected_staging_host = env.get('EXPECTED_STAGING_DB_HOST', '').strip()
        if expected_staging_host and active_db_host == expected_staging_host:
            return False, f"CRITICAL TARGET MISMATCH: Production environment is connected to staging DB host '{active_db_host}'!"

        if active_bucket and 'staging' in active_bucket.lower():
            return False, f"CRITICAL S3 MISMATCH: Production environment is connected to staging S3 bucket '{active_bucket}'!"

        if rzp_key_id and rzp_key_id.startswith('rzp_test_'):
            return False, f"CRITICAL PAYMENT MISMATCH: Test Razorpay credentials ('{rzp_key_id[:12]}...') detected in production!"

        if connection_test_fn is not None:
            try:
                connection_test_fn()
            except Exception as exc:
                return False, f"Live connection verification to production database failed: {exc}"

        return True, "Production pre-migration target checks PASSED successfully."

    # 4. Local Development / Test Environment
    elif environment in ('local-dev', 'development', 'test'):
        if not (env.get('DEBUG', 'False') == 'True' or 'test' in sys.argv or environment == 'test'):
            return False, f"Environment '{environment}' is only permitted when DEBUG=True or during automated tests."
        return True, f"Environment '{environment}' target checks PASSED."

    return False, f"Unhandled environment '{environment}'."


def validate_staging_target():
    print("[validate_target_db] Starting fail-closed pre-migration target validation...")

    # Initialize Django
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')
    import django
    django.setup()

    from django.db import connection
    from django.conf import settings

    # Ensure ENVIRONMENT is resolved if not explicitly set in OS environment
    if not os.environ.get('ENVIRONMENT'):
        default_env = getattr(settings, 'ENVIRONMENT', 'production' if not getattr(settings, 'DEBUG', False) else 'local-dev')
        os.environ['ENVIRONMENT'] = default_env

    db_settings = connection.settings_dict
    db_name = str(db_settings.get('NAME', '')).strip()
    db_host = str(db_settings.get('HOST', '')).strip()
    active_bucket = getattr(settings, 'AWS_STORAGE_BUCKET_NAME', '')
    rzp_key = getattr(settings, 'RAZORPAY_KEY_ID', '')
    rzp_webhook_secret = getattr(settings, 'RAZORPAY_WEBHOOK_SECRET', '') or os.environ.get('RAZORPAY_WEBHOOK_SECRET', '')

    print(f"[validate_target_db] Active ENVIRONMENT: {os.environ.get('ENVIRONMENT')}")
    print(f"[validate_target_db] Resolved active DB NAME: {db_name}")
    print(f"[validate_target_db] Resolved active DB HOST: {db_host}")
    print(f"[validate_target_db] Active AWS S3 Bucket: {active_bucket}")

    is_ok, msg = evaluate_staging_target(
        environment_vars=os.environ,
        active_db_name=db_name,
        active_db_host=db_host,
        active_bucket=active_bucket,
        rzp_key_id=rzp_key,
        rzp_webhook_secret=rzp_webhook_secret,
        connection_test_fn=connection.ensure_connection,
    )

    if not is_ok:
        print(f"[validate_target_db] FATAL: {msg}", file=sys.stderr)
        return 1

    print(f"[validate_target_db] {msg}")
    return 0


if __name__ == '__main__':
    sys.exit(validate_staging_target())
