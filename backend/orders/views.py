import razorpay
from decimal import Decimal
from django.conf import settings
from django.db import transaction
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework import status, permissions
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

from rest_framework.permissions import IsAuthenticated

@method_decorator(csrf_exempt, name='dispatch')
class CreateOrderView(APIView):
    authentication_classes = [JWTCookieAuthentication, CsrfExemptSessionAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request):
        course_id = request.data.get('course_id')
        if not course_id:
            return Response({"error": "course_id is required"}, status=status.HTTP_400_BAD_REQUEST)

        course = get_object_or_404(Course, id=course_id)

        # Phase 3.1: application-level duplicate-purchase prevention -- not a
        # DB unique constraint on (user, course), deliberately. A hard
        # constraint would block legitimate future cases the Phase 3 plan
        # already anticipates: repurchase after a refund, retrying after a
        # FAILED payment, bundles, subscriptions. What we CAN safely block
        # unconditionally is a student paying twice for a course they
        # already successfully own -- mirrors the identical check already
        # used in AdminUserViewSet.assign_course (users/views.py), now
        # consistent across both the self-serve and admin paths. A PENDING
        # purchase from an earlier abandoned/failed attempt does NOT block a
        # fresh attempt here -- that's a legitimate retry; reconciling/
        # expiring stale PENDING rows is a separate, deliberately deferred
        # concern (see PHASE3_PAYMENTS_FINANCE_PLAN.md Part J).
        if Purchase.objects.filter(user=request.user, course=course, status=Purchase.Status.SUCCESS).exists():
            return Response({"error": "You have already purchased this course."}, status=status.HTTP_400_BAD_REQUEST)

        # Razorpay expects amount in paise (multiply by 100)
        amount_in_paise = int(course.price * 100)

        try:
            # Create Razorpay Order
            razorpay_order = client.order.create({
                "amount": amount_in_paise,
                "currency": "INR",
                "payment_capture": "1" # Auto capture
            })

            # Create Purchase record
            purchase = Purchase.objects.create(
                user=request.user,
                course=course,
                razorpay_order_id=razorpay_order['id'],
                amount=course.price,
                status=Purchase.Status.PENDING
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

        from .services import fulfill_purchase

        try:
            # Phase 3.1: select_for_update() + an outer atomic() block close
            # the race where two verify-payment calls for the SAME order
            # (a client retry, a double-click, a network-level resubmit)
            # arrive close together -- without the lock, both could read the
            # purchase while it's still PENDING, both then set it SUCCESS,
            # and both call fulfill_purchase with previous_status='PENDING',
            # firing the payment/enrollment notifications twice. With the
            # lock, the second request blocks until the first commits, then
            # sees status already SUCCESS and short-circuits below.
            # (select_for_update() is a documented no-op on SQLite, so this
            # is unchanged/safe in local dev and the test suite; it takes
            # effect on the production Postgres/RDS backend.)
            with transaction.atomic():
                purchase = Purchase.objects.select_for_update().get(
                    razorpay_order_id=razorpay_order_id, user=request.user
                )

                if purchase.status == Purchase.Status.SUCCESS:
                    # Already fulfilled by an earlier request (or the request
                    # this one raced with). Idempotent no-op -- same success
                    # response, no re-verification, no re-fulfillment.
                    return Response({"message": "Payment verified and course enrolled!"})

                previous_status = purchase.status

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
                purchase.status = Purchase.Status.SUCCESS
                purchase.save()

                fulfill_purchase(purchase, previous_status)

            return Response({"message": "Payment verified and course enrolled!"})

        except razorpay.errors.SignatureVerificationError:
            # Raised inside the atomic() block above, which rolls back
            # (nothing had been written yet at that point) before this
            # handler runs. This save() is therefore its own fresh,
            # unlocked write -- matching the pre-3.1 behavior exactly.
            if 'purchase' in locals():
                purchase.status = Purchase.Status.FAILED
                purchase.save()
            return Response({"error": "Invalid Payment Signature"}, status=status.HTTP_400_BAD_REQUEST)
        except Purchase.DoesNotExist:
            return Response({"error": "Order not found"}, status=status.HTTP_404_NOT_FOUND)
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


import json
import logging
from django.utils import timezone
from django.db import IntegrityError
from rest_framework.permissions import AllowAny
from .models import WebhookEvent

logger = logging.getLogger(__name__)


@method_decorator(csrf_exempt, name='dispatch')
class RazorpayWebhookView(APIView):
    """
    Phase 3.2: server-to-server endpoint Razorpay calls directly -- there is
    no user session, so authentication is signature-based, not JWT/cookie.
    Trust is established ONLY via the HMAC-SHA256 signature in the
    X-Razorpay-Signature header (verified against RAZORPAY_WEBHOOK_SECRET,
    a secret separate from RAZORPAY_KEY_SECRET, configured in the Razorpay
    dashboard) -- never from anything the request body merely claims.

    This view is the reconciliation source of truth for payments that
    complete without the client ever calling /verify-payment/ (a closed
    browser tab, a network drop right after Razorpay captures the payment,
    etc.) -- see PHASE3_PAYMENTS_FINANCE_PLAN.md Part C. It reuses the
    existing fulfill_purchase() exactly as every other success path does;
    it does not introduce a second fulfillment mechanism.

    Razorpay explicitly guarantees at-least-once, not-necessarily-ordered
    delivery and retries on anything but a fast 2xx -- every code path here
    is written assuming the same event can arrive more than once, and that
    a payment.failed for an order can arrive AFTER that order's
    payment.captured already succeeded (a retried payment attempt).
    """
    authentication_classes = []
    permission_classes = [AllowAny]

    # Event types that mean "this order's payment succeeded" vs "this
    # payment attempt failed". order.paid is included alongside
    # payment.captured because Razorpay may fire either or both depending
    # on which events are enabled in the dashboard -- handling both is
    # safe: fulfill_purchase()'s own previous-status check makes a second
    # invocation for an already-SUCCESS purchase a no-op.
    SUCCESS_EVENT_TYPES = {'payment.captured', 'order.paid'}
    FAILURE_EVENT_TYPES = {'payment.failed'}

    # Phase 3.4.3.1: Razorpay subscription lifecycle events. Confirmed
    # against Razorpay's current webhook payload documentation that every
    # one of these carries payload.subscription.entity, and that only
    # subscription.charged (of the ones in scope here) additionally
    # carries payload.payment.entity -- see _reconcile_subscription/
    # _record_subscription_charge below. subscription.updated is
    # deliberately NOT included (out of scope for this phase; falls
    # through to the existing safe IGNORED path like any other unhandled
    # event type).
    SUBSCRIPTION_EVENT_TYPES = {
        'subscription.authenticated', 'subscription.activated', 'subscription.charged',
        'subscription.pending', 'subscription.halted', 'subscription.cancelled',
        'subscription.completed', 'subscription.paused', 'subscription.resumed',
    }

    def post(self, request):
        raw_body = request.body  # raw bytes -- signature is computed over
        # the exact, unparsed request body per Razorpay's docs; request.data
        # is deliberately never used in this view.
        signature = request.headers.get('X-Razorpay-Signature', '')
        event_id = request.headers.get('x-razorpay-event-id', '')
        webhook_secret = settings.RAZORPAY_WEBHOOK_SECRET

        if not webhook_secret:
            # Logged, not raised -- see the comment on RAZORPAY_WEBHOOK_SECRET
            # in core/settings.py for why this doesn't crash the whole app.
            logger.error("Razorpay webhook received but RAZORPAY_WEBHOOK_SECRET is not configured; rejecting.")
            return Response(status=status.HTTP_400_BAD_REQUEST)

        if not signature:
            logger.warning("Razorpay webhook rejected: missing X-Razorpay-Signature header.")
            return Response(status=status.HTTP_400_BAD_REQUEST)

        try:
            # Never log the signature or the secret themselves -- only that
            # verification happened and its outcome.
            client.utility.verify_webhook_signature(raw_body.decode('utf-8'), signature, webhook_secret)
        except razorpay.errors.SignatureVerificationError:
            logger.warning("Razorpay webhook rejected: signature verification failed.")
            return Response(status=status.HTTP_400_BAD_REQUEST)
        except (UnicodeDecodeError, TypeError):
            logger.warning("Razorpay webhook rejected: request body could not be read for signature verification.")
            return Response(status=status.HTTP_400_BAD_REQUEST)

        if not event_id:
            # Can only reach here with a body that DID pass signature
            # verification, so this is a genuine Razorpay misconfiguration/
            # unexpected payload shape, not a forged request -- still
            # rejected, since event_id is the sole idempotency key below.
            logger.warning("Razorpay webhook rejected: missing x-razorpay-event-id header.")
            return Response(status=status.HTTP_400_BAD_REQUEST)

        try:
            payload = json.loads(raw_body)
        except (ValueError, TypeError):
            logger.warning(f"Razorpay webhook rejected: malformed JSON payload, event_id={event_id}.")
            return Response(status=status.HTTP_400_BAD_REQUEST)

        event_type = payload.get('event', '')

        # Idempotency / duplicate-webhook handling: the SAME try/except-
        # IntegrityError-on-a-unique-field pattern already proven by
        # NotificationService.create_notification -- attempt the create,
        # and if the unique constraint on razorpay_event_id rejects it,
        # this exact event was already received (Razorpay retry, or a
        # genuine simultaneous duplicate delivery) -- return 200 immediately
        # without reprocessing. Django's unique constraint makes this
        # correct even under real concurrency (two requests for the same
        # event_id arriving at the same instant), not just sequentially.
        try:
            # The create() must be inside its own atomic() block: catching
            # IntegrityError without one leaves the *outer* transaction
            # unusable for the rest of the request (a well-known Django
            # gotcha) -- this is a savepoint, so only this insert rolls
            # back on a duplicate, nothing else.
            with transaction.atomic():
                webhook_event = WebhookEvent.objects.create(
                    razorpay_event_id=event_id,
                    event_type=event_type,
                    payload=payload,
                    status=WebhookEvent.Status.RECEIVED,
                )
        except IntegrityError:
            logger.info(f"Razorpay webhook duplicate ignored: event_id={event_id}, event_type={event_type}.")
            return Response(status=status.HTTP_200_OK)

        self._process_event(webhook_event)

        # Always 200 once the event is durably recorded (even if processing
        # itself failed, see _process_event) -- returning a non-2xx here
        # would make Razorpay retry an event we've already stored and are
        # tracking as FAILED for manual review, risking duplicate side
        # effects the WebhookEvent uniqueness constraint exists to prevent.
        return Response(status=status.HTTP_200_OK)

    def _process_event(self, webhook_event):
        try:
            if webhook_event.event_type in self.SUCCESS_EVENT_TYPES or webhook_event.event_type in self.FAILURE_EVENT_TYPES:
                self._reconcile_payment(webhook_event)
            elif webhook_event.event_type in self.SUBSCRIPTION_EVENT_TYPES:
                self._reconcile_subscription(webhook_event)
            else:
                webhook_event.status = WebhookEvent.Status.IGNORED
                webhook_event.processed_at = timezone.now()
                webhook_event.save(update_fields=['status', 'processed_at'])
                logger.info(f"Razorpay webhook ignored (no handler for this event type): "
                            f"event_type={webhook_event.event_type}, event_id={webhook_event.razorpay_event_id}.")
        except Exception as e:
            webhook_event.status = WebhookEvent.Status.FAILED
            webhook_event.error_message = str(e)[:2000]
            webhook_event.processed_at = timezone.now()
            webhook_event.save(update_fields=['status', 'error_message', 'processed_at'])
            logger.error(
                f"Razorpay webhook processing failed: event_id={webhook_event.razorpay_event_id}, "
                f"event_type={webhook_event.event_type}, error={str(e)}",
                exc_info=True
            )

    def _reconcile_payment(self, webhook_event):
        """
        Phase 3.3: extended (not redesigned) to also understand the new
        Order model. A single Razorpay order id can only ever belong to
        EITHER the legacy Purchase flow OR the new Order flow -- each is
        created via its own, separate client.order.create() call
        (CreateOrderView vs OrderViewSet.create()) -- so trying Purchase
        first and falling back to Order is an unambiguous, safe mapping,
        never a guess about which system "owns" a given id.
        """
        payment_entity = webhook_event.payload.get('payload', {}).get('payment', {}).get('entity', {})
        order_id = payment_entity.get('order_id')
        payment_id = payment_entity.get('id')

        if not order_id:
            raise ValueError(f"{webhook_event.event_type} webhook payload missing payload.payment.entity.order_id")

        is_success_event = webhook_event.event_type in self.SUCCESS_EVENT_TYPES

        # select_for_update() + atomic(): the exact same concurrency
        # protection added to VerifyPaymentView/mark_paid in Phase 3.1,
        # applied here too -- a webhook delivery can race a client's own
        # /verify-payment/ call for the same order, or two webhook
        # deliveries can race each other under Razorpay's at-least-once
        # retry behavior.
        with transaction.atomic():
            purchase = Purchase.objects.select_for_update().filter(razorpay_order_id=order_id).first()
            if purchase:
                self._apply_purchase_reconciliation(webhook_event, purchase, payment_id, is_success_event)
                return

            order = Order.objects.select_for_update().filter(razorpay_order_id=order_id).first()
            if order:
                self._apply_order_reconciliation(webhook_event, order, payment_id, is_success_event)
                return

            # Not necessarily an error on Razorpay's side -- e.g. a test
            # webhook, or an order created through a path this app doesn't
            # track. Recorded as FAILED so it surfaces in the WebhookEvent
            # admin view for manual reconciliation, but this method returns
            # normally (no exception) so _process_event doesn't overwrite
            # this more specific error_message with a generic one.
            webhook_event.status = WebhookEvent.Status.FAILED
            webhook_event.error_message = f"No Purchase or Order found for razorpay_order_id={order_id}"
            webhook_event.processed_at = timezone.now()
            webhook_event.save(update_fields=['status', 'error_message', 'processed_at'])
            logger.warning(
                f"Razorpay webhook: no matching Purchase or Order for order_id={order_id}, "
                f"event_id={webhook_event.razorpay_event_id}."
            )

    def _apply_purchase_reconciliation(self, webhook_event, purchase, payment_id, is_success_event):
        from .services import fulfill_purchase

        webhook_event.purchase = purchase
        previous_status = purchase.status

        if is_success_event:
            if purchase.status != Purchase.Status.SUCCESS:
                if payment_id:
                    purchase.razorpay_payment_id = payment_id
                purchase.status = Purchase.Status.SUCCESS
                purchase.save()
            # Reuses the exact same fulfillment path as every other success
            # route (manual verify, admin mark-paid) -- idempotent per its
            # own docstring, safe to call even when already SUCCESS.
            fulfill_purchase(purchase, previous_status)
        else:
            # Never downgrade an already-SUCCESS purchase because of a
            # late/out-of-order payment.failed webhook for the same order --
            # e.g. a student's first payment attempt failed, they
            # immediately retried and succeeded, and the FAILED webhook for
            # the first attempt arrives after the SUCCESS one. Only a
            # still-PENDING purchase is moved to FAILED.
            if purchase.status == Purchase.Status.PENDING:
                purchase.status = Purchase.Status.FAILED
                purchase.save()

        webhook_event.status = WebhookEvent.Status.PROCESSED
        webhook_event.processed_at = timezone.now()
        webhook_event.save(update_fields=['purchase', 'status', 'processed_at'])

    def _apply_order_reconciliation(self, webhook_event, order, payment_id, is_success_event):
        # Phase 3.3: same shape as _apply_purchase_reconciliation above, for
        # the new Order model -- WebhookEvent.purchase stays unset for an
        # Order-mapped event (that FK is Purchase-specific, unchanged from
        # Phase 3.2; the raw payload + this method's own logging are the
        # traceability trail for Order-mapped events).
        from .services import fulfill_order

        previous_status = order.status

        if is_success_event:
            if order.status != Order.Status.PAID:
                if payment_id:
                    order.razorpay_payment_id = payment_id
                order.status = Order.Status.PAID
                order.save()
            fulfill_order(order, previous_status)
        else:
            if order.status == Order.Status.PENDING:
                order.status = Order.Status.FAILED
                order.save()

        webhook_event.status = WebhookEvent.Status.PROCESSED
        webhook_event.processed_at = timezone.now()
        webhook_event.save(update_fields=['status', 'processed_at'])
        logger.info(
            f"Razorpay webhook reconciled against Order {order.order_number} "
            f"(event_id={webhook_event.razorpay_event_id})."
        )

    def _reconcile_subscription(self, webhook_event):
        """
        Phase 3.4.3.1: recurring subscription lifecycle events. Every
        subscription.* event carries payload.subscription.entity -- that
        entity's own `id` (Razorpay's subscription id) is the ONLY thing
        used to find the local Subscription row; nothing about ownership/
        user identity is ever read from the payload, exactly like order_id
        is already the sole lookup key for _reconcile_payment above. A
        Razorpay subscription id with no matching local row is handled the
        same way an unmatched order_id already is: recorded FAILED for
        admin visibility, no fake local Subscription is ever created, no
        SubscriptionPayment, no access granted -- this method never creates
        a Subscription, only updates an existing one.
        """
        subscription_entity = webhook_event.payload.get('payload', {}).get('subscription', {}).get('entity', {})
        razorpay_subscription_id = subscription_entity.get('id')

        if not razorpay_subscription_id:
            raise ValueError(f"{webhook_event.event_type} webhook payload missing payload.subscription.entity.id")

        # select_for_update() + atomic(): the same concurrency protection
        # _reconcile_payment already uses for Purchase/Order -- a
        # subscription.charged retry racing itself, or racing a future
        # subscription.activated for the same subscription, serializes on
        # this lock rather than both reading a stale pre-update row.
        with transaction.atomic():
            subscription = Subscription.objects.select_for_update().filter(
                razorpay_subscription_id=razorpay_subscription_id
            ).first()

            if not subscription:
                webhook_event.status = WebhookEvent.Status.FAILED
                webhook_event.error_message = f"No Subscription found for razorpay_subscription_id={razorpay_subscription_id}"
                webhook_event.processed_at = timezone.now()
                webhook_event.save(update_fields=['status', 'error_message', 'processed_at'])
                logger.warning(
                    f"Razorpay webhook: no matching Subscription for razorpay_subscription_id="
                    f"{razorpay_subscription_id}, event_id={webhook_event.razorpay_event_id}."
                )
                return

            if self._is_stale_subscription_event(webhook_event, razorpay_subscription_id):
                # Finding B (Phase 3.4.3.1 audit fix): an earlier-generated
                # event arrived after a later one already updated this
                # subscription (Razorpay's delivery is at-least-once and
                # explicitly NOT guaranteed to be ordered) -- status/period
                # sync is skipped so a stale snapshot never overwrites
                # already-applied, newer state. A subscription.charged
                # payment is still recorded below regardless of staleness --
                # see _record_subscription_charge's docstring for why that
                # must never be gated by this check.
                logger.info(
                    f"Razorpay webhook: stale subscription event, status/period sync skipped "
                    f"(razorpay_subscription_id={razorpay_subscription_id}, event_type={webhook_event.event_type}, "
                    f"event_id={webhook_event.razorpay_event_id})."
                )
            else:
                self._sync_subscription_state(webhook_event.event_type, subscription, subscription_entity)

            if webhook_event.event_type == 'subscription.charged':
                # The only subscription event that records a payment --
                # per the approved scope, subscription.completed does not,
                # even though Razorpay may also attach a payment entity to
                # it (that same final charge already arrives, and is
                # recorded, via its own subscription.charged delivery).
                # Deliberately NOT gated by the staleness check above -- a
                # real charge is a real charge regardless of whether the
                # status snapshot bundled alongside it in the same webhook
                # is stale; skipping payment recording because of that
                # would silently lose real revenue, not just a display value.
                self._record_subscription_charge(webhook_event, subscription)

            webhook_event.status = WebhookEvent.Status.PROCESSED
            webhook_event.processed_at = timezone.now()
            webhook_event.save(update_fields=['status', 'processed_at'])
            logger.info(
                f"Razorpay webhook reconciled against Subscription {subscription.id} "
                f"(razorpay_subscription_id={razorpay_subscription_id}, event_type={webhook_event.event_type}, "
                f"event_id={webhook_event.razorpay_event_id})."
            )

    def _is_stale_subscription_event(self, webhook_event, razorpay_subscription_id):
        """
        Finding B (Phase 3.4.3.1 audit fix). select_for_update() in
        _reconcile_subscription only serializes truly SIMULTANEOUS
        deliveries -- it does nothing for two deliveries that arrive
        minutes apart in the wrong order (Razorpay's own docs: delivery is
        at-least-once and explicitly NOT guaranteed to be ordered), e.g. a
        delayed subscription.authenticated arriving after a
        subscription.activated for the same subscription already advanced
        it to ACTIVE.

        Razorpay's webhook ENVELOPE carries its own top-level `created_at`
        (confirmed against Razorpay's current webhook documentation --
        distinct from payload.subscription.entity.created_at, which is
        when the SUBSCRIPTION itself was created, a different value) --
        this is the only reliable signal for "was this event generated
        before or after the one we last applied". A simple rule like
        "never move to an earlier-looking status" was considered and
        rejected: several Razorpay statuses legitimately recur over a
        subscription's life (ACTIVE <-> PENDING during a renewal retry,
        ACTIVE <-> PAUSED via pause/resume) -- a pure status-based rule
        would incorrectly block those genuine transitions too, not just
        stale ones. Timestamp comparison handles both cases correctly.

        Compares against the most recently PROCESSED subscription.*
        webhook event already recorded for this SAME
        razorpay_subscription_id -- read from the EXISTING WebhookEvent
        table (no new field/migration: every event's full payload,
        envelope included, is already stored there via the Phase 3.2
        mechanism). Computed in Python rather than via ORM-level ordering
        on the JSON path, to avoid relying on numeric JSON ordering
        behaving identically across SQLite (this project's dev/test
        backend) and PostgreSQL (production) -- equality filtering on a
        JSONField key path is well-established and portable, verified
        directly against this project's backend before relying on it;
        ordering by an extracted JSON number is a subtler, less portable
        guarantee this avoids needing at all.
        """
        current_created_at = webhook_event.payload.get('created_at')
        if current_created_at is None:
            # No envelope timestamp to compare against -- can't determine
            # staleness, so apply the event rather than silently discard
            # information that might be perfectly valid.
            return False

        # NOTE the double "payload": the outer one is the WebhookEvent.payload
        # model field (the entire webhook envelope, {"event": ..., "payload":
        # {...}, "created_at": ...}); the inner one is Razorpay's own JSON
        # key holding {"subscription": {"entity": {...}}} -- matches exactly
        # how _reconcile_subscription itself reads the entity a few lines
        # above (webhook_event.payload.get('payload', {})...). Verified
        # directly against this project's DB backend (see the empirically-
        # confirmed JSONField key-transform query used here) before relying
        # on it, per the "don't assume SQLite equals Postgres" instruction --
        # this is a plain equality lookup, not an ordering operation, which
        # is the well-supported, portable case for JSONField key transforms.
        prior_payloads = WebhookEvent.objects.filter(
            event_type__in=self.SUBSCRIPTION_EVENT_TYPES,
            payload__payload__subscription__entity__id=razorpay_subscription_id,
            status=WebhookEvent.Status.PROCESSED,
        ).exclude(pk=webhook_event.pk).values_list('payload', flat=True)

        latest_applied_created_at = None
        for payload in prior_payloads:
            candidate = payload.get('created_at') if isinstance(payload, dict) else None
            if candidate is not None and (latest_applied_created_at is None or candidate > latest_applied_created_at):
                latest_applied_created_at = candidate

        if latest_applied_created_at is None:
            return False  # first subscription event ever applied for this subscription

        return current_created_at < latest_applied_created_at

    def _sync_subscription_state(self, event_type, subscription, subscription_entity):
        """
        Applies exactly what Razorpay's own subscription entity reports --
        status and period timestamps -- the same "never invent, only
        mirror" principle CreateSubscriptionView/VerifySubscriptionPaymentView
        already follow (Phase 3.4.2), reusing their exact
        _map_razorpay_subscription_status/_datetime_from_unix helpers so
        there is only ever one place that understands Razorpay's status
        vocabulary. access_until is deliberately never touched here --
        what it means and how it's derived is explicit access-control
        logic, out of scope for this phase. Only called for a non-stale
        event -- see _is_stale_subscription_event above.
        """
        if subscription.status in Subscription.TERMINAL_STATUSES:
            # Finding B (Phase 3.4.3.1 audit fix), defense-in-depth on top
            # of the timestamp-based staleness check above: a terminal
            # subscription (cancelled/expired/completed) never transitions
            # again in Razorpay's own model, matching
            # Subscription.TERMINAL_STATUSES' existing definition
            # elsewhere in this codebase. This guard costs nothing (uses
            # only the already-loaded subscription.status) and doesn't
            # depend on trusting Razorpay's envelope timestamp to be
            # well-formed.
            logger.info(
                f"Razorpay webhook: Subscription {subscription.id} is already in a terminal state "
                f"({subscription.status}), status/period sync skipped for event_type={event_type}."
            )
            return

        # Finding A (Phase 3.4.3.1 audit fix): default=None here, NOT the
        # CREATED fallback _map_razorpay_subscription_status uses for its
        # other caller (subscription creation). A missing/unrecognized
        # status in a webhook leaves the existing local status untouched --
        # applying CREATED to an existing subscription would be a silent,
        # incorrect downgrade, never a safe default in this context.
        mapped_status = _map_razorpay_subscription_status(subscription_entity.get('status'), default=None)
        if mapped_status is not None:
            subscription.status = mapped_status

        # Phase 3.4.3.2: only ever assign a period timestamp when the
        # webhook actually provided a valid one -- current_start/
        # current_end can legitimately be missing/null on some events (a
        # subscription not yet in an active billing cycle), and
        # _datetime_from_unix now also tolerates a malformed value by
        # returning None (see its docstring) -- in both cases that must
        # leave the existing, previously-synced period value untouched
        # rather than blanking it out to None.
        new_period_start = _datetime_from_unix(subscription_entity.get('current_start'))
        if new_period_start is not None:
            subscription.current_period_start = new_period_start

        new_period_end = _datetime_from_unix(subscription_entity.get('current_end'))
        if new_period_end is not None:
            subscription.current_period_end = new_period_end

        if event_type == 'subscription.cancelled' and not subscription.cancelled_at:
            # Razorpay's own ended_at timestamp is the authoritative record
            # of when cancellation actually took effect -- preferred over
            # timezone.now() (webhook receipt time), which could be
            # skewed by a delayed delivery or retry. Only ever set once
            # (never overwritten by a later, out-of-order duplicate
            # delivery of the same or another terminal event).
            subscription.cancelled_at = _datetime_from_unix(subscription_entity.get('ended_at')) or timezone.now()

        # Phase 3.4.5: Natya's own 3-day grace period, explicitly separate
        # from Razorpay's own retry schedule (PENDING/HALTED just mean
        # "Razorpay is retrying" / "Razorpay has given up retrying" --
        # neither carries any Natya-specific timing). Subscription.access_until
        # is the exact field the model's own docstring reserved for this:
        # "the single field access-control logic will read in a later
        # sub-phase... left entirely unpopulated... only the column exists
        # so later phases don't need another migration" -- this is that
        # phase. courses/services/access.py's _valid_subscription_filter
        # already prioritizes access_until over current_period_end when
        # set, unchanged since Phase 3.4.4 -- no access-control code needed
        # any change at all for this to take effect.
        #
        # Set ONCE per trouble episode (guarded by "access_until is still
        # None"), not on every PENDING/HALTED delivery -- otherwise a
        # repeated Razorpay retry notification would keep pushing the
        # deadline forward, silently turning a bounded 3-day grace period
        # into an unbounded one. A PENDING -> HALTED transition (Razorpay
        # giving up retries) does NOT reset the clock either, for the same
        # reason -- the grace period is anchored to when trouble first
        # began, not to how Razorpay's own retry state escalates.
        #
        # Cleared on recovery (back to ACTIVE) so a healthy subscription's
        # access reverts to being governed by current_period_end alone,
        # exactly as before this phase. Deliberately NOT touched for
        # CANCELLED/EXPIRED/COMPLETED/PAUSED/CREATED/AUTHENTICATED --
        # cancellation uses current_period_end via the existing, unchanged
        # logic (see CancelSubscriptionView), and a subscription already in
        # grace that then gets cancelled should not have its grace deadline
        # silently extended by the cancellation.
        newly_entered_grace = False
        if subscription.status in (Subscription.Status.PENDING, Subscription.Status.HALTED):
            if subscription.access_until is None:
                subscription.access_until = timezone.now() + datetime.timedelta(days=SUBSCRIPTION_GRACE_PERIOD_DAYS)
                newly_entered_grace = True
        elif subscription.status == Subscription.Status.ACTIVE:
            if subscription.access_until is not None:
                subscription.access_until = None

        subscription.save()

        if newly_entered_grace:
            from .tasks import notify_subscription_grace_period_expired
            notify_subscription_grace_period_expired.apply_async(
                args=[subscription.id, int(subscription.access_until.timestamp())],
                eta=subscription.access_until,
            )

    def _record_subscription_charge(self, webhook_event, subscription):
        """
        subscription.charged is the only subscription event that creates a
        SubscriptionPayment. The payment entity Razorpay includes alongside
        the subscription entity for this event is the sole, authoritative
        source for the payment id/amount/currency/status -- never the
        client, and never the plan's list price (the actual charged amount
        can legitimately differ from it -- a plan price change after the
        subscription started, for example).
        """
        payment_entity = webhook_event.payload.get('payload', {}).get('payment', {}).get('entity', {})
        razorpay_payment_id = payment_entity.get('id')

        if not razorpay_payment_id:
            raise ValueError("subscription.charged webhook payload missing payload.payment.entity.id")

        amount_paise = payment_entity.get('amount')
        if amount_paise is None:
            raise ValueError("subscription.charged webhook payload missing payload.payment.entity.amount")

        # Idempotency, checked up front: SubscriptionPayment.razorpay_payment_id
        # is globally unique -- a Razorpay retry delivering the exact same
        # charge again must not create a second row. This is the common
        # case (a plain retry of an already-fully-processed event) and is
        # cheap to short-circuit before ever attempting a write.
        if SubscriptionPayment.objects.filter(razorpay_payment_id=razorpay_payment_id).exists():
            logger.info(
                f"subscription.charged duplicate ignored: razorpay_payment_id={razorpay_payment_id} "
                f"already recorded (event_id={webhook_event.razorpay_event_id})."
            )
            return

        # Phase 3.4.3.2: Razorpay's full Payment entity status vocabulary
        # (confirmed against Razorpay's current API documentation) is
        # created, authorized, captured, refunded, failed -- not just
        # captured/failed. Treating everything other than 'captured' as
        # FAILED (the original 3.4.3.1 behavior) would misrepresent a
        # payment that's merely not yet resolved (created/authorized) --
        # or, in principle, one that was captured and later refunded -- as
        # an outright failure. Only an EXPLICIT 'failed' maps to FAILED;
        # only an explicit 'captured' maps to SUCCESS; anything else
        # (including 'created', 'authorized', 'refunded', or a future
        # value Razorpay might add) maps to the model's existing CREATED
        # status -- "recorded, not yet confirmed either way" is accurate
        # for all of them, and correct: 'refunded' handling belongs to the
        # Phase 3.7 refunds work (SubscriptionPayment.Status.REFUNDED
        # exists on the model for schema completeness only -- no code in
        # this phase, or the one before it, is permitted to set it).
        payment_status = payment_entity.get('status')
        mapped_status = _map_razorpay_payment_status(payment_status)
        if payment_status not in ('captured', 'failed'):
            logger.warning(
                f"subscription.charged payment status is neither 'captured' nor 'failed' "
                f"(status={payment_status!r}) -- recorded as CREATED, not assumed SUCCESS or FAILED "
                f"(razorpay_payment_id={razorpay_payment_id}, event_id={webhook_event.razorpay_event_id})."
            )
        is_successful_charge = mapped_status == SubscriptionPayment.Status.SUCCESS

        # The create() is still wrapped in its own atomic() savepoint as
        # the race-safe backstop behind the upfront check above -- the
        # exact pattern already used for WebhookEvent's own duplicate-event
        # handling (Phase 3.2) and VerifySubscriptionPaymentView's
        # SubscriptionPayment creation (Phase 3.4.2 hardening patch). The
        # select_for_update() lock _reconcile_subscription already holds on
        # `subscription` serializes two concurrent deliveries of the same
        # charge for the same subscription; this savepoint additionally
        # covers the (vanishingly unlikely, but possible) case of the same
        # razorpay_payment_id ever appearing against a different
        # subscription row, without poisoning the outer transaction.
        try:
            with transaction.atomic():
                SubscriptionPayment.objects.create(
                    subscription=subscription,
                    razorpay_payment_id=razorpay_payment_id,
                    razorpay_subscription_id=subscription.razorpay_subscription_id,
                    amount=Decimal(amount_paise) / 100,
                    currency=payment_entity.get('currency') or subscription.plan.currency,
                    status=mapped_status,
                    paid_at=_datetime_from_unix(payment_entity.get('created_at')) if is_successful_charge else None,
                )
        except IntegrityError:
            logger.info(
                f"subscription.charged duplicate ignored (race): razorpay_payment_id={razorpay_payment_id} "
                f"already recorded by a concurrent delivery (event_id={webhook_event.razorpay_event_id})."
            )


from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.pagination import PageNumberPagination
from users.permissions import IsSuperAdminOrAdmin
from .serializers import AdminPurchaseSerializer

class StandardResultsSetPagination(PageNumberPagination):
    page_size = 10
    page_size_query_param = 'page_size'
    max_page_size = 100

class AdminPurchaseViewSet(viewsets.ModelViewSet):
    queryset = Purchase.objects.all().select_related('user', 'course').order_by('-created_at')
    serializer_class = AdminPurchaseSerializer
    permission_classes = [IsSuperAdminOrAdmin]
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
        # get_object() runs the standard DRF lookup + permission check
        # (IsSuperAdminOrAdmin) against the unlocked row first; the actual
        # mutation below re-fetches under select_for_update() so two
        # admins (or a double-click) hitting this at the same moment can't
        # both observe PENDING and both fire fulfillment/notifications.
        target = self.get_object()
        from .services import fulfill_purchase
        with transaction.atomic():
            purchase = Purchase.objects.select_for_update().get(pk=target.pk)
            previous_status = purchase.status
            purchase.status = Purchase.Status.SUCCESS
            purchase.save()
            fulfill_purchase(purchase, previous_status)

        return Response({"message": "Successfully marked as paid and course enrolled!"})


# ---------------------------------------------------------------------------
# Phase 3.3: Bundles + Orders. A second, PARALLEL payment path alongside
# CreateOrderView/VerifyPaymentView above -- deliberately not merged with
# them (see PHASE3_PAYMENTS_FINANCE_PLAN.md Part C / the Phase 3.3 brief's
# "Architecture Principle": legacy Purchase and new Order run side by side).
# ---------------------------------------------------------------------------

from courses.models import Bundle
from .serializers import BundleSerializer, OrderSerializer
from .models import Order, OrderItem


class IsSuperAdminOrAdminOrReadOnly(permissions.BasePermission):
    """
    Bundles are public catalog data -- ANY visitor can browse/read them,
    matching CourseViewSet.get_queryset's actual public-read posture for
    published courses exactly (published courses are visible to anonymous
    requests too; a Next.js server component -- see frontend/src/app/
    bundles/page.tsx, mirroring courses/page.tsx -- fetches server-side
    with no cookies forwarded, so a stricter "must be authenticated to
    even read" rule would make that page always render empty, logged in
    or not). Only Admin/Super Admin can create/edit/deactivate one --
    Bundle management is deliberately NOT a new custom frontend surface,
    it reuses Django's own admin site (see BundleAdmin in courses/admin.py),
    so write access here exists only as a safety net / for any future
    programmatic use, not as the primary management path.
    """
    def has_permission(self, request, view):
        if request.method in permissions.SAFE_METHODS:
            return True
        return bool(
            request.user and request.user.is_authenticated
            and (request.user.is_superuser or request.user.is_staff)
        )


class BundleViewSet(viewsets.ModelViewSet):
    serializer_class = BundleSerializer
    permission_classes = [IsSuperAdminOrAdminOrReadOnly]

    def get_queryset(self):
        qs = Bundle.objects.all().prefetch_related('courses')
        user = self.request.user
        if user.is_authenticated and (user.is_superuser or user.is_staff):
            return qs
        # Non-admins only ever see active bundles in the list/detail --
        # is_purchasable (courses must ALSO all be published) is still
        # surfaced per-bundle in the response so a storefront can show
        # "coming soon" rather than hiding an almost-ready bundle entirely.
        return qs.filter(is_active=True)


class OrderViewSet(viewsets.ModelViewSet):
    """
    The new multi-item checkout. create() accepts one or more {course_id}
    / {bundle_id} line items, prices everything server-side, creates the
    Razorpay order, and returns it the same shape CreateOrderView already
    returns (plus the created Order) so the frontend can reuse the exact
    same Razorpay-modal-open pattern CheckoutButton.tsx already uses.

    No PUT/PATCH/DELETE anywhere -- a student can create and read their own
    orders and verify payment on them; nothing about an Order's price,
    status, or line items is ever client-writable (OrderSerializer marks
    every field read_only, matching AdminPurchaseSerializer's precedent).
    """
    serializer_class = OrderSerializer
    permission_classes = [IsAuthenticated]
    http_method_names = ['get', 'post', 'head', 'options']

    def get_queryset(self):
        user = self.request.user
        qs = Order.objects.all().select_related('user').prefetch_related('items__course', 'items__bundle')
        if user.is_superuser or user.is_staff:
            return qs
        # Security boundary: a student can only ever see their OWN orders --
        # never another user's, admin or not.
        return qs.filter(user=user)

    def create(self, request, *args, **kwargs):
        items_data = request.data.get('items')
        if not items_data or not isinstance(items_data, list):
            return Response({"error": "items is required and must be a non-empty list."}, status=status.HTTP_400_BAD_REQUEST)

        resolved_items = []
        seen_course_ids = set()
        seen_bundle_ids = set()

        for raw_item in items_data:
            if not isinstance(raw_item, dict):
                return Response({"error": "Each item must be an object with a course_id or bundle_id."}, status=status.HTTP_400_BAD_REQUEST)

            course_id = raw_item.get('course_id')
            bundle_id = raw_item.get('bundle_id')

            # Exactly one of course_id/bundle_id -- never both, never neither.
            if bool(course_id) == bool(bundle_id):
                return Response({"error": "Each item must specify exactly one of course_id or bundle_id."}, status=status.HTTP_400_BAD_REQUEST)

            if course_id:
                if course_id in seen_course_ids:
                    return Response({"error": f"Duplicate course_id {course_id} in items."}, status=status.HTTP_400_BAD_REQUEST)
                seen_course_ids.add(course_id)
                try:
                    course = Course.objects.get(id=course_id, is_published=True)
                except (Course.DoesNotExist, ValueError, TypeError):
                    return Response({"error": f"Course {course_id} not found or not available for purchase."}, status=status.HTTP_400_BAD_REQUEST)

                # Same "already own it" guard as CreateOrderView (Phase
                # 3.1), for consistency between the two checkout paths --
                # applies only to a plain course item, never to a bundle
                # (partial bundle ownership is explicitly allowed, see
                # fulfill_order()).
                already_owns = (
                    Enrollment.objects.filter(user=request.user, course=course).exists()
                    or Purchase.objects.filter(user=request.user, course=course, status=Purchase.Status.SUCCESS).exists()
                )
                if already_owns:
                    return Response({"error": f"You already have access to '{course.title}'."}, status=status.HTTP_400_BAD_REQUEST)

                resolved_items.append({
                    'item_type': OrderItem.ItemType.COURSE, 'course': course, 'bundle': None,
                    'title_snapshot': course.title, 'unit_price': course.price,
                })
            else:
                if bundle_id in seen_bundle_ids:
                    return Response({"error": f"Duplicate bundle_id {bundle_id} in items."}, status=status.HTTP_400_BAD_REQUEST)
                seen_bundle_ids.add(bundle_id)
                try:
                    bundle = Bundle.objects.get(id=bundle_id)
                except (Bundle.DoesNotExist, ValueError, TypeError):
                    return Response({"error": f"Bundle {bundle_id} not found."}, status=status.HTTP_400_BAD_REQUEST)
                if not bundle.is_purchasable:
                    return Response({"error": f"Bundle '{bundle.name}' is not currently available for purchase."}, status=status.HTTP_400_BAD_REQUEST)

                resolved_items.append({
                    'item_type': OrderItem.ItemType.BUNDLE, 'course': None, 'bundle': bundle,
                    'title_snapshot': bundle.name, 'unit_price': bundle.price,
                })

        if not resolved_items:
            return Response({"error": "No valid items provided."}, status=status.HTTP_400_BAD_REQUEST)

        # Server-calculated, never trusting anything the client submitted --
        # the client only ever sent course_id/bundle_id above.
        subtotal = sum((i['unit_price'] for i in resolved_items), Decimal('0'))
        discount_amount = Decimal('0')
        total_amount = subtotal - discount_amount

        try:
            # Everything -- the local Order/OrderItem rows AND the Razorpay
            # order creation call -- lives inside one atomic() block. If
            # Razorpay's API call fails, the whole block (including the
            # just-created Order/OrderItems) rolls back, so there's no
            # orphaned local Order with no way to ever be paid. (This is
            # slightly more robust than the legacy CreateOrderView, which
            # creates the Razorpay order BEFORE the local Purchase row for
            # historical reasons -- not changed here, only improved upon
            # for the new path.)
            with transaction.atomic():
                order = Order.objects.create(
                    user=request.user, subtotal=subtotal, discount_amount=discount_amount,
                    total_amount=total_amount, currency='INR', status=Order.Status.PENDING,
                )
                for item in resolved_items:
                    OrderItem.objects.create(
                        order=order,
                        item_type=item['item_type'],
                        course=item['course'],
                        bundle=item['bundle'],
                        title_snapshot=item['title_snapshot'],
                        unit_price=item['unit_price'],
                        quantity=1,
                        total_price=item['unit_price'],
                    )

                amount_in_paise = int(total_amount * 100)
                razorpay_order = client.order.create({
                    "amount": amount_in_paise,
                    "currency": "INR",
                    "payment_capture": "1",
                })
                order.razorpay_order_id = razorpay_order['id']
                order.save(update_fields=['razorpay_order_id'])
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        order.refresh_from_db()
        serializer = self.get_serializer(order)
        return Response(
            {
                **serializer.data,
                "razorpay": {
                    "order_id": order.razorpay_order_id,
                    "amount": amount_in_paise,
                    "currency": order.currency,
                    "key_id": settings.RAZORPAY_KEY_ID,
                },
            },
            status=status.HTTP_201_CREATED,
        )

    @action(detail=True, methods=['post'])
    def verify(self, request, pk=None):
        """
        Mirrors VerifyPaymentView exactly (same select_for_update()+atomic()
        locking, same already-PAID short-circuit, same signature-failure
        handling) -- just for Order instead of Purchase. get_object() above
        already scopes this to the caller's own orders (or any order, for
        an admin), so a student can never verify someone else's order.
        """
        order_lookup = self.get_object()
        razorpay_payment_id = request.data.get('razorpay_payment_id')
        razorpay_order_id = request.data.get('razorpay_order_id')
        razorpay_signature = request.data.get('razorpay_signature')

        if not all([razorpay_payment_id, razorpay_order_id, razorpay_signature]):
            return Response({"error": "Missing payment details"}, status=status.HTTP_400_BAD_REQUEST)

        from .services import fulfill_order

        try:
            with transaction.atomic():
                order = Order.objects.select_for_update().get(pk=order_lookup.pk, razorpay_order_id=razorpay_order_id)

                if order.status == Order.Status.PAID:
                    return Response({"message": "Payment verified and courses enrolled!"})

                previous_status = order.status

                params_dict = {
                    'razorpay_order_id': razorpay_order_id,
                    'razorpay_payment_id': razorpay_payment_id,
                    'razorpay_signature': razorpay_signature,
                }
                client.utility.verify_payment_signature(params_dict)

                order.razorpay_payment_id = razorpay_payment_id
                order.razorpay_signature = razorpay_signature
                order.status = Order.Status.PAID
                order.save()

                fulfill_order(order, previous_status)

            return Response({"message": "Payment verified and courses enrolled!"})

        except razorpay.errors.SignatureVerificationError:
            if 'order' in locals():
                order.status = Order.Status.FAILED
                order.save()
            return Response({"error": "Invalid Payment Signature"}, status=status.HTTP_400_BAD_REQUEST)
        except Order.DoesNotExist:
            return Response({"error": "Order not found"}, status=status.HTTP_404_NOT_FOUND)
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


# ---------------------------------------------------------------------------
# Phase 3.4.2: Razorpay Subscription creation + checkout verification.
#
# Scope is deliberately narrow -- creates a Razorpay Subscription for a
# server-selected SubscriptionPlan, stores the local Subscription row, and
# verifies the signature from the FIRST checkout payment. Does NOT grant
# course access (that's a later sub-phase, driven off Subscription.status/
# access_until, never off the mere existence of a Subscription row), does
# NOT implement webhook-driven recurring reconciliation (Phase 3.4.3), does
# NOT touch Purchase/Order/Bundle fulfillment at all.
# ---------------------------------------------------------------------------

import datetime
from django.core.exceptions import ValidationError
from .models import SubscriptionPlan, Subscription, SubscriptionPayment


# Razorpay requires every subscription to be bounded by total_count (or
# end_at) -- verified against Razorpay's current Subscriptions API docs and
# the installed SDK during the Phase 3.4 audit: there is no native
# "indefinite/until cancelled" subscription concept, and Razorpay caps
# subscription duration at roughly 100 years regardless of billing
# interval. The approved business rule ("a student subscribes until they
# cancel") is modeled here as the largest bounded total_count that stays
# within that ~100-year ceiling for each interval -- purely an
# implementation detail to satisfy Razorpay's API contract. It is never
# surfaced to admins/students as a real limit (nobody will organically
# reach cycle 1200 of a monthly plan) and never described anywhere as
# "unlimited". Only total_count is ever sent, never end_at alongside it --
# Razorpay's docs are explicit a subscription may specify one or the
# other, never both.
SUBSCRIPTION_TOTAL_COUNT_BY_INTERVAL = {
    SubscriptionPlan.BillingInterval.MONTHLY: 1200,  # 100 years x 12 cycles/year
    SubscriptionPlan.BillingInterval.YEARLY: 100,     # 100 years x 1 cycle/year
}

# Phase 3.4.5: Natya's own grace period after a subscription enters a
# payment-trouble state (PENDING/HALTED), deliberately separate from and
# not derived from Razorpay's own retry schedule -- see
# RazorpayWebhookView._sync_subscription_state for where this is applied.
SUBSCRIPTION_GRACE_PERIOD_DAYS = 3

# Razorpay's own subscription-status strings (lowercase) mapped to this
# app's Subscription.Status choices -- used both at creation and after
# checkout verification, so a subscription's local status is always set
# from what Razorpay's API actually reports, never guessed/hardcoded (e.g.
# never assumed ACTIVE just because it was created or a payment verified).
RAZORPAY_SUBSCRIPTION_STATUS_MAP = {
    'created': Subscription.Status.CREATED,
    'authenticated': Subscription.Status.AUTHENTICATED,
    'active': Subscription.Status.ACTIVE,
    'pending': Subscription.Status.PENDING,
    'halted': Subscription.Status.HALTED,
    'paused': Subscription.Status.PAUSED,
    'cancelled': Subscription.Status.CANCELLED,
    'expired': Subscription.Status.EXPIRED,
    'completed': Subscription.Status.COMPLETED,
}


def _map_razorpay_subscription_status(razorpay_status, default=Subscription.Status.CREATED):
    """
    Phase 3.4.3.1 audit fix (Finding A). `default` exists because this
    helper has two genuinely different callers with different correct
    behavior for "Razorpay sent no recognized status":

    - Subscription CREATION (CreateSubscriptionView, and
      VerifySubscriptionPaymentView's post-checkout status refresh) --
      a brand new subscription with no real status yet reasonably
      defaults to CREATED. These callers don't pass `default`, so they
      keep this exact behavior, unchanged.

    - Webhook STATE SYNCHRONIZATION (_sync_subscription_state) -- for an
      EXISTING subscription there is no safe default: silently falling
      back to CREATED would downgrade an already-active/halted/paused
      subscriber on nothing more than a missing or unrecognized status
      string. That caller passes default=None and must leave the
      existing local status untouched when None comes back, rather than
      applying None as a value.
    """
    return RAZORPAY_SUBSCRIPTION_STATUS_MAP.get(razorpay_status, default)


# Phase 3.4.3.2. Razorpay's Payment entity status vocabulary (confirmed
# against Razorpay's current API documentation): created, authorized,
# captured, refunded, failed -- a strict superset of the two values the
# original 3.4.3.1 implementation assumed (captured/anything-else-is-
# failed). Only 'captured' is a genuine success and only 'failed' is a
# genuine failure for the purposes of a SubscriptionPayment row; every
# other value maps to SubscriptionPayment.Status.CREATED (see
# _map_razorpay_payment_status's docstring for why).
RAZORPAY_PAYMENT_STATUS_MAP = {
    'captured': SubscriptionPayment.Status.SUCCESS,
    'failed': SubscriptionPayment.Status.FAILED,
}


def _map_razorpay_payment_status(razorpay_payment_status):
    """
    Maps a Razorpay Payment entity's `status` (as seen in
    payload.payment.entity.status on a subscription.charged webhook) to
    this app's SubscriptionPayment.Status. Deliberately NOT a simple
    success/failure boolean: 'created' and 'authorized' both mean "not yet
    resolved", not "failed" -- treating them as FAILED would misrepresent
    a payment that might still succeed. 'refunded' is also NOT treated as
    FAILED (a refunded payment did originally succeed) nor as SUCCESS
    (money was returned) -- SubscriptionPayment.Status.REFUNDED exists on
    the model for schema completeness only; no code before Phase 3.7 is
    permitted to set it, so 'refunded' also falls through to CREATED here,
    same as any other value this function doesn't explicitly recognize.
    CREATED is a safe, honest "recorded, not confirmed either way" default
    for all of these -- never SUCCESS, never FAILED, on an unrecognized or
    not-yet-final status.
    """
    return RAZORPAY_PAYMENT_STATUS_MAP.get(razorpay_payment_status, SubscriptionPayment.Status.CREATED)


def _datetime_from_unix(value):
    """Razorpay reports current_start/current_end/created_at as Unix
    seconds (or None/absent when not yet applicable) -- converts to an
    aware datetime for storage, or None.

    Phase 3.4.3.2: tolerant of a malformed value (present, but not a real
    Unix timestamp -- a string, an out-of-range number, etc.) -- returns
    None rather than raising, so one corrupt timestamp field in an
    otherwise-valid webhook doesn't abort the whole reconciliation. Callers
    that assign the result directly to a model field (e.g. period sync)
    already treat None as "no valid value to apply" -- see
    _sync_subscription_state, which never overwrites existing valid period
    data with a None result.
    """
    if value is None:
        return None
    try:
        return datetime.datetime.fromtimestamp(value, tz=datetime.timezone.utc)
    except (TypeError, ValueError, OSError, OverflowError):
        return None


@method_decorator(csrf_exempt, name='dispatch')
class CreateSubscriptionView(APIView):
    """
    Creates a Razorpay Subscription for the authenticated student against a
    server-selected SubscriptionPlan, plus a local Subscription row
    mirroring it. Mirrors OrderViewSet.create()'s ordering: the local row
    is created first (nothing external yet, status=CREATED), Razorpay is
    called inside the same atomic() block, then the local row is updated
    with what Razorpay returned -- so a Razorpay failure rolls back the
    whole transaction and never leaves an orphaned local row with no
    razorpay_subscription_id.
    """
    authentication_classes = [JWTCookieAuthentication, CsrfExemptSessionAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request):
        plan_id = request.data.get('plan_id')
        if not plan_id:
            return Response({"error": "plan_id is required"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            plan = SubscriptionPlan.objects.get(id=plan_id)
        except (SubscriptionPlan.DoesNotExist, ValueError, TypeError):
            return Response({"error": "Subscription plan not found."}, status=status.HTTP_404_NOT_FOUND)

        if not plan.is_active:
            return Response({"error": "This subscription plan is not currently available."}, status=status.HTTP_400_BAD_REQUEST)

        if not plan.razorpay_plan_id:
            # Admin hasn't linked this plan to a real Razorpay Plan yet
            # (see SubscriptionPlanAdmin) -- fail safely rather than ever
            # calling Razorpay without a real plan id.
            return Response({"error": "This subscription plan is not yet available for purchase."}, status=status.HTTP_400_BAD_REQUEST)

        # Same rule Subscription.clean() enforces at the DB layer -- checked
        # here too so a student who already has one never triggers a live
        # Razorpay subscription.create() call only to have it rolled back a
        # moment later.
        if Subscription.objects.filter(user=request.user).exclude(status__in=Subscription.TERMINAL_STATUSES).exists():
            return Response({"error": "You already have an active subscription."}, status=status.HTTP_400_BAD_REQUEST)

        # Hardening fix (post-audit): .get() instead of [] -- a
        # SubscriptionPlan with an unexpected billing_interval (only
        # reachable via data that bypassed the admin's own choice
        # validation, since SubscriptionPlan doesn't call full_clean() on
        # save()) now gets a clean 400 instead of an uncaught KeyError.
        total_count = SUBSCRIPTION_TOTAL_COUNT_BY_INTERVAL.get(plan.billing_interval)
        if total_count is None:
            logger.error(
                f"SubscriptionPlan {plan.id} has an unrecognized billing_interval={plan.billing_interval!r}; "
                f"refusing to create a Razorpay subscription."
            )
            return Response({"error": "This subscription plan is misconfigured. Please contact support."}, status=status.HTTP_400_BAD_REQUEST)

        razorpay_subscription = None
        try:
            with transaction.atomic():
                subscription = Subscription.objects.create(
                    user=request.user,
                    plan=plan,
                    status=Subscription.Status.CREATED,
                )

                razorpay_subscription = client.subscription.create({
                    "plan_id": plan.razorpay_plan_id,
                    "total_count": total_count,
                    "quantity": 1,
                    "customer_notify": 1,
                })

                subscription.razorpay_subscription_id = razorpay_subscription['id']
                subscription.razorpay_plan_id = plan.razorpay_plan_id
                subscription.status = _map_razorpay_subscription_status(razorpay_subscription.get('status'))
                subscription.current_period_start = _datetime_from_unix(razorpay_subscription.get('current_start'))
                subscription.current_period_end = _datetime_from_unix(razorpay_subscription.get('current_end'))
                subscription.save()
        except (ValidationError, IntegrityError):
            # The model-level defense-in-depth check (ValidationError, via
            # Subscription.save()'s full_clean()) or the database's own
            # partial UniqueConstraint (IntegrityError -- reachable under
            # real concurrent Postgres transactions, where full_clean()'s
            # own non-locking uniqueness check can't see another
            # in-flight, not-yet-committed request) caught a race the
            # earlier explicit check missed (two near-simultaneous create
            # requests from the same student). Either can only be raised
            # by the FIRST Subscription.objects.create() call above,
            # before Razorpay is ever reached -- razorpay_subscription is
            # still None, no cleanup is needed, and the constraint itself
            # is untouched.
            return Response({"error": "You already have an active subscription."}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            if razorpay_subscription is not None:
                # Razorpay's subscription WAS created, but persisting/
                # updating the local record afterward failed -- the
                # atomic() block above has already rolled back (no
                # orphaned local row), but Razorpay now has a live,
                # un-tracked subscription that would otherwise silently
                # sit there. Best-effort cleanup: cancel it immediately.
                # A cleanup failure is logged separately and never masks
                # the original error -- either way the student sees a
                # clean failure response.
                logger.error(
                    f"Razorpay subscription {razorpay_subscription.get('id')} was created but the local "
                    f"Subscription record failed to save (user={request.user.id}, plan={plan.id}): {e}",
                    exc_info=True,
                )
                try:
                    client.subscription.cancel(razorpay_subscription['id'])
                    logger.warning(
                        f"Orphaned Razorpay subscription {razorpay_subscription.get('id')} was cancelled "
                        f"after a local save failure."
                    )
                except Exception as cleanup_error:
                    logger.error(
                        f"Failed to cancel orphaned Razorpay subscription {razorpay_subscription.get('id')}: "
                        f"{cleanup_error}",
                        exc_info=True,
                    )
            else:
                logger.error(
                    f"Subscription creation failed for user={request.user.id}, plan={plan.id}: {e}",
                    exc_info=True,
                )
            return Response(
                {"error": "Unable to complete subscription setup. Please try again or contact support."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        return Response({
            "subscription_id": subscription.razorpay_subscription_id,
            "razorpay_key_id": settings.RAZORPAY_KEY_ID,
            "plan_id": plan.id,
            "amount": int(plan.price * 100),
            "currency": plan.currency,
        }, status=status.HTTP_201_CREATED)


@method_decorator(csrf_exempt, name='dispatch')
class VerifySubscriptionPaymentView(APIView):
    """
    Verifies the signature Razorpay Checkout returns after the FIRST
    subscription payment (the authentication charge), and records that one
    payment as a SubscriptionPayment. This is explicitly NOT the webhook-
    driven recurring-payment reconciliation path (Phase 3.4.3) -- this view
    only ever processes the one checkout response it is called with, using
    client.utility.verify_subscription_payment_signature (a distinct SDK
    method from order/webhook verification -- HMAC over
    "payment_id|subscription_id" keyed by RAZORPAY_KEY_SECRET, never the
    webhook secret).
    """
    authentication_classes = [JWTCookieAuthentication, CsrfExemptSessionAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request):
        razorpay_payment_id = request.data.get('razorpay_payment_id')
        razorpay_subscription_id = request.data.get('razorpay_subscription_id')
        razorpay_signature = request.data.get('razorpay_signature')

        if not all([razorpay_payment_id, razorpay_subscription_id, razorpay_signature]):
            return Response({"error": "Missing payment details"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            with transaction.atomic():
                subscription = Subscription.objects.select_for_update().get(
                    razorpay_subscription_id=razorpay_subscription_id
                )

                # 404, not 403 -- never reveal that a razorpay_subscription_id
                # belonging to another student even exists, matching this
                # codebase's existing queryset-scoping-as-isolation
                # convention (e.g. OrderViewSet.get_queryset()).
                if subscription.user_id != request.user.id:
                    return Response({"error": "Subscription not found"}, status=status.HTTP_404_NOT_FOUND)

                # Idempotent short-circuit BEFORE re-verifying the signature
                # -- mirrors VerifyPaymentView/OrderViewSet.verify()'s exact
                # already-processed pattern. Also the race-safety net: two
                # concurrent verify calls for the same payment serialize on
                # the select_for_update() lock above; the second sees this
                # row once the first has committed.
                if SubscriptionPayment.objects.filter(
                    subscription=subscription, razorpay_payment_id=razorpay_payment_id
                ).exists():
                    return Response({"message": "Subscription payment already verified."})

                client.utility.verify_subscription_payment_signature({
                    'razorpay_subscription_id': subscription.razorpay_subscription_id,
                    'razorpay_payment_id': razorpay_payment_id,
                    'razorpay_signature': razorpay_signature,
                })

                # Signature is valid -- refresh from Razorpay's own current
                # subscription state rather than assuming/hardcoding ACTIVE
                # just because a payment was verified (the state a fresh
                # subscription is actually in immediately after its first
                # checkout is "authenticated", not "active" -- billing
                # activation is a separate Razorpay-driven transition).
                try:
                    razorpay_subscription = client.subscription.fetch(subscription.razorpay_subscription_id)
                except Exception as e:
                    logger.error(
                        f"Subscription payment {razorpay_payment_id} signature verified, but fetching current "
                        f"Razorpay subscription state failed for {subscription.razorpay_subscription_id}: {e}",
                        exc_info=True,
                    )
                    return Response(
                        {"error": "Payment verified, but your subscription status could not be confirmed. Please contact support."},
                        status=status.HTTP_502_BAD_GATEWAY,
                    )

                subscription.status = _map_razorpay_subscription_status(razorpay_subscription.get('status'))
                subscription.current_period_start = _datetime_from_unix(razorpay_subscription.get('current_start'))
                subscription.current_period_end = _datetime_from_unix(razorpay_subscription.get('current_end'))
                subscription.save()

                # Amount/currency come from the server-side plan, never the
                # client -- a subscription's first charge is exactly the
                # plan's price (Razorpay has no partial/prorated first
                # charge in this flow), a trusted source that avoids an
                # extra client.payment.fetch() call.
                #
                # Hardening fix (post-audit): the idempotency check above
                # is scoped to (subscription, razorpay_payment_id), but
                # SubscriptionPayment.razorpay_payment_id is globally
                # unique (not scoped to a subscription) -- an unexpected
                # cross-subscription payment-id collision would otherwise
                # raise an uncaught IntegrityError here. The create() is
                # wrapped in its own atomic() savepoint (the established
                # pattern from RazorpayWebhookView's duplicate-event
                # handling) so catching that IntegrityError doesn't poison
                # the outer transaction, which still needs to commit the
                # subscription.status update made just above.
                try:
                    with transaction.atomic():
                        SubscriptionPayment.objects.create(
                            subscription=subscription,
                            razorpay_payment_id=razorpay_payment_id,
                            razorpay_subscription_id=subscription.razorpay_subscription_id,
                            amount=subscription.plan.price,
                            currency=subscription.plan.currency,
                            status=SubscriptionPayment.Status.SUCCESS,
                            paid_at=timezone.now(),
                        )
                except IntegrityError:
                    logger.error(
                        f"SubscriptionPayment creation failed for razorpay_payment_id={razorpay_payment_id}: "
                        f"a payment record with this id already exists (unexpected cross-subscription "
                        f"collision -- the pre-check above only looked at this specific subscription).",
                        exc_info=True,
                    )
                    return Response({"error": "This payment has already been recorded."}, status=status.HTTP_409_CONFLICT)

            return Response({"message": "Subscription payment verified."})

        except razorpay.errors.SignatureVerificationError:
            return Response({"error": "Invalid Payment Signature"}, status=status.HTTP_400_BAD_REQUEST)
        except Subscription.DoesNotExist:
            return Response({"error": "Subscription not found"}, status=status.HTTP_404_NOT_FOUND)
        except Exception as e:
            # Hardening fix (post-audit): never return raw exception text
            # for an unanticipated failure -- full detail still goes to
            # the server log, the client only ever sees a safe, generic
            # message.
            logger.error(f"Subscription payment verification failed: {e}", exc_info=True)
            return Response(
                {"error": "Unable to verify subscription payment. Please try again or contact support."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


# ---------------------------------------------------------------------------
# Phase 3.4.5: student subscription cancellation + grace period.
#
# Both views below derive the subscription entirely from request.user --
# neither ever accepts a subscription id (local or Razorpay's) from the
# client, so there is no ownership check to get wrong: a student can only
# ever act on whichever non-terminal Subscription row (if any) actually
# belongs to them.
# ---------------------------------------------------------------------------

from .serializers import SubscriptionSerializer


@method_decorator(csrf_exempt, name='dispatch')
class SubscriptionMeView(APIView):
    """
    GET /api/orders/subscriptions/me/ -- the authenticated user's own
    current (non-terminal) subscription, or 404 if they don't have one.
    Read-only; existence of this endpoint is what CancelSubscriptionView's
    frontend consumer needs to know what to show before offering to cancel.
    """
    authentication_classes = [JWTCookieAuthentication, CsrfExemptSessionAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request):
        subscription = Subscription.objects.select_related('plan').filter(
            user=request.user
        ).exclude(status__in=Subscription.TERMINAL_STATUSES).first()

        if not subscription:
            return Response({"error": "You do not have an active subscription."}, status=status.HTTP_404_NOT_FOUND)

        return Response(SubscriptionSerializer(subscription).data)


@method_decorator(csrf_exempt, name='dispatch')
class CancelSubscriptionView(APIView):
    """
    POST /api/orders/subscriptions/cancel/ -- cancels the authenticated
    user's own current (non-terminal) subscription, at the end of the
    current billing cycle (Razorpay's cancel_at_cycle_end=1 -- confirmed
    against Razorpay's current Cancel Subscription API documentation
    before using it: default is cancel_at_cycle_end=0/immediate, so this
    must be passed explicitly). Access is NOT revoked here or by this
    change at all -- courses/services/access.py's existing, unmodified
    read-time logic already grants access through current_period_end (or
    access_until, if a grace period is active) regardless of what
    Subscription.status says at any given moment, so a student keeps
    access for exactly as long as approved business rule #2/#3 require
    with zero special-casing needed in this view beyond setting
    cancel_at_period_end/cancelled_at.

    Deliberately does NOT set Subscription.status to CANCELLED itself --
    that remains the existing, approved subscription.cancelled webhook's
    job alone (Phase 3.4.3, unmodified), which will confirm the actual
    status transition once Razorpay processes it (at cycle end, per
    cancel_at_cycle_end=1) -- this view only ever records that
    cancellation was REQUESTED and the local fields that are genuinely
    known right now (cancel_at_period_end, cancelled_at).
    """
    authentication_classes = [JWTCookieAuthentication, CsrfExemptSessionAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request):
        try:
            with transaction.atomic():
                subscription = Subscription.objects.select_related('plan').select_for_update().filter(
                    user=request.user
                ).exclude(status__in=Subscription.TERMINAL_STATUSES).first()

                if not subscription:
                    return Response({"error": "You do not have an active subscription to cancel."}, status=status.HTTP_404_NOT_FOUND)

                if subscription.cancel_at_period_end:
                    # Idempotent: a second cancellation request (double-click,
                    # client retry) is a no-op success, not an error, and
                    # never calls Razorpay's cancel API again.
                    return Response(SubscriptionSerializer(subscription).data)

                if not subscription.razorpay_subscription_id:
                    # Shouldn't be reachable in practice (every Subscription
                    # gets one at creation, see CreateSubscriptionView) --
                    # refuse rather than ever calling Razorpay with an
                    # invalid/empty subscription id.
                    logger.error(f"Subscription {subscription.id} has no razorpay_subscription_id; cancellation refused.")
                    return Response(
                        {"error": "This subscription cannot be cancelled right now. Please contact support."},
                        status=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    )

                try:
                    client.subscription.cancel(subscription.razorpay_subscription_id, {"cancel_at_cycle_end": 1})
                except Exception as e:
                    logger.error(
                        f"Razorpay subscription cancellation failed for subscription {subscription.id} "
                        f"(razorpay_subscription_id={subscription.razorpay_subscription_id}): {e}",
                        exc_info=True,
                    )
                    return Response(
                        {"error": "Unable to cancel your subscription right now. Please try again or contact support."},
                        status=status.HTTP_502_BAD_GATEWAY,
                    )

                subscription.cancel_at_period_end = True
                subscription.cancelled_at = timezone.now()
                subscription.save()

            return Response(SubscriptionSerializer(subscription).data)
        except Exception as e:
            logger.error(f"Subscription cancellation failed unexpectedly: {e}", exc_info=True)
            return Response(
                {"error": "Unable to cancel your subscription right now. Please try again or contact support."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


# ---------------------------------------------------------------------------
# Phase 3.4.6: subscription REST API layer -- public plan catalog + the
# authenticated user's own payment history. Nothing here creates, mutates,
# or grants anything (no Enrollment, no status/access_until change) -- this
# phase is read-only API surface on top of everything 3.4.1-3.4.5 already
# built.
# ---------------------------------------------------------------------------

from rest_framework import generics
from .serializers import SubscriptionPlanSerializer, SubscriptionPaymentSerializer
from .models import SubscriptionPayment


class SubscriptionPlanViewSet(viewsets.ReadOnlyModelViewSet):
    """
    GET /api/orders/subscription-plans/ and .../{id}/ -- public catalog of
    subscription plans, mirroring BundleViewSet's exact read posture
    (published/active catalog data is public, matching CourseViewSet's own
    precedent for published courses). Read-only (ReadOnlyModelViewSet, not
    ModelViewSet) -- unlike Bundle, no API write path was added: plan
    management already works via Django's existing SubscriptionPlanAdmin,
    and adding one here isn't required by this phase and would edge into
    "admin dashboard" territory it explicitly excludes.

    AllowAny (not IsSuperAdminOrAdminOrReadOnly): there is no write action
    on this viewset for that class's admin-write branch to ever gate, so a
    plain AllowAny is the simpler, equally-correct choice here -- the admin
    queryset bypass below is what actually lets staff/superuser preview an
    inactive/unlinked plan, independent of the permission class.
    """
    serializer_class = SubscriptionPlanSerializer
    permission_classes = [permissions.AllowAny]

    def get_queryset(self):
        qs = SubscriptionPlan.objects.all().prefetch_related('courses')
        user = self.request.user
        if user.is_authenticated and (user.is_superuser or user.is_staff):
            return qs
        # Non-admins only ever see plans that are both active AND actually
        # purchasable (linked to a real Razorpay Plan) -- showing a plan
        # CreateSubscriptionView would immediately reject ("not yet
        # available for purchase") serves no one.
        return qs.filter(is_active=True, razorpay_plan_id__isnull=False)


class SubscriptionPaymentHistoryView(generics.ListAPIView):
    """
    GET /api/orders/subscriptions/payments/ -- the authenticated user's own
    SubscriptionPayment history, across ALL of their subscriptions
    (current and historical/terminal alike -- a cancelled subscription's
    past payments remain part of the student's own payment history). Never
    another user's: the queryset is scoped to request.user alone, and no
    query parameter is ever used to select a different user or
    subscription, so there is no ownership check to get wrong.
    """
    serializer_class = SubscriptionPaymentSerializer
    authentication_classes = [JWTCookieAuthentication, CsrfExemptSessionAuthentication]
    permission_classes = [IsAuthenticated]
    pagination_class = StandardResultsSetPagination

    def get_queryset(self):
        return SubscriptionPayment.objects.filter(
            subscription__user=self.request.user
        ).select_related('subscription', 'subscription__plan').order_by('-created_at')
