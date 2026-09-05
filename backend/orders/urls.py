from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    CreateOrderView, VerifyPaymentView, AdminPurchaseViewSet, RazorpayWebhookView,
    BundleViewSet, OrderViewSet, CreateSubscriptionView, VerifySubscriptionPaymentView,
    SubscriptionMeView, CancelSubscriptionView, SubscriptionPlanViewSet, SubscriptionPaymentHistoryView,
)

router = DefaultRouter()
router.register(r'purchases-admin', AdminPurchaseViewSet, basename='purchases-admin')
router.register(r'bundles', BundleViewSet, basename='bundle')
router.register(r'orders', OrderViewSet, basename='order')
# Phase 3.4.6: public plan catalog, read-only (ReadOnlyModelViewSet) --
# distinct path segment ('subscription-plans') from the plain
# 'subscriptions/*' APIView paths below, so there is no route collision.
router.register(r'subscription-plans', SubscriptionPlanViewSet, basename='subscription-plan')

urlpatterns = [
    path('create-order/', CreateOrderView.as_view(), name='create-order'),
    path('verify-payment/', VerifyPaymentView.as_view(), name='verify-payment'),
    path('webhook/razorpay/', RazorpayWebhookView.as_view(), name='razorpay-webhook'),
    # Phase 3.4.2/3.4.5/3.4.6: intentionally plain APIView paths (not
    # router-registered ViewSet actions) -- mirrors create-order/
    # verify-payment's shape. Still no full SubscriptionViewSet for
    # list/retrieve of historical subscriptions or admin management --
    # these are exactly what student-facing checkout, cancellation, and
    # the "my subscription"/payment-history read API need, nothing more.
    path('subscriptions/create/', CreateSubscriptionView.as_view(), name='create-subscription'),
    path('subscriptions/verify/', VerifySubscriptionPaymentView.as_view(), name='verify-subscription'),
    path('subscriptions/me/', SubscriptionMeView.as_view(), name='subscription-me'),
    path('subscriptions/cancel/', CancelSubscriptionView.as_view(), name='cancel-subscription'),
    path('subscriptions/payments/', SubscriptionPaymentHistoryView.as_view(), name='subscription-payments'),
    path('', include(router.urls)),
]
