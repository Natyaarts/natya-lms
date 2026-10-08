from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    StudentBillingPlanViewSet,
    StudentInvoiceViewSet,
    ManualPaymentViewSet,
    BillingAccessExtensionViewSet,
    StudentSelfBillingViewSet,
)

router = DefaultRouter()
router.register(r'plans', StudentBillingPlanViewSet, basename='billing-plan')
router.register(r'invoices', StudentInvoiceViewSet, basename='billing-invoice')
router.register(r'manual-payments', ManualPaymentViewSet, basename='billing-manual-payment')
router.register(r'extensions', BillingAccessExtensionViewSet, basename='billing-extension')
router.register(r'my-billing', StudentSelfBillingViewSet, basename='billing-my-billing')

urlpatterns = [
    path('', include(router.urls)),
]
