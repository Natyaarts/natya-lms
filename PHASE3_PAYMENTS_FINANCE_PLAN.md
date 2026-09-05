# Phase 3 — Payments & Finance: Audit + Architecture + Implementation Plan

> Read-only audit and planning document. No code, migrations, or configuration was
> changed to produce this. Prepared against the actual current codebase (verified via
> direct code reads, not the earlier `ARCHITECTURE_PROPOSAL.md` draft, which is
> superseded by everything below wherever the two disagree).

---

## PART A — Current Codebase Audit (verified facts)

### `orders` app — the entire current payment system

**`orders/models.py`** — `Purchase` is the *only* model in the app:

```python
class Purchase(models.Model):
    user = models.ForeignKey(User, related_name='purchases', on_delete=models.CASCADE)
    course = models.ForeignKey(Course, related_name='purchases', on_delete=models.CASCADE)
    razorpay_order_id = models.CharField(max_length=255, blank=True, null=True)
    razorpay_payment_id = models.CharField(max_length=255, blank=True, null=True)
    razorpay_signature = models.CharField(max_length=255, blank=True, null=True)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    status = models.CharField(max_length=50, default='PENDING')  # PENDING, SUCCESS, FAILED
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
```

Important, previously-unverified details:
- **`status` has no `choices=` enum** — it's a bare `CharField`, the `# PENDING, SUCCESS, FAILED` is only a comment. Nothing at the DB or model layer stops an arbitrary string being saved.
- **No `Meta` class at all** — no ordering, no indexes, no unique constraints on `razorpay_order_id` or `(user, course)`.
- **Single course only** — `course` is a plain FK, not M2M. This is *the* structural reason bundles need a new model.
- **One migration total** (`orders/migrations/0001_initial.py`) — the model has never changed since creation.

**`orders/views.py`** — three view classes, fully quoted/verified:
- `CreateOrderView` (`IsAuthenticated`, JWT-cookie auth) — derives `amount_in_paise` from `Course.price` **at request time**, creates the Razorpay order, then `Purchase.objects.create(..., status='PENDING')`. No dedup against an existing PENDING purchase for the same user/course.
- `VerifyPaymentView` (`IsAuthenticated`) — looks up `Purchase.objects.get(razorpay_order_id=..., user=request.user)`, verifies via Razorpay SDK's `client.utility.verify_payment_signature(...)`, sets `status='SUCCESS'`, calls `fulfill_purchase(purchase, previous_status)`. On `SignatureVerificationError`, sets `status='FAILED'`.
- `AdminPurchaseViewSet` (`IsSuperAdminOrAdmin`, paginated 10/page, filter by `?status=`/`?search=`) — standard ModelViewSet (though `AdminPurchaseSerializer` marks every field read-only, so create/update via API is a no-op) plus a `mark_paid` action that sets `status='SUCCESS'` and calls `fulfill_purchase`.

**`orders/services.py`** — `fulfill_purchase(purchase, previous_status)` is the **single centralized fulfillment function**, already built in Phase 0 specifically to stop duplicated enrollment logic:
```python
def fulfill_purchase(purchase, previous_status):
    if purchase.status != 'SUCCESS':
        return
    Enrollment.objects.get_or_create(user=purchase.user, course=purchase.course)
    NotificationService.trigger_payment_success(purchase, previous_status)
```
Called from exactly **4 places**: `orders/views.py` (`VerifyPaymentView`, `AdminPurchaseViewSet.mark_paid`) and `users/views.py` (`AdminUserViewSet.assign_course`, `AdminUserViewSet.mark_purchase_paid`). This is the correct, single hook point for any new Phase 3 fulfillment logic (bundles, subscriptions) to extend rather than duplicate.

**`courses.models.LiveBatchStudent.purchase`** — the existing live-batch↔purchase relationship Phase 3 must not break:
```python
purchase = models.ForeignKey('orders.Purchase', on_delete=models.SET_NULL, null=True, blank=True,
                              related_name='live_batch_assignments', db_index=True)
```
Nullable, `SET_NULL` — deleting a `Purchase` never cascades into deleting a `LiveBatchStudent` row.

**Webhooks: confirmed absent entirely.** A repo-wide grep for "webhook" (case-insensitive) returns zero matches anywhere in `backend/`. No `RAZORPAY_WEBHOOK_SECRET`, no route, no handler. This is 100% new work.

**`RAZORPAY_KEY_ID`/`RAZORPAY_KEY_SECRET`** — confirmed env-driven, `os.getenv(..., '')`, used only inside `orders/views.py` (module-level `razorpay.Client(...)` singleton). No other app touches Razorpay directly.

### Phase 1 identity model — verified exact current state (do not trust the old proposal doc's guesses)

**`TeacherProfile`** and **`MentorProfile`** (both `users/models.py`) — **neither has any financial field whatsoever.** No `hourly_rate`, no `payout_method`, no bank details, nothing. Fields are purely profile/bio content: `bio`, `profile_image`, `specialization`, `qualifications`, `experience_years`, `languages` (JSON list), plus `short_intro`/`is_public` (teacher) or `availability_status`/`social_links` (mentor), `is_active`, timestamps. The earlier `ARCHITECTURE_PROPOSAL.md` guessed these fields exist — **they do not.** Any payout-relevant fields must be added fresh (see Part D).

**`CourseInstructor`** (`courses/models.py`) — the real course-ownership relationship, already built in Phase 0/1:
```python
class CourseInstructor(models.Model):
    class InstructorRole(models.TextChoices):
        TEACHER = "TEACHER", "Teacher"
        MENTOR = "MENTOR", "Mentor"
        ASSISTANT = "ASSISTANT", "Assistant"
    course = models.ForeignKey(Course, related_name='instructors', on_delete=models.CASCADE)
    user = models.ForeignKey(User, related_name='course_instructor_roles', on_delete=models.CASCADE)
    role = models.CharField(max_length=20, choices=InstructorRole.choices, default=InstructorRole.TEACHER)
    is_primary = models.BooleanField(default=False, help_text="Primary instructor for revenue/payout attribution (future use).")
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        unique_together = ('course', 'user', 'role')
```
**`is_primary` was explicitly designed as the future payout-attribution hook** — it exists today, is populated by the backfill migration (`is_primary=True` for every backfilled TEACHER row), but **nothing currently reads it for any financial purpose.** This is exactly the field Phase 3's earnings attribution should key off.

**Role model confirmed**: `is_teacher`, `is_student`, `is_mentor` are three independent plain `BooleanField`s directly on `User` — no separate role table, nothing stops a user having more than one simultaneously. `IsSuperAdminOrAdmin` treats Django's own `is_staff` as "Admin." None of the 8 permission classes in `users/permissions.py` have any `has_object_permission` (all are view-level only), and none reference finance concepts.

**Confirmed absent, repo-wide**: no `Payout`, `Commission`, `Earning`, `Ledger`, or `Wallet` model/view/serializer exists anywhere. The only hits for those words are the two forward-looking `is_primary` help-text comments quoted above.

### Notifications — the pattern Phase 3 must extend, not duplicate

`NotificationType` (`notifications/models.py`) has **exactly 7 values**: `COURSE_UPDATE`, `ENROLLMENT`, `PAYMENT`, `ANNOUNCEMENT`, `COURSE_COMPLETION`, `CERTIFICATE`, `LIVE_CLASS`. **There is no `REFUND` or `PAYOUT` type yet** — these need to be added (additive `choices=` extension, not a schema change, since `notification_type` is already a `CharField(choices=...)` — adding new choices is a migration-free-at-the-DB-level, code-only change... actually adding new `choices` values to an existing `TextChoices` **is** technically a field-definition change that Django's `makemigrations` may flag depending on whether `choices` participates in the migration state — it will generate a no-op-at-the-DB-level `AlterField` migration since `choices` isn't enforced by Postgres; safe, additive, no data risk).

`NotificationService.create_notification`'s idempotency mechanism is a **try/except `IntegrityError`** pattern (attempt create with the `idempotency_key`, on unique-constraint violation look up and return the existing row), not `get_or_create`. Important for webhook design: this means concurrent duplicate calls are already race-safe at the DB level via the unique constraint, not just app-level logic.

`trigger_payment_success(purchase, previous_status)` is the exact template to follow for `trigger_refund`/`trigger_payout` — guards on a status *transition* (not a poll), defers via `transaction.on_commit()` when inside an atomic block, wraps the actual create call in try/except-log-don't-raise, and keys the idempotency string off the domain object's id + event name (`f"payment:{purchase.id}:success"`).

