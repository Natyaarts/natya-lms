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
    1. Automatically connects Google login to an existing user with the same email,
       bypassing the raw /accounts/3rdparty/signup/ intermediary screen.
    2. Automatically assigns a unique username for new signups so allauth never stops to prompt.
    3. Handles seamless post-login redirection (honors `next` parameter to return students to their course).
    4. Silences notification email failures so mail/credential issues never block OAuth login.
    """

    def pre_social_login(self, request, sociallogin):
        if sociallogin.is_existing:
            return

        # Extract verified email from Google OAuth extra_data or user object
        email = sociallogin.account.extra_data.get('email')
        if not email and sociallogin.user and sociallogin.user.email:
            email = sociallogin.user.email
        if not email and sociallogin.email_addresses:
            email = sociallogin.email_addresses[0].email

        if not email:
            return

        try:
            existing_user = User.objects.filter(email__iexact=email).first()
            if existing_user:
                # Direct bind: connect Google SocialAccount directly to existing user
                sociallogin.user = existing_user
                sociallogin.account.user = existing_user
                sociallogin._did_authenticate_by_email = None

                # Persist or update SocialAccount record
                try:
                    from allauth.socialaccount.models import SocialAccount
                    sa = SocialAccount.objects.filter(
                        provider=sociallogin.account.provider,
                        uid=sociallogin.account.uid
                    ).first()
                    if sa:
                        if sa.user != existing_user:
                            sa.user = existing_user
                        sa.extra_data = sociallogin.account.extra_data
                        sa.save()
                        sociallogin.account = sa
                    else:
                        sociallogin.account.user = existing_user
                        sociallogin.account.save()
                except Exception as save_err:
                    logger.warning(f"Could not persist SocialAccount record directly: {save_err}")

                logger.info(f"Auto-connected Google OAuth to existing user: {existing_user.username} ({email})")
        except Exception as e:
            logger.error(f"Error connecting social account to existing user: {e}")

    def is_auto_signup_allowed(self, request, sociallogin):
        # Always allow automatic signup without prompting the user
        return True

    def populate_user(self, request, sociallogin, data):
        user = super().populate_user(request, sociallogin, data)
        # Ensure a clean, guaranteed unique username is assigned so auto-signup never fails
        email = data.get('email') or sociallogin.account.extra_data.get('email', '')
        base = user.username or (email.split('@')[0] if email else 'student')
        base = re.sub(r'[^a-zA-Z0-9_]', '', base)[:20] or 'student'
        candidate = base
        counter = 1
        while User.objects.filter(username=candidate).exclude(pk=user.pk if user.pk else None).exists():
            candidate = f"{base}_{counter}"
            counter += 1
        user.username = candidate

        # Ensure first/last names are populated if provided by Google
        if not user.first_name:
            user.first_name = sociallogin.account.extra_data.get('given_name', '')[:30]
        if not user.last_name:
            user.last_name = sociallogin.account.extra_data.get('family_name', '')[:30]

        return user

    def send_notification_mail(self, email_template, user, context=None):
        # Suppress any notification mail failures so email issues don't abort OAuth
        try:
            return super().send_notification_mail(email_template, user, context=context)
        except Exception as e:
            logger.warning(f"Ignored social notification mail error: {e}")

    def get_login_redirect_url(self, request):
        next_url = request.GET.get('next') or request.POST.get('next') or request.session.get('next')
        if next_url and (next_url.startswith('/') or next_url.startswith(('https://', 'http://'))):
            return next_url
        return super().get_login_redirect_url(request)

