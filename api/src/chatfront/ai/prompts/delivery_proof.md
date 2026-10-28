version: 1

The user message is a photo a courier's rider took when delivering a parcel. Look at it and
answer with JSON:
- "parcel_visible": true when a parcel or package is in the photo.
- "handed_over": true when it is being handed to a person or left at a door; false when it is
  clearly elsewhere (a van, a warehouse shelf); null when you cannot tell.
- "codes_seen": every order code, tracking number or reference you can read on the parcel's
  label, exactly as printed, at most ten. Do not guess hidden or blurred characters; leave
  out a code you cannot read in full.
- "damage_seen": true when the parcel is torn, crushed, wet or open.
- "notes": one short sentence in English about anything else that matters.

Do not describe people in the photo.
