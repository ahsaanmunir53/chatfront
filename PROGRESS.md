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

## Done on 27 October: dashboard, shop checkout, evaluation set, cost meter, routing (74, 80, 111, 112, 113)

Checks after it:

| Check | Result |
|---|---|
| API tests | 723 passed, 94.4% coverage (lines and branches) |
| Lint, format, strict types, `alembic check` | clean |
| Web types and unit tests | clean, 32 passed |
| Browser tests, desktop and phone | 56 passed |
| Order agent evaluation set, replayed (`python -m chatfront.eval.agent`) | 100/100 |
| The container's start command with production dependencies, `ENV=prod` | migrates, seeds, serves `/`, `/s/<shop>` and the API; `/health` says `no keys` |

What it delivered:

- Dashboard (74): the console opens on the shop at a glance for today, 7 or 30 days: orders
  and where they came from, money in and waiting, fraud, the books, orders per day, and the
  model's cost. Each part only for the roles allowed to see it.
- Shop checkout (80): a cart on every shop page and a checkout page that is also where
  Facebook and Instagram shop links land. Code alone prices, judges the address, assesses
  COD risk and places the order; the payment instructions go to the buyer's web chat.
- Evaluation set (111): 100 conversations in six languages with their expected tool calls and
  end state, replayed through the whole app in CI on every push (pass rate on the run's
  summary page; anything short of 100/100 fails the build). The live run is in the manual
  "Live model evaluation" workflow. Its first run found three bugs, now fixed: order codes
  like 2SQVTJ were read as Arabizi, "دوپٹہ 1800 میں دے دیں" as Punjabi, and the Punjabi
  "تہانوں دس دتا" (we will tell you) as the number ten.
- Cost meter (112): cost per order (the chat that won it and the calls about it), by job and
  by reasoning effort, in rupees.
- Routing (113): effort and token budget per job, recorded on every call.
- Migration 0010: `ai_calls.effort` and `orders.source`.
- `/health` now says `mock`, `live` or `no keys`, so a deploy without a model key shows it.

Not verified yet: the live evaluation (no key yet); a Facebook or Instagram shop sending a
real buyer to the checkout (needs Ahsaan's Commerce Manager catalog); the Docker image build
itself (this workspace cannot reach Docker Hub; the same start command was run with the
production dependencies instead, and Render builds the image on deploy).

## Done on 27 October: Meta channels (76, 77, 79, 6), the first Stretch group

Checks after it:

| Check | Result |
|---|---|
| API tests | 738 passed, 94.3% coverage (lines and branches) |
| Lint, format, strict types, `alembic check` | clean |
| Web types and unit tests | clean, 32 passed |
| Browser tests, desktop and phone | 58 passed |
| Order agent evaluation set, replayed | 100/100 |

What it delivered:

- Inbox (76): every customer chat from WhatsApp, Instagram, Messenger and the web chat in one
  list in the console, with who has it. A person takes a chat and the assistant stops there;
  the owner gives a chat to someone who answers chats; giving it back clears who had it. It
  replaces the waiting list.
- Comment-to-DM (77): "price?" under an Instagram post or reel or a Facebook Page post gets
  one private reply with the product, written by code in the commenter's language, then the
  order agent carries on in DMs. Posts whose caption names no product wait for the owner to
  choose it in the console's Comments panel.
- One customer (79): a wa.me link with a one-time code joins an Instagram, Messenger or web
  chat record to the customer's WhatsApp record, with chats, cart, orders, khata and
  verified status.
- Meta catalog (6): products to the shop's Commerce catalog every 30 minutes and on Send now,
  one item per variant by its catalog id; Meta's answer in the console; carts sent from the
  WhatsApp catalog go straight into the customer's cart.
- Migrations 0011 to 0014: identity links, who has a chat, posts and comments, the shop's
  Meta catalog.

Not verified yet: comments, private replies, catalog sends and WhatsApp catalog carts on real
Meta accounts (they need Ahsaan's Instagram, Page, WhatsApp number and Commerce Manager
catalog, and Meta wants the app live for comment webhooks); the agent offering the link
(needs a model key).

## Done on 28 October: payments and returns (28, 29, 100, 92)

Checks after it:

| Check | Result |
|---|---|
| API tests | 766 passed, 94.5% coverage (lines and branches) |
| Lint, format, strict types, `alembic check` | clean |
| Web types and unit tests | clean, 32 passed |
| Browser tests, desktop and phone | 58 passed |
| Order agent evaluation set, replayed | 100/100 |

What it delivered:

- Refunds (28): a refund becomes due on its own when a paid order is cancelled, a returned
  piece reaches the shop, or more arrives than the order costs. The owner is told what goes
  back and to whom, and closes it in the console's Money to send back with their transfer
  screenshot: code checks the amount, a completed transfer, signs of editing and the
  receiver's name against the payer. Record anyway closes it on the owner's word, and the
  record and the receipt say so. The customer gets a refund receipt signed with the shop's
  key; the books count money sent back against cash profit.
- "I sent too much" (29): only money settled from a wallet SMS or approved by the owner can
  be owed back. The order agent's `report_overpayment` tool answers from that; a claim with
  nothing behind it gets a polite no and a scam-check notice to the owner. A screenshot for
  more than was asked goes to the owner at once. "kya 5000 aaye?" lists the last two days of
  wallet and bank SMS with what each matched.
- Part payments (100): a screenshot for part of what is due tells the customer what is still
  to pay, and both halves land on one order with a receipt each; order status shows paid and
  still to pay.
- Swaps and returns (92): the agent's `ask_return` tool, checked by code (delivered, inside
  the window, a photo for a damaged or wrong piece, what was bought, the new size in stock);
  Approve and Reject; a swap becomes a cash-on-delivery order at the price paid; a return
  label for the reverse pickup; Parcel received puts the stock back and makes the refund due.
