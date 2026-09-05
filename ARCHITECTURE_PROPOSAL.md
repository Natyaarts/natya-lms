# Natya LMS — Architecture Audit & Expansion Proposal

> Read-only audit. No code was changed to produce this document. Prepared 2026-09-02.

This document audits the current Natya LMS codebase (Django backend + Next.js frontend + Expo mobile app) and proposes an architecture for expanding it into a full platform covering: student management, teacher/mentor modules, live class scheduling (recurring, calendar, availability, attendance, recordings), chat, payments (one-time + subscriptions + invoices + refunds), teacher/mentor payouts, finance/reports/analytics, notifications, certificates, quizzes, assignments, reviews, coupons, and learning paths/prerequisites.

**Governing principle throughout: extend, don't duplicate.** Several of the 30 requested features already have a real (if partial) foundation in this codebase — most notably live classes. The plan below is built around reusing that foundation rather than re-inventing it.

---

## 1. Current Architecture

### 1.1 Backend apps

| App | Owns |
|---|---|
| `users` | `User` (custom, extends `AbstractUser`), `OnboardingField`, `OTPVerification`, all role/permission primitives |
| `courses` | `Course`, `Module`, `VideoLesson`, `Enrollment`, `TranslatedAudio`, `LessonProgress`, `LiveClass`, `LiveBatch`, `LiveBatchStudent` |
| `orders` | `Purchase` (the only payment model) |
| `notifications` | `Notification`, `Announcement`, `NotificationService`, `LiveClassNotificationService`, signals |
| `cms` | `HeroSection`, `Feature` (landing-page content only) |
| `core` | settings, URL root, Celery bootstrap — no models |

### 1.2 Roles today — important, this shapes everything

There is **no RBAC table and no Mentor role**. Role is entirely three booleans on `User`: `is_superuser`, `is_teacher`, `is_student` (`is_staff` also exists via `AbstractUser`). These aren't mutually exclusive and nothing stops a user being both. Reusable permission classes (`users/permissions.py`): `IsSuperAdmin`, `IsTeacher`, `IsStudent`, `IsSuperAdminOrTeacher`, `IsSuperAdminOrTeacherOrReadOnly`, plus two live-class-specific ones defined inline in `courses/views.py` (`IsSuperAdminOrAuthorizedTeacherOrReadOnly`, `IsSuperAdminOrStaffOrReadOnlyBatches`).

**"Teacher owns this course" is not a real relationship** — it's inferred by a teacher being *enrolled* in their own course (`AdminUserViewSet.teacher_students` queries `Enrollment` for the teacher, then finds other students in the same courses). This hack is the single biggest thing new Teacher/Mentor work needs to replace, not build on top of.

Admin frontend (`admin/layout.tsx`) treats `is_superuser` and `is_teacher` **identically** — same nav, same full access. There's no scoped-down teacher experience yet.

### 1.3 Live classes — already substantial, reuse heavily

`courses.LiveClass` + `LiveBatch` + `LiveBatchStudent` already provide:
- One-to-one and group batches (`LiveBatch.batch_type`)
- Scheduling with **conflict detection** (`LiveClassSerializer.validate()` checks batch/instructor double-booking in a 24h window) and past-date rejection
- A status machine (SCHEDULED → LIVE → COMPLETED / CANCELLED) with dedicated `start/end/cancel/reschedule` actions
- `meeting_url` visibility gated by role (`to_representation` hides it from students not assigned to the batch, teachers not the instructor)
- Celery-scheduled reminder notifications (`send_class_reminder`, `apply_async(eta=...)`), wired through `LiveClassNotificationService` (`notify_scheduled`/`notify_rescheduled`/`notify_cancelled`) — genuinely used from `LiveClassViewSet.perform_create`/`reschedule`/`cancel`, not dead code
- Admin-only student↔batch assignment (`LiveBatchService.assign_student`, validates purchase/capacity, row-locked)

**What's missing**: recurrence, a teacher/mentor availability model, a calendar aggregation endpoint, attendance, and recordings. None of these need a new "event" model — they extend what's there.

### 1.4 Payments — minimal, single-purpose

