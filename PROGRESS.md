# Progress

Updated after every phase so a new session can carry on from here.

## Where things stand (27 October, end of Phase 0)

Phase 0, the foundation, is done and every check passes:

| Check | Result |
|---|---|
| API tests (`cd api && uv run pytest --cov`) | 129 passed, 97.7% line coverage (minimum 85%) |
| Lint and format (`uv run ruff check . && uv run ruff format --check .`) | clean |
| Types (`uv run mypy`, strict) | clean |
| Web types and unit tests (`cd web && npm run typecheck && npm test`) | clean, 8 passed |
| Web build (`npm run build`) | builds |
| Browser tests (`npm run e2e`, desktop and phone) | 8 passed |

What Phase 0 delivered:

- Database: Alembic migration 0001 with every Phase 0 table and row-level security per
  tenant. Tenant transactions run as `chatfront_app` with `app.tenant_id` set; tests prove a
  tenant cannot read or write another tenant's rows and the app cannot edit the audit log.
- Models: OpenAI-compatible provider, roles with reasoning effort, failover
  Baseten -> Fireworks (Glimmer on Modal first for watcher and simulator), JSON-schema
  outputs with one repair round, tool registry, prompt files, cost meter in `ai_calls`,
  mock provider with fixtures. Production never falls back to the mock.
- Channels: WhatsApp webhook (signature check, idempotent, statuses), media download,
  sends with the 24-hour window guard, the try-it chat API, the reply job (safe to run twice).
- Queue: Postgres jobs with SKIP LOCKED, retries with backoff, dead jobs, stale-job
  requeue; `/internal/cron` for cron-job.org.
- Media: local and Supabase storage, signed URLs, type sniffing, size limits, dedupe,
  blurred copies.
- Audit: hash-chained per tenant with verification.
- Six business templates and a demo shop for each (`python -m chatfront.seed`).
- Web: home with the demo shops, shop page, phone-style try-it chat, trust page, receipt and
  dashboard placeholders, English, Urdu (right to left) and Roman Urdu, installable PWA.
- CI workflow, Dockerfile and Render blueprint, `.env.example` (a test keeps it in step
  with the settings).

Not verified yet: the Docker image and the Render deploy (no Docker here); the live model
calls and the real WhatsApp webhook (no keys yet).

## Next: Phase 1 (Build features, by 2 November)

In the timeline's order:

1. 28 Oct: catalog and order states: 1, 4, 83, 86.
2. 29 Oct: order agent and guards: 9, 10, 11, 12, 89, 90, 93, 94, 105, 106, 109 (demo login).
3. 30-31 Oct: payments: 23, 24, 25, 26, 27, 30, 97, 98, 99.
4. 31 Oct: delivery and COD: 33, 34, 101.
5. 1 Nov: books, stock and shield: 38, 42, 48, 49, 50, 52, 75, 104, 107.
6. 2 Nov: dashboard, eval set, cost meter, reasoning routing, deploy: 74, 80, 111, 112, 113.

The order agent replaces `ShopAssistant` as the responder in `services.py`; the reply job
stays the same.

## Open questions for Ahsaan

1. GitHub repo `chatfront`: created? Access for pushing this history.
2. Render: which of the 25 services to free.
3. Which credits arrived: Baseten, Fireworks, Modal.
4. Payment samples: which wallets and banks; one real small transfer's credit SMS and
   screenshot from each, with name, number and TID covered. Needed before 30 October.
5. Pilot shops and their business types.

## Decisions taken

- Spark Open model slug on Baseten is `meta-models/muse-spark-open` (Baseten's model page);
  the spec's `-1026` suffix is kept out until a live call confirms it. Configurable.
- Spark Open failover is Baseten -> Fireworks; Modal hosts only Glimmer.
- Shop pages live at `/s/<slug>`; one Render service serves API and web.
- Ids are UUIDv7; order codes will use the short Crockford code in `core/ids.py`.
- The language defaults to the browser's (Urdu for Urdu browsers, else English), and the
  choice is remembered.

## Working notes for the next session

- Integration tests need PostgreSQL 16 with a non-superuser owner role; see README step 1.
  `TEST_DATABASE_URL` and `E2E_DATABASE_URL` point the tests elsewhere.
- If the installed Chromium does not match Playwright's version, set
  `PLAYWRIGHT_CHROMIUM_PATH` to its executable.
- Commits go out as Ahsaan Munir <ahsaanmunir53@gmail.com>, with no co-author line.