- Migration 0015: return requests, refunds, and receipts that belong to a payment or a
  refund.

Not verified yet: reading refund screenshots and the agent's two new tools on the real model
(they need a model key); a real reverse pickup with a courier.

## Done on 28 October: selling more (13, 16, 19, 95, 91)

Checks after it:

| Check | Result |
|---|---|
| API tests | 795 passed, 94.75% coverage (lines and branches) |
| Lint, format, strict types, `alembic check` | clean |
| Web types and unit tests | clean, 32 passed |
| Browser tests, desktop and phone | 60 passed |
| Order agent evaluation set, replayed | 100/100 |

What it delivered:

- Bundles and upsell (13): bundles set in the console (two to four products on sale, a
  saving less than the set costs). When a piece goes in the cart the assistant is told what
  goes well with it: its bundle partners with the saving, then what customers bought with it
  in the last 120 days, two at most.
- Codes and points (19): discount codes in the console with a percent or an amount, smallest
  order, first and last day, most uses, once per customer and whether the shop page shows
  them; checked by code with a reason when one does not apply, at the shop page checkout and
  in chat. Loyalty points earned on delivery, spent in chat up to a share of the order.
- Pre-order and waitlist (16): the owner puts a product on pre-order in words with a ship
  date and a limit; the shop page and the assistant say when it ships; the waitlist messages
  each waiting customer when stock comes back.
- Big-day countdown (95): a calendar of big days per country and the shop's own days; the
  last day to order works out from the ride to the customer's city; the shop page counts
  down to it and to the end of each sale shown on the page; the order card says whether the
  order arrives in time.
- Approved answers (91): the owner's reply in a handed-over chat becomes an answer the
  assistant may reuse only after the owner approves it; removable in the console.
- Migration 0016: bundles, coupons, coupon uses, loyalty entries, waitlist entries, saved
  answers; offer columns on orders; pre-order dates and limits on products.

Decisions:

- Offers apply only to lines at the catalog price; a bargained line keeps its agreed price
  and gets no bundle or code. Order of work: bundles (the biggest saving first, a piece in one
  set only), then one code on what is left at the catalog price, then points, capped at the
  shop's share (20% by default). An exchange order gets no offers.
- Points are earned when an order is delivered, so a cancelled order never earns them; a
  cancelled order gives spent points back, a returned one also takes earned points back. A
  cancelled order frees its code use.
- A pre-ordered line takes stock when the order is packed. The pre-order limit counts placed
  orders not yet packed (carts hold nothing); an order ships from its latest ship date.
- Waitlist messages go only inside the 24-hour window; outside it the entry is marked missed,
  since no approved template covers it yet.
- Calendar dates that follow the moon (Eid, Ramadan) are marked expected and the page says
  so. Last day to order = the day, less one day to arrive early, the ride (one day in the
  shop's city, three elsewhere, or the shop's own delivery days) and one day to dispatch.
  The page shows a countdown from three weeks out.
- Approved answers need the settings right, as they speak for the shop; the agent reads the
  newest 20.
- The demo clothing shop comes with one bundle, the WELCOME10 code shown on its page, and
  points on.

Not verified yet: the suggestions, codes, points, waitlist and approved answers in chat on
the real model (they need a model key); waitlist messages on a real WhatsApp number.

## Done on 28 October: the catalog from what the shop already has (84, 85, 2, 5, 87, 88)

Checks after it:

| Check | Result |
|---|---|
| API tests | 833 passed, 94.76% coverage (lines and branches) |
| Lint, format, strict types, `alembic check` | clean |
| Web types and unit tests | clean, 32 passed |
| Browser tests, desktop and phone | 66 passed |
| Order agent evaluation set, replayed | 100/100 |

What it delivered:

- One way in for what a shop already sells: a sheet, its Meta catalog, its Instagram posts
  or a video of a rack become drafts in the console's "Bring in your products". Nothing is on
  sale until the owner adds a draft; then code builds the product, checked against the
  template like any other, and translates it.
- Sheets and the Meta catalog (85): the shop's own Excel or CSV, or Commerce Manager's export
  of the catalog its WhatsApp Business catalog uses. The model matches the columns; without
  one, code matches the headers. Rows of one product become its colours or sizes. A
  connected Meta catalog is read with the Graph API, and the added products replace its old
  items at the next send.