`orders.Purchase` is the *entire* payments domain: one row per single-course Razorpay purchase (`user`, `course`, `amount`, `status` PENDING/SUCCESS/FAILED, Razorpay order/payment/signature). No cart, no bundles, no subscriptions, no invoices, no refunds, no payouts, no coupons.

Two real problems to fix, not just extend around:
- `CreateOrderView` has `permission_classes=[AllowAny]`, explicitly flagged in-code as "temporarily for local testing." An anonymous request even falls back to attaching the purchase to `User.objects.first()`.
- **Enrollment-on-payment is duplicated in three places** with inconsistent behavior: `VerifyPaymentView` (auto-enrolls), `AdminPurchaseViewSet.mark_paid` (auto-enrolls), `AdminUserViewSet.mark_purchase_paid` (does **not** auto-enroll — needs a separate `enroll_course` call). Any new payment feature (subscriptions, bundles) will multiply this inconsistency if built on top as-is.

### 1.5 Notifications — well-built, the pattern to reuse everywhere

`NotificationService.create_notification()` uses an `idempotency_key` unique constraint + `transaction.on_commit()` to safely dedupe concurrent/duplicate notification creation. `courses.Enrollment`'s `post_save` signal (registered in `NotificationsConfig.ready()`) is the template for "fire a notification automatically whenever X happens elsewhere in the app," reusable for every new module below. The `NotificationType` enum **already reserves** `CERTIFICATE`, `COURSE_COMPLETION`, and `LIVE_CLASS` — the system was designed anticipating these features even though no certificate model exists yet.

### 1.6 Frontend

Next.js App Router, almost entirely client components (two exceptions: `/courses` and `/courses/[id]` are server components), **zero global state management** — every page independently `useState`/`useEffect`-fetches its own data (no Context/Redux/React Query), and the CSRF-cookie-reading snippet is duplicated verbatim in 6+ files. Admin sidebar nav is a flat 5-item array (`Overview, Users, Courses, Onboarding, Payments`) in `admin/layout.tsx` — trivial to extend. Two features are **already stubbed in the UI** anticipating this exact expansion: the user-detail page has a "Sessions" tab (placeholder for live scheduling) and a "Communication" tab (hardcoded mock chat/WhatsApp log). The dashboard's course-progress bar is hardcoded to a fake 5% — real data (`LessonProgress`) exists on the backend but isn't wired to this UI yet.

### 1.7 Mobile

Expo/React Native, **student-only** — no teacher, mentor, or admin surface exists at all. Any new module intended for teachers/mentors on mobile is net-new mobile work, not an extension.

### 1.8 What doesn't exist at all today

Mentor role, chat/messaging, subscriptions, bundles, invoices, refunds, payouts, coupons, quizzes, assignments, certificates, reviews, learning paths/prerequisites, attendance, class recordings, recurring live classes, teacher/mentor availability, calendar view, real course-completion tracking on the frontend, any RBAC beyond three booleans.

---

## 2. Proposed Architecture (by feature area)

For each of the 30 requested capabilities: what to **reuse**, what to **extend**, and what's genuinely **new**.

### A. People & Roles — (1) Student management, (2) Teacher module, (3) Mentor module

