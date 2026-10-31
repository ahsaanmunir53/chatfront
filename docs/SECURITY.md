# Chatfront security checklist (20 points)

Status is deliberately conservative: **Implemented** means the behavior is present in code and has a focused test or CI guard; **Partial** means meaningful controls exist but the full checklist is not yet verified; **Open** means further implementation or a route-by-route audit is required. Passing CI alone does not prove every route is secure.

| # | Area | Status | Evidence / tests | Remaining work |
|---:|---|---|---|---|
| 1 | SQL injection | Implemented (review) | SQLAlchemy ORM with bound parameters everywhere; the only f-string SQL is in `core/rls.py` and `core/db.py` with constant names; name searches in `insights/price_check.py` use letters-only words. | A dedicated injection test for search inputs. |
| 2 | XSS / rendered HTML | Implemented | React escapes text; no `dangerouslySetInnerHTML`. The share page escapes every value and redirects with a meta refresh, no script: `api/tests/unit/test_catalog_share.py`, `api/tests/integration/test_app.py` (shared page escapes shop and product text), `api/tests/test_security_config.py` (script-free share page). | None. |
| 3 | CSRF | Not applicable (by design) | Owner and chat auth travel only in headers (`Authorization`, `X-Chat-Token`), never cookies, so a cross-site form cannot carry them. | Add SameSite cookies and CSRF tokens if a cookie session is ever added. |
| 4 | Upload validation | Implemented | `media/service.py` sniffs the type from the bytes and caps the size; renamed HTML and SVG are refused: `api/tests/unit/test_core.py` (media sniff). | Per-route allow-lists for sheets. |
| 5 | SSRF | Implemented | Photos are fetched only from Meta hosts over https on the standard port, with no user info; the HTTP client does not follow redirects to other hosts: `api/tests/unit/test_catalog_import.py`, `api/tests/unit/test_http.py`. Model, courier and storage URLs come from env only. | None. |
| 6 | BOLA / tenant isolation | Implemented | RLS on every tenant table plus tenant sessions; another shop's customer, conversation and batch ids answer 404 and change nothing: `api/tests/integration/test_tenant_isolation.py`, `api/tests/integration/test_rls.py`. | Extend the isolation test to every id-taking route as routes are added. |
| 7 | Rate limiting | Implemented | Public route families (chat, demo, receipts, passports, keys, trust, webhooks) are limited and answer 429 with `Retry-After`: `api/tests/integration/test_app.py`, `api/tests/test_security_config.py`. | Limits are per process (one Render instance). |
| 8 | Passwords | Not applicable | Owners sign in by signed links (`owner/sessions.py`); no password is accepted or stored. | None. |
| 9 | MFA / session invalidation | Implemented | The sign-in link reaches the owner's own WhatsApp (possession). Sessions carry a version; "sign out of all devices" revokes them; team changes, money settings, link regeneration and erasure need a recent sign-in: `api/tests/integration/test_owner_flow.py`, `api/tests/test_security_config.py`. | None. |
| 10 | Server permissions | Implemented | A test walks every `/api/owner` and `/api/platform` route and fails without a rights or reviewer dependency; role tests check 403s: `api/tests/test_security_config.py`, `api/tests/integration/test_owner_flow.py`. | None. |
| 11 | RLS | Implemented | Every table with `tenant_id` must have RLS on and the tenant policy: `api/tests/integration/test_rls.py`. | None. |
| 12 | Production secrets | Implemented | `Settings.require_production_secrets()` refuses default or short `SECRET_KEY`, `PHONE_HASH_KEY`, `CRON_SECRET`: `api/tests/test_security_config.py`. | Rotation procedure in the deploy notes. |
| 13 | API secrets | Implemented | Keys are `SecretStr` from env; logs are redacted; upstream error bodies are never exposed; gitleaks scans the full history in CI: `api/tests/test_security_config.py`, `api/tests/unit/test_http.py`. | None. |
| 14 | Browser token storage | Implemented | The owner token is kept in `sessionStorage` only, removed from the URL hash once read, and revocable server-side (point 9). | None. |
| 15 | Default credentials | Implemented (review) | No default admin or seeded password; production refuses development secrets; demo sign-in makes expiring copies only. | A dedicated test that demo sign-in cannot open the platform shop. |
| 16 | CORS | Implemented | Wildcards, paths and http origins are refused in production: `api/tests/test_security_config.py`. | None. |
| 17 | Webhooks | Implemented | Meta webhooks need a valid `X-Hub-Signature-256` and have a body cap; the SMS forwarder and cron compare secrets in constant time: `api/tests/integration/test_app.py`, `api/tests/integration/test_payments.py`. | Replay checks where a sender gives a timestamp. |
| 18 | Source maps | Implemented | `build.sourcemap: false`; CI fails if `web/dist` has `.map` files. | None. |
| 19 | Sensitive data / headers | Implemented | CSP (scripts from this origin only), frame denial, nosniff, Referrer-Policy and production HSTS on every response; errors carry no traceback; logs redact tokens, phones and emails: `api/tests/test_security_config.py`. | None. |
| 20 | Dependencies | Implemented | `pip-audit`, `npm audit --omit=dev --audit-level=high` and gitleaks in CI; Dependabot for Python, npm and Actions. | None. |

## Production deployment notes

- `SECRET_KEY`, `PHONE_HASH_KEY`, and `CRON_SECRET` must each be independently generated random values of at least 32 bytes. Do not paste them into issues, chats, commits, or this document.
- `CORS_ORIGINS` must be a comma-separated list of explicit origins. In production each origin must use HTTPS; wildcard origins, paths, credentials, queries and fragments are rejected.
- Production source maps are disabled. CI checks that the built `web/dist` contains no `.map` files.
- Security headers are emitted by the API. HSTS is emitted only when `ENV=prod`; deploy behind HTTPS.
- No new Render environment variables are introduced by this phase.

## Validation policy

Only report a control as verified after the relevant code, tests and CI run have been inspected. Keep this table current as each remaining audit is completed.
