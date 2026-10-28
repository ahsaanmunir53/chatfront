# Verified commerce, version 1

An open way for a small shop to sign what it says about money, so that anyone can check it
without trusting the shop's server, Chatfront, or the person holding the paper. Chatfront
uses it for receipts, refund receipts and credit passports; any other system may use it too.

## What is signed

A document is a JSON object. Three kinds are defined:

| Kind | What it says | Schema |
|---|---|---|
| `receipt` | This much was paid for this order, and how the payment was checked | [receipt.schema.json](receipt.schema.json) |
| `refund_receipt` | This much was sent back to the buyer, and why | [receipt.schema.json](receipt.schema.json) (`"kind": "refund"`) |
| `credit_passport` | The shop's sales over twelve whole months, for a lender | [passport.schema.json](passport.schema.json) |

Amounts are integers in the currency's minor units (paisa for PKR). A payment receipt and a
passport name the shop but no buyer: no name, phone number or address. A refund receipt
carries one buyer detail, `to`, the name on the account the money was sent back to, because
that is what lets the buyer show the refund reached them.

## How it is signed

1. Write the document as **canonical JSON**: keys sorted at every level, no spaces between
   tokens, strings as they are (UTF-8, not escaped to `\uXXXX`). In Python this is
   `json.dumps(doc, sort_keys=True, separators=(",", ":"), ensure_ascii=False)`.
2. Sign the UTF-8 bytes of that text with the shop's **Ed25519** private key.
3. Publish the signature and the public key as **base64url without padding**: the raw 64
   signature bytes and the raw 32 key bytes.

Each shop has its own key. The private half never leaves the shop's server and is stored
sealed; the public half is published.

## How it is checked

1. Get the document, its signature and the shop's public key. The key is in the document's
   own response, and the shop also publishes it at `GET /api/shops/{slug}/keys`.
2. Rebuild the canonical JSON yourself from the document; do not take the signed text from
   the server.
3. Verify the signature with Ed25519 over those bytes.

A check that passes means the document is exactly what the shop's key signed. It says
nothing about whether the shop is honest about what it signed; what each field means, and
how it was checked before signing (an SMS matched by code, or the owner's word), is written
in the document itself (`verified_by` on a receipt; separate columns on a passport).

The browser pages (`/r/{code}` for a receipt, `/passport/{code}` for a passport) check the
signature with the browser's own Web Crypto. `python -m chatfront.verify document.json`
checks a saved response from the command line.

## Discovery

`GET /.well-known/verified-commerce` on a server that follows this standard returns where
the keys and documents are:

```json
{
  "version": 1,
  "algorithm": "Ed25519",
  "shop_keys": "/api/shops/{slug}/keys",
  "documents": {"receipt": "/api/receipts/{code}", "credit_passport": "/api/passport/{code}"}
}
```

## Test vectors

[vectors.json](vectors.json) holds a published test key (the seed is the bytes `00` to
`1f`, never to be used for anything real), a receipt and a passport, their canonical text and
their signatures. An implementation is correct when it produces the same canonical text and
accepts the signatures, and rejects them after any change to the document.

## Versions

`v` (receipts) and `version` (passports) say which version a document follows. A new field
may be added in a later version; a checker reads the version before trusting the fields.
