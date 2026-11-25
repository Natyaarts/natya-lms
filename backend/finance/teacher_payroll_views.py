"""
Admin API for teacher salary (see finance/teacher_payroll.py for the rules).

All endpoints are admin-only (IsSuperAdminOrAdmin) under
/api/finance/admin/teacher-salary/. Every change is written to AdminAuditLog.
"""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404
from rest_framework import serializers, status
from rest_framework.response import Response
from rest_framework.views import APIView

from users.permissions import IsSuperAdminOrAdmin

from . import teacher_payroll as payroll
from .models import (
    TeacherPayrollLine, TeacherPayrollSettings, TeacherPayrollStatement, TeacherRateCard, TeacherRateTier,
)

User = get_user_model()
MAX_AMOUNT = Decimal("10000000")


def _name(user):
    if user is None:
        return ""
    return user.get_full_name().strip() or user.username


def _audit(request, action, target_type, target_id, description, metadata=None):
    from users.models import AdminAuditLog

    AdminAuditLog.record(actor=request.user, action=action, target_type=target_type, target_id=target_id,
                         description=description, metadata=metadata or {})


def _error(exc):
    return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)


# --------------------------------------------------------------------------- serializers

class RateTierSerializer(serializers.Serializer):
    min_students = serializers.IntegerField(min_value=0, max_value=1000)
    max_students = serializers.IntegerField(min_value=0, max_value=1000, allow_null=True, required=False, default=None)
    hourly_rate = serializers.DecimalField(max_digits=10, decimal_places=2, min_value=Decimal("0"), max_value=MAX_AMOUNT)


class RateCardSerializer(serializers.ModelSerializer):
    teacher_id = serializers.IntegerField()
    course_id = serializers.IntegerField(allow_null=True, required=False, default=None)
    teacher_name = serializers.SerializerMethodField()
    course_title = serializers.SerializerMethodField()
    tiers = RateTierSerializer(many=True, required=False)
    per_class_amount = serializers.DecimalField(max_digits=10, decimal_places=2, min_value=Decimal("0"),
                                                max_value=MAX_AMOUNT, required=False)
    per_student_amount = serializers.DecimalField(max_digits=10, decimal_places=2, min_value=Decimal("0"),
                                                  max_value=MAX_AMOUNT, required=False)

    class Meta:
        model = TeacherRateCard
        fields = ["id", "teacher_id", "teacher_name", "course_id", "course_title", "per_class_amount",
                  "per_student_amount", "currency", "is_active", "notes", "tiers", "updated_at"]
        read_only_fields = ["id", "currency", "updated_at"]

    def get_teacher_name(self, obj):
        return _name(obj.teacher)

    def get_course_title(self, obj):
        return obj.course.title if obj.course_id else "All other courses (default)"

    def validate_teacher_id(self, value):
        if self.instance is not None and value != self.instance.teacher_id:
            raise serializers.ValidationError("The teacher of a rate card cannot be changed.")
        user = User.objects.filter(pk=value).first()
        if not payroll.is_payable_teacher(user):
            raise serializers.ValidationError("Choose a teacher or mentor account.")
        return value

    def validate_course_id(self, value):
        from courses.models import Course

        if value is not None and not Course.objects.filter(pk=value).exists():
            raise serializers.ValidationError("Course not found.")
        return value

    def validate_tiers(self, tiers):
        if len(tiers) > 20:
            raise serializers.ValidationError("At most 20 tiers.")
        ordered = sorted(tiers, key=lambda t: t["min_students"])
        for i, tier in enumerate(ordered):
            low, high = tier["min_students"], tier.get("max_students")
            if high is not None and high < low:
                raise serializers.ValidationError(f"Tier starting at {low}: 'to' must be at least 'from'.")
            if high is None and i != len(ordered) - 1:
                raise serializers.ValidationError("Only the last tier (highest student count) can be open-ended.")
            if i:
                prev_high = ordered[i - 1].get("max_students")
                if prev_high is None or low <= prev_high:
                    raise serializers.ValidationError("Tiers overlap: each student count may match only one tier.")
        return ordered

    def validate(self, attrs):
        teacher_id = attrs.get("teacher_id", getattr(self.instance, "teacher_id", None))
        course_id = attrs["course_id"] if "course_id" in attrs else getattr(self.instance, "course_id", None)
        clash = TeacherRateCard.objects.filter(teacher_id=teacher_id, course_id=course_id)
        if self.instance is not None:
            clash = clash.exclude(pk=self.instance.pk)
        if clash.exists():
            raise serializers.ValidationError(
                "This teacher already has rates for this course." if course_id else
                "This teacher already has default rates."
            )
        return attrs

    def _save_tiers(self, card, tiers):
        card.tiers.all().delete()
        TeacherRateTier.objects.bulk_create([
            TeacherRateTier(rate_card=card, min_students=t["min_students"], max_students=t.get("max_students"),
                            hourly_rate=t["hourly_rate"])
            for t in tiers
        ])

    def create(self, validated):
        tiers = validated.pop("tiers", [])
        with transaction.atomic():
            card = TeacherRateCard.objects.create(updated_by=self.context["request"].user, **validated)
            self._save_tiers(card, tiers)
        return card

    def update(self, card, validated):
        tiers = validated.pop("tiers", None)
        validated.pop("teacher_id", None)
        with transaction.atomic():
            for key, value in validated.items():
                setattr(card, key, value)
            card.updated_by = self.context["request"].user
            card.save()
            if tiers is not None:
                self._save_tiers(card, tiers)
        return card