| | |
|---|---|
| **Reuse** | `User` model, `Enrollment`, `AdminUserViewSet` CRUD pattern, `admin/users` list+detail pages, existing permission classes |
| **Extend** | Add `is_mentor` boolean to `User` (same additive pattern as the existing `is_teacher`/`is_student` — cheap, non-breaking, matches this codebase's own migration history). Add server-side pagination to `admin-users/` (currently client-side over the full list — a flagged scalability gap). |
| **New models** | `TeacherProfile` (user 1:1, bio, specialization, hourly_rate, payout_method, is_approved) and `MentorProfile` (user 1:1, bio, specialties, max_students, hourly_rate, payout_method) — role-specific data lives here, not bloating `User`. **`CourseInstructor`** (course FK, user FK, role: TEACHER/ASSISTANT) — replaces the "teacher enrolled in their own course" hack; this becomes the real ownership relationship `LiveBatch.instructor`, permission checks, and `teacher_students` should all be rebuilt on. |
| **Do NOT duplicate** | `User` itself — do not create separate `Student`/`Teacher`/`Mentor` user tables. Keep one auth identity, layer profiles on top. |
| **Permissions** | Add `IsMentor`, `IsSuperAdminOrTeacherOrMentor`, and an object-level `IsCourseInstructor` (checks `CourseInstructor`, not `Enrollment`) to `users/permissions.py`, following the file's existing style exactly. |
| **Admin pages** | Extend `admin/users` with a Mentors tab (alongside existing Students/Teachers tabs); extend `admin/users/[id]` with instructor-assignment UI (assign to `CourseInstructor` instead of implicit self-enrollment). |
| **Teacher pages** | New teacher-scoped dashboard (currently teachers see the *same* admin view as superusers — needs its own scoped `/teacher` area or role-branching inside the existing admin shell). |
| **Mentor pages** | New `/mentor` area: assigned students, availability, upcoming sessions, payout summary. |
| **Student pages** | Existing `/dashboard`, `/courses/[id]/learn` — extend with real progress data (already backed by `LessonProgress`) and, later, certificates/reviews entry points. |

### B. Live Classes & Scheduling — (4) Live classes, (5) Scheduling, (6) Recurring, (7) Calendar, (8) Availability

| | |
|---|---|
| **Reuse (heavily)** | `LiveClass`, `LiveBatch`, `LiveBatchStudent`, the existing conflict-detection validator, status machine, reminder task, `LiveClassNotificationService`. This is the strongest existing foundation of any requested feature — **do not build a parallel event/session model.** |
| **New models** | `RecurringSchedule` (batch/course FK, frequency, interval, days_of_week, start_date, end_date_or_count) — a Celery-beat task expands this into concrete `LiveClass` rows on a rolling window (e.g. next 4–8 weeks), each tagged `recurrence_rule` FK back to the parent so reschedule/cancel of one occurrence doesn't touch the series. `TeacherAvailability` (user FK, day_of_week or date, start_time, end_time, is_recurring) — used both to power a calendar and to extend the *existing* conflict-detection logic in `LiveClassSerializer.validate()` (add an availability check alongside the double-booking check already there, don't replace it). |
| **New (non-model)** | Calendar is an **aggregation endpoint**, not a new model — extend `LiveClassViewSet` with a `?start=&end=` range query reusing `upcoming`/`history`'s existing patterns, feeding a frontend calendar component. |
| **Admin/Teacher/Mentor pages** | Fill the already-stubbed "Sessions" tab on `admin/users/[id]`; new `/admin/live-classes` (or `/teacher/schedule`, `/mentor/schedule`) calendar view; an availability-setting form for teachers/mentors. |
| **Student pages** | "My Sessions" view on `/dashboard` (currently absent — the dashboard only shows recorded courses today). |

### C. Attendance & Recordings — (9) Attendance, (10) Class recordings

| | |
|---|---|
| **New models** | `Attendance` (live_class FK, student FK, joined_at, left_at, status: present/absent/late) — populated via meeting-provider webhook if available, else manual admin marking. `ClassRecording` (live_class FK, recording_url, duration, uploaded_at, visibility). |
| **Do NOT duplicate/confuse with** | `courses.VideoLesson` (pre-recorded *course* content — structurally unrelated) and `TranslatedAudio` (multilingual dub tracks for `VideoLesson`, unrelated to live sessions). Keep these three concepts (course lesson video, lesson audio dub, live-class recording) as clearly separate models even though they're all "video/audio files" conceptually. |

### D. Communication — (11) Student↔teacher chat, (12) Student↔mentor chat, (13) Admin communication

| | |
|---|---|
| **Reuse** | `NotificationService` to fire a "new message" notification (add a `MESSAGE` `NotificationType`) — don't build a second notification pathway for chat. |
| **New models** | `Conversation` (participants via `ConversationParticipant` through-model with role, optional `course` FK for context, `conversation_type`: STUDENT_TEACHER / STUDENT_MENTOR / ADMIN_SUPPORT), `Message` (conversation FK, sender FK, body, attachment, created_at) with per-participant read state. One schema covers all three chat types (11/12/13) — don't build three separate chat models. |
| **Real-time** | The frontend currently has **no polling/websocket infra anywhere**. MVP chat can reuse the existing "poll on interval" pattern already used by `NotificationBell` (30s cache); true real-time (Django Channels + websockets) is a bigger infra decision — flag as phase 2, not required for a working chat MVP. |
| **Replaces** | The hardcoded mock "Communication" tab on `admin/users/[id]` becomes real data. |

