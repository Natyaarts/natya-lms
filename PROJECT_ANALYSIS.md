# Natya LMS — Full Technical Documentation

> Generated from a full-codebase analysis (backend, frontend, mobile) on 2026-09-02.

## 0. Architecture Overview

A monorepo with three independently deployed apps sharing one Django REST API backend:

| Layer | Stack | Deployment target |
|---|---|---|
| **Backend** | Django 5 + DRF, PostgreSQL (RDS), Celery + Redis, AWS S3 | AWS Elastic Beanstalk |
| **Frontend (web)** | Next.js 16 (App Router) + React 19 + TypeScript + Tailwind v4 | Vercel |
| **Mobile** | Expo / React Native 0.85 + TypeScript | EAS Build → Google Play (Android AAB) |

Core domain: an Indian classical arts e-learning platform selling **recorded video courses** (with AI-dubbed multi-language audio tracks) and **live 1:1/group classes** (Zoom/Meet/Teams), with Razorpay payments, OTP + Google auth, and a full admin backoffice.

---

## 1. Backend — Django Apps

| App | Purpose |
|---|---|
| **users** | Custom `User` model (OTP/email/phone + Google login), roles (student/teacher/superuser), dynamic onboarding forms, admin user management |
| **courses** | Catalog domain: `Course → Module → VideoLesson`, `Enrollment`, `LessonProgress`, AI dubbing (`TranslatedAudio` + Celery/OpenAI/Google pipeline), live classes (`LiveClass`, `LiveBatch`, `LiveBatchStudent`) |
| **orders** | `Purchase` model + Razorpay order creation/verification, admin purchase management |
| **cms** | Landing page content: `HeroSection` (singleton) + `Feature` list |
| **notifications** | `Notification` + `Announcement` models, notification service layer, `post_save` signal on `Enrollment` |
| **core** | Project settings, URL routing, Celery bootstrap — no models |

*(`backend/test_zip_extract/` is a stray duplicate, not real source — ignore it.)*

---

## 2. Database Schema

### `users` app

**User** (extends `AbstractUser`, `AUTH_USER_MODEL`)
| Field | Type | Options |
|---|---|---|
| is_teacher | Boolean | default=False |
| is_student | Boolean | default=True |
| is_onboarded | Boolean | default=False |
| onboarding_data | JSONField | default=dict, blank=True |
| phone_number | Char(15) | blank/null |
| parent_name | Char(255) | blank/null |
| parent_phone | Char(15) | blank/null |

**OnboardingField**
| Field | Type | Options |
|---|---|---|
| name | Char(50) | variable name |
| label | Char(100) | display label |
| field_type | Char(20) | choices: text/textarea/date/dropdown/checkbox |
| is_required | Boolean | default=True |
| options | JSONField | blank/null |
| order | Integer | default=0, `ordering=['order']` |

**OTPVerification**
| Field | Type | Options |
|---|---|---|
| identifier | Char(255) | email or phone |
| otp | Char(6) | |
| created_at | DateTime | auto_now_add |
| is_verified | Boolean | default=False |

### `courses` app

**Course**
| Field | Type | Options |
|---|---|---|
| title | Char(255) | |
| description | Text | |
| price | Decimal(10,2) | default=0.00 |
| thumbnail | Image | upload_to='course_thumbnails/' |
| is_published | Boolean | default=False |
| course_type | Char(20) | choices: LIVE/RECORDED, default=RECORDED, indexed |
| created_at/updated_at | DateTime | auto_now_add / auto_now |

**Module** — `course→Course` (CASCADE, related_name='modules'), title, order (`ordering=['order']`)

**VideoLesson** — `module→Module` (CASCADE, related_name='lessons'), title, description, transcript (Text, for AI dubbing), timed_transcript (manual `HH:MM:SS --> text` sync), video_file (FileField), order

**Enrollment** — `user→User`, `course→Course` (both CASCADE), enrolled_at; **unique_together('user','course')**

**TranslatedAudio** — `lesson→VideoLesson` (CASCADE, related_name='translated_audios'), language_code (e.g. ml-IN), audio_file, status (processing/completed/failed); **unique_together('lesson','language_code')**

**LessonProgress** — `user→User`, `lesson→VideoLesson` (CASCADE), last_watched_position (Float), video_duration (Float), completed (Boolean), completed_at; **UniqueConstraint(user, lesson)**; property `progress_percentage`

