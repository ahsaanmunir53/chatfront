# Country packs

One file per country: currency and how money is written, number grouping, date style, phone
format, time zone, the tax label, the languages customers there write in (most common first)
and the wallets and banks people pay with. Adding a country means adding a file; no code changes.

- Money is stored in integer minor units of `currency`; the number of minor units comes from CLDR.
- `number_locale` is the CLDR locale whose grouping and separators money uses; `grouping` records
  what that gives (3 = 1,234,567; 3;2 = 12,34,567) and a test checks the two agree.
- `local_symbols` replaces the symbol for readers of a language ("4,850 روپے" for Urdu).
- `parser` names the SMS (or notification) parser in `api/src/chatfront/payments/sms/`. A parser
  is "unverified" until it passes on a real message from that provider; the payment check never
  approves on an unverified parser alone and sends the case to the owner instead.

Pakistan is the default and the only pack the demo depends on. There are no packs for countries
the hackathon rules exclude (Belarus, China, Cuba, Iran, North Korea, Russia, Syria, Ukraine)
or for sanctioned regions; a test enforces it.
