# Generated for Natya LMS Staging - Phase A Ownership & Closure
import datetime
import hashlib
import re
import uuid
from django.db import migrations, models
import django.db.models.deletion
from django.conf import settings
from django.db.migrations.exceptions import IrreversibleError


def preflight_and_backfill_historical_data(apps, schema_editor):
    """
    Idempotent preflight and historical data backfill for Zoom meeting ownership.
    Scans every existing LiveClass row with a non-empty zoom_meeting_id and partitions
    them safely into:
      - Duplicate Collisions: HistoricalZoomConflict (DUPLICATE_COLLISION) + POISONED ZoomMeetingRecord
      - Length Overflow (>64 chars) or Malformed: HistoricalZoomConflict + POISONED ZoomMeetingRecord
      - Cancelled Classes (unverified deletion): POISONED ZoomMeetingRecord
      - Clean Active / Completed: ASSIGNED ZoomMeetingRecord
    """
    LiveClass = apps.get_model('courses', 'LiveClass')
    ZoomMeetingRecord = apps.get_model('courses', 'ZoomMeetingRecord')
    HistoricalZoomConflict = apps.get_model('courses', 'HistoricalZoomConflict')

    # Step 1: Detect duplicate meeting IDs
    duplicate_qs = (
        LiveClass.objects.exclude(zoom_meeting_id='')
        .exclude(zoom_meeting_id__isnull=True)
        .values('zoom_meeting_id')
        .annotate(cnt=models.Count('id'))
        .filter(cnt__gt=1)
    )
    duplicate_ids = {d['zoom_meeting_id'] for d in duplicate_qs}

    # Step 2: Process every historical LiveClass with a meeting ID in deterministic ID order
    historical_classes = (
        LiveClass.objects
        .exclude(zoom_meeting_id='')
        .exclude(zoom_meeting_id__isnull=True)
        .order_by('id')
    )

    for lc in historical_classes:
        raw_id = lc.zoom_meeting_id.strip() if lc.zoom_meeting_id else ''
        if not raw_id:
            continue

        is_numeric_9_11 = bool(re.match(r'^\d{9,11}$', raw_id))
        is_length_overflow = len(raw_id) > 64

        snapshot = {
            "title": lc.title,
            "status": lc.status,
            "scheduled_start": lc.scheduled_start.isoformat() if lc.scheduled_start else None,
            "instructor_id": lc.instructor_id,
            "batch_id": lc.batch_id
        }

        # Case A: Duplicate Collision
        if raw_id in duplicate_ids:
            if not HistoricalZoomConflict.objects.filter(historical_live_class_id=lc.id, conflict_type='DUPLICATE_COLLISION').exists():
                HistoricalZoomConflict.objects.create(
                    live_class=lc,
                    historical_live_class_id=lc.id,
                    live_class_snapshot=snapshot,
                    zoom_meeting_id_raw=raw_id,
                    conflict_type='DUPLICATE_COLLISION',
                    details={'class_status': lc.status, 'title': lc.title}
                )
            # Create single POISONED meeting record if not already present
            lookup_id = raw_id[:64] if not is_length_overflow else f"OVERFLOW-{lc.id}-{hashlib.md5(raw_id.encode()).hexdigest()[:16]}"
            if not ZoomMeetingRecord.objects.filter(zoom_meeting_id=lookup_id).exists():
                ZoomMeetingRecord.objects.create(
                    zoom_meeting_id=lookup_id,
                    live_class=None,
                    historical_live_class_id=lc.id,
                    state='POISONED',
                    topic=lc.title[:255] if lc.title else '',
                    quarantine_reason="DUPLICATE_COLLISION: Shared across multiple LiveClass IDs"
                )

        # Case B: Length Overflow (> 64 chars) or Malformed Non-Numeric
        elif is_length_overflow or not is_numeric_9_11:
            conflict_label = 'LENGTH_OVERFLOW' if is_length_overflow else 'MALFORMED_FORMAT'
            if not HistoricalZoomConflict.objects.filter(historical_live_class_id=lc.id, conflict_type=conflict_label).exists():
                HistoricalZoomConflict.objects.create(
                    live_class=lc,
                    historical_live_class_id=lc.id,
                    live_class_snapshot=snapshot,
                    zoom_meeting_id_raw=raw_id,
                    conflict_type=conflict_label,
                    details={'class_status': lc.status, 'title': lc.title, 'length': len(raw_id)}
                )
            lookup_id = raw_id if not is_length_overflow else f"OVERFLOW-{lc.id}-{hashlib.md5(raw_id.encode()).hexdigest()[:16]}"
            if not ZoomMeetingRecord.objects.filter(zoom_meeting_id=lookup_id).exists():
                ZoomMeetingRecord.objects.create(
                    zoom_meeting_id=lookup_id,
                    live_class=None,
                    historical_live_class_id=lc.id,
                    state='POISONED',
                    topic=lc.title[:255] if lc.title else '',
                    quarantine_reason=f"{conflict_label}: Original raw length {len(raw_id)}"
                )

        # Case C: Cancelled Class without confirmed Zoom API deletion
        elif lc.status == 'CANCELLED':
            if not ZoomMeetingRecord.objects.filter(zoom_meeting_id=raw_id).exists():
                ZoomMeetingRecord.objects.create(
                    zoom_meeting_id=raw_id,
                    live_class=None,
                    historical_live_class_id=lc.id,
                    state='POISONED',
                    topic=lc.title[:255] if lc.title else '',
                    quarantine_reason="HISTORICAL_CANCELLED_UNVERIFIED_ZOOM_DELETION"
                )

        # Case D: Clean Active or Completed Class -> ASSIGNED
        else:
            if not ZoomMeetingRecord.objects.filter(zoom_meeting_id=raw_id).exists():
                ZoomMeetingRecord.objects.create(
                    zoom_meeting_id=raw_id,
                    live_class=lc,
                    historical_live_class_id=lc.id,
                    state='ASSIGNED',
                    topic=lc.title[:255] if lc.title else ''
                )


