import { API_BASE } from "./config";
import type { Text3 } from "./i18n";
import type { ReceiptPayload } from "./receipt";

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
  ) {
    super(message);
  }
}

export type ChannelLink = { channel: string; display: string | null; url: string | null };

export type Shop = {
  slug: string;
  name: string;
  country: string;
  currency: string;
  business_type: Text3;
  city: string | null;
  tagline: Text3;
  default_lang: string;
  is_demo: boolean;
  attribution: string;
  channels: ChannelLink[];
  countdowns: Countdown[];
};

/** A deadline on the shop page: the last day to order for delivery before a big day, or a
 * sale's last day (feature 95). `ends_at` is the end of that day in the shop's time. */
export type Countdown = {
  kind: "delivery" | "sale";
  event: string | null;
  name: string | null;
  day: string;
  ends_at: string;
  expected: boolean;
  code: string | null;
  percent: number | null;
  amount: number | null;
};

export type TrustInfo = { slug: string; name: string; channels: ChannelLink[] };

export type ChatButton = { id: string; title: string };
export type ChatListRow = { id: string; title: string; description: string | null };
export type ServerMessage = {
  id: string;
  direction: "in" | "out";
  kind: string;
  body: string | null;
  lang?: string | null;
  payload: {
    buttons?: ChatButton[];
    list?: { button: string; rows: ChatListRow[] };
    location_request?: boolean;
    location?: { latitude: number; longitude: number };
  };
  media_url?: string | null;
  client_id?: string | null;
  created_at: string;
};

export type PublicVariant = {
  id: string;
  catalog_id: string;
  options: Record<string, string>;
  price: number;
  in_stock: boolean;
};
export type PublicProduct = {
  code: string;
  name: string;
  description: string;
  category: string | null;
  price: number;
  attributes: Record<string, unknown>;
  translations: Record<string, { name?: string; description?: string }>;
  image_url: string | null;
  in_stock: boolean;
  ships_from: string | null;
  size_chart: SizeChart | null;
  variants: PublicVariant[];
  share_url: string; // the link with a preview card (feature 116)
  sale?: PublicSale | null; // a flash or live sale it is in now (feature 8)
};
export type PublicSale = {
  name: string;
  price: number;
  ends_at: string;
  left: number;
  live: boolean;
  number: number;
};

/** A size chart (feature 5): each size's range per measure, smallest size first. */
export type Measure =
  "chest" | "waist" | "hips" | "shoulder" | "length" | "sleeve" | "inseam" | "foot";
export type SizeRow = { size: string; ranges: Partial<Record<Measure, [number, number]>> };
export type SizeChart = {
  name: string;
  unit: "in" | "cm";
  categories?: string[];
  measures?: Measure[];
  rows: SizeRow[];
};
export type SizeCharts = { charts: SizeChart[]; usual: SizeChart[]; categories: string[] };

/** What an import found (features 85, 84 and 2), waiting for the owner. */
export type ImportDraft = {
  id: string;
  import_id: string;
  source: ImportSource;
  name: string;
  price: number | null;
  price_from: string | null;
  description: string;
  category: string | null;
  variants: { options: Record<string, string>; price: number | null; stock: number | null }[];
  photo: string | null;
  ready: boolean;
  in_shop: string | null;
  link: string | null;
};
export type ImportSource = "sheet" | "meta_catalog" | "instagram" | "video";
export type ImportRun = {
  id: string;
  source: ImportSource;
  label: string;
  status: "reading" | "ready" | "failed";
  found: number;
  waiting: number;
  error: string | null;
  created_at: string;
};
export type Imports = {
  currency: string;
  runs: ImportRun[];
  drafts: ImportDraft[];
  instagram: boolean;
  meta_catalog: boolean;
};
export type ShelfCount = {
  note: string;
  rows: { variant_id: string; name: string; before: number | null; counted: number }[];
  problems: string[];
};

export type QuoteLine = {
  item: string;
  name: string | null;
  options: Record<string, string>;
  unit_price: number;
  quantity: number;
  line_total: number;
  problem: "not_found" | "sold_out" | "not_enough" | null;
  left: number | null;
  ships_from: string | null;
};
export type QuoteOffer = { kind: "bundle" | "coupon"; label: string; amount: number };
export type Quote = {
  lines: QuoteLine[];
  subtotal: number;
  delivery: number | null;
  free_delivery: boolean;
  offers: QuoteOffer[];
  coupon_problem: string | null;
  total: number;
  methods: ("cod" | "transfer")[];
  currency: string;
};
export type DeliveryDetails = {
  name: string;
  phone: string;
  city: string;
  address: string;
  area: string;
  landmark: string;
};
export type CheckoutRequest = {
  lines: { item: string; quantity: number }[];
  details: DeliveryDetails | null;
  join: { code: string; name: string } | null;
  start_group: number | null;
  gift: { message: string; from_name: string } | null;
  payment_method: "cod" | "transfer";
  coupon: string | null;
  source: "shop_page" | "facebook" | "instagram" | "assistant";
  lang: string;
};
export type Placed = {
  code: string;
  status: string;
  total: number;
  due_now: number | null;
  payment_method: string;
  message: string;
  group: string | null;
};

export type OwnerMe = {
  slug: string;
  shop_name: string;
  staff_id: string;
  staff_name: string;
  lang: string;
  template: string;
  country: string;
  currency: string;
  is_demo: boolean;
  languages: string[];
  role: string;
  rights: string[];
  is_platform?: boolean;
};
export type OwnerProduct = {
  id: string;
  code: string;
  status: "draft" | "active" | "archived";
  name: string;
  price: number;
  floor_price: number | null;
  translations: Record<string, { name?: string; description?: string; by?: "owner" | "model" }>;
  variants: { id: string; options: Record<string, string>; price: number; stock: number | null }[];
};

export type NetworkBranch = {
  id: string;
  name: string;
  code: string;
  city: string | null;
  address: string | null;
  active: boolean;
  is_default: boolean;
};
export type NetworkSettings = {
  wholesale_enabled: boolean;
  provider_ready: boolean;
  provider: string | null;
};
export type WholesaleListing = {
  id: string;
  seller_tenant_id: string;
  seller_slug: string;
  seller_name: string;
  title: string;
  description: string;
  unit_price: number;
  minimum_quantity: number;
  maximum_quantity: number;
  currency: string;
  active: boolean;
};
export type NetworkOrder = {
  id: string;
  listing_title: string;
  buyer_name: string;
  seller_name: string;
  quantity: number;
  initial_offer_unit_price: number;
  agreed_unit_price: number | null;
  total: number | null;
  currency: string;
  status: string;
  rounds: number;
  created_at: string;
  checkout_reference: string | null;
};
export type NetworkRound = {
  round: number;
  buyer_offer: number;
  seller_counter: number | null;
  outcome: string;
  created_at: string;
};
export type NetworkPayment = {
  id: string;
  checkout_reference: string;
  provider: string;
  amount: number;
  currency: string;
  platform_fee: number;
  seller_amount: number;
  status: string;
  payout_status: string;
  checkout_available: boolean;
};

export type InboxState = "assistant" | "waiting" | "person";
export type InboxPerson = { id: string; name: string };
export type InboxItem = {
  conversation_id: string;
  customer: string | null;
  channel: string;
  known_on: string[];
  state: InboxState;
  handover_id: string | null;
  summary: string | null;
  assigned: InboxPerson | null;
  last: {
    by: "customer" | "agent" | "staff";
    kind: string;
    text: string | null;
    at: string;
  } | null;
  window_open: boolean;
};
export type SocialPost = {
  id: string;
  channel: string;
  permalink: string | null;
  caption: string | null;
  product_id: string | null;
  product_name: string | null;
  linked_by: "caption" | "owner" | null;
  waiting: number;
  answered: number;
};
export type PostComment = {
  id: string;
  channel: string;
  text: string;
  status: "new" | "waiting" | "sent" | "skipped" | "failed";
  reason: string | null;
  product_name: string | null;
  post_id: string | null;
  commented_at: string;
};
export type Posts = { posts: SocialPost[]; comments: PostComment[] };
export type CatalogSync = {
  connected: boolean;
  catalog_id: string | null;
  status: string | null;
  items: number;
  synced_at: string | null;
  errors: { id?: string | null; message: string }[];
  left_out: string[];
};
export type InboxShow = "all" | "waiting" | "mine";
export type Inbox = {
  items: InboxItem[];
  waiting: number;
  people: InboxPerson[];
  can_assign: boolean;
};
export type ConsoleMessage = {
  id: string;
  direction: "in" | "out";
  kind: string;
  body: string | null;
  original: string | null;
  by: "customer" | "agent" | "staff";
  created_at: string;
};

