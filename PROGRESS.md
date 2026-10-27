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

Still open for 121 and 122: the handover screen (90), live eval, voice recordings,
native-speaker review of every non-English catalog and of the eval lines, and real SMS samples
to verify the parsers. (Product translations were added with the catalog group.)

## Done on 27 October: the catalog group (1, 4, 83, 86; 109 and the shop page catalog)

Started a day early, at Ahsaan's request, in the planned order. Checks after it:

| Check | Result |
|---|---|
| API tests | 501 passed, 95.7% line coverage (116 of them are the money cases shared with the web) |
| Lint, format, strict types, `alembic check` | clean |
| Web types and unit tests | clean, 26 passed |
| Browser tests, desktop and phone | 42 passed |

What it delivered:

- Data: products, variants, append-only stock movements, orders and order items with an
  explicit state table (stock taken on placing, given back on cancel or return), pending
  owner actions. Migration 0004; every table under row-level security, checked by a test.
- The owner's assistant (staff are recognised by phone on WhatsApp, by a signed session on
  the web): a photo with words or a voice note becomes a product card checked against the
  template; prices, lowest prices, on and off sale and shop policies change by chat. Nothing
  changes until the owner taps Confirm or Apply, and buttons carry the action id, so an old
  card can never confirm a newer change.
- Without a model key, code reads typed names and prices, typed price changes and the start
  page answers, so the demo works before the credits arrive.
- Shops made by chat (83) on the web start page (/start) and, once a number is connected to
  the shop `chatfront`, on WhatsApp. Web-made shops are kept 7 days.
- Try as the owner (109): a signed-in three-day copy of any demo shop; at most 300 at once;
  a cron job removes expired ones with their files.
- Product texts translated per shop language in the background; the owner's own wording
  per language is never overwritten (part of 121).
- Web: the shop page catalog (price tags in the reader's digits, sold out, Ask about this),
  the owner console (assistant beside the product list, wording per language), the start
  page, photos and voice notes in every chat, list replies, the console link as a button.
- Country packs now carry `minor_digits` (browsers' ICU says 0 for the rupee; CLDR and the
  API say 2), and `i18n/money-cases.json` keeps API and web money text identical.
- The Docker image installs ffmpeg for voice notes. A manual "Live model evaluation"
  workflow runs the language set against the real models from repository secrets.

Not verified yet: real model reading of photos and voice notes (no keys yet), WhatsApp
onboarding on a real number, voice recording on iPhone Safari (it records MP4, which the
chat does not offer yet; Android and desktop browsers record WebM).

## Done on 27 October: the order agent group (9, 10, 11, 12, 89, 90, 93, 94, 105, 106)

Checks after it:

| Check | Result |
|---|---|
| API tests | 550 passed, 94.7% line coverage |
| Lint, format, strict types, `alembic check` | clean |
| Web types and unit tests | clean, 26 passed |
| Browser tests, desktop and phone | 44 passed |

What it delivered:

- The order agent answers customers on every channel (the shop assistant without tools
  now only serves the language evaluation). Spark Open calls the shop's tools for up to five
  rounds: find products, cart, shopping list, delivery details, offer, checkout, order status,
  change, cancel, hand over. Photos and voice notes reach it with the tools.
- Orders are placed, changed and cancelled only when the customer taps a card that code
  wrote from the database; buttons carry the action id.
- Bargaining, delivery charges, product search and list matching are code (see FEATURES).
- Answer check and injection guard on every reply.
- Handover: three-line summary to the owner on WhatsApp (when their chat is open) and in the
  owner console, where they read and reply in their own language and give the chat back.
- Migration 0005: price_offers (append-only), handovers, the catalog price on bargained lines.
- The mock model answers each round of a tool loop (`step`), so tool conversations are
  testable without keys.

Not verified yet: any of it against the real model (no keys yet); the WhatsApp handover
notice on a real number.

## Done on 27 October: the payments group (23, 24, 25, 26, 27, 30, 97, 98, 99)

Checks after it:

| Check | Result |
|---|---|
| API tests | 587 passed, 94.9% line coverage |
| Lint, format, strict types, `alembic check` | clean |
| Web types and unit tests | clean, 28 passed |
| Browser tests, desktop and phone | 46 passed |

What it delivered:

- Placing a transfer order asks for an exact, unique amount (a few rupees off) with the order
  code for the remarks; cash on delivery at a shop that takes an advance asks for the advance
  the same way. Part payments are instalments, each with its own receipt.
- A picture from a customer whose order waits for money is read as a payment screenshot
  before the order agent sees it, checked by code, and either settled by a matching SMS,
  handed to the owner, or left waiting for the SMS (15 minutes, then the owner).
- The SMS webhook (`/webhooks/sms/<shop>`, secret per shop) keeps only messages a provider
  parser trusts, sealed and deduplicated, and matches them to a screenshot or an order.
- The owner approves or rejects on WhatsApp (buttons) or in the console's payments panel;
  the console also lists unpaid orders and money received, and makes the forwarder link.
- Receipts are signed with each shop's own Ed25519 key; `/r/<code>` checks them in the
  browser and shows a QR code that opens the same page.
- The watcher runs from cron every 5 minutes; the watcher model writes one line on each
  payment card when every number in it is in the evidence.
- Migration 0006: payment_claims, wallet_credits, payments and receipts (the last three
  append-only); order advance, paid, due now and unique amount; shop SMS secret and receipt key.

Not verified yet: the screenshot reading against the real model (no keys yet); the parsers
against real SMS (samples not here yet, so every SMS match goes to the owner for now); a real
SMS forwarder app posting to the webhook.

## Done on 27 October: the delivery group (33, 34, 101)

Checks after it:

| Check | Result |
|---|---|
| API tests | 621 passed, 95.1% line coverage |
| Lint, format, strict types, `alembic check` | clean |
| Web types and unit tests | clean, 28 passed |
| Browser tests, desktop and phone | 48 passed |

What it delivered:

- Addresses: the agent splits them, code tidies them and decides whether a rider can find
  them; a vague one gets a pin request in the way the channel allows, and checkout waits for
  the pin. Pins come from WhatsApp locations, the web chat's Share my location, or a maps link.
- Risk: a score with reasons on every order, from the buyer's history in the shop (by
  customer and by phone number) and a few other signs; high-risk cash on delivery asks for an
  advance at shops that also take transfers.
