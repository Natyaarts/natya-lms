# Production Hardening — Implementation & Verification Report

Scope: 11 approved production-safety fixes on top of the existing AWS
Elastic Beanstalk / RDS / S3 / Redis+Celery / Vercel / GitHub Actions
infrastructure. No infrastructure was rebuilt or redesigned. No Phase 2
business logic, live-class architecture, translated-audio sync logic, or
the AI-dubbing pipeline was touched. No Razorpay webhook was added.
Phase 3 was not started. Nothing was committed or pushed.

---

## Files changed

| File | Change |
|---|---|
| `backend/core/settings.py` | Items 1–8 (all settings hardening) |
| `backend/orders/views.py` | Item 9 — removed debug `print()` of the Razorpay key ID |
| `backend/test_zip_extract/` (53 tracked files, incl. `backend-release.zip`) | Item 10 — deleted (stray extracted deployment artifact) |
| `.gitignore` (repo root) | Item 10 — added `test_zip_extract/` and `*-release.zip` |
| `frontend/next.config.ts` | Item 11 — removed the dead hardcoded `http://` Elastic Beanstalk hostname from `images.remotePatterns` |
| `backend/.env` (**new, local-only**) | Not part of the 11 items — created so `DEBUG` defaulting to `False` doesn't break local development in this environment. Already covered by `.gitignore`; never committed. |

---

## Change-by-change explanation

1. **`LOGIN_REDIRECT_URL`** — now `f'{FRONTEND_URL}/dashboard'`. `FRONTEND_URL` defaults to `http://localhost:3000` only when `DEBUG=True`, and to `https://academy.natyaarts.com` (the real prod domain) when `DEBUG=False`. Can no longer resolve to localhost in production, with or without the env var set.
2. **`DEBUG`** — default flipped from `'True'` to `'False'`. Local dev preserved via `backend/.env` (`DEBUG=True`, gitignored).
3. **`ALLOWED_HOSTS`** — `DEBUG=True`: defaults to `localhost,127.0.0.1` (was `*`). `DEBUG=False`: **requires** the env var, raises `ImproperlyConfigured` at boot if missing.
4. **`SECRET_KEY`** — `DEBUG=True`: obviously-fake local-only placeholder (replacing a real-looking, source-visible key). `DEBUG=False`: **requires** the env var, raises if missing.
5. **`INTERAKT_SECRET_KEY`** — same pattern. Note: the previous hardcoded fallback here was **not a placeholder** — an older committed snapshot of this file (inside the now-deleted `test_zip_extract/`) shows this line used to default to `''`. The value that had been added looked like a real, live credential. **Recommend rotating this key with Interakt regardless of this code change**, since it sat in git history.
6. **S3** — added `AWS_STORAGE_BUCKET_NAME` to the "all creds present" check (previously only 2 of 3 vars were checked, so a missing bucket name would have misconfigured `S3Boto3Storage` and failed confusingly on first upload). `DEBUG=False` and creds incomplete → raises at boot. `DEBUG=True` and no creds → unchanged, falls through to local `FileSystemStorage`.
7. **Celery/Redis** — `DEBUG=True`: unchanged, defaults to `redis://localhost:6379/0`. `DEBUG=False`: **requires** both `CELERY_BROKER_URL` and `CELERY_RESULT_BACKEND`, raises if either is missing.
8. **CORS / CSRF** — both now `os.environ.get('CORS_ALLOWED_ORIGINS' / 'CSRF_TRUSTED_ORIGINS', <default>)`, comma-split, overridable. `DEBUG=True` default: today's localhost/127.0.0.1 list, **minus** the two `192.168.1.43` LAN-IP entries (flagged as dev cruft in the audit — re-add via the env var locally if your team's on-device testing needs it). `DEBUG=False` default: the two real production origins (`https://academy.natyaarts.com`, `https://natya-lms.vercel.app`) — **not** a hard requirement, so this cannot break live CORS if the new env var isn't set in EB yet.
9. **`orders/views.py:30`** — deleted `print(f"USING RAZORPAY KEY ID: ...")`. Nothing else in that view changed.
10. **`test_zip_extract/`** — deleted (confirmed unreferenced by `.ebextensions`, `requirements.txt`, and the GitHub Actions deploy workflow, which zips `backend/` fresh from source). Added to `.gitignore` to prevent recurrence.
11. **`next.config.ts`** — removed only the EB hostname entry (confirmed dead: no `next/image` in the app points at a remote host). The `localhost:8000` and `images.unsplash.com` entries were left untouched.

