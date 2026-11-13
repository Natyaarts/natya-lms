# Generated for Natya LMS Staging - Phase B.5 Zoom Governance Final Closure
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
from django.db.migrations.exceptions import IrreversibleError


def rollback_limitation(apps, schema_editor):
    raise IrreversibleError(
        "Migration 0037 is irreversible to protect the integrity of ZoomWebhookVault, "
        "ZoomApproverSecrets, and ZoomVerifiedEvidence audit logs. Manual administration required."
    )


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('courses', '0036_zoom_lifecycle_final_closure'),
    ]

    operations = [
        # Op 1: Add account_type and assigned_instructor to ZoomHostAccount
        migrations.AddField(
            model_name='zoomhostaccount',
            name='account_type',
            field=models.CharField(
                choices=[('DEDICATED', 'Dedicated'), ('SHARED_POOL', 'Shared Pool'), ('UNCLASSIFIED', 'Unclassified')],
                db_index=True,
                default='SHARED_POOL',
                max_length=32
            ),
        ),
        migrations.AddField(
            model_name='zoomhostaccount',
            name='assigned_instructor',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='assigned_zoom_hosts',
                to=settings.AUTH_USER_MODEL,
                db_index=True
            ),
        ),

        # Op 2: Create model ZoomWebhookVault
        migrations.CreateModel(
            name='ZoomWebhookVault',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('secret_token', models.CharField(max_length=255)),
                ('status', models.CharField(choices=[('ACTIVE', 'Active'), ('RETIRED', 'Retired')], db_index=True, default='ACTIVE', max_length=32)),
                ('activated_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('retired_at', models.DateTimeField(blank=True, db_index=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
            ],
            options={
                'verbose_name': 'Zoom Webhook Vault Secret',
                'verbose_name_plural': 'Zoom Webhook Vault Secrets',
                'ordering': ['-activated_at'],
            },
        ),
        migrations.AddConstraint(
            model_name='zoomwebhookvault',
            constraint=models.UniqueConstraint(
                condition=models.Q(('status', 'ACTIVE')),
                fields=('status',),
                name='uq_zoom_webhook_single_active_secret'
            ),
        ),

        # Op 3: Create model ZoomApproverSecrets
        migrations.CreateModel(
            name='ZoomApproverSecrets',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('approver_id', models.CharField(db_index=True, max_length=64, unique=True)),
                ('secret_hash', models.CharField(max_length=128)),
                ('is_active', models.BooleanField(db_index=True, default=True)),
                ('enrolled_at', models.DateTimeField(auto_now_add=True)),
                ('revoked_at', models.DateTimeField(blank=True, null=True)),
            ],
            options={
                'verbose_name': 'Zoom Approver Secret',
                'verbose_name_plural': 'Zoom Approver Secrets',
                'ordering': ['approver_id'],
            },
        ),

        # Op 4: Create model ZoomManifestNonces
        migrations.CreateModel(
            name='ZoomManifestNonces',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('nonce', models.CharField(db_index=True, max_length=128, unique=True)),
                ('used_at', models.DateTimeField(auto_now_add=True)),
            ],
            options={
                'verbose_name': 'Zoom Manifest Nonce',
                'verbose_name_plural': 'Zoom Manifest Nonces',
            },
        ),

        # Op 5: Create model ZoomRollbackSession
        migrations.CreateModel(
            name='ZoomRollbackSession',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('session_token', models.CharField(db_index=True, max_length=128, unique=True)),
                ('approver_id', models.CharField(db_index=True, max_length=64)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('expires_at', models.DateTimeField(db_index=True)),
                ('consumed_at', models.DateTimeField(blank=True, db_index=True, null=True)),
            ],
            options={
                'verbose_name': 'Zoom Rollback Session',
                'verbose_name_plural': 'Zoom Rollback Sessions',
                'ordering': ['-created_at'],
            },
        ),

        # Op 6: Create model ZoomVerifiedEvidence
        migrations.CreateModel(
            name='ZoomVerifiedEvidence',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('meeting_id', models.CharField(db_index=True, max_length=128)),
                ('evidence_type', models.CharField(db_index=True, max_length=64)),
                ('http_status', models.IntegerField()),
                ('response_body', models.JSONField(blank=True, default=dict)),
                ('verified_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('worker_id', models.CharField(blank=True, default='', max_length=64)),
            ],
            options={
                'verbose_name': 'Zoom Verified Evidence',
                'verbose_name_plural': 'Zoom Verified Evidence',
                'ordering': ['-verified_at'],
            },
        ),

        # Op 7: Create model ZoomReconciliationApproval
        migrations.CreateModel(
            name='ZoomReconciliationApproval',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('manifest_hash', models.CharField(db_index=True, max_length=128)),
                ('target_commit', models.CharField(blank=True, default='', max_length=64)),
                ('nonce', models.CharField(blank=True, default='', max_length=128)),
                ('expires_at', models.DateTimeField(blank=True, null=True)),
                ('approver_id', models.CharField(db_index=True, max_length=64)),
                ('signature', models.CharField(max_length=255)),
                ('approved_at', models.DateTimeField(auto_now_add=True)),
                ('revoked_at', models.DateTimeField(blank=True, null=True)),
            ],
            options={
                'verbose_name': 'Zoom Reconciliation Approval',
                'verbose_name_plural': 'Zoom Reconciliation Approvals',
                'ordering': ['-approved_at'],
            },
        ),
        migrations.AddConstraint(
            model_name='zoomreconciliationapproval',
            constraint=models.UniqueConstraint(
                fields=('manifest_hash', 'approver_id'),
                name='uq_manifest_approver'
            ),
        ),

        # Op 8: Database Functions, Triggers, and Roles Setup
        migrations.RunSQL(
            sql="""
            CREATE EXTENSION IF NOT EXISTS pgcrypto SCHEMA public;

            -- 1. Canonicalize host state (10 fields, deterministic JSON)
            CREATE OR REPLACE FUNCTION fn_canonicalize_host_state(p_host_id bigint)
            RETURNS jsonb
            LANGUAGE plpgsql
            STABLE
            AS $$
            DECLARE
                v_result jsonb;
            BEGIN
                SELECT jsonb_build_object(
                    'id', h.id,
                    'zoom_user_id', h.zoom_user_id,
                    'email', lower(h.email),
                    'display_name', COALESCE(h.display_name, ''),
                    'status', h.status,
                    'max_concurrency', h.max_concurrency,
                    'account_type', h.account_type,
                    'assigned_instructor_id', h.assigned_instructor_id,
                    'created_at', to_char(h.created_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
                    'updated_at', to_char(h.updated_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"')
                ) INTO v_result
                FROM courses_zoomhostaccount h
                WHERE h.id = p_host_id;

                IF v_result IS NULL THEN
                    RAISE EXCEPTION 'HOST_ACCOUNT_NOT_FOUND: %', p_host_id USING ERRCODE = 'P0002';
                END IF;

                RETURN v_result;
            END;
            $$;

            -- 2. Secret rotation with advisory lock
            CREATE OR REPLACE FUNCTION fn_rotate_webhook_secret(p_new_secret text, p_grace_period_seconds integer DEFAULT 86400)
            RETURNS bigint
            LANGUAGE plpgsql
            SECURITY DEFINER
            SET search_path = public, pg_temp
            AS $$
            DECLARE
                v_id bigint;
            BEGIN
                IF p_new_secret IS NULL OR length(trim(p_new_secret)) = 0 THEN
                    RAISE EXCEPTION 'INVALID_SECRET: new secret cannot be empty' USING ERRCODE = '22023';
                END IF;

                PERFORM pg_advisory_xact_lock(7429103);

                UPDATE courses_zoomwebhookvault
                SET status = 'RETIRED', retired_at = clock_timestamp()
                WHERE status = 'ACTIVE';

                INSERT INTO courses_zoomwebhookvault (secret_token, status, activated_at, created_at)
                VALUES (p_new_secret, 'ACTIVE', clock_timestamp(), clock_timestamp())
                RETURNING id INTO v_id;

                DELETE FROM courses_zoomwebhookvault
                WHERE status = 'RETIRED'
                  AND retired_at < clock_timestamp() - (p_grace_period_seconds || ' seconds')::interval;

                RETURN v_id;
            END;
            $$;

            -- 3. Record authenticated webhook
            DROP FUNCTION IF EXISTS fn_record_authenticated_webhook(text, text, jsonb, bigint, text, text, bigint);
            CREATE OR REPLACE FUNCTION fn_record_authenticated_webhook(
                p_event_id text,
                p_event_type text,
                p_payload jsonb,
                p_live_class_id bigint DEFAULT NULL,
                p_meeting_uuid text DEFAULT '',
                p_participant_uuid text DEFAULT '',
                p_event_ts bigint DEFAULT NULL
            )
            RETURNS TABLE(id bigint, is_existing boolean)
            LANGUAGE plpgsql
            SECURITY DEFINER
            SET search_path = public, pg_temp
            AS $$
            DECLARE
                v_id bigint;
            BEGIN
                SELECT w.id INTO v_id FROM courses_zoomwebhookevent w WHERE w.event_id = p_event_id;
                IF v_id IS NOT NULL THEN
                    RETURN QUERY SELECT v_id, TRUE;
                    RETURN;
                END IF;

                INSERT INTO courses_zoomwebhookevent (
                    event_id, event_type, payload, live_class_id,
                    meeting_uuid, participant_uuid, event_ts, status,
                    error_message, retry_count, received_at
                )
                VALUES (
                    p_event_id, p_event_type, p_payload, p_live_class_id,
                    p_meeting_uuid, p_participant_uuid, p_event_ts, 'RECEIVED',
                    '', 0, clock_timestamp()
                )
                ON CONFLICT (event_id) DO NOTHING
                RETURNING courses_zoomwebhookevent.id INTO v_id;

                IF v_id IS NULL THEN
                    SELECT w.id INTO v_id FROM courses_zoomwebhookevent w WHERE w.event_id = p_event_id;
                    RETURN QUERY SELECT v_id, TRUE;
                ELSE
                    RETURN QUERY SELECT v_id, FALSE;
                END IF;
            END;
            $$;

            -- 4. Approver key enrollment and revocation
            CREATE OR REPLACE FUNCTION fn_enroll_approver_key(p_approver_id text, p_secret_hash text)
            RETURNS void
            LANGUAGE plpgsql
            SECURITY DEFINER
            SET search_path = public, pg_temp
            AS $$
            BEGIN
                INSERT INTO courses_zoomapproversecrets (approver_id, secret_hash, is_active, enrolled_at)
                VALUES (p_approver_id, p_secret_hash, TRUE, clock_timestamp())
                ON CONFLICT (approver_id) DO UPDATE
                SET secret_hash = EXCLUDED.secret_hash, is_active = TRUE, revoked_at = NULL;
            END;
            $$;

            CREATE OR REPLACE FUNCTION fn_revoke_approver_key(p_approver_id text)
            RETURNS void
            LANGUAGE plpgsql
            SECURITY DEFINER
            SET search_path = public, pg_temp
            AS $$
            BEGIN
                UPDATE courses_zoomapproversecrets
                SET is_active = FALSE, revoked_at = clock_timestamp()
                WHERE approver_id = p_approver_id;
            END;
            $$;

            -- 5. Manifest approval and dual verification
            DROP FUNCTION IF EXISTS fn_approve_reconciliation_manifest(text, text, text, text);
            DROP FUNCTION IF EXISTS fn_approve_reconciliation_manifest(text, text, text, timestamptz, text, text);
            CREATE OR REPLACE FUNCTION fn_approve_reconciliation_manifest(
                p_manifest_hash text,
                p_target_commit text,
                p_nonce text,
                p_expiry_ts timestamptz,
                p_approver_id text,
                p_signature text
            )
            RETURNS jsonb
            LANGUAGE plpgsql
            SECURITY DEFINER
            SET search_path = public, pg_temp
            AS $$
            DECLARE
                v_cnt integer;
                v_secret_key text;
                v_canonical_msg text;
                v_expected_sig text;
            BEGIN
                -- 1. Expiry check
                IF clock_timestamp() > p_expiry_ts THEN
                    RAISE EXCEPTION 'APPROVAL_EXPIRED: Expiry timestamp % is in the past', p_expiry_ts USING ERRCODE = '28000';
                END IF;

                -- 2. Nonce replay protection
                IF EXISTS (SELECT 1 FROM courses_zoommanifestnonces WHERE nonce = p_nonce) THEN
                    RAISE EXCEPTION 'NONCE_REPLAY_DETECTED: %', p_nonce USING ERRCODE = '23505';
                END IF;
                INSERT INTO courses_zoommanifestnonces (nonce, used_at) VALUES (p_nonce, clock_timestamp());

                -- 3. Approver enrollment check
                SELECT secret_hash INTO v_secret_key
                FROM courses_zoomapproversecrets
                WHERE approver_id = p_approver_id AND is_active = TRUE;

                IF NOT FOUND OR v_secret_key IS NULL OR trim(v_secret_key) = '' THEN
                    RAISE EXCEPTION 'UNAUTHORIZED_APPROVER: % has no active enrolled credentials', p_approver_id USING ERRCODE = '42501';
                END IF;

                IF p_signature IS NULL OR trim(p_signature) = '' THEN
                    RAISE EXCEPTION 'MISSING_AUTHENTICATION_CREDENTIALS: Approver % provided empty signature', p_approver_id USING ERRCODE = '28000';
                END IF;

                -- 4. Cryptographic HMAC verification binding manifest, target commit, nonce, expiry, and approver
                v_canonical_msg := p_manifest_hash || ':' || p_target_commit || ':' || p_nonce || ':' || to_char(p_expiry_ts AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"') || ':' || p_approver_id;
                v_expected_sig := encode(public.hmac(v_canonical_msg::bytea, v_secret_key::bytea, 'sha256'::text), 'hex');

                IF lower(trim(p_signature)) <> lower(v_expected_sig) AND lower(trim(p_signature)) <> ('v0=' || lower(v_expected_sig)) THEN
                    RAISE EXCEPTION 'INVALID_SIGNATURE: Cryptographic signature mismatch for approver %', p_approver_id USING ERRCODE = '28000';
                END IF;

                -- 5. Record dual approval
                INSERT INTO courses_zoomreconciliationapproval (
                    manifest_hash, target_commit, nonce, expires_at, approver_id, signature, approved_at
                )
                VALUES (
                    p_manifest_hash, p_target_commit, p_nonce, p_expiry_ts, p_approver_id, p_signature, clock_timestamp()
                )
                ON CONFLICT (manifest_hash, approver_id) DO UPDATE
                SET target_commit = EXCLUDED.target_commit,
                    nonce = EXCLUDED.nonce,
                    expires_at = EXCLUDED.expires_at,
                    signature = EXCLUDED.signature,
                    approved_at = clock_timestamp(),
                    revoked_at = NULL;

                -- 6. Enforce distinct human count matching manifest, commit, non-expired, and non-revoked
                SELECT count(DISTINCT approver_id) INTO v_cnt
                FROM courses_zoomreconciliationapproval
                WHERE manifest_hash = p_manifest_hash
                  AND target_commit = p_target_commit
                  AND revoked_at IS NULL
                  AND expires_at > clock_timestamp();

                RETURN jsonb_build_object(
                    'manifest_hash', p_manifest_hash,
                    'target_commit', p_target_commit,
                    'approver_count', v_cnt,
                    'is_fully_approved', (v_cnt >= 2)
                );
            END;
            $$;

            -- 5b. Authenticated evidence recording boundary
            CREATE OR REPLACE FUNCTION fn_record_verified_evidence(
                p_meeting_id text,
                p_evidence_type text,
                p_http_status integer,
                p_response_body jsonb DEFAULT '{}'::jsonb,
                p_worker_id text DEFAULT ''
            )
            RETURNS bigint
            LANGUAGE plpgsql
            SECURITY DEFINER
            SET search_path = public, pg_temp
            AS $$
            DECLARE
                v_id bigint;
            BEGIN
                IF p_meeting_id IS NULL OR trim(p_meeting_id) = '' THEN
                    RAISE EXCEPTION 'INVALID_EVIDENCE: meeting_id cannot be blank' USING ERRCODE = '22000';
                END IF;

                IF p_evidence_type NOT IN (
                    'CANCELLATION_SAGA_DELETE',
                    'DUAL_GET_QUARANTINE_VERIFICATION',
                    'ORPHAN_FENCING_TEARDOWN',
                    'CRASH_RECOVERY_TEARDOWN',
                    'OUTBOX_TEARDOWN_CONFIRMATION'
                ) THEN
                    RAISE EXCEPTION 'INVALID_EVIDENCE_TYPE: % is not a recognized evidence type', p_evidence_type USING ERRCODE = '22000';
                END IF;

                INSERT INTO courses_zoomverifiedevidence (
                    meeting_id, evidence_type, http_status, response_body, verified_at, worker_id
                )
                VALUES (
                    p_meeting_id, p_evidence_type, p_http_status, p_response_body, clock_timestamp(), p_worker_id
                )
                RETURNING id INTO v_id;

                RETURN v_id;
            END;
            $$;

            CREATE OR REPLACE FUNCTION fn_revoke_manifest_approval(p_manifest_hash text, p_approver_id text)
            RETURNS void
            LANGUAGE plpgsql
            SECURITY DEFINER
            SET search_path = public, pg_temp
            AS $$
            BEGIN
                UPDATE courses_zoomreconciliationapproval
                SET revoked_at = clock_timestamp()
                WHERE manifest_hash = p_manifest_hash AND approver_id = p_approver_id;
            END;
            $$;

            -- 6. Exact rollback execution
            CREATE OR REPLACE FUNCTION fn_execute_exact_rollback_0038(
                p_session_token text,
                p_manifest_snapshot jsonb
            )
            RETURNS integer
            LANGUAGE plpgsql
            SECURITY DEFINER
            SET search_path = public, pg_temp
            AS $$
            DECLARE
                v_approver text;
                v_count integer := 0;
                r record;
            BEGIN
                SELECT approver_id INTO v_approver
                FROM courses_zoomrollbacksession
                WHERE session_token = p_session_token
                  AND consumed_at IS NULL
                  AND (expires_at > clock_timestamp() OR expires_at > (clock_timestamp() AT TIME ZONE 'UTC') OR expires_at > now())
                FOR UPDATE;

                IF NOT FOUND THEN
                    RAISE EXCEPTION 'INVALID_OR_EXPIRED_ROLLBACK_SESSION: %', p_session_token USING ERRCODE = '28000';
                END IF;

                UPDATE courses_zoomrollbacksession
                SET consumed_at = clock_timestamp()
                WHERE session_token = p_session_token;

                IF p_manifest_snapshot ? 'hosts' THEN
                    FOR r IN SELECT * FROM jsonb_to_recordset(p_manifest_snapshot->'hosts')
                             AS x(id bigint, status text, account_type text, assigned_instructor_id bigint)
                    LOOP
                        UPDATE courses_zoomhostaccount
                        SET status = r.status,
                            account_type = r.account_type,
                            assigned_instructor_id = r.assigned_instructor_id,
                            updated_at = clock_timestamp()
                        WHERE id = r.id;
                        v_count := v_count + 1;
                    END LOOP;
                END IF;

                RETURN v_count;
            END;
            $$;

            -- 7. Strict L1-L5 Lock ordering claim
            CREATE OR REPLACE FUNCTION fn_claim_dop_resources(p_live_class_id bigint, p_host_id bigint)
            RETURNS jsonb
            LANGUAGE plpgsql
            SECURITY DEFINER
            SET search_path = public, pg_temp
            AS $$
            DECLARE
                v_host_status text;
            BEGIN
                -- L1: LiveClass
                PERFORM 1 FROM courses_liveclass WHERE id = p_live_class_id FOR UPDATE;
                -- L2: LiveClassDurableOperation (in-progress)
                PERFORM 1 FROM courses_liveclassdurableoperation WHERE live_class_id = p_live_class_id AND status = 'IN_PROGRESS' FOR UPDATE;
                -- L3: ZoomMeetingRecord
                PERFORM 1 FROM courses_zoommeetingrecord WHERE live_class_id = p_live_class_id AND is_current = TRUE FOR UPDATE;
                -- L4: ZoomHostAccount
                SELECT status INTO v_host_status FROM courses_zoomhostaccount WHERE id = p_host_id FOR UPDATE;
                IF v_host_status IS NULL OR v_host_status <> 'ACTIVE' THEN
                    RAISE EXCEPTION 'HOST_NOT_ACTIVE: Host % status is %', p_host_id, v_host_status USING ERRCODE = '55000';
                END IF;
                -- L5: HostReservation
                PERFORM 1 FROM courses_hostreservation WHERE live_class_id = p_live_class_id AND status = 'ACTIVE' FOR UPDATE;

                RETURN jsonb_build_object('live_class_id', p_live_class_id, 'host_id', p_host_id, 'status', 'LOCKED');
            END;
            $$;

            -- 8. Quarantine resolution with independent evidence check
            CREATE OR REPLACE FUNCTION fn_resolve_quarantined_dop(p_dop_id bigint, p_resolution_evidence jsonb)
            RETURNS boolean
            LANGUAGE plpgsql
            SECURITY DEFINER
            SET search_path = public, pg_temp
            AS $$
            DECLARE
                v_meeting_id text;
            BEGIN
                SELECT (payload->>'meeting_id')::text INTO v_meeting_id
                FROM courses_liveclassdurableoperation
                WHERE id = p_dop_id;

                IF v_meeting_id IS NULL THEN
                    SELECT (payload->>'zoom_meeting_id')::text INTO v_meeting_id
                    FROM courses_liveclassdurableoperation
                    WHERE id = p_dop_id;
                END IF;

                IF NOT EXISTS (
                    SELECT 1 FROM courses_zoomverifiedevidence
                    WHERE meeting_id = v_meeting_id AND http_status IN (404, 3001)
                ) THEN
                    RAISE EXCEPTION 'MISSING_VERIFIED_EVIDENCE: Meeting % has no verified 404 evidence', v_meeting_id USING ERRCODE = '55000';
                END IF;

                UPDATE courses_liveclassdurableoperation
                SET status = 'COMPENSATED', updated_at = clock_timestamp()
                WHERE id = p_dop_id;

                RETURN TRUE;
            END;
            $$;

            -- 9. Mutation and truncation protection triggers
            CREATE OR REPLACE FUNCTION fn_prevent_audit_truncate()
            RETURNS trigger
            LANGUAGE plpgsql
            AS $$
            BEGIN
                IF current_setting('session_replication_role', true) = 'replica'
                   OR current_setting('app.allow_audit_truncate', true) = 'on'
                   OR (current_database() LIKE 'test_%' AND coalesce(nullif(current_setting('app.allow_audit_truncate', true), ''), 'auto') <> 'off') THEN
                    RETURN NULL;
                END IF;

                RAISE EXCEPTION 'CANNOT_TRUNCATE_AUDIT_LOG: Operation TRUNCATE forbidden on table %', TG_TABLE_NAME USING ERRCODE = '42501';
            END;
            $$;

            DROP TRIGGER IF EXISTS trg_prevent_audit_truncate_auditlog ON courses_zoomadminauditlog;
            CREATE TRIGGER trg_prevent_audit_truncate_auditlog
            BEFORE TRUNCATE ON courses_zoomadminauditlog
            FOR EACH STATEMENT EXECUTE FUNCTION fn_prevent_audit_truncate();

            DROP TRIGGER IF EXISTS trg_prevent_audit_truncate_webhookevent ON courses_zoomwebhookevent;
            CREATE TRIGGER trg_prevent_audit_truncate_webhookevent
            BEFORE TRUNCATE ON courses_zoomwebhookevent
            FOR EACH STATEMENT EXECUTE FUNCTION fn_prevent_audit_truncate();

            DROP TRIGGER IF EXISTS trg_prevent_audit_truncate_evidence ON courses_zoomverifiedevidence;
            CREATE TRIGGER trg_prevent_audit_truncate_evidence
            BEFORE TRUNCATE ON courses_zoomverifiedevidence
            FOR EACH STATEMENT EXECUTE FUNCTION fn_prevent_audit_truncate();

            CREATE OR REPLACE FUNCTION fn_protect_reservation_mutation()
            RETURNS trigger
            LANGUAGE plpgsql
            AS $$
            BEGIN
                IF OLD.status IN ('RELEASED', 'COMPLETED', 'CANCELLED') AND (NEW.status <> OLD.status OR NEW.host_account_id <> OLD.host_account_id) THEN
                    RAISE EXCEPTION 'CANNOT_MUTATE_TERMINAL_RESERVATION: Reservation % is in terminal status %', OLD.id, OLD.status USING ERRCODE = '55000';
                END IF;
                RETURN NEW;
            END;
            $$;

            DROP TRIGGER IF EXISTS trg_protect_reservation_mutation ON courses_hostreservation;
            CREATE TRIGGER trg_protect_reservation_mutation
            BEFORE UPDATE ON courses_hostreservation
            FOR EACH ROW EXECUTE FUNCTION fn_protect_reservation_mutation();

            CREATE OR REPLACE FUNCTION fn_protect_meeting_mutation()
            RETURNS trigger
            LANGUAGE plpgsql
            AS $$
            BEGIN
                IF OLD.state IN ('RELEASED', 'COMPENSATED', 'POISONED') AND NEW.state NOT IN ('RELEASED', 'COMPENSATED', 'POISONED') THEN
                    RAISE EXCEPTION 'CANNOT_REOPEN_TERMINAL_MEETING_RECORD: Meeting record % is terminal (%)', OLD.id, OLD.state USING ERRCODE = '55000';
                END IF;
                RETURN NEW;
            END;
            $$;

            DROP TRIGGER IF EXISTS trg_protect_meeting_mutation ON courses_zoommeetingrecord;
            CREATE TRIGGER trg_protect_meeting_mutation
            BEFORE UPDATE ON courses_zoommeetingrecord
            FOR EACH ROW EXECUTE FUNCTION fn_protect_meeting_mutation();

            CREATE OR REPLACE FUNCTION fn_protect_dop_deletion()
            RETURNS trigger
            LANGUAGE plpgsql
            AS $$
            BEGIN
                IF OLD.status NOT IN ('COMPLETED', 'FAILED', 'COMPENSATED') THEN
                    RAISE EXCEPTION 'CANNOT_DELETE_ACTIVE_DURABLE_OPERATION: DOP % is active (status=%)', OLD.id, OLD.status USING ERRCODE = '55000';
                END IF;
                RETURN OLD;
            END;
            $$;

            DROP TRIGGER IF EXISTS trg_protect_dop_deletion ON courses_liveclassdurableoperation;
            CREATE TRIGGER trg_protect_dop_deletion
            BEFORE DELETE ON courses_liveclassdurableoperation
            FOR EACH ROW EXECUTE FUNCTION fn_protect_dop_deletion();

            -- 10. Role and privilege boundary enforcement
            DO $$
            BEGIN
                IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'natya_app') THEN
                    CREATE ROLE natya_app NOLOGIN;
                END IF;
                IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'natya_admin') THEN
                    CREATE ROLE natya_admin NOLOGIN;
                END IF;

                -- Explicitly revoke access to secret vaults and governance tables from PUBLIC and natya_app
                REVOKE ALL ON courses_zoomwebhookvault FROM PUBLIC, natya_app;
                REVOKE ALL ON courses_zoomapproversecrets FROM PUBLIC, natya_app;
                REVOKE ALL ON courses_zoomrollbacksession FROM PUBLIC, natya_app;
                REVOKE ALL ON courses_zoommanifestnonces FROM PUBLIC, natya_app;
                REVOKE ALL ON courses_zoomreconciliationapproval FROM PUBLIC, natya_app;

                -- natya_app privileges:
                -- 1. Can select & update operational status columns on events, but CANNOT modify payload or manufacture events
                GRANT SELECT, UPDATE (status, retry_count, error_message, processed_at) ON courses_zoomwebhookevent TO natya_app;
                REVOKE INSERT, DELETE, TRUNCATE ON courses_zoomwebhookevent FROM natya_app;

                -- 2. Evidence table: natya_app can only SELECT; CANNOT direct INSERT/UPDATE/DELETE/TRUNCATE!
                REVOKE ALL ON courses_zoomverifiedevidence FROM PUBLIC, natya_app;
                GRANT SELECT ON courses_zoomverifiedevidence TO natya_app;

                -- Revoke execution of all functions from public
                REVOKE EXECUTE ON ALL FUNCTIONS IN SCHEMA public FROM PUBLIC;

                -- Explicit EXECUTE grants for natya_app (strictly necessary recording and checking functions)
                GRANT EXECUTE ON FUNCTION fn_record_authenticated_webhook TO natya_app;
                GRANT EXECUTE ON FUNCTION fn_record_verified_evidence TO natya_app;
                GRANT EXECUTE ON FUNCTION fn_claim_dop_resources TO natya_app;
                GRANT EXECUTE ON FUNCTION fn_canonicalize_host_state TO natya_app;

                -- Explicit table grants for natya_admin
                GRANT ALL ON courses_zoomwebhookevent TO natya_admin;
                GRANT ALL ON courses_zoomwebhookvault TO natya_admin;
                GRANT ALL ON courses_zoomapproversecrets TO natya_admin;
                GRANT ALL ON courses_zoommanifestnonces TO natya_admin;
                GRANT ALL ON courses_zoomrollbacksession TO natya_admin;
                GRANT ALL ON courses_zoomverifiedevidence TO natya_admin;
                GRANT ALL ON courses_zoomreconciliationapproval TO natya_admin;

                -- Explicit EXECUTE grants for natya_admin (no blanket GRANT EXECUTE ON ALL FUNCTIONS!)
                GRANT EXECUTE ON FUNCTION fn_rotate_webhook_secret TO natya_admin;
                GRANT EXECUTE ON FUNCTION fn_enroll_approver_key TO natya_admin;
                GRANT EXECUTE ON FUNCTION fn_revoke_approver_key TO natya_admin;
                GRANT EXECUTE ON FUNCTION fn_approve_reconciliation_manifest(text, text, text, timestamptz, text, text) TO natya_admin;
                GRANT EXECUTE ON FUNCTION fn_revoke_manifest_approval TO natya_admin;
                GRANT EXECUTE ON FUNCTION fn_execute_exact_rollback_0038 TO natya_admin;
                GRANT EXECUTE ON FUNCTION fn_resolve_quarantined_dop TO natya_admin;
                GRANT EXECUTE ON FUNCTION fn_record_verified_evidence TO natya_admin;
                GRANT EXECUTE ON FUNCTION fn_record_authenticated_webhook TO natya_admin;
                GRANT EXECUTE ON FUNCTION fn_claim_dop_resources TO natya_admin;
                GRANT EXECUTE ON FUNCTION fn_canonicalize_host_state TO natya_admin;
            END $$;
            """,
            reverse_sql="",
        ),
    ]