class PayrollLineSerializer(serializers.ModelSerializer):
    live_class_id = serializers.IntegerField(read_only=True)
    blocking = serializers.SerializerMethodField()

    class Meta:
        model = TeacherPayrollLine
        fields = [
            "id", "live_class_id", "course_id", "batch_id_snapshot", "class_title", "course_title", "scheduled_start",
            "is_substitute", "scheduled_minutes", "zoom_minutes", "attendee_count", "attendance_finalized",
            "minutes_override", "attendee_count_override", "is_excluded", "admin_note",
            "per_class_amount", "per_student_amount", "rate_tiers_snapshot", "paid_minutes", "students_counted",
            "hourly_rate", "tier_label", "hours_amount", "class_amount", "student_amount", "amount", "flags", "blocking",
        ]
        read_only_fields = fields

    def get_blocking(self, obj):
        return payroll.line_is_blocking(obj)


class StatementSerializer(serializers.ModelSerializer):
    teacher_name = serializers.SerializerMethodField()
    month = serializers.SerializerMethodField()
    needs_review = serializers.SerializerMethodField()
    approved_by_name = serializers.SerializerMethodField()
    paid_by_name = serializers.SerializerMethodField()

    class Meta:
        model = TeacherPayrollStatement
        fields = [
            "id", "teacher_id", "teacher_name", "month", "period_start", "period_end", "status", "currency",
            "class_count", "total_minutes", "classes_amount", "adjustment_amount", "adjustment_reason", "net_amount",
            "needs_review", "notes", "method", "reference_number", "generated_at", "approved_at", "approved_by_name",
            "paid_at", "paid_by_name", "cancelled_at", "created_at",
        ]
        read_only_fields = fields

    def get_teacher_name(self, obj):
        return _name(obj.teacher)

    def get_month(self, obj):
        return obj.period_start.strftime("%Y-%m")

    def get_needs_review(self, obj):
        count = getattr(obj, "blocking_count", None)
        if count is None:
            count = sum(1 for ln in obj.lines.all() if payroll.line_is_blocking(ln))
        return count

    def get_approved_by_name(self, obj):
        return _name(obj.approved_by)

    def get_paid_by_name(self, obj):
        return _name(obj.paid_by)


class StatementDetailSerializer(StatementSerializer):
    lines = PayrollLineSerializer(many=True, read_only=True)

    class Meta(StatementSerializer.Meta):
        fields = StatementSerializer.Meta.fields + ["lines"]
        read_only_fields = fields


def _statement_detail(statement):
    statement = (
        TeacherPayrollStatement.objects.select_related("teacher", "approved_by", "paid_by")
        .prefetch_related("lines").get(pk=statement.pk)
    )
    return StatementDetailSerializer(statement).data


# --------------------------------------------------------------------------- views

