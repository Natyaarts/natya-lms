from rest_framework.views import APIView
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework import status
from django.utils import timezone
from datetime import timedelta
import random
from .models import OTPVerification, User
from rest_framework_simplejwt.tokens import RefreshToken

import requests

class SendOTPView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request):
        identifier = request.data.get('identifier')
        if not identifier:
            return Response({"error": "Email or Mobile Number is required"}, status=status.HTTP_400_BAD_REQUEST)
        
        # Generate 6-digit OTP
        otp = str(random.randint(100000, 999999))
        
        # Save to DB
        OTPVerification.objects.create(identifier=identifier, otp=otp)
        
        # Send OTP
        if identifier == "+919999999999":
            pass # Bypass actual sending for Google Play reviewers
        elif '@' in identifier:
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
            
            # Interakt requires the phone number without the '+' sign
            formatted_number = identifier.lstrip('+')
            payload = {
                "fullPhoneNumber": formatted_number,
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
    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request):
        identifier = request.data.get('identifier')
        otp = request.data.get('otp')
        
        if not identifier or not otp:
            return Response({"error": "Identifier and OTP required"}, status=status.HTTP_400_BAD_REQUEST)
            
        if identifier == "+919999999999" and otp == "123456":
            pass # Bypass OTP check for Google Play reviewers
        else:
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
            "created": created,
            "is_onboarded": user.is_onboarded,
            "tokens": {
                "access": str(refresh.access_token),
                "refresh": str(refresh)
            }
        })
        
        # Set JWT Cookies for dj-rest-auth
        from django.conf import settings
        access_cookie_key = getattr(settings, 'REST_AUTH', {}).get('JWT_AUTH_COOKIE', 'natya-auth')
        refresh_cookie_key = getattr(settings, 'REST_AUTH', {}).get('JWT_AUTH_REFRESH_COOKIE', 'natya-refresh')
        
        response.set_cookie(
            access_cookie_key,
            str(refresh.access_token),
            httponly=True,
            samesite='None',
            secure=True,
            domain=getattr(settings, 'SESSION_COOKIE_DOMAIN', None)
        )
        response.set_cookie(
            refresh_cookie_key,
            str(refresh),
            httponly=True,
            samesite='None',
            secure=True,
            domain=getattr(settings, 'SESSION_COOKIE_DOMAIN', None)
        )
        
        return response

from rest_framework import status, viewsets, permissions
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
        from courses.models import Enrollment
        
        purchases = Purchase.objects.filter(user=user, status='SUCCESS').select_related('course')
        enrollments = Enrollment.objects.filter(user=user).select_related('course')
        
        data = []
        for p in purchases:
            data.append({
                "id": f"p_{p.id}",
                "course_id": p.course.id,
                "title": p.course.title,
                "thumbnail": request.build_absolute_uri(p.course.thumbnail.url) if p.course.thumbnail else None,
                "assigned_at": p.created_at
            })
        for e in enrollments:
            data.append({
                "id": f"e_{e.id}",
                "course_id": e.course.id,
                "title": e.course.title,
                "thumbnail": request.build_absolute_uri(e.course.thumbnail.url) if e.course.thumbnail else None,
                "assigned_at": e.enrolled_at
            })
            
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
            previous_status = purchase.status
            purchase.status = 'SUCCESS'
            purchase.save()

            from notifications.services import NotificationService
            NotificationService.trigger_payment_success(purchase, previous_status)

            return Response({"message": "Successfully marked as paid!"})
        except Purchase.DoesNotExist:
            return Response({"error": "Purchase record not found"}, status=status.HTTP_404_NOT_FOUND)

    @action(detail=True, methods=['post'])
    def unassign_course(self, request, pk=None):
        user = self.get_object()
        course_id = request.data.get('course_id')
        
        if not course_id:
            return Response({"error": "course_id is required"}, status=status.HTTP_400_BAD_REQUEST)
            
        from courses.models import Enrollment
        
        # Delete only enrollment records (keep purchase log intact)
        deleted_enrollments, _ = Enrollment.objects.filter(user=user, course_id=course_id).delete()
        
        if deleted_enrollments:
            return Response({"message": "Successfully unassigned the course."})
        else:
            return Response({"error": "The user is not assigned to this course."}, status=status.HTTP_404_NOT_FOUND)

    @action(detail=True, methods=['post'])
    def enroll_course(self, request, pk=None):
        user = self.get_object()
        course_id = request.data.get('course_id')
        
        if not course_id:
            return Response({"error": "course_id is required"}, status=status.HTTP_400_BAD_REQUEST)
            
        from courses.models import Course, Enrollment
        course = get_object_or_404(Course, id=course_id)
        
        Enrollment.objects.get_or_create(user=user, course=course)
        return Response({"message": f"Successfully enrolled {user.username} in {course.title}."})

    @action(detail=True, methods=['get'])
    def teacher_students(self, request, pk=None):
        teacher = self.get_object()
        if not teacher.is_teacher:
            return Response({"error": "User is not a teacher"}, status=status.HTTP_400_BAD_REQUEST)
            
        from courses.models import Course
        from django.db.models import Q
        
        # Get courses assigned to this teacher
        teacher_courses = Course.objects.filter(
            Q(enrollments__user=teacher)
        ).distinct()
        
        # Get students enrolled in these courses
        students = User.objects.filter(
            is_student=True,
            is_teacher=False,
            is_superuser=False
        ).filter(
            Q(enrollments__course__in=teacher_courses)
        ).distinct().order_by('-date_joined')
        
        serializer = self.get_serializer(students, many=True)
        return Response(serializer.data)