export type PaymentSignal = { code: string; detail: string };
export type RefundReason = "cancelled" | "returned" | "overpaid";
export type Refund = {
  id: string;
  order_code: string;
  customer: string;
  amount: number;
  reason: RefundReason;
  to_name: string | null;
  to_provider: string | null;
  status: "due" | "sent";
  return_code: string | null;
  signals: PaymentSignal[];
  proof_url: string | null;
  verified_by: "proof" | "owner" | null;
  receipt_code: string | null;
  created_at: string;
  sent_at: string | null;
};
export type Refunds = { currency: string; due: Refund[]; sent: Refund[] };
export type RefundResult = {
  outcome: "sent" | "check";
  signals: PaymentSignal[];
  receipt_code: string | null;
  customer_told: boolean;
};
export type CouponIn = {
  code: string;
  percent: number | null;
  amount: number | null;
  min_subtotal: number;
  starts_on: string | null;
  ends_on: string | null;
  max_uses: number | null;
  once_per_customer: boolean;
  show_on_page: boolean;
};
export type OwnerCoupon = CouponIn & { id: string; uses: number; active: boolean };
export type OwnerBundle = {
  id: string;
  name: string;
  products: { code: string; name: string; price: number }[];
  off: number;
  active: boolean;
};
export type LoyaltySettings = {
  on: boolean;
  earn_per: number;
  point_value: number;
  max_share: number;
};
export type ShopEvent = { name: string; date: string };
export type ReferralSettings = {
  on: boolean;
  friend_percent: number;
  reward_points: number;
  reward_percent: number;
};
export type Offers = {
  currency: string;
  coupons: OwnerCoupon[];
  bundles: OwnerBundle[];
  loyalty: LoyaltySettings | null;
  events: ShopEvent[];
  referral: ReferralSettings | null;
  referral_stats: { codes: number; friends: number; delivered: number };
  reminders: { on: boolean; reminded: number; placed: number; value: number };
  rates: Record<string, number>;
  currencies: string[];
};
export type SaleItemIn = {
  product_code: string;
  price: number;
  units: number;
  per_customer: number;
};
export type SaleIn = {
  name: string;
  live: boolean;
  starts_in_minutes: number;
  minutes: number;
  hold_minutes: number;
  items: SaleItemIn[];
};
export type OwnerSale = {
  id: string;
  name: string;
  live: boolean;
  starts_at: string;
  ends_at: string;
  hold_minutes: number;
  state: "upcoming" | "on" | "ended";
  items: {
    number: number;
    product_code: string;
    name: string;
    price: number;
    list_price: number;
    units: number;
    held: number;
    ordered: number;
    left: number;
    per_customer: number;
  }[];
};
export type PostTarget = "instagram" | "facebook" | "whatsapp_catalog" | "whatsapp_status";
export type Publication = {
  id: string;
  kind: "daily" | "product";
  status: "draft" | "sent" | "skipped";
  products: string[];
  captions: Record<string, string>;
  by_model: boolean;
  images: { product: string; post_url: string; status_url: string }[];
  targets: Partial<Record<PostTarget, { status: string; id?: string; error?: string }>>;
  created_at: string;
};
export type Publications = {
  daily: boolean;
  connected: Record<"instagram" | "facebook" | "whatsapp_catalog", boolean>;
  items: Publication[];
};
export type Picture = { media_id: string; url: string };
export type LeadRow = {
  kind: "story_reply" | "story_mention" | "ad" | "link";
  channel: string;
  customer: string;
  text: string;
  title: string;
  url: string;
  at: string;
  ordered: boolean;
};
export type Leads = {
  leads: LeadRow[];
  brought: {
    kind: LeadRow["kind"];
    ref: string;
    title: string;
    chats: number;
    orders: number;
    value: number;
  }[];
};
export type BroadcastResult = {
  text: string;
  product: string | null;
  category: string | null;
  created_at: string;
  sent: number;
  templated: number;
  closed: number;
  orders: number;
  value: number;
  with_product: number;
};
export type Broadcasts = { categories: string[]; template: boolean; results: BroadcastResult[] };
export type BroadcastAbout = { product_code: string | null; category: string | null };
export type Audience = { interested: number; agreed: number };
export type ConsentRow = {
  purpose: "offers" | "fraud_list" | "credit_passport";
  granted: boolean;
  via: string;
  wording: string;
  who: string;
  at: string;
};
export type Consents = {
  offers_yes: number;
  offers_no: number;
  fraud_list: boolean | null;
  credit_passport: boolean | null;
  rows: ConsentRow[];
};
export type GroupPart = { name: string; items: number; paid: boolean | null; organiser: boolean };
export type GroupView = {
  code: string;
  organiser: string;
  city: string;
  closes_at: string;
  open: boolean;
  mine: boolean;
  parts: GroupPart[];
};
export type Waiting = {
  id: string;
  product_code: string;
  name: string;
  customer: string;
  quantity: number;
  status: "waiting" | "missed";
  created_at: string;
  told_at: string | null;
};
export type SavedAnswer = { id: string; question: string; answer: string; created_at: string };
export type ReturnLine = { name: string; quantity: number; to: string | null };
export type ReturnStatus = "requested" | "approved" | "rejected" | "received" | "closed";
export type OwnerReturn = {
  id: string;
  code: string;
  order_code: string;
  customer: string;
  kind: "exchange" | "return";
  reason_code: string;
  reason: string;
  lines: ReturnLine[];
  photo_url: string | null;
  status: ReturnStatus;
  approval_id: string | null;
  exchange_order_code: string | null;
  created_at: string;
  advice: "supports_claim" | "against_claim" | "unclear" | null;
  unboxing: Unboxing | null;
};
/** What the model saw in an unboxing video (feature 36); code turns it into advice. */
export type Unboxing = {
  seal_intact_at_start: boolean | null;
  filmed_without_cut: boolean | null;
  item_matches_catalog: boolean | null;
  item_matches_packed: boolean | null;
  damage_seen: boolean;
  damage: string;
  notes: string;
};
export type ForecastWeek = {
  starts: string;
  money_in: number;
  money_out: number;
  fixed: { category: string; amount: number }[];
  balance: number | null;
  short: boolean;
};
export type CashForecast = {
  usual_in: number;
  usual_out: number;
  cash_now: number | null;
  cash_on: string | null;
  fixed: { category: string; amount: number; day: number }[];
  weeks: ForecastWeek[];
};
export type YearSummary = {
  label: string;
  start: string;
  end: string;
  sales: number;
  udhaar_paid: number;
  expenses: number;
  refunds: number;
  write_offs: number;
  cash_profit: number;
  goods_profit: number;
  orders: number;
  months: { month: string; money_in: number; money_out: number }[];
  expense_categories: Record<string, number>;
  top: { name: string; units: number; revenue: number }[];
  zakat_inputs: { stock: number; stock_without_cost: number; owed_to_shop: number };
};
export type ZakatIn = {
  cash: number;
  stock: number | null;
  owed_to_shop: number | null;
  debts: number;
  nisab: number;
};
export type Zakat = {
  cash: number;
  stock: number;
  stock_without_cost: number;
  owed_to_shop: number;
  debts: number;
  net: number;
  nisab: number;
  due: number;
};
export type StaffPayRow = {
  staff_id: string;
  name: string;
  salary: number;
  commission_bp: number;
  pay_day: number;
  sales: number;
  commission: number;
  due: number;
  paid: number | null;
  paid_at: string | null;
};
export type Passport = {
  code: string;
  made_on: string;
  revoked: boolean;
  totals: Record<string, number>;
};
export type PassportMonth = {
  month: string;
  orders_delivered: number;
  delivered_value: number;
  orders_returned: number;
  paid_matched_to_sms: number;
  paid_approved_by_owner: number;
  cash_at_door: number;
};
export type PublicPassport = {
  statement: {
    code: string;
    shop: string;
    shop_page: string;
    country: string;
    currency: string;
    selling_since: string | null;
    made_on: string;
    months: PassportMonth[];
    totals: Omit<PassportMonth, "month">;
  };
  signature: string;
  public_key: string;
  signed_text: string;
  valid: boolean;
  revoked: boolean;
};
export type SeasonAhead = {
  key: string;
  starts: string;
  ends: string;
  last_year: { starts: string; ends: string } | null;
  lifts: { category: string; units: number; times: number }[];
  usual: string[];
};
export type CoachNote = {
  week: string;
  lines: string[];
  advice: string[];
  by_model: boolean;
  created_at: string;
};
export type ScamKind = "phone" | "wallet" | "url";
export type ScamReportOut = {
  id: string;
  kind: ScamKind;
  value: string;
  posing_as: string;
  source: string;
  status: "pending" | "confirmed" | "dismissed";
  created_at: string;
};
export type ScamToReview = ScamReportOut & { shop: string; evidence: Record<string, unknown> };
export type CallCheck = {
  verdict: "scam" | "suspicious" | "no_signs";
  reading: { posing_as: string; summary: string; numbers: string[] } & Record<string, unknown>;
};
export type Erased = { messages: number; media: number; orders: number; reviews: number };
export type OfficialResult = { official: boolean; channel: string | null; display: string | null };
export type BookingHours = { days: Record<string, string>; seats: number };
export type AppointmentStatus = "booked" | "cancelled" | "done" | "no_show";
export type Appointment = {
  id: string;
  code: string;
  customer: string;
  service: string;
  price: number;
  starts_at: string;
  ends_at: string;
  status: AppointmentStatus;
};
export type Bookings = { hours: BookingHours; coming: Appointment[] };
export type CustomKind = "cake" | "stitching" | "print";
export type CustomRequest = {
  id: string;
  code: string;
  customer: string;
  kind: CustomKind;
  details: {
    text?: string;
    colours?: string[];
    size?: string;
    needed_by?: string;
    notes?: string;
  };
  sketch_url: string | null;
  status: "waiting" | "quoted" | "accepted" | "declined";
  price: number | null;
  note: string;
  created_at: string;
};
export type CourierAccount = {
  courier: string;
  name: string;
  connected: boolean;
  pickup_code: string | null;
};
export type SettlementReport = {
  rows: number;
  matched: number;
  short: string[];
  unknown: string[];
  paid_before: string[];
  delivered: string[];
  returned: string[];
  collected: number;
  paid_out: number;
};
export type Settlement = {
  order: string;
  source: string;
  collected: number | null;
  charges: number | null;
  paid: number | null;
  short: number;
  at: string;
};
export type Settlements = { rows: Settlement[]; paid_total: number; short_total: number };
export type DeliveryProof = {
  id: string;
  url: string;
  matched: boolean | null;
  reading: {
    parcel_visible?: boolean;
    handed_over?: boolean | null;
    codes_seen?: string[];
    damage_seen?: boolean;
    notes?: string;
  } | null;
  at: string;
};
export type PaymentReview = {
  id: string;
  order_code: string;
  customer: string;
  asked: number | null;
  screenshot_amount: number | null;
  provider: string | null;
  transaction_id: string | null;
  sms_amount: number | null;
  signals: PaymentSignal[];
  screenshot_url: string | null;
  created_at: string;
};
export type PaymentWaiting = {
  order_code: string;
  customer: string;
  method: string | null;
  due_now: number | null;
  total: number;
  paid: number;
  placed_at: string | null;
  reminded: boolean;
};
export type PaymentDone = {
  order_code: string;
  amount: number;
  verified_by: "sms" | "owner";
  receipt_code: string | null;
  created_at: string;
};
export type PaymentWallet = {
  name: string;
  kind: "wallet" | "bank";
  read: boolean;
  checked: boolean;
};
export type Payments = {
  forwarding: boolean;
  currency: string;
  wallets: PaymentWallet[];
  share_unread: boolean;
  unread_kept: number;
  reviews: PaymentReview[];
  waiting: PaymentWaiting[];
  recent: PaymentDone[];
};
export type Forwarding = { url: string; header: string; secret: string };
export type SupplierQuote = {
  supplier: string;
  phone: string | null;
  unit_cost: number;
  on: string;
  times: number;
};
export type SupplierItem = {
  variant_id: string;
  name: string;
  left: number | null;
  quotes: SupplierQuote[];
};
export type SupplierReorder = {
  supplier: string | null;
  phone: string | null;
  lines: { name: string; quantity: number; unit_cost: number | null }[];
  total: number | null;
  draft: string;
  link: string | null;
};
export type Suppliers = { compared: SupplierItem[]; reorders: SupplierReorder[] };
export type PriceElsewhere = {
  product_id: string;
  name: string;
  price: number;
  shops: number;
  lowest: number | null;
  middle: number | null;
  highest: number | null;
  place: "below" | "within" | "above" | null;
};
export type SmsParserOut = {
  parser: string;
  provider: string;
  country: string;
  currency: string;
  verified: boolean;
  credits: number;
  last_seen: string | null;
};
export type SmsSampleOut = {
  id: string;
  country: string;
  sender: string;
  text: string;
  seen: number;
  last_seen_at: string;
};
export type SmsFormats = { parsers: SmsParserOut[]; unread: SmsSampleOut[] };
export type ReceiptCheck = {
  payload: ReceiptPayload;
  signature: string;
  public_key: string | null;
  signed_text: string;
  valid: boolean;
};