def reverse_preflight_and_backfill(apps, schema_editor):
    """
    Explicitly irreversible to protect historical conflict evidence and quarantined meeting IDs.
    Dropping tables or unravelling registry records would cause permanent loss of audit trail.
    """
    raise IrreversibleError(
        "Migration 0035 is explicitly irreversible to protect the integrity of HistoricalZoomConflict "
        "and ZoomMeetingRecord audit logs. Dropping these tables would cause permanent loss of meeting "
        "ownership history and collision forensics. Manual database administration is required if rollback is needed."
    )


class Migration(migrations.Migration):

    atomic = True

    dependencies = [
        ('courses', '0034_liveclass_min_attendance_percentage_and_more'),
    ]

    operations = [
        # Op 1: Extension creation, helper functions, and local lock timeout
        migrations.RunSQL(
            sql="""
            CREATE EXTENSION IF NOT EXISTS btree_gist;
            SET LOCAL lock_timeout = '5s';
            CREATE OR REPLACE FUNCTION liveclass_tstzrange(scheduled_start timestamptz, duration_minutes integer)
            RETURNS tstzrange AS $$
                SELECT tstzrange(scheduled_start, scheduled_start + (duration_minutes * INTERVAL '1 minute'), '[)');
            $$ LANGUAGE sql IMMUTABLE;
            CREATE OR REPLACE FUNCTION liveclass_effective_instructor(substitute_id bigint, instructor_id bigint)
            RETURNS bigint AS $$
                SELECT COALESCE(substitute_id, instructor_id);
            $$ LANGUAGE sql IMMUTABLE;
            """,
            reverse_sql="""
            SET LOCAL lock_timeout = '5s';
            DROP FUNCTION IF EXISTS liveclass_effective_instructor(bigint, bigint);
            DROP FUNCTION IF EXISTS liveclass_tstzrange(timestamptz, integer);
            """
        ),

        # Op 2: Create LiveClassDurableOperation
        migrations.CreateModel(
            name='LiveClassDurableOperation',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('idempotency_key', models.CharField(db_index=True, max_length=128, unique=True)),
                ('operation_type', models.CharField(
                    choices=[
                        ('ZOOM_PROVISION', 'Zoom Provisioning'),
                        ('ZOOM_CANCEL', 'Zoom Cancellation'),
                        ('ZOOM_RESCHEDULE', 'Zoom Reschedule'),
                        ('ZOOM_RECOVERY', 'Zoom Recovery')
                    ],
                    db_index=True,
                    default='ZOOM_PROVISION',
                    max_length=32
                )),
                ('status', models.CharField(
                    choices=[
                        ('IN_PROGRESS', 'In Progress'),
                        ('COMPLETED', 'Completed'),
                        ('FAILED', 'Failed'),
                        ('OUTCOME_UNKNOWN', 'Outcome Unknown'),
                        ('COMPENSATION_REQUIRED', 'Compensation Required'),
                        ('COMPENSATING', 'Compensating'),
                        ('COMPENSATED', 'Compensated'),
                        ('MANUAL_REVIEW_REQUIRED', 'Manual Review Required')
                    ],
                    db_index=True,
                    default='IN_PROGRESS',
                    max_length=32
                )),
                ('payload', models.JSONField(blank=True, default=dict)),
                ('payload_hash', models.CharField(blank=True, default='', max_length=64)),
                ('response_payload', models.JSONField(blank=True, default=dict)),
                ('error_message', models.TextField(blank=True, default='')),
                ('lease_worker', models.UUIDField(blank=True, db_index=True, null=True)),
                ('lease_expires_at', models.DateTimeField(blank=True, db_index=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('live_class', models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='durable_operations',
                    to='courses.liveclass'
                )),
            ],
            options={
                'ordering': ['-created_at'],
                'verbose_name': 'Live Class Durable Operation',
                'verbose_name_plural': 'Live Class Durable Operations'
            },
        ),

        # Op 3: Create HistoricalZoomConflict
        migrations.CreateModel(
            name='HistoricalZoomConflict',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('historical_live_class_id', models.BigIntegerField(db_index=True, help_text='Immutable snapshot of the LiveClass primary key at conflict recording time.')),
                ('live_class_snapshot', models.JSONField(blank=True, default=dict, help_text='Immutable snapshot of class title, scheduled_start, status, and instructor at conflict time.')),
                ('zoom_meeting_id_raw', models.TextField(help_text='Unbounded text preserving the exact raw meeting ID string without truncation.')),
                ('conflict_type', models.CharField(db_index=True, help_text='Category of conflict: DUPLICATE_COLLISION, MALFORMED_ID, LENGTH_OVERFLOW, etc.', max_length=64)),
                ('details', models.JSONField(blank=True, default=dict)),
                ('resolved', models.BooleanField(default=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('live_class', models.ForeignKey(
                    blank=True,
                    help_text='Soft pointer to LiveClass; preserved as null if LiveClass is removed.',
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='zoom_conflicts',
                    to='courses.liveclass'
                )),
            ],
            options={
                'ordering': ['-created_at'],
                'verbose_name': 'Historical Zoom Conflict',
                'verbose_name_plural': 'Historical Zoom Conflicts'
            },
        ),

        # Op 4: Create ZoomMeetingRecord with PROTECT Foreign Key
        migrations.CreateModel(
            name='ZoomMeetingRecord',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('zoom_meeting_id', models.CharField(db_index=True, max_length=64, unique=True)),
                ('topic', models.CharField(blank=True, default='', max_length=255)),
                ('state', models.CharField(
                    choices=[
                        ('RESERVED', 'Reserved'),
                        ('ASSIGNED', 'Assigned'),
                        ('RELEASED', 'Released'),
                        ('REASSIGNED', 'Reassigned'),
                        ('COMPENSATING', 'Compensating'),
                        ('COMPENSATED', 'Compensated'),
                        ('POISONED', 'Poisoned')
                    ],
                    db_index=True,
                    default='RESERVED',
                    max_length=32
                )),
                ('owner_token', models.UUIDField(db_index=True, default=uuid.uuid4)),
                ('lease_worker', models.UUIDField(blank=True, db_index=True, null=True)),
                ('lease_expires_at', models.DateTimeField(blank=True, db_index=True, null=True)),
                ('retry_count', models.PositiveSmallIntegerField(default=0)),
                ('quarantine_reason', models.TextField(blank=True, default='')),
                ('version', models.PositiveIntegerField(default=1)),
                ('historical_live_class_id', models.BigIntegerField(blank=True, db_index=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('durable_operation', models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='zoom_records',
                    to='courses.liveclassdurableoperation'
                )),
                ('live_class', models.OneToOneField(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name='zoom_meeting_record',
                    to='courses.liveclass'
                )),
            ],
            options={
                'ordering': ['-created_at'],
                'verbose_name': 'Zoom Meeting Record',
                'verbose_name_plural': 'Zoom Meeting Records'
            },
        ),

        # Op 5: Add LiveClass columns, Parity & Positive Duration Constraints, and Sync Trigger
        migrations.AddField(
            model_name='liveclass',
            name='substitute_instructor',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='substituted_live_classes',
                to=settings.AUTH_USER_MODEL,
                help_text='Optional substitute instructor conducting this specific class instance.'
            ),
        ),
        migrations.AddField(
            model_name='liveclass',
            name='is_substitute_instructor',
            field=models.BooleanField(default=False, help_text='Flag indicating whether a substitute instructor is assigned.'),
        ),
        migrations.RunSQL(sql="SET LOCAL lock_timeout = '5s';", reverse_sql="SET LOCAL lock_timeout = '5s';"),
        migrations.AddConstraint(
            model_name='liveclass',
            constraint=models.CheckConstraint(
                condition=models.Q(duration_minutes__gt=0),
                name='check_liveclass_positive_duration'
            ),
        ),
        migrations.AddConstraint(
            model_name='liveclass',
            constraint=models.CheckConstraint(
                condition=models.Q(
                    models.Q(is_substitute_instructor=True, substitute_instructor__isnull=False) |
                    models.Q(is_substitute_instructor=False, substitute_instructor__isnull=True)
                ),
                name='check_liveclass_substitute_flag_parity'
            ),
        ),
        migrations.RunSQL(
            sql="""
            SET LOCAL lock_timeout = '5s';
            CREATE OR REPLACE FUNCTION sync_liveclass_substitute_flag()
            RETURNS TRIGGER AS $$
            BEGIN
                NEW.is_substitute_instructor := (NEW.substitute_instructor_id IS NOT NULL);
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql;

            DROP TRIGGER IF EXISTS trg_sync_liveclass_substitute_flag ON courses_liveclass;
            CREATE TRIGGER trg_sync_liveclass_substitute_flag
            BEFORE INSERT OR UPDATE ON courses_liveclass
            FOR EACH ROW
            EXECUTE FUNCTION sync_liveclass_substitute_flag();
            """,
            reverse_sql="""
            SET LOCAL lock_timeout = '5s';
            DROP TRIGGER IF EXISTS trg_sync_liveclass_substitute_flag ON courses_liveclass;
            DROP FUNCTION IF EXISTS sync_liveclass_substitute_flag();
            """
        ),

        # Op 6: Backfill Historical Data
        migrations.RunPython(
            code=preflight_and_backfill_historical_data,
            reverse_code=reverse_preflight_and_backfill
        ),

        # Op 7: Add Registry Check Constraints
        migrations.RunSQL(sql="SET LOCAL lock_timeout = '5s';", reverse_sql="SET LOCAL lock_timeout = '5s';"),
        migrations.AddConstraint(
            model_name='zoommeetingrecord',
            constraint=models.CheckConstraint(
                condition=models.Q(models.Q(state='ASSIGNED', _negated=True) | models.Q(live_class__isnull=False)),
                name='check_zmr_assigned_has_live_class'
            ),
        ),
        migrations.AddConstraint(
            model_name='zoommeetingrecord',
            constraint=models.CheckConstraint(
                condition=models.Q(models.Q(state='COMPENSATING', _negated=True) | models.Q(live_class__isnull=True)),
                name='check_zmr_compensating_no_live_class'
            ),
        ),
        migrations.AddConstraint(
            model_name='zoommeetingrecord',
            constraint=models.CheckConstraint(
                condition=models.Q(models.Q(state='COMPENSATED', _negated=True) | models.Q(live_class__isnull=True)),
                name='check_zmr_compensated_no_live_class'
            ),
        ),
        migrations.AddConstraint(
            model_name='zoommeetingrecord',
            constraint=models.CheckConstraint(
                condition=models.Q(state='POISONED') | models.Q(zoom_meeting_id__regex=r'^\d{9,11}$'),
                name='check_zmr_numeric_meeting_id'
            ),
        ),

        # Op 8: PostgreSQL exclusion constraints for instructor and batch overlap (active states only)
        migrations.RunSQL(
            sql="""
            SET LOCAL lock_timeout = '5s';
            ALTER TABLE courses_liveclass
                ADD CONSTRAINT exclude_instructor_schedule_overlap
                EXCLUDE USING gist (
                    liveclass_effective_instructor(substitute_instructor_id, instructor_id) WITH =,
                    liveclass_tstzrange(scheduled_start, duration_minutes) WITH &&
                ) WHERE (status IN ('SCHEDULED', 'LIVE'));

            SET LOCAL lock_timeout = '5s';
            ALTER TABLE courses_liveclass
                ADD CONSTRAINT exclude_batch_schedule_overlap
                EXCLUDE USING gist (
                    batch_id WITH =,
                    liveclass_tstzrange(scheduled_start, duration_minutes) WITH &&
                ) WHERE (status IN ('SCHEDULED', 'LIVE'));
            """,
            reverse_sql="""
            ALTER TABLE courses_liveclass DROP CONSTRAINT IF EXISTS exclude_instructor_schedule_overlap;
            ALTER TABLE courses_liveclass DROP CONSTRAINT IF EXISTS exclude_batch_schedule_overlap;
            """
        ),
    ]
