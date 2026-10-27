# Features

All 122 features: 1 to 116 from the spec, numbered as there, and 117 to 122 added on
27 October. Phase says when each is planned: Build (works end to end by 2 November; 47
features), Stretch (after Build passes; 34), Later (roadmap; 41). Features 121 and 122 are
Build for the layer, the Tier 1 languages and the Pakistan pack; their Tier 2 languages and
the other countries' payment parsers are Stretch.

Status values:

- `done`: built, tested, and reachable from the try-it chat or the dashboard.
- `partial`: part of it is built and tested; the note says what is missing.
- `needs-key`: built and tested against its adapter with a mock; the env var named turns it on.
- `model-api-only`: needs the Meta Model API (blocked from Pakistan); built behind its interface with the stand-in named.
- `planned`: not started.

Phase 0 (the foundation every feature stands on) is done; see PROGRESS.md.

| # | Feature | Module | Meta model | Phase | Status | Notes | Code | Tests |
|---|---|---|---|---|---|---|---|---|
| 1 | Product from a photo and a voice note | Catalog | Spark Open (vision, audio) | Build | `planned` |  |  |  |
| 2 | Whole shop from one video of a rack or shelf | Catalog | Spark Open (video); cut-outs Model API only (SAM) | Stretch | `planned` |  |  |  |
| 3 | Clean and lifestyle catalog images | Catalog | Model API only (Muse Image, SAM); card templates for now | Later | `planned` |  |  |  |
| 4 | Product details from the photo: description, tags, size and colour variants | Catalog | Spark Open (vision) | Build | `planned` |  |  |  |
| 5 | Size chart, and size advice from the customer's measurements | Catalog | Spark Open | Stretch | `planned` |  |  |  |
| 6 | WhatsApp catalog and Instagram shop sync | Catalog | None (Meta Commerce catalog) | Stretch | `planned` |  |  |  |
| 7 | Daily Status and Instagram post from new stock, Urdu and English captions | Catalog | Spark Open captions; images Model API only, templates for now | Later | `planned` |  |  |  |
| 8 | Flash sale and live sale mode: timer and first-come reservation | Catalog | None | Later | `planned` |  |  |  |
| 9 | Order agent in Urdu, Roman Urdu and English; understands Pashto, Punjabi, Sindhi | Order agent | Spark Open | Build | `planned` |  |  |  |
| 10 | Order from a voice note | Order agent | Spark Open (audio) | Build | `planned` |  |  |  |
| 11 | Search by photo: "yeh wala chahiye" | Order agent | Spark Open (vision) | Build | `planned` |  |  |  |
| 12 | Bargaining agent that stays above the owner's lowest price | Order agent | Spark Open and rules | Build | `planned` |  |  |  |
| 13 | Upsell and bundles | Order agent | Spark Open | Stretch | `planned` |  |  |  |
| 14 | Abandoned-cart reminders inside the free 24-hour window | Order agent | None | Later | `planned` |  |  |  |
| 15 | Smart broadcast to interested customers, with the return on each | Order agent | None | Later | `planned` |  |  |  |
| 16 | Pre-order and waitlist | Order agent | None | Stretch | `planned` |  |  |  |
| 17 | Custom order mockup (cake, tailoring, print) | Order agent | Model API only (Muse Image) | Later | `planned` |  |  |  |
| 18 | Appointment booking for salons and tutors | Order agent | None (WhatsApp Flows) | Later | `planned` |  |  |  |
| 19 | Coupons, discount codes and loyalty points | Order agent | None | Stretch | `planned` |  |  |  |
| 20 | Referral programme | Order agent | None | Later | `planned` |  |  |  |
| 21 | Gift order with a message card | Order agent | Card image Model API only; template for now | Later | `planned` |  |  |  |
| 22 | Group order with split payment | Order agent | None (shared cart link) | Later | `planned` |  |  |  |
| 23 | Payment screenshot forensics | Payments | Spark Open (vision) | Build | `planned` |  |  |  |
| 24 | Payment match from the seller's credit SMS (Android forwarder) | Payments | None | Build | `planned` |  |  |  |
| 25 | Approve and Reject buttons for the owner on WhatsApp | Payments | None | Build | `planned` |  |  |  |
| 26 | Signed QR receipt anyone can check | Payments | None | Build | `planned` |  |  |  |
| 27 | Advance on COD, and instalment tracking | Payments | None | Build | `planned` |  |  |  |
| 28 | Refund with verified proof | Payments | None | Stretch | `planned` |  |  |  |
| 29 | "Sent too much by mistake" scam check against the real SMS amount | Payments | None | Stretch | `planned` |  |  |  |
| 30 | Always-on watcher for pending payments and orders | Payments | Glimmer | Build | `planned` |  |  |  |
| 31 | Courier booking and tracking with customer updates | Delivery | None | Later | `planned` |  |  |  |
| 32 | Courier COD settlement matched to orders | Delivery | None | Later | `planned` |  |  |  |
| 33 | Address clean-up, asking for a location pin when needed | Delivery | Spark Open | Build | `planned` |  |  |  |
| 34 | RTO and COD risk score | Delivery | None | Build | `planned` |  |  |  |
| 35 | Proof of delivery from the rider's photo | Delivery | Spark Open (vision); masks Model API only | Later | `planned` |  |  |  |
| 36 | Dispute decision: unboxing video against the catalog photo | Delivery | Spark Open (video); masks Model API only | Later | `planned` |  |  |  |
| 37 | Fraud-buyer list, hashed, only after review | Shield | None | Stretch | `planned` |  |  |  |
| 38 | Verified-buyer list: good buyers get COD at once | Shield | None | Build | `planned` |  |  |  |
| 39 | Call and voice-note guardian against fake couriers and agents | Shield | Spark Open (audio) | Later | `planned` |  |  |  |
| 40 | Fake "Meta or WhatsApp support" links caught; OTP alert | Shield | Spark Open and Safe Browsing | Stretch | `planned` |  |  |  |
| 41 | The shop's official numbers on a trust page | Shield | None | Later | `partial` | Trust page lists the shop's official channels with links that open them (wa.me, ig.me, m.me). A verification badge comes later. | api/src/chatfront/routes/public.py, api/src/chatfront/channels/links.py, web/src/pages/InfoPages.tsx | tests/integration/test_app.py, web/e2e/try-it.spec.ts |
| 42 | Staff roles and a hash-chained audit log | Shield | None | Build | `partial` | Hash-chained, append-only audit log (the app role cannot update or delete it) with chain verification. Staff table and roles exist; permissions per role come with the dashboard. | api/src/chatfront/audit/, api/migrations/versions/0001_foundation.py | tests/integration/test_audit.py, tests/integration/test_rls.py |
| 43 | Privacy guard: CNIC and card photos saved blurred | Shield | Spark Open (vision); masks Model API only | Stretch | `planned` |  |  |  |
| 44 | Customer data export, and deletion on request | Shield | None | Later | `planned` |  |  |  |
| 45 | Trust page for buyers: delivered orders, reply time, return rate | Shield | None | Stretch | `planned` |  |  |  |
| 46 | Reviews from verified buyers only | Shield | None | Stretch | `planned` |  |  |  |
| 47 | STIX report of scam numbers for NCCIA and CERT | Shield | None | Later | `planned` |  |  |  |
| 48 | Ledger from bill and expense photos or voice notes | Books | Spark Open (vision, audio) | Build | `planned` |  |  |  |
| 49 | Udhaar khata with reminders that grow firmer | Books | None | Build | `planned` |  |  |  |
| 50 | Daily and weekly profit and loss card on WhatsApp | Books | None | Build | `planned` |  |  |  |
| 51 | True profit per product after courier costs and returns | Books | None | Stretch | `planned` |  |  |  |
| 52 | Stock tracking with low-stock alerts | Growth | None | Build | `planned` |  |  |  |
| 53 | Reorder drafts for suppliers, and supplier price comparison | Growth | Spark Open | Later | `planned` |  |  |  |
| 54 | B2B orders from wholesalers in the same network | Growth | None | Later | `planned` |  |  |  |
| 55 | Season forecast: Ramzan, Eid, weddings, 14 August, winter | Growth | Spark Open and a calendar file | Later | `planned` |  |  |  |
| 56 | Price check: what the same item sells for elsewhere | Growth | Model API only (web search) | Later | `planned` |  |  |  |
| 57 | Ask your data: "what sold most last month?" | Growth | Spark Open | Stretch | `planned` |  |  |  |
| 58 | Cash-flow forecast | Books | None | Later | `planned` |  |  |  |
| 59 | Customer CRM: repeat customers, lifetime value, Eid greetings | Growth | None | Stretch | `planned` |  |  |  |
| 60 | Staff salary and commission | Growth | None | Later | `planned` |  |  |  |
| 61 | Several branches and their staff | Growth | None | Later | `planned` |  |  |  |
| 62 | Yearly summary and zakat calculation | Books | None | Later | `planned` |  |  |  |
| 63 | Sales and expense export for FBR filing | Books | None | Later | `planned` |  |  |  |
| 64 | Credit Passport: verified sales history for a microfinance bank | Growth | None | Later | `planned` |  |  |  |
| 65 | AI business coach with weekly advice | Growth | Spark Open | Later | `planned` |  |  |  |
| 66 | Agent-to-agent commerce: MCP endpoint for outside assistants | Future | Spark Open | Stretch | `planned` |  |  |  |
| 67 | Buyer's AI and shop's AI bargain with each other (A2A) | Future | Spark Open | Later | `planned` |  |  |  |
| 68 | Network marketplace with verified money splits | Future | None | Later | `planned` |  |  |  |
| 69 | Glasses and shelf mode: count stock by looking | Future | Needs Meta glasses; 87 is the phone version | Later | `planned` |  |  |  |
| 70 | Offline and privacy mode: Glimmer on the shop's own PC | Future | Glimmer | Later | `planned` |  |  |  |
| 71 | Run the whole shop by voice, for owners who cannot read | Future | Spark Open (audio) | Later | `planned` |  |  |  |
| 72 | Open "verified commerce" standard for signed receipts | Future | None | Later | `planned` |  |  |  |
| 73 | Other countries: bKash, M-Pesa and other wallet SMS formats | Future | None | Later | `planned` |  |  |  |
| 74 | Web dashboard: orders, money, fraud and books | Future | None | Build | `planned` |  |  |  |
| 75 | Owner's WhatsApp commands: "aaj ki sale?" | Future | Spark Open | Build | `planned` |  |  |  |
| 76 | Unified inbox for WhatsApp, Instagram and Messenger, with staff assignment | Channels | None | Stretch | `partial` | WhatsApp, Instagram DMs and Messenger all land in one messages table through one webhook (`/webhooks/meta`) and get replies from the same agent. The inbox screen and staff assignment come with the dashboard. | api/src/chatfront/channels/messaging.py, api/src/chatfront/channels/routes.py | tests/unit/test_messaging.py, tests/integration/test_app.py::test_instagram_and_messenger_dms_get_replies |
| 77 | Comment-to-DM: a product card by private reply | Channels | None | Stretch | `planned` |  |  |  |
| 78 | Story replies and mentions handled as leads | Channels | None | Later | `planned` |  |  |  |
| 79 | One customer identity across channels | Channels | None | Stretch | `partial` | Each channel id is stored sealed with a keyed hash; a WhatsApp id links to the customer's phone hash. Linking an Instagram or Messenger customer to the same record needs their phone number confirmed in chat. | api/src/chatfront/channels/service.py (identify) | tests/integration/test_app.py |
| 80 | Hosted shop page as the checkout for Facebook and Instagram shop links | Channels | None | Build | `partial` | Hosted shop page per tenant at /s/<slug>, with Chat here plus WhatsApp, Instagram and Messenger buttons. Products, cart and checkout come with the catalog and payments. | web/src/pages/ShopPage.tsx, api/src/chatfront/routes/public.py | tests/integration/test_app.py, web/e2e/try-it.spec.ts |
| 81 | Ad attribution: which ad brought which order | Channels | None | Later | `partial` | The ref on ig.me and m.me links, and the referral Meta sends for ads, are kept with the message. Crediting orders to ads comes with orders. | api/src/chatfront/channels/messaging.py, api/src/chatfront/channels/whatsapp.py | tests/unit/test_messaging.py, tests/unit/test_channels.py |
| 82 | Cross-posting one new product to every channel | Channels | None | Later | `planned` |  |  |  |
| 83 | Shop in five minutes over WhatsApp | Catalog | Spark Open | Build | `planned` |  |  |  |
| 84 | Instagram import of past posts as draft products | Catalog | Spark Open (vision) | Stretch | `planned` |  |  |  |
| 85 | Excel or WhatsApp Business catalog import | Catalog | Spark Open | Stretch | `planned` |  |  |  |
| 86 | Teach by voice: prices and policy updated after the owner confirms | Catalog | Spark Open (audio) | Build | `planned` |  |  |  |
| 87 | Shelf photo stock count, confirmed by the owner | Catalog | Spark Open (vision) | Stretch | `planned` |  |  |  |
| 88 | Video search: find the item in a clip or reel | Order agent | Spark Open (video) | Stretch | `planned` |  |  |  |
| 89 | Handwritten kiryana list photo becomes a cart | Order agent | Spark Open (vision) | Build | `planned` |  |  |  |
| 90 | Human handover with a three-line summary | Order agent | Spark Open | Build | `planned` |  |  |  |
| 91 | Approved answers reused only after the owner approves | Order agent | Spark Open | Stretch | `planned` |  |  |  |
| 92 | Exchange and return flow | Order agent | Spark Open | Stretch | `planned` |  |  |  |
| 93 | Edit or cancel before dispatch, stock put back | Order agent | None | Build | `planned` |  |  |  |
| 94 | Delivery charge by city and weight, free-delivery threshold | Order agent | None | Build | `planned` |  |  |  |
| 95 | Eid and sale cut-off countdown | Order agent | None | Stretch | `planned` |  |  |  |
| 96 | Diaspora gift orders with proof from a foreign transfer | Order agent | Spark Open (vision) | Later | `planned` |  |  |  |
| 97 | Unique amount per transfer order (Rs 2,497 instead of 2,500) | Payments | None | Build | `planned` |  |  |  |
| 98 | Order code in the transfer remarks, read from SMS and screenshot | Payments | Spark Open (vision) | Build | `planned` |  |  |  |
| 99 | Reused-screenshot catch by perceptual hash | Payments | None | Build | `planned` |  |  |  |
| 100 | Partial payment: ask for the balance, link both claims | Payments | Spark Open | Stretch | `planned` |  |  |  |
| 101 | Shipping label PDF with order QR and COD amount | Delivery | None | Build | `planned` |  |  |  |
| 102 | Packing check: packed items photo against the order | Delivery | Spark Open (vision) | Stretch | `planned` |  |  |  |
| 103 | Daily pick list and packing slips | Delivery | None | Stretch | `planned` |  |  |  |
| 104 | Autonomy levels per action: auto, ask me, never | Shield | None | Build | `planned` |  |  |  |
| 105 | Answer check: prices, stock and totals checked before sending | Shield | None | Build | `planned` |  |  |  |
| 106 | Prompt-injection guard: tools enforce the policy | Shield | Spark Open and rules | Build | `planned` |  |  |  |
| 107 | Why panel: evidence and the rule that fired | Shield | None | Build | `planned` |  |  |  |
| 108 | Consent log for broadcasts, fraud list and credit passport | Shield | None | Later | `planned` |  |  |  |
| 109 | Try-it page for judges and a demo owner login | Demo | Spark Open | Build | `partial` | Phone-style try-it chat on the shop page runs the same reply job as WhatsApp. Demo owner login comes with the dashboard. | web/src/pages/ChatPage.tsx, api/src/chatfront/channels/routes.py | tests/integration/test_app.py::test_try_it_chat, web/e2e/try-it.spec.ts |
| 110 | Customer simulator: Glimmer plays 50 customers | Demo | Glimmer and Spark Open | Stretch | `planned` |  |  |  |
| 111 | Evaluation set: 100 scripted conversations, pass rate in CI | Demo | Spark Open | Build | `planned` |  |  |  |
| 112 | Cost meter: tokens and rupees per order | Demo | None | Build | `partial` | Every model call records tokens, cost in thousandths of a paisa, latency and what it was for (`ai_calls`). Per-order view on the dashboard comes with feature 74. | api/src/chatfront/ai/client.py, api/src/chatfront/ai/meter.py | tests/unit/test_ai.py, tests/integration/test_ai_meter.py |
| 113 | Reasoning routing: light for chat, deepest for forensics | Demo | Spark Open (reasoning setting) | Build | `partial` | Reasoning effort per role (low for chat, medium for vision and audio) with a per-call override; payment forensics will call it with xhigh. | api/src/chatfront/ai/config.py | tests/unit/test_ai.py::TestConfig |
| 114 | Failover between Baseten, Modal and Fireworks | Demo | Spark Open | Stretch | `needs-key` | Failover Baseten -> Fireworks for Spark Open (Modal hosts Glimmer), tested against mocked endpoints. Runs live with BASETEN_API_KEY and FIREWORKS_API_KEY. | api/src/chatfront/ai/client.py, api/src/chatfront/ai/config.py | tests/unit/test_ai.py::TestClient |
| 115 | Urdu right-to-left dashboard, installable, with push alerts | Demo | None | Stretch | `partial` | The web app reads right to left in Urdu and installs as a PWA. The dashboard and push alerts come later. | web/src/lang.tsx, web/public/manifest.webmanifest, web/public/sw.js | web/src/i18n.test.ts, web/e2e/try-it.spec.ts |
| 116 | Share cards: product link previews for WhatsApp and X | Growth | None | Stretch | `planned` |  |  |  |
| 117 | Restock by chat, voice note or bill photo: Confirm before stock changes, bill lines to products, purchase into the ledger, sheet import; every change a stock movement | Growth | Spark Open (audio, vision, JSON) | Build | `planned` | Uses the ledger (48); the sheet import path is built here and reused by 85. |  |  |
| 118 | Owner panel inside WhatsApp: Flows to edit products, prices, limits and settings; low-stock alert once per crossing with Reorder and Snooze | Growth | Spark Open (owner's chat commands) | Build | `planned` | Reorder button needs the supplier draft from 53; alerts outside 24 hours need an approved utility template. |  |  |
| 119 | Stock batches and true profit per batch: FIFO or FEFO, batch cost saved on each order line, the owner decides prices when costs change | Books | None | Build | `planned` | Feeds 51; returns go back to their batch. |  |  |
| 120 | Expiry dates and write-offs: daily expiry alerts, expired stock never sold, discards recorded as a loss | Growth | Spark Open (vision, expiry from a bill or label) | Build | `planned` | Builds on 119's batches. |  |  |
| 121 | Many languages, the way people type: one language layer (ICU catalog, detection of language and script, same-language replies, switching mid-chat, owner's own language), code-mixed and romanised writing, handover translation, product translations, right-to-left and fonts, numbers in any form; Tier 1 for the demo, Tier 2 Stretch | Core | Spark Open (replies, translation) | Build | `partial` | Built and tested: the language registry (42 languages; Tier 1 is 14 tags), one ICU catalog per interface language (en, ur, ur-Latn, pa-Arab, ps, sd, hi, bn, ar; same output in API and browser), detection of script and language per message including Roman Urdu, Hinglish, Banglish, Arabizi and Gulf and Egyptian Arabic, replies in the customer's language with a check and one retry then translation, switching mid-chat ("English mein baat karo"), owner's own language (`staff.lang`) with handover translation kept on each message, right-to-left and Noto fonts per script, numbers in every listed form. Missing: product translations (with the catalog, 1 and 4), the handover screen (90), live eval runs (need BASETEN_API_KEY), voice-note recordings for the eval. All interface translations except English are model-drafted and marked for native review (i18n/review.json); every language shows beta until a live run passes. | i18n/, api/src/chatfront/core/{languages,langdetect,icu,i18n,numbers}.py, api/src/chatfront/channels/{language,handover}.py, api/src/chatfront/ai/translate.py, api/src/chatfront/eval/, web/src/i18n.ts, web/src/lang.tsx | tests/unit/test_languages.py, tests/integration/test_languages_flow.py, tests/eval/test_languages_eval.py, web/src/i18n.test.ts, web/e2e/languages.spec.ts |
| 122 | Country packs: one config file per country (currency, grouping, dates, phones, time zone, tax label, wallets and banks), CLDR formatting, money in integer minor units, SMS parser plugins per provider marked unverified until tested on real samples | Core | None | Build | `partial` | Built and tested: 20 country packs (PK default; IN, BD, NP, LK, AE, SA, EG, NG, GH, KE, ZA, ID, MY, PH, VN, TH, TR, BR, MX), money in integer minor units with the count from CLDR, CLDR number, money and date formats in the reader's digits, phone numbers by pack, 21 SMS parser plugins (JazzCash, Easypaisa and Pakistani banks first; then bKash, Nagad, UPI, eSewa, Khalti, M-Pesa, MTN MoMo, OPay, PalmPay, GCash, Maya, GoPay, OVO, DANA, Pix, InstaPay, Vodafone Cash, STC Pay). Every parser is `unverified`: its fixtures are synthetic until a real message arrives, and the SMS payment match (24) will never approve on an unverified parser alone. Prices and receipts start using the packs when the catalog and orders are built. | i18n/countries/, api/src/chatfront/core/{countries,money,phone}.py, api/src/chatfront/payments/sms/ | tests/unit/test_countries.py, tests/fixtures/sms/ |
