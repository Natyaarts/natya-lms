import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')
django.setup()

from django.contrib.auth import get_user_model
User = get_user_model()
u = User.objects.get(username='natyaadmin')
u.set_password('NatyaLMS2026Secure')
u.save()
print("Superuser password set successfully!")
