# Chatfront

Chatfront runs a small business inside WhatsApp, Instagram and Facebook: catalog, orders,
verified payments, delivery, books and fraud protection. Meta's models read photos, voice
notes and screenshots; tested rules make every decision that touches money, stock or a
customer's standing.

It starts with sellers in Pakistan who are paid by JazzCash, Easypaisa or bank transfer and
lose money to fake payment screenshots. Chatfront matches each screenshot against the credit
SMS from the seller's own phone, and sends a signed receipt anyone can check. The same idea
fits any market that pays by wallet transfer instead of WhatsApp Pay.

Built with Muse Spark Open.

Security hardening status and deployment requirements: [docs/SECURITY.md](docs/SECURITY.md).

## Try it

Every shop gets a page at `/s/<shop>` with a chat that runs the same agent as the shop's
WhatsApp number, plus buttons that open the shop on WhatsApp, Instagram and Messenger.
WhatsApp test numbers can only message their verified recipients, so the page chat is how
anyone outside the shop can try it. Six demo shops, one per business type, are created on
first start: open `/` and pick one. They and their customers are made up.

In a demo copy of a shop ("Try as the owner"), Busy day lets 10, 20 or 50 simulated
customers chat with it at once in Urdu and Roman Urdu, and the console shows them come in and
how each chat ended. With model keys Muse Glimmer plays them and Spark Open answers; without,
they replay recorded conversations. From the command line: `uv run python -m chatfront.sim`.

The web app is static, so it opens at once even when the API on Render's free tier is
asleep. It pings the API and shows a countdown while it wakes; anything typed in the chat
meanwhile is sent as soon as the API answers.

## Run it locally in ten steps

