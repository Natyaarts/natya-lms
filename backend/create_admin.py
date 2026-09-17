import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')
django.setup()

from django.contrib.auth import get_user_model

# Production Environment Verification gap fix: this script previously
# hardcoded username='admin', email='admin@natya.com',
# password='AdminPassword123!' in source -- a real, source-visible,
# guessable superuser credential, auto-created on every leader-only deploy
# (.ebextensions/02_django.config's `03_create_superuser` container
# command) whenever no user named 'admin' already existed. Now reads the
# same three env var names Django's own `createsuperuser --noinput`
# convention already uses, and does nothing (rather than falling back to
# a hardcoded password) if DJANGO_SUPERUSER_PASSWORD isn't set -- no
# credential is invented here.
User = get_user_model()
username = os.environ.get('DJANGO_SUPERUSER_USERNAME', 'admin')
email = os.environ.get('DJANGO_SUPERUSER_EMAIL', 'admin@natya.com')
password = os.environ.get('DJANGO_SUPERUSER_PASSWORD')

if not password:
    print(
        "DJANGO_SUPERUSER_PASSWORD is not set -- skipping bootstrap superuser creation. "
        "Set DJANGO_SUPERUSER_USERNAME/DJANGO_SUPERUSER_EMAIL/DJANGO_SUPERUSER_PASSWORD in the "
        "deployment environment if a bootstrap admin account is needed, or create one manually."
    )
elif not User.objects.filter(username=username).exists():
    User.objects.create_superuser(username=username, email=email, password=password)
    print(f"Superuser '{username}' created successfully.")
else:
    print(f"Superuser '{username}' already exists.")
