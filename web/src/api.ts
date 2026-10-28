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
  variants: PublicVariant[];
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
};
export type Quote = {
  lines: QuoteLine[];
  subtotal: number;
  delivery: number | null;
  free_delivery: boolean;
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
  details: DeliveryDetails;
  payment_method: "cod" | "transfer";
  source: "shop_page" | "facebook" | "instagram";
  lang: string;
};
export type Placed = {
  code: string;
  status: string;
  total: number;
  due_now: number | null;
  payment_method: string;
  message: string;
};

export type OwnerMe = {
  slug: string;
  shop_name: string;
  staff_name: string;
  lang: string;
  template: string;
  country: string;
  currency: string;
  is_demo: boolean;
  languages: string[];
  role: string;
  rights: string[];
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

export type Handover = {
  id: string;
  conversation_id: string;
  customer: string | null;
  channel: string;
  reason: string;
  summary: string;
  requested_by: string;
  created_at: string;
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
export type Payments = {
  forwarding: boolean;
  currency: string;
  reviews: PaymentReview[];
  waiting: PaymentWaiting[];
  recent: PaymentDone[];
};
export type Forwarding = { url: string; header: string; secret: string };
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
  shipment: { courier: string | null; tracking_number: string | null; public_code: string } | null;
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
  cost_unknown: number;
};
export type LedgerEntry = {
  id: string;
  kind: "sale" | "expense" | "udhaar_given" | "udhaar_paid" | "write_off";
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
export type WhyHistory = { at: string; action: string; actor: string; data: Record<string, unknown> };
export type Why = { code: string; status: string; decisions: WhyDecision[]; history: WhyHistory[] };

/** Who is chatting: a customer with a random chat token, or the owner with a signed session. */
export type ChatAuth = { token: string; owner: boolean };

function authHeaders(auth: ChatAuth): Record<string, string> {
  return auth.owner ? { Authorization: `Bearer ${auth.token}` } : { "X-Chat-Token": auth.token };
}

const bearer = (token: string) => ({ Authorization: `Bearer ${token}` });

type Init = Omit<RequestInit, "headers"> & { headers?: Record<string, string> };

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
  quote: (slug: string, lines: { item: string; quantity: number }[], city: string) =>
    call<Quote>(`${shopPath(slug)}/checkout/quote`, {
      method: "POST",
      body: JSON.stringify({ lines, city: city.trim() || null }),
    }),
  checkout: (slug: string, chatToken: string, order: CheckoutRequest) =>
    call<Placed>(`${shopPath(slug)}/checkout`, {
      method: "POST",
      headers: { "X-Chat-Token": chatToken },
      body: JSON.stringify(order),
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
  handovers: (token: string) => call<Handover[]>("/api/owner/handovers", { headers: bearer(token) }),
  handoverMessages: (token: string, conversationId: string) =>
    call<ConsoleMessage[]>(
      `/api/owner/conversations/${encodeURIComponent(conversationId)}/messages`,
      { headers: bearer(token) },
    ),
  staffReply: (token: string, conversationId: string, text: string) =>
    call<{ sent: boolean }>(`/api/owner/conversations/${encodeURIComponent(conversationId)}/reply`, {
      method: "POST",
      headers: bearer(token),
      body: JSON.stringify({ text }),
    }),
  payments: (token: string) => call<Payments>("/api/owner/payments", { headers: bearer(token) }),
  decidePayment: (token: string, claimId: string, answer: "approve" | "reject") =>
    call<{ outcome: string; receipt_code: string | null }>(
      `/api/owner/payments/${encodeURIComponent(claimId)}/${answer}`,
      { method: "POST", headers: bearer(token) },
    ),
  setUpForwarding: (token: string) =>
    call<Forwarding>("/api/owner/sms-forwarding", { method: "POST", headers: bearer(token) }),
  orders: (token: string) => call<OwnerOrder[]>("/api/owner/orders", { headers: bearer(token) }),
  moveOrder: (token: string, code: string, to: string, courier = "", tracking = "") =>
    call<OwnerOrder>(`/api/owner/orders/${encodeURIComponent(code)}/status`, {
      method: "POST",
      headers: bearer(token),
      body: JSON.stringify({ to, courier, tracking }),
    }),
  label: async (token: string, code: string): Promise<Blob> => {
    const response = await fetch(
      `${API_BASE}/api/owner/orders/${encodeURIComponent(code)}/label.pdf`,
      { headers: bearer(token) },
    );
    if (!response.ok) throw new ApiError(response.status, `http_${response.status}`, "No label");
    return response.blob();
  },
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
  approvals: (token: string) => call<Approval[]>("/api/owner/approvals", { headers: bearer(token) }),
  answerApproval: (token: string, approvalId: string, approve: boolean) =>
    call<{ outcome: string }>(`/api/owner/approvals/${encodeURIComponent(approvalId)}`, {
      method: "POST",
      headers: bearer(token),
      body: JSON.stringify({ approve }),
    }),
  customers: (token: string) =>
    call<OwnerCustomer[]>("/api/owner/customers", { headers: bearer(token) }),
  setVerified: (token: string, customerId: string, verified: boolean) =>
    call<{ verified: boolean }>(`/api/owner/customers/${encodeURIComponent(customerId)}/verified`, {
      method: "PUT",
      headers: bearer(token),
      body: JSON.stringify({ verified }),
    }),
  settings: (token: string) => call<ShopSettings>("/api/owner/settings", { headers: bearer(token) }),
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
};
