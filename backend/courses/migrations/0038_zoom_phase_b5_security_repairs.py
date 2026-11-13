# Natya LMS Staging - Phase B.5 critical defect repairs (audit blockers B1-B3).
#
# B1  Approver identity is the authenticated PostgreSQL session user (a LOGIN role
#     that is a member of natya_approver), never a caller-supplied id and never a
#     shared secret stored in the database. Approvals are immutable (only a one-way
#     revocation is allowed; no replacement, reactivation or deletion). Shared-key
#     enrollment is disabled and the stored keys are scrubbed.
#     Rollback execution is DISABLED: independent dual-human authentication cannot
#     be guaranteed against the table owner / RDS master credential holder in this
#     architecture, so no approval may authorize it.
# B2  fn_record_verified_evidence is owner-only (revoked from PUBLIC, natya_app,
#     natya_admin), only accepts confirmation statuses consistent with the evidence
#     type, binds evidence to an existing DOP, and evidence rows are immutable.
# B3  fn_resolve_quarantined_dop requires a quarantined DOP, an expired grace
#     period measured from quarantined_at, no active lease, and two fresh 404
#     probes bound to that DOP and gathered after the grace period.
from django.db import migrations, models


FORWARD_SQL = r"""
-- ---------------------------------------------------------------- B1 -----
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'natya_approver') THEN
        CREATE ROLE natya_approver NOLOGIN;
    END IF;
END $$;

CREATE OR REPLACE FUNCTION fn_session_approver_identity()
RETURNS text
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $$
DECLARE
    v_user name := session_user;
    v_super boolean;
    v_createrole boolean;
    v_bypassrls boolean;
    v_owner name;
BEGIN
    SELECT r.rolsuper, r.rolcreaterole, r.rolbypassrls
      INTO v_super, v_createrole, v_bypassrls
      FROM pg_roles r WHERE r.rolname = v_user;
    IF v_super OR v_createrole OR v_bypassrls THEN
        RAISE EXCEPTION 'APPROVER_PRIVILEGED_SESSION: % holds SUPERUSER/CREATEROLE/BYPASSRLS and cannot act as an independent approver', v_user
            USING ERRCODE = '42501';
    END IF;
    IF NOT pg_has_role(v_user, 'natya_approver', 'MEMBER') THEN
        RAISE EXCEPTION 'UNAUTHORIZED_APPROVER: session user % is not a member of natya_approver', v_user
            USING ERRCODE = '42501';
    END IF;
    IF pg_has_role(v_user, 'natya_app', 'MEMBER') OR pg_has_role(v_user, 'natya_admin', 'MEMBER') THEN
        RAISE EXCEPTION 'APPROVER_ROLE_CONFLICT: % is also a member of natya_app/natya_admin', v_user
            USING ERRCODE = '42501';
    END IF;
    SELECT pg_get_userbyid(c.relowner) INTO v_owner
      FROM pg_class c WHERE c.oid = 'public.courses_zoomreconciliationapproval'::regclass;
    IF v_user = v_owner OR pg_has_role(v_user, v_owner, 'MEMBER') THEN
        RAISE EXCEPTION 'APPROVER_IS_LEDGER_OWNER: % owns (or is a member of the owner of) the approval ledger', v_user
            USING ERRCODE = '42501';
    END IF;
    RETURN v_user::text;
END;
$$;

CREATE OR REPLACE FUNCTION fn_guard_reconciliation_approval()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public, pg_temp
AS $$
DECLARE
    v_approver text;
BEGIN
    IF TG_OP = 'INSERT' THEN
        -- Identity is enforced on the row itself, so a direct INSERT by any role
        -- (including the table owner) is held to the same rule as the function.
        v_approver := fn_session_approver_identity();
        IF NEW.approver_id IS DISTINCT FROM v_approver THEN
            RAISE EXCEPTION 'APPROVER_IDENTITY_MISMATCH: row approver % is not the authenticated session user %', NEW.approver_id, v_approver
                USING ERRCODE = '42501';
        END IF;
        RETURN NEW;
    ELSIF TG_OP = 'UPDATE' THEN
        IF OLD.revoked_at IS NULL AND NEW.revoked_at IS NOT NULL
           AND NEW.id = OLD.id
           AND NEW.manifest_hash = OLD.manifest_hash
           AND NEW.target_commit = OLD.target_commit
           AND NEW.nonce = OLD.nonce
           AND NEW.expires_at IS NOT DISTINCT FROM OLD.expires_at
           AND NEW.approver_id = OLD.approver_id
           AND NEW.signature = OLD.signature
           AND NEW.approved_at = OLD.approved_at THEN
            RETURN NEW;  -- one-way revocation is the only permitted change
        END IF;
        RAISE EXCEPTION 'APPROVAL_IMMUTABLE: approval % cannot be modified or reactivated', OLD.id
            USING ERRCODE = '55000';
    END IF;
    RAISE EXCEPTION 'APPROVAL_IMMUTABLE: approval % cannot be deleted', OLD.id
        USING ERRCODE = '55000';
END;
$$;

DROP TRIGGER IF EXISTS trg_guard_reconciliation_approval ON courses_zoomreconciliationapproval;
CREATE TRIGGER trg_guard_reconciliation_approval
BEFORE INSERT OR UPDATE OR DELETE ON courses_zoomreconciliationapproval
FOR EACH ROW EXECUTE FUNCTION fn_guard_reconciliation_approval();

DROP TRIGGER IF EXISTS trg_prevent_audit_truncate_approval ON courses_zoomreconciliationapproval;
CREATE TRIGGER trg_prevent_audit_truncate_approval
BEFORE TRUNCATE ON courses_zoomreconciliationapproval
FOR EACH STATEMENT EXECUTE FUNCTION fn_prevent_audit_truncate();

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
SET search_path = pg_catalog, public, pg_temp
AS $$
DECLARE
    v_approver text;
    v_cnt integer;
BEGIN
    -- Identity = authenticated session user. p_approver_id is only an assertion
    -- that must match it; p_signature is ignored (the former HMAC used a key that
    -- was stored in this database and could be read by its owner).
    v_approver := fn_session_approver_identity();
    IF p_approver_id IS DISTINCT FROM v_approver THEN
        RAISE EXCEPTION 'APPROVER_ID_MISMATCH: caller-supplied approver id % does not match authenticated session user %', p_approver_id, v_approver
            USING ERRCODE = '42501';
    END IF;
    IF p_manifest_hash IS NULL OR p_manifest_hash !~ '^[0-9a-f]{64}$' THEN
        RAISE EXCEPTION 'INVALID_MANIFEST_HASH: expected a lowercase SHA-256 hex digest' USING ERRCODE = '22023';
    END IF;
    IF p_target_commit IS NULL OR p_target_commit !~ '^[0-9a-f]{40}$' THEN
        RAISE EXCEPTION 'INVALID_TARGET_COMMIT: expected a full 40-character git commit SHA' USING ERRCODE = '22023';
    END IF;
    IF p_nonce IS NULL OR length(p_nonce) < 16 THEN
        RAISE EXCEPTION 'INVALID_NONCE: nonce must be at least 16 characters' USING ERRCODE = '22023';
    END IF;
    IF p_expiry_ts IS NULL OR p_expiry_ts <= clock_timestamp() THEN
        RAISE EXCEPTION 'APPROVAL_EXPIRED: Expiry timestamp % is not in the future', p_expiry_ts USING ERRCODE = '28000';
    END IF;
    IF p_expiry_ts > clock_timestamp() + interval '24 hours' THEN
        RAISE EXCEPTION 'APPROVAL_WINDOW_TOO_LONG: approvals may not be valid for more than 24 hours' USING ERRCODE = '22023';
    END IF;

    BEGIN
        INSERT INTO courses_zoommanifestnonces (nonce, used_at) VALUES (p_nonce, clock_timestamp());
    EXCEPTION WHEN unique_violation THEN
        RAISE EXCEPTION 'NONCE_REPLAY_DETECTED: %', p_nonce USING ERRCODE = '23505';
    END;

    BEGIN
        INSERT INTO courses_zoomreconciliationapproval (
            manifest_hash, target_commit, nonce, expires_at, approver_id, signature, approved_at
        ) VALUES (
            p_manifest_hash, p_target_commit, p_nonce, p_expiry_ts, v_approver, 'PG_SESSION_AUTHENTICATED', clock_timestamp()
        );
    EXCEPTION WHEN unique_violation THEN
        RAISE EXCEPTION 'APPROVAL_ALREADY_RECORDED: % has already approved manifest %; approvals cannot be replaced or reactivated', v_approver, p_manifest_hash
            USING ERRCODE = '23505';
    END;

    SELECT count(DISTINCT approver_id) INTO v_cnt
      FROM courses_zoomreconciliationapproval
     WHERE manifest_hash = p_manifest_hash
       AND target_commit = p_target_commit
       AND revoked_at IS NULL
       AND expires_at > clock_timestamp();

    RETURN jsonb_build_object(
        'manifest_hash', p_manifest_hash,
        'target_commit', p_target_commit,
        'approver', v_approver,
        'approver_count', v_cnt,
        'is_fully_approved', (v_cnt >= 2),
        'authorizes_execution', false
    );
END;
$$;

CREATE OR REPLACE FUNCTION fn_revoke_manifest_approval(p_manifest_hash text, p_approver_id text)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $$
DECLARE
    v_approver text;
BEGIN
    v_approver := fn_session_approver_identity();
    IF p_approver_id IS DISTINCT FROM v_approver THEN
        RAISE EXCEPTION 'APPROVER_ID_MISMATCH: an approver can only revoke their own approval' USING ERRCODE = '42501';
    END IF;
    UPDATE courses_zoomreconciliationapproval
       SET revoked_at = clock_timestamp()
     WHERE manifest_hash = p_manifest_hash AND approver_id = v_approver AND revoked_at IS NULL;
END;
$$;

CREATE OR REPLACE FUNCTION fn_enroll_approver_key(p_approver_id text, p_secret_hash text)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $$
BEGIN
    RAISE EXCEPTION 'APPROVER_KEY_ENROLLMENT_DISABLED: approvers are PostgreSQL LOGIN roles granted natya_approver; shared keys are not stored in the database'
        USING ERRCODE = '42501';
END;
$$;

CREATE OR REPLACE FUNCTION fn_revoke_approver_key(p_approver_id text)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $$
BEGIN
    RAISE EXCEPTION 'APPROVER_KEY_ENROLLMENT_DISABLED: revoke approver access with REVOKE natya_approver FROM <role>'
        USING ERRCODE = '42501';
END;
$$;

-- Shared HMAC keys stored by 0037 are no longer used for anything: scrub them.
UPDATE courses_zoomapproversecrets
   SET secret_hash = '', is_active = FALSE, revoked_at = COALESCE(revoked_at, clock_timestamp());

CREATE OR REPLACE FUNCTION fn_execute_exact_rollback_0038(p_session_token text, p_manifest_snapshot jsonb)
RETURNS integer
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $$
BEGIN
    RAISE EXCEPTION 'ROLLBACK_EXECUTION_DISABLED: independent dual-human authorization cannot be guaranteed in this architecture; rollback must be performed manually under change control'
        USING ERRCODE = '42501';
END;
$$;

-- ---------------------------------------------------------------- B2 -----
DROP FUNCTION IF EXISTS fn_record_verified_evidence(text, text, integer, jsonb, text);

CREATE OR REPLACE FUNCTION fn_record_verified_evidence(
    p_meeting_id text,
    p_evidence_type text,
    p_http_status integer,
    p_response_body jsonb,
    p_worker_id text,
    p_dop_id bigint
)
RETURNS bigint
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $$
DECLARE
    v_id bigint;
BEGIN
    IF p_meeting_id IS NULL OR p_meeting_id !~ '^[0-9]{8,15}$' THEN
        RAISE EXCEPTION 'INVALID_EVIDENCE: meeting id % is not a Zoom numeric meeting id', p_meeting_id USING ERRCODE = '22000';
    END IF;
    IF p_evidence_type IN ('CANCELLATION_SAGA_DELETE', 'ORPHAN_FENCING_TEARDOWN',
                           'CRASH_RECOVERY_TEARDOWN', 'OUTBOX_TEARDOWN_CONFIRMATION') THEN
        IF p_http_status IS NULL OR p_http_status NOT IN (204, 404) THEN
            RAISE EXCEPTION 'EVIDENCE_NOT_A_CONFIRMATION: % requires HTTP 204 or 404, got %', p_evidence_type, p_http_status USING ERRCODE = '22000';
        END IF;
    ELSIF p_evidence_type = 'DUAL_GET_QUARANTINE_VERIFICATION' THEN
        IF p_http_status IS DISTINCT FROM 404 THEN
            RAISE EXCEPTION 'EVIDENCE_NOT_A_CONFIRMATION: % requires HTTP 404, got %', p_evidence_type, p_http_status USING ERRCODE = '22000';
        END IF;
    ELSE
        RAISE EXCEPTION 'INVALID_EVIDENCE_TYPE: % is not a recognized evidence type', p_evidence_type USING ERRCODE = '22000';
    END IF;
    IF p_evidence_type IN ('DUAL_GET_QUARANTINE_VERIFICATION', 'CRASH_RECOVERY_TEARDOWN', 'ORPHAN_FENCING_TEARDOWN') THEN
        IF p_dop_id IS NULL OR NOT EXISTS (SELECT 1 FROM courses_liveclassdurableoperation WHERE id = p_dop_id) THEN
            RAISE EXCEPTION 'EVIDENCE_DOP_REQUIRED: % evidence must be bound to an existing durable operation', p_evidence_type USING ERRCODE = '22000';
        END IF;
    END IF;

    INSERT INTO courses_zoomverifiedevidence (
        meeting_id, evidence_type, http_status, response_body, verified_at, worker_id, dop_id
    ) VALUES (
        p_meeting_id, p_evidence_type, p_http_status, COALESCE(p_response_body, '{}'::jsonb), clock_timestamp(),
        COALESCE(p_worker_id, ''), p_dop_id
    )
    RETURNING id INTO v_id;
    RETURN v_id;
END;
$$;

CREATE OR REPLACE FUNCTION fn_protect_verified_evidence()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'EVIDENCE_IMMUTABLE: % on verified evidence % is forbidden', TG_OP, OLD.id USING ERRCODE = '55000';
END;
$$;

DROP TRIGGER IF EXISTS trg_protect_verified_evidence ON courses_zoomverifiedevidence;
CREATE TRIGGER trg_protect_verified_evidence
BEFORE UPDATE OR DELETE ON courses_zoomverifiedevidence
FOR EACH ROW EXECUTE FUNCTION fn_protect_verified_evidence();

-- ---------------------------------------------------------------- B3 -----
CREATE OR REPLACE FUNCTION fn_resolve_quarantined_dop(p_dop_id bigint, p_resolution_evidence jsonb)
RETURNS boolean
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $$
DECLARE
    v_lc_id bigint;
    v_dop courses_liveclassdurableoperation%ROWTYPE;
    v_meeting_id text;
    v_q_start timestamptz;
    v_probes integer;
BEGIN
    SELECT live_class_id INTO v_lc_id FROM courses_liveclassdurableoperation WHERE id = p_dop_id;
    IF v_lc_id IS NOT NULL THEN
        PERFORM 1 FROM courses_liveclass WHERE id = v_lc_id FOR UPDATE;              -- L1
    END IF;
    SELECT * INTO v_dop FROM courses_liveclassdurableoperation WHERE id = p_dop_id FOR UPDATE;  -- L2
    IF NOT FOUND THEN
        RAISE EXCEPTION 'DOP_NOT_FOUND: %', p_dop_id USING ERRCODE = 'P0002';
    END IF;
    IF v_dop.status NOT IN ('OUTCOME_UNKNOWN', 'COMPENSATION_REQUIRED') THEN
        RAISE EXCEPTION 'DOP_NOT_QUARANTINED: DOP % is %', p_dop_id, v_dop.status USING ERRCODE = '55000';
    END IF;
    IF v_dop.lease_expires_at IS NOT NULL AND v_dop.lease_expires_at > clock_timestamp() THEN
        RAISE EXCEPTION 'DOP_LEASE_ACTIVE: DOP % is leased until %', p_dop_id, v_dop.lease_expires_at USING ERRCODE = '55000';
    END IF;
    v_q_start := COALESCE(v_dop.quarantined_at, v_dop.updated_at);
    IF clock_timestamp() < v_q_start + interval '15 minutes' THEN
        RAISE EXCEPTION 'QUARANTINE_GRACE_ACTIVE: DOP % quarantined at %', p_dop_id, v_q_start USING ERRCODE = '55000';
    END IF;
    v_meeting_id := COALESCE(NULLIF(v_dop.payload->>'meeting_id', ''),
                             NULLIF(v_dop.payload->>'zoom_meeting_id', ''),
                             NULLIF(v_dop.response_payload->>'orphan_meeting_id', ''));
    IF v_meeting_id IS NULL THEN
        RAISE EXCEPTION 'QUARANTINE_MEETING_UNKNOWN: DOP % has no meeting id; use the application resolver (host listing scan)', p_dop_id
            USING ERRCODE = '55000';
    END IF;
    SELECT count(*) INTO v_probes
      FROM courses_zoomverifiedevidence
     WHERE meeting_id = v_meeting_id
       AND dop_id = p_dop_id
       AND evidence_type = 'DUAL_GET_QUARANTINE_VERIFICATION'
       AND http_status = 404
       AND verified_at >= v_q_start + interval '15 minutes'
       AND verified_at >= clock_timestamp() - interval '10 minutes';
    IF v_probes < 2 THEN
        RAISE EXCEPTION 'MISSING_VERIFIED_EVIDENCE: meeting % has % fresh bound 404 probes for DOP % (2 required)', v_meeting_id, v_probes, p_dop_id
            USING ERRCODE = '55000';
    END IF;

    UPDATE courses_liveclassdurableoperation
       SET status = 'COMPENSATED', version = version + 1, lease_worker = NULL, lease_expires_at = NULL,
           updated_at = clock_timestamp()
     WHERE id = p_dop_id;
    UPDATE courses_hostreservation
       SET status = 'RELEASED', release_reason = 'Quarantine resolved via fn_resolve_quarantined_dop (verified dual GET 404)',
           updated_at = clock_timestamp()
     WHERE durable_operation_id = p_dop_id AND status IN ('PROVISIONAL', 'MANUAL_REVIEW');
    RETURN TRUE;
END;
$$;

-- ------------------------------------------------------------ privileges --
DO $$
BEGIN
    -- New functions are executable by PUBLIC by default: lock each one down.
    REVOKE ALL ON FUNCTION fn_session_approver_identity() FROM PUBLIC;
    REVOKE ALL ON FUNCTION fn_guard_reconciliation_approval() FROM PUBLIC;
    REVOKE ALL ON FUNCTION fn_protect_verified_evidence() FROM PUBLIC;
    REVOKE ALL ON FUNCTION fn_record_verified_evidence(text, text, integer, jsonb, text, bigint) FROM PUBLIC, natya_app, natya_admin;
    REVOKE ALL ON FUNCTION fn_approve_reconciliation_manifest(text, text, text, timestamptz, text, text) FROM PUBLIC, natya_app, natya_admin;
    REVOKE ALL ON FUNCTION fn_revoke_manifest_approval(text, text) FROM PUBLIC, natya_app, natya_admin;
    REVOKE ALL ON FUNCTION fn_enroll_approver_key(text, text) FROM PUBLIC, natya_app, natya_admin;
    REVOKE ALL ON FUNCTION fn_revoke_approver_key(text) FROM PUBLIC, natya_app, natya_admin;
    REVOKE ALL ON FUNCTION fn_execute_exact_rollback_0038(text, jsonb) FROM PUBLIC, natya_app, natya_admin;

    GRANT EXECUTE ON FUNCTION fn_session_approver_identity() TO natya_approver;
    GRANT EXECUTE ON FUNCTION fn_approve_reconciliation_manifest(text, text, text, timestamptz, text, text) TO natya_approver;
    GRANT EXECUTE ON FUNCTION fn_revoke_manifest_approval(text, text) TO natya_approver;

    -- Governance tables: no direct access for application/admin roles.
    REVOKE ALL ON courses_zoomreconciliationapproval FROM PUBLIC, natya_app, natya_admin, natya_approver;
    REVOKE ALL ON courses_zoommanifestnonces FROM PUBLIC, natya_app, natya_admin, natya_approver;
    REVOKE ALL ON courses_zoomapproversecrets FROM PUBLIC, natya_app, natya_admin, natya_approver;
    REVOKE ALL ON courses_zoomrollbacksession FROM PUBLIC, natya_app, natya_admin, natya_approver;
    GRANT SELECT ON courses_zoomreconciliationapproval TO natya_admin;

    -- Evidence: read-only for application/admin roles; writes only via the owner.
    REVOKE ALL ON courses_zoomverifiedevidence FROM PUBLIC, natya_app, natya_admin;
    GRANT SELECT ON courses_zoomverifiedevidence TO natya_app, natya_admin;
END $$;
"""


def _forward(apps, schema_editor):
    if schema_editor.connection.vendor != 'postgresql':
        return
    # params=None: no client-side %-interpolation (the SQL contains RAISE '%' placeholders).
    schema_editor.execute(FORWARD_SQL, params=None)


def _irreversible(apps, schema_editor):
    from django.db.migrations.exceptions import IrreversibleError
    raise IrreversibleError(
        "0038 hardens approval/evidence/rollback security; reversing it would restore known-vulnerable "
        "functions and grants. Manual administration required."
    )


class Migration(migrations.Migration):

    dependencies = [
        ('courses', '0037_zoom_phase_b5_governance_closure'),
    ]

    operations = [
        migrations.AddField(
            model_name='liveclassdurableoperation',
            name='quarantined_at',
            field=models.DateTimeField(blank=True, db_index=True, null=True),
        ),
        migrations.AddField(
            model_name='zoomverifiedevidence',
            name='dop_id',
            field=models.BigIntegerField(blank=True, db_index=True, null=True),
        ),
        migrations.RunPython(_forward, _irreversible),
    ]