export type RiskReason = { code: string; points: number; count?: number };
export type OwnerOrder = {
  code: string;
  customer: string;
  status: string;
  method: "cod" | "transfer" | "credit" | null;
  total: number;
  paid: number;
  due_now: number | null;
  to_collect: number;
  risk: { score: number; band: "low" | "medium" | "high"; reasons: RiskReason[] };
  pin: boolean;
  placed_at: string | null;
  next: string[];
  label: boolean;
  shipment: {
    courier: string | null;
    tracking_number: string | null;
    public_code: string;
    courier_charge: number | null;
    return_charge: number | null;
  } | null;
  packing: PackingCheck | null;
  gift: boolean;
  group: string | null;
  group_member: string;
};
export type PackingLine = { name: string; quantity: number; seen: number | null; sure: boolean };
export type PackingCheck = {
  verdict: "match" | "mismatch" | "unclear";
  lines: PackingLine[];
  extra: string[];
  asked_owner: boolean;
  created_at: string;
};
export type PickList = {
  day: string;
  orders: number;
  pieces: number;
  lines: { name: string; quantity: number; orders: string; short: number }[];
  waiting: { code: string; ships_from: string }[];
  grouped: { code: string; group: string; closes_on: string }[];
};
export type Parcel = {
  shop: string;
  shop_slug: string;
  country: string;
  order: string;
  status: string;
  steps: { step: string; at: string }[];
  collect: number;
  courier: string | null;
  tracking_number: string | null;
};

