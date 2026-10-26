from django.db import migrations, models

DEFAULT_STATES = [
    "Andhra Pradesh", "Arunachal Pradesh", "Assam", "Bihar", "Chhattisgarh",
    "Goa", "Gujarat", "Haryana", "Himachal Pradesh", "Jharkhand",
    "Karnataka", "Kerala", "Madhya Pradesh", "Maharashtra", "Manipur",
    "Meghalaya", "Mizoram", "Nagaland", "Odisha", "Punjab",
    "Rajasthan", "Sikkim", "Tamil Nadu", "Telangana", "Tripura",
    "Uttar Pradesh", "Uttarakhand", "West Bengal",
    "Andaman and Nicobar Islands", "Chandigarh", "Dadra and Nagar Haveli and Daman and Diu",
    "Delhi", "Jammu and Kashmir", "Ladakh", "Lakshadweep", "Puducherry",
    "Outside India"
]

def seed_default_onboarding_fields(apps, schema_editor):
    OnboardingField = apps.get_model('users', 'OnboardingField')
    
    defaults = [
        {
            "name": "student_type",
            "label": "Learning Track",
            "field_type": "dropdown",
            "is_required": True,
            "options": ["Career Academy", "Regular"],
            "order": 1,
        },
        {
            "name": "full_name",
            "label": "Full Name",
            "field_type": "text",
            "is_required": True,
            "options": None,
            "order": 2,
        },
        {
            "name": "phone_number",
            "label": "Mobile Number",
            "field_type": "text",
            "is_required": True,
            "options": None,
            "order": 3,
        },
        {
            "name": "email",
            "label": "Email Address",
            "field_type": "text",
            "is_required": True,
            "options": None,
            "order": 4,
        },
        {
            "name": "state",
            "label": "State",
            "field_type": "dropdown",
            "is_required": True,
            "options": DEFAULT_STATES,
            "order": 5,
        },
        {
            "name": "district",
            "label": "District",
            "field_type": "text",
            "is_required": True,
            "options": None,
            "order": 6,
        },
        {
            "name": "pincode",
            "label": "Pincode",
            "field_type": "text",
            "is_required": True,
            "options": None,
            "order": 7,
        },
    ]

    for item in defaults:
        if not OnboardingField.objects.filter(name=item["name"]).exists():
            OnboardingField.objects.create(**item)

def remove_default_onboarding_fields(apps, schema_editor):
    OnboardingField = apps.get_model('users', 'OnboardingField')
    OnboardingField.objects.filter(
        name__in=["student_type", "full_name", "phone_number", "email", "state", "district", "pincode"]
    ).delete()

class Migration(migrations.Migration):

    dependencies = [
        ('users', '0010_user_student_id'),
    ]

    operations = [
        migrations.AddField(
            model_name='user',
            name='student_type',
            field=models.CharField(
                blank=True,
                choices=[('CAREER_ACADEMY', 'Career Academy'), ('REGULAR', 'Regular')],
                default='',
                help_text='Primary learning track: Career Academy or Regular',
                max_length=50
            ),
        ),
        migrations.AddField(
            model_name='user',
            name='state',
            field=models.CharField(blank=True, default='', max_length=100),
        ),
        migrations.AddField(
            model_name='user',
            name='district',
            field=models.CharField(blank=True, default='', max_length=100),
        ),
        migrations.AddField(
            model_name='user',
            name='pincode',
            field=models.CharField(blank=True, default='', max_length=20),
        ),
        migrations.RunPython(seed_default_onboarding_fields, remove_default_onboarding_fields),
    ]