You need Python 3.12 with [uv](https://docs.astral.sh/uv/), Node 22 and PostgreSQL 16.

1. Create the owner role and database (the owner must not be a superuser, so row-level
   security is tested the way it runs on Neon):
   `psql -U postgres -c "CREATE ROLE chatfront_owner LOGIN PASSWORD 'owner' CREATEROLE" -c "CREATE DATABASE chatfront OWNER chatfront_owner"`
2. `cp .env.example api/.env` and check `DATABASE_URL`.
3. `cd api && uv sync`
4. `uv run alembic upgrade head`
5. `uv run python -m chatfront.seed`
6. `uv run uvicorn chatfront.main:create_app --factory --reload` (API and job worker on :8000)
7. In another terminal: `cd web && npm ci && npm run dev` (web app on :5173, proxied to :8000)
8. Open http://localhost:5173, pick a shop, open its chat and say salam.
9. With no model keys the replies come from recorded fixtures, one per Tier 1 language, and
   most start with `[mock reply]`.
   Add `BASETEN_API_KEY` to `api/.env` for real answers.
10. Tests: `cd api && uv run pytest --cov`, then `cd web && npm test && npm run e2e`.

## Local Glimmer privacy mode

For model inference on the shop's own PC, run an OpenAI-compatible Glimmer server and set
`GLIMMER_BASE_URL` to its `/v1` endpoint (for example,
`http://127.0.0.1:1234/v1` when the API runs on the same host). Set
`GLIMMER_API_KEY` only if that local server requires one, then set
`AI_PRIVACY_MODE=true`. In this mode every model role uses only the configured local Glimmer
endpoint: it never falls back to Modal, Baseten, Fireworks or Meta. If the local endpoint is
missing, production model calls fail closed. A Dockerized API may need a host-reachable address
such as `host.docker.internal` rather than `127.0.0.1`.

This localizes model inference, not the whole application: PostgreSQL, media storage, WhatsApp
and other connected services still need their own connectivity. Leave `AI_PRIVACY_MODE=false`
to retain the normal provider chains.

## Evaluation

| Set | What it checks | Replay (CI, every push) | Live (the real model) |
|---|---|---|---|
| Order agent, 100 conversations in six languages | the tool calls a good answer makes and the state the shop ends in | 100/100 | not run yet: needs a model key |
| Languages, 10 conversations per Tier 1 language (14), plus 2 voice notes each once recorded | the customer's language is recognised and answered in | 140/140 | not run yet: needs a model key |

The order agent's set (`api/src/chatfront/eval/data/agent/cases.json`) covers finding,
adding, shopping lists, bargaining, checkout with the tap on Place order, order status,
cancelling and changing orders, handover, prompt injection and the cart. Each conversation
runs in a fresh demo shop copy through the same path as a real message. The replay sends the
scripted tool calls in place of the model, so it checks the tools, cards, taps and the answer
and language checks, and CI fails on anything short of 100/100; only the live run says how
well the model chooses. Run them with `uv run python -m chatfront.eval.agent` (add `--live`
with a key) and `uv run python -m chatfront.eval.languages`, or from the "Live model
evaluation" workflow.

## Architecture

```
WhatsApp ─┐                      ┌─ catalog ─ orders ─ payments ─ delivery
Instagram ┼─ webhooks ─┐         │  books ─ shield ─ growth ─ future
Messenger ┘   (200 at once)      │  (switched on per tenant by tier)
Shop page chat ────────┼─ queue ─┤
SMS forwarder ─────────┘ (Postgres│
                     SKIP LOCKED) └─ ai/ (the only code that calls a model)
                                      Baseten → Fireworks: Muse Spark Open
                                      Modal: Muse Glimmer
```

One FastAPI service on Render's free tier runs the API, an in-process job worker and the
built web app. Render's free tier has no background workers, so cron-job.org calls
`/internal/cron` every few minutes; due periodic tasks become queued jobs.

- **Tenancy.** One PostgreSQL database. Every business table has `tenant_id` and a
  row-level security policy. Tenant work runs inside a transaction that does
  `SET LOCAL ROLE chatfront_app` and `SET LOCAL app.tenant_id`, so a query can only see one
  shop's rows. The app role cannot update or delete the audit log.
- **Models read, code decides.** `api/src/chatfront/ai/` holds roles (chat, vision, audio,
  video, watcher, simulator), the reasoning effort per role, failover, JSON-schema outputs
  validated with Pydantic, one tool registry, prompt files under version control and the
  cost meter. A tool proposes; its handler checks the tenant's rules before changing
  anything.
- **Channels.** One adapter interface for WhatsApp, Instagram, Messenger and the web chat.
  Every webhook checks `X-Hub-Signature-256`, provider message ids make handling idempotent,
  and a guard enforces the 24-hour window before any send.
- **Money** is an integer number of paisa everywhere. **Time** is stored in UTC and shown in
  the shop's zone. **Phone numbers** are stored as keyed hashes where the number is not needed;
  channel ids are stored sealed.
- **Business types** are JSON templates in `api/src/chatfront/templates/`, not code.
- **Languages.** `i18n/languages.json` lists 42 languages; Tier 1 (Urdu, Roman Urdu, English,
  Punjabi in Shahmukhi, Pashto, Sindhi, Hindi and Hinglish, Bengali and Banglish, Arabic with
  Gulf, Egyptian and Arabizi) is built and tested. Every customer message is detected for
  script and language, the reply is checked to be in that language, customers can switch
  mid-chat, and staff read and write in their own language. One ICU catalog per interface
  language serves the API and the web app, with right-to-left layouts and a Noto font per
  script. Numbers are read in any digits and in words ("dedh hazaar", "ألف ونص").
- **Country packs.** `i18n/countries/<CC>.json`: currency, money and number formats from
  CLDR, phones, time zone, tax label, languages and the wallets and banks people pay with.
  Pakistan is the default. Wallet SMS parsers live in `api/src/chatfront/payments/sms/`, and
  each stays unverified until it passes on a real message.

```
api/src/chatfront/
  core/       settings, db + RLS sessions, money, time, ids, phone hashing, errors;
              languages, detection, ICU messages, numbers, country packs
  ai/         provider layer, mock fixtures, prompts, tools, cost meter
  channels/   adapters, webhooks, try-it chat API, reply job
  jobs/       queue, worker, cron
  media/      Supabase and local storage, signed URLs, blurred copies
  audit/      hash-chained audit log
  modules/    module registry and tiers
  templates/  business-type JSON
  payments/   screenshot checks, SMS parsers and webhook, matching, receipts, watcher
  delivery/   addresses and pins, COD risk, parcels, shipping labels and their fonts
  insights/   questions about the shop's records, answered by code
  crm/        buyers' worth, groups and greetings
  mcp/        the MCP endpoint for outside AI assistants
  sim/        the customer simulator's busy day
  push/       alerts on staff phones (Web Push)
deploy/       Muse Glimmer on Modal
  eval/       evaluation sets: languages, and the order agent's 100 conversations
i18n/         language registry, catalogs, number words, detector hints, country packs
web/src/      React app: shop page with cart and checkout, try-it chat, start page, owner console
              (dashboard, inbox, comments, Meta catalog), trust page, receipt check, parcels
```

## Built during the hackathon

Everything in this repository was written from 27 October 2026, during the Meta Global AI
Developer Hackathon. The commit history shows the order.

- **28 October, posting and sales:** flash and live sales where the first to take a piece gets
  it, held in their cart for a set time, and a live sale's pieces claimed by writing a number in
  a chat or a comment; a morning post of new stock and one product put up on Instagram, the
  Facebook Page and the WhatsApp catalog, with pictures drawn from the owner's own photos and a
  Status picture to share by hand; story replies, mentions and ads kept as leads, with the
  orders each ad brought.
- **28 October, selling later:** a reminder about a cart left in a chat, once and inside the
  free 24-hour window; broadcasts to the customers who showed interest and said yes to offers,
  at most one a week, with the orders each brought back; "send to a friend" with a code good
  on a first order and a thank-you on delivery; gifts with a printed message card; group
  orders where friends add and pay for their own parts and it all ships together; gifts paid
  from abroad, converted at the owner's own rate and always checked by the owner; and the
  consent log behind all of it.
- **28 October, demo engineering:** a busy day of up to fifty simulated customers in Urdu
  and Roman Urdu against a demo copy of a shop, played by Muse Glimmer or replayed from the
  evaluation set; failover across Baseten, Modal and Fireworks with a minute's rest for an
  endpoint that keeps failing, an outage drill and the dashboard saying which endpoint
  answered; and alerts on staff phones through Web Push, built on the RFCs with no outside
  service, next to the right-to-left Urdu dashboard.
- **28 October, data and growth:** the owner asks the shop's records in the chat ("sab se
  zyada kya bika pichle mahine?") and code answers from the shop's own rows; the customers
  list with what each buyer spent, who came back and who went quiet, and a greeting (Eid, or
  a thank-you) sent only to the buyers whose chat is open; a share link for every product
  whose preview on WhatsApp or X is a card drawn by code; and an MCP endpoint per shop, so
  any AI assistant can look up products, price a cart and hand its person a checkout link.
- **28 October, packing and books:** the day's pick list, added up across orders waiting to
  be packed and printed with a packing slip per order, in the console, in the chat and to the
  packers each morning; a photo of the packed parcel checked against the order before it is
  sealed, with unclear checks going to the owner; and true profit per product after what the
  courier charged, out and back, and what came-back parcels cost.
- **28 October, shield:** a buyer reported by a shop counts against them there at once and,
  once Chatfront's own staff confirm it, at every shop, as a hash of the number that nobody
  can read back; fake WhatsApp and Meta support links and requests for a one-time code caught
  by code (and by Google Safe Browsing when its key is set) before the assistant answers,
  with a warning to the owner, who can also send any link to hear what it is; photos of ID
  and bank cards kept only blurred and card or CNIC numbers masked in chats; and reviews only
  from buyers whose order was delivered, with the shop's record (orders delivered, returns,
  first-reply time, rating) on its trust page.
