from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from django.utils import timezone
from datetime import timedelta
import random
from .models import OTPVerification, User
from rest_framework_simplejwt.tokens import RefreshToken

import requests

class SendOTPView(APIView):
    def post(self, request):
        identifier = request.data.get('identifier')
        if not identifier:
            return Response({"error": "Email or Mobile Number is required"}, status=status.HTTP_400_BAD_REQUEST)
        
        # Generate 6-digit OTP
        otp = str(random.randint(100000, 999999))
        
        # Save to DB
        OTPVerification.objects.create(identifier=identifier, otp=otp)
        
        # Send OTP
        if '@' in identifier:
            # TODO: Integrate AWS SES via boto3 or django-ses
            print(f"*** AWS SES MOCK: Sending Email to {identifier} with OTP: {otp} ***")
        else:
            # --- INTERAKT WHATSAPP INTEGRATION ---
            from django.conf import settings
            
            INTERAKT_SECRET_KEY = settings.INTERAKT_SECRET_KEY
            TEMPLATE_NAME = settings.INTERAKT_TEMPLATE_NAME
            
            headers = {
                "Authorization": f"Basic {INTERAKT_SECRET_KEY}",
                "Content-Type": "application/json"
            }
            
            payload = {
                "fullPhoneNumber": identifier,
                "type": "Template",
                "template": {
                    "name": TEMPLATE_NAME,
                    "languageCode": "en",
                    "bodyValues": [otp],
                    "buttonValues": {"0": [otp]}
                }
            }
            
            print(f"*** Sending WhatsApp via Interakt to {identifier} with OTP: {otp} ***")
            response = requests.post("https://api.interakt.ai/v1/public/message/", json=payload, headers=headers)
            print("Interakt Response:", response.json())
            
        return Response({"message": "OTP sent successfully"})

class VerifyOTPView(APIView):
    def post(self, request):
        identifier = request.data.get('identifier')
        otp = request.data.get('otp')
        
        if not identifier or not otp:
            return Response({"error": "Identifier and OTP required"}, status=status.HTTP_400_BAD_REQUEST)
            
        # Check if OTP is valid and not expired (5 minutes)
        time_threshold = timezone.now() - timedelta(minutes=5)
        otp_record = OTPVerification.objects.filter(
            identifier=identifier, 
            otp=otp, 
            is_verified=False,
            created_at__gte=time_threshold
        ).last()
        
        if not otp_record:
            return Response({"error": "Invalid or expired OTP"}, status=status.HTTP_400_BAD_REQUEST)
            
        otp_record.is_verified = True
        otp_record.save()
        
        # Get or create user
        if '@' in identifier:
            user, created = User.objects.get_or_create(email=identifier, defaults={'username': identifier.split('@')[0] + str(random.randint(1000, 9999))})
        else:
            user, created = User.objects.get_or_create(phone_number=identifier, defaults={'username': identifier})
            
        # Generate JWT Tokens
        refresh = RefreshToken.for_user(user)
        
        response = Response({
            "message": "Login successful",
            "user_id": user.id,
            "created": created
        })
        
        # Set JWT Cookies for dj-rest-auth
        from django.conf import settings
        access_cookie_key = getattr(settings, 'REST_AUTH', {}).get('JWT_AUTH_COOKIE', 'natya-auth')
        refresh_cookie_key = getattr(settings, 'REST_AUTH', {}).get('JWT_AUTH_REFRESH_COOKIE', 'natya-refresh')
        
        response.set_cookie(
            access_cookie_key,
            str(refresh.access_token),
            httponly=True,
            samesite='Lax'
        )
        response.set_cookie(
            refresh_cookie_key,
            str(refresh),
            httponly=True,
            samesite='Lax'
        )
        
        return response

from rest_framework import viewsets
from rest_framework.decorators import action
from .serializers import AdminUserSerializer
from .permissions import IsSuperAdmin

