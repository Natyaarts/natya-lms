from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import LandingPageView, AdminHeroSectionViewSet, AdminFeatureViewSet

router = DefaultRouter()
router.register(r'hero-admin', AdminHeroSectionViewSet, basename='hero-admin')
router.register(r'features-admin', AdminFeatureViewSet, basename='features-admin')

urlpatterns = [
    path('landing-page/', LandingPageView.as_view(), name='landing-page'),
    path('', include(router.urls)),
]