class SalaryTeachersView(APIView):
    """GET teacher-salary/teachers/ -- payable teachers/mentors (+ rate card counts) and courses for the forms."""
    permission_classes = [IsSuperAdminOrAdmin]

    def get(self, request):
        from courses.models import Course

        teachers = (
            User.objects.filter(Q(is_teacher=True) | Q(is_mentor=True))
            .exclude(pk__in=payroll.PAYROLL_EXCLUDED_USER_IDS)
            .annotate(rate_cards=Count("teacher_rate_cards"))
            .order_by("first_name", "last_name", "username")
        )
        courses = Course.objects.order_by("title").values("id", "title")
        return Response({
            "teachers": [
                {"id": t.pk, "name": _name(t), "username": t.username, "is_active": t.is_active,
                 "rate_cards": t.rate_cards}
                for t in teachers
            ],
            "courses": list(courses),
        })


class RateCardListCreateView(APIView):
    """GET/POST teacher-salary/rate-cards/  (?teacher_id=)"""
    permission_classes = [IsSuperAdminOrAdmin]

    def get(self, request):
        cards = TeacherRateCard.objects.select_related("teacher", "course").prefetch_related("tiers")
        teacher_id = request.query_params.get("teacher_id")
        if teacher_id:
            if not str(teacher_id).isdigit():
                return _error("teacher_id must be a number.")
            cards = cards.filter(teacher_id=int(teacher_id))
        return Response(RateCardSerializer(cards, many=True).data)

    def post(self, request):
        serializer = RateCardSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        try:
            card = serializer.save()
        except IntegrityError:
            return _error("This teacher already has rates for this course.")
        _audit(request, "TEACHER_RATE_CARD_CREATED", "TeacherRateCard", card.pk,
               f"Rate card #{card.pk} created for teacher #{card.teacher_id}.",
               {"teacher_id": card.teacher_id, "course_id": card.course_id})
        return Response(RateCardSerializer(card).data, status=status.HTTP_201_CREATED)


class RateCardDetailView(APIView):
    """GET/PUT/PATCH/DELETE teacher-salary/rate-cards/<pk>/"""
    permission_classes = [IsSuperAdminOrAdmin]

    def _card(self, pk):
        return get_object_or_404(TeacherRateCard.objects.select_related("teacher", "course"), pk=pk)

    def get(self, request, pk):
        return Response(RateCardSerializer(self._card(pk)).data)

    def put(self, request, pk, partial=False):
        card = self._card(pk)
        before = RateCardSerializer(card).data
        serializer = RateCardSerializer(card, data=request.data, partial=partial, context={"request": request})
        serializer.is_valid(raise_exception=True)
        try:
            card = serializer.save()
        except IntegrityError:
            return _error("This teacher already has rates for this course.")
        after = RateCardSerializer(card).data
        _audit(request, "TEACHER_RATE_CARD_UPDATED", "TeacherRateCard", card.pk,
               f"Rate card #{card.pk} updated for teacher #{card.teacher_id}.",
               {"before": {k: before[k] for k in ("course_id", "per_class_amount", "per_student_amount", "is_active", "tiers")},
                "after": {k: after[k] for k in ("course_id", "per_class_amount", "per_student_amount", "is_active", "tiers")}})
        return Response(after)

    def patch(self, request, pk):
        return self.put(request, pk, partial=True)

    def delete(self, request, pk):
        card = self._card(pk)
        meta = {"teacher_id": card.teacher_id, "course_id": card.course_id}
        card.delete()  # statements keep their own snapshot of the rates used
        _audit(request, "TEACHER_RATE_CARD_DELETED", "TeacherRateCard", pk, f"Rate card #{pk} deleted.", meta)
        return Response(status=status.HTTP_204_NO_CONTENT)


class StatementListView(APIView):
    """GET teacher-salary/statements/?month=YYYY-MM&teacher_id=&status="""
    permission_classes = [IsSuperAdminOrAdmin]

    def get(self, request):
        qs = TeacherPayrollStatement.objects.select_related("teacher", "approved_by", "paid_by").prefetch_related("lines")
        params = request.query_params
        if params.get("month"):
            try:
                year, month = payroll.parse_month(params["month"])
                first, _, _, _ = payroll.month_period(year, month)
            except payroll.PayrollError as exc:
                return _error(exc)
            qs = qs.filter(period_start=first)
        if params.get("teacher_id"):
            if not str(params["teacher_id"]).isdigit():
                return _error("teacher_id must be a number.")
            qs = qs.filter(teacher_id=int(params["teacher_id"]))
        if params.get("status"):
            if params["status"] not in TeacherPayrollStatement.Status.values:
                return _error("Unknown status.")
            qs = qs.filter(status=params["status"])
        elif params.get("include_cancelled") not in ("1", "true"):
            qs = qs.exclude(status=TeacherPayrollStatement.Status.CANCELLED)
        return Response(StatementSerializer(qs[:500], many=True).data)