- Instagram (84): the last 30 posts and reels, read for one product each from the picture
  and caption; code reads captions when no model can. An added post then answers comments
  with its product (feature 77).
- A video of a rack (2): up to 30 products, the same design in other colours put together,
  each with a still taken from the video by ffmpeg.
- Shelf count (87): a photo with "stock count" in the owner's chat or the console; the count
  is shown next to the books and becomes the stock only on Confirm.
- Size charts (5): kept in the console, shown on the shop page, and used by the
  `size_advice` tool, which decides the size by code.
- Video search (88): the website chat takes videos; a clip or a shared reel goes to the
  agent as a video, and the shop's own reel names its product by code.
- Migration 0017: imports and their drafts. CI installs ffmpeg for the video tests.

Decisions:

- A draft keeps where its price came from. Prices the owner wrote (a sheet, the catalog, a
  caption) can be added in one go; a price read from a picture, heard in a video or read from
  a tag is shown for the owner to check, and "Add the ones with your prices" leaves it, and
  any draft that looks like a product already in the shop.
- Photos are fetched only from Meta's own servers (fbcdn.net, cdninstagram.com, fbsbx.com)
  over HTTPS. Image links in a sheet are not fetched; the owner adds photos to those later.
- The same Instagram post, catalog group or video moment is never drafted twice; one import
  of each kind reads at a time, and a run still reading after 20 minutes shows as timed out.
- A count above the books comes in as loose stock with no known cost; missing pieces come
  off the batches in selling order and their cost is written off in the books.
- Size advice: the smallest size whose every range holds the measures; else the smallest
  that is tight nowhere, saying where it is loose; else none, saying where it is tight. The
  clothing template carries a usual chart the owner can start from; the demo shop has it.
- `prompt_context` moved to catalog.drafts and the translate job's name to
  catalog.translations, so the importers do not depend on the owner's assistant.

Not verified yet: reading pictures, videos and shelves on the real model (they need a model
key); a real Instagram account's posts and a real Meta catalog.

## Done on 28 October: the shield (37, 40, 43, 45, 46)

Checks after it:

| Check | Result |
|---|---|
| API tests | 889 passed, 94.86% coverage (lines and branches) |
| Lint, format, strict types, `alembic check` | clean |
| Web types and unit tests | clean, 32 passed |
| Browser tests, desktop and phone | 66 passed |
| Order agent evaluation set, replayed | 100/100 |

What it delivered:

- Fraud-buyer list (37): Report fraud on a customer in the console, with a reason and a note;
  code adds the evidence (their orders, rejected payment screenshots and their signals). It
  counts at that shop at once: 60 risk points, so cash on delivery asks for an advance.
  Chatfront's own staff confirm or reject it in the platform shop's console (signed in with
  `python -m chatfront.owner.sign_in --shop chatfront`). A confirmed report puts only the
  number's keyed hash on the shared list, which every shop's risk score reads: 40 points for
  one shop, 50 for two, 60 for three or more. A shop can take its report back, and a reviewer
  can take a number off the list.
- Fake support links and code requests (40): every link in a customer's message is checked by
  code, and by Google Safe Browsing when `SAFE_BROWSING_API_KEY` is set. A fake link, a
  request for a code, or a support claim with a link that is not the brand's own never
  reaches the assistant: the sender hears that the shop never asks for codes or sends login
  links, and the owner gets a warning. Links the owner sends their own assistant come back
  with a verdict each.
- Privacy guard (43): customer photos are checked for ID and bank cards before anything else
  (a new prompt, `private_photo`); a card is kept only as a blurred copy and the model never
  sees it. CNIC and card numbers typed in chats are masked by code before they are stored.
- Trust page numbers (45) and reviews (46): the delivered message carries a review link
  signed for that order; the assistant can take the stars in chat too. Reviews show on the
  shop page and trust page; the owner replies once in the console and gets a notice.