**LiveClass**
| Field | Type | Options |
|---|---|---|
| course | FK→Course | CASCADE, indexed |
| instructor | FK→User | SET_NULL, indexed |
| batch | FK→LiveBatch | SET_NULL, indexed |
| title/description | Char/Text | |
| scheduled_start | DateTime | indexed |
| duration_minutes | PositiveInt | |
| meeting_provider | Char(20) | ZOOM/GOOGLE_MEET/TEAMS/OTHER |
| meeting_url | URL(1000) | |
| status | Char(20) | SCHEDULED/LIVE/COMPLETED/CANCELLED, indexed |

Custom `save()`: derives course/instructor from batch, enforces duration>0, calls `full_clean()`.

**LiveBatch** — `course→Course`, `instructor→User` (SET_NULL); batch_type (ONE_TO_ONE/GROUP, indexed); validates course must be LIVE type, instructor must be staff/teacher

**LiveBatchStudent** — `batch→LiveBatch`, `student→User` (CASCADE), `purchase→orders.Purchase` (SET_NULL); **unique_together('batch','student')**; validates ONE_TO_ONE batches cap at 1 student

### `orders` app

**Purchase** — `user→users.User`, `course→courses.Course` (CASCADE); razorpay_order_id/payment_id/signature; amount (Decimal); status (PENDING/SUCCESS/FAILED, default PENDING)

### `notifications` app

**Notification** — `recipient→User` (CASCADE, indexed); title, body; notification_type (COURSE_UPDATE/ENROLLMENT/PAYMENT/ANNOUNCEMENT/COURSE_COMPLETION/CERTIFICATE/LIVE_CLASS); is_read (indexed); read_at; action_url; **idempotency_key (unique, indexed)**; `ordering=['-created_at']`

**Announcement** — `sender→User` (SET_NULL), `course→Course` (SET_NULL, null=global); title, content, is_published

### `cms` app

**HeroSection** (singleton via `get_or_create(id=1)`) — title, subtitle, description, button_text, button_link, bg_image_url (all with defaults)

**Feature** — title, description, order

### Relationship Map
```
User ─┬─< Enrollment >─ Course ─┬─< Module ─< VideoLesson ─┬─< TranslatedAudio
      ├─< Purchase   >──────────┤                          └─< LessonProgress >─ User
      ├─< Notification          ├─< LiveClass >─ LiveBatch ─< LiveBatchStudent ─┬─ User
      ├─< Announcement          ├─< LiveBatch                                  └─ Purchase
      └─< LessonProgress        └─< Announcement
```
No M2M or OneToOne fields anywhere — everything is FK-based.

---

## 3. Backend API Endpoints

Root routing (`core/urls.py`): `/api/courses/`, `/api/orders/`, `/api/cms/`, `/api/auth/` (dj-rest-auth), `/api/users/`, `/api/` (notifications), `/accounts/` (allauth social).

### `/api/users/`
| Method | Path | Auth | Purpose |
|---|---|---|---|
| POST | send-otp/ | Open | Send OTP (email mock / WhatsApp via Interakt) |
| POST | verify-otp/ | Open | Verify OTP, get-or-create user, issue JWT |
| POST | mobile-google-login/ | Open | Verify Google ID token, issue JWT |
| GET | me/ | Auth | Current user |
| GET | onboarding-fields/ | Open | Dynamic onboarding schema |
| POST | save-profile/ | Auth | Save onboarding_data, set is_onboarded |
| GET | admin-stats/ | SuperAdmin | Dashboard stats |
| CRUD | admin-users/ | SuperAdmin | User management |
| POST | admin-users/{id}/assign_course, mark_purchase_paid, unassign_course, enroll_course/ | SuperAdmin | Manual enrollment/payment ops |
| GET | admin-users/{id}/courses, purchases, teacher_students/ | SuperAdmin | Related data |
| CRUD | onboarding-fields-admin/ | SuperAdmin | Manage onboarding form schema |

