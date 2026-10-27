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

export type Shop = {
  slug: string;
  name: string;
  business_type: Text3;
  city: string | null;
  tagline: Text3;
  default_lang: string;
  is_demo: boolean;
  attribution: string;
};

export type ShopSummary = Pick<Shop, "slug" | "name" | "business_type" | "tagline">;

export type OfficialChannel = { channel: string; display: string | null };
export type TrustInfo = { slug: string; name: string; channels: OfficialChannel[] };

export type ChatButton = { id: string; title: string };
export type ServerMessage = {
  id: string;
  direction: "in" | "out";
  kind: string;
  body: string | null;
  payload: { buttons?: ChatButton[] };
  created_at: string;
};

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
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

export const api = {
  demoShops: () => call<ShopSummary[]>("/api/demo-shops"),
  shop: (slug: string) => call<Shop>(shopPath(slug)),
  trust: (slug: string) => call<TrustInfo>(`${shopPath(slug)}/trust`),
  startChat: (slug: string) =>
    call<{ token: string }>(`${shopPath(slug)}/chat/session`, { method: "POST" }),
  postChat: (slug: string, body: { token: string; client_id: string; text: string }) =>
    call<{ accepted: boolean }>(`${shopPath(slug)}/chat/messages`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  listChat: (slug: string, token: string, after?: string) => {
    const query = new URLSearchParams({ token, ...(after ? { after } : {}) });
    return call<ServerMessage[]>(`${shopPath(slug)}/chat/messages?${query}`);
  },
};
