# Phase 3.4 — Subscriptions: Audit + Architecture Report (REVISED)

> Read-only audit/design document. No code, migrations, or configuration was
> changed to produce this revision. Every Razorpay API claim was verified
> against Razorpay's current official documentation and the actual installed
> SDK source during the original audit pass (unchanged in this revision).
> This revision incorporates the business rules you approved and replaces
> `SubscriptionInvoice` with `SubscriptionPayment` per your explicit
> instruction, updating every dependent section accordingly.

---

## 0. APPROVED BUSINESS RULES

Recorded verbatim-in-substance as the fixed baseline for this design — nothing below contradicts these; every design choice in this document either implements one directly or explains how it doesn't apply yet.

1. **Access**: a subscription grants access only to the courses/bundles *explicitly attached to its plan* — never the whole catalog, never future courses added later unless an admin explicitly adds them to the plan. Recorded-course access only. **Live-class access is out of scope for V1** — remains entirely `LiveBatchStudent`-gated, unrelated to subscriptions.
2. **Billing**: Monthly and Yearly only. No free trial. `INR` only. No tax/GST engine.
3. **Student subscriptions**: exactly **one active subscription per student** at a time. Student can view and cancel their own. **Cancellation does not immediately revoke access** — access continues through the already-paid period. No student-facing pause/resume in V1.
4. **Payment failure**: rely on Razorpay's own recurring retry behavior; **the LMS additionally provides a 3-day grace period** after entering the failed/pending state; recovery within the grace period resumes normally; failure to recover past the grace period suspends access. Payment failure, cancellation, expiration, and refund are four distinct, never-conflated concepts.
5. **Legacy purchases are永久 (permanent) and untouchable**: a course individually owned via `Purchase`/`Order` stays accessible forever, regardless of any subscription's state. Subscription access is **additive on top of**, never a **replacement for**, `Enrollment`-based ownership.
6. **Refunds**: not built now (Phase 3.7); the design below must not need to be reshaped when refunds arrive.
7. **Mobile**: no payment code; a future read-only status API is the only mobile-relevant surface, not built now.
8. **Finance**: Phase 3.4 preserves everything Phase 3.5's `LedgerEntry` will need; no ledger/commission/payout code is written now.
9. **Model chain (your explicit instruction)**: `SubscriptionPlan → Subscription → SubscriptionPayment → Invoice (3.6) → LedgerEntry (3.5) → Payout (later)`. **No `SubscriptionInvoice` model** — replaced by `SubscriptionPayment` throughout this document.
10. **Razorpay's bounded-duration limitation is a hard constraint, not something to paper over**: every Razorpay subscription requires `total_count` or `end_at` (max 100 years, Section 4 of the original audit, reconfirmed below). "Until cancelled" is *modeled*, not natively supported — Section 5.3 below specifies exactly how.

---

## 1. Executive Summary (revised)

