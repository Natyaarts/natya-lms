from django.contrib import admin
from .models import (
    StudentBillingPlan, StudentInvoice, ManualPaymentRecord,
    BillingPlanChangeLog, BillingAccessExtension
)


@admin.register(StudentBillingPlan)
class StudentBillingPlanAdmin(admin.ModelAdmin):
    list_display = ('id', 'student', 'course', 'billing_type', 'amount', 'currency', 'status', 'next_billing_date', 'due_date', 'is_active')
    list_filter = ('billing_type', 'status', 'is_active', 'auto_restrict_access')
    search_fields = ('student__username', 'student__email', 'course__title')
    readonly_fields = ('created_at', 'updated_at')


@admin.register(StudentInvoice)
class StudentInvoiceAdmin(admin.ModelAdmin):
    list_display = ('invoice_number', 'student', 'course', 'amount', 'period_start', 'period_end', 'due_date', 'status', 'paid_at')
    list_filter = ('status', 'payment_method')
    search_fields = ('invoice_number', 'student__username', 'course__title', 'razorpay_order_id', 'razorpay_payment_id')
    readonly_fields = ('invoice_number', 'created_at', 'updated_at')


@admin.register(ManualPaymentRecord)
class ManualPaymentRecordAdmin(admin.ModelAdmin):
    list_display = ('id', 'invoice', 'amount', 'payment_method', 'payment_date', 'reference_number', 'recorded_by')
    list_filter = ('payment_method',)
    search_fields = ('invoice__invoice_number', 'reference_number')
    readonly_fields = ('created_at',)


@admin.register(BillingPlanChangeLog)
class BillingPlanChangeLogAdmin(admin.ModelAdmin):
    list_display = ('id', 'billing_plan', 'actor', 'change_mode', 'old_billing_type', 'new_billing_type', 'old_amount', 'new_amount', 'effective_date')
    search_fields = ('billing_plan__student__username', 'reason')
    readonly_fields = ('created_at',)


@admin.register(BillingAccessExtension)
class BillingAccessExtensionAdmin(admin.ModelAdmin):
    list_display = ('id', 'student', 'course', 'original_status', 'extended_until', 'is_active', 'created_by')
    list_filter = ('is_active',)
    search_fields = ('student__username', 'course__title', 'reason')
    readonly_fields = ('created_at',)