### `/api/courses/`
| Method | Path | Auth | Purpose |
|---|---|---|---|
| CRUD | `` (CourseViewSet) | Teacher/Admin write, public read (published only) | Courses |
| GET | my_courses/ | Auth | Enrolled courses |
| CRUD | modules/, lessons/ | Teacher/Admin | Curriculum structure |
| POST | lessons/{id}/generate_ai_audio/ | Teacher/Admin | Trigger Celery AI dubbing |
| GET/POST/PATCH | lessons/{id}/progress/ | Auth (enrollment-gated) | Watch progress |
| CRUD | enrollments-admin/ | SuperAdmin | Admin enrollment list (filter Paid/Manual) |
| CRUD | live-classes/ | Auth, role-scoped | Live class CRUD |
| POST | live-classes/{id}/start, end, cancel, reschedule/ | role-scoped | State transitions + notifications |
| GET | live-classes/upcoming, history/ | role-scoped | Filtered lists |
| CRUD | live-batches/ | Admin/Staff write | Batch CRUD |
| GET/POST | live-batches/{id}/students/ | Admin/Staff | Assign/list batch students |
| DELETE | live-batches/{id}/students/{student_id}/ | Admin/Staff | Remove student |

### `/api/orders/`
| Method | Path | Auth | Purpose |
|---|---|---|---|
| POST | create-order/ | Open (dev note: "temporarily AllowAny") | Create Razorpay order + PENDING Purchase |
| POST | verify-payment/ | Auth | Verify signature → SUCCESS + Enrollment |
| CRUD | purchases-admin/ | SuperAdmin | Admin ledger |
| POST | purchases-admin/{id}/mark_paid/ | SuperAdmin | Manual mark paid + enroll |

### `/api/cms/`
GET `landing-page/` (public); CRUD `hero-admin/`, `features-admin/` (SuperAdmin)

### `/api/` (notifications)
GET `notifications/` (list/filter by is_read), `notifications/{id}/`, POST `notifications/{id}/read/`, `notifications/read-all/`, GET `notifications/unread-count/`; CRUD `announcements/` (students: published+relevant; staff: full CRUD)

---

## 4. Backend Settings & Infra

- **DB**: PostgreSQL (AWS RDS) in prod via `RDS_DB_NAME` env detection; SQLite fallback in dev
- **Auth**: JWT via `dj-rest-auth` + `simplejwt`, cookie-based (`natya-auth`/`natya-refresh`, SameSite=None, Secure, domain `.natyaarts.com`), 30-day access & refresh token lifetime; plus custom OTP flow and Google ID-token / allauth social login
- **Storage**: AWS S3 (`django-storages`+`boto3`) for media when creds present, else local; WhiteNoise for static
- **CORS/CSRF**: explicit allowlist (`natya-lms.vercel.app`, `academy.natyaarts.com`, localhost)
- **Integrations**: Razorpay (payments), Interakt (WhatsApp OTP), OpenAI Whisper (transcription), Google Cloud Translation + TTS (dubbing), Google OAuth, Celery+Redis (background jobs); email sending is **stubbed/not implemented**
- **Security note**: some secrets have insecure hardcoded fallback defaults if env vars are missing — flag for hardening
- **Migrations**: courses=11 (most mature), users=4, notifications=3, cms=1, orders=1

### Key dependencies
Django≥5.0.3, DRF≥3.15, dj-rest-auth, django-allauth, simplejwt, razorpay, django-storages+boto3, celery+redis, openai, pydub, google-auth, psycopg2-binary, gunicorn, whitenoise

---

## 5. Backend Business Logic (Services/Tasks)

