import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')
django.setup()

from django.contrib.sites.models import Site
from allauth.socialaccount.models import SocialApp

# 1. Update Site
site, created = Site.objects.get_or_create(id=1)
site.domain = '127.0.0.1:8000'
site.name = 'Natya LMS'
site.save()

# 2. Create or Update SocialApp for Google
app, created = SocialApp.objects.get_or_create(provider='google', defaults={'name': 'Google Auth'})
app.client_id = 'YOUR_GOOGLE_CLIENT_ID'
app.secret = 'YOUR_GOOGLE_CLIENT_SECRET'
app.save()

# 3. Link app to site
app.sites.add(site)
print("SUCCESSFULLY CONFIGURED GOOGLE AUTH!")