The approved business rules resolve the single largest open question from the original audit (Business Decision #1/#9/#10 there): because access is **plan-scoped, non-future-inclusive, and never stored as a permanent `Enrollment` row**, the "does ALL_COURSES need new catalog-wide access-check logic" risk from the original audit **no longer exists** — access-checking is a straightforward, uniform "does an eligible grant cover this course, checked live" comparison, the same shape whether the grant is a permanent `Enrollment` or a time-bounded active subscription. This also *eliminates* the need for any new Celery Beat infrastructure: because subscription access is governed by a single timestamp (`Subscription.access_until`, introduced below) rather than by proactively deleting/creating rows, "access ending" is just the passage of time, checked at read time — no scheduled revocation job needed for the common case. The only new scheduled work is a single **one-off `apply_async(eta=...)` task per grace-period episode** (exactly mirroring the existing `send_class_reminder` pattern from Phase 2), not a recurring poll.

**READY FOR IMPLEMENTATION: YES**, with 3 small, non-blocking implementation-detail defaults flagged in Section 10 (proceeding with a stated sensible default for each unless you say otherwise — these are not business-rule-shaped decisions, they're engineering-detail confirmations).

---

## 2. Current Repository Findings

Unchanged from the original audit (re-verified, no drift since): `Purchase`/`Order`/`OrderItem`/`WebhookEvent` all as described previously; `fulfill_purchase()`/`fulfill_order()` both call a shared `_grant_course_access()` helper that does `Enrollment.get_or_create` — **this helper is deliberately NOT reused for subscription access** (Section 6), since subscription access must never become a permanent `Enrollment` row per Business Rule #5. No Celery Beat exists. `NotificationType` has 7 values, none subscription-relevant yet.

---

## 3. Current Razorpay Integration

Unchanged from the original audit — `client.order`/`client.utility.verify_payment_signature`/`client.utility.verify_webhook_signature` in active use; `client.plan`/`client.subscription`/`client.utility.verify_subscription_payment_signature` confirmed present in the installed SDK, unused until now.

---

## 4. Razorpay Subscription Capability Verification

Unchanged from the original audit (all facts below were verified against Razorpay's current official documentation and the installed SDK source during that pass — restated here only where this revision's design depends on them):

- Plan: `period` ∈ `{daily, weekly, monthly, yearly}`, `interval` (cycles between charges), `item.amount` (paise). Response `id` = `plan_...`.
- Subscription: **requires `total_count` XOR `end_at`** — confirmed, no native indefinite option, max duration 100 years. Response `id` = `sub_...`, starts `status="created"`.
- Full status list: `created → authenticated → active ⇄ pending → halted`; `active ⇄ paused/resumed`; `→ cancelled` (terminal); `→ expired` (never authenticated in time); `→ completed` (cycles exhausted).
- Webhook events: `subscription.authenticated/.activated/.charged/.pending/.halted/.cancelled/.completed/.paused/.resumed/.updated`. `subscription.charged` carries both `payload.subscription.entity` and `payload.payment.entity`.
- Initial-checkout signature: `client.utility.verify_subscription_payment_signature({razorpay_subscription_id, razorpay_payment_id, razorpay_signature})` — HMAC over `payment_id|subscription_id`, keyed by `RAZORPAY_KEY_SECRET` (not the webhook secret) — a genuinely different SDK method from order verification, confirmed from source.
- Cancellation: `client.subscription.cancel(id, {"cancel_at_cycle_end": 0|1})`.

**How this revision resolves the bounded-duration constraint (Business Rule #10)**: `total_count` is **not** an admin-entered field at all — it is *computed automatically* from `SubscriptionPlan.period` at the moment a `Subscription` is created, as the largest value that safely fits Razorpay's ~100-year ceiling:
- `period=MONTHLY` → `total_count = 1200` (100 years × 12 cycles)
- `period=YEARLY` → `total_count = 100`

This is an implementation technicality, invisible to admins and students — the product-facing behavior is "billed every month/year until you cancel," which is what actually happens (nobody's subscription will organically reach cycle 1200); Razorpay's own bound is satisfied without the LMS ever pretending it doesn't exist. If a genuinely fixed-term plan is ever wanted (e.g. "12-month program, then it just ends"), that's a distinct product concept from "until cancelled" and is **not** what V1's Monthly/Yearly plans are (per Business Rule #2, which describes billing *cadence*, not a fixed *term*) — noted as a possible future plan type, not built now.

---

## 5. Proposed Architecture (revised)

Same three-tier separation as the original audit (Razorpay state / local LMS state / individual payment state), with the individual-payment tier renamed `SubscriptionPayment` per your instruction, and a fourth concept made explicit in this revision:

**A subscription's access grant is represented by exactly one field, `Subscription.access_until` (a timestamp) plus the plan's `courses`/`bundles` — never by `Enrollment` rows.** This is the key structural decision this revision adds on top of the original audit, made possible (in fact necessary) by Business Rules #3 (cancellation ≠ immediate revocation) and #5 (subscription access must be additive, never touching `Enrollment`). Section 8 explains exactly why.

---

## 6. Database Design (revised)

All models remain additive-only, in the `orders` app (matching `Purchase`/`Order`/`WebhookEvent`), except the `courses`/`bundles` M2M targets on `SubscriptionPlan` (cross-app FK, same established pattern as `OrderItem.course`/`bundle`).

### `SubscriptionPlan`
| Field | Type | Nullable | Default | Why |
|---|---|---|---|---|
| `name` | `CharField(255)` | No | — | e.g. "Bharatanatyam Track — Monthly" |
| `slug` | `SlugField(unique=True)` | blank=True (auto) | — | mirrors `Bundle.slug` exactly |
| `description` | `TextField` | blank=True | `''` | |
| `razorpay_plan_id` | `CharField(255, unique=True)` | Yes | `None` | set once via an admin sync action calling `client.plan.create()` — a catalog object, reused by every subscriber to this plan |
| `period` | `CharField` + `TextChoices` | No | — | **`MONTHLY`/`YEARLY` only** (Business Rule #2 — the `DAILY`/`WEEKLY` values Razorpay itself supports are deliberately not exposed as choices here) |
| `amount` | `DecimalField(10,2)` | No | — | rupees, source of truth for what we charge — Razorpay's `item.amount` (paise) is derived from this, never the reverse |
| `currency` | `CharField(3)` | No | `'INR'` | matches `Order`/`Bundle`'s existing default (Business Rule #2) |
| `courses` | `ManyToManyField(Course, related_name='subscription_plans', blank=True)` | — | — | the explicit, plan-scoped course grant (Business Rule #1) |
| `bundles` | `ManyToManyField(Bundle, related_name='subscription_plans', blank=True)` | — | — | a plan may also grant every course inside a specific `Bundle`, mirroring how `OrderItem` already supports course-or-bundle line items — avoids forcing an admin to re-enumerate a bundle's courses one by one inside a plan too |
| `is_active` | `BooleanField` | No | `True` | mirrors `Bundle.is_active` — deactivating stops new subscriptions, never affects existing subscribers |
| `created_at`/`updated_at` | `DateTimeField` | — | auto | |

**No `total_count`/`access_scope` field** — `total_count` is computed at subscription-creation time (Section 4), not stored on the plan; `access_scope` doesn't exist because Business Rule #1 removed the "all courses" option entirely — `courses`/`bundles` being both empty is simply an invalid/unpurchasable plan (mirrors `Bundle.is_purchasable`'s existing "empty course list ⇒ not purchasable" rule).

### `Subscription`
| Field | Type | Nullable | Default | Why |
|---|---|---|---|---|
| `user` | FK → User | No | — | `CASCADE`, `related_name='subscriptions'` |
| `plan` | FK → SubscriptionPlan | No | — | `PROTECT` (financial history, same reasoning as `OrderItem.course`/`bundle`) |
| `razorpay_subscription_id` | `CharField(255, unique=True)` | Yes | `None` | `sub_...` |
| `status` | `CharField` + `TextChoices` | No | `CREATED` | **mirrors Razorpay's own 9 states exactly** — `CREATED/AUTHENTICATED/ACTIVE/PENDING/HALTED/PAUSED/CANCELLED/EXPIRED/COMPLETED` (unchanged from the original audit — Razorpay's vocabulary is not reinvented) |
| `current_period_start`/`current_period_end` | `DateTimeField` | Yes | `None` | local mirror of Razorpay's `current_start`/`current_end`, refreshed on every successful-cycle webhook |
| `access_until` | `DateTimeField` | Yes | `None` | **the single authoritative "this student's subscription-granted access is valid through this instant" field — see Section 8.** Not a mirror of anything Razorpay sends; computed and owned entirely by our own business logic (grace period, cancellation-without-immediate-revocation). |
| `cancel_at_cycle_end` | `BooleanField` | No | `False` | matches Razorpay's own cancel-call parameter; **V1 always passes `True`** per Business Rule #3 (no immediate-cutoff student-facing option), field kept for the admin-override case (Section 10) |
| `cancelled_at` | `DateTimeField` | Yes | `None` | when cancellation was *requested* |
| `grace_period_started_at`/`grace_period_ends_at` | `DateTimeField` | Yes | `None` | set once, the first time a given failure episode enters `PENDING` (Section 9) — cleared back to `None` on recovery, so a *later, separate* failure episode gets its own fresh 3-day window, not a stale one |
| `paid_count`/`remaining_count` | `PositiveIntegerField` | No | `0` | local mirror of Razorpay's counters |
| `metadata` | `JSONField` | — | `dict` | matches `Order.metadata` |
| `created_at`/`updated_at` | `DateTimeField` | — | auto | |

**Constraints**:
- `CheckConstraint(paid_count__gte=0)`, `CheckConstraint(remaining_count__gte=0)`.
- **`UniqueConstraint(fields=['user'], condition=~Q(status__in=['CANCELLED','EXPIRED','COMPLETED']), name='unique_live_subscription_per_user')`** — enforces Business Rule #3 ("only ONE active subscription at a time") at the DB layer as a race-safety net, mirroring `Mentorship.unique_active_mentorship`'s exact precedent from Phase 1 (a partial unique constraint keyed on "not yet terminal", not a blanket unique-per-user, so subscription *history* is preserved). The primary UX-facing gate remains an application-level check in `SubscriptionViewSet.create()` (clear error message, same as every other "already own it"-style check in this codebase) — the DB constraint exists only to make a race between two concurrent create-attempts impossible, not as the sole enforcement.

### `SubscriptionPayment` (renamed from `SubscriptionInvoice` per your instruction)
| Field | Type | Nullable | Default | Why |
|---|---|---|---|---|
| `subscription` | FK → Subscription | No | — | `CASCADE`, `related_name='payments'` |
| `razorpay_payment_id` | `CharField(255, unique=True)` | Yes | `None` | the actual charge — unique because one real Razorpay payment can only ever back one cycle |
| `amount` | `DecimalField(10,2)` | No | — | snapshot of what was actually charged (normally == `plan.amount` at the time, kept independent for the same "don't recompute from a possibly-since-changed plan" reasoning as `OrderItem.unit_price`) |
| `status` | `CharField` + `TextChoices` | No | — | `SUCCESS`/`FAILED` — mirrors `Purchase.Status`'s minimal vocabulary |
| `cycle_start`/`cycle_end` | `DateTimeField` | Yes | `None` | the billing period this charge covers |
| `charged_at` | `DateTimeField` | Yes | `None` | when Razorpay actually processed it |
| `created_at` | `DateTimeField` | — | auto | when our row was created (webhook receipt time) |

**Explaining the full chain, as requested**: `SubscriptionPlan` is *catalog* data (what can be bought, at what price, for what access) — analogous to `Bundle`. `Subscription` is *the relationship* (this student, this plan, right now) — analogous to `Order`, but long-lived and mutable instead of one-shot. `SubscriptionPayment` is *one raw transaction* — analogous to `Purchase`/`OrderItem`, the actual movement of money for one cycle. **Phase 3.6's `Invoice`** will be the *formal billing document* generated from a `SubscriptionPayment` (or a `Purchase`/`Order`) — a distinct concept (a document with a legal/tax-relevant number and format), not a transaction record itself, which is exactly why your instruction to keep `SubscriptionPayment` separate from a future `Invoice` is architecturally correct, not just a naming preference: an `Invoice` will likely reference a `SubscriptionPayment` via a nullable FK (mirroring how the original Phase 3 plan already designed `Invoice.purchase`/`Invoice.order` as nullable FKs with an exactly-one-set constraint — `Invoice.subscription_payment` slots into that same pattern as a fourth nullable option when Phase 3.6 arrives, needing no redesign of `SubscriptionPayment` itself). **Phase 3.5's `LedgerEntry`** will reference `SubscriptionPayment` the same way it will reference `Purchase`/`OrderItem` — one `LedgerEntry` per revenue-eligible `CourseInstructor` per payment, resolved via `payment.subscription.plan.courses`/`.bundles` → each course's primary `CourseInstructor` (no new field needed on `SubscriptionPayment` for this, exactly mirroring the original Phase 3 plan's `LedgerEntry` design). **Payout** is unaffected by anything in this phase, downstream of `LedgerEntry` only.

---

## 7. State Machines (revised)

### `Subscription.status` — unchanged from the original audit (Razorpay's own machine, Section 4), reproduced for completeness:
```
CREATED → AUTHENTICATED → ACTIVE ⇄ PENDING → HALTED
ACTIVE ⇄ PAUSED/RESUMED        (not reachable in V1 -- no pause/resume UI, Business Rule #3)
ACTIVE/AUTHENTICATED/PENDING/HALTED → CANCELLED   (terminal)
AUTHENTICATED-eligible → EXPIRED                   (terminal, never authenticated in time)
ANY → COMPLETED                                    (terminal, total_count cycles exhausted -- effectively unreachable in V1 given the 1200/100-cycle computed total_count, Section 4, but modeled for correctness)
```

### `Subscription.access_until` — **new state machine this revision introduces**, the actual access-control driver (Section 8):
```
(subscription created)         access_until = None                          (no access yet)
subscription.activated/.charged (success)
                                access_until = current_period_end            (rolls forward every successful cycle)
subscription.pending (first time in a failure episode)
                                grace_period_started_at = now()
                                grace_period_ends_at = now() + 3 days
                                access_until = grace_period_ends_at          (grace period, per Business Rule #4)
subscription.charged (recovery within grace)
                                grace_period_started_at/ends_at = None       (episode cleared)
                                access_until = new current_period_end        (back to normal)
grace period lapses without recovery (scheduled task, Section 9)
                                access_until unchanged (already == the lapsed grace_period_ends_at,
                                which is now in the past -- access has ALREADY stopped by the time
                                anyone reads it; the task's job is notification + bookkeeping, not
                                "turning access off", because there was never an "on/off" flag to begin with)
subscription.cancelled          access_until is NOT changed -- stays frozen at whatever the last
                                successfully-paid period's end already was (Business Rule #3: access
                                continues until the paid-for period ends, then naturally lapses)
subscription.completed          access_until stays at the final period's end, same reasoning
```

### `SubscriptionPayment.status`
```
(created) → SUCCESS   (subscription.charged with a captured payment)
(created) → FAILED    (a charge attempt recorded during a .pending episode, if Razorpay's payload includes one -- see Section 12 risk)
```

---

## 8. Access-Control Model (revised, resolved by the business rules — no longer deferred)

**Access-checking logic (wherever a view currently asks "does this user have access to this course") becomes:**
```
has_access(user, course) =
    Enrollment.objects.filter(user=user, course=course).exists()          # permanent grants: Purchase, Order, admin-assign -- UNCHANGED, still the ONLY thing that ever writes an Enrollment row
    OR (
        user.subscriptions.filter(
            access_until__gte=now(),
            plan__courses=course                                          # OR plan__bundles__courses=course
        ).exists()
    )
```
This is a **live, read-time check**, not a stored grant — the second half of the OR is true only while `access_until` hasn't passed, and automatically becomes false the instant it does, with zero proactive revocation code required. This directly satisfies Business Rule #5 (subscription access is additive, never touches `Enrollment`, never at risk of "helpfully" deleting a legitimately-purchased course's access) and Business Rule #3 (cancellation doesn't immediately revoke — `access_until` simply isn't extended further, so access fades out naturally at the paid-through date rather than being cut off by any explicit action).

**Where this check needs to be wired in** is real, non-trivial work (correctly flagged as a risk in the original audit) — every existing call site that currently gates course access purely by `Enrollment.objects.filter(...).exists()` (course-learn pages, video-lesson streaming permission, progress-tracking endpoints) needs the OR-clause added. This is **not** a new pattern to invent, just a mechanical, careful sweep of existing `Enrollment`-based checks — Section 15/16 (test plan) explicitly covers verifying every such call site continues to correctly gate access for non-subscribers while newly also granting it correctly for active subscribers, without ever accidentally weakening the check for someone with neither.

**Explicit answers to the original audit's open access questions, now settled:**
- **Expiry**: not "immediate" in the sense of an active revocation event — access simply stops matching `access_until__gte=now()` once that timestamp passes. No `Enrollment` row is ever touched or deleted.
- **Grace period**: access continues (Business Rule #4) because `access_until` was extended into the grace window the moment `PENDING` was first observed.
- **Already-completed courses / progress**: `LessonProgress` (keyed on `user`+`lesson`, independent of `Enrollment`/subscription) is never touched by any of this — a student's progress record survives regardless of what happens to their subscription, matching the original audit's reasoning, now simply confirmed rather than conditional.
- **Live classes**: confirmed out of scope (Business Rule #1) — zero changes to `LiveBatchStudent` or any live-class permission logic.
- **Same student, individual purchase AND subscription overlap for the same course**: the `OR` in `has_access()` already handles this correctly and cheaply — if `Enrollment` exists (they bought it outright), access is `True` regardless of subscription state, with no special-casing needed anywhere.

---

## 9. Payment Lifecycle (revised)

Unchanged shape from the original audit's Section 8 (create → Checkout with `subscription_id` → `verify()` → webhooks), with the grace-period mechanics now precisely specified:

```
Student selects a SubscriptionPlan (courses/bundles it grants shown up front)
  → POST /api/orders/subscriptions/  (server resolves plan; rejects if student already has a
     non-terminal Subscription, Business Rule #3; computes total_count per Section 4, never
     client-supplied)
  → local Subscription(status=CREATED) + client.subscription.create(plan_id, total_count, customer_notify=True)
  → frontend opens Checkout with options.subscription_id (mirrors CheckoutButton/BundleCheckoutButton)
  → student authorizes → handler receives {razorpay_payment_id, razorpay_subscription_id, razorpay_signature}
  → POST /api/orders/subscriptions/{id}/verify/ → client.utility.verify_subscription_payment_signature()
     against OUR stored subscription id
  → webhook subscription.authenticated / .activated arrive (idempotent, may race verify() -- both
     paths converge on the same select_for_update()-guarded status/access_until update)
  → status=ACTIVE, current_period_start/end + access_until set → has_access() now returns True for
     every course in plan.courses/plan.bundles
  → [next cycle] Razorpay auto-charges, server-to-server only
  → webhook subscription.charged → new SubscriptionPayment(status=SUCCESS), access_until rolled forward
  → subscription remains ACTIVE indefinitely (in the product sense), bounded internally by the large
     computed total_count (Section 4) which no real subscriber will ever reach
```

**Failure/edge cases, resolved per Business Rule #4:**
- **Initial payment failure**: no `authenticated` webhook ever fires; `Subscription` stays `CREATED`, `access_until` stays `None` — no access was ever granted, nothing to revoke.
- **Recurring payment failure**: `subscription.pending` → `Subscription.status=PENDING`, `grace_period_started_at=now()`, `grace_period_ends_at=now()+3d`, `access_until=grace_period_ends_at` — access **continues** through the grace window (Business Rule #4), and a `SubscriptionPayment(status=FAILED)` row is recorded if the webhook payload carries a payment id for the failed attempt (needs confirming against a real test-mode payload before implementation — flagged in Section 14).
- **Recovery within grace**: a later `subscription.charged` clears `grace_period_started_at/ends_at` and recomputes `access_until` normally — the episode is fully forgotten, a *future* separate failure gets its own fresh 3 days.
- **Grace period lapses without recovery**: the one-off scheduled task (Section 12) fires at `grace_period_ends_at`, finds the subscription still not recovered, and (a) sends the "access suspended" notification, (b) does **not** proactively call `client.subscription.cancel()` (Section 10's flagged default) — Razorpay's own retry/halt timeline continues independently; our `access_until` has already lapsed by construction, so LMS access is already correctly suspended regardless of what Razorpay's status says at that exact moment.
- **Duplicate/delayed/out-of-order webhook, frontend-before-webhook races**: identical mechanisms to every prior phase (`WebhookEvent` dedup, `select_for_update()`+atomic, "whoever gets there first wins" short-circuit) — no new pattern.

---

## 10. Cancellation Lifecycle (new dedicated section, per your request)

```
Student: POST /api/orders/subscriptions/{id}/cancel/  (own subscription only)
  → server calls client.subscription.cancel(razorpay_subscription_id, {"cancel_at_cycle_end": 1})
     -- ALWAYS 1 in V1, per Business Rule #3 (no immediate-cutoff option exposed to students)
  → local: cancelled_at = now(), cancel_at_cycle_end = True
  → access_until is NOT touched -- it already reflects the current paid-through date, so access
     naturally continues exactly through that date and no further, with zero extra logic
  → webhook subscription.cancelled eventually arrives (Razorpay's own timing, once the
     already-paid cycle actually ends) → status=CANCELLED
  → Subscription now permanently in a terminal, non-blocking-for-a-new-subscription state
    (the UniqueConstraint, Section 6, only blocks non-terminal states -- the student CAN
    re-subscribe immediately if they want, even before the old one's access_until passes,
    though product-wise that's an edge case worth being aware of, not one this phase needs
    to specially handle: the OR-based has_access() check is unaffected either way)
```

**Admin-initiated cancellation** (Section 15 permissions): same endpoint, admin-authorized, with the flexibility to pass `cancel_at_cycle_end=False` for an immediate cutoff (e.g. a fraud/abuse case) — **this is the one place V1 should expose the immediate-vs-end-of-period choice**, since Business Rule #3 only constrains the *student-facing* experience, not admin tooling. Flagged as a small, non-blocking default in Section 17.

**Explicitly distinguished, per your instruction**: *Cancellation* = student/admin-initiated, intentional, `cancel_at_cycle_end` governs timing. *Expiration* = the `EXPIRED` status, meaning a subscription that **never successfully authenticated** in the first place — not the same as a cancelled-and-lapsed one, and never had `access_until` set at all. *Payment failure* = `PENDING`/`HALTED`, a *temporary-then-possibly-terminal* state distinct from a deliberate cancellation, governed by the grace period, not by `cancel_at_cycle_end`. *Refund* = not built in this phase (Business Rule #6) — but because `SubscriptionPayment` already records each individual charge with its own `razorpay_payment_id`, a future `Refund` model (Phase 3.7) can reference a specific `SubscriptionPayment` exactly the way the original Phase 3 finance plan already designed `Refund.purchase`/`Refund.order` — no redesign needed, confirming Business Rule #6 is satisfied by construction.

---

## 11. Failed-Payment / Grace-Period Lifecycle (new dedicated section, per your request)

Fully specified in Sections 6/9/12 already; summarized here as its own lifecycle for clarity:

```
ACTIVE
  → (charge attempt fails) → PENDING, grace timer starts (3 days), access continues
  → EITHER: a retry succeeds within 3 days → back to ACTIVE, grace timer cleared, access uninterrupted
  → OR: 3 days pass with no successful charge → scheduled task fires:
       - access already lapsed (access_until reached) -- nothing further to "turn off"
       - "your subscription access has been suspended" notification sent (idempotent, Section 13)
       - Subscription.status is whatever Razorpay itself currently reports (likely still PENDING or
         now HALTED, depending on Razorpay's own retry cadence relative to our 3-day window -- this
         LMS-side grace period is intentionally independent of/possibly-overlapping-with Razorpay's
         own retries, per Business Rule #4's exact wording)
  → EITHER (later): Razorpay's own retries eventually succeed → subscription.charged → back to
    ACTIVE, access_until recomputed normally, notification "welcome back" (optional, Section 13)
  → OR: Razorpay exhausts its own retries → subscription.halted webhook → status=HALTED
    (bookkeeping only -- access was already gated off by access_until, this doesn't change
    anything access-wise, it's the Razorpay-side confirmation of what our own grace-period
    timer had already independently concluded)
```

---

## 12. Celery / Scheduled Jobs (revised — significantly simplified from the original audit)

**No Celery Beat needed anywhere in Phase 3.4.** The original audit flagged Beat as *possibly* required for grace-period handling; the `access_until`-timestamp design (Section 8) removes that need entirely for the access-control side. The **one** new scheduled task:

- **`check_subscription_grace_period(subscription_id, expected_grace_deadline_timestamp)`** — a one-off task scheduled via `apply_async(eta=grace_period_ends_at)` the moment a subscription first enters `PENDING` (mirrors `courses/tasks.py::send_class_reminder`'s exact existing pattern: bind a task, pass an expected-state timestamp so a stale/superseded scheduling is a safe no-op if the subscription recovered before this fires — same "compare expected vs. current, no-op if it's changed" guard `send_class_reminder` already uses for rescheduled live classes). Its only job: if the subscription is *still* not recovered (`grace_period_ends_at` unchanged/still in the past, status still not `ACTIVE`) at fire time, send the "access suspended" notification (idempotent via the standard `idempotency_key` mechanism, Section 13). It does **not** call Razorpay, does **not** change `access_until` (already correctly lapsed by construction), and does **not** force a status change (Razorpay's own webhooks remain the sole source of truth for `Subscription.status`).

No periodic reconciliation job is proposed for V1 either (the "webhook black hole" safety net mentioned as optional/low-priority in the original audit) — can be added later without any redesign if operational experience shows it's needed.

---

## 13. Notifications (revised)

One new `NotificationType.SUBSCRIPTION` value (additive `AlterField`, no data risk — same as every prior phase's notification-type addition), used for: activated, payment failed/entering grace, access suspended (grace expired), recovered/reactivated, cancelled, expired (never activated). Recurring **successful** cycle charges reuse the existing `"PAYMENT"` type (consistent with `trigger_order_payment_success`'s precedent of reusing `PAYMENT` for a genuinely-a-payment event) — recommend **not** notifying on every single successful renewal by default (that would be noisy for a monthly/yearly product), only on the *first* activation and on any *problem* state; a routine on-time renewal charge can optionally stay silent, or notify at low priority — this is a small UX default, not a business-rule-level decision, flagged in Section 17. Every trigger follows the exact `trigger_payment_success`/`trigger_order_payment_success` shape: transition-guarded, `transaction.on_commit`-deferred, try/except-logged, `idempotency_key`-deduped (e.g. `f"subscription:{sub.id}:activated"`, `f"subscription:{sub.id}:grace-suspended:{sub.grace_period_started_at.isoformat()}"` — the grace-episode timestamp included so a *second, later* failure episode's notification isn't deduped against the first one's key).

---

## 14. API Design (revised)

| Method | Path | Auth | Request | Response | Notes |
|---|---|---|---|---|---|
| GET | `/api/orders/subscription-plans/` , `/{id}/` | Public read | — | plan incl. nested `courses`/`bundles` | mirrors `Bundle`'s public-read posture |
| POST/PATCH | `/api/orders/subscription-plans/` , `/{id}/` | Admin only | plan fields incl. `course_ids`/`bundle_ids` | plan | mirrors `BundleSerializer`'s writable-M2M pattern exactly (Phase 3.3 precedent, including the bug that pattern already fixed once — every field writable except `id`/`slug`/timestamps) |
| POST | `/api/orders/subscriptions/` | `IsAuthenticated` | `{plan_id}` | `{id, razorpay: {subscription_id, key_id}}` | rejects if plan inactive/unpurchasable or student already has a non-terminal subscription (Business Rule #3) |
| GET | `/api/orders/subscriptions/` | Own only (admin: all) | — | list | mirrors `OrderViewSet.get_queryset` |
| GET | `/api/orders/subscriptions/{id}/` | Own only | — | detail incl. nested `payments` | |
| POST | `/api/orders/subscriptions/{id}/verify/` | Own only | `{razorpay_payment_id, razorpay_subscription_id, razorpay_signature}` | `{message}` | mirrors `OrderViewSet.verify()` exactly, using `verify_subscription_payment_signature` |
| POST | `/api/orders/subscriptions/{id}/cancel/` | Own, or admin | `{}` (student; always `cancel_at_cycle_end=True`) or `{cancel_at_cycle_end: bool}` (admin) | updated subscription | Section 10 |
| GET | `/api/orders/subscriptions-admin/` | `IsSuperAdminOrAdmin` | filters | list | mirrors `AdminPurchaseSerializer`'s search/filter/pagination |

No `pause`/`resume` endpoints (Business Rule #3). Frontend `verify()` call retained despite the webhook being authoritative — same reasoning as the original audit (immediate UX feedback; webhook remains the closed-tab resilience backstop), unchanged.

---

## 15. Permissions & Security (unchanged from the original audit, restated briefly)

Never trust a client-submitted amount (plan resolved server-side). `RAZORPAY_KEY_SECRET`/`RAZORPAY_WEBHOOK_SECRET` never leave the server. `SubscriptionViewSet.get_queryset()` scopes to `user=request.user` unless admin (404, not 403, for another user's subscription — matches the established convention). Cancellation endpoint: ownership-scoped for students; admin bypass is a separate, explicitly-elevated action (`IsSuperAdminOrAdmin`), never inferred. Teacher/Mentor get zero new access. `WebhookEvent.payload` (reused) is the audit trail for every subscription webhook.

---

## 16. Finance/Ledger Compatibility (revised — see Section 6's chain explanation)

`SubscriptionPayment.amount`/`charged_at`/`subscription.plan.courses`/`.bundles` together give Phase 3.5 everything it needs to attribute per-cycle instructor earnings via `LedgerEntry`, without this phase building any ledger code. No `commission_rate`/`LedgerEntry` FK added now, matching the original audit and Business Rule #8.

---

## 17. Test Plan (revised, comprehensive)

**Unit**: `total_count` computed correctly per period (Section 4); `access_until` computed correctly through every transition in Section 7's second state machine (activation, successful renewal, entering grace, recovering within grace, grace lapsing, cancellation not touching it, a second independent failure episode getting a fresh grace window not reusing a stale one).

**API**: plan CRUD + permissions (mirrors `BundleAdminAPITests` exactly); subscription creation (server-computed amount/`total_count`, rejects a second non-terminal subscription for the same user — both the app-level message and, in a dedicated concurrency test, the DB constraint under a simulated race); subscription list/detail ownership scoping (404 for another user's); cancel (student always `cancel_at_cycle_end=True`; admin can override); invalid plan id; already-subscribed rejection.

**Webhook**: valid `subscription.authenticated`/`.activated`/`.charged`/`.pending`/`.cancelled`/`.completed` each correctly update `status`/`access_until`/create a `SubscriptionPayment` where applicable; signature failure rejected and nothing persisted (mirrors the Phase 3.2 test exactly); missing event id rejected; duplicate webhook is a no-op (mirrors Phase 3.2/3.3's proven pattern); unknown event type safely `IGNORED`; malformed payload safely `400`; **out-of-order webhook** (a `.pending` arriving after a later `.charged` already recovered it — must not regress `access_until` backward); webhook for a subscription id matching nothing is recorded `FAILED`, not silently dropped.

**Access control**: `has_access()` (Section 8) — permanent `Enrollment` grants access with zero subscription involvement (regression-critical: a legacy `Purchase`/`Order` course must remain accessible with **no** active subscription at all); an active subscription grants access to its plan's courses/bundles and **only** those; a course *not* in the plan is correctly denied even with an otherwise-active subscription; access during the grace period is correctly still granted; access after the grace period lapses (and no webhook has necessarily arrived yet) is correctly denied purely by the timestamp check; a cancelled-but-still-within-period subscription still grants access; a cancelled-and-now-past-period subscription does not; a student with both an individual `Purchase` for course X and an active subscription that also covers course X retains access via the `Enrollment` OR-branch even if hypothetically the subscription branch were somehow wrong (defense-in-depth test, asserting the OR is genuinely evaluated, not short-circuited incorrectly).

**Concurrency**: two simultaneous `POST /subscriptions/` for the same user only create one (DB constraint under a simulated race, mirroring the pattern already proven for `Purchase`/`Order` concurrent-verification tests); two simultaneous `verify()` calls for the same subscription only fulfill once; two simultaneous webhook deliveries for the same event id only process once.

**Grace-period task**: scheduled correctly on entering `PENDING`; a superseded/stale scheduled task (subscription recovered before the eta) is a safe no-op (mirrors `send_class_reminder`'s expected-timestamp guard test); the notification fires exactly once per distinct failure episode, not duplicated by a second unrelated task run.

**Regression (mandatory, full suites)**: `orders` (all of Phase 3.1/3.2/3.3's existing tests), `notifications`, `users`, relevant `courses` — **plus a new, explicit assertion that no existing `Enrollment`-based access check anywhere in the codebase was weakened** by the `has_access()` sweep (Section 8) — every call site touched needs its own before/after-equivalent test, not just new subscription-specific tests.

**Frontend** (once built, not scoped in detail here per your "do not build yet" instruction for 3.4's design phase): `SubscriptionCheckoutButton` mirrors `BundleCheckoutButton`'s existing test-by-manual-review precedent (no automated frontend test suite exists in this repo currently for `CheckoutButton`/`BundleCheckoutButton` either, confirmed — TypeScript compilation (`tsc --noEmit`) is this codebase's only current frontend verification gate, and would apply here identically).

**Staging**: a real Razorpay test-mode subscription taken through create → authenticate → activate → at least one real `subscription.charged` webhook (Razorpay's test mode supports accelerated billing cycles for exactly this purpose — needs confirming the exact mechanism against Razorpay's test-mode docs before the staging pass, not assumed here) → a simulated failed charge → grace period → recovery, and separately → grace period → lapse, before this ever touches production.

---

## 18. Implementation Plan (revised)

- **3.4.1 — Database**: `SubscriptionPlan`, `Subscription` (incl. `access_until` + the partial unique constraint), `SubscriptionPayment`, `NotificationType.SUBSCRIPTION`.
- **3.4.2 — Razorpay integration**: admin plan-sync action, `SubscriptionViewSet.create()` (computed `total_count`, server-priced), `verify()`.
- **3.4.3 — Webhooks**: extend `RazorpayWebhookView` with a `SUBSCRIPTION_EVENT_TYPES` branch + `_reconcile_subscription()`, reusing `WebhookEvent` as-is; implement the `access_until` state machine (Section 7) precisely here.
- **3.4.4 — Access control**: the `has_access()` sweep (Section 8) — now well-defined, no longer blocked on a business decision, but still the largest single sub-phase by code-touched-surface-area; do this carefully, one call site at a time, with a before/after test per site.
- **3.4.5 — Celery**: the single one-off grace-period task (Section 12) — no Beat.
- **3.4.6 — APIs**: as Section 14.
- **3.4.7 — Admin UI**: `SubscriptionPlanAdmin` (Django `contrib.admin`, mirrors `BundleAdmin`); a custom Next.js subscriptions list/detail page (mirrors `admin/payments`).
- **3.4.8 — Student UI**: `/subscribe` (mirrors `/bundles`), `SubscriptionCheckoutButton.tsx`, subscription status surfaced (mirrors `/orders`).
- **3.4.9 — Tests**: Section 17 in full, plus the complete existing regression gate.
- **3.4.10 — Staging verification**: Section 17's staging pass, before production.

---

## FINAL SUMMARY (as requested)

### 1. Final proposed models
`SubscriptionPlan`, `Subscription`, `SubscriptionPayment` — 3 new models, 0 changes to any existing model.

### 2. Final field list
See Section 6 in full.

### 3. Final state machines
`Subscription.status` (Razorpay's own 9-state machine, Section 7) + `Subscription.access_until` (the new LMS-owned access-timing machine, Section 7) + `SubscriptionPayment.status` (`SUCCESS`/`FAILED`).

### 4. Final API list
8 endpoints, Section 14 — no `pause`/`resume` (Business Rule #3), admin cancel supports immediate cutoff (Section 10), student cancel does not.

### 5. Final webhook events to handle
`subscription.authenticated`, `.activated`, `.charged`, `.pending`, `.halted`, `.cancelled`, `.completed` — 7 events, extending `RazorpayWebhookView`. `.paused`/`.resumed`/`.updated` explicitly not handled in V1 (no pause/resume, no plan-change flow).

### 6. Final Celery tasks, if any
**One**: `check_subscription_grace_period`, a one-off `apply_async(eta=...)` task per grace-period episode, mirroring `send_class_reminder`'s existing pattern exactly. **No Celery Beat.**

### 7. Final migration plan
1. `orders`: create `SubscriptionPlan` (+ 2 M2M through-tables: `courses`, `bundles`).
2. `orders`: create `Subscription` (incl. the partial `UniqueConstraint` and 2 `CheckConstraint`s).
3. `orders`: create `SubscriptionPayment`.
4. `notifications`: `AlterField` adding `SUBSCRIPTION` to `NotificationType.choices` (no data change).

All additive. Zero changes to any existing migration, `Purchase`, `Order`, `OrderItem`, `Bundle`, or `WebhookEvent`.

### 8. Final test plan
Section 17 in full — unit, API, webhook (incl. out-of-order), access-control (the largest category, correctly so, since it's the largest behavioral change), concurrency, grace-period-task, full regression gate, staging.

### 9. Remaining risks
- **Exact payload shape of a failed recurring charge within `subscription.pending`** (does it reliably include a `payment.entity` worth recording as a `FAILED` `SubscriptionPayment`, or is the failure sometimes payload-less) — needs confirming against a real Razorpay test-mode webhook before `_reconcile_subscription()` is finalized, not assumed in this document.
- **Razorpay test-mode's accelerated-billing mechanism for staging verification** — needs confirming before Section 17's staging pass can actually be executed; not blocking design/implementation, only blocking the final staging sign-off.
- **The `has_access()` sweep (Section 8/18.4)** touches existing, currently-`Enrollment`-only code paths — the single highest-code-risk item in this phase, exactly as flagged in the original audit, now scoped precisely rather than left open-ended.
- **Razorpay's own retry/halt cadence vs. our independent 3-day grace timer** may not line up exactly (Business Rule #4 explicitly allows this — "use Razorpay's own retry behavior" **and** "additionally" a 3-day LMS grace period, not necessarily synchronized) — worth confirming this dual-timer behavior is genuinely the intended product experience (a student could see "access suspended" from our side while Razorpay is still independently retrying in the background, or vice versa) rather than an accidental mismatch; flagged for awareness, not a blocker.

### 10. Remaining decisions requiring approval
None are business-rule-shaped or blocking — three small engineering-detail defaults, stated here for transparency, proceeding with each unless you say otherwise:
1. **Admin-cancel supports immediate cutoff (`cancel_at_cycle_end=False`); student-cancel never does** (Section 10) — a reasonable, minimal extension of Business Rule #3, not a new business rule, but flagging since it's a capability beyond what was explicitly specified.
2. **Grace-period lapse does not auto-call `client.subscription.cancel()`** — access is already suspended via `access_until`; the Razorpay-side subscription is left to its own retry/halt fate rather than the LMS forcing a cancellation. Reversible/toggleable later with no model change if you'd rather auto-cancel.
3. **Successful *routine* renewal charges do not trigger a notification by default** (only first activation + any problem state) — a UX-noise-reduction default, not a business rule; trivial to flip to "notify every renewal" later.

---

## READY FOR IMPLEMENTATION: YES

Design is fully resolved against your approved business rules, every Razorpay capability claim is verified against current documentation and the actual installed SDK, and the three remaining items above are non-blocking defaults, not open decisions. Waiting for your explicit go-ahead before writing any code, migrations, or configuration.
