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
  method: "cod" | "transfer" | null;
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