from django.db.models import Sum
from courses.models import Course
from orders.models import Purchase

class AdminStatsView(APIView):
    permission_classes = [IsSuperAdmin]

    def get(self, request):
        from django.utils import timezone
        import datetime
        from django.db.models import Sum, Count, OuterRef, Exists
        from django.db.models.functions import TruncMonth
        from courses.models import Course, Enrollment
        from orders.models import Purchase

        now = timezone.now()
        start_of_week = now - datetime.timedelta(days=7)
        start_of_month = now - datetime.timedelta(days=30)

        # Users
        total_students = User.objects.filter(is_student=True, is_teacher=False, is_superuser=False).count()
        new_students_week = User.objects.filter(is_student=True, is_teacher=False, is_superuser=False, date_joined__gte=start_of_week).count()
        new_students_month = User.objects.filter(is_student=True, is_teacher=False, is_superuser=False, date_joined__gte=start_of_month).count()
        active_students = User.objects.filter(is_student=True, is_teacher=False, is_superuser=False, is_active=True).count()
        inactive_students = User.objects.filter(is_student=True, is_teacher=False, is_superuser=False, is_active=False).count()
        total_teachers = User.objects.filter(is_teacher=True, is_superuser=False).count()

        # Courses
        total_courses = Course.objects.count()
        active_courses = Course.objects.filter(is_published=True).count()
        draft_courses = Course.objects.filter(is_published=False).count()
        
        top_courses_qs = Course.objects.annotate(enrollment_count=Count('enrollments')).order_by('-enrollment_count')[:5]
        top_courses = []
        for c in top_courses_qs:
            top_courses.append({
                "id": c.id,
                "title": c.title,
                "enrollments": c.enrollment_count
            })

        # Payments & Revenue
        revenue = Purchase.objects.filter(status='SUCCESS').aggregate(total=Sum('amount'))['total'] or 0.00
        current_month_revenue = Purchase.objects.filter(status='SUCCESS', created_at__gte=start_of_month).aggregate(total=Sum('amount'))['total'] or 0.00
        
        success_payments = Purchase.objects.filter(status='SUCCESS').count()
        pending_payments = Purchase.objects.filter(status='PENDING').count()
        failed_payments = Purchase.objects.filter(status='FAILED').count()

        # Monthly breakdown
        monthly_rev = Purchase.objects.filter(status='SUCCESS') \
            .annotate(month=TruncMonth('created_at')) \
            .values('month') \
            .annotate(total=Sum('amount')) \
            .order_by('month')
        revenue_breakdown = []
        for item in monthly_rev:
            month_date = item['month']
            month_str = month_date.strftime("%B %Y") if month_date else "Unknown"
            revenue_breakdown.append({
                "month": month_str,
                "total": float(item['total'] or 0.00)
            })

        # Enrollments
        total_enrollments = Enrollment.objects.count()
        new_enrollments_month = Enrollment.objects.filter(enrolled_at__gte=start_of_month).count()
        
        purchases = Purchase.objects.filter(
            user_id=OuterRef('user_id'),
            course_id=OuterRef('course_id'),
            status='SUCCESS'
        )
        paid_enrollments_count = Enrollment.objects.filter(Exists(purchases)).count()
        manual_enrollments_count = Enrollment.objects.filter(~Exists(purchases)).count()

        # Recent Activity
        recent_reg_qs = User.objects.filter(is_student=True, is_teacher=False, is_superuser=False).order_by('-date_joined')[:5]
        recent_registrations = []
        for u in recent_reg_qs:
            name = f"{u.first_name} {u.last_name}".strip() or u.username
            recent_registrations.append({
                "username": u.username,
                "name": name,
                "email": u.email,
                "date_joined": u.date_joined
            })

        recent_pay_qs = Purchase.objects.select_related('user', 'course').order_by('-created_at')[:5]
        recent_payments = []
        for p in recent_pay_qs:
            name = f"{p.user.first_name} {p.user.last_name}".strip() or p.user.username
            recent_payments.append({
                "id": p.id,
                "student_name": name,
                "course_title": p.course.title,
                "amount": float(p.amount),
                "status": p.status,
                "created_at": p.created_at
            })

        recent_enroll_qs = Enrollment.objects.select_related('user', 'course').order_by('-enrolled_at')[:5]
        recent_enrollments = []
        for e in recent_enroll_qs:
            name = f"{e.user.first_name} {e.user.last_name}".strip() or e.user.username
            recent_enrollments.append({
                "id": e.id,
                "student_name": name,
                "course_title": e.course.title,
                "enrolled_at": e.enrolled_at
            })

        return Response({
            "total_students": total_students,
            "new_students_week": new_students_week,
            "new_students_month": new_students_month,
            "active_students": active_students,
            "inactive_students": inactive_students,
            "total_teachers": total_teachers,
            
            "total_courses": total_courses,
            "active_courses": active_courses,  # Published courses
            "draft_courses": draft_courses,
            "top_courses": top_courses,
            
            "total_revenue": float(revenue),
            "current_month_revenue": float(current_month_revenue),
            "success_payments": success_payments,
            "pending_payments": pending_payments,
            "failed_payments": failed_payments,
            "revenue_breakdown": revenue_breakdown,
            
            "total_enrollments": total_enrollments,
            "new_enrollments_month": new_enrollments_month,
            "paid_enrollments_count": paid_enrollments_count,
            "manual_enrollments_count": manual_enrollments_count,
            
            "recent_registrations": recent_registrations,
            "recent_payments": recent_payments,
            "recent_enrollments": recent_enrollments
        })

