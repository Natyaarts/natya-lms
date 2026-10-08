from decimal import Decimal
from django.conf import settings
from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework import serializers
from courses.models import Course, Enrollment
from .models import (
    StudentBillingPlan,
    StudentInvoice,
    ManualPaymentRecord,
    BillingPlanChangeLog,
    BillingAccessExtension,
    BillingType,
    IntervalUnit,
    PlanStatus,
    InvoiceStatus,
    PaymentMethod,
)
from .services.lifecycle import (
    create_billing_plan,
    generate_cycle_invoice,
    apply_plan_change,
    extend_access,
)
from .services.payment import record_manual_payment

User = get_user_model()


class UserMinimalSerializer(serializers.ModelSerializer):
    full_name = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ['id', 'username', 'email', 'full_name']

    def get_full_name(self, obj):
        return obj.get_full_name() or obj.username


class CourseMinimalSerializer(serializers.ModelSerializer):
    class Meta:
        model = Course
        fields = ['id', 'title', 'price', 'thumbnail', 'course_type']


class StudentInvoiceSerializer(serializers.ModelSerializer):
    student_details = UserMinimalSerializer(source='student', read_only=True)
    course_details = CourseMinimalSerializer(source='course', read_only=True)

    class Meta:
        model = StudentInvoice
        fields = [
            'id',
            'invoice_number',
            'billing_plan',
            'student',
            'student_details',
            'course',
            'course_details',
            'period_start',
            'period_end',
            'issue_date',
            'due_date',
            'amount',
            'currency',
            'status',
            'razorpay_order_id',
            'razorpay_payment_id',
            'payment_method',
            'paid_at',
            'notes',
            'metadata',
            'created_at',
            'updated_at',
        ]
        read_only_fields = [
            'id',
            'invoice_number',
            'student_details',
            'course_details',
            'paid_at',
            'razorpay_order_id',
            'razorpay_payment_id',
            'created_at',
            'updated_at',
        ]


class ManualPaymentRecordSerializer(serializers.ModelSerializer):
    invoice_number = serializers.CharField(source='invoice.invoice_number', read_only=True)
    recorded_by_name = serializers.CharField(source='recorded_by.username', read_only=True)

    class Meta:
        model = ManualPaymentRecord
        fields = [
            'id',
            'invoice',
            'invoice_number',
            'amount',
            'payment_method',
            'payment_date',
            'reference_number',
            'receipt_file',
            'recorded_by',
            'recorded_by_name',
            'notes',
            'created_at',
        ]
        read_only_fields = ['id', 'invoice_number', 'recorded_by_name', 'created_at']


class BillingPlanChangeLogSerializer(serializers.ModelSerializer):
    actor_name = serializers.CharField(source='actor.username', read_only=True)

    class Meta:
        model = BillingPlanChangeLog
        fields = [
            'id',
            'billing_plan',
            'actor',
            'actor_name',
            'change_mode',
            'old_billing_type',
            'new_billing_type',
            'old_amount',
            'new_amount',
            'effective_date',
            'reason',
            'created_at',
        ]
        read_only_fields = ['id', 'actor_name', 'created_at']


class BillingAccessExtensionSerializer(serializers.ModelSerializer):
    student_details = UserMinimalSerializer(source='student', read_only=True)
    course_details = CourseMinimalSerializer(source='course', read_only=True)
    created_by_name = serializers.CharField(source='created_by.username', read_only=True)

    class Meta:
        model = BillingAccessExtension
        fields = [
            'id',
            'student',
            'student_details',
            'course',
            'course_details',
            'billing_plan',
            'original_status',
            'extended_until',
            'reason',
            'is_active',
            'created_by',
            'created_by_name',
            'created_at',
        ]
        read_only_fields = ['id', 'student_details', 'course_details', 'created_by_name', 'created_at']


