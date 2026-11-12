# Generated for Natya LMS Staging - Phase B.4 Zoom Lifecycle Final Closure
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
from django.db.migrations.exceptions import IrreversibleError


def preflight_and_quarantine_unverified_records(apps, schema_editor):
    """
    Forward data integrity migration:
    - Resolves DEF-05: Safely detects multiple active historical records for the same LiveClass.
    - Quarantines ambiguous colliding records into HistoricalZoomConflict without arbitrary election.
    - Marks terminal and unlinked records as is_current=False.
    - Marks only unique, defensibly active records as is_current=True.
    - Preserves all historical records, foreign keys, and audit metadata intact.
    """
    ZoomMeetingRecord = apps.get_model('courses', 'ZoomMeetingRecord')
    HistoricalZoomConflict = apps.get_model('courses', 'HistoricalZoomConflict')
    LiveClass = apps.get_model('courses', 'LiveClass')

    # Step 1: Mark terminal states and unlinked records as is_current=False
    for zmr in ZoomMeetingRecord.objects.all():
        if zmr.state in ('RELEASED', 'COMPENSATED', 'POISONED', 'REASSIGNED') or not zmr.live_class_id:
            zmr.is_current = False
            zmr.save(update_fields=['is_current'])

    # Step 2: Group active candidates (ASSIGNED, RESERVED) by live_class_id
    active_candidates = ZoomMeetingRecord.objects.filter(
        state__in=('ASSIGNED', 'RESERVED'),
        live_class_id__isnull=False
    ).order_by('id')

    records_by_class = {}
    for zmr in active_candidates:
        records_by_class.setdefault(zmr.live_class_id, []).append(zmr)

    for lc_id, class_zmrs in records_by_class.items():
        if len(class_zmrs) == 1:
            # Single defensible active record for this class
            zmr = class_zmrs[0]
            zmr.is_current = True
            zmr.save(update_fields=['is_current'])
        else:
            # Multiple active records for the same LiveClass: AMBIGUITY DETECTED!
            # Never arbitrarily elect one as current. Mark all is_current=False and quarantine.
            lc = LiveClass.objects.filter(id=lc_id).first()
            snapshot = {
                "title": lc.title if lc else "",
                "status": lc.status if lc else "",
                "scheduled_start": lc.scheduled_start.isoformat() if lc and lc.scheduled_start else None,
            }
            meeting_ids = [r.zoom_meeting_id for r in class_zmrs]

            for zmr in class_zmrs:
                zmr.is_current = False
                zmr.state = 'POISONED'
                zmr.quarantine_reason = (
                    f"MIGRATION_0036_COLLISION: Multiple active records {meeting_ids} "
                    f"for LiveClass #{lc_id}. Quarantined without arbitrary election."
                )
                zmr.save(update_fields=['is_current', 'state', 'quarantine_reason'])

                HistoricalZoomConflict.objects.create(
                    live_class=lc,
                    historical_live_class_id=lc_id,
                    live_class_snapshot=snapshot,
                    zoom_meeting_id_raw=zmr.zoom_meeting_id,
                    conflict_type='DUPLICATE_ACTIVE_RECORDS_MIGRATION_QUARANTINE',
                    details={
                        "record_id": zmr.id,
                        "record_state": "POISONED",
                        "colliding_records_count": len(class_zmrs),
                        "all_meeting_ids": meeting_ids,
                        "reason": "Multiple active (ASSIGNED/RESERVED) records discovered for single LiveClass during Migration 0036. Quarantined without inventing election."
                    },
                    resolved=False
                )


