from django.urls import path
from .views import LandingPageView

urlpatterns = [
    path('landing-page/', LandingPageView.as_view(), name='landing-page'),
]
