# Progress

Updated after every phase so a new session can carry on from here.

## Where things stand (27 October, end of Phase 0)

Phase 0, the foundation, is done and every check passes:

| Check | Result |
|---|---|
| API tests (`cd api && uv run pytest --cov`) | 142 passed, 97% line coverage (minimum 85%) |
| Lint and format (`uv run ruff check . && uv run ruff format --check .`) | clean |
| Types (`uv run mypy`, strict) | clean |
| Web types and unit tests (`cd web && npm run typecheck && npm test`) | clean, 11 passed |
| Web build (`npm run build`) | builds |
| Browser tests (`npm run e2e`, desktop and phone) | 10 passed |

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

## Also done on 27 October, at Ahsaan's request

- Instagram DMs and Facebook Messenger: one adapter for both, one webhook for all three
  channels (`/webhooks/meta`), replies through the same reply job, quick replies for
  buttons, referral refs kept. Tested end to end against mocked Graph endpoints.
- Shop pages link out with Meta's official short links: wa.me (with a greeting),
  ig.me/m and m.me (with a ref). `python -m chatfront.channels.connect` attaches a real
  number, Instagram account or Page to a shop.
- Redesigned web app: landing page with the payment-verification story, shop profile with
  Chat here plus WhatsApp, Instagram and Messenger, a polished chat with suggestions, quick
  replies and ticks.
- The web app opens at once without the API: demo shops are bundled from the templates, a
  countdown shows while Render wakes, and chat messages wait and send when it answers.
  GitHub Pages deploy workflow (manual) for deploy day; CORS on the API.

## Done on 27 October: the language layer and country packs (121, 122)

Built first in Phase 1 so nothing has to be reworked later. Checks after it:

| Check | Result |
|---|---|
| API tests | 348 passed, 96.9% line coverage |
| Lint, format, strict types | clean |
| Web types and unit tests | clean, 18 passed (includes the ICU cases shared with the API) |
| Browser tests, desktop and phone | 34 passed (right to left in ur, pa-Arab, ps, sd, ar; hi and bn left to right with their fonts; an Egyptian browser opens in Arabic; a Pashto reply renders right to left) |

Language evaluation (`python -m chatfront.eval.languages`), mock models, 27 October:

| Tier 1 language | Text conversations passed | Voice notes |
|---|---|---|
| ur, ur-Latn, en, ps, sd, hi, hi-Latn, bn, bn-Latn, ar, afb, arz, ar-Latn | 10 of 10 each | 2 each, pending recordings |
| pa-Arab (Punjabi, Shahmukhi) | 10 of 10 (6 of 10 before Punjabi marker words were added) | 2, pending recordings |

What the mock run measures: that each language is recognised and the reply is routed to that
language. It does not measure how well the model writes Pashto or Sindhi; only a live run
(`--live`, needs BASETEN_API_KEY) can, and only a live run can lift a language out of beta.
The Punjabi word list was extended after the first run, using the misses; held-out Punjabi and
Urdu lines in the unit tests check it did not overfit.

Still open for 121 and 122: product translations (with the catalog), the handover screen
(90), live eval, voice recordings, native-speaker review of every non-English catalog and of
the eval lines, and real SMS samples to verify the parsers.

## Next: Phase 1 (Build features, by 2 November)

In the timeline's order:

1. 28 Oct: catalog and order states: 1, 4, 83, 86.
2. 29 Oct: order agent and guards: 9, 10, 11, 12, 89, 90, 93, 94, 105, 106, 109 (demo login).
3. 30-31 Oct: payments: 23, 24, 25, 26, 27, 30, 97, 98, 99.
4. 31 Oct: delivery and COD: 33, 34, 101.
5. 1 Nov: books, stock and shield: 38, 42, 48, 49, 50, 52, 75, 104, 107.
6. 1-2 Nov: restock, owner panel, batches and expiry: 117, 118, 119, 120.
7. 2 Nov: dashboard, eval set, cost meter, reasoning routing, deploy: 74, 80, 111, 112, 113.
   The dashboard parts of 117 to 120 (sheet upload, queued alerts, batch and expiry views)
   land here with 74.

Build is now 45 features in six days; 117 to 120 are the largest. If 2 November slips, the
dashboard views of 119 and 120 are the first to move, since the WhatsApp cards already show
the same numbers.

## Plan for 117 to 120

Shared rules: money in integer paisa; code decides every stock and price change, the model
only reads; nothing changes without the owner's Confirm; every change writes a stock
movement and an audit entry; only staff with the owner or the new `stock` role can change
stock, prices or limits.

**117 Restock by chat, voice note or bill photo.**
- Tables: `stock_movements` (product or variant, delta, before, after, source: chat, voice,
  bill, sheet, order, cancel, return, write-off; staff or agent; time). The only path that
  changes stock writes one; a test proves stock cannot change without a movement.
- Chat and voice: Spark Open turns "blue lawn ke 30 aur aa gaye" into {product text,
  quantity}; code matches products (name, variants, aliases); several matches get a list to
  pick from; then "Blue lawn: 54 + 30 = 84" with Confirm and Cancel buttons. A pending
  change is stored and applied once, on Confirm only.
- Bill or challan photo: Spark Open (vision role, JSON schema) extracts lines {item,
  quantity, unit cost, total}; code matches lines to products; one list to confirm;
  unmatched lines map to a product or become draft products; one Confirm updates every
  line, creates the batches (119) and writes the purchase to the ledger as an expense (48).
- Sheet upload: the CSV/Excel import path is built here, column matching by the model,
  rows confirmed like a bill; 85 reuses it later.
