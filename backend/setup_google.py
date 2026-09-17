"""
Production Environment Verification follow-up.

This script previously WROTE a DB-backed SocialApp row containing literal
placeholder strings ('YOUR_GOOGLE_CLIENT_ID'/'YOUR_GOOGLE_CLIENT_SECRET')
-- never a real credential, and dangerous if it was ever run against a
real database, since it would leave a broken 'google' SocialApp sitting
there.

Google OAuth for web login is now configured via environment variables
(GOOGLE_OAUTH_CLIENT_ID/GOOGLE_OAUTH_CLIENT_SECRET, read by
SOCIALACCOUNT_PROVIDERS['google']['APP'] in core/settings.py) -- no
Django-admin or manual DB step is needed for a fresh deploy.

allauth blends DB-backed and settings-backed provider apps into one list
(see allauth.socialaccount.adapter.DefaultSocialAccountAdapter.list_apps),
so a leftover DB 'google' SocialApp row (from this script's old behavior,
or from a manual Django-admin edit) would make allauth see TWO apps for
'google' and raise MultipleObjectsReturned at every Google login attempt,
once the settings-based one is also configured. This script now checks
for and removes any such row instead of creating one -- it invents no
credential, and is safe to run more than once (a no-op if nothing is
found). It is NOT wired into any deploy hook (.ebextensions/Procfile) --
run it manually only if you suspect a legacy row exists, e.g.:
    python manage.py shell -c "exec(open('setup_google.py').read())"
"""
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')
django.setup()

from allauth.socialaccount.models import SocialApp

legacy_apps = SocialApp.objects.filter(provider='google')
count = legacy_apps.count()
if count:
    for app in legacy_apps:
        print(f"Removing legacy DB-backed Google SocialApp id={app.id} name={app.name!r} client_id={app.client_id!r}")
    legacy_apps.delete()
    print(f"Removed {count} legacy Google SocialApp row(s). Web Google login now relies entirely on "
          f"GOOGLE_OAUTH_CLIENT_ID/GOOGLE_OAUTH_CLIENT_SECRET (see core/settings.py).")
else:
    print("No legacy DB-backed Google SocialApp row found -- nothing to do.")