class StudentBillingPlanSerializer(serializers.ModelSerializer):
    student_details = UserMinimalSerializer(source='student', read_only=True)
    course_details = CourseMinimalSerializer(source='course', read_only=True)
    invoices = serializers.SerializerMethodField()
    active_extension = serializers.SerializerMethodField()
    change_logs = BillingPlanChangeLogSerializer(many=True, read_only=True)

    class Meta:
        model = StudentBillingPlan
        fields = [
            'id',
            'student',
            'student_details',
            'course',
            'course_details',
            'enrollment',
            'billing_type',
            'amount',
            'currency',
            'billing_interval_value',
            'billing_interval_unit',
            'start_date',
            'current_period_start',
            'current_period_end',
            'next_billing_date',
            'due_date',
            'advance_invoice_days',
            'grace_period_days',
            'grace_until',
            'access_restriction_date',
            'status',
            'auto_generate_invoice',
            'auto_restrict_access',
            'auto_send_reminders',
            'is_active',
            'pending_billing_type',
            'pending_amount',
            'pending_effective_date',
            'paused_at',
            'cancelled_at',
            'notes',
            'created_at',
            'updated_at',
            'invoices',
            'active_extension',
            'change_logs',
        ]
        read_only_fields = [
            'id',
            'student_details',
            'course_details',
            'current_period_start',
            'current_period_end',
            'next_billing_date',
            'due_date',
            'grace_until',
            'access_restriction_date',
            'pending_billing_type',
            'pending_amount',
            'pending_effective_date',
            'paused_at',
            'cancelled_at',
            'created_at',
            'updated_at',
            'invoices',
            'active_extension',
            'change_logs',
        ]

    def get_invoices(self, obj):
        recent_invoices = obj.invoices.all().order_by('-due_date')[:10]
        return StudentInvoiceSerializer(recent_invoices, many=True).data

    def get_active_extension(self, obj):
        ext = obj.access_extensions.filter(is_active=True).first()
        if ext:
            return BillingAccessExtensionSerializer(ext).data
        return None


class StudentBillingPlanCreateSerializer(serializers.ModelSerializer):
    """
    Serializer for creating a new StudentBillingPlan through backend services.
    Never duplicates date math — invokes create_billing_plan service.
    """
    issue_initial_invoice = serializers.BooleanField(default=True, write_only=True)

    class Meta:
        model = StudentBillingPlan
        fields = [
            'student',
            'course',
            'enrollment',
            'billing_type',
            'amount',
            'currency',
            'start_date',
            'billing_interval_value',
            'billing_interval_unit',
            'advance_invoice_days',
            'grace_period_days',
            'auto_generate_invoice',
            'auto_restrict_access',
            'auto_send_reminders',
            'notes',
            'issue_initial_invoice',
        ]

    def validate(self, attrs):
        billing_type = attrs.get('billing_type', BillingType.MONTHLY)
        if billing_type == BillingType.CUSTOM:
            interval_val = attrs.get('billing_interval_value')
            interval_unit = attrs.get('billing_interval_unit')
            if not interval_val or interval_val <= 0:
                raise serializers.ValidationError({
                    "billing_interval_value": "Interval value must be positive for custom billing."
                })
            if not interval_unit:
                raise serializers.ValidationError({
                    "billing_interval_unit": "Interval unit is required for custom billing."
                })

        # Ensure no existing active billing plan for this student + course
        student = attrs['student']
        course = attrs['course']
        if StudentBillingPlan.objects.filter(student=student, course=course, is_active=True).exists():
            raise serializers.ValidationError(
                f"An active billing plan already exists for student {student.username} on course {course.title}."
            )

        return attrs

    def create(self, validated_data):
        issue_initial_invoice = validated_data.pop('issue_initial_invoice', True)
        request = self.context.get('request')
        actor = request.user if request and request.user.is_authenticated else None

        # Defer authoritative calculation to backend lifecycle service
        plan = create_billing_plan(
            student=validated_data['student'],
            course=validated_data['course'],
            billing_type=validated_data['billing_type'],
            amount=validated_data['amount'],
            start_date=validated_data['start_date'],
            currency=validated_data.get('currency', 'INR'),
            enrollment=validated_data.get('enrollment'),
            billing_interval_value=validated_data.get('billing_interval_value'),
            billing_interval_unit=validated_data.get('billing_interval_unit'),
            advance_invoice_days=validated_data.get('advance_invoice_days', 7),
            grace_period_days=validated_data.get('grace_period_days', 7),
            auto_generate_invoice=validated_data.get('auto_generate_invoice', True),
            auto_restrict_access=validated_data.get('auto_restrict_access', True),
            auto_send_reminders=validated_data.get('auto_send_reminders', True),
            created_by=actor,
            notes=validated_data.get('notes', ''),
            issue_initial_invoice=issue_initial_invoice
        )
        return plan


class PlanChangeRequestSerializer(serializers.Serializer):
    new_billing_type = serializers.ChoiceField(choices=BillingType.choices)
    new_amount = serializers.DecimalField(max_digits=10, decimal_places=2, min_value=Decimal('0.00'))
    change_mode = serializers.ChoiceField(
        choices=[('NEXT_CYCLE', 'Next Cycle (Standard)'), ('IMMEDIATE', 'Immediate')],
        default='NEXT_CYCLE'
    )
    reason = serializers.CharField(required=True, allow_blank=False, max_length=1000)