**No `CELERY_BEAT_SCHEDULE` exists anywhere in the codebase.** Confirmed by repo-wide grep. Every existing scheduled task (live-class reminders) uses `apply_async(eta=...)` — a one-off scheduled call, not a recurring beat job. **Any Phase 3 feature needing a truly periodic job (subscription renewal checks, payout batch generation) requires adding Celery Beat from scratch** — a new dependency/process (a beat scheduler process alongside the existing worker), not just new task code. This is a real infrastructure addition, not just an app-level change, and should be flagged to whoever manages the EB/worker environment before Phase 3.4+ ships.

### Frontend — verified exact current state

- **`CheckoutButton.tsx`** — full, correct flow already: create-order → Razorpay JS modal → verify-payment → redirect. Amount/currency/key are **always** sourced from the backend's order-creation response, never hardcoded client-side. Confirmed no Razorpay key anywhere in frontend source.
- **`admin/payments/page.tsx`** (`PaymentsLedger`) — search + status filter + 10/page pagination + a "Mark as Paid" action hitting `/api/orders/purchases-admin/{id}/mark_paid/`. No refund action anywhere in this file.
- **`admin/users/[id]/page.tsx`** "Fees" tab — a **second, separate** purchases view with its **own separate** mark-paid endpoint (`/api/users/admin-users/{id}/mark_purchase_paid/` — different URL than the ledger page's action, though both now funnel through the same `fulfill_purchase()` server-side per Phase 0's fix). Also has completely separate "Assign Course"/"Unassign Course" actions that grant `Enrollment` directly with **no `Purchase` row created at all** — a free/comp-access path that exists today and will never appear in any purchase-based finance report unless Phase 3 explicitly decides to represent it (flagged as a business decision in Part D/J).
- **`admin/page.tsx`** — read-only revenue/payment stats already exist (`total_revenue`, `success_payments`, `pending_payments`, `failed_payments`, `revenue_breakdown`, `recent_payments`) from `/api/users/admin-stats/` — this is the existing aggregate-endpoint pattern Phase 3's finance dashboard should extend, not replace.
- **Confirmed completely absent**: no student-facing payment-history page, no invoice view, no subscription page, no refund-request UI anywhere in `frontend/src`.

### Mobile — verified exact current state

**Zero payment code exists.** The "Buy Course" button and the locked-lesson prompt both simply `Linking.openURL('https://academy.natyaarts.com/courses/{id}')` out to the web app — explicitly, per an in-code comment, to avoid Google Play Billing's 30% cut. No Razorpay SDK, no order/verify calls, no payment-history screen.