- Shipping: the console's orders panel moves orders through packed, dispatched (courier and
  tracking number), delivered, returned or cancelled, checked against the order states; the
  customer hears when it leaves and arrives. Labels are A6 PDFs with a QR code to `/t/<code>`.
- Migration 0007: shipments, and each order's risk score and reasons.
- New dependencies: fpdf2 (LGPL-3.0) and uharfbuzz (Apache-2.0) for the labels; Noto fonts
  (OFL-1.1) are bundled in `api/src/chatfront/delivery/fonts/`.

Not verified yet: the address split by the real model (no keys yet); WhatsApp's location
request on a real phone; a label printed on a real label printer.

## Done on 27 October: the books and shield group (38, 42, 48, 49, 50, 52, 75, 104, 107)

Checks after it:

| Check | Result |
|---|---|
| API tests | 647 passed, 94.4% line coverage |
| Lint, format, strict types, `alembic check` | clean |
| Web types and unit tests | clean, 29 passed |
| Browser tests, desktop and phone | 50 passed |

What it delivered:

- Books: an append-only ledger (sales from settled payments and cash at the door, expenses,
  udhaar given and paid back), Undo by a mirror entry, expenses from bill photos, text, voice
  notes and the console's form, each saved only after the owner taps Save.
- Udhaar: one account per person, a payback never more than what is owed, reminders at one,
  two and three weeks while the chat window is open.
- Reports: today's and the week's cash card on the owner's WhatsApp in the evening (cron,
  once a day), and the same answers when the owner asks; the console's day book.
- Stock: one alert when a sale takes a variant down to its limit, re-armed when it is back up.
- Staff: five roles with rights checked in the API and in the owner's chat; team management
  with sign-in links; the activity log after a full chain check.
- Autonomy and approvals: five actions with their levels; questions to the owner on WhatsApp
  and in the console, asked once and answered once.
- Verified buyers by rule or by the owner; credit (khata) orders for them at shops that give
  credit.
- The why panel for every order.
- Migration 0008: ledger entries (append-only), udhaar accounts, approvals, each product's
  low-stock limit, each variant's alert flag, and who verified a buyer.

Not verified yet: bill photos and owner commands read by the real model (no keys yet); the
evening card on a real phone.

