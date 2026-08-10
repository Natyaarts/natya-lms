from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import viewsets
from users.permissions import IsSuperAdmin
from .models import HeroSection, Feature
from .serializers import HeroSectionSerializer, FeatureSerializer

class LandingPageView(APIView):
    def get(self, request, *args, **kwargs):
        # Get or create the first HeroSection
        hero, created = HeroSection.objects.get_or_create(id=1)
        features = Feature.objects.all()
        
        return Response({
            'hero': HeroSectionSerializer(hero).data,
            'features': FeatureSerializer(features, many=True).data
        })

class AdminHeroSectionViewSet(viewsets.ModelViewSet):
    queryset = HeroSection.objects.all()
    serializer_class = HeroSectionSerializer
    permission_classes = [IsSuperAdmin]

class AdminFeatureViewSet(viewsets.ModelViewSet):
    queryset = Feature.objects.all().order_by('order')
    serializer_class = FeatureSerializer
    permission_classes = [IsSuperAdmin]
