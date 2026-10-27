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

## Try it

Every shop gets a page at `/s/<shop>` with a chat that runs the same agent as the shop's
WhatsApp number, plus buttons that open the shop on WhatsApp, Instagram and Messenger.
WhatsApp test numbers can only message their verified recipients, so the page chat is how
anyone outside the shop can try it. Six demo shops, one per business type, are created on
first start: open `/` and pick one. They and their customers are made up.

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
  eval/       evaluation sets (the language set runs in CI on the mock)
i18n/         language registry, catalogs, number words, detector hints, country packs
web/src/      React app: shop page and catalog, try-it chat, start page, owner console, trust page,
              receipt check, parcel tracking, dashboard
```

## Built during the hackathon

Everything in this repository was written from 27 October 2026, during the Meta Global AI
Developer Hackathon. The commit history shows the order.

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
   header `X-Cron-Secret` set to the `CRON_SECRET` Render generated.
5. Meta: in the app dashboard set one webhook URL for WhatsApp, Instagram and Messenger:
   `https://<service>.onrender.com/webhooks/meta`, with `META_VERIFY_TOKEN` as the verify
   token. Subscribe WhatsApp to `messages`, Instagram to `messages` and
   `messaging_postbacks`, and the Page to `messages` and `messaging_postbacks`.
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
8. GitHub Pages (optional): Settings > Pages > Source: GitHub Actions; add the repository
   variable `API_BASE` = the Render URL; set `CORS_ORIGINS` on Render to
   `https://<user>.github.io` and `WEB_BASE_URL` to `https://<user>.github.io/chatfront`
   (so links sent in chats open the Pages app); then run the "Deploy the web app to GitHub
   Pages" workflow.
9. Model keys: add `BASETEN_API_KEY` (and `FIREWORKS_API_KEY`, `MODAL_GLIMMER_*` when they
   come) in Render's Environment for the app, and as repository secrets for the manual
   "Live model evaluation" workflow. They never go in the repository or the chat.

## Roadmap

Build features (47) work end to end by 2 November, Stretch features (34) follow, and Later
features (41) stay on the roadmap. The demo shows only work built during the event.

## Attribution

Built with Muse Spark Open. Muse Spark Open and Muse Glimmer are Meta's models, used under
their licenses.
