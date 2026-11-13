# Natya LMS Staging - Phase B.5 runtime verification follow-up.
#
# Production connects as the RDS master login (RDS_USERNAME / DATABASE_URL), which also
# runs `manage.py migrate` and therefore OWNS every table and function: revoking EXECUTE
# from natya_app/natya_admin does not (and cannot) restrict it. The database cannot
# authenticate a Zoom HTTP response, so evidence is treated as an AUDIT RECORD, never as
# an authorization token:
#
#   * fn_record_verified_evidence now additionally binds every row to an operation
#     that legitimately expects it (see BINDING below), so a call cannot attach
#     evidence to an unrelated meeting, an operation it does not hold the lease for,
#     or a meeting that is not being torn down.
#   * fn_resolve_quarantined_dop -- the only path that released a host on the basis of
#     stored evidence alone -- is DISABLED. Hosts are released only by the application
#     resolver/recovery, which make the live authenticated Zoom calls themselves.
from django.db import migrations


FORWARD_SQL = r"""
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
    v_dop courses_liveclassdurableoperation%ROWTYPE;
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
        SELECT * INTO v_dop FROM courses_liveclassdurableoperation WHERE id = p_dop_id;
        IF p_dop_id IS NULL OR NOT FOUND THEN
            RAISE EXCEPTION 'EVIDENCE_DOP_REQUIRED: % evidence must be bound to an existing durable operation', p_evidence_type USING ERRCODE = '22000';
        END IF;
    END IF;

    -- BINDING: the operation must legitimately expect this evidence right now.
    IF p_evidence_type = 'DUAL_GET_QUARANTINE_VERIFICATION' THEN
        IF v_dop.status NOT IN ('OUTCOME_UNKNOWN', 'COMPENSATION_REQUIRED')
           OR p_meeting_id NOT IN (COALESCE(v_dop.payload->>'meeting_id', ''), COALESCE(v_dop.payload->>'zoom_meeting_id', ''),
                                   COALESCE(v_dop.response_payload->>'orphan_meeting_id', ''))
           OR v_dop.lease_worker IS NULL OR v_dop.lease_expires_at IS NULL OR v_dop.lease_expires_at <= clock_timestamp()
           OR p_worker_id IS DISTINCT FROM ('quarantine:' || v_dop.lease_worker::text) THEN
            RAISE EXCEPTION 'EVIDENCE_NOT_BOUND: quarantine probe evidence requires a quarantined DOP, one of its own meetings and the current resolver lease'
                USING ERRCODE = '22000';
        END IF;
    ELSIF p_evidence_type = 'CRASH_RECOVERY_TEARDOWN' THEN
        IF v_dop.status NOT IN ('IN_PROGRESS', 'OUTCOME_UNKNOWN')
           OR v_dop.lease_worker IS NULL OR v_dop.lease_expires_at IS NULL OR v_dop.lease_expires_at <= clock_timestamp()
           OR p_worker_id IS DISTINCT FROM ('crash_recovery:' || v_dop.lease_worker::text) THEN
            RAISE EXCEPTION 'EVIDENCE_NOT_BOUND: crash recovery evidence requires an in-flight DOP and the current recovery lease'
                USING ERRCODE = '22000';
        END IF;
    ELSIF p_evidence_type = 'ORPHAN_FENCING_TEARDOWN' THEN
        IF COALESCE(v_dop.response_payload->>'orphan_meeting_id', '') <> p_meeting_id THEN
            RAISE EXCEPTION 'EVIDENCE_NOT_BOUND: meeting % is not the recorded fenced orphan of DOP %', p_meeting_id, p_dop_id
                USING ERRCODE = '22000';
        END IF;
    ELSIF p_evidence_type = 'CANCELLATION_SAGA_DELETE' THEN
        IF NOT EXISTS (SELECT 1 FROM courses_zoommeetingrecord
                        WHERE zoom_meeting_id = p_meeting_id AND state IN ('COMPENSATING', 'COMPENSATED')) THEN
            RAISE EXCEPTION 'EVIDENCE_NOT_BOUND: meeting % is not under cancellation compensation', p_meeting_id
                USING ERRCODE = '22000';
        END IF;
    ELSIF p_evidence_type = 'OUTBOX_TEARDOWN_CONFIRMATION' THEN
        IF NOT EXISTS (SELECT 1 FROM courses_zoomlifecycleoutbox
                        WHERE event_type = 'ZOOM_MEETING_COMMIT_FAILED_NEEDS_TEARDOWN' AND payload->>'meeting_id' = p_meeting_id) THEN
            RAISE EXCEPTION 'EVIDENCE_NOT_BOUND: no teardown is queued for meeting %', p_meeting_id
                USING ERRCODE = '22000';
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

CREATE OR REPLACE FUNCTION fn_resolve_quarantined_dop(p_dop_id bigint, p_resolution_evidence jsonb)
RETURNS boolean
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $$
BEGIN
    RAISE EXCEPTION 'SQL_QUARANTINE_RESOLUTION_DISABLED: stored evidence cannot authorize a host release; use ZoomQuarantineService (live authenticated Zoom probes)'
        USING ERRCODE = '42501';
END;
$$;

DO $$
BEGIN
    REVOKE ALL ON FUNCTION fn_record_verified_evidence(text, text, integer, jsonb, text, bigint) FROM PUBLIC, natya_app, natya_admin;
    REVOKE ALL ON FUNCTION fn_resolve_quarantined_dop(bigint, jsonb) FROM PUBLIC, natya_app, natya_admin;
END $$;
"""


def _forward(apps, schema_editor):
    if schema_editor.connection.vendor != 'postgresql':
        return
    schema_editor.execute(FORWARD_SQL, params=None)


def _irreversible(apps, schema_editor):
    from django.db.migrations.exceptions import IrreversibleError
    raise IrreversibleError(
        "0039 binds evidence to live operations and disables evidence-only host release; reversing it "
        "would re-open those paths. Manual administration required."
    )


class Migration(migrations.Migration):

    dependencies = [
        ('courses', '0038_zoom_phase_b5_security_repairs'),
    ]

    operations = [
        migrations.RunPython(_forward, _irreversible),
    ]
