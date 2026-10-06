import logging
import re
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from django.contrib.auth import get_user_model
from django.conf import settings

User = get_user_model()
logger = logging.getLogger(__name__)


class CustomSocialAccountAdapter(DefaultSocialAccountAdapter):
    """
    Custom social account adapter for Google OAuth:
    1. Automatically connects Google login to an existing user with the same verified email,
       bypassing the raw /accounts/3rdparty/signup/ intermediary screen.
    2. Automatically assigns a unique username for new signups so allauth never stops to prompt.
    3. Handles seamless post-login redirection (honors `next` parameter to return students to their course).
    """

    def pre_social_login(self, request, sociallogin):
        if sociallogin.is_existing:
            return

        # Extract verified email from Google OAuth extra_data or user object
        email = sociallogin.account.extra_data.get('email')
        if not email and sociallogin.user and sociallogin.user.email:
            email = sociallogin.user.email

        if email:
            try:
                existing_user = User.objects.filter(email__iexact=email).first()
                if existing_user:
                    # Connect Google account directly to this user and proceed with login
                    sociallogin.connect(request, existing_user)
                    logger.info(f"Auto-connected Google OAuth to existing user: {existing_user.username} ({email})")
            except Exception as e:
                logger.error(f"Error connecting social account to existing user: {e}")

    def populate_user(self, request, sociallogin, data):
        user = super().populate_user(request, sociallogin, data)
        # Ensure a clean, unique username is assigned so auto-signup never fails
        if not user.username:
            email = data.get('email') or sociallogin.account.extra_data.get('email', '')
            base = email.split('@')[0] if email else 'student'
            base = re.sub(r'[^a-zA-Z0-9_]', '', base)[:20] or 'student'
            candidate = base
            counter = 1
            while User.objects.filter(username=candidate).exists():
                candidate = f"{base}_{counter}"
                counter += 1
            user.username = candidate

        # Ensure first/last names are populated if provided by Google
        if not user.first_name:
            user.first_name = sociallogin.account.extra_data.get('given_name', '')[:30]
        if not user.last_name:
            user.last_name = sociallogin.account.extra_data.get('family_name', '')[:30]

        return user

    def get_login_redirect_url(self, request):
        next_url = request.GET.get('next') or request.POST.get('next') or request.session.get('next')
        if next_url and (next_url.startswith('/') or next_url.startswith(('https://', 'http://'))):
            return next_url
        return super().get_login_redirect_url(request)
