"""
Unit tests for hardened target database validation (validate_target_db.py).
Verifies strict fail-closed protection for test, local-dev, and development environments.
"""
import unittest
import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from validate_target_db import evaluate_staging_target


class TargetValidationHardeningTests(unittest.TestCase):
    """
    Validates all 9 safety rules required by Phase A.1.
    """

    def setUp(self):
        self.base_env = {
            'ENVIRONMENT': 'test',
            'DEBUG': 'False'
        }

    def test_1_loopback_ipv4_accepted(self):
        """Rule 1: 127.0.0.1 accepted."""
        is_ok, msg = evaluate_staging_target(
            environment_vars=self.base_env,
            active_db_name='natya_test_db',
            active_db_host='127.0.0.1'
        )
        self.assertTrue(is_ok, f"Expected 127.0.0.1 to be accepted, got: {msg}")
        self.assertIn("target checks PASSED", msg)

    def test_2_localhost_accepted_after_resolution(self):
        """Rule 2: localhost accepted after resolution."""
        is_ok, msg = evaluate_staging_target(
            environment_vars=self.base_env,
            active_db_name='natya_test_db',
            active_db_host='localhost'
        )
        self.assertTrue(is_ok, f"Expected localhost to be accepted, got: {msg}")
        self.assertIn("target checks PASSED", msg)

    def test_3_loopback_ipv6_accepted(self):
        """Rule 3: ::1 accepted."""
        is_ok, msg = evaluate_staging_target(
            environment_vars=self.base_env,
            active_db_name='natya_test_db',
            active_db_host='::1'
        )
        self.assertTrue(is_ok, f"Expected ::1 to be accepted, got: {msg}")
        self.assertIn("target checks PASSED", msg)

    def test_4_aws_rds_hostname_rejected(self):
        """Rule 4: AWS RDS hostname rejected."""
        rds_hosts = [
            'natya-prod.c12345678901.ap-south-1.rds.amazonaws.com',
            'natya-staging.amazonaws.com'
        ]
        for host in rds_hosts:
            is_ok, msg = evaluate_staging_target(
                environment_vars=self.base_env,
                active_db_name='natya_test_db',
                active_db_host=host
            )
            self.assertFalse(is_ok, f"Expected AWS RDS host '{host}' to be rejected")
            self.assertIn("Remote AWS RDS database host", msg)

    def test_5_remote_ip_rejected(self):
        """Rule 5: Remote IP, link-local IPv6, and non-loopback addresses rejected."""
        non_loopback_ips = [
            'fe80::1',  # IPv6 link-local
            'fe80::200:5aee:feaa:20a2',  # IPv6 link-local
            '169.254.1.1',  # IPv4 link-local
            '192.168.1.100',  # LAN
            '10.0.0.5',  # LAN
            '54.240.196.1'  # Public WAN
        ]
        for ip in non_loopback_ips:
            is_ok, msg = evaluate_staging_target(
                environment_vars=self.base_env,
                active_db_name='natya_test_db',
                active_db_host=ip
            )
            self.assertFalse(is_ok, f"Expected non-loopback IP '{ip}' to be rejected")
            self.assertIn("non-local IP", msg)

    def test_6_production_database_name_rejected(self):
        """Rule 6: Production database name rejected."""
        prod_names = ['natyalms', 'natyalms_prod', 'production', 'my_prod_db']
        for name in prod_names:
            is_ok, msg = evaluate_staging_target(
                environment_vars=self.base_env,
                active_db_name=name,
                active_db_host='127.0.0.1'
            )
            self.assertFalse(is_ok, f"Expected production DB name '{name}' to be rejected")
            self.assertIn("matches production naming", msg)

    def test_7_staging_database_name_rejected(self):
        """Rule 7: Staging database name rejected."""
        staging_names = ['natyalms_staging_db', 'staging', 'natya_staging_test']
        for name in staging_names:
            is_ok, msg = evaluate_staging_target(
                environment_vars=self.base_env,
                active_db_name=name,
                active_db_host='127.0.0.1'
            )
            self.assertFalse(is_ok, f"Expected staging DB name '{name}' to be rejected")
            self.assertIn("matches staging naming", msg)

    def test_8_missing_host_rejected(self):
        """Rule 8: Missing host rejected."""
        for empty_host in [None, '', '   ']:
            is_ok, msg = evaluate_staging_target(
                environment_vars=self.base_env,
                active_db_name='natya_test_db',
                active_db_host=empty_host
            )
            self.assertFalse(is_ok, "Expected empty/missing host to be rejected")
            self.assertIn("Database host is missing or empty", msg)

    def test_9_environment_variable_mismatch_rejected(self):
        """Rule 9: Environment variable mismatch rejected."""
        # Host mismatch between DATABASE_URL and active_db_host
        env_with_mismatch = {
            'ENVIRONMENT': 'test',
            'DATABASE_URL': 'postgresql://postgres@192.168.1.50:5432/natya_test_db'
        }
        is_ok, msg = evaluate_staging_target(
            environment_vars=env_with_mismatch,
            active_db_name='natya_test_db',
            active_db_host='127.0.0.1'
        )
        self.assertFalse(is_ok, "Expected DATABASE_URL host mismatch to be rejected")
        self.assertIn("does not match active database host", msg)

        # DB name mismatch between DATABASE_URL and active_db_name
        env_with_db_mismatch = {
            'ENVIRONMENT': 'test',
            'DATABASE_URL': 'postgresql://postgres@127.0.0.1:5432/other_db'
        }
        is_ok, msg = evaluate_staging_target(
            environment_vars=env_with_db_mismatch,
            active_db_name='natya_test_db',
            active_db_host='127.0.0.1'
        )
        self.assertFalse(is_ok, "Expected DATABASE_URL database mismatch to be rejected")
        self.assertIn("does not match active database name", msg)


if __name__ == '__main__':
    unittest.main()