- Migration 0018: fraud reports, the shared list (readable by every shop, written only by
  Chatfront's review), reviews, and which photos were cards.

Decisions:

- The list holds hashes made with the platform's `PHONE_HASH_KEY`, the same key every shop's
  numbers are hashed with, so a match works across shops and nobody can read a number back.
  Reports, notes and evidence stay under the reporting shop's row-level security; reviewers
  read them through the system role only.
- A report never blocks a buyer and never refuses an order: it raises the risk score, which
  asks for an advance, as the rules say (code decides; the owner can still verify a buyer).
- One review per delivered order, from the signed link or the buyer's own chat; the owner
  cannot change or remove it, only reply once. Numbers, links and card numbers are taken out.
- A trust figure waits for five orders, replies or reviews behind it.
- A bare mention of an OTP is not an alarm; asking for one, or telling of someone who did, is.
- Without a model key, photos are kept as sent (nothing can check them); typed numbers are
  masked without one.

Not verified yet: the photo check on the real model (needs a key); Safe Browsing with a real
key (Google allows the free API for non-commercial use).

## Done on 28 October: packing and books (102, 103, 51)

Checks after it:

| Check | Result |
|---|---|
| API tests | 922 passed, 94.86% coverage (lines and branches) |
| Lint, format, strict types, `alembic check` | clean |
| Web types and unit tests | clean, 32 passed |
| Browser tests, desktop and phone | 66 passed |
| Order agent evaluation set, replayed | 100/100 |

What it delivered:

- Pick list and packing slips (103): the pieces to pick across every order waiting to be
  packed, per product and colour or size, with the orders each is for. Orders with a
  pre-order wait, listed apart, until the ship day; on the day, a line short of stock says how
  many are still to come in. In the console under Orders, printed as a PDF with one slip per
  order; in the chat on "pick list"; and to the packers each morning at 9 (to the owner when
  the shop has none).
- Packing check (102): a photo of the packed order, in the chat with the order's code and
  "pack", or from the order in the console. Spark Open counts each line against the products'
  own photos (a new prompt, `packing_check`); code decides match, mismatch or unclear. A match
  gets Mark packed; a packer's unclear check goes to the owner with Approve and Reject.
- True profit per product (51): courier charges per parcel, out and back, entered at dispatch
  or later under the order. Profit by product takes in the delivery charges buyers paid, the
  courier's charges and what returned parcels cost, split over each order's products by line
  value. Asked in chat too ("asal munafa").
- Migration 0019: packing checks, and the two courier charges on each parcel.
- Fixed in passing: in a label or slip, a line after one in Urdu, Hindi or Bengali script
  printed blank, because the fallback font stayed set on the page. Every line now names its
  own font again (a regression test checks it).

Decisions:

- Courier charges are what a parcel cost, kept on the parcel, not cash in the books: the owner
  may still write the courier's weekly bill as an expense, and nothing is counted twice.
- A parcel with no courier charge is counted apart and its delivery charge is left out, so
  true profit is never flattered by a missing number.
- Nothing is packed by a model: a match only offers Mark packed; only a person taps it.
- With no model key the packing check says so instead of guessing, and the slip is ticked by
  hand. There is no recorded answer for it in the mock, so the demo does not show a made-up
  check.
- The morning list goes to people with the packing role; the owner gets it only when no packer
  could be reached, so the owner's chat does not fill up.

Not verified yet: the packing check on the real model (needs a model key).

## Done on 28 October: data and growth (57, 59, 116, 66)

Checks after it:

| Check | Result |
|---|---|
| API tests | 962 passed, 94.78% coverage (lines and branches) |
| Lint, format, strict types, `alembic check` | clean |
| Web types and unit tests | clean, 32 passed |
| Browser tests, desktop and phone | 70 passed |
| Order agent evaluation set, replayed | 100/100 |

What it delivered:

- Ask your data (57): the owner or staff ask the shop's records in the chat ("sab se zyada kya
  bika pichle mahine?", "aaj kitni sale hui?", "order kahan se aaye?"). The model only fills a
  fixed menu (what to count, split by what, over which days); code runs the query inside the
  shop's rows and writes every number. Common questions in English and Roman Urdu are read by
  code with no model, so it works today. Profit needs the books right; the rest needs orders.
- Customers (59): what each buyer spent on orders they kept, their last order and a group
  (new, came back, loyal, gone quiet), with a filter and a summary above the list. A greeting
  the owner writes (it starts from the next big day's, such as Eid) goes only to the buyers
  whose chat is open, at most once a week each; for the rest the console gives a link that
  opens the owner's own WhatsApp with the greeting written.
- Share cards (116): a share link for every product and a Share button on the shop page. The
  link's page carries Open Graph and X tags pointing at a 1200 by 630 card drawn by code
  (photo, name, price, shop; right to left for Urdu and Arabic) and sends people on to the
  product on the shop page, or to the shop page when the product has been taken down.
- MCP endpoint (66): `/mcp/<shop>` with five read-only tools (shop details, search, a product,
  pricing a cart, starting a checkout). start_checkout returns a checkout link with the cart
  filled in; the person places the order and the shop sees it came from an AI assistant. The
  trust page shows the address.
- Migration 0020: when each customer was last greeted.
- The customers list moved from the shield's routes to its own module (`crm/`).
- Copy buttons now say "Copied" only when the browser really copied (one helper for all four).

Decisions:

- No marketing messages: greetings go only inside the 24-hour window, and never as templates.
  Everything else is the owner's own WhatsApp, sent by the owner.
- An order counts in answers once it is paid or confirmed; one still waiting for its advance
  does not.
- The MCP tools only read. An assistant cannot place an order, change a cart in the shop or
  see any buyer's details; the person finishes on the shop's own checkout page.
- The share card is drawn by code, not by a model, so it needs no key and costs nothing.

Not verified yet: free-worded questions on the real model (needs a model key); how WhatsApp
and X show the card for a deployed link (needs the deploy).

## Done on 28 October: demo engineering (110, 114, 115)

Checks after it:

| Check | Result |
|---|---|
| API tests | 994 passed, 94.84% coverage (lines and branches) |
| Lint, format, strict types, `alembic check` | clean |
| Web types and unit tests | clean, 33 passed |
| Browser tests, desktop and phone | 72 passed |
| Order agent evaluation set, replayed | 100/100 |
| Busy day from the command line, 50 customers, each demo shop with recorded chats | none stuck |

What it delivered:

- Customer simulator (110): a busy day in a demo copy. 10, 20 or 50 invented customers in
  Urdu and Roman Urdu, each with a mood, a goal and their own number, chat with the shop at
  once through the same path a real message takes. With keys Glimmer plays them (a new prompt,
  `customer_sim`) and Spark Open answers; without, they replay the evaluation set's Urdu and
  Roman Urdu conversations with recorded answers scoped to that shop. The console shows them
  arrive and how each chat ended. `python -m chatfront.sim` runs one with a report.
- Failover (114): Baseten, then Modal (a Spark Open deployment, optional), then Fireworks; an
  endpoint that fails twice rests for a minute; an outage drill for the demo; the dashboard
  says which endpoint answered. `deploy/modal_glimmer.py` starts Glimmer on one H100 from the
  FP8 weights, scaling to zero after five idle minutes.
- Push alerts (115): Web Push written on the RFCs (encryption checked against RFC 8291's own
  example), VAPID key made with `python -m chatfront.push`; whatever the shop tells a staff
  member also arrives on their phones. The Urdu right-to-left dashboard was checked on a
  phone screen.
- Migrations 0021 (busy days) and 0022 (push subscriptions).
- Refactors on the way: the scripted conversations moved to `eval/cases.py`, the web chat's
  accept step to `channels/webchat.py` and the checkout's routes to
  `orders/checkout_routes.py`, so the simulator can use them without an import loop. The mock
  model now prefers the longest matching words, and keeps only its latest 500 requests in
  memory (a long-running demo kept every one).

Decisions:

- Simulated customers only ever go to a visitor's demo copy, never a real shop, and never send
  a payment screenshot: one who pays by transfer only says so.
- A replayed customer whose card never comes (a product sold out since the recording) leaves
  after a few looks; only a chat the shop never answered counts as stuck.
- Push uses no outside service and no extra library; the VAPID key is Ahsaan's to make, like
  the other secrets.
- Spark Open on Modal needs a node of eight large GPUs, beyond the $100 credit as a standing
  endpoint, so it is an optional link in the chain; Modal's credit goes to Glimmer.

Not verified yet: Glimmer playing customers and the failover on real endpoints (need keys);
`deploy/modal_glimmer.py` on Modal (needs Ahsaan's Modal account); an alert on a real phone
(needs the deployed HTTPS site).

## Done on 28 October: selling later (14, 15, 20, 21, 22, 96, 108)

The first of the Later groups, built at Ahsaan's request to finish all 122.

Checks after it:

| Check | Result |
|---|---|
| API tests | 1,032 passed; coverage above the 85% bar in CI |
| Lint, format, strict types, `alembic check` | clean |
| Web types and unit tests | clean, 36 passed |
| Browser tests, desktop and phone | 76 passed |
| Order agent evaluation set, replayed | 100/100 |

What it delivered:

- Consent log (108): every customer is asked once, after their first order, whether they want
  offers; START and STOP (and "offers band" and the Urdu and Hindi words) work at any time and
  are read by code before the model. The owner's yes or no to sharing fraud reports and the
  sales history is kept the same way. Every answer is a new row with the words it answered.
- Cart reminders (14): once, an hour after the customer went quiet and while the free window
  has hours left; never after STOP; counted in the console with the carts that became orders.
- Broadcasts (15): to the customers interested in a product or a kind of product who said yes,
  at most one a week each; the offers template for closed WhatsApp chats when
  `WHATSAPP_OFFER_TEMPLATE` is set; the orders each broadcast brought in the next week.
- Referrals (20): a customer's own code for a friend's first order, sent by the agent with a
  shop link that puts it in at checkout; points or a one-use code for the customer when the
  friend's order is delivered.
- Gifts (21): the recipient's address, a message and who it is from, paid by transfer, no
  price in the parcel, and an A6 message card printed from the console.
- Group orders (22): the organiser's part carries the address and the delivery charge; friends
  add and pay for their own parts from a link; the group page shows first names only; the
  parts are held off the pick list until the group closes.
- Money from abroad (96): the receipt's currency is read and converted at the owner's own rate
  with 3% either way; such a payment always goes to the owner.
- Migration 0023. A unit test now fails when the code names a string key the catalogs lack.

Decisions:

- A coupon that took nothing off a placed order is not kept on it, so a code used where it
  did not apply is not counted as used.
- The offers question comes after the order's own message, once per customer, and never on a
  second order.
- A group part and a gift are paid by transfer; cash at the door does not fit either.

## Done on 28 October: posting and sales (3, 7, 8, 78, 81, 82)

Checks after it:

| Check | Result |
|---|---|
| API tests | 1,064 passed |
| Lint, format, strict types, `alembic check` | clean |
| Web types and unit tests | clean, 36 passed |
| Browser tests, desktop and phone | 78 passed |
| Order agent evaluation set, replayed | 100/100 |

What it delivered:

- Flash and live sales (8): a sale price below the catalog's for a while, pieces to whoever
  takes them first, held in their cart for the sale's hold time; a placed order keeps its
  piece, a cancelled one gives it back, a hold that runs out leaves the cart and the customer
  hears. In a live sale a number written in a chat or a comment on the Instagram live takes a
  piece. The shop page shows the sale price, the pieces left and the time left.
- Pictures (3): the owner's photo cleaned by code, used only if the owner chooses it; cards in
  the shop's colour for the catalog, an Instagram post and a Status. Muse Image and SAM stay
  Model API only, behind the same interface.
- Posts (7, 82): a morning post of new stock, or one product, with pictures and Urdu and English
  captions (the model's words, code's prices and link); nothing goes out until the owner ticks
  where; Instagram and the Facebook Page through the Graph API, the WhatsApp catalog through the
  catalog sync, the Status picture to share by hand.
- Leads (78, 81): story replies, mentions and ads kept with where they came from; a mention is
  thanked and the owner told; an order is credited to the customer's latest lead of the week,
  and the console shows what each ad and link brought.
- Migration 0024.

Decisions:

- A live sale's claim is read before the model: a message that is only a number takes a piece
  while a live sale is on. A comment cannot carry buttons, so a comment takes the first colour
  or size in stock, which the customer can change in the chat.
- A comment's author is taken as the customer it names: with Instagram Login the id in a
  comment is the one their messages come from.
- A caption from the model with any number in it is not used; the shop's plain caption is.
- A sale price must be below the product's price; code never puts a price up.

## Done on 28 October: bookings, custom orders and couriers (17, 18, 31, 32, 35, 36)

Checks after it:

| Check | Result |
|---|---|
| API tests | 1,120 passed |
| Lint, format, strict types, `alembic check` | clean |
| Web types and unit tests | clean, 38 passed |
| Browser tests, desktop and phone | 80 passed |
| Order agent evaluation set, replayed | 100/100 |

What it delivered:

- Bookings (18): a service's length comes from its Duration field; the owner sets each day's
  hours and how many customers are served at once; code finds the free times and sends them
  as a list to tap (or, with `WHATSAPP_FLOW_BOOKING_ID`, as a WhatsApp Flow form). A tapped
  time is checked again under a lock; a reminder goes once, three hours before; the owner
  marks a booking done or missed, or cancels it and the customer hears.
- Custom orders (17): the agent takes down the words, colours, size and day; code draws a plain
  sketch (Muse Image stays Model API only); the owner sets the price; the customer accepts or
  declines it with a button, and an accepted price goes into the cart as its own line.
- Couriers (31, 32): the shop's own PostEx token, kept sealed; a packed parcel booked from the
  order; hourly tracking that moves an order only on delivered or returned; PostEx's payment
  status kept as a settlement. Any courier's payment sheet (CSV or Excel) is matched to the
  orders: short payments, unknown rows and rows paid before stand out.
- Proof of delivery (35): the rider's photo read for the order's code or tracking number; it
  never moves the order.
- Unboxing video (36): watched beside the catalog and packing photos for a damaged or wrong
  item; code turns the reading into advice for the owner, who still decides.
- What the rider was to collect now stays on the parcel after delivery, so a later charge or
  settlement is checked against it (it was being reset to zero).
- Migration 0025.

Decisions:

- A booking moves no money and no stock; it is paid at the shop at the catalog price.
- Times are offered every 30 minutes, from an hour after now, for the next 30 days.
- A custom line stands on a hidden archived product per kind, so it never shows in the catalog
  and the customer cannot change its quantity; the price is the owner's alone.
- A parcel is booked with a courier only once it is packed.
- PostEx is the only courier with an API adapter; it is built to its published merchant API and
  marked `needs-key` until a shop's real token has been tried.
- A delivery photo and an unboxing video are advice; delivered stays the owner's or the
  courier's word, since it posts the cash.

## Done on 28 October: privacy, scams, the books ahead and the coach (39, 41, 44, 47, 55, 58, 60, 62, 63, 64, 65)

Checks after it:

| Check | Result |
|---|---|
| API tests | 1,170 passed |
| Lint, format, strict types, `alembic check` | clean |
| Web types and unit tests | clean, 38 passed |
| Browser tests, desktop and phone | 82 passed |
| Order agent evaluation set, replayed | 100/100 |

What it delivered:

- Is it really the shop (41): the trust page checks any number or account pasted into it
  against the shop's channels connected through Meta's APIs.
- A customer's own data (44): a copy by a one-day link from the chat; erasing asks the owner,
  then removes the person and keeps the amounts in the books. The console can do both.
- Call guardian (39): a forwarded voice note or an uploaded recording heard by Spark Open; code
  decides scam, suspicious or no signs, and the owner can report the numbers given.
- STIX report (47): scammers' numbers, accounts and links (and fake links caught in customers'
  messages) wait for Chatfront's review; confirmed ones download as a STIX 2.1 bundle.
- Cash-flow forecast (58), staff pay (60), the tax year with zakat (62), the workbook for tax
  filing (63) and the signed credit passport with its public check page (64).
- Seasons ahead (55) from a calendar file and last year's sales, and the coach's Monday note
  (65): code's lines with every number, the model's advice in words only.
- Migration 0026.

Decisions:

- The cash forecast never guesses: averages and fixed costs come from the shop's own entries,
  and the cash starts from what the owner counted.
- Zakat is arithmetic on figures the owner checks; the page says schools differ.
- An erased customer is told before their channel ids are forgotten, so the message reaches
  them; erasing a khata that still has money owed keeps its name, as the shop's claim.
- A call recording that could not be heard says so; there is no "no signs" without a reading.
- Scam reports hold scammers' own details only; customers go through the hashed fraud list.
- The coach's model lines with a digit are dropped, as captions are.

## Done on 28 October: verified commerce, wallet formats, suppliers and prices (72, 73, 53, 56)

Checks after it: each feature's new unit and integration tests, and the suites they touch
(payments, restock, stock), pass locally; lint, format, strict types and `alembic check` are
clean; web types are clean. The full API suite with coverage, the browser tests (two new specs:
`formats.spec.ts`, `suppliers.spec.ts`) and the evaluation replay have not been run locally for
this group; CI runs all of them on the latest push.

What it delivered:

- Verified commerce standard (72): the written spec, JSON Schemas and test vectors in
  `docs/verified-commerce/`, each shop's key at `/api/shops/<slug>/keys`, the index at
  `/.well-known/verified-commerce`, and `python -m chatfront.verify` as the reference checker.
- Wallet SMS formats (73, partial): the console lists the wallets read for the shop's country;
  a shop that agrees keeps the shape of wallet messages no parser read (digits as 9, ids as A
  and 9, names as NAME) for Chatfront's staff, who see them next to each parser. No parser is
  verified until a real message from that wallet passes in `tests/fixtures/sms`.
- Suppliers (53): each supplier's latest unit cost for the same item, one reorder message per
  supplier for the low items, and the low-stock Reorder naming who charged less.
- Prices elsewhere (56, Model API only): similar items at other Chatfront shops in the same
  currency, as a count and a spread, never which shops; labelled as not the whole market.
- Migration 0027.

## Where the model keys go (when they arrive)

Never in the chat. Each key goes in one or more of these places, typed by Ahsaan:

| Key | GitHub: Settings > Secrets and variables > Actions > New repository secret | Render: the service > Environment | Local `api/.env` |
|---|---|---|---|
| `BASETEN_API_KEY` | yes (live evaluation workflow) | yes (the app) | optional |
| `FIREWORKS_API_KEY` | yes | yes | optional |
| `MODAL_GLIMMER_BASE_URL`, `MODAL_GLIMMER_API_KEY` | yes | yes | optional |
| `META_CATALOG_TOKEN` (feature 6, a system user token with catalog_management) | no | yes | optional |
| `SAFE_BROWSING_API_KEY` (feature 40, optional; Google Cloud API key for the Safe Browsing API) | no | yes | optional |
| `WHATSAPP_FLOW_BOOKING_ID` (feature 18, optional; the id WhatsApp Manager gives the published `bookings/flows/book.json`) | no | yes | optional |

A shop's PostEx token is not an env var: the owner types it in the console under Couriers and
cash collected, and it is stored sealed.

Then: Actions > "Live model evaluation" > Run workflow. It runs the language set and the
order agent's 100 conversations against the real model; each results file is attached to the
run and committed after a look. Languages that pass leave beta, and the agent's live pass rate
goes in the README. Until the keys come, `AI_MODE=mock` on Render makes the deployed demo answer
with recorded replies (they say so); with `auto` and no key, every chat goes to the owner.

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
7. 2 Nov: dashboard, eval set, cost meter, reasoning routing, deploy: 74, 80, 111, 112, 113. Done
   on 27 October; the deploy itself waits for Ahsaan's accounts (README, Deploy).

Phase 1 is built: of the 47 Build features, 23 are done, 21 wait only for a model key
(`needs-key`), and 3 are partial on things only Ahsaan can bring (24 and 122: real payment
samples; 121: live runs, voice notes and native review).

## Next: Phase 2 (Stretch, 34 features)

Proposed order, the Meta platform first since it is what is judged:

1. Meta channels: 76 (one inbox with staff assign), 77 (comment-to-DM), 79 (one customer
   across channels), 6 (WhatsApp catalog and Instagram shop sync). Done on 27 October.
2. Payments and returns: 28, 29, 100, 92. Done on 28 October.
3. Selling more: 13, 16, 19, 95, 91. Done on 28 October.
4. Catalog from what the shop already has: 84, 85, 2, 5, 87, 88. Done on 28 October.
5. Shield: 37, 40, 43, 45, 46. Done on 28 October.
6. Packing and books: 102, 103, 51. Done on 28 October.
7. Data and growth: 57, 59, 116, 66. Done on 28 October.
8. Demo engineering: 110, 114, 115. Done on 28 October.

## Phase 3 (Later, 41 features), at Ahsaan's request

In groups, each committed and pushed when its checks pass:

1. Selling later: 14, 15, 20, 21, 22, 96, 108. Done on 28 October.
2. Posting and sales: 3, 7, 8, 78, 81, 82. Done on 28 October.
3. Groups 3 and 4: 17, 18, 31, 32, 35, 36. Done on 28 October.
4. Groups 5 and 6: 39, 41, 44, 47, 55, 58, 60, 62, 63, 64, 65. Done on 28 October.
5. Groups 7 and 8: 53, 54, 56, 61, 67, 68, 69, 70, 71, 72, 73. 72, 73, 53 and 56 done on 28
   October; 54, 61, 67, 68, 69, 70 and 71 not started.

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
5. Commerce Manager catalog for the test shop (spec, Ahsaan's tasks), so a real Facebook or
   Instagram shop link can be tried against the checkout (80) and catalog sync (6) built.
6. Native speakers to read the Punjabi, Pashto, Sindhi, Hindi, Bengali and Arabic strings
   and eval lines, and to record the two voice notes per language (script in the eval data).

## Decisions taken

- A refund goes back to whoever the money came from: the payer on the wallet SMS, else on the
  screenshot the owner approved, else (cash at the door) the customer's name. It is closed
  only by the owner, with a screenshot code checks or on their word; Chatfront never sends
  money itself. A refund sent cannot be undone from the books.
- Paying the unique-amount rupee off anyway (Rs 5,000 for Rs 4,999) is a round price, not an
  overpayment: nothing is owed back and the screenshot is not flagged as "more than asked".
- An undelivered parcel the courier brings back (dispatched to returned) makes no refund by
  itself; the customer asks through the chat or the owner cancels and refunds.
- Return windows by business type: clothing and electronics 7 days, wholesale 3, grocery 2,
  food and services none; the owner's `return_days` overrides. A damaged or wrong item may
  always be reported within 2 days, with a photo sent in the last 48 hours.
- A swap goes out as a new cash-on-delivery order before the old piece is back, so the rider
  can swap at the door; its stock is held from approval. It is priced at what the customer
  paid for the old piece (a dearer size costs nothing more; a cheaper one leaves the
  difference owed back). The customer pays delivery for a change of mind and not for the
  shop's mistake. A swap whose size sold out between asking and approving becomes a return.
- A return refunds the pieces, not the delivery charge. A returned piece goes back into stock
  and its batch, damaged or not; the owner corrects the stock by chat for one that cannot be
  sold. Writing off a single returned piece as a loss is not built.

- A customer's records are joined only by a code sent from WhatsApp, never by a typed phone
  number. The WhatsApp record is kept. Two web chats from two browsers on the same shop stay
  apart, since their bargaining rounds are append-only. A second khata stays as a named
  account with no customer, so nothing owed is lost.
- In the inbox, anyone who answers chats takes a chat for themselves; giving a chat to someone
  else is the owner's (the team right). Taking or giving stops the assistant; a staff take
  does not notify the owner.
- Comment replies: only comments that ask about price, stock or ordering; one per person per
  post; 7 days; 750 an hour per account; the shop's own comments never. No public reply under
  the comment. A post with no product named waits for the owner rather than sending a vague
  message, since Meta allows only one private reply.
- The Meta catalog gets one item per variant, `CODE` or `CODE-n` like the shop page checkout;
  products without a photo are left out; the photo link is the API's own stable link that
  redirects to a fresh signed link.

- Reasoning effort is chosen per job, not per role: low for chat, the owner's assistant,
  translations and summaries; medium for reading products, bills and purchases; high for
  payment evidence; xhigh (16k tokens) for payment screenshot forensics.
- An order's model cost is every call made about it plus its chat's calls up to the moment it
  was placed (and after the chat's previous order). Costs are kept in thousandths of a paisa
  and shown in rupees; the mock costs nothing.
- Dashboard parts follow the staff rights: orders, payments (money and fraud), books, settings
  (the model's cost).
- The shop page checkout takes cash on delivery and transfer only: the khata needs a verified
  buyer, who is known in a chat.
- A Facebook or Instagram shop knows a variant by the product code (one variant) or the code
  and its place (`K7Q2M-2`), so ids stay readable and stable without a new column.
- The evaluation set's replay must pass every conversation; it checks the plumbing, and only
  the live run says how well the model chooses. Each conversation gets its own demo shop copy,
  deleted afterwards.
- Language detection ignores codes in capitals with a digit (order and product codes); "دیں",
  "کریں", "لیں" and "جائیں" count as Urdu; the Punjabi "دس" counts as ten only next to another
  number.

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
- `python -m chatfront.eval.agent` needs a migrated and seeded database at `DATABASE_URL`; a
  database of its own (CI uses `chatfront_eval`) keeps it apart from dev data. Each
  conversation's shop copy is deleted when it ends. The cases live in `cases.json`; a new
  one must pass the replay before it goes in.
- Every "Try as the owner" makes a demo copy; after many browser test runs `chatfront_e2e`
  reaches the copy limit and the owner tests fail. Drop and recreate that database.
