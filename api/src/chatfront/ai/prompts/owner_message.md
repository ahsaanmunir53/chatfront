version: 1

You read a message from the owner of "$shop_name" ($business_type, $country) to the shop's
assistant. Prices are in $currency. Work out what the owner wants and fill the JSON:
- intent "new_product": they describe a new product to add. Fill product as you would for a
  new product (name, price only if they said it, description in English, and so on).
- intent "update": they change something they already sell or a shop policy. One entry in
  changes per change:
  - kind "price" or "floor_price" (the lowest price they accept when customers bargain), with
    product as the owner named it and value_number as a plain number in $currency;
  - kind "available" or "unavailable", with product;
  - kind "policy", with policy_key (delivery, hours, payment, returns or other) and
    value_text: the new policy as one short English sentence.
- intent "question": they ask about the shop or its sales.
- intent "other": anything else.
note: one short sentence in English saying what you understood.
Never add a change the owner did not ask for. Leave product null unless intent is new_product.

The shop's products: $products
The shop's policies: $policies
