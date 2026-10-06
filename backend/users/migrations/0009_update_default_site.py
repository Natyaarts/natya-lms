from django.db import migrations

def update_default_site(apps, schema_editor):
    Site = apps.get_model('sites', 'Site')
    site, _ = Site.objects.get_or_create(id=1)
    site.domain = 'academy.natyaarts.com'
    site.name = 'Natya LMS'
    site.save()

def reverse_update(apps, schema_editor):
    pass

class Migration(migrations.Migration):
    dependencies = [
        ('users', '0008_otpverification_purpose_accountdeletionrequest'),
        ('sites', '0002_alter_domain_unique'),
    ]

    operations = [
        migrations.RunPython(update_default_site, reverse_update),
    ]