class AdminUserViewSet(viewsets.ModelViewSet):
    queryset = User.objects.all().order_by('-date_joined')
    serializer_class = AdminUserSerializer
    permission_classes = [IsSuperAdmin]

    @action(detail=True, methods=['get'])
    def courses(self, request, pk=None):
        user = self.get_object()
        from orders.models import Purchase
        # A user's assigned courses can be fetched from their purchases
        purchases = Purchase.objects.filter(user=user).select_related('course')
        data = []
        for p in purchases:
            data.append({
                "id": p.id,
                "course_id": p.course.id,
                "title": p.course.title,
                "assigned_at": p.created_at
            })
        # Deduplicate courses if they bought it multiple times
        unique_courses = { c['course_id']: c for c in data }.values()
        return Response(list(unique_courses))

    @action(detail=True, methods=['get'])
    def purchases(self, request, pk=None):
        user = self.get_object()
        from orders.models import Purchase
        purchases = Purchase.objects.filter(user=user).select_related('course').order_by('-created_at')
        data = []
        for p in purchases:
            data.append({
                "id": p.id,
                "course_title": p.course.title,
                "amount": p.amount,
                "status": p.status,
                "created_at": p.created_at
            })
        return Response(data)

    @action(detail=True, methods=['post'])
    def assign_course(self, request, pk=None):
        user = self.get_object()
        course_id = request.data.get('course_id')
        payment_status = request.data.get('payment_status', 'SUCCESS') # SUCCESS or PENDING
        
        if not course_id:
            return Response({"error": "course_id is required"}, status=status.HTTP_400_BAD_REQUEST)
        
        from courses.models import Course
        from orders.models import Purchase
        
        try:
            course = Course.objects.get(id=course_id)
        except Course.DoesNotExist:
            return Response({"error": "Course not found"}, status=status.HTTP_404_NOT_FOUND)
            
        # Check if already purchased
        if Purchase.objects.filter(user=user, course=course, status='SUCCESS').exists():
            return Response({"error": "User already has this course"}, status=status.HTTP_400_BAD_REQUEST)
            
        amount = request.data.get('amount', course.price)
            
        Purchase.objects.create(
            user=user,
            course=course,
            amount=amount,
            status=payment_status
        )
        return Response({"message": f"Successfully assigned {course.title} to {user.username}"})

    @action(detail=True, methods=['post'])
    def mark_purchase_paid(self, request, pk=None):
        user = self.get_object()
        purchase_id = request.data.get('purchase_id')
        
        if not purchase_id:
            return Response({"error": "purchase_id is required"}, status=status.HTTP_400_BAD_REQUEST)
            
        from orders.models import Purchase
        try:
            purchase = Purchase.objects.get(id=purchase_id, user=user)
            purchase.status = 'SUCCESS'
            purchase.save()
            return Response({"message": "Successfully marked as paid!"})
        except Purchase.DoesNotExist:
            return Response({"error": "Purchase record not found"}, status=status.HTTP_404_NOT_FOUND)

    @action(detail=True, methods=['post'])
    def unassign_course(self, request, pk=None):
        user = self.get_object()
        course_id = request.data.get('course_id')
        
        if not course_id:
            return Response({"error": "course_id is required"}, status=status.HTTP_400_BAD_REQUEST)
            
        from orders.models import Purchase
        
        # Delete all purchase records for this course for this user
        deleted, _ = Purchase.objects.filter(user=user, course_id=course_id).delete()
        
        if deleted:
            return Response({"message": "Successfully unassigned the course."})
        else:
            return Response({"error": "The user is not assigned to this course."}, status=status.HTTP_404_NOT_FOUND)

from django.db.models import Sum
from courses.models import Course
from orders.models import Purchase

class AdminStatsView(APIView):
    permission_classes = [IsSuperAdmin]

    def get(self, request):
        total_students = User.objects.filter(is_student=True, is_teacher=False, is_superuser=False).count()
        active_courses = Course.objects.filter(is_published=True).count()
        revenue = Purchase.objects.filter(status='SUCCESS').aggregate(total=Sum('amount'))['total'] or 0.00
        
        return Response({
            "total_students": total_students,
            "active_courses": active_courses,
            "total_revenue": float(revenue)
        })