- Tests: matching (one, several, none), confirm and cancel, bill extraction on mock
  fixtures, the ledger entry, no stock change without a movement.

**118 Owner panel inside WhatsApp.**
- WhatsApp Flows (navigate flows that carry their data in the message, so no encrypted
  endpoint is needed) for add or edit product, change price, low-stock limits and shop
  settings; list messages to browse products and pending orders. The Flow JSON is
  published once in WhatsApp Manager.
- Limits: one per product, plus a shop default; set by Flow or by chat ("blue lawn ka
  alert 10 pe").
- Alert state per product: alerted once when stock crosses below the limit after an order,
  cancel or manual change; no repeat until stock is back above it. Buttons: "Send reorder to
  supplier" (the supplier draft from 53, built here as far as 118 needs it) and "Snooze".
- Inside the owner's 24-hour window: free service message. Outside it: an approved utility
  template; with no template yet, the alert is queued and shown on the dashboard.
- Tests: the limit crossing (alert once, not repeated, again after recovery), role checks,
  the 24-hour window rule, the queued alert.

**119 Stock batches and true profit per batch.**
- Tables: `stock_batches` (product or variant, quantity, units left, total cost, unit cost,
  supplier, date, expiry, source bill) and `batch_allocations` (order line, batch, units,
  unit cost at sale). Unit cost spreads any remainder paisa so the batch total stays exact.
- Allocation: FIFO, or FEFO for products with expiry; one order line can span two batches;
  a return goes back to the batch it came from. Profit already earned never changes.
- When a new batch costs differently from stock still on hand, the owner gets the price
  question with three buttons: keep the price, new price now, new price when the old
  batch sells out. Code never sets a price by itself.
- Reports per batch and per product, in the dashboard and as a WhatsApp card; feeds 51.
- Tests: FIFO and FEFO order, cost saved per order line, the owner's example (batch A cost
  54,000, sold 60,000, profit 6,000; batch B cost 58,000, sold 70,000, profit 12,000; one
  order spanning both), returns to the right batch, the price question.

**120 Expiry dates and write-offs.**
- Expiry per batch by Flow, chat ("expiry 12 Dec") or from the bill or label photo, always
  confirmed by the owner.
- A daily cron job finds batches inside the warning period (default 7 days) and sends one
  alert per batch with Discount, Discard and Remind later; expired batches are marked and
  never allocated to a sale.
- Discard (owner or stock role): a write-off movement with a reason (expired, damaged,
  lost) and the cost in the ledger as a loss that shows in profit reports.
- Tests: one alert per batch, expired stock cannot be sold, a discard writes the loss.

The order agent replaces `ShopAssistant` as the responder in `services.py`; the reply job
stays the same.

## Open questions for Ahsaan

1. Render: which service to free. Ahsaan will say before deploy day.
2. Credits: the Baseten form is submitted, Modal and Fireworks are in progress, nothing has
   arrived. Building continues on the mock.
3. Payment samples: real credit SMS and screenshots, with name, number and TID covered,
   coming before 30 October. They will turn the JazzCash, Easypaisa and bank parsers from
   unverified to verified.
4. Pilot shops and their business types.
5. Native speakers to read the Punjabi, Pashto, Sindhi, Hindi, Bengali and Arabic strings
   and eval lines, and to record the two voice notes per language (script in the eval data).

## Decisions taken

- Features 117 to 120 were added on 27 October as Build features, placed after the 1 November
  books and stock work so the planned order is unchanged.
- A `stock` staff role is added with 117 and 118.

- Spark Open model slug on Baseten is `meta-models/muse-spark-open-1026`, as section 6 of the
  participant guide says (the docs' `meta-models/muse-spark-open` is not the hackathon one).
  Set by `SPARK_MODEL_BASETEN`.
- Event times are Eastern Time (participant guide). The first commit, 26 Oct 22:44 Pacific, is
  27 Oct 01:44 ET, inside the building period. History is never rewritten.
- Spark Open failover is Baseten -> Fireworks; Modal hosts only Glimmer.
- Shop pages live at `/s/<slug>`; one Render service serves API and web.
- Ids are UUIDv7; order codes will use the short Crockford code in `core/ids.py`.
- The interface opens in the saved language, else the first browser language with a catalog
  (an Egyptian browser gets Arabic), else English; the choice is remembered.
- Pashto, Sindhi and Punjabi readers fall back to Urdu, then English, wherever a string or a
  shop text has no translation of its own. Hinglish and Banglish readers get English.
- A customer's explicit request ("English mein baat karo") is kept as their choice and wins
  over detection until they ask for another. Asked for in Roman Urdu, Hinglish or Banglish,
  "Urdu", "Hindi" or "Bangla" means the romanised form unless they mention the script.
- In the evaluation, Gulf and Egyptian customers answered in Modern Standard Arabic, and
  Roman Urdu answered as Hinglish, count as answered in their language (each reads the other);
  the exact-language count is reported next to it.
- SMS parser fixtures are synthetic and labelled so; every parser stays unverified until a
  real message passes, and a test enforces that.

## Working notes for the next session

- Integration tests need PostgreSQL 16 with a non-superuser owner role; see README step 1.
  `TEST_DATABASE_URL` and `E2E_DATABASE_URL` point the tests elsewhere.
- If the installed Chromium does not match Playwright's version, set
  `PLAYWRIGHT_CHROMIUM_PATH` to its executable.
- Commits go out as Ahsaan Munir <ahsaanmunir53@gmail.com>, with no co-author line.