def rollback_limitation(apps, schema_editor):
    """
    Explicit rollback limitation to prevent data corruption or loss of audit logs.
    """
    raise IrreversibleError(
        "Migration 0036 is irreversible to protect the integrity of ZoomHostAccount, "
        "HostReservation, and ZoomAdminAuditLog records. Manual administration is required."
    )


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('courses', '0035_zoom_meeting_record_ownership_closure'),
    ]

    operations = [
        # Op 1: Stage 1 Preflight Check (Pre-existing Phase A schema verification)
        migrations.RunSQL(
            sql="""
            DO $$
            BEGIN
                IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'courses_liveclass' AND column_name = 'zoom_meeting_id') THEN
                    RAISE EXCEPTION 'Preflight Failure: courses_liveclass.zoom_meeting_id missing';
                END IF;
                IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'courses_historicalzoomconflict' AND column_name = 'live_class_id') THEN
                    RAISE EXCEPTION 'Preflight Failure: courses_historicalzoomconflict.live_class_id missing';
                END IF;
            END $$;
            """,
            reverse_sql="",
        ),

        # Op 2: Create model ZoomHostAccount
        migrations.CreateModel(
            name='ZoomHostAccount',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('zoom_user_id', models.CharField(db_index=True, max_length=64, unique=True)),
                ('email', models.EmailField(db_index=True, max_length=254, unique=True)),
                ('display_name', models.CharField(blank=True, default='', max_length=255)),
                ('status', models.CharField(choices=[('ACTIVE', 'Active'), ('DRAINING', 'Draining'), ('INACTIVE', 'Inactive'), ('SUSPENDED', 'Suspended')], db_index=True, default='ACTIVE', max_length=32)),
                ('max_concurrency', models.PositiveIntegerField(default=1)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'verbose_name': 'Zoom Host Account',
                'verbose_name_plural': 'Zoom Host Accounts',
                'ordering': ['id'],
            },
        ),

        # Op 3: Create model HostReservation
        migrations.CreateModel(
            name='HostReservation',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('scheduled_start', models.DateTimeField(db_index=True)),
                ('scheduled_end', models.DateTimeField(db_index=True)),
                ('generation', models.PositiveIntegerField(default=1)),
                ('status', models.CharField(choices=[('PROVISIONAL', 'Provisional'), ('ACTIVE', 'Active'), ('DRAINING', 'Draining'), ('MANUAL_REVIEW', 'Manual Review'), ('RELEASED', 'Released'), ('COMPLETED', 'Completed'), ('CANCELLED', 'Cancelled')], db_index=True, default='PROVISIONAL', max_length=32)),
                ('release_reason', models.TextField(blank=True, default='')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('durable_operation', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='host_reservations', to='courses.liveclassdurableoperation')),
                ('host_account', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='reservations', to='courses.zoomhostaccount')),
                ('live_class', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='host_reservations', to='courses.liveclass')),
            ],
            options={
                'verbose_name': 'Host Reservation',
                'verbose_name_plural': 'Host Reservations',
                'ordering': ['-created_at'],
            },
        ),

        # Op 4: Create model ZoomAdminAuditLog
        migrations.CreateModel(
            name='ZoomAdminAuditLog',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('action', models.CharField(default='MANUAL_RELEASE', max_length=64)),
                ('target_meeting_id', models.CharField(blank=True, default='', max_length=128)),
                ('resolution_type', models.CharField(choices=[('CONFIRMED_NEVER_CREATED', 'Confirmed Never Created'), ('CONFIRMED_TERMINATED_EXTERNALLY', 'Confirmed Terminated Externally')], max_length=64)),
                ('reason', models.TextField()),
                ('evidence', models.JSONField(blank=True, default=dict)),
                ('before_state', models.JSONField(blank=True, default=dict)),
                ('after_state', models.JSONField(blank=True, default=dict)),
                ('ip_address', models.GenericIPAddressField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('admin_user', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='zoom_admin_actions', to=settings.AUTH_USER_MODEL)),
                ('target_dop', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to='courses.liveclassdurableoperation')),
                ('target_host', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to='courses.zoomhostaccount')),
                ('target_live_class', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to='courses.liveclass')),
            ],
            options={
                'verbose_name': 'Zoom Admin Audit Log',
                'verbose_name_plural': 'Zoom Admin Audit Logs',
                'ordering': ['-created_at'],
            },
        ),

        # Op 5: Create model ZoomLifecycleOutbox
        migrations.CreateModel(
            name='ZoomLifecycleOutbox',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('aggregate_id', models.BigIntegerField(db_index=True)),
                ('event_type', models.CharField(db_index=True, max_length=64)),
                ('status', models.CharField(choices=[('PENDING', 'Pending'), ('IN_FLIGHT', 'In Flight'), ('COMPLETED', 'Completed'), ('RETRYING', 'Retrying'), ('FAILED', 'Failed'), ('DEAD_LETTER', 'Dead Letter')], db_index=True, default='PENDING', max_length=32)),
                ('payload', models.JSONField(blank=True, default=dict)),
                ('retry_count', models.PositiveSmallIntegerField(default=0)),
                ('max_retries', models.PositiveSmallIntegerField(default=5)),
                ('next_retry_at', models.DateTimeField(blank=True, db_index=True, null=True)),
                ('error_message', models.TextField(blank=True, default='')),
                ('lease_worker', models.UUIDField(blank=True, db_index=True, null=True)),
                ('lease_expires_at', models.DateTimeField(blank=True, db_index=True, null=True)),
                ('version', models.PositiveIntegerField(default=1)),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'verbose_name': 'Zoom Lifecycle Outbox',
                'verbose_name_plural': 'Zoom Lifecycle Outbox Messages',
                'ordering': ['created_at'],
            },
        ),

        # Op 6: Remove obsolete numeric-only check constraint
        migrations.RemoveConstraint(
            model_name='zoommeetingrecord',
            name='check_zmr_numeric_meeting_id',
        ),

        # Op 7: Add version to LiveClassDurableOperation
        migrations.AddField(
            model_name='liveclassdurableoperation',
            name='version',
            field=models.PositiveIntegerField(default=1),
        ),

        # Op 8: Add fields to ZoomMeetingRecord
        migrations.AddField(
            model_name='zoommeetingrecord',
            name='generation',
            field=models.PositiveIntegerField(default=1),
        ),
        migrations.AddField(
            model_name='zoommeetingrecord',
            name='is_current',
            field=models.BooleanField(db_index=True, default=True),
        ),
        migrations.AddField(
            model_name='zoommeetingrecord',
            name='scheduled_start',
            field=models.DateTimeField(blank=True, db_index=True, null=True),
        ),
        migrations.AddField(
            model_name='zoommeetingrecord',
            name='scheduled_end',
            field=models.DateTimeField(blank=True, db_index=True, null=True),
        ),
        migrations.AddField(
            model_name='zoommeetingrecord',
            name='host_account',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='zoom_records', to='courses.zoomhostaccount'),
        ),

        # Op 9: Alter zoom_meeting_id from VARCHAR(64) to VARCHAR(128)
        migrations.AlterField(
            model_name='zoommeetingrecord',
            name='zoom_meeting_id',
            field=models.CharField(db_index=True, max_length=128),
        ),

        # Op 10: Alter live_class to ForeignKey
        migrations.AlterField(
            model_name='zoommeetingrecord',
            name='live_class',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='zoom_meeting_records', to='courses.liveclass'),
        ),

        # Op 11: Alter state choices
        migrations.AlterField(
            model_name='zoommeetingrecord',
            name='state',
            field=models.CharField(choices=[('RESERVED', 'Reserved'), ('ASSIGNED', 'Assigned'), ('RELEASED', 'Released'), ('REASSIGNED', 'Reassigned'), ('COMPENSATING', 'Compensating'), ('COMPENSATED', 'Compensated'), ('POISONED', 'Poisoned'), ('ADOPTED_PENDING_COMMIT', 'Adopted Pending Commit'), ('COMMITTED_PENDING_TEARDOWN', 'Committed Pending Teardown')], db_index=True, default='RESERVED', max_length=32),
        ),

        # Op 12: Run Python data backfill/quarantine
        migrations.RunPython(
            code=preflight_and_quarantine_unverified_records,
            reverse_code=rollback_limitation,
        ),

        # Op 13: Add new constraints to ZoomMeetingRecord
        migrations.AddConstraint(
            model_name='zoommeetingrecord',
            constraint=models.CheckConstraint(condition=models.Q(('state', 'POISONED'), ('zoom_meeting_id__regex', '^\\d{8,15}$'), ('zoom_meeting_id__regex', '^[a-zA-Z0-9+/=_-]{16,128}$'), _connector='OR'), name='check_zmr_meeting_id_format'),
        ),
        migrations.AddConstraint(
            model_name='zoommeetingrecord',
            constraint=models.UniqueConstraint(condition=models.Q(('is_current', True)), fields=('live_class',), name='idx_zmr_active_live_class'),
        ),
        migrations.AddConstraint(
            model_name='zoommeetingrecord',
            constraint=models.UniqueConstraint(condition=models.Q(('is_current', True)), fields=('zoom_meeting_id',), name='idx_zmr_active_meeting_id'),
        ),

        # Op 14: Add constraints to HostReservation
        migrations.AddConstraint(
            model_name='hostreservation',
            constraint=models.UniqueConstraint(fields=('live_class', 'host_account', 'scheduled_start', 'scheduled_end', 'generation'), name='uq_host_res_composite_key'),
        ),
        migrations.AddConstraint(
            model_name='hostreservation',
            constraint=models.UniqueConstraint(condition=models.Q(('status', 'ACTIVE')), fields=('live_class',), name='uq_host_res_single_active_per_class'),
        ),
    ]

