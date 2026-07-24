from rest_framework.views import APIView
from rest_framework.response import Response
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
