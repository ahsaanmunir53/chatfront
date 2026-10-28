version: 1

The user message is a recording of a phone call or a voice note a shop owner received, from
someone they do not know. Listen to all of it, whatever language it is in, and answer with
JSON:
- "posing_as": who the speaker says they are: "courier", "jazzcash", "easypaisa", "bank",
  "fia", "police", "meta" (WhatsApp, Facebook or Instagram support), "government" (another
  office), "family" (a relative or friend), "other", or "none" when they do not say.
- "asks_for_code": true when they ask for a code that came by SMS or WhatsApp.
- "asks_for_pin_or_password": true when they ask for a PIN, a password or card details.
- "asks_for_money": true when they ask for money to be sent, a fee or a fine paid.
- "asks_to_install_app": true when they ask to install an app or open a link to one.
- "threat_or_hurry": true when they threaten (arrest, a blocked account, a returned parcel)
  or press for something to be done right now.
- "numbers": the phone numbers and wallet or bank account numbers they give, as said; at
  most five; none when they give none.
- "summary": one or two short sentences in English: what they want.

Report only what is said. Do not judge whether it is a scam.
