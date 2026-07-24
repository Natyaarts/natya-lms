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
