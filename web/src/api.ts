import { API_BASE } from "./config";
import type { Text3 } from "./i18n";

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
  payload: { buttons?: ChatButton[]; list?: { button: string; rows: ChatListRow[] } };
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

export type ChatText = { client_id: string; text: string; button_id?: string };

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
