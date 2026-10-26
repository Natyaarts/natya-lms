import secrets
from django.db import migrations, models


def populate_student_ids(apps, schema_editor):
    User = apps.get_model('users', 'User')
    existing_ids = set(User.objects.exclude(student_id__isnull=True).values_list('student_id', flat=True))
    for user in User.objects.filter(student_id__isnull=True):
        candidate = None
        for _ in range(50):
            token = f"NAT-{secrets.token_hex(3).upper()}"
            if token not in existing_ids:
                candidate = token
                break
        if not candidate:
            candidate = f"NAT-{secrets.token_hex(6).upper()}"
        existing_ids.add(candidate)
        user.student_id = candidate
        user.save(update_fields=['student_id'])


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0009_update_default_site'),
    ]

    operations = [
        migrations.AddField(
            model_name='user',
            name='student_id',
            field=models.CharField(blank=True, db_index=True, help_text='Stable, unique student identifier for dynamic video watermarking (e.g. NAT-XXXXXX).', max_length=32, null=True, unique=True),
        ),
        migrations.RunPython(populate_student_ids, noop),
    ]

