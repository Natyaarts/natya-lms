import logging
import re
from allauth.account.adapter import DefaultAccountAdapter
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from django.contrib.auth import get_user_model
from django.conf import settings

User = get_user_model()
logger = logging.getLogger(__name__)


def attach_jwt_cookies(response, user):
    """
    Attach dj-rest-auth / SimpleJWT cookies to an HTTP response so that
    cross-origin frontend clients (Next.js on academy.natyaarts.com) are
    immediately authenticated for DRF API calls.
    """
    if not user or not user.is_authenticated:
        return response

    try:
        from datetime import timedelta
        from rest_framework_simplejwt.tokens import RefreshToken
        refresh = RefreshToken.for_user(user)
        access_cookie_key = getattr(settings, 'REST_AUTH', {}).get('JWT_AUTH_COOKIE', 'natya-auth')
        refresh_cookie_key = getattr(settings, 'REST_AUTH', {}).get('JWT_AUTH_REFRESH_COOKIE', 'natya-refresh')
        cookie_domain = getattr(settings, 'COOKIE_DOMAIN', None)

        access_lifetime = getattr(settings, 'SIMPLE_JWT', {}).get('ACCESS_TOKEN_LIFETIME', timedelta(days=1))
        refresh_lifetime = getattr(settings, 'SIMPLE_JWT', {}).get('REFRESH_TOKEN_LIFETIME', timedelta(days=30))
        access_max_age = int(access_lifetime.total_seconds()) if hasattr(access_lifetime, 'total_seconds') else 86400
        refresh_max_age = int(refresh_lifetime.total_seconds()) if hasattr(refresh_lifetime, 'total_seconds') else 2592000

        response.set_cookie(
            access_cookie_key,
            str(refresh.access_token),
            max_age=access_max_age,
            httponly=True,
            samesite='None',
            secure=True,
            domain=cookie_domain
        )
        response.set_cookie(
            refresh_cookie_key,
            str(refresh),
            max_age=refresh_max_age,
            httponly=True,
            samesite='None',
            secure=True,
            domain=cookie_domain
        )
        logger.info(f"Attached JWT auth cookies for user: {user.username}")
    except Exception as e:
        logger.error(f"Error attaching JWT cookies: {e}")
    return response


class CustomAccountAdapter(DefaultAccountAdapter):
    """
    Custom account adapter that attaches JWT cookies on allauth post_login,
    bridging allauth web logins to dj-rest-auth / SimpleJWT for the Next.js frontend.
    """

    def post_login(self, request, user, **kwargs):
        frontend_url = getattr(settings, 'FRONTEND_URL', 'https://academy.natyaarts.com').rstrip('/')
        redirect_url = kwargs.get('redirect_url')
        if redirect_url and redirect_url.startswith('/'):
            kwargs['redirect_url'] = f"{frontend_url}{redirect_url}"
        elif not redirect_url:
            kwargs['redirect_url'] = f"{frontend_url}/dashboard"

        response = super().post_login(request, user, **kwargs)

        if hasattr(response, 'has_header') and response.has_header('Location'):
            loc = response['Location']
            if loc.startswith('/'):
                response['Location'] = f"{frontend_url}{loc}"

        return attach_jwt_cookies(response, user)

    def add_message(self, request, level, template_name, context=None, extra_tags="", **kwargs):
        try:
            super().add_message(request, level, template_name, context=context, extra_tags=extra_tags, **kwargs)
        except Exception:
            pass

    def get_login_redirect_url(self, request):
        session_next = request.session.get('next') if hasattr(request, 'session') else None
        next_url = request.GET.get('next') or request.POST.get('next') or session_next
        frontend_url = getattr(settings, 'FRONTEND_URL', 'https://academy.natyaarts.com').rstrip('/')
        if next_url:
            if next_url.startswith('/'):
                return f"{frontend_url}{next_url}"
            if next_url.startswith(('https://', 'http://')):
                return next_url
        return f"{frontend_url}/dashboard"


class CustomSocialAccountAdapter(DefaultSocialAccountAdapter):
    """
    Custom social account adapter for Google OAuth:
    1. Automatically connects Google login to an existing user with the same email,
       bypassing the raw /accounts/3rdparty/signup/ intermediary screen.
    2. Automatically assigns a unique username for new signups so allauth never stops to prompt.
    3. Handles seamless post-login redirection to the frontend web app.
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

    def get_connect_redirect_url(self, request, socialaccount):
        frontend_url = getattr(settings, 'FRONTEND_URL', 'https://academy.natyaarts.com').rstrip('/')
        return f"{frontend_url}/dashboard"

    def on_authentication_error(self, request, provider, error=None, exception=None, extra_context=None):
        logger.error(f"Social authentication error for {provider}: error={error}, exception={exception}")
        from allauth.exceptions import ImmediateHttpResponse
        from django.http import HttpResponseRedirect
        frontend_url = getattr(settings, 'FRONTEND_URL', 'https://academy.natyaarts.com').rstrip('/')
        raise ImmediateHttpResponse(HttpResponseRedirect(f"{frontend_url}/login?error=oauth_failed"))

    def get_login_redirect_url(self, request):
        session_next = request.session.get('next') if hasattr(request, 'session') else None
        next_url = request.GET.get('next') or request.POST.get('next') or session_next
        frontend_url = getattr(settings, 'FRONTEND_URL', 'https://academy.natyaarts.com').rstrip('/')
        if next_url:
            if next_url.startswith('/'):
                return f"{frontend_url}{next_url}"
            if next_url.startswith(('https://', 'http://')):
                return next_url
        return f"{frontend_url}/dashboard"



