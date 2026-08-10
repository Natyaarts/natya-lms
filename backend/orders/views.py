import razorpay
from django.conf import settings
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework import status
from django.shortcuts import get_object_or_404
from courses.models import Course, Enrollment
from .models import Purchase

client = razorpay.Client(auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET))

from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from rest_framework.authentication import SessionAuthentication
from dj_rest_auth.jwt_auth import JWTCookieAuthentication

class CsrfExemptSessionAuthentication(SessionAuthentication):
    def enforce_csrf(self, request):
        return  # Bypass CSRF

from rest_framework.permissions import IsAuthenticated, AllowAny

@method_decorator(csrf_exempt, name='dispatch')
class CreateOrderView(APIView):
    authentication_classes = [JWTCookieAuthentication, CsrfExemptSessionAuthentication]
    permission_classes = [AllowAny] # Temporarily AllowAny for local testing

    def post(self, request):
        print(f"USING RAZORPAY KEY ID: {settings.RAZORPAY_KEY_ID}")
        
        course_id = request.data.get('course_id')
        if not course_id:
            return Response({"error": "course_id is required"}, status=status.HTTP_400_BAD_REQUEST)
            
        course = get_object_or_404(Course, id=course_id)
        
        # Razorpay expects amount in paise (multiply by 100)
        amount_in_paise = int(course.price * 100)
        
        try:
            # Create Razorpay Order
            razorpay_order = client.order.create({
                "amount": amount_in_paise,
                "currency": "INR",
                "payment_capture": "1" # Auto capture
            })
            
            # Fallback for local testing if cookie is blocked
            from django.contrib.auth import get_user_model
            purchase_user = request.user
            if purchase_user.is_anonymous:
                purchase_user = get_user_model().objects.first()

            # Create Purchase record
            purchase = Purchase.objects.create(
                user=purchase_user,
                course=course,
                razorpay_order_id=razorpay_order['id'],
                amount=course.price,
                status='PENDING'
            )
            
            return Response({
                "order_id": razorpay_order['id'],
                "amount": amount_in_paise,
                "currency": "INR",
                "key_id": settings.RAZORPAY_KEY_ID
            })
            
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

@method_decorator(csrf_exempt, name='dispatch')
class VerifyPaymentView(APIView):
    authentication_classes = [JWTCookieAuthentication, CsrfExemptSessionAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request):
        razorpay_payment_id = request.data.get('razorpay_payment_id')
        razorpay_order_id = request.data.get('razorpay_order_id')
        razorpay_signature = request.data.get('razorpay_signature')
        
        if not all([razorpay_payment_id, razorpay_order_id, razorpay_signature]):
            return Response({"error": "Missing payment details"}, status=status.HTTP_400_BAD_REQUEST)
            
        try:
            purchase = Purchase.objects.get(razorpay_order_id=razorpay_order_id, user=request.user)
            
            # Verify Signature
            params_dict = {
                'razorpay_order_id': razorpay_order_id,
                'razorpay_payment_id': razorpay_payment_id,
                'razorpay_signature': razorpay_signature
            }
            client.utility.verify_payment_signature(params_dict)
            
            # If successful (no exception thrown):
            purchase.razorpay_payment_id = razorpay_payment_id
            purchase.razorpay_signature = razorpay_signature
            purchase.status = 'SUCCESS'
            purchase.save()
            
            # Create Enrollment
            Enrollment.objects.get_or_create(user=request.user, course=purchase.course)
            
            return Response({"message": "Payment verified and course enrolled!"})
            
        except razorpay.errors.SignatureVerificationError:
            if 'purchase' in locals():
                purchase.status = 'FAILED'
                purchase.save()
            return Response({"error": "Invalid Payment Signature"}, status=status.HTTP_400_BAD_REQUEST)
        except Purchase.DoesNotExist:
            return Response({"error": "Order not found"}, status=status.HTTP_404_NOT_FOUND)
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.pagination import PageNumberPagination
from users.permissions import IsSuperAdmin
from .serializers import AdminPurchaseSerializer

class StandardResultsSetPagination(PageNumberPagination):
    page_size = 10
    page_size_query_param = 'page_size'
    max_page_size = 100

class AdminPurchaseViewSet(viewsets.ModelViewSet):
    queryset = Purchase.objects.all().select_related('user', 'course').order_by('-created_at')
    serializer_class = AdminPurchaseSerializer
    permission_classes = [IsSuperAdmin]
    pagination_class = StandardResultsSetPagination

    def get_queryset(self):
        queryset = Purchase.objects.all().select_related('user', 'course').order_by('-created_at')
        status_param = self.request.query_params.get('status')
        search_param = self.request.query_params.get('search')
        
        if status_param:
            queryset = queryset.filter(status=status_param)
        if search_param:
            from django.db.models import Q
            queryset = queryset.filter(
                Q(user__username__icontains=search_param) |
                Q(user__first_name__icontains=search_param) |
                Q(user__last_name__icontains=search_param) |
                Q(user__email__icontains=search_param) |
                Q(course__title__icontains=search_param) |
                Q(razorpay_order_id__icontains=search_param) |
                Q(razorpay_payment_id__icontains=search_param)
            )
        return queryset

    @action(detail=True, methods=['post'])
    def mark_paid(self, request, pk=None):
        purchase = self.get_object()
        purchase.status = 'SUCCESS'
        purchase.save()
        
        # Triggers standard enrollment behavior:
        from courses.models import Enrollment
        Enrollment.objects.get_or_create(user=purchase.user, course=purchase.course)
        
        return Response({"message": "Successfully marked as paid and course enrolled!"})