export type Sums = {
  sales: number;
  udhaar_given: number;
  udhaar_paid: number;
  expenses: number;
  orders: number;
  cash_profit: number;
  goods_profit: number;
  write_offs: number;
  refunds: number;
  cost_unknown: number;
};
export type LedgerEntry = {
  id: string;
  kind: "sale" | "expense" | "udhaar_given" | "udhaar_paid" | "write_off" | "refund";
  category: string;
  amount: number;
  description: string;
  occurred_on: string;
  source: string;
  order_code: string | null;
  account: string | null;
  reverses: string | null;
  reversed: boolean;
  has_bill: boolean;
};
export type KhataAccount = {
  id: string;
  name: string;
  balance: number;
  owed_days: number | null;
  reminders_sent: number;
  customer_id: string | null;
};
export type Books = {
  today: Sums;
  week: Sums;
  month: Sums;
  entries: LedgerEntry[];
  accounts: KhataAccount[];
  categories: string[];
};
export type NewExpense = { amount: number; category: string; description: string };
export type UdhaarMove = {
  kind: "udhaar_given" | "udhaar_paid";
  amount: number;
  account_id?: string;
  name?: string;
};
export type LowStock = { name: string; left: number; limit: number };
export type NoticeButton = { id: string; title: string };
export type OwnerNotice = {
  id: string;
  kind: string;
  text: string;
  buttons: NoticeButton[];
  sent: boolean;
  created_at: string;
};
export type RestockLine = {
  variant_id: string;
  quantity: number;
  total_cost?: number | null;
  expires_on?: string | null;
};
export type Restock = {
  lines: RestockLine[];
  supplier?: string;
  supplier_phone?: string;
  source?: "console" | "sheet";
};
export type SheetRow = {
  row: number;
  item: string;
  quantity: number | null;
  total_cost: number | null;
  expires_on: string | null;
  variant_id: string | null;
  name: string | null;
  problem: string | null;
};
export type SheetReading = {
  columns: Record<string, string | null>;
  supplier: string | null;
  rows: SheetRow[];
};
export type Batch = {
  id: string;
  product_id: string;
  name: string;
  quantity: number;
  units_left: number;
  unit_cost: number | null;
  supplier: string | null;
  received_on: string;
  expires_on: string | null;
  status: "open" | "expired" | "discarded";
  price_question: string | null;
  sold: number;
  revenue: number | null;
  cost_sold: number | null;
  profit: number | null;
};
export type DiscardReason = "expired" | "damaged" | "lost";
export type DashboardDays = 1 | 7 | 30;
export type Usage = { calls: number; failed: number; tokens: number; cost_millipaisa: number };
export type Dashboard = {
  period: { days: DashboardDays; start: string; end: string };
  orders: {
    placed: number;
    value: number;
    open: Record<string, number>;
    closed: Record<string, number>;
    sources: Record<string, number>;
  } | null;
  money: {
    received: number;
    payments: number;
    awaiting: number;
    awaiting_orders: number;
    to_review: number;
    at_doors: number;
  } | null;
  fraud: {
    screenshots: number;
    flagged: number;
    reused: number;
    rejected: number;
    risky_orders: number;
    advances: number;
  } | null;
  books: {
    sales: number;
    expenses: number;
    cash_profit: number;
    goods_profit: number;
    write_offs: number;
    udhaar_owed: number;
  } | null;
  ai:
    | (Usage & {
        live: boolean;
        per_order_millipaisa: number | null;
        efforts: Record<string, number>;
        endpoints: { name: string; answered: number; failed: number }[];
        jobs: (Usage & { job: string })[];
      })
    | null;
  daily: { day: string; orders: number; sales: number | null }[] | null;
  costs: (Usage & { code: string; status: string; total: number; placed_at: string })[] | null;
};
export type ProductProfit = {
  product_id: string;
  name: string;
  sold: number;
  revenue: number;
  cost: number;
  profit: number;
  cost_unknown: number;
  delivery_fees: number;
  courier: number;
  returns: number;
  parcels_unknown: number;
  true_profit: number;
};
export type Approval = { id: string; kind: string; text: string; created_at: string };
export type OwnerCustomer = {
  id: string;
  name: string | null;
  verified: boolean;
  verified_by: "rule" | "owner" | null;
  orders: number;
  delivered: number;
  returned: number;
  owes: number;
  reported: FraudStatus | null;
  listed_by: number;
  spent: number; // lifetime value, minor units (feature 59)
  first_order: string | null;
  last_order: string | null;
  segment: Segment;
  open_chat: boolean;
  phone: string | null;
};
export type Segment = "new" | "repeat" | "loyal" | "quiet" | "none";
/** A busy day of simulated customers in a demo copy (feature 110). */
export type BusyDayRun = {
  mode: "live" | "replay";
  customers: number;
  started_at: string;
  finished_at: string | null;
  active: number;
  ordered: number;
  handover: number;
  left: number;
  stuck: number;
  messages: number;
};
export type BusyDay = { mode: "live" | "replay" | null; run: BusyDayRun | null };
export type CustomerSummary = {
  buyers: number;
  came_back: number;
  repeat_rate: number | null;
  quiet: number;
  average_spent: number;
  greeting: string;
};
/** Fraud reports and the shared list (feature 37). */
export type FraudReason = "fake_payment" | "refused_cod" | "scam" | "abuse";
export type FraudStatus = "pending" | "confirmed" | "rejected" | "withdrawn";
export type FraudReport = {
  id: string;
  customer_id: string | null;
  name: string | null;
  reason: FraudReason;
  note: string;
  status: FraudStatus;
  review_note: string | null;
  created_at: string;
};
export type FraudToReview = {
  id: string;
  shop: string;
  reason: FraudReason;
  note: string;
  evidence: {
    orders?: { code: string; status: string; total: number; method: string | null }[];
    rejected_payments?: number;
    signals?: string[];
  };
  others: number;
  created_at: string;
};
export type FraudReview = {
  pending: FraudToReview[];
  listed: { id: string; ref: string; reasons: FraudReason[]; shops: number; listed_at: string }[];
};
/** Reviews from verified buyers (feature 46) and the trust page's numbers (feature 45). */
export type ShopReview = {
  id: string;
  rating: number;
  text: string;
  name: string | null;
  items: string[];
  reply: string | null;
  created_at: string;
};
export type ShopReviews = { average: number | null; count: number; reviews: ShopReview[] };
export type ReviewForm = {
  shop: string;
  code: string;
  items: string[];
  delivered: boolean;
  review: ShopReview | null;
};
export type TrustStats = {
  since: string;
  delivered: number;
  return_rate: number | null;
  reply_minutes: number | null;
  rating: number | null;
  reviews: number;
};
export type ShopSettings = {
  autonomy: Record<string, string>;
  choices: Record<string, string[]>;
  verified_after: number;
  low_stock_limit: number;
  ask_discount_over: number;
};
export type SettingsChange = {
  autonomy?: Record<string, string>;
  verified_after?: number;
  low_stock_limit?: number;
};
export type Member = {
  id: string;
  name: string;
  role: string;
  active: boolean;
  whatsapp: boolean;
  branch_id?: string | null;
  rights: string[];
  created_at: string;
};
export type Invite = { member: Member; link: string };
export type ActivityEntry = {
  seq: number;
  at: string;
  actor: string;
  action: string;
  entity: string | null;
  data: Record<string, unknown>;
};
export type ActivityLog = {
  intact: boolean;
  checked: number;
  broken_at: number | null;
  entries: ActivityEntry[];
};
export type WhyDecision = {
  topic: "risk" | "advance" | "price" | "payment";
  outcome: string;
  rule: string;
  values: Record<string, unknown>;
  evidence: Record<string, unknown>[];
};
export type WhyHistory = {
  at: string;
  action: string;
  actor: string;
  data: Record<string, unknown>;
};
export type Why = { code: string; status: string; decisions: WhyDecision[]; history: WhyHistory[] };

/** Who is chatting: a customer with a random chat token, or the owner with a signed session. */
export type ChatAuth = { token: string; owner: boolean };

function authHeaders(auth: ChatAuth): Record<string, string> {
  return auth.owner ? { Authorization: `Bearer ${auth.token}` } : { "X-Chat-Token": auth.token };
}

const bearer = (token: string) => ({ Authorization: `Bearer ${token}` });

type Init = Omit<RequestInit, "headers"> & { headers?: Record<string, string> };

/** A PDF the console downloads with the owner's token: a shipping or return label. */
async function pdf(token: string, path: string): Promise<Blob> {
  const response = await fetch(API_BASE + path, { headers: bearer(token) });
  if (!response.ok) throw new ApiError(response.status, `http_${response.status}`, "No label");
  return response.blob();
}

/** A file the owner downloads (a workbook, a data copy, a report). */
async function blob(token: string, path: string): Promise<Blob> {
  const response = await fetch(API_BASE + path, { headers: bearer(token) });
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as { error?: { code?: string } } | null;
    throw new ApiError(response.status, body?.error?.code ?? `http_${response.status}`, "No file");
  }
  return response.blob();
}

async function call<T>(path: string, init?: Init): Promise<T> {
  // JSON bodies say so; a FormData body lets the browser set its multipart boundary.
  const json: Record<string, string> =
    typeof init?.body === "string" ? { "Content-Type": "application/json" } : {};
  const response = await fetch(API_BASE + path, {
    ...init,
    headers: { ...json, ...init?.headers },
  });
  if (!response.ok) {
    let body: { error?: { code?: string; message?: string } } | undefined;
    try {
      body = await response.json();
    } catch {
      body = undefined;
    }
    throw new ApiError(
      response.status,
      body?.error?.code ?? `http_${response.status}`,
      body?.error?.message ?? response.statusText,
    );
  }
  return (await response.json()) as T;
}

const shopPath = (slug: string) => `/api/shops/${encodeURIComponent(slug)}`;

export async function ping(timeoutMs: number): Promise<boolean> {
  try {
    const response = await fetch(`${API_BASE}/health`, {
      signal: AbortSignal.timeout(timeoutMs),
      cache: "no-store",
    });
    return response.ok;
  } catch {
    return false;
  }
}

export type Coordinates = { latitude: number; longitude: number };
export type ChatText = {
  client_id: string;
  text: string;
  button_id?: string;
  location?: Coordinates;
};