class StatementGenerateView(APIView):
    """POST teacher-salary/statements/generate/ {"month": "YYYY-MM", "teacher_ids": [..] (optional)}"""
    permission_classes = [IsSuperAdminOrAdmin]

    def post(self, request):
        try:
            year, month = payroll.parse_month(request.data.get("month"))
            payroll.month_period(year, month)
        except payroll.PayrollError as exc:
            return _error(exc)
        teacher_ids = request.data.get("teacher_ids") or None
        if teacher_ids is not None:
            if not isinstance(teacher_ids, list) or not all(isinstance(i, int) for i in teacher_ids):
                return _error("teacher_ids must be a list of numbers.")
        results = payroll.generate_month(year, month, actor=request.user, teacher_ids=teacher_ids)
        done = [r for r in results if r["statement_id"]]
        _audit(request, "TEACHER_PAYROLL_GENERATED", "TeacherPayrollStatement", "",
               f"Teacher salary generated for {year}-{month:02d}: {len(done)} statement(s).",
               {"month": f"{year}-{month:02d}", "results": results})
        return Response({"month": f"{year}-{month:02d}", "results": results})


class StatementDetailView(APIView):
    """GET teacher-salary/statements/<pk>/ ; PATCH {adjustment_amount, adjustment_reason, notes} (draft only)"""
    permission_classes = [IsSuperAdminOrAdmin]

    def get(self, request, pk):
        return Response(_statement_detail(get_object_or_404(TeacherPayrollStatement, pk=pk)))

    def patch(self, request, pk):
        statement = get_object_or_404(TeacherPayrollStatement, pk=pk)
        changes = {}
        if "adjustment_amount" in request.data:
            field = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=-MAX_AMOUNT, max_value=MAX_AMOUNT)
            try:
                changes["amount"] = field.to_internal_value(request.data.get("adjustment_amount"))
            except serializers.ValidationError:
                return _error("Enter a valid bonus/deduction amount.")
        if "adjustment_reason" in request.data:
            changes["reason"] = str(request.data.get("adjustment_reason") or "")
        if "notes" in request.data:
            changes["notes"] = str(request.data.get("notes") or "")
        try:
            statement = payroll.set_adjustment(statement, **changes)
        except payroll.PayrollError as exc:
            return _error(exc)
        _audit(request, "TEACHER_PAYROLL_ADJUSTED", "TeacherPayrollStatement", statement.pk,
               f"Statement #{statement.pk} adjustment set to {statement.adjustment_amount}.",
               {"adjustment_amount": str(statement.adjustment_amount), "reason": statement.adjustment_reason})
        return Response(_statement_detail(statement))


class StatementActionView(APIView):
    """POST teacher-salary/statements/<pk>/<regenerate|approve|mark-paid|cancel>/"""
    permission_classes = [IsSuperAdminOrAdmin]
    action = None

    def post(self, request, pk):
        statement = get_object_or_404(TeacherPayrollStatement.objects.select_related("teacher"), pk=pk)
        try:
            if self.action == "regenerate":
                if statement.status != TeacherPayrollStatement.Status.DRAFT:
                    raise payroll.PayrollError("Only a draft statement can be regenerated.")
                statement, _ = payroll.generate_statement(
                    statement.teacher, statement.period_start.year, statement.period_start.month, actor=request.user)
                code = "TEACHER_PAYROLL_REGENERATED"
            elif self.action == "approve":
                statement = payroll.approve_statement(statement, request.user)
                code = "TEACHER_PAYROLL_APPROVED"
            elif self.action == "mark-paid":
                statement = payroll.mark_statement_paid(
                    statement, request.user,
                    method=str(request.data.get("method") or ""),
                    reference_number=str(request.data.get("reference_number") or "").strip(),
                )
                code = "TEACHER_PAYROLL_PAID"
            elif self.action == "cancel":
                statement = payroll.cancel_statement(statement, request.user,
                                                     reason=str(request.data.get("reason") or "").strip()[:255])
                code = "TEACHER_PAYROLL_CANCELLED"
            else:  # pragma: no cover
                return _error("Unknown action.")
        except payroll.PayrollError as exc:
            return _error(exc)
        _audit(request, code, "TeacherPayrollStatement", statement.pk,
               f"Statement #{statement.pk} ({statement.period_start:%Y-%m}) for teacher #{statement.teacher_id}: {self.action}.",
               {"teacher_id": statement.teacher_id, "net_amount": str(statement.net_amount), "status": statement.status})
        return Response(_statement_detail(statement))