**Mobile auth is JWT bearer tokens in `Authorization` header (from `AsyncStorage`), not the web's session-cookie+CSRF scheme.** Any new Phase 3 mobile-facing endpoint (e.g. a read-only payment-history screen, if ever added) must work under bearer-token auth, which DRF's `JWTCookieAuthentication` already supports as a fallback (it's the same `dj_rest_auth`/SimpleJWT stack), but this needs explicit verification before assuming any new endpoint "just works" for mobile.

---

## PART B — Existing vs New (per Phase 3 feature)

| Feature | Exists | Partial | Missing | Reuse | Extend | New model/API/UI |
|---|:---:|:---:|:---:|---|---|---|
| 1. One-time payment hardening | ✓ | | | `Purchase`, `fulfill_purchase`, existing views | Add `choices=` to `status`, add dedup-on-create, add row locking on verify | — |
| 2. Razorpay webhooks | | | ✓ | Razorpay SDK client, `fulfill_purchase` pattern | — | `WebhookEvent` model, new endpoint, new settings var |
| 3. Payment reconciliation | | | ✓ | `AdminPurchaseViewSet` pattern, Razorpay SDK read APIs | `AdminPurchaseViewSet` gets a reconciliation action | New admin endpoint/UI |
| 4. Payment history | ✓ (admin only) | ✓ | student-facing missing | `AdminPurchaseSerializer` pattern | New serializer scoped to `request.user` | New student API + page |
| 5. Subscriptions | | | ✓ | `fulfill_purchase` pattern, notifications | — | `Subscription`, `SubscriptionPlan`, new endpoints, Celery Beat |
| 6. Bundles | | | ✓ | `Course` M2M-able, `Enrollment.get_or_create` loop | — | `Order`, `OrderItem` |
| 7. Orders/OrderItems | | | ✓ (needed for bundles/subscriptions only) | — | — | `Order`, `OrderItem` |
| 8. Invoices | | | ✓ | S3 storage already configured | — | `Invoice` model, generation hook, PDF |
| 9. Refunds | | | ✓ | Razorpay SDK, `Purchase.status` | Add `REFUNDED` status value | `Refund` model, endpoints, admin UI |
| 10. Teacher payouts | | | ✓ | `CourseInstructor.is_primary`, `TeacherProfile` | Add payout-method field | `LedgerEntry`, `Payout` (shared, see Part D) |
| 11. Mentor payouts | | | ✓ | same as above | same as above | same models, no separate model |
| 12. Commission calculation | | | ✓ | `CourseInstructor` | Add `commission_rate` field | `LedgerEntry` computation logic |
| 13. Finance ledger | | | ✓ | — | — | `LedgerEntry` model |
| 14. Finance dashboard | ✓ (revenue-only) | ✓ | payouts/refunds/ledger view missing | `AdminStatsView` pattern | New aggregate actions | New admin page sections |
| 15. Revenue reports | ✓ (basic) | ✓ | breakdown by course/instructor/period missing | `AdminStatsView` | Extend with query params | — |
| 16. Payout reports | | | ✓ | `AdminStatsView` pattern | — | New endpoint |
| 17. Student payment history | | | ✓ | `AdminPurchaseSerializer` shape | New student-scoped serializer | New API + page |
| 18. Admin payment management | ✓ | | | `AdminPurchaseViewSet`, `admin/payments` page | Add refund action, consolidate the 2 mark-paid endpoints (optional) | — |
| 19. Payment/refund/payout notifications | ✓ (payment only) | ✓ | refund/payout types missing | `NotificationService`, `trigger_payment_success` pattern | Add `REFUND`/`PAYOUT` `NotificationType` values, add `trigger_refund`/`trigger_payout` | — |

---

## PART C — Final Payment Architecture

### What remains in `Purchase`
**Everything, unchanged.** `Purchase` continues to represent exactly one thing: a single-course Razorpay payment. Every existing field, every existing call site (`CreateOrderView`, `VerifyPaymentView`, `AdminPurchaseViewSet`, `fulfill_purchase`, `LiveBatchStudent.purchase`, the admin ledger UI, the per-user Fees tab) keeps working with zero changes to its contract. The only additive change: give `status` a real `choices=` enum (`PENDING`/`SUCCESS`/`FAILED`/`REFUNDED`) — a validation tightening, not a behavior change, since no code currently saves any value outside those four strings anyway.

### Is `Order`/`OrderItem` needed?
**Yes, but only for the cases `Purchase` structurally cannot express**: a single checkout covering more than one course (a bundle) or a checkout that establishes a subscription. `Order` never replaces `Purchase` for the existing single-course flow — `CheckoutButton.tsx`'s current single-course path keeps calling `create-order`/`verify-payment` exactly as today, creating a `Purchase` exactly as today. A **new** bundle-checkout flow (new frontend entry point, new backend endpoints) creates an `Order` + N `OrderItem`s instead. They coexist permanently, not as a migration path from one to the other.

### How bundles work
`Bundle` (title, description, `courses` M2M, `price`) is a sellable catalog item. Buying one creates one `Order` (status PENDING → SUCCESS/FAILED, same Razorpay order/payment/signature fields as `Purchase`, `amount` = bundle price) plus one `OrderItem` per course in the bundle (`order` FK, `course` FK, `price_at_purchase` — snapshot, since a bundle's per-course attribution can matter for instructor payouts even though the student paid one bundle price). On `Order` success, a new `fulfill_order(order, previous_status)` in `orders/services.py` — a sibling to `fulfill_purchase`, same shape — loops `order.items.all()` and does the same `Enrollment.get_or_create` + ledger-entry-creation per course that `fulfill_purchase` does for its single course. Both functions should delegate their shared "grant one course + one ledger entry" step to one small private helper to avoid re-duplicating that logic a second time (the exact anti-pattern Phase 0 already fixed once).

### How subscriptions work
**Flag up front: Razorpay Subscriptions is a genuinely different API product from Razorpay Orders** (a recurring mandate with its own `subscription.create`/`subscription.cancel` calls and its own webhook event names — `subscription.activated`, `subscription.charged`, `subscription.cancelled`, `subscription.completed`, etc. — distinct from the `payment.captured`/`order.paid` events the one-time flow would use). This is not "the same Order flow but recurring" — it needs its own SDK integration surface. Recommend treating this as its own sub-phase (3.4) with explicit confirmation of the exact Razorpay Subscriptions API contract against Razorpay's current documentation before writing code (their API surface/webhook payload shape should be verified live, not assumed from memory).

Proposed shape: `SubscriptionPlan` (name, price, interval MONTHLY/YEARLY, `courses` M2M-or-"all-access" flag — **business decision**: does a subscription grant access to specific courses, or the whole catalog?). `Subscription` (user, plan FK, `razorpay_subscription_id`, status ACTIVE/PAST_DUE/CANCELLED/EXPIRED, `current_period_start`/`current_period_end`, `cancel_at_period_end` bool). Each successful recurring charge (delivered via the `subscription.charged` webhook, not a `verify-payment` call from the client — recurring charges happen server-to-server on Razorpay's schedule) creates a lightweight `SubscriptionInvoice`-type record (or reuses `Invoice`, see below) and refreshes `current_period_end`; access is granted/checked by "does the user have an ACTIVE `Subscription` covering this course," not by creating an `Enrollment` row per charge (avoids an ever-growing Enrollment table for a recurring product) — **business decision**: confirm whether existing `Enrollment`-gated features (progress tracking, certificates) need subscriptions to still create/maintain an `Enrollment` row for compatibility, or whether course-access-checking logic needs a new "has active subscription OR has enrollment" check added wherever access is currently gated purely by `Enrollment`.

### How Razorpay entities map to our database

| Razorpay entity | Our model |
|---|---|
| Order (one-time) | `Purchase.razorpay_order_id` (existing) or `Order.razorpay_order_id` (new, bundles) |
| Payment | `Purchase.razorpay_payment_id` / `Order.razorpay_payment_id` |
| Subscription | `Subscription.razorpay_subscription_id` |
| Refund | `Refund.razorpay_refund_id` |
| Webhook event | `WebhookEvent.razorpay_event_id` (dedup key — see below) |

### Payment status transitions
See Part F for the full state machine. Summary: unchanged for `Purchase`/`Order` (`PENDING → SUCCESS`, `PENDING → FAILED`, both now also `SUCCESS → REFUNDED` as a new terminal transition).

### How webhook events are handled, and duplicate prevention
New endpoint `POST /api/orders/webhook/razorpay/` — **unauthenticated** (Razorpay calls it server-to-server, no user session), **CSRF-exempt** (same pattern already used for `CreateOrderView`/`VerifyPaymentView`), but signature-verified using `RAZORPAY_WEBHOOK_SECRET` (a **new, separate** secret from `RAZORPAY_KEY_SECRET` — Razorpay webhooks are signed with their own dedicated secret configured in the Razorpay dashboard, verified via `razorpay.Utility.verify_webhook_signature(payload, signature, webhook_secret)`).

Idempotency: every inbound webhook call first tries to create a `WebhookEvent` row keyed on Razorpay's event id (the exact header/payload field name must be verified against Razorpay's current webhook documentation before implementation — do not assume a name from memory), using the **exact same try/except-`IntegrityError`-on-a-unique-field pattern already proven in `NotificationService.create_notification`** — if the row already exists, return `200 OK` immediately without reprocessing (Razorpay retries on any non-2xx or timeout, so idempotent-and-fast is required). Only after the `WebhookEvent` row is newly created does the handler dispatch to the appropriate status update (`payment.captured` → mirrors what `VerifyPaymentView` already does for the matching `Purchase`/`Order`; `refund.processed` → updates the matching `Refund`; `subscription.charged` → extends the matching `Subscription`). The webhook becomes the **source of truth** for state transitions that can happen without a client ever calling `verify-payment` (e.g., a payment that captures after the browser tab closed) — this directly closes the "Medium" risk flagged in the earlier production audit (abandoned checkouts stranding a paid-but-unenrolled user).

### How refunds work
Admin (or, if the business wants it, a student self-service request — **business decision**) creates a `Refund` row (`status='REQUESTED'`) against a `Purchase` or `Order`. An admin action calls Razorpay's refund API (`client.payment.refund(payment_id, {"amount": ...})`), sets `status='PROCESSING'`, and the **webhook's** `refund.processed` event (not the synchronous API response, which only confirms Razorpay *accepted* the request) is what flips it to `SUCCESS` — refunds are not instantaneous on Razorpay's side. On `SUCCESS`, `Purchase.status`/`Order.status` moves to `REFUNDED`, and a `LedgerEntry` of type `CLAWBACK` is created for any already-attributed instructor earning tied to that purchase (does **not** automatically claw back a `Payout` that already happened — see Part D/J).

### How invoices are generated
On `fulfill_purchase`/`fulfill_order` success (same hook point), create an `Invoice` row referencing the `Purchase` or `Order` (mutually-exclusive nullable FKs, validated exactly-one-set). PDF generation is a Celery task (async, doesn't block the payment-verification response) that renders a template and uploads to S3 via the already-configured `django-storages` backend, storing the resulting URL. **Invoice numbering scheme, GST/tax applicability, and whether invoices are even a legal requirement for this business are explicitly flagged as business/CA decisions in Part D — no tax logic is assumed here.**

### How finance ledger entries are created
Exactly one `LedgerEntry` per (successful purchase/order-item, revenue-eligible `CourseInstructor`) pair, created inside the same `fulfill_purchase`/`fulfill_order` transaction. "Revenue-eligible" defaults to the course's `is_primary=True` `CourseInstructor` row (falls back to "no entry created, flagged for manual admin attribution" if a course has no primary instructor set — this will be common initially since `is_primary` is currently only populated for backfilled legacy teachers, not for every course) — **business decision**: confirm whether non-primary instructors/assistants ever earn a split, and if so what percentage.

### How teacher/mentor earnings are calculated
`LedgerEntry.gross_amount` = the purchase/item amount attributed to that instructor. `LedgerEntry.commission_rate` = a **snapshot** at creation time (never recalculated retroactively if the global rate changes later) read from `CourseInstructor.commission_rate` if set, else a platform default. `commission_amount = gross_amount * commission_rate`. `net_amount = gross_amount - commission_amount` — this net amount is what accumulates toward that instructor's next `Payout`.

### How platform commission is calculated
See above — a rate, not a flat model-level constant, stored per-`CourseInstructor` (override) with a global default fallback. **Exact default percentage is a business decision — not invented here.**

### How payouts are tracked
A `Payout` batches a recipient's un-paid-out `LedgerEntry` rows for a period into one row (`gross_amount`/`commission_amount`/`net_amount` on `Payout` are **sums** of its linked `LedgerEntry`s, not independently entered), moves through DRAFT → APPROVED → PROCESSING → PAID (or → FAILED/CANCELLED), and each included `LedgerEntry.payout` FK is set once batched — this is what makes a payout fully reconcilable back to individual purchases.

### How reconciliation works
A new admin action/report cross-checks: every `Purchase`/`Order` with `status='SUCCESS'` has exactly one corresponding successful Razorpay payment (queried live via the Razorpay SDK's payment-fetch API, not just trusted from our own DB) and exactly the `LedgerEntry`/`Invoice` rows it should have; flags any `Purchase` stuck in `PENDING` for longer than a configurable window (a strong signal of the "abandoned checkout, payment actually captured, webhook never arrived" failure mode) for manual review.

### Idempotency & transaction-safety summary
Every write path that can be called more than once for the same real-world event (webhook retries, a double-click on "mark paid," a race between the client's `verify-payment` call and the webhook for the same payment) is protected by: (a) a DB-level unique constraint + try/except `IntegrityError` (the proven `NotificationService` pattern, reused for `WebhookEvent` dedup and payment-notification dedup), (b) `select_for_update()` row locking on `Purchase`/`Order` during any status-transition write (a **new** hardening — not present today, flagged as a gap in the earlier production audit), and (c) every multi-step fulfillment (grant access + create ledger entry + create invoice + notify) wrapped in one `transaction.atomic()` block so a failure partway through rolls back cleanly rather than leaving a purchase "SUCCESS" with no enrollment.

---

## PART D — Teacher/Mentor Payout Architecture

```
User
 ├── TeacherProfile   (bio/profile data only, today)
 ├── MentorProfile    (bio/profile data only, today)
 ├── CourseInstructor (× many — course ownership, role, is_primary)
 └── Payout           (× many — NEW, one unified model)
```

**One `Payout` model, not `TeacherPayout`/`MentorPayout`.** `Payout.recipient` is a plain FK to `User` — the recipient's `is_teacher`/`is_mentor` flag (or the role on their `CourseInstructor` rows) determines *how* they earned, not *which table* their payout lives in. Payout mechanics (approval, bank transfer, status tracking) don't differ by role, matching the explicit instruction not to split this.

**Course ownership → who earns money**: `CourseInstructor.is_primary=True` for a given course identifies the earning instructor for that course's purchases (today, only backfilled legacy teachers have this set — every newly-assigned `CourseInstructor` needs a deliberate choice of `is_primary` at assignment time, likely defaulting `True` for a course's first/only instructor and requiring explicit admin choice when a second instructor — e.g. an ASSISTANT or a co-teaching MENTOR — is added to an already-owned course).

### New fields required (all additive)

On `CourseInstructor` (courses app):
- `commission_rate` — `DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)` — percentage override for this specific instructor/course; `null` = use platform default.

On `TeacherProfile` / `MentorProfile` (users app) — **both**, mirrored fields:
- `payout_method` — `CharField(choices=[BANK_TRANSFER, UPI, OTHER], blank=True)` — **exact supported methods are a business decision.**
- `payout_details` — a JSON or encrypted-text field holding bank account/UPI id. **Flag clearly: storing raw bank account details requires a decision on encryption-at-rest and PCI/data-handling policy — do not store plaintext account numbers without explicit confirmation of how they'll be protected; this may warrant using a payment processor's own payout/vendor system (e.g. Razorpay Route/Payouts) instead of storing bank details ourselves at all — worth a dedicated business/security discussion before this field is implemented.**

New model, `FinanceSettings` (or a single-row config table) — global default `commission_rate`. **Exact default percentage: business decision, not invented here.**

### `LedgerEntry` fields (defined fully in Part E) cover: commission percentage/fixed commission (rate stored, both percentage-based and a possible flat-fee override — mark flat-fee support as optional/deferred unless confirmed needed), gross amount, discount (if a coupon/discount reduces the purchase price, `gross_amount` should reflect the *actual paid* amount, not list price — coupons are out of Phase 3's listed scope per the original architecture doc's Phase 6, so for now `gross_amount` = `Purchase.amount`/`OrderItem.price_at_purchase` as-is), tax (**explicitly not modeled — business/CA decision on whether commission is calculated pre-tax or post-tax, and whether tax is even collected at all**), platform commission, instructor earning (net), payout status (via the `payout` FK — `null` = not yet batched), payout period (via the parent `Payout.period_start`/`period_end`), payout method (on `Payout`, not per-entry), payout reference (`Payout.reference_number`), payout approval (`Payout.approved_by`/`approved_at`), payout completion (`Payout.paid_at`).

**Explicitly not invented / requires business confirmation before coding:**
- Exact commission percentage (or whether it's a flat fee for some course types)
- Whether commission is calculated before or after tax
- Whether/how GST or other tax applies to instructor payouts at all
- Whether payouts happen automatically (e.g. monthly Celery Beat batch) or are always manually admin-triggered
- Minimum payout threshold (do small balances roll over rather than being paid out immediately?)
- How bank/UPI details are collected and protected (plaintext field vs. encrypted vs. a third-party payout processor)
- Whether a second instructor (ASSISTANT/co-MENTOR) on a course ever earns a split, and by what percentage

---

## PART E — Database Schema

All new models are additive-only (new tables + one additive field on `CourseInstructor`/`TeacherProfile`/`MentorProfile`, one additive `choices=` tightening on `Purchase.status`). Nothing existing is renamed, dropped, or narrowed.

### `Order` (new, `orders` app)
| Field | Type | Rules |
|---|---|---|
| `user` | FK → User | `on_delete=CASCADE`, `related_name='orders'` |
| `order_type` | `CharField` + `TextChoices` | `BUNDLE`, `SUBSCRIPTION` |
| `razorpay_order_id` / `razorpay_payment_id` / `razorpay_signature` | `CharField(255)` | `blank=True, null=True` — mirrors `Purchase` exactly |
| `amount` | `DecimalField(10,2)` | required |
| `status` | `CharField` + `TextChoices` | `PENDING/SUCCESS/FAILED/REFUNDED`, indexed |
| `created_at`/`updated_at` | `DateTimeField` | auto |
| **Meta** | | `indexes=[razorpay_order_id]`, `unique=[razorpay_order_id]` (nullable-safe uniqueness — Django allows multiple NULLs under a unique field) |

### `OrderItem` (new, `orders` app)
| Field | Type | Rules |
|---|---|---|
| `order` | FK → Order | `on_delete=CASCADE`, `related_name='items'` |
| `course` | FK → Course | `on_delete=PROTECT` (don't allow deleting a course with historical order items — differs deliberately from `Purchase.course`'s `CASCADE`, worth confirming as an intentional improvement, not a required match) |
| `price_at_purchase` | `DecimalField(10,2)` | snapshot, required |
| **Meta** | | `unique_together=('order','course')` |

### `Bundle` (new, `courses` app — catalog data, not a transaction)
| Field | Type | Rules |
|---|---|---|
| `title` | `CharField(255)` | required |
| `description` | `TextField` | blank |
| `courses` | `ManyToManyField(Course)` | |
| `price` | `DecimalField(10,2)` | required |
| `is_published` | `BooleanField` | default False |
| `created_at`/`updated_at` | auto | |

### `SubscriptionPlan` (new, `orders` app)
| Field | Type | Rules |
|---|---|---|
| `name` | `CharField(255)` | |
| `interval` | `CharField` + `TextChoices` | `MONTHLY`, `YEARLY` |
| `price` | `DecimalField(10,2)` | |
| `courses` | `ManyToManyField(Course, blank=True)` | empty = all-access (**confirm this convention with business**) |
| `is_active` | `BooleanField` | default True |

### `Subscription` (new, `orders` app)
| Field | Type | Rules |
|---|---|---|
| `user` | FK → User | `CASCADE`, `related_name='subscriptions'` |
| `plan` | FK → SubscriptionPlan | `PROTECT` |
| `razorpay_subscription_id` | `CharField(255)` | `unique=True, null=True, blank=True` |
| `status` | `CharField` + `TextChoices` | `ACTIVE/PAST_DUE/CANCELLED/EXPIRED` |
| `current_period_start`/`current_period_end` | `DateTimeField` | |
| `cancel_at_period_end` | `BooleanField` | default False |
| `created_at`/`updated_at` | auto | |

### `Invoice` (new, `orders` app)
| Field | Type | Rules |
|---|---|---|
| `purchase` | FK → Purchase | `null=True, blank=True`, `SET_NULL` |
| `order` | FK → Order | `null=True, blank=True`, `SET_NULL` |
| `invoice_number` | `CharField(50)` | `unique=True` |
| `amount` | `DecimalField(10,2)` | |
| `tax_amount` | `DecimalField(10,2)` | default 0 — **placeholder only, see Part D tax caveat** |
| `pdf_file` | `FileField` (S3) | `blank=True, null=True` |
| `status` | `CharField` + `TextChoices` | `ISSUED/CANCELLED` |
| `issued_at` | `DateTimeField` | auto_now_add |
| **Constraint** | | `CheckConstraint`: exactly one of `purchase`/`order` is non-null |

### `Refund` (new, `orders` app)
| Field | Type | Rules |
|---|---|---|
| `purchase` | FK → Purchase | `null=True, blank=True`, `SET_NULL` |
| `order` | FK → Order | `null=True, blank=True`, `SET_NULL` |
| `amount` | `DecimalField(10,2)` | required — supports partial refunds (≤ original amount, enforced in `clean()`) |
| `reason` | `TextField` | blank |
| `status` | `CharField` + `TextChoices` | `REQUESTED/PROCESSING/SUCCESS/REJECTED/FAILED` |
| `razorpay_refund_id` | `CharField(255)` | `blank=True, null=True` |
| `requested_by` | FK → User | `SET_NULL, null=True` |
| `processed_by` | FK → User | `SET_NULL, null=True, blank=True` |
| `requested_at` | auto_now_add | |
| `processed_at` | `DateTimeField` | `null=True, blank=True` |
| **Constraint** | | exactly one of `purchase`/`order` non-null |

### `LedgerEntry` (new, `orders` app — the finance ledger)
| Field | Type | Rules |
|---|---|---|
| `course_instructor` | FK → CourseInstructor | `PROTECT`, `related_name='ledger_entries'` |
| `purchase` | FK → Purchase | `null=True, blank=True`, `SET_NULL` |
| `order_item` | FK → OrderItem | `null=True, blank=True`, `SET_NULL` |
| `entry_type` | `CharField` + `TextChoices` | `EARNING/CLAWBACK/ADJUSTMENT` |
| `gross_amount` | `DecimalField(10,2)` | |
| `commission_rate` | `DecimalField(5,2)` | snapshot at creation |
| `commission_amount` | `DecimalField(10,2)` | |
| `net_amount` | `DecimalField(10,2)` | |
| `payout` | FK → Payout | `null=True, blank=True`, `SET_NULL`, `related_name='entries'` — null = not yet batched |
| `created_at` | auto_now_add | |
| **Constraint** | | exactly one of `purchase`/`order_item` non-null; index on `(course_instructor, payout)` for fast "unpaid balance" queries |

### `Payout` (new, `orders` app — unified for teacher and mentor)
| Field | Type | Rules |
|---|---|---|
| `recipient` | FK → User | `PROTECT`, `related_name='payouts'` |
| `period_start`/`period_end` | `DateField` | |
| `gross_amount`/`commission_amount`/`net_amount` | `DecimalField(10,2)` | computed sums of linked `LedgerEntry` rows at batching time |
| `status` | `CharField` + `TextChoices` | `DRAFT/APPROVED/PROCESSING/PAID/FAILED/CANCELLED` |
| `method` | `CharField` + `TextChoices` | `BANK_TRANSFER/UPI/OTHER` |
| `reference_number` | `CharField(255)` | `blank=True` — bank transfer UTR or similar |
| `approved_by` | FK → User | `SET_NULL, null=True, blank=True` |
| `approved_at`/`paid_at` | `DateTimeField` | `null=True, blank=True` |
| `notes` | `TextField` | blank |
| `created_at`/`updated_at` | auto | |

### `WebhookEvent` (new, `orders` app)
| Field | Type | Rules |
|---|---|---|
| `razorpay_event_id` | `CharField(255)` | `unique=True` — dedup key, **exact source field to be confirmed against current Razorpay webhook docs before implementation** |
| `event_type` | `CharField(100)` | e.g. `payment.captured` |
| `payload` | `JSONField` | full raw payload, for audit/replay |
| `processed_at` | `DateTimeField` | `null=True, blank=True` |
| `status` | `CharField` + `TextChoices` | `RECEIVED/PROCESSED/FAILED` |
| `created_at` | auto_now_add | |

### Modified existing models (additive only)
- `Purchase.status` — add `choices=` `TextChoices(PENDING, SUCCESS, FAILED, REFUNDED)` (`AlterField` migration, no data change).
- `CourseInstructor` — add `commission_rate` (nullable `DecimalField`).
- `TeacherProfile`/`MentorProfile` — add `payout_method`, `payout_details` (both nullable/blank — **pending the security decision flagged in Part D**).
- `notifications.NotificationType` — add `REFUND`, `PAYOUT` choices (`AlterField`, no data change).

**Models deliberately NOT created**: `TeacherPayout`/`MentorPayout` (unified into `Payout` per explicit instruction), a generic `Order` replacing `Purchase` (explicitly forbidden), a separate `Commission` model (folded into `LedgerEntry`/`CourseInstructor.commission_rate` — a standalone model would be unnecessary indirection for what's fundamentally one rate value per attribution).

---

## PART F — State Machines

**`Purchase` / `Order`** (unchanged shape, one new terminal state):
```
PENDING → SUCCESS
PENDING → FAILED
SUCCESS → REFUNDED   (new — driven by a Refund reaching SUCCESS)
```

**`Subscription`**:
```
(created) → ACTIVE
ACTIVE → PAST_DUE        (a scheduled charge fails)
PAST_DUE → ACTIVE         (a retried charge succeeds)
PAST_DUE → EXPIRED        (retries exhausted — window is a business decision)
ACTIVE → CANCELLED        (user-initiated, effective at period end if cancel_at_period_end)
```

**`Refund`**:
```
REQUESTED → PROCESSING
PROCESSING → SUCCESS
REQUESTED → REJECTED      (admin declines before ever calling Razorpay)
PROCESSING → FAILED       (Razorpay-side failure)
```

**`Invoice`**:
```
(created) → ISSUED
ISSUED → CANCELLED        (e.g. paired purchase later refunded — business decision on whether to cancel or credit-note instead)
```

**`Payout`**:
```
DRAFT → APPROVED
APPROVED → PROCESSING
PROCESSING → PAID
APPROVED → FAILED → DRAFT   (retry loop)
DRAFT/APPROVED → CANCELLED
```

**`WebhookEvent`**:
```
RECEIVED → PROCESSED
RECEIVED → FAILED   (handler raised — row still exists so the SAME event id is not reprocessed as new; a manual admin retry action re-attempts the same row rather than Razorpay's own retry creating a duplicate)
```

---

## PART G — API Design

### Student
| Method | Path | Permission | Notes |
|---|---|---|---|
| GET | `/api/orders/my-purchases/` | `IsAuthenticated` | list own `Purchase` rows, reuse `AdminPurchaseSerializer` shape minus admin-only fields |
| GET | `/api/orders/my-orders/` | `IsAuthenticated` | own `Order` + nested `OrderItem`s |
| GET | `/api/orders/invoices/` , `/api/orders/invoices/{id}/` | `IsAuthenticated`, scoped to own | list/detail, `{id}/download/` returns the S3 PDF URL |
| GET | `/api/orders/my-subscription/` | `IsAuthenticated` | current active subscription status, or 404 |
| POST | `/api/orders/subscriptions/{id}/cancel/` | `IsAuthenticated`, owner only | sets `cancel_at_period_end=True` |
| GET | `/api/orders/refunds/?purchase={id}` | `IsAuthenticated`, scoped to own | refund status for a purchase |
| POST | `/api/orders/refunds/request/` | `IsAuthenticated` | **only if self-service refund requests are approved by business** — creates `Refund(status='REQUESTED')` |

### Admin
| Method | Path | Permission | Notes |
|---|---|---|---|
| GET | `/api/orders/purchases-admin/` | `IsSuperAdminOrAdmin` | *(existing, unchanged)* |
| POST | `/api/orders/purchases-admin/{id}/mark_paid/` | `IsSuperAdminOrAdmin` | *(existing, unchanged)* |
| GET | `/api/orders/orders-admin/` | `IsSuperAdminOrAdmin` | new, mirrors purchases-admin for `Order` |
| GET | `/api/orders/refunds-admin/` | `IsSuperAdminOrAdmin` | list/filter all refunds |
| POST | `/api/orders/refunds-admin/{id}/process/` | `IsSuperAdminOrAdmin` | triggers Razorpay refund call, sets PROCESSING |
| POST | `/api/orders/refunds-admin/{id}/reject/` | `IsSuperAdminOrAdmin` | |
| GET | `/api/orders/invoices-admin/` | `IsSuperAdminOrAdmin` | |
| GET | `/api/orders/payouts-admin/` | `IsSuperAdminOrAdmin` | filter by recipient/status/period |
| POST | `/api/orders/payouts-admin/generate/` | `IsSuperAdminOrAdmin` | body: `{period_start, period_end}` — batches all unattached `LedgerEntry` rows per recipient into new DRAFT `Payout`s |
| POST | `/api/orders/payouts-admin/{id}/approve/` | `IsSuperAdminOrAdmin` | DRAFT → APPROVED |
| POST | `/api/orders/payouts-admin/{id}/complete/` | `IsSuperAdminOrAdmin` | body: `{reference_number}`, APPROVED/PROCESSING → PAID |
| GET | `/api/orders/finance/dashboard/` | `IsSuperAdminOrAdmin` | extends existing `admin-stats` pattern: revenue, refund total, payout total, outstanding ledger balance |
| GET | `/api/orders/finance/reports/revenue/?start=&end=&group_by=course|instructor|month` | `IsSuperAdminOrAdmin` | |
| GET | `/api/orders/finance/reconciliation/` | `IsSuperAdminOrAdmin` | flags stuck-PENDING purchases/orders older than N hours |
| GET | `/api/orders/webhook-events-admin/` | `IsSuperAdminOrAdmin` | read-only audit view of received webhooks |
| POST | `/api/orders/webhook-events-admin/{id}/retry/` | `IsSuperAdminOrAdmin` | manual reprocess of a FAILED event |
| GET/POST | `/api/courses/bundles-admin/` | `IsSuperAdminOrAdmin` | CRUD for `Bundle` catalog items |
| GET/POST | `/api/orders/subscription-plans-admin/` | `IsSuperAdminOrAdmin` | CRUD for `SubscriptionPlan` |

### Teacher/Mentor
| Method | Path | Permission | Notes |
|---|---|---|---|
| GET | `/api/orders/my-earnings/` | `IsAuthenticated`, `is_teacher or is_mentor` | own `LedgerEntry` rows, summarized by unpaid/paid balance |
| GET | `/api/orders/my-payouts/` | same | own `Payout` history |
| GET | `/api/orders/my-payouts/{id}/` | same, owner only | one payout's included ledger entries |

### Razorpay-facing
| Method | Path | Permission | Notes |
|---|---|---|---|
| POST | `/api/orders/create-order/` | `IsAuthenticated` | *(existing, unchanged)* |
| POST | `/api/orders/verify-payment/` | `IsAuthenticated` | *(existing, unchanged)* |
| POST | `/api/orders/create-bundle-order/` | `IsAuthenticated` | new, same shape as create-order but body is `{bundle_id}`, creates `Order`+`OrderItem`s |
| POST | `/api/orders/verify-bundle-payment/` | `IsAuthenticated` | new, mirrors verify-payment for `Order` |
| POST | `/api/orders/subscribe/` | `IsAuthenticated` | new — creates the Razorpay subscription + local `Subscription` row |
| POST | `/api/orders/webhook/razorpay/` | **`AllowAny`** (signature-verified instead of session-auth) | new, CSRF-exempt, the reconciliation source of truth |

---

## PART H — Frontend Plan

Reuse, don't reinvent: the search+filter+paginated-table pattern from `admin/payments/page.tsx`, the tabbed-detail-page pattern from `admin/users/[id]/page.tsx`, the stat-card grid from `admin/page.tsx`, the CSRF-cookie + `credentials:'include'` fetch convention used everywhere, and the dark `bg-zinc-900`/`#facc15`-accent visual system already established (matching the Phase 2 live-class pages built this session).

**Admin** (`/admin/finance/*`, new section in the sidebar):
- Finance Dashboard — stat cards (total revenue, refunded, payouts pending/paid, outstanding instructor balance) + a revenue-over-time chart, extending `admin/page.tsx`'s existing stats fetch pattern.
- Payments — the existing `admin/payments` page, extended with a "Refund" action per SUCCESS row.
- Payment detail — a drill-in view (currently only a modal; could stay a modal, extended with linked Invoice/Refund/LedgerEntry info).
- Refunds — new list page mirroring the Payments ledger's table/filter pattern.
- Invoices — new list page, download link per row.
- Payouts — new list page (filter by recipient/status/period), "Generate Payouts" button (calls the batch-generation endpoint), per-row Approve/Complete actions.
- Teacher/Mentor earnings — a per-instructor drill-in (reuse the `admin/users/[id]` tab pattern — add an "Earnings" tab there for teacher/mentor-role users).
- Reconciliation — new page listing flagged stuck-PENDING transactions.
- Finance reports — filterable revenue-breakdown table/chart.

**Teacher** (`/admin` area, since teachers already share the admin shell — new nav items "Earnings"/"Payouts" scoped to their own data via the existing role-branching in `admin/layout.tsx`).

**Mentor** — same pattern, own nav items.

**Student**:
- `/dashboard` gains a "Payment History" section or a new `/payments` page (currently completely absent) — reuses the ledger-table visual pattern, scoped to `my-purchases`/`my-orders`.
- Invoices — list + download link.
- Subscription — current plan/status/cancel button, only shown if subscriptions ship.

---

## PART I — Mobile Plan

**No payment functionality should be added to mobile.** Confirmed the existing strategy is deliberate (avoiding Google Play Billing's 30% cut via `Linking.openURL()` to the web checkout) — Phase 3 must not contradict this.

**Remains web-only**: checkout (one-time, bundle, subscription), refund requests, invoice PDF viewing/download, payout management (teacher/mentor payouts are an admin/instructor-facing concern, not relevant to the student mobile app at all).

**What mobile could optionally display** (read-only, low-risk, and NOT required for Phase 3 completion — flag as an optional nice-to-have, not scope): a simple "My Purchases" read-only list reusing the new `GET /api/orders/my-purchases/` endpoint, since mobile's existing JWT-bearer auth pattern (`Authorization: Bearer` header via `AsyncStorage`) is compatible with the same DRF endpoint the web frontend would use — no separate mobile-specific API needed if this is ever requested. No mobile API changes are required for Phase 3's core scope.

---

## PART J — Security / Edge Cases

- **Duplicate Razorpay webhooks**: handled by `WebhookEvent.razorpay_event_id` unique constraint + try/except `IntegrityError` (Part C) — a retried webhook is a guaranteed no-op on the second delivery.
- **Replay attacks**: webhook signature verification (`Utility.verify_webhook_signature`) prevents a forged payload from being accepted; combined with the event-id dedup, a captured-and-replayed *genuine* payload also just no-ops.
- **Signature verification**: order/payment verification already uses Razorpay's official SDK method (confirmed, unchanged) — webhook verification uses the SDK's separate `verify_webhook_signature` method with its own secret, never the payment-verification secret.
- **Amount tampering**: already safe by construction — `CreateOrderView` derives the amount server-side from `Course.price` at order-creation time; the client never supplies an amount. This pattern must be preserved identically for `Order`/`Bundle`/`Subscription` checkout — the new endpoints must derive amount from `Bundle.price`/`SubscriptionPlan.price` server-side, never trust a client-supplied amount.
- **Course price changes after order creation**: already safe — `amount_in_paise` is computed once at order-creation time and stored on both the Razorpay order and `Purchase.amount`; a later price change doesn't retroactively affect an in-flight or completed order. No fix needed, just confirm this invariant is preserved for `Order`/`Bundle` too.
- **Duplicate purchases**: currently **not** prevented — `CreateOrderView` doesn't check for an existing PENDING/SUCCESS purchase for the same user/course before creating a new one. Recommend adding a check (if a SUCCESS purchase already exists, reject; if a PENDING one exists and is recent, reuse it instead of creating a new Razorpay order) as part of Phase 3.1 hardening.
- **Concurrent payment verification**: currently **no row locking** — recommend `select_for_update()` on the `Purchase`/`Order` fetch inside `VerifyPaymentView`/webhook handlers (Phase 3.1/3.2), closing the gap flagged in the earlier production audit.
- **Refund after payout**: the hard case — if `LedgerEntry.payout` is already set (paid out) when a refund succeeds, do **not** try to reverse the completed `Payout`. Instead create a `CLAWBACK` `LedgerEntry` (unattached, `payout=null`) that nets against that instructor's *next* payout batch. **Business decision needed**: is the clawback ever forgiven (e.g. for goodwill/small amounts) or always deducted?
- **Failed refund**: `Refund.status='FAILED'` — stays visible in the admin refund list for manual retry/investigation; does not silently disappear.
- **Partial refund**: `Refund.amount` is independent of the original `Purchase.amount`/`Order.amount` — validated `≤` the refundable remainder (original amount minus any prior refunds on the same purchase) in `clean()`. `Purchase.status` only moves to `REFUNDED` once refunds sum to the full original amount — a partial refund leaves status at `SUCCESS` with a `Refund` record attached (this convention should be confirmed with the business, since "REFUNDED" as a single boolean-ish status doesn't naturally express "partially refunded").
- **Subscription cancellation**: `cancel_at_period_end=True` — access continues through the already-paid period, then `Subscription.status` moves to `CANCELLED` at `current_period_end` via a Celery Beat check (needs the new Beat schedule, Part A).
- **Subscription renewal failure**: driven by the `subscription.charged`-failure webhook event (or a Beat-scheduled check against Razorpay's subscription status) → `PAST_DUE`. **Business decision**: grace period length before access is actually revoked.
- **Expired subscription**: access-checking logic (wherever course access is currently gated by `Enrollment` alone) needs a new "OR has an ACTIVE Subscription covering this course" check — this is a real, non-trivial code change touching existing access-control call sites, not just new models; scope this explicitly in Phase 3.4, don't underestimate it.
- **Invoice duplication**: `invoice_number` unique constraint + generation only from the single `fulfill_purchase`/`fulfill_order` hook (not from any user-facing endpoint) prevents double-issuance for the same transaction.
- **Payout duplication**: `LedgerEntry.payout` FK being set (non-null) is what excludes an entry from the next batch-generation query — an entry can only ever belong to one `Payout`.
- **Double enrollment**: already safe — `Enrollment.get_or_create` (unchanged, reused identically by `fulfill_order`).
- **Transaction rollback**: every fulfillment path wrapped in `transaction.atomic()` (Part C) — a mid-way failure (e.g. PDF generation error) must not leave a `SUCCESS` purchase with no `Enrollment`. Note PDF generation should be a **separate** async Celery task (not inside the atomic block) specifically so a slow/failing PDF render never blocks or rolls back the actual payment/enrollment fulfillment.
- **Unauthorized teacher/mentor access**: `my-earnings`/`my-payouts` endpoints must filter strictly to `recipient=request.user` — never expose another instructor's `LedgerEntry`/`Payout` rows. No object-level permission classes exist yet anywhere in `users/permissions.py` (confirmed) — these new endpoints need `get_queryset()`-level scoping (the same pattern already used correctly throughout `courses/views.py`'s Phase 2 work), not a new permission class alone.
- **Admin-only finance operations**: every admin-prefixed endpoint above uses the existing `IsSuperAdminOrAdmin` class — confirmed this already correctly gates on `is_superuser or is_staff`, matching every other admin surface in the app.

---

## PART K — Migrations

All additive. Grouped by app, in dependency order:

**`orders` app** (new migration file(s) after `0001_initial`):
1. Add `choices=` to `Purchase.status` (`AlterField`, no data change).
2. Create `Order`, `OrderItem`.
3. Create `SubscriptionPlan`, `Subscription`.
4. Create `Invoice`.
5. Create `Refund`.
6. Create `LedgerEntry`, `Payout` (in that order — `LedgerEntry.payout` FK depends on `Payout` existing, or create both in one migration).
7. Create `WebhookEvent`.

(Steps 2-7 can be one migration or several — recommend several small ones per the codebase's own established convention of one-concept-per-migration, matching `0013_courseinstructor` / `0014_backfill_course_instructors` being separate.)

**`courses` app**:
8. Add `Bundle` model.
9. Add `CourseInstructor.commission_rate` field.

**`users` app**:
10. Add `TeacherProfile.payout_method`/`payout_details`.
11. Add `MentorProfile.payout_method`/`payout_details`.

**`notifications` app**:
12. `AlterField` on `Notification.notification_type` to add `REFUND`/`PAYOUT` choices (no data change).

**No existing migration is modified.** No `RunPython` data migration is strictly required for Phase 3 (unlike Phase 0's `CourseInstructor` backfill) — `LedgerEntry` rows are generated going forward from new purchases only; retroactively backfilling ledger entries for historical `Purchase` rows is optional and, if wanted, should be its own explicitly-approved data migration written after the models exist, not bundled into initial schema creation.

---

## PART L — Test Plan

**Must continue passing (regression gate)**: the full existing `orders` suite (`PaymentNotificationTests` — 5 tests), the `notifications` suite (`NotificationIntegrationTests` especially — verifies the exact PENDING→SUCCESS transition behavior `fulfill_purchase` relies on), and anything in `courses`/`users` touching `LiveBatchStudent.purchase` or `AdminUserViewSet.assign_course`/`mark_purchase_paid` (Phase 1/2 tests already cover these indirectly).

**New unit tests**:
- `Purchase.status` choices validation (existing invalid-string saves should now be rejected at `full_clean()`, though raw `.save()` without `full_clean()` still bypasses it — note this limitation).
- `LedgerEntry`/`Payout` amount-sum consistency (`Payout.net_amount` == sum of its `entries.net_amount`).
- `Invoice`/`Refund` exactly-one-FK-set constraint.

**Serializer tests**: `OrderSerializer`, `SubscriptionSerializer`, `RefundSerializer`, `PayoutSerializer`, `LedgerEntrySerializer` — field exposure per role (student sees own only, admin sees all, teacher/mentor sees own earnings only).

**API tests**: every endpoint in Part G — status codes, permission boundaries (student can't hit admin endpoints, teacher A can't see teacher B's earnings — mirrors the exact `test_teacher_cannot_...another_teacher's...` pattern already established in Phase 2's test suite).

**Permission tests**: explicit "Teacher/Mentor can only manage resources they own" tests for `my-earnings`/`my-payouts`, matching Phase 2's real-JWT-cookie verification class pattern for at least the 2-3 most security-critical boundaries (cross-instructor earnings leakage, non-admin payout approval attempt).

**Payment verification tests**: extend existing `VerifyPaymentView` tests to cover the new `select_for_update()` locking (simulate concurrent verification calls) and duplicate-purchase-prevention logic.

**Webhook tests**: signature verification (valid/invalid/missing), duplicate event id → no reprocessing → still 200, unknown event type → logged and ignored gracefully (not a 500), malformed payload → 400 not 500.

**Idempotency tests**: calling the same webhook event twice produces exactly one `LedgerEntry`/`Invoice`/notification; calling `fulfill_order` twice for an already-fulfilled order is a no-op (mirrors the existing `test_admin_mark_paid_repeated_calls_do_not_duplicate_notifications` pattern exactly).

**Refund tests**: full REQUESTED→PROCESSING→SUCCESS flow via mocked Razorpay + webhook; partial refund amount validation; refund-after-payout creates a CLAWBACK not a Payout mutation.

**Subscription tests**: creation, renewal-success extends period, renewal-failure moves to PAST_DUE, cancellation respects `cancel_at_period_end`, access-check logic (`Enrollment` OR active `Subscription`) — mocked Razorpay subscription webhooks throughout (no real recurring billing in tests).

**Bundle tests**: `Order`+`OrderItem` creation grants `Enrollment` for every course in the bundle, price-tampering rejection (client can't submit a custom `OrderItem` price), partial-failure rollback (one course in the bundle can't be enrolled → whole order fulfillment rolls back).

**Invoice tests**: numbering uniqueness under concurrent creation, PDF generation task failure doesn't roll back the payment.

**Payout tests**: batch-generation only includes unattached `LedgerEntry` rows, running generation twice for the same period doesn't double-batch already-batched entries, approval/completion state transitions, unauthorized (non-admin) approval attempt rejected.

**Finance calculation tests**: commission-rate snapshot doesn't change retroactively when the global default rate is later edited; `gross - commission = net` arithmetic; multi-instructor course split (once that business rule is confirmed).

**Concurrency tests**: two simultaneous `verify-payment` calls for the same order only fulfill once (`select_for_update` + status-check); two simultaneous webhook deliveries for the same event id only process once.

**Regression tests**: every existing Phase 2 live-class test that touches `LiveBatchStudent.purchase` (batch assignment with a purchase) — confirm untouched behavior; every existing Phase 0/1 permission test.

---

## PART M — Implementation Order

Refined from your suggested ordering based on what the audit found (webhook infrastructure needs to exist before reconciliation/refund can be verified end-to-end; ledger must exist before payouts can consume it; the Celery Beat *infrastructure* addition is a prerequisite for subscriptions specifically, not the whole phase):

- **3.1 — Payment hardening** (no new models): `Purchase.status` choices, duplicate-purchase-on-create prevention, `select_for_update()` on verify-payment, remove-the-debug-print-class cleanup already done in the production-hardening pass. *Files: `orders/models.py`, `orders/views.py`, one migration.*
- **3.2 — Razorpay webhook + `WebhookEvent` + idempotency**: the new endpoint, signature verification, event dedup, wiring `payment.captured` to mirror `verify-payment`'s effect. *Files: `orders/models.py`, `orders/views.py`, `orders/urls.py`, `core/settings.py` (new `RAZORPAY_WEBHOOK_SECRET`), one migration.* — Built early because reconciliation, refunds, and subscriptions all depend on webhooks being trustworthy.
- **3.3 — Order/Bundle architecture**: `Order`, `OrderItem`, `Bundle`, `fulfill_order()`, bundle checkout endpoints + admin bundle CRUD. *Files: `orders/models.py`, `orders/services.py`, `orders/views.py`, `orders/serializers.py`, `courses/models.py` (Bundle), migrations.*
- **3.4 — Subscriptions** (its own sub-phase given the distinct Razorpay API surface flagged in Part C): `SubscriptionPlan`, `Subscription`, Celery Beat infrastructure addition, renewal/expiry webhook handling, the `Enrollment`-or-`Subscription` access-check change. *Files: `orders/models.py`, `orders/tasks.py` (new), `core/celery.py`/`core/settings.py` (Beat schedule), wherever course-access is currently gated by `Enrollment` alone.*
- **3.5 — Finance ledger + commission**: `LedgerEntry`, `CourseInstructor.commission_rate`, `FinanceSettings` default, ledger-entry creation hooked into `fulfill_purchase`/`fulfill_order`. *Files: `orders/models.py`, `orders/services.py`, `courses/models.py`, migrations.*
- **3.6 — Invoices**: `Invoice` model, PDF generation Celery task, numbering scheme (pending business confirmation on the scheme itself). *Files: `orders/models.py`, `orders/tasks.py`, `orders/services.py`.*
- **3.7 — Refunds**: `Refund` model, admin refund action, webhook-driven status completion, refund-after-payout clawback handling. *Files: `orders/models.py`, `orders/views.py`, `orders/services.py`, `notifications/services.py` (`trigger_refund`).*
- **3.8 — Teacher/Mentor payouts**: `Payout`, `payout_method`/`payout_details` fields (pending the security decision on how bank details are stored), batch-generation endpoint, approval/completion flow, `notifications` `PAYOUT` type + `trigger_payout`. *Files: `orders/models.py`, `orders/views.py`, `orders/serializers.py`, `users/models.py`, `notifications/models.py`, `notifications/services.py`, migrations.*
- **3.9 — Admin finance UI**: Finance Dashboard, Refunds/Invoices/Payouts admin pages, reconciliation view. *Files: new `frontend/src/app/admin/finance/*` pages, extends `admin/layout.tsx` nav.*
- **3.10 — Student/Teacher/Mentor UI**: student payment-history/invoices/subscription pages, teacher/mentor earnings+payout-history pages. *Files: new `frontend/src/app/payments/*` (or under `/dashboard`), new teacher/mentor nav items in `admin/layout.tsx`.*
- **3.11 — Reconciliation tooling**: the stuck-PENDING report, live Razorpay-vs-DB cross-check admin action.
- **3.12 — Final testing**: the full Part L test plan executed end-to-end, plus a manual production verification pass (mirroring the Phase 2 manual-AWS-test approach) — a real test bundle purchase, a real refund, a real payout batch, in a staging/sandboxed Razorpay environment before this touches live payment data.

You may reorder 3.4 (Subscriptions) later if it's lower business priority than Refunds/Payouts — it's sequenced early only because of the Celery Beat infrastructure dependency other later sub-phases don't share; if subscriptions aren't an immediate priority, 3.4 can safely move to the end without blocking 3.5-3.11.

---

## Summary (as requested)

**1. Current payment architecture summary**: `Purchase` is the entire payment domain today — one row per single-course Razorpay purchase, `status` unconstrained by `choices=`, no webhook, no refund, no payout, no ledger. Fulfillment (enrollment + notification) is already correctly centralized in `orders/services.py::fulfill_purchase()`, called from exactly 4 places. Frontend checkout flow is correct and secure (server-derived amounts, no hardcoded keys). Mobile has zero payment code by design.

**2. Existing vs missing features**: see Part B table — everything in the 19-item Phase 3 scope is either fully missing (webhooks, subscriptions, bundles, invoices, refunds, payouts, ledger) or only exists in admin-facing/read-only form (payment history, finance dashboard basics).

**3. Final proposed architecture**: `Purchase` stays untouched for single-course buys; a new parallel `Order`/`OrderItem` pair handles bundles/subscription-initiation; `LedgerEntry` + one unified `Payout` model drive teacher/mentor earnings, keyed off the existing (currently-unused) `CourseInstructor.is_primary`; a new `WebhookEvent`-backed webhook endpoint becomes the reconciliation source of truth. Full detail in Part C.

**4. Database schema**: 10 new models (`Order`, `OrderItem`, `Bundle`, `SubscriptionPlan`, `Subscription`, `Invoice`, `Refund`, `LedgerEntry`, `Payout`, `WebhookEvent`) + 5 additive fields on existing models. Full field-level detail in Part E.

**5. API specification**: ~30 new endpoints across student/admin/teacher-mentor/Razorpay-facing surfaces, all reusing existing permission classes and pagination/serialization conventions. Full list in Part G.

**6. Permission model**: reuses `IsSuperAdminOrAdmin` for all admin finance operations (already correctly scoped); new teacher/mentor "own data only" endpoints need `get_queryset()`-level scoping since no object-level permission classes exist in this codebase yet — matches the pattern already proven correct in Phase 2.

**7. State machines**: Part F, six state machines defined, all backward-compatible extensions of `Purchase`'s existing two-transition shape.

**8. Frontend plan**: Part H — new Admin Finance section, new Teacher/Mentor earnings pages, new Student payment-history/invoices/subscription pages, all explicitly reusing existing Natya UI patterns (ledger table, tabbed detail page, stat cards).

**9. Mobile plan**: no payment functionality added; existing web-checkout-handoff strategy preserved exactly; an optional read-only purchase-history view is possible later using mobile's existing JWT-bearer auth, not required for Phase 3.

**10. Security risks**: full list in Part J — most are net-new hardening (row locking, dedup, signature verification) since almost none of this attack surface exists yet; the two carried-over risks from the earlier production audit (no row locking, no dedup-on-create) get fixed as part of 3.1.

**11. Migration plan**: Part K — 12 additive migration steps across `orders`/`courses`/`users`/`notifications`, zero destructive changes, zero existing migrations touched.

**12. Test plan**: Part L — unit/serializer/API/permission/webhook/idempotency/refund/subscription/bundle/invoice/payout/finance/concurrency/regression coverage specified; explicit regression gate on the existing `orders`/`notifications` suites plus Phase 1/2 tests touching `Purchase`.

**13. Exact implementation phases**: 3.1 through 3.12, detailed in Part M, with the dependency reasoning for the ordering (webhooks before reconciliation/refunds, ledger before payouts, Beat infrastructure isolated to the subscriptions sub-phase).

**14. Files changed per sub-phase**: listed inline under each sub-phase in Part M.

**15. Risks / possible breaking changes**: (a) `Purchase.status` gaining `choices=` is technically an `AlterField` migration — zero data risk, but confirm no external system/report queries `Purchase.status` expecting to write non-enum values. (b) The access-check change needed for subscriptions (Part J, "Expired subscription") touches existing `Enrollment`-gated logic across the app — this is the single largest real code-risk item in the whole plan and deserves its own careful review pass when 3.4 is scoped, not treated as a trivial addition. (c) Storing bank/payout details raises a real data-security question that needs a decision before the relevant migration ships (Part D). (d) The Celery Beat addition is new production infrastructure (a scheduler process), not just new code — needs deployment coordination, not just a code merge.

**16. Items requiring your business decision before coding** (collected from throughout this document):
- Exact platform commission percentage (and whether it's ever a flat fee instead)
- Whether commission is calculated pre-tax or post-tax, and whether GST/other tax applies to instructor payouts at all
- Whether Natya needs to issue GST-compliant invoices at all, and if so the numbering/format rules (needs CA/legal confirmation, not engineering judgment)
- Whether a second instructor (ASSISTANT/co-MENTOR) on a course ever earns a revenue split, and by what percentage
- How bank/UPI payout details are collected and protected — plaintext field, encrypted field, or delegate to a third-party payout processor (e.g. Razorpay Route) instead of storing them ourselves
- Whether payouts are ever automatic (scheduled) or always manually admin-triggered, and what the minimum payout threshold is (if any)
- Whether students can self-request refunds or refunds are always admin-initiated
- Whether a refund clawback against an already-paid-out instructor is always deducted from their next payout or sometimes forgiven
- Subscription plan shape: does a plan grant access to specific courses or the whole catalog by default
- Subscription past-due grace period length before access is actually revoked
- Whether historical `Purchase` rows should ever be retroactively backfilled into `LedgerEntry` (optional, not required for Phase 3 to function going forward)

---

**No code was written. No migrations were created. No files outside this document were modified. Waiting for approval before starting 3.1.**