export const api = {
  shop: (slug: string) => call<Shop>(shopPath(slug)),
  products: (slug: string) => call<PublicProduct[]>(`${shopPath(slug)}/products`),
  trust: (slug: string) => call<TrustInfo>(`${shopPath(slug)}/trust`),
  checkOfficial: (slug: string, value: string) =>
    call<OfficialResult>(`${shopPath(slug)}/trust/check?value=${encodeURIComponent(value)}`),
  quote: (
    slug: string,
    lines: { item: string; quantity: number }[],
    city: string,
    coupon: string | null = null,
  ) =>
    call<Quote>(`${shopPath(slug)}/checkout/quote`, {
      method: "POST",
      body: JSON.stringify({ lines, city: city.trim() || null, coupon }),
    }),
  checkout: (slug: string, chatToken: string, order: CheckoutRequest) =>
    call<Placed>(`${shopPath(slug)}/checkout`, {
      method: "POST",
      headers: { "X-Chat-Token": chatToken },
      body: JSON.stringify(order),
    }),
  group: (slug: string, code: string, chatToken: string | null) =>
    call<GroupView>(`${shopPath(slug)}/groups/${encodeURIComponent(code)}`, {
      headers: chatToken ? { "X-Chat-Token": chatToken } : {},
    }),
  closeGroup: (slug: string, code: string, chatToken: string) =>
    call<GroupView>(`${shopPath(slug)}/groups/${encodeURIComponent(code)}/close`, {
      method: "POST",
      headers: { "X-Chat-Token": chatToken },
    }),
  startChat: (slug: string) =>
    call<{ token: string }>(`${shopPath(slug)}/chat/session`, { method: "POST" }),
  postChat: (slug: string, auth: ChatAuth, message: ChatText) =>
    call<{ accepted: boolean }>(`${shopPath(slug)}/chat/messages`, {
      method: "POST",
      headers: auth.owner ? authHeaders(auth) : {},
      body: JSON.stringify(auth.owner ? message : { ...message, token: auth.token }),
    }),
  postMedia: (slug: string, auth: ChatAuth, clientId: string, file: Blob, caption?: string) => {
    const form = new FormData();
    form.append("file", file, file instanceof File ? file.name : "upload");
    form.append("client_id", clientId);
    if (caption) form.append("caption", caption);
    return call<{ accepted: boolean }>(`${shopPath(slug)}/chat/media`, {
      method: "POST",
      headers: authHeaders(auth),
      body: form,
    });
  },
  listChat: (slug: string, auth: ChatAuth, after?: string) => {
    const query = after ? `?${new URLSearchParams({ after })}` : "";
    return call<ServerMessage[]>(`${shopPath(slug)}/chat/messages${query}`, {
      headers: authHeaders(auth),
    });
  },
  tryAsOwner: (slug: string) =>
    call<{ token: string; slug: string }>("/api/owner/demo", {
      method: "POST",
      body: JSON.stringify({ slug }),
    }),
  me: (token: string) => call<OwnerMe>("/api/owner/me", { headers: bearer(token) }),
  ownerProducts: (token: string) =>
    call<OwnerProduct[]>("/api/owner/products", { headers: bearer(token) }),
  inbox: (token: string, show: InboxShow, channel: string) => {
    const query = new URLSearchParams({ show, ...(channel ? { channel } : {}) });
    return call<Inbox>(`/api/owner/inbox?${query}`, { headers: bearer(token) });
  },
  takeChat: (token: string, conversationId: string) =>
    call<InboxItem>(`/api/owner/inbox/${encodeURIComponent(conversationId)}/take`, {
      method: "POST",
      headers: bearer(token),
    }),
  giveChat: (token: string, conversationId: string, staffId: string | null) =>
    call<InboxItem>(`/api/owner/inbox/${encodeURIComponent(conversationId)}/assign`, {
      method: "POST",
      headers: bearer(token),
      body: JSON.stringify({ staff_id: staffId }),
    }),
  posts: (token: string) => call<Posts>("/api/owner/posts", { headers: bearer(token) }),
  setPostProduct: (token: string, postId: string, productId: string | null) =>
    call<{ answering: number }>(`/api/owner/posts/${encodeURIComponent(postId)}/product`, {
      method: "PUT",
      headers: bearer(token),
      body: JSON.stringify({ product_id: productId }),
    }),
  catalogSync: (token: string) =>
    call<CatalogSync>("/api/owner/catalog-sync", { headers: bearer(token) }),
  sendCatalog: (token: string) =>
    call<{ queued: boolean }>("/api/owner/catalog-sync", {
      method: "POST",
      headers: bearer(token),
    }),
  chatMessages: (token: string, conversationId: string) =>
    call<ConsoleMessage[]>(
      `/api/owner/conversations/${encodeURIComponent(conversationId)}/messages`,
      { headers: bearer(token) },
    ),
  staffReply: (token: string, conversationId: string, text: string) =>
    call<{ sent: boolean }>(
      `/api/owner/conversations/${encodeURIComponent(conversationId)}/reply`,
      {
        method: "POST",
        headers: bearer(token),
        body: JSON.stringify({ text }),
      },
    ),
  payments: (token: string) => call<Payments>("/api/owner/payments", { headers: bearer(token) }),
  decidePayment: (token: string, claimId: string, answer: "approve" | "reject") =>
    call<{ outcome: string; receipt_code: string | null }>(
      `/api/owner/payments/${encodeURIComponent(claimId)}/${answer}`,
      { method: "POST", headers: bearer(token) },
    ),
  shareUnreadSms: (token: string, share: boolean) =>
    call<{ share: boolean }>("/api/owner/sms-samples", {
      method: "PUT",
      headers: bearer(token),
      body: JSON.stringify({ share }),
    }),
  smsFormats: (token: string) =>
    call<SmsFormats>("/api/platform/sms-formats", { headers: bearer(token) }),
  smsFormatHandled: (token: string, id: string) =>
    call<{ handled: boolean }>(`/api/platform/sms-formats/${encodeURIComponent(id)}/handled`, {
      method: "POST",
      headers: bearer(token),
    }),
  setUpForwarding: (token: string) =>
    call<Forwarding>("/api/owner/sms-forwarding", { method: "POST", headers: bearer(token) }),
  orders: (token: string) => call<OwnerOrder[]>("/api/owner/orders", { headers: bearer(token) }),
  moveOrder: (
    token: string,
    code: string,
    to: string,
    courier = "",
    tracking = "",
    charge: number | null = null,
  ) =>
    call<OwnerOrder>(`/api/owner/orders/${encodeURIComponent(code)}/status`, {
      method: "POST",
      headers: bearer(token),
      body: JSON.stringify({ to, courier, tracking, charge }),
    }),
  setCharges: (token: string, code: string, there: number | null, back: number | null) =>
    call<unknown>(`/api/owner/orders/${encodeURIComponent(code)}/charges`, {
      method: "PUT",
      headers: bearer(token),
      body: JSON.stringify({ there, back }),
    }),
  packingCheck: (token: string, code: string, file: File) => {
    const form = new FormData();
    form.append("file", file);
    return call<PackingCheck>(`/api/owner/orders/${encodeURIComponent(code)}/packing-check`, {
      method: "POST",
      headers: bearer(token),
      body: form,
    });
  },
  pickList: (token: string) => call<PickList>("/api/owner/pick-list", { headers: bearer(token) }),
  pickListPdf: (token: string) => pdf(token, "/api/owner/pick-list.pdf"),
  giftCardPdf: (token: string, code: string) =>
    pdf(token, `/api/owner/orders/${encodeURIComponent(code)}/gift-card.pdf`),
  label: (token: string, code: string) =>
    pdf(token, `/api/owner/orders/${encodeURIComponent(code)}/label.pdf`),
  returnLabel: (token: string, returnId: string) =>
    pdf(token, `/api/owner/returns/${encodeURIComponent(returnId)}/label.pdf`),
  refunds: (token: string) => call<Refunds>("/api/owner/refunds", { headers: bearer(token) }),
  refundProof: (token: string, refundId: string, file: Blob) => {
    const form = new FormData();
    form.append("file", file);
    return call<RefundResult>(`/api/owner/refunds/${encodeURIComponent(refundId)}/proof`, {
      method: "POST",
      headers: bearer(token),
      body: form,
    });
  },
  recordRefund: (token: string, refundId: string) =>
    call<RefundResult>(`/api/owner/refunds/${encodeURIComponent(refundId)}/record`, {
      method: "POST",
      headers: bearer(token),
    }),
  returns: (token: string) => call<OwnerReturn[]>("/api/owner/returns", { headers: bearer(token) }),
  offers: (token: string) => call<Offers>("/api/owner/offers", { headers: bearer(token) }),
  addCoupon: (token: string, coupon: CouponIn) =>
    call<{ code: string }>("/api/owner/offers/coupons", {
      method: "POST",
      headers: bearer(token),
      body: JSON.stringify(coupon),
    }),
  addBundle: (token: string, bundle: { name: string; product_codes: string[]; off: number }) =>
    call<OwnerBundle>("/api/owner/offers/bundles", {
      method: "POST",
      headers: bearer(token),
      body: JSON.stringify(bundle),
    }),
  switchOff: (token: string, kind: "coupons" | "bundles", id: string) =>
    call<{ active: boolean }>(`/api/owner/offers/${kind}/${encodeURIComponent(id)}/off`, {
      method: "POST",
      headers: bearer(token),
    }),
  setLoyalty: (token: string, loyalty: LoyaltySettings) =>
    call<LoyaltySettings>("/api/owner/offers/loyalty", {
      method: "PUT",
      headers: bearer(token),
      body: JSON.stringify(loyalty),
    }),
  setEvents: (token: string, events: ShopEvent[]) =>
    call<ShopEvent[]>("/api/owner/offers/events", {
      method: "PUT",
      headers: bearer(token),
      body: JSON.stringify(events),
    }),
  setReferral: (token: string, referral: ReferralSettings) =>
    call<ReferralSettings>("/api/owner/offers/referral", {
      method: "PUT",
      headers: bearer(token),
      body: JSON.stringify(referral),
    }),
  setCartReminders: (token: string, on: boolean) =>
    call<{ on: boolean }>("/api/owner/offers/cart-reminders", {
      method: "PUT",
      headers: bearer(token),
      body: JSON.stringify({ on }),
    }),
  setRates: (token: string, rates: Record<string, number>) =>
    call<{ rates: Record<string, number> }>("/api/owner/offers/rates", {
      method: "PUT",
      headers: bearer(token),
      body: JSON.stringify({ rates }),
    }),
  broadcasts: (token: string) =>
    call<Broadcasts>("/api/owner/broadcasts", { headers: bearer(token) }),
  audience: (token: string, about: BroadcastAbout) =>
    call<Audience>("/api/owner/broadcasts/audience", {
      method: "POST",
      headers: bearer(token),
      body: JSON.stringify(about),
    }),
  broadcast: (token: string, about: BroadcastAbout, text: string) =>
    call<Audience>("/api/owner/broadcasts", {
      method: "POST",
      headers: bearer(token),
      body: JSON.stringify({ ...about, text }),
    }),
  consents: (token: string) => call<Consents>("/api/owner/consents", { headers: bearer(token) }),
  sales: (token: string) => call<OwnerSale[]>("/api/owner/sales", { headers: bearer(token) }),
  startSale: (token: string, sale: SaleIn) =>
    call<OwnerSale>("/api/owner/sales", {
      method: "POST",
      headers: bearer(token),
      body: JSON.stringify(sale),
    }),
  stopSale: (token: string, id: string) =>
    call<OwnerSale>(`/api/owner/sales/${encodeURIComponent(id)}/stop`, {
      method: "POST",
      headers: bearer(token),
    }),
  publications: (token: string) =>
    call<Publications>("/api/owner/publications", { headers: bearer(token) }),
  setDailyPost: (token: string, on: boolean) =>
    call<{ on: boolean }>("/api/owner/publications/daily", {
      method: "PUT",
      headers: bearer(token),
      body: JSON.stringify({ on }),
    }),
  postToday: (token: string) =>
    call<Publication>("/api/owner/publications/today", {
      method: "POST",
      headers: bearer(token),
    }),
  postProduct: (token: string, code: string) =>
    call<Publication>("/api/owner/publications/product", {
      method: "POST",
      headers: bearer(token),
      body: JSON.stringify({ product_code: code }),
    }),
  sendPublication: (token: string, id: string, targets: PostTarget[]) =>
    call<{ status: string }>(`/api/owner/publications/${encodeURIComponent(id)}/send`, {
      method: "POST",
      headers: bearer(token),
      body: JSON.stringify({ targets }),
    }),
  skipPublication: (token: string, id: string) =>
    call<{ status: string }>(`/api/owner/publications/${encodeURIComponent(id)}/skip`, {
      method: "POST",
      headers: bearer(token),
    }),
  cleanPhoto: (token: string, code: string) =>
    call<Picture>(`/api/owner/products/${encodeURIComponent(code)}/photo/clean`, {
      method: "POST",
      headers: bearer(token),
    }),
  usePhoto: (token: string, code: string, mediaId: string) =>
    call<Picture>(`/api/owner/products/${encodeURIComponent(code)}/photo`, {
      method: "PUT",
      headers: bearer(token),
      body: JSON.stringify({ media_id: mediaId }),
    }),
  productCard: (token: string, code: string, shape: "square" | "post" | "status") =>
    call<Picture>(
      `/api/owner/products/${encodeURIComponent(code)}/card?${new URLSearchParams({ shape })}`,
      { method: "POST", headers: bearer(token) },
    ),
  leads: (token: string) => call<Leads>("/api/owner/leads", { headers: bearer(token) }),
  shopConsent: (token: string, purpose: "fraud_list" | "credit_passport", granted: boolean) =>
    call<Consents>("/api/owner/consents/shop", {
      method: "POST",
      headers: bearer(token),
      body: JSON.stringify({ purpose, granted }),
    }),
  waitlist: (token: string) => call<Waiting[]>("/api/owner/waitlist", { headers: bearer(token) }),
  answers: (token: string) => call<SavedAnswer[]>("/api/owner/answers", { headers: bearer(token) }),
  removeAnswer: (token: string, id: string) =>
    call<{ removed: boolean }>(`/api/owner/answers/${encodeURIComponent(id)}`, {
      method: "DELETE",
      headers: bearer(token),
    }),
  returnReceived: (token: string, returnId: string) =>
    call<{ code: string; refund: number }>(
      `/api/owner/returns/${encodeURIComponent(returnId)}/received`,
      { method: "POST", headers: bearer(token) },
    ),
  track: (code: string) => call<Parcel>(`/api/track/${encodeURIComponent(code)}`),
  receipt: (code: string) => call<ReceiptCheck>(`/api/receipts/${encodeURIComponent(code)}`),
  resumeHandover: (token: string, handoverId: string) =>
    call<{ resumed: boolean }>(`/api/owner/handovers/${encodeURIComponent(handoverId)}/resume`, {
      method: "POST",
      headers: bearer(token),
    }),
  books: (token: string) => call<Books>("/api/owner/books", { headers: bearer(token) }),
  addExpense: (token: string, expense: NewExpense) =>
    call<{ id: string }>("/api/owner/books/expenses", {
      method: "POST",
      headers: bearer(token),
      body: JSON.stringify(expense),
    }),
  reverseEntry: (token: string, entryId: string) =>
    call<{ id: string }>(`/api/owner/books/entries/${encodeURIComponent(entryId)}/reverse`, {
      method: "POST",
      headers: bearer(token),
    }),
  moveUdhaar: (token: string, move: UdhaarMove) =>
    call<KhataAccount>("/api/owner/udhaar", {
      method: "POST",
      headers: bearer(token),
      body: JSON.stringify(move),
    }),
  lowStock: (token: string) => call<LowStock[]>("/api/owner/low-stock", { headers: bearer(token) }),
  approvals: (token: string) =>
    call<Approval[]>("/api/owner/approvals", { headers: bearer(token) }),
  answerApproval: (token: string, approvalId: string, approve: boolean) =>
    call<{ outcome: string }>(`/api/owner/approvals/${encodeURIComponent(approvalId)}`, {
      method: "POST",
      headers: bearer(token),
      body: JSON.stringify({ approve }),
    }),
  customers: (token: string) =>
    call<OwnerCustomer[]>("/api/owner/customers", { headers: bearer(token) }),
  pushKey: (token: string) =>
    call<{ public_key: string | null }>("/api/owner/push/key", { headers: bearer(token) }),
  pushOn: (token: string, subscription: PushSubscriptionJSON) =>
    call<{ on: boolean }>("/api/owner/push", {
      method: "POST",
      headers: bearer(token),
      body: JSON.stringify(subscription),
    }),
  pushOff: (token: string, endpoint: string) =>
    call<{ on: boolean }>("/api/owner/push/off", {
      method: "POST",
      headers: bearer(token),
      body: JSON.stringify({ endpoint }),
    }),
  pushTest: (token: string, text: string, lang: string) =>
    call<{ queued: boolean }>("/api/owner/push/test", {
      method: "POST",
      headers: bearer(token),
      body: JSON.stringify({ text, lang }),
    }),
  busyDay: (token: string) => call<BusyDay>("/api/owner/busy-day", { headers: bearer(token) }),
  startBusyDay: (token: string, customers: 10 | 20 | 50) =>
    call<BusyDay>("/api/owner/busy-day", {
      method: "POST",
      headers: bearer(token),
      body: JSON.stringify({ customers }),
    }),
  customerSummary: (token: string) =>
    call<CustomerSummary>("/api/owner/customers/summary", { headers: bearer(token) }),
  greet: (token: string, text: string, segment: Segment | "all") =>
    call<{ sent: number; closed: number; recent: number }>("/api/owner/customers/greet", {
      method: "POST",
      headers: bearer(token),
      body: JSON.stringify({ text, segment }),
    }),
  setVerified: (token: string, customerId: string, verified: boolean) =>
    call<{ verified: boolean }>(`/api/owner/customers/${encodeURIComponent(customerId)}/verified`, {
      method: "PUT",
      headers: bearer(token),
      body: JSON.stringify({ verified }),
    }),
  settings: (token: string) =>
    call<ShopSettings>("/api/owner/settings", { headers: bearer(token) }),
  saveSettings: (token: string, change: SettingsChange) =>
    call<ShopSettings>("/api/owner/settings", {
      method: "PUT",
      headers: bearer(token),
      body: JSON.stringify(change),
    }),
  team: (token: string) => call<Member[]>("/api/owner/team", { headers: bearer(token) }),
  addMember: (token: string, member: { name: string; role: string; phone?: string }) =>
    call<Invite>("/api/owner/team", {
      method: "POST",
      headers: bearer(token),
      body: JSON.stringify(member),
    }),
  memberLink: (token: string, staffId: string) =>
    call<Invite>(`/api/owner/team/${encodeURIComponent(staffId)}/link`, {
      method: "POST",
      headers: bearer(token),
    }),
  removeMember: (token: string, staffId: string) =>
    call<Member>(`/api/owner/team/${encodeURIComponent(staffId)}/deactivate`, {
      method: "POST",
      headers: bearer(token),
    }),
  signOutAll: (token: string) =>
    call<{ signed_out: boolean }>("/api/owner/sign-out-all", {
      method: "POST",
      headers: bearer(token),
    }),
  activity: (token: string) => call<ActivityLog>("/api/owner/activity", { headers: bearer(token) }),
  why: (token: string, code: string) =>
    call<Why>(`/api/owner/orders/${encodeURIComponent(code)}/why`, { headers: bearer(token) }),
  notices: (token: string) => call<OwnerNotice[]>("/api/owner/notices", { headers: bearer(token) }),
  noticeSeen: (token: string, noticeId: string) =>
    call<{ seen: boolean }>(`/api/owner/notices/${encodeURIComponent(noticeId)}/seen`, {
      method: "POST",
      headers: bearer(token),
    }),
  tapNotice: (token: string, noticeId: string, buttonId: string) =>
    call<{ replies: string[] }>(`/api/owner/notices/${encodeURIComponent(noticeId)}/tap`, {
      method: "POST",
      headers: bearer(token),
      body: JSON.stringify({ button_id: buttonId }),
    }),
  answerPrice: (token: string, batchId: string, choice: "keep" | "now" | "later", price?: number) =>
    call<{ name: string; old: number; new: number | null }>(
      `/api/owner/price-questions/${encodeURIComponent(batchId)}`,
      {
        method: "POST",
        headers: bearer(token),
        body: JSON.stringify({ choice, price: price ?? null }),
      },
    ),
  restock: (token: string, restock: Restock) =>
    call<{ batches: number }>("/api/owner/restock", {
      method: "POST",
      headers: bearer(token),
      body: JSON.stringify(restock),
    }),
  readSheet: (token: string, file: File) => {
    const form = new FormData();
    form.append("file", file);
    return call<SheetReading>("/api/owner/restock/sheet", {
      method: "POST",
      headers: bearer(token),
      body: form,
    });
  },
  batches: (token: string) => call<Batch[]>("/api/owner/batches", { headers: bearer(token) }),
  suppliers: (token: string) => call<Suppliers>("/api/owner/suppliers", { headers: bearer(token) }),
  priceCheck: (token: string) =>
    call<PriceElsewhere[]>("/api/owner/price-check", { headers: bearer(token) }),
  setExpiry: (token: string, batchId: string, expiresOn: string) =>
    call<{ expires_on: string }>(`/api/owner/batches/${encodeURIComponent(batchId)}/expiry`, {
      method: "POST",
      headers: bearer(token),
      body: JSON.stringify({ expires_on: expiresOn }),
    }),
  discardBatch: (token: string, batchId: string, reason: DiscardReason) =>
    call<{ name: string; units: number; cost: number }>(
      `/api/owner/batches/${encodeURIComponent(batchId)}/discard`,
      { method: "POST", headers: bearer(token), body: JSON.stringify({ reason }) },
    ),
  profit: (token: string) => call<ProductProfit[]>("/api/owner/profit", { headers: bearer(token) }),
  dashboard: (token: string, days: DashboardDays) =>
    call<Dashboard>(`/api/owner/dashboard?days=${days}`, { headers: bearer(token) }),
  saveWording: (
    token: string,
    productId: string,
    lang: string,
    text: { name: string; description: string },
  ) =>
    call<{ saved: boolean }>(
      `/api/owner/products/${encodeURIComponent(productId)}/translations/${encodeURIComponent(lang)}`,
      { method: "PUT", headers: bearer(token), body: JSON.stringify(text) },
    ),
  imports: (token: string) => call<Imports>("/api/owner/imports", { headers: bearer(token) }),
  importSheet: (token: string, file: File) => {
    const form = new FormData();
    form.append("file", file);
    return call<ImportRun>("/api/owner/imports/sheet", {
      method: "POST",
      headers: bearer(token),
      body: form,
    });
  },
  importVideo: (token: string, file: File) => {
    const form = new FormData();
    form.append("file", file);
    return call<{ queued: boolean }>("/api/owner/imports/video", {
      method: "POST",
      headers: bearer(token),
      body: form,
    });
  },
  importFrom: (token: string, source: "instagram" | "meta-catalog") =>
    call<{ queued: boolean }>(`/api/owner/imports/${source}`, {
      method: "POST",
      headers: bearer(token),
    }),
  addDraft: (token: string, draftId: string, edits: { name?: string; price?: number }) =>
    call<{ code: string; name: string }>(
      `/api/owner/imports/drafts/${encodeURIComponent(draftId)}/add`,
      { method: "POST", headers: bearer(token), body: JSON.stringify(edits) },
    ),
  dropDraft: (token: string, draftId: string) =>
    call<{ dropped: boolean }>(`/api/owner/imports/drafts/${encodeURIComponent(draftId)}/drop`, {
      method: "POST",
      headers: bearer(token),
    }),
  addReady: (token: string, importId: string) =>
    call<{ added: number }>(`/api/owner/imports/${encodeURIComponent(importId)}/add-ready`, {
      method: "POST",
      headers: bearer(token),
    }),
  dropRest: (token: string, importId: string) =>
    call<{ dropped: number }>(`/api/owner/imports/${encodeURIComponent(importId)}/drop-rest`, {
      method: "POST",
      headers: bearer(token),
    }),
  countShelf: (token: string, file: Blob, ephemeral = false) => {
    const form = new FormData();
    form.append("file", file);
    const path = ephemeral
      ? "/api/owner/stock/count/photo?ephemeral=true"
      : "/api/owner/stock/count/photo";
    return call<ShelfCount>(path, {
      method: "POST",
      headers: bearer(token),
      body: form,
    });
  },
  saveCount: (token: string, lines: { variant_id: string; counted: number }[]) =>
    call<{ changed: number }>("/api/owner/stock/count", {
      method: "POST",
      headers: bearer(token),
      body: JSON.stringify({ lines }),
    }),
  sizeCharts: (token: string) =>
    call<SizeCharts>("/api/owner/size-charts", { headers: bearer(token) }),
  saveSizeCharts: (token: string, charts: SizeChart[]) =>
    call<SizeCharts>("/api/owner/size-charts", {
      method: "PUT",
      headers: bearer(token),
      body: JSON.stringify({ charts }),
    }),
  reportFraud: (token: string, customerId: string, reason: FraudReason, note: string) =>
    call<FraudReport>(`/api/owner/customers/${encodeURIComponent(customerId)}/fraud`, {
      method: "POST",
      headers: bearer(token),
      body: JSON.stringify({ reason, note }),
    }),
  fraudReports: (token: string) =>
    call<FraudReport[]>("/api/owner/fraud-reports", { headers: bearer(token) }),
  withdrawReport: (token: string, reportId: string) =>
    call<{ status: string }>(`/api/owner/fraud-reports/${encodeURIComponent(reportId)}/withdraw`, {
      method: "POST",
      headers: bearer(token),
    }),
  fraudReview: (token: string) =>
    call<FraudReview>("/api/platform/fraud", { headers: bearer(token) }),
  decideReport: (token: string, reportId: string, confirm: boolean, note: string) =>
    call<{ status: string }>(`/api/platform/fraud/reports/${encodeURIComponent(reportId)}`, {
      method: "POST",
      headers: bearer(token),
      body: JSON.stringify({ confirm, note }),
    }),
  unlist: (token: string, entryId: string, reason: string) =>
    call<{ status: string }>(`/api/platform/fraud/list/${encodeURIComponent(entryId)}/remove`, {
      method: "POST",
      headers: bearer(token),
      body: JSON.stringify({ reason }),
    }),
  shopReviews: (slug: string) => call<ShopReviews>(`${shopPath(slug)}/reviews`),
  trustStats: (slug: string) => call<TrustStats>(`${shopPath(slug)}/trust/stats`),
  reviewForm: (slug: string, code: string, signature: string) =>
    call<ReviewForm>(
      `${shopPath(slug)}/review/${encodeURIComponent(code)}?s=${encodeURIComponent(signature)}`,
    ),
  sendReview: (
    slug: string,
    code: string,
    signature: string,
    review: { rating: number; text: string; lang: string },
  ) =>
    call<ReviewForm>(
      `${shopPath(slug)}/review/${encodeURIComponent(code)}?s=${encodeURIComponent(signature)}`,
      { method: "POST", body: JSON.stringify(review) },
    ),
  bookings: (token: string) => call<Bookings>("/api/owner/bookings", { headers: bearer(token) }),
  saveBookingHours: (token: string, hours: BookingHours) =>
    call<BookingHours>("/api/owner/bookings/hours", {
      method: "PUT",
      headers: bearer(token),
      body: JSON.stringify(hours),
    }),
  markBooking: (token: string, id: string, to: "done" | "no_show" | "cancelled") =>
    call<Appointment>(`/api/owner/bookings/${encodeURIComponent(id)}/status`, {
      method: "POST",
      headers: bearer(token),
      body: JSON.stringify({ to }),
    }),
  customRequests: (token: string) =>
    call<CustomRequest[]>("/api/owner/custom", { headers: bearer(token) }),
  quoteCustom: (token: string, id: string, price: number, note: string) =>
    call<{ status: string }>(`/api/owner/custom/${encodeURIComponent(id)}/quote`, {
      method: "POST",
      headers: bearer(token),
      body: JSON.stringify({ price, note }),
    }),
  couriers: (token: string) =>
    call<CourierAccount[]>("/api/owner/couriers", { headers: bearer(token) }),
  connectCourier: (token: string, courier: string, secret: string, pickupCode: string) =>
    call<CourierAccount>(`/api/owner/couriers/${encodeURIComponent(courier)}`, {
      method: "PUT",
      headers: bearer(token),
      body: JSON.stringify({ token: secret, pickup_code: pickupCode }),
    }),
  bookParcel: (token: string, code: string, courier: string) =>
    call<{ status: string; tracking: string | null }>(
      `/api/owner/orders/${encodeURIComponent(code)}/book`,
      { method: "POST", headers: bearer(token), body: JSON.stringify({ courier }) },
    ),
  settlements: (token: string) =>
    call<Settlements>("/api/owner/settlements", { headers: bearer(token) }),
  uploadSettlement: (token: string, file: File) => {
    const form = new FormData();
    form.append("file", file);
    return call<SettlementReport>("/api/owner/settlements", {
      method: "POST",
      headers: bearer(token),
      body: form,
    });
  },
  deliveryProofs: (token: string, code: string) =>
    call<DeliveryProof[]>(`/api/owner/orders/${encodeURIComponent(code)}/delivery-proof`, {
      headers: bearer(token),
    }),
  addDeliveryProof: (token: string, code: string, file: Blob) => {
    const form = new FormData();
    form.append("file", file);
    return call<DeliveryProof>(`/api/owner/orders/${encodeURIComponent(code)}/delivery-proof`, {
      method: "POST",
      headers: bearer(token),
      body: form,
    });
  },
  forecast: (token: string) =>
    call<CashForecast>("/api/owner/books/forecast", { headers: bearer(token) }),
  setCash: (token: string, amount: number) =>
    call<CashForecast>("/api/owner/books/cash", {
      method: "PUT",
      headers: bearer(token),
      body: JSON.stringify({ amount }),
    }),
  yearSummary: (token: string, starts?: number) =>
    call<YearSummary>(`/api/owner/books/year${starts ? `?starts=${starts}` : ""}`, {
      headers: bearer(token),
    }),
  zakat: (token: string, figures: ZakatIn) =>
    call<Zakat>("/api/owner/books/zakat", {
      method: "POST",
      headers: bearer(token),
      body: JSON.stringify(figures),
    }),
  taxWorkbook: (token: string, start: string, end: string) =>
    blob(
      token,
      `/api/owner/books/export.xlsx?start=${encodeURIComponent(start)}&end=${encodeURIComponent(end)}`,
    ),
  staffPay: (token: string, month: string) =>
    call<StaffPayRow[]>(`/api/owner/team/pay?month=${encodeURIComponent(month)}`, {
      headers: bearer(token),
    }),
  setStaffPay: (
    token: string,
    staffId: string,
    pay: { salary: number; commission_bp: number; pay_day: number },
  ) =>
    call<{ salary: number }>(`/api/owner/team/${encodeURIComponent(staffId)}/pay`, {
      method: "PUT",
      headers: bearer(token),
      body: JSON.stringify(pay),
    }),
  payStaff: (token: string, staffId: string, month: string) =>
    call<StaffPayRow>(
      `/api/owner/team/${encodeURIComponent(staffId)}/pay/${encodeURIComponent(month)}`,
      { method: "POST", headers: bearer(token) },
    ),
  passports: (token: string) =>
    call<Passport[]>("/api/owner/passports", { headers: bearer(token) }),
  makePassport: (token: string, wording: string) =>
    call<Passport>("/api/owner/passports", {
      method: "POST",
      headers: bearer(token),
      body: JSON.stringify({ agree: true, wording }),
    }),
  revokePassport: (token: string, code: string) =>
    call<{ revoked: boolean }>(`/api/owner/passports/${encodeURIComponent(code)}/revoke`, {
      method: "POST",
      headers: bearer(token),
    }),
  passport: (code: string) => call<PublicPassport>(`/api/passport/${encodeURIComponent(code)}`),
  seasons: (token: string) => call<SeasonAhead[]>("/api/owner/seasons", { headers: bearer(token) }),
  coachNotes: (token: string) => call<CoachNote[]>("/api/owner/coach", { headers: bearer(token) }),
  coachNow: (token: string) =>
    call<CoachNote>("/api/owner/coach/now", { method: "POST", headers: bearer(token) }),
  checkCall: (token: string, file: Blob) => {
    const form = new FormData();
    form.append("file", file);
    return call<CallCheck>("/api/owner/guardian", {
      method: "POST",
      headers: bearer(token),
      body: form,
    });
  },
  scams: (token: string) => call<ScamReportOut[]>("/api/owner/scams", { headers: bearer(token) }),
  reportScam: (token: string, kind: ScamKind, value: string, posingAs: string) =>
    call<ScamReportOut>("/api/owner/scams", {
      method: "POST",
      headers: bearer(token),
      body: JSON.stringify({ kind, value, posing_as: posingAs }),
    }),
  scamsToReview: (token: string) =>
    call<ScamToReview[]>("/api/platform/scams", { headers: bearer(token) }),
  decideScam: (token: string, id: string, confirm: boolean) =>
    call<ScamReportOut>(`/api/platform/scams/${encodeURIComponent(id)}`, {
      method: "POST",
      headers: bearer(token),
      body: JSON.stringify({ confirm }),
    }),
  stixReport: (token: string) => blob(token, "/api/platform/stix"),
  customerData: (token: string, customerId: string) =>
    blob(token, `/api/owner/customers/${encodeURIComponent(customerId)}/data`),
  eraseCustomer: (token: string, customerId: string) =>
    call<Erased>(`/api/owner/customers/${encodeURIComponent(customerId)}/erase`, {
      method: "POST",
      headers: bearer(token),
    }),
  ownerReviews: (token: string) =>
    call<ShopReviews>("/api/owner/reviews", { headers: bearer(token) }),
  replyToReview: (token: string, reviewId: string, text: string) =>
    call<{ reply: string }>(`/api/owner/reviews/${encodeURIComponent(reviewId)}/reply`, {
      method: "POST",
      headers: bearer(token),
      body: JSON.stringify({ text }),
    }),
  networkSettings: (token: string) =>
    call<NetworkSettings>("/api/network/settings", { headers: bearer(token) }),
  saveNetworkSettings: (token: string, wholesaleEnabled: boolean) =>
    call<NetworkSettings>("/api/network/settings", {
      method: "PUT",
      headers: bearer(token),
      body: JSON.stringify({ wholesale_enabled: wholesaleEnabled }),
    }),
  networkBranches: (token: string) =>
    call<NetworkBranch[]>("/api/network/branches", { headers: bearer(token) }),
  createNetworkBranch: (
    token: string,
    branch: { name: string; code: string; city?: string; address?: string; is_default?: boolean },
  ) =>
    call<NetworkBranch>("/api/network/branches", {
      method: "POST",
      headers: bearer(token),
      body: JSON.stringify(branch),
    }),
  updateNetworkBranch: (
    token: string,
    id: string,
    change: { name?: string; city?: string | null; address?: string | null; active?: boolean; is_default?: boolean },
  ) =>
    call<NetworkBranch>(`/api/network/branches/${encodeURIComponent(id)}`, {
      method: "PUT",
      headers: bearer(token),
      body: JSON.stringify(change),
    }),
  assignStaffBranch: (token: string, staffId: string, branchId: string | null) =>
    call<{ saved: boolean }>(`/api/network/team/${encodeURIComponent(staffId)}/branch`, {
      method: "PUT",
      headers: bearer(token),
      body: JSON.stringify({ branch_id: branchId }),
    }),
  networkListings: (token: string) =>
    call<WholesaleListing[]>("/api/network/listings", { headers: bearer(token) }),
  myNetworkListings: (token: string) =>
    call<WholesaleListing[]>("/api/network/my-listings", { headers: bearer(token) }),
  createWholesaleListing: (
    token: string,
    listing: {
      product_id: string;
      variant_id?: string | null;
      branch_id?: string | null;
      title: string;
      description?: string;
      unit_price: number;
      minimum_unit_price?: number | null;
      minimum_quantity: number;
      maximum_quantity: number;
    },
  ) =>
    call<WholesaleListing>("/api/network/listings", {
      method: "POST",
      headers: bearer(token),
      body: JSON.stringify(listing),
    }),
  updateWholesaleListing: (
    token: string,
    id: string,
    change: {
      title?: string;
      description?: string;
      unit_price?: number;
      minimum_unit_price?: number | null;
      minimum_quantity?: number;
      maximum_quantity?: number;
      active?: boolean;
    },
  ) =>
    call<WholesaleListing>(`/api/network/listings/${encodeURIComponent(id)}`, {
      method: "PUT",
      headers: bearer(token),
      body: JSON.stringify(change),
    }),
  placeWholesaleOrder: (
    token: string,
    listingId: string,
    order: { quantity: number; initial_offer_unit_price: number; budget_unit_price: number; branch_id?: string | null },
  ) =>
    call<NetworkOrder>(`/api/network/listings/${encodeURIComponent(listingId)}/orders`, {
      method: "POST",
      headers: bearer(token),
      body: JSON.stringify(order),
    }),
  buyerNetworkOrders: (token: string) =>
    call<NetworkOrder[]>("/api/network/buyer/orders", { headers: bearer(token) }),
  sellerNetworkOrders: (token: string) =>
    call<NetworkOrder[]>("/api/network/seller/orders", { headers: bearer(token) }),
  networkOrderRounds: (token: string, orderId: string) =>
    call<NetworkRound[]>(`/api/network/orders/${encodeURIComponent(orderId)}/rounds`, {
      headers: bearer(token),
    }),
  approveNetworkOrder: (token: string, orderId: string) =>
    call<NetworkOrder>(`/api/network/seller/orders/${encodeURIComponent(orderId)}/approve`, {
      method: "POST",
      headers: bearer(token),
    }),
  rejectNetworkOrder: (token: string, orderId: string) =>
    call<NetworkOrder>(`/api/network/seller/orders/${encodeURIComponent(orderId)}/reject`, {
      method: "POST",
      headers: bearer(token),
    }),
  confirmNetworkOrder: (token: string, orderId: string) =>
    call<NetworkPayment>(`/api/network/buyer/orders/${encodeURIComponent(orderId)}/confirm`, {
      method: "POST",
      headers: bearer(token),
    }),
  networkPayments: (token: string) =>
    call<NetworkPayment[]>("/api/network/payments", { headers: bearer(token) }),
};