## Done on 27 October: restock, owner panel, batches and expiry (117, 118, 119, 120)

Checks after it:

| Check | Result |
|---|---|
| API tests | 692 passed, 94.3% coverage (lines and branches) |
| Lint, format, strict types, `alembic check` | clean |
| Web types and unit tests | clean, 29 passed |
| Browser tests, desktop and phone | 52 passed |

What it delivered:

- Restock (117): stock that came in by chat, voice note, a supplier's bill or challan photo,
  a CSV or Excel sheet, or the console's form. The model reads; code matches every line to
  one product and variant and shows before and after; nothing changes until Confirm. Each
  line becomes a batch, and the purchase goes in the books with its bill photo.
- The owner's panel (118): "products" and "orders" lists in the owner's chat, a product's
  price, stock and alert limit with buttons, an order's next moves, and a product form as a
  WhatsApp Flow once it is published. Alerts (low stock with Reorder and Snooze, expiry,
  price questions) go to WhatsApp in the 24-hour window, as the alert template outside it,
  and wait in the console's Alerts otherwise.
- Batches (119): first in, first out, or first to expire first; the exact cost of every
  unit sold kept on the order line; returns back to their batch; the price question when new
  stock costs differently; goods profit on the daily card, in the day book and per product.
- Expiry (120): one warning per batch, expired stock held out of sale, discards written off
  as a loss.
- The `stock` role: receives stock and keeps batches and expiry dates; it sees costs but not
  profit or the books.
- Migration 0009: stock batches, batch allocations (append-only), owner notices, and a
  snooze time on variants.

Not verified yet: bill photos, the owner's words and voice notes read by the real model (no
keys yet); the Flow and the alert template on a real WhatsApp number (both need Ahsaan's
WhatsApp Manager).

## Where the model keys go (when they arrive)

Never in the chat. Each key goes in one or more of these places, typed by Ahsaan:

| Key | GitHub: Settings > Secrets and variables > Actions > New repository secret | Render: the service > Environment | Local `api/.env` |
|---|---|---|---|
| `BASETEN_API_KEY` | yes (live evaluation workflow) | yes (the app) | optional |
| `FIREWORKS_API_KEY` | yes | yes | optional |
| `MODAL_GLIMMER_BASE_URL`, `MODAL_GLIMMER_API_KEY` | yes | yes | optional |

Then: Actions > "Live model evaluation" > Run workflow. Its results file is attached to the
run; it is committed after a look, and languages that pass leave beta.

## Next: Phase 1 (Build features, by 2 November)

In the timeline's order:

1. 28 Oct: catalog and order states: 1, 4, 83, 86. Done on 27 October.
2. 29 Oct: order agent and guards: 9, 10, 11, 12, 89, 90, 93, 94, 105, 106, 109 (demo login).
   Done on 27 October.
3. 30-31 Oct: payments: 23, 24, 25, 26, 27, 30, 97, 98, 99. Done on 27 October.
4. 31 Oct: delivery and COD: 33, 34, 101. Done on 27 October.
5. 1 Nov: books, stock and shield: 38, 42, 48, 49, 50, 52, 75, 104, 107. Done on 27 October.
6. 1-2 Nov: restock, owner panel, batches and expiry: 117, 118, 119, 120. Done on 27
   October, console views included (sheet upload, alerts, batches, expiry, profit).
7. 2 Nov: dashboard, eval set, cost meter, reasoning routing, deploy: 74, 80, 111, 112, 113.

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
   unverified to verified (until then every SMS match waits for the owner's Approve), and the
   screenshots become recorded readings for the payment tests.
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
- Owner sessions are signed tokens (7 days) that travel in the link's fragment
  (`/owner#t=…`), so they never reach a server log; the console moves them to the browser's
  storage and out of the address bar.
- A Latin-script message that could be English or Roman Urdu is answered in Roman Urdu for a
  Pakistani shop, for owners too ("Lawn suit 4850" gets "Dukaan mein daal doon?").
- The catalog lists newest first; demo products are seeded last first, so each template's
  first product leads. A demo shop seeded before it had products gets them on the next seed.
- Web-made shops are kept 7 days and demo copies 3 days; WhatsApp-made shops are kept.
- Customers get the order agent; with no model available (production without keys) their
  chat goes to the owner rather than a dead reply.
- The language evaluation still runs the tool-less shop assistant; it moves onto the order
  agent with the evaluation set (111), which will need a database in the live workflow.
- Demo products' lowest prices live in the template files, which are public like the repo;
  real shops' lowest prices are only in the database and never reach a customer.
- An owner's language is their own setting, else the shop's (Roman Urdu in Pakistan): the
  handover summary and notices use it.
- Payments settle only on a verified parser's matching SMS or the owner's Approve; a
  screenshot alone never settles anything. A shop without SMS forwarding sends every
  screenshot straight to the owner.
- The unique amount is taken off the order's total (the shop gives up a few rupees, at most
  49) and only until the first money arrives; after a part payment the rest is asked as it is.
