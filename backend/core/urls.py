from django.contrib import admin
from django.urls import path, re_path, include
from django.http import HttpResponse, JsonResponse

def health_check(request):
    # Elastic Beanstalk's own load-balancer health check hits this exact
    # path -- deliberately left as a trivial, dependency-free "OK" (see
    # health_check_detailed below for the real, per-dependency check).
    # Changing this to do real work risks the EB health check itself
    # timing out/failing (and taking the whole environment down) if a
    # dependency is merely slow, not actually down.
    return HttpResponse("OK")


def health_check_detailed(request):
    """
    Phase 3.9. A real dependency health check -- DB, Redis (the same
    broker Celery/the cache both use), and Celery worker liveness --
    intended for manual/monitoring use (e.g. an uptime check hitting this
    on a schedule), NOT for the EB load-balancer's own health check (see
    health_check above for why that one stays a bare "OK"). Always
    returns 200 with a per-dependency status field, never a 5xx, so a
    monitoring tool can distinguish "the app is up but a dependency is
    unhealthy" from "the app itself is down" -- an app that can't even
    serve this endpoint is a very different failure than one degraded
    to failing over on 200 makes that operationally clearer to alert on.

    This endpoint is unauthenticated (a monitoring probe can't be expected
    to hold a login), so the per-dependency detail returned to the CALLER
    is deliberately just 'ok'/'error' -- never the raw exception text,
    which could otherwise hand any anonymous caller internal infrastructure
    detail (e.g. the broker's host:port from a connection-refused message).
    The full exception is still logged server-side for real debugging.
    """
    import logging
    from django.conf import settings
    from django.db import connection

    logger = logging.getLogger('core.health')
    checks = {}

    try:
        connection.ensure_connection()
        checks['database'] = 'ok'
    except Exception:
        logger.error("Detailed health check: database dependency failed", exc_info=True)
        checks['database'] = 'error'

    try:
        import redis
        redis.Redis.from_url(settings.CELERY_BROKER_URL, socket_connect_timeout=2).ping()
        checks['redis'] = 'ok'
    except Exception:
        logger.error("Detailed health check: redis dependency failed", exc_info=True)
        checks['redis'] = 'error'

    try:
        from core.celery import app as celery_app
        pings = celery_app.control.inspect(timeout=2).ping()
        checks['celery'] = 'ok' if pings else 'error'
        if not pings:
            logger.warning("Detailed health check: no celery workers responded")
    except Exception:
        logger.error("Detailed health check: celery dependency failed", exc_info=True)
        checks['celery'] = 'error'

    healthy = all(v == 'ok' for v in checks.values())
    return JsonResponse({'status': 'ok' if healthy else 'degraded', 'checks': checks})


from users.views import ThrottledLoginView, ThrottledPasswordResetView

urlpatterns = [
    path('', health_check, name='health_check'),
    path('health/detailed/', health_check_detailed, name='health_check_detailed'),
    path('admin/', admin.site.urls),
    path('api/courses/', include('courses.urls')),
    path('api/orders/', include('orders.urls')),
    path('api/finance/', include('finance.urls')),
    path('api/cms/', include('cms.urls')),
    # Final release-blocker fix: overrides dj_rest_auth.urls' own
    # 'login/' pattern with a throttled subclass (see
    # users.views.ThrottledLoginView) -- same URL, same name ('rest_login',
    # matching dj-rest-auth's own so any existing reverse('rest_login')
    # keeps working), same view behavior otherwise. Must come BEFORE the
    # dj_rest_auth.urls include below: Django's resolver matches
    # urlpatterns top-to-bottom, first match wins, exactly the same
    # ordering requirement already documented in notifications/urls.py
    # for its own device-token/ override.
    #
    # re_path with the EXACT same r'login/?$' shape dj_rest_auth.urls uses
    # internally (confirmed by reading site-packages/dj_rest_auth/urls.py)
    # -- NOT path('api/auth/login/', ...). A plain path() with a literal
    # trailing slash only matches '/api/auth/login/' and lets
    # '/api/auth/login' (no trailing slash) fall through to dj_rest_auth's
    # own optional-slash pattern underneath, completely unthrottled --
    # confirmed by hitting exactly that gap while testing this fix
    # (reverse('rest_login') itself resolved to the no-slash form, and a
    # POST to it bypassed this override entirely).
    re_path(r'^api/auth/login/?$', ThrottledLoginView.as_view(), name='rest_login'),
    # General API rate limiting gap fix -- same override technique/reasoning
    # as ThrottledLoginView immediately above (identical URL-shadowing
    # pitfall avoided the same way: exact r'password/reset/?$' shape,
    # confirmed against dj_rest_auth/urls.py's own pattern, placed before
    # the include() below).
    re_path(r'^api/auth/password/reset/?$', ThrottledPasswordResetView.as_view(), name='rest_password_reset'),
    path('api/auth/', include('dj_rest_auth.urls')),
    path('api/auth/registration/', include('dj_rest_auth.registration.urls')),
    path('api/users/', include('users.urls')),
    path('api/', include('notifications.urls')),
    path('accounts/', include('allauth.urls')),
]

from django.conf import settings
from django.conf.urls.static import static

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