---

## Local development impact

With `backend/.env` containing `DEBUG=True`, every hardened setting falls back to its exact previous local-dev value (or a functionally identical placeholder for `SECRET_KEY`) with **zero other environment variables required** — identical to the pre-change experience. Nothing in the test suite depends on `INTERAKT_SECRET_KEY` holding a real value.

Production behavior only changes for variables expected to already be set in the Elastic Beanstalk environment (per your confirmation that S3/Redis/Celery/RDS are already configured) — for those, this change is a no-op unless one turns out to be missing, in which case the app now **fails loudly at boot** instead of silently degrading.

**Action needed before deploying:** confirm these are set in the EB environment — `SECRET_KEY`, `ALLOWED_HOSTS`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_STORAGE_BUCKET_NAME`, `CELERY_BROKER_URL`, `CELERY_RESULT_BACKEND`, `INTERAKT_SECRET_KEY`. If any is missing, the app will now refuse to start rather than run in a broken state.

---

## Verification performed

### Backend tests
| Suite | Result |
|---|---|
| `orders` (touched directly) | 5/5 passed |
| `users` (auth/cookie-adjacent) | 26/26 passed |
| `courses` — core Live/Batch/Model/Migration classes | 52/52 passed |
| `courses` — all 8 Phase 2 classes incl. real-JWT-cookie tests | 46/46 passed |

### `makemigrations --check --dry-run`
Clean — "No changes detected" (settings.py changes don't touch models).

### Frontend TypeScript
`npx tsc --noEmit` — zero new errors. Only the pre-existing, unrelated `lucide-react` type-declaration gap remains (present before this change too).

### Fail-loud paths (verified by direct settings instantiation with `DEBUG=False`)
| Scenario | Result |
|---|---|
| `SECRET_KEY` unset | `ImproperlyConfigured: SECRET_KEY environment variable must be set in production.` |
| `ALLOWED_HOSTS` unset | `ImproperlyConfigured: ALLOWED_HOSTS environment variable must be set in production.` |
| S3 vars unset | `ImproperlyConfigured: AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY and AWS_STORAGE_BUCKET_NAME must all be set in production ...` |
| `INTERAKT_SECRET_KEY` unset | `ImproperlyConfigured: INTERAKT_SECRET_KEY environment variable must be set in production.` |
| Celery vars unset | `ImproperlyConfigured: CELERY_BROKER_URL and CELERY_RESULT_BACKEND environment variables must be set in production ...` |
| All vars set, `DEBUG=False` | Loads cleanly. `LOGIN_REDIRECT_URL = https://academy.natyaarts.com/dashboard`. `CORS_ALLOWED_ORIGINS = ['https://natya-lms.vercel.app', 'https://academy.natyaarts.com']`. |

### Targeted greps
- `localhost:3000/dashboard` in `settings.py` → none found.
- `redis://localhost` in `settings.py` → only inside the `if DEBUG:` branch.
- `ALLOWED_HOSTS` `'*'` fallback → gone, replaced with the guarded logic above.
- `USING RAZORPAY KEY ID` print → gone.
- `test_zip_extract` directory → gone from disk.
- The leaked-looking `INTERAKT_SECRET_KEY` value → gone from source.
- `elasticbeanstalk` hostname in `next.config.ts` → gone.

---

## Git diff summary

```
.gitignore                | +4   (excludes test_zip_extract/, *-release.zip)
backend/core/settings.py  | +99 -62  (all 8 hardening items)
backend/orders/views.py   | -1 line  (debug print)
frontend/next.config.ts   | -4       (dead EB http hostname)
backend/test_zip_extract/ | 53 files deleted (219K)
backend/.env              | new, gitignored, not tracked, not committed
```

Nothing else in the working tree was touched by this task. All other modified/untracked files visible in `git status` are pre-existing from earlier phases of this project.

**Not committed. Not pushed.**

---

## Status

- Phase 2: unaffected, still complete, all 46 dedicated + 133 regression tests still passing.
- Phase 3: **not started**, per instruction.
- Manual AWS Celery/Redis and live-class end-to-end tests: still pending — to be run manually against the real environment, as agreed.

**Waiting for approval before any further action.**