- **`courses/tasks.py`**: `generate_dubbed_audio_task` (Celery, retry w/ backoff, differentiates transient vs permanent errors); `send_class_reminder` (scheduled 1hr before LiveClass via `transaction.on_commit`, re-validates class wasn't cancelled)
- **`courses/services/ai_translator.py`**: Full dubbing pipeline — ffmpeg audio extraction → Whisper transcription (or manual timed_transcript) → Google Translate per segment → Google TTS → time-stretch/pad via pydub → ffmpeg concat → save `TranslatedAudio`
- **`courses/services/live_batch_service.py`**: `assign_student()` — transactional, row-locked, validates purchase/capacity, syncs `Enrollment`
- **`notifications/services.py`**: `NotificationService.create_notification()` (idempotent via unique key), `trigger_payment_success()`, `LiveClassNotificationService` (scheduled/rescheduled/cancelled events)
- **Signal**: `post_save` on `Enrollment` → auto-creates ENROLLMENT notification

---

## 6. Frontend (Next.js Web)

**Stack**: Next.js 16 (App Router) + React 19 + TypeScript + Tailwind v4, Framer Motion, lucide-react. **No** state library, no React Query, no axios — raw `fetch` everywhere; no form library; auth via Django session cookies + manual CSRF header (no NextAuth); Razorpay Checkout.js for payments.

### Route tree
```
/                                  Public landing (CMS-driven hero+features)
/login, /register                 OTP + Google OAuth
/onboarding                        Dynamic profile form
/dashboard                         Student "My Learning"
/courses                           Public catalog (RSC)
/courses/[id]                      Course detail + checkout (RSC)
/courses/[id]/learn                Custom video player w/ dubbing switch
/admin (layout-guarded)
  /admin, /admin/login
  /admin/courses, /new, /[id]      Course + curriculum editor, AI dub trigger
  /admin/users, /[id]              User mgmt, enroll/assign, teacher-student linking
  /admin/onboarding-fields         Onboarding schema builder
  /admin/payments                  Purchase ledger, manual mark-paid
```
No route groups, no `middleware.ts` — auth guarding is 100% client-side (`fetch('/api/users/me/')` on mount + redirect).

### Components (only 3 extracted; most UI is inline per-page)
`CheckoutButton.tsx` (Razorpay flow), `CountrySelect.tsx` (phone country picker), `NotificationBell.tsx` (unread badge/dropdown)

### Key features
Landing page, OTP+Google auth, dynamic onboarding, course catalog/checkout (Razorpay, single-item, no cart), custom HTML5 video player with **watch-progress resume** and **multi-language audio dub switching**, student dashboard w/ progress bars + announcements, notification bell, full admin backoffice (analytics, course/curriculum editor incl. AI dubbing trigger, user management, payments ledger, onboarding field builder). No quizzes; certificates mentioned as backend-only concept.

**Env var**: `NEXT_PUBLIC_API_URL` (fallback `http://localhost:8000`) — no `.env.example` committed.

---

## 7. Mobile (Expo / React Native)

**Stack**: Expo SDK ~56, RN 0.85.3, React 19.2, TypeScript, React Navigation v7 (native-stack + bottom-tabs), axios (with JWT interceptor + AsyncStorage), expo-video + expo-screen-capture (anti-recording), Google Sign-In. No state library, no UI kit — hand-rolled dark theme (`#050505`/`#facc15`).

### Screens (flat, student-only — no instructor app)
- `LoginScreen` — Phone+OTP (+91 hardcoded) / Google Sign-In
- `OnboardingScreen` — server-driven dynamic form
- `DashboardScreen` ("My Learning" tab) — enrolled courses
- `CatalogScreen` ("Catalog" tab) — browse all courses
- `CourseDetailsScreen` — marketing page, "Buy" deep-links to **web checkout** (avoids Play Billing 30% cut)
- `LearnScreen` — video player (locked list if not enrolled)

### Navigation
```
Stack: Login → Onboarding → MainTabs(Dashboard, Catalog) → CourseDetails / Learn
```
Boot logic: checks AsyncStorage token → `users/me/` → routes to MainTabs/Onboarding/Login.

### API client (`src/api/client.ts`)
Hardcoded prod base URL `https://academy-api.natyaarts.com/api/`, Bearer token injected via interceptor, **no refresh-token flow implemented** despite storing one.

**Known gaps to flag**: hardcoded API URL (no env switching), no 401/refresh interceptor, Google Sign-In web client ID left as literal placeholder (`YOUR_WEB_CLIENT_ID_HERE`) — not functional yet.

---

## 8. Cross-Cutting Notes / Risks

- Payment flow bypasses in-app purchase entirely (web-redirect pattern) on mobile — compliant with Play Store IAP policy but means no native payment UX.
- CSRF-token cookie parsing logic is duplicated 5× across the frontend instead of centralized.
- Layout/nav chrome duplicated per-page in frontend (no shared `Layout`/`Navbar` component).
- Email delivery is stubbed/mocked — not production-ready.
- Some secret keys have insecure hardcoded fallbacks in settings.py.
- `orders/create-order/` is marked `AllowAny` with a "temporarily for local testing" comment — worth revisiting before hardening auth.
