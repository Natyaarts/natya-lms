import django.core.validators
from django.db import migrations, models


class Migration(migrations.Migration):
    """
    Optional automatic LATE rule (default 0 = off) and the waiting period after
    a Zoom meeting ends before attendance is finalized (default 15 minutes, the
    wait the catch-up task already used).
    """

    dependencies = [
        ('courses', '0043_certificate_is_active'),
    ]

    operations = [
        migrations.AddField(
            model_name='attendanceconfiguration',
            name='late_after_minutes',
            field=models.PositiveSmallIntegerField(
                default=0,
                db_default=0,
                help_text=(
                    "0 = off. Otherwise a student who meets the threshold but first joined Zoom more than "
                    "this many minutes after the class started is marked LATE instead of PRESENT."
                ),
                validators=[django.core.validators.MaxValueValidator(240)],
            ),
        ),
        migrations.AddField(
            model_name='attendanceconfiguration',
            name='finalize_after_minutes',
            field=models.PositiveSmallIntegerField(
                default=15,
                db_default=15,
                help_text=(
                    "Minutes to wait after the Zoom meeting ended before attendance is finalized "
                    "(students with no verified Zoom attendance are then marked ABSENT)."
                ),
                validators=[django.core.validators.MaxValueValidator(1440)],
            ),
        ),
    ]