class CurrentUserView(APIView):
    def get(self, request):
        if not request.user.is_authenticated:
            return Response({"error": "Not authenticated"}, status=status.HTTP_401_UNAUTHORIZED)
        return Response({
            "id": request.user.id,
            "username": request.user.username,
            "email": request.user.email,
            "phone_number": request.user.phone_number,
            "is_student": getattr(request.user, 'is_student', False),
            "is_teacher": getattr(request.user, 'is_teacher', False),
            "is_superuser": request.user.is_superuser,
            "is_onboarded": getattr(request.user, 'is_onboarded', False)
        })

class OnboardingFieldsView(APIView):
    permission_classes = [permissions.AllowAny]

    def get(self, request):
        from .models import OnboardingField
        fields = OnboardingField.objects.all()
        data = []
        for f in fields:
            data.append({
                "name": f.name,
                "label": f.label,
                "type": f.field_type,
                "required": f.is_required,
                "options": f.options
            })
        return Response(data)

class SaveProfileView(APIView):
    def post(self, request):
        if not request.user.is_authenticated:
            return Response({"error": "Not authenticated"}, status=status.HTTP_401_UNAUTHORIZED)
            
        data = request.data
        user = request.user
        
        # We could validate against OnboardingField here, but for flexibility we just save it
        user.onboarding_data = data
        user.is_onboarded = True
        
        # Map common fields directly to User model if they exist
        if 'first_name' in data: user.first_name = data['first_name']
        if 'last_name' in data: user.last_name = data['last_name']
        if 'phone_number' in data: user.phone_number = data['phone_number']
        
        user.save()
        return Response({"message": "Profile saved successfully"})

from google.oauth2 import id_token
from google.auth.transport import requests as google_requests

class MobileGoogleLoginView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request):
        token = request.data.get('token')
        if not token:
            return Response({"error": "No token provided"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            # Specify the CLIENT_ID of the app that accesses the backend:
            # Note: For production, validate the client ID. We skip audience verification here 
            # for ease of setup across different development keys, but get the info.
            idinfo = id_token.verify_oauth2_token(token, google_requests.Request(), clock_skew_in_seconds=10)
            
            email = idinfo.get('email')
            if not email:
                return Response({"error": "Google token did not contain an email"}, status=status.HTTP_400_BAD_REQUEST)

            # Get or create user
            user, created = User.objects.get_or_create(email=email, defaults={
                'username': email.split('@')[0] + str(random.randint(1000, 9999)),
                'first_name': idinfo.get('given_name', ''),
                'last_name': idinfo.get('family_name', '')
            })

            # Generate JWT Tokens
            refresh = RefreshToken.for_user(user)
            
            return Response({
                "message": "Login successful",
                "user_id": user.id,
                "created": created,
                "is_onboarded": user.is_onboarded,
                "tokens": {
                    "access": str(refresh.access_token),
                    "refresh": str(refresh)
                }
            })

        except ValueError as e:
            # Invalid token
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)

from .models import OnboardingField
from .serializers import OnboardingFieldSerializer

class OnboardingFieldViewSet(viewsets.ModelViewSet):
    queryset = OnboardingField.objects.all().order_by('order')
    serializer_class = OnboardingFieldSerializer
    permission_classes = [IsSuperAdmin]