class PayrollLineView(APIView):
    """PATCH teacher-salary/lines/<pk>/ {minutes_override, attendee_count_override, is_excluded, admin_note} (draft only)"""
    permission_classes = [IsSuperAdminOrAdmin]

    def patch(self, request, pk):
        line = get_object_or_404(TeacherPayrollLine, pk=pk)
        data = request.data
        changes = {}

        def _optional_int(key, upper):
            value = data.get(key)
            if value in (None, ""):
                return None
            if isinstance(value, bool) or not str(value).isdigit() or int(value) > upper:
                raise payroll.PayrollError(f"{key.replace('_', ' ')} must be a whole number from 0 to {upper}.")
            return int(value)

        try:
            if "minutes_override" in data:
                changes["minutes_override"] = _optional_int("minutes_override", 1440)
            if "attendee_count_override" in data:
                changes["attendee_count_override"] = _optional_int("attendee_count_override", 1000)
            if "is_excluded" in data:
                if not isinstance(data.get("is_excluded"), bool):
                    raise payroll.PayrollError("is_excluded must be true or false.")
                changes["is_excluded"] = data["is_excluded"]
            if "admin_note" in data:
                changes["admin_note"] = str(data.get("admin_note") or "")
            line = payroll.update_line(line, **changes)
        except payroll.PayrollError as exc:
            return _error(exc)
        _audit(request, "TEACHER_PAYROLL_LINE_CORRECTED", "TeacherPayrollLine", line.pk,
               f"Line #{line.pk} on statement #{line.statement_id} corrected.",
               {"changes": {k: v for k, v in changes.items()}, "amount": str(line.amount)})
        return Response(_statement_detail(line.statement))


class PayrollSettingsSerializer(serializers.ModelSerializer):
    updated_by_name = serializers.SerializerMethodField()

    class Meta:
        model = TeacherPayrollSettings
        fields = ["count_late_as_attended", "count_excused_as_attended", "cap_zoom_time_at_schedule",
                  "missing_zoom_time", "updated_at", "updated_by_name"]
        read_only_fields = ["updated_at", "updated_by_name"]

    def get_updated_by_name(self, obj):
        return _name(obj.updated_by) if obj.updated_by_id else ""


SETTINGS_KEYS = ("count_late_as_attended", "count_excused_as_attended", "cap_zoom_time_at_schedule", "missing_zoom_time")


class PayrollSettingsView(APIView):
    """GET/PUT teacher-salary/settings/ -- the admin-editable calculation rules."""
    permission_classes = [IsSuperAdminOrAdmin]

    def get(self, request):
        return Response(PayrollSettingsSerializer(TeacherPayrollSettings.get_solo()).data)

    def put(self, request):
        settings_obj = TeacherPayrollSettings.get_solo()
        before = PayrollSettingsSerializer(settings_obj).data
        serializer = PayrollSettingsSerializer(settings_obj, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        settings_obj = serializer.save(updated_by=request.user)
        after = PayrollSettingsSerializer(settings_obj).data
        _audit(request, "TEACHER_PAYROLL_SETTINGS_UPDATED", "TeacherPayrollSettings", 1,
               "Teacher salary calculation settings updated.",
               {"before": {k: before[k] for k in SETTINGS_KEYS}, "after": {k: after[k] for k in SETTINGS_KEYS}})
        return Response(after)

    patch = put
