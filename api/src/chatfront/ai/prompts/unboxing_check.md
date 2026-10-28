version: 1

A customer says the parcel they received is wrong or damaged (their reason: $reason) and sent
a video of opening it. The user message has the video, then, when the shop has them, the
product's catalog photo and the photo the shop took when packing it. Watch the whole video and
answer with JSON:
- "seal_intact_at_start": true when the parcel is shown sealed and unopened at the start;
  false when it is already open; null when you cannot tell.
- "filmed_without_cut": true when the video runs without a cut or a jump from the sealed
  parcel to the item; false when there is a cut; null when you cannot tell.
- "item_matches_catalog": true when the item taken out is the product in the catalog photo
  (same design and colour); false when it is clearly a different one; null without a catalog
  photo or when you cannot tell.
- "item_matches_packed": the same against the packing photo; null without one.
- "damage_seen": true when the item or parcel is visibly damaged.
- "damage": what the damage is, in a few words; empty when none.
- "notes": one or two short sentences in English about anything else that matters.

Report only what the video shows. Do not decide the dispute and do not describe people.
