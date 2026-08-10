from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import CreateOrderView, VerifyPaymentView, AdminPurchaseViewSet

router = DefaultRouter()
router.register(r'purchases-admin', AdminPurchaseViewSet, basename='purchases-admin')

urlpatterns = [
    path('create-order/', CreateOrderView.as_view(), name='create-order'),
    path('verify-payment/', VerifyPaymentView.as_view(), name='verify-payment'),
    path('', include(router.urls)),
]
