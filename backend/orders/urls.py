from django.urls import path
from .views import CreateOrderView, VerifyPaymentView

urlpatterns = [
    path('create/', CreateOrderView.as_view(), name='create_order'),
    path('verify/', VerifyPaymentView.as_view(), name='verify_payment'),
]