### E. Payments & Finance — (14) One-time, (15) Recurring/subscriptions, (16) Payment history, (17) Invoices, (18) Refunds, (19) Finance, (20) Teacher payouts, (21) Mentor payouts

| | |
|---|---|
| **Fix first (Phase 0, not a feature)** | `CreateOrderView`'s `AllowAny` permission; centralize the 3-way duplicated enrollment-on-payment logic into one `orders/services.py: fulfill_purchase()` function before adding anything on top of it. |
| **Reuse** | `Purchase` for what it already does well (single-course Razorpay buys) — **leave it untouched**, don't rewrite it into a generic Order system. `AdminPurchaseViewSet` + `admin/payments` ledger UI pattern (server-paginated, filter by status/search) is exactly the pattern to replicate for invoices/refunds/payouts admin screens. |
| **New models** | `Subscription` (user, plan, razorpay_subscription_id, status, current_period_end) for (15). `Bundle` (title, courses M2M, price) for course packages, purchased via a new lightweight `Order`/`OrderItem` pair that sits *alongside* `Purchase` rather than replacing it (keeps existing single-course flow 100% unaffected). `Invoice` (references Purchase or Order generically, invoice_number, pdf_url, amount, tax, issued_at) — generated at the same success-hook point `trigger_payment_success` already uses. `Refund` (references Purchase/Order, amount, reason, status, razorpay_refund_id, processed_by). `Payout` (recipient FK — teacher **or** mentor, same model for both since payout mechanics don't differ by role; amount, period, status, method) — do **not** build `TeacherPayout`/`MentorPayout` as separate models. |
| **Finance/Reports (19)** | Extend `AdminStatsView`'s existing aggregation pattern with new endpoints (`finance-summary`, `payouts-summary`) rather than a separate reporting engine. |
| **Do NOT duplicate** | `Purchase` — every new payment model above is additive, referencing it or sitting beside it, never replacing it. |

### F. Reports & Analytics — (22), (23)

Not a new subsystem — extends `AdminStatsView`'s already-established aggregate-endpoint pattern (revenue breakdown, top courses, recent activity feeds) with new endpoints per domain as each new module lands (attendance rates, payout summaries, quiz pass rates, chat volume). Build incrementally alongside each feature, not as one big reporting phase.

### G. Notifications — (24)

Already exists and is well-designed. Purely additive: new `NotificationType` values (`MESSAGE`, `ATTENDANCE`, `PAYOUT`, `REFUND`, `QUIZ_GRADED`, `ASSIGNMENT_DUE`) plus new signal/service call-sites following the exact `Enrollment` post_save / `trigger_payment_success` patterns already in the codebase. `CERTIFICATE` and `COURSE_COMPLETION` are already reserved and waiting for (25).

### H. Certificates — (25)

New model: `Certificate` (user, course, issued_at, certificate_number, pdf_url). Trigger: a signal on `LessonProgress`/`Enrollment` reaching course completion (100% across a course's lessons) generates it and fires the already-reserved `CERTIFICATE` notification type. Depends on real progress tracking being wired end-to-end first (backend already has `LessonProgress`; frontend doesn't consume it yet — fix that as part of this).

### I. Quizzes/Assessments — (26)

New models: `Quiz` (lesson/module/course FK, title, passing_score), `Question` (quiz FK, text, type), `Choice` (question FK, text, is_correct), `QuizAttempt` (quiz, user, score, answers JSON, submitted_at). Attaches to existing `VideoLesson`/`Module`/`Course` via FK — doesn't touch or duplicate course structure.

### J. Assignments — (27)

New models: `Assignment` (course/module FK, title, description, due_date, max_score), `AssignmentSubmission` (assignment, student, file, submitted_at, grade, feedback, graded_by).

### K. Reviews/Ratings — (28)

New model: `Review` (course FK, user FK, rating 1–5, comment, created_at), `unique_together(course, user)`. Business rule: only users with an `Enrollment` (or completed `LessonProgress`) for that course may review — enforced in the serializer, not a new relationship.

### L. Coupons/Promotions — (29)

New model: `Coupon` (code, discount_type, value, valid_from/to, max_uses, used_count, applicable courses M2M or "all"). Applied inside the same order-creation flow centralized in Phase 0 — another reason to centralize that logic before adding coupons, or discount logic will end up duplicated the same way enrollment-creation currently is.

### M. Learning Paths / Prerequisites — (30)

New models: `LearningPath` (title, description) with `PathStep` (path FK, course FK, order) as the through-model; `Course.prerequisites` — a self-referential M2M on the existing `courses.Course`. Enforcement point: the centralized enrollment service from Phase 0 gains a prerequisite check before creating an `Enrollment`.

---

## 3. Database ER-style Relationships (text form)

**Existing (unchanged):**
```
User ─┬─< Enrollment >─ Course ─┬─< Module ─< VideoLesson ─┬─< TranslatedAudio
      ├─< Purchase   >──────────┤                          └─< LessonProgress >─ User
      ├─< Notification          ├─< LiveClass >─ LiveBatch ─< LiveBatchStudent ─┬─ User
      ├─< Announcement          ├─< LiveBatch                                  └─ Purchase
      └─< LessonProgress        └─< Announcement
```

**Proposed additions (all additive FKs onto the above, nothing removed/renamed):**
```
User ─┬─(1:1)─ TeacherProfile
      ├─(1:1)─ MentorProfile
      ├─< CourseInstructor >─ Course            (replaces "teacher self-enrolled" hack)
      ├─< TeacherAvailability
      ├─< Attendance >─ LiveClass
      ├─< ConversationParticipant >─ Conversation ─< Message
      ├─< Subscription
      ├─< Payout                                (recipient: teacher or mentor)
      ├─< Certificate >─ Course
      ├─< Review >─ Course
      └─< QuizAttempt >─ Quiz ─ (Module | Course)

LiveClass ─(1:1 or FK)─ ClassRecording
LiveBatch/Course ─< RecurringSchedule >─< LiveClass (generated occurrences)
Course ─< Bundle (M2M) ─< Order >─< OrderItem
Order/Purchase ─< Invoice
Order/Purchase ─< Refund
Course ─< PathStep >─ LearningPath
Course ─(self M2M)─ Course.prerequisites
Coupon ─(M2M, optional)─ Course
Quiz ─(FK)─ Module | Course
Question ─< Choice
Assignment ─(FK)─ Module | Course ─< AssignmentSubmission ─ User
```

---

## 4. Module Dependency Map

```
users (People/Roles)               ← foundational; every module below depends on it
   │
   ├── courses (existing)          ← depends on users (instructor via new CourseInstructor)
   │      │
   │      ├── live classes (existing) ── extends with: recurrence, availability, calendar, attendance, recordings
   │      ├── quizzes / assignments    ── attach to Module/Course
   │      ├── certificates             ── depends on courses + LessonProgress completion + notifications
   │      ├── reviews                  ── depends on courses + enrollment
   │      └── learning paths           ── depends on courses (composition + prerequisite gating on enrollment)
   │
   ├── orders/payments (existing, needs Phase-0 hardening first)
   │      ├── subscriptions / bundles / invoices / refunds
   │      ├── coupons                  ── applied inside centralized order-creation flow
   │      └── payouts (teacher + mentor) ── depends on users (People/Roles) for recipient identity
   │
   ├── notifications (existing)     ← depended on by nearly everything above as the common "notify" channel
   │
   ├── chat/communication           ── depends on users (People/Roles) for scoping + notifications for alerts
   │
   └── reports/analytics/finance    ── depends on ALL transactional data above existing; build last per-domain
```

---

## 5. Recommended Implementation Order

**Phase 0 — Hardening (no new features, do this first):**
Fix `CreateOrderView`'s `AllowAny` gap; centralize the 3-way duplicated enrollment-on-payment logic into one service function; extract the duplicated CSRF/cookie-reading frontend boilerplate into a shared utility; wire the dashboard's fake progress bar to the real `LessonProgress` data. Skipping this phase means every later feature compounds these existing inconsistencies.

**Phase 1 — People & Roles:**
Mentor role, `TeacherProfile`/`MentorProfile`, real `CourseInstructor` model (replacing the self-enrollment hack), admin role-management UI. Foundational: live-class instructor assignment, payouts, and chat scoping all need real instructor/mentor identity.

**Phase 2 — Live Classes extension:**
Recurrence, availability, calendar, attendance, recordings — built directly on the already-solid `LiveClass`/`LiveBatch` foundation from Phase 1's instructor model.

**Phase 3 — Payments & Finance:**
`Subscription`, `Bundle`/`Order`, `Invoice`, `Refund`, `Payout` (needs Phase 1's teacher/mentor identity), Finance/Reports aggregate endpoints extending `AdminStatsView`'s pattern.

**Phase 4 — Engagement/Content:**
Quizzes, Assignments, Certificates, Reviews, Learning Paths/Prerequisites — mostly additive, lower cross-module risk once courses/enrollment/notifications are stable.

**Phase 5 — Communication:**
Chat (student-teacher, student-mentor, admin) — placed after Phase 1 (needs real relationship scoping) and after deciding on real-time infra (polling MVP vs. Django Channels).

**Phase 6 — Coupons/Promotions:**
Layered onto the Phase 3 order flow once it's stable.

**Phase 7 — Reports/Analytics polish:**
Cross-cutting dashboards consuming everything above, once there's real data to report on.

---

## 6. Risks / Possible Breaking Changes

- **Role model**: adding `is_mentor` as a third boolean is safe and additive. A full RBAC-table rewrite instead would risk breaking every `is_teacher`/`is_superuser` check across 15+ existing call sites — recommend the additive-boolean + profile-model hybrid described above specifically to avoid this.
- **Replacing the "teacher self-enrollment" ownership hack** with `CourseInstructor` is important but touches `teacher_students`, `LiveBatch.instructor` validation, and the live-class permission classes — needs a backfill/migration script (create `CourseInstructor` rows from existing self-enrollments) run alongside the code change, not a hard cutover, or existing teacher dashboards break mid-rollout.
- **Payments**: the plan explicitly keeps `Purchase` untouched and adds new models beside it. If a future phase instead tries to generalize `Purchase` into `Order` wholesale, every existing reference (`AdminUserViewSet` ×2, `orders/views.py` ×2, `AdminPurchaseSerializer`, `LiveBatchStudent.purchase` FK, the notification trigger) breaks — avoid that path.
- **`CreateOrderView` AllowAny fix**: correctly a breaking change for anything currently relying on unauthenticated order creation — low risk since it's self-flagged in the code as unintentional, but should ship as a coordinated fix, not silently.
- **Centralizing enrollment-creation logic**: safe if the three current call sites' *existing* behavior is explicitly preserved during extraction — note that `AdminUserViewSet.assign_course` currently does **not** auto-enroll while `mark_purchase_paid`/`mark_paid` **do**; decide and document the intended unified behavior before merging them, or "fixing" the duplication silently changes admin workflows.
- **Migrations**: every new model proposed is additive (new tables); this codebase's own history (`is_teacher`/`is_onboarded` etc. added incrementally to `User`) shows additive migrations are the established safe pattern here — keep following it rather than altering existing tables.
- **Mobile app**: has zero teacher/mentor/admin surface today. Any of these modules intended for teacher/mentor use on mobile is net-new mobile work, not something "already there to extend" — a scope decision to make explicitly per phase, not an afterthought.
- **No global frontend state management**: as chat, live calendars, and real-time features land, the current "every page independently refetches everything locally" pattern will become a real bottleneck. Recommend introducing a data-fetching/caching layer (e.g. React Query/SWR) as infrastructure work during Phase 2–3, not deferred indefinitely.
- **Celery/Redis load**: live-class reminders already depend on it; recurrence expansion, payout batch processing, and invoice generation will all add to the same worker queue — confirm production Celery/Redis capacity before stacking more scheduled jobs on it (local dev testing earlier in this project found no Redis running locally, which is fine for dev but worth explicitly confirming in production).