- The COD advance is the shop's own `cod_advance` (minor units) if set, else the delivery
  charge, else a tenth of the order (rounded up to 50, at least 100).
- Owner payment buttons carry the claim id (`pay:<id>:ok|no`); the claim's own status is the
  state, so a second tap or a console decision cannot settle twice.
- Test screenshots are plain coloured pictures with recorded readings; no image that looks
  like a bank or wallet receipt is ever generated.
- An address is too vague for a rider when it has no area, or neither a house or street
  number nor a landmark. Then checkout waits for a location pin.
- A high COD risk (60 or more) never blocks a buyer. It asks for an advance only where the shop
  also takes transfers, and the owner can switch that off (the `risk_advance` autonomy level).
- The parcel page shows the shop, the order code, its steps and what to pay at the door, never
  the address or the phone number.
- The owner moves orders in the console for now; WhatsApp owner commands (75) answer
  questions about the shop and do not move orders yet.
- A low-stock alert fires when a sale or write-off reaches the limit, not for stock that
  starts low; the console and "low stock?" list those.
- Udhaar reminders default to "ask me": each one waits for the owner's Approve until the owner
  lets them go on their own.
- Staff sign in by link like the owner; staff rows carry a placeholder email
  (`<id>@staff.chatfront.invalid`) that is never used to send anything.
- A customer's verified badge set by the owner is never changed by the automatic rule.
- Expenses come from the owner's words or the bill; a bill photo without a readable total and
  without a typed amount waits for the owner to type it, never a guess.
- Goods profit (what goods sold for, less what those very pieces cost, less stock thrown
  away) sits beside cash profit; neither replaces the other.
- Stock counted before batches existed is "loose": the oldest stock, sold first, its cost
  unknown and counted apart rather than guessed. Demo products carry a synthetic cost
  (about 70% of the price) so their opening stock has a batch.
- Once any batch of a variant has an expiry date, that variant sells first to expire first,
  and loose stock sells last.
- A cancelled or returned unit goes back to the batch it came from at the cost it left at;
  units of a batch already held out as expired are held out again; a batch already discarded
  takes them back as loose stock.
- The price question is asked when new stock costs 2% or more away from the stock on hand.
  "After the old stock" is applied at the new batch's first sale; answered after that batch
  has started selling, it applies at once.
- Snooze on a low-stock alert is 3 days; Remind later on an expiry warning is 2 days; the
  expiry warning period is 7 days unless the shop sets its own (`expiry_warning_days`).
- The morning stock job runs once a day per shop after 8 am shop time (hourly cron, deduped).
- A reorder never messages a supplier: the owner gets the draft and a wa.me link that opens
  their own WhatsApp, when the supplier's number is on a bill.
- "Maal" is not a purchase word for photos ("naya maal" also means a new design); "challan",
  "stock" and "supplier" are.
- A write-off cannot be undone from the books, since the stock stays thrown away.
- Sheets are read with openpyxl (MIT): the first sheet of an .xlsx, or a CSV; 2 MB and 500
  rows at most.
- Notices keep the text in the owner's language at the time they were made; the console shows
  that text as it is.

## Working notes for the next session

- Integration tests need PostgreSQL 16 with a non-superuser owner role; see README step 1.
  `TEST_DATABASE_URL` and `E2E_DATABASE_URL` point the tests elsewhere.
- If the installed Chromium does not match Playwright's version, set
  `PLAYWRIGHT_CHROMIUM_PATH` to its executable.
- Commits go out as Ahsaan Munir <ahsaanmunir53@gmail.com>, with no co-author line.