- **28 October, the catalog from what the shop already has:** a sheet, the shop's Meta
  catalog, its past Instagram posts or a video of its rack turned into drafts the owner adds
  in the console; stock counted from a shelf photo and set once the owner confirms; size
  charts on the shop page and a size worked out by code from the customer's measurements;
  and a clip or a shared reel in the chat read as a video, the shop's own reel naming its
  product.
- **28 October, selling more:** bundles the owner sets in the console, suggested when a piece
  goes in the cart and taken off by code; discount codes with their own rules, typed at the
  shop page checkout or given in chat, and loyalty points earned on delivery and spent on
  later orders; pre-orders that take stock at packing and a waitlist that messages customers
  when stock comes back; a countdown on the shop page to the last day to order before Eid,
  Diwali or the shop's own big days, and to the end of a sale; and answers the owner gave in
  a handed-over chat, reused by the assistant only after the owner approves them.
- **28 October, payments and returns:** refunds owed on their own (a paid order cancelled, a
  piece returned, money beyond the order's cost) and closed with the owner's transfer
  screenshot, checked by code, or on the owner's word, with a signed refund receipt; "I sent
  too much, send it back" answered from what really reached the shop's wallets; part payments
  that ask for the balance and link both halves to one order; size and colour swaps and
  returns from the chat, inside the shop's return window, with a photo for a damaged piece,
  the owner's Approve and Reject, a return label for the reverse pickup, and the stock and
  the money coming back when the parcel does.
- **27 October, Meta channels:** one inbox for WhatsApp, Instagram, Messenger and web chats,
  where a person takes a chat or the owner gives it to someone; "price?" under an Instagram or
  Facebook post answered by a private reply with the product; one customer across channels,
  joined by a code sent from WhatsApp; the shop's products sent to its Meta catalog, and carts
  from the WhatsApp catalog taken straight into the customer's cart.
- **27 October, dashboard, shop checkout and the evaluation set:** the owner console opens on
  the shop at a glance (orders and where they came from, money in and waiting, fraud, the
  books, orders per day, the model's cost per order); a cart and checkout on every shop page,
  which is also where Facebook and Instagram shop links land, with no model in the way; the
  order agent's evaluation set of 100 conversations in six languages, replayed in CI on every
  push; reasoning effort chosen per job, deepest for payment forensics.
- **27 October, restock, batches and expiry:** stock that came in by chat, voice note, a
  supplier's bill photo, a CSV or Excel sheet or the console's form, always shown as before
  and after and saved on Confirm; every delivery kept as a batch with its exact cost, sold
  first in first out (or first to expire first), so each sale carries its true cost and
  goods profit sits beside cash profit; the owner deciding the price when new stock costs
  differently; expiry warnings, expired stock kept off sale and written off as a loss; the
  owner's panel in WhatsApp (product and order lists, buttons, a product form as a WhatsApp
  Flow) and alerts with Reorder and Snooze that use the approved template outside the
  24-hour window.
- **27 October, books and shield:** the shop's books kept by chat, voice note, bill photo or
  the console's form, with Undo by a mirror entry; the udhaar khata with reminders that grow
  firmer; today's and the week's cash profit as a WhatsApp card in the evening, and answers to
  "aaj ki sale?" and "pending payments?"; low-stock alerts; staff roles with their own sign-in
  links and the activity log checked end to end; what the assistant may do on its own, ask
  first or never do, with Approve and Reject for the owner; verified buyers who skip the COD
  advance and can buy on the khata; and a why panel under every order.
- **27 October, delivery:** addresses tidied for a rider, with a location pin asked for when
  they are too vague (WhatsApp's Send location button, Share my location in the web chat, or a
  maps link); a cash-on-delivery risk score with its reasons that asks risky buyers for an
  advance; the owner packing, dispatching and closing orders from the console while the
  customer is told; A6 shipping labels with the COD amount and a QR code to the parcel's page.
- **27 October, payments:** a customer's payment screenshot read by Spark Open and checked by
  code (wrong amount, other receiver, edits, reused picture by perceptual hash, here or at
  another shop); wallet and bank SMS forwarded from the owner's phone and matched to it, or to
  the order whose code is in the remarks or whose unique amount it is (Rs 2,497 instead of
  2,500); Approve and Reject for the owner on WhatsApp and in the console; an advance on cash
  on delivery and part payments; a watcher every 5 minutes; and a receipt signed with the
  shop's own key that the receipt page checks in the browser.
- **27 October, the order agent:** Spark Open with the shop's tools in every customer chat:
  orders from text, voice notes, photos and handwritten lists, bargaining above the owner's
  lowest price, delivery by city and weight, changes and cancellations before dispatch, all
  confirmed by the customer's tap on cards code writes; an answer check on every number;
  handover to the owner with a three-line summary.
- **27 October, catalog and the owner's assistant:** products, variants, stock movements and
  order states; products added from a photo and a voice note and prices and policies changed
  by chat, always after the owner confirms; shops made by chat on the start page; Try as the
  owner on every demo shop; the shop page catalog and the owner console.
- **27 October, languages and countries:** the language layer for 14 Tier 1 languages and
  dialects (detection, same-language replies, switching, handover translation, right to left,
  numbers in any form) with its evaluation set, and 20 country packs with 21 wallet SMS parsers.
- **27 October, linking and design:** Instagram DMs and Facebook Messenger alongside
  WhatsApp through one webhook, wa.me/ig.me/m.me links on every shop page, the connect
  command, and the redesigned web app that opens at once and counts down while the API wakes.
- **27 October, foundation:** database with row-level security and migrations; the model
  provider layer with failover, mock fixtures and the cost meter; the WhatsApp adapter and
  webhook; the try-it chat; the job queue, worker and cron; media storage with signed URLs;
  the hash-chained audit log; six business templates and their demo shops; the web app
  with the shop page, try-it chat and trust page in English, Urdu and Roman Urdu; CI.

The table in [FEATURES.md](FEATURES.md) tracks all 122 features and what state each is in.

## For AI assistants

Every shop has an MCP endpoint at `https://<api>/mcp/<shop>` (Streamable HTTP, JSON-RPC 2.0;
the shop's trust page shows its address). Its five tools only read: `shop_info`,
`search_products`, `get_product`, `quote_cart` and `start_checkout`. The last returns a link
to the shop's checkout page with the cart filled in; the person places the order there, and
the shop sees that it came from an AI assistant.

```
curl -s https://<api>/mcp/gulmohar-lawn -H 'content-type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"search_products","arguments":{"query":"lawn suit"}}}'
```

## Model API upgrade path

The Meta Model API (Muse Spark 1.3, Voice Transcribe, SAM 3.1, Muse Image) is not reachable
from Pakistan, so Chatfront runs Meta's open-weight models: Muse Spark Open on Baseten with
Fireworks as fallback, and Muse Glimmer on Modal. A Meta Model API provider sits behind the
same interface, off by default. Setting `META_MODEL_API_ENABLED=true` with a key and base URL
puts it first in every chain, with no code change. Features that need SAM masks, Muse Image
or web search are built behind their interfaces with stand-ins (card templates, a season
calendar, Safe Browsing), and are marked `model-api-only` in FEATURES.md.

## Deploy

The API is one free Docker web service on Render (`render.yaml`). The web app can be served by
it, or on its own from GitHub Pages so it never sleeps. Before the first deploy:

1. Neon: create a project and copy the direct connection string into `DATABASE_URL`.
2. Supabase: create a private bucket `chatfront-media`; set `SUPABASE_URL` and
   `SUPABASE_SERVICE_KEY`.
3. Render: New > Blueprint > this repository. Fill the values marked "sync: false".
4. cron-job.org: POST `https://<service>.onrender.com/internal/cron` every 5 minutes with the
   header `X-Cron-Secret` set to the `CRON_SECRET` Render generated. This runs the periodic
   jobs and also keeps the free service awake, which matters from 5 to 9 November while the
   judges try it.
5. Meta: in the app dashboard set one webhook URL for WhatsApp, Instagram and Messenger:
   `https://<service>.onrender.com/webhooks/meta`, with `META_VERIFY_TOKEN` as the verify
   token. Subscribe WhatsApp to `messages`, Instagram to `messages`, `messaging_postbacks`
   and `comments`, and the Page to `messages`, `messaging_postbacks` and `feed` (the last two
   bring comments under posts, feature 77).
6. Connect each account to its shop (tokens come from environment variables, never the
   command line):
   `python -m chatfront.channels.connect --shop gulmohar-lawn --channel whatsapp --account-id <phone number id> --handle <public number> --token-env WA_TOKEN`
   and the same with `--channel instagram` (Instagram account id, username) and
   `--channel messenger` (Page id, Page username). A number connected to the shop
   `chatfront` is where new shops are made by chat (feature 83).
7. SMS forwarding (per shop, by its owner): in the owner console, Payments > Set up SMS
   forwarding makes a link with the shop's secret, shown once. In a free SMS forwarder app on
   the owner's Android phone, forward messages from JazzCash, Easypaisa and the bank to it
   (POST, JSON or form). Messages from other senders are dropped unread.
8. Meta catalog (feature 6, optional): in Commerce Manager make a catalog for the shop, connect
   it to the WhatsApp Business Account and the Instagram account, and set the website's
   checkout URL to `https://<web app>/s/<shop>/checkout`. Then
   `python -m chatfront.catalog.meta --shop <shop> --catalog-id <id> --token-env CATALOG_TOKEN`
   with a system user token that has catalog_management (or set `META_CATALOG_TOKEN` on
   Render). Products go to the catalog every 30 minutes, and on Send now in the console.
9. Fraud reports (feature 37): Chatfront's own staff review shops' reports in the console of
   the platform shop `chatfront`. Make a sign-in link from Render's Shell (it reads
   `DATABASE_URL` and `SECRET_KEY` from the environment):
   `python -m chatfront.owner.sign_in --shop chatfront --hours 12`
10. GitHub Pages (optional): Settings > Pages > Source: GitHub Actions; add the repository
   variable `API_BASE` = the Render URL; set `CORS_ORIGINS` on Render to
   `https://<user>.github.io` and `WEB_BASE_URL` to `https://<user>.github.io/chatfront`
   (so links sent in chats open the Pages app); then run the "Deploy the web app to GitHub
   Pages" workflow.
11. Model keys: add `BASETEN_API_KEY` (and `FIREWORKS_API_KEY`, `MODAL_GLIMMER_*` when they
   come; `deploy/modal_glimmer.py` says how to start Glimmer on Modal) in Render's Environment for the app, and as repository secrets for the manual
   "Live model evaluation" workflow. They never go in the repository or the chat. Until they
   come, set `AI_MODE=mock` on Render so the demo answers with recorded replies, which say so;
   `https://<service>.onrender.com/health` shows `mock`, `live` or `no keys`. Optional:
   `SAFE_BROWSING_API_KEY` (where to get it is in `.env.example`) adds Google's lists of
   phishing sites to the link check (feature 40); code catches fake support links without it.
12. Alerts on staff phones (feature 115, optional): run `uv run python -m chatfront.push` once
   and put the `VAPID_PRIVATE_KEY` it prints in Render's Environment. Staff then turn alerts
   on from the console on each phone. The browsers' push services are free.
13. Failover drill (feature 114): with the keys in, take Baseten out for five minutes and the
   calls move to the next endpoint:
   `curl -X POST https://<service>.onrender.com/internal/ai/drill -H "X-Cron-Secret: $CRON_SECRET" -H 'content-type: application/json' -d '{"endpoint":"baseten","minutes":5}'`
   (`"minutes":0` brings it back; `GET /internal/ai` shows each endpoint's state).

## Roadmap

Build features (47) work end to end, Stretch features (34) followed, and the Later features
(41) are being built in groups; [FEATURES.md](FEATURES.md) says which are done. The demo shows
only work built during the event.

## Attribution

Built with Muse Spark Open. Muse Spark Open and Muse Glimmer are Meta's models, used under
their licenses.

## Network commerce (features 54, 61, 67 and 68)

The owner API exposes network workflows under `/api/network`. Management routes require an active
signed-in staff session and the relevant staff right. Branches, products and variants are validated
within the current tenant; buyer and seller order views are limited to their own party. A shop must
opt in through `PUT /api/network/settings` before its wholesale listings appear to other signed-in
shops.

- Branches: `GET/POST /api/network/branches`, `PUT /api/network/branches/{branch_id}`, and
  `PUT /api/network/team/{staff_id}/branch`.
- Wholesale: `POST /api/network/listings`, `PUT /api/network/listings/{listing_id}`,
  `GET /api/network/listings` and `GET /api/network/my-listings`.
- A2A orders: `POST /api/network/listings/{listing_id}/orders`, then the seller uses
  `GET /api/network/seller/orders` and `POST /api/network/seller/orders/{order_id}/approve`
  (or `/reject`). The buyer then confirms with
  `POST /api/network/buyer/orders/{order_id}/confirm`. The buyer budget and seller floor are
  deliberately absent from the other party's API response.
- Split ledger: provider events post to `POST /api/network/payments/provider-webhook`. Configure
  `MARKETPLACE_PROVIDER` and a random `MARKETPLACE_WEBHOOK_SECRET` of 32+ characters. The sender
  computes HMAC-SHA256 over the exact raw JSON body and sends
  `X-Marketplace-Signature: sha256=<hex>`. The normalized payload has
  `provider`, `event_id`, `transaction_id`, `checkout_reference`, integer-minor-unit
  `amount`, `currency`, and `status: "paid"`. Only signed, exact-amount, exact-currency events
  settle the ledger. This is a provider-neutral verification hook—not a hosted checkout or payout
  integration—so seller payout remains pending until a real payout adapter is configured.