class ExtendAccessRequestSerializer(serializers.Serializer):
    extended_until = serializers.DateField(required=True)
    reason = serializers.CharField(required=True, allow_blank=False, max_length=1000)

    def validate_extended_until(self, value):
        if value < timezone.now().date():
            raise serializers.ValidationError("Extension date must be in the future.")
        return value


class ManualPaymentCreateSerializer(serializers.Serializer):
    invoice = serializers.PrimaryKeyRelatedField(queryset=StudentInvoice.objects.all())
    amount = serializers.DecimalField(max_digits=10, decimal_places=2, min_value=Decimal('0.01'))
    payment_method = serializers.ChoiceField(choices=PaymentMethod.choices, default=PaymentMethod.CASH)
    payment_date = serializers.DateTimeField(required=False, default=timezone.now)
    reference_number = serializers.CharField(required=False, allow_blank=True, default='')
    receipt_file = serializers.FileField(required=False, allow_null=True)
    notes = serializers.CharField(required=False, allow_blank=True, default='')

    def validate(self, attrs):
        invoice = attrs['invoice']
        amount = attrs['amount']

        if invoice.status == InvoiceStatus.PAID:
            raise serializers.ValidationError("This invoice is already paid.")

        if amount < invoice.amount:
            raise serializers.ValidationError({
                "amount": f"Payment amount (₹{amount}) is less than outstanding invoice amount (₹{invoice.amount}). Full payment is required."
            })

        return attrs

    def save(self):
        invoice = self.validated_data['invoice']
        amount = self.validated_data['amount']
        payment_method = self.validated_data.get('payment_method', PaymentMethod.CASH)
        reference_number = self.validated_data.get('reference_number', '')
        receipt_file = self.validated_data.get('receipt_file')
        notes = self.validated_data.get('notes', '')

        request = self.context.get('request')
        actor = request.user if request and request.user.is_authenticated else None

        paid_invoice, processed = record_manual_payment(
            invoice=invoice,
            amount=amount,
            payment_method=payment_method,
            reference_number=reference_number,
            actor=actor,
            notes=notes,
            receipt_file=receipt_file
        )
        return paid_invoice


class StudentInvoiceReceiptSerializer(serializers.ModelSerializer):
    """
    Printable invoice / official receipt metadata for student download and tax records.
    Includes academy GSTIN/PAN and payment confirmation details.
    """
    student_details = UserMinimalSerializer(source='student', read_only=True)
    course_details = CourseMinimalSerializer(source='course', read_only=True)
    manual_payments = ManualPaymentRecordSerializer(many=True, read_only=True)
    academy_details = serializers.SerializerMethodField()
    billing_plan_details = serializers.SerializerMethodField()

    class Meta:
        model = StudentInvoice
        fields = [
            'id',
            'invoice_number',
            'billing_plan',
            'billing_plan_details',
            'student',
            'student_details',
            'course',
            'course_details',
            'period_start',
            'period_end',
            'issue_date',
            'due_date',
            'amount',
            'currency',
            'status',
            'razorpay_order_id',
            'razorpay_payment_id',
            'payment_method',
            'paid_at',
            'manual_payments',
            'academy_details',
            'notes',
            'created_at',
        ]

    def get_academy_details(self, obj):
        config = getattr(settings, 'ACADEMY_BILLING_METADATA', {})
        legal_name = config.get("LEGAL_NAME", "")
        gstin = config.get("GSTIN", "")
        pan = config.get("PAN", "")
        address = config.get("ADDRESS", "")
        phone = config.get("PHONE", "")
        email = config.get("EMAIL", "") or getattr(settings, 'DEFAULT_FROM_EMAIL', "support@natyaarts.com")
        website = config.get("WEBSITE", "") or "https://academy.natyaarts.com"
        name = config.get("NAME", "") or "Natya Arts Academy"

        # Production validity flag: only considered authoritative if all official tax identifiers are configured
        is_tax_profile_configured = bool(legal_name and gstin and pan)

        return {
            "name": name,
            "legal_name": legal_name if is_tax_profile_configured else "Natya Arts Academy (Tax Registration Pending)",
            "address": address if is_tax_profile_configured else "Natya Arts Academy Campus",
            "gstin": gstin,
            "pan": pan,
            "email": email,
            "phone": phone,
            "website": website,
            "is_tax_profile_configured": is_tax_profile_configured
        }

    def get_billing_plan_details(self, obj):
        plan = obj.billing_plan
        return {
            "id": plan.id,
            "billing_type": plan.billing_type,
            "billing_type_display": plan.get_billing_type_display(),
            "billing_interval_value": plan.billing_interval_value,
            "billing_interval_unit": plan.billing_interval_unit,
        }

