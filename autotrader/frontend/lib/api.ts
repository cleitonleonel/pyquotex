/**
 * Minimal fetch wrapper for the autotrader API.
 *
 * The bearer token issued by /auth/login is kept in localStorage so it
 * survives page reloads. On any 401 the caller should clear it and send
 * the user back to /login.
 */

const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";
const TOKEN_KEY = "autotrader.token";

export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
    public detail?: unknown,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string | null) {
  if (typeof window === "undefined") return;
  if (token === null) window.localStorage.removeItem(TOKEN_KEY);
  else window.localStorage.setItem(TOKEN_KEY, token);
}

export async function api<T = unknown>(
  path: string,
  init: RequestInit = {},
): Promise<T> {
  const token = getToken();
  const headers = new Headers(init.headers);
  if (!headers.has("Content-Type") && init.body) {
    headers.set("Content-Type", "application/json");
  }
  if (token) headers.set("Authorization", `Bearer ${token}`);

  const res = await fetch(`${API_BASE}${path}`, { ...init, headers });
  const text = await res.text();
  const body = text ? safeJson(text) : null;

  if (!res.ok) {
    const message =
      body && typeof body === "object" && "detail" in body && typeof body.detail === "string"
        ? body.detail
        : res.statusText;
    throw new ApiError(message, res.status, body);
  }
  return body as T;
}

function safeJson(text: string): unknown {
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

export async function login(passcode: string): Promise<string> {
  const r = await api<{ token: string }>("/auth/login", {
    method: "POST",
    body: JSON.stringify({ passcode }),
  });
  setToken(r.token);
  return r.token;
}

export function logout() {
  setToken(null);
}

// ---------------------------------------------------------------------------
// Broker
// ---------------------------------------------------------------------------

export type AccountMode = "PRACTICE" | "REAL";
export type ConnectState =
  | "idle"
  | "connecting"
  | "awaiting_otp"
  | "connected"
  | "error";

export interface BrokerStatus {
  configured: boolean;
  connected: boolean;
  state: ConnectState;
  awaiting_otp: boolean;
  otp_prompt: string | null;
  email_masked: string | null;
  account_mode: AccountMode;
  connected_at: string | null;
  last_error: string | null;
}

export interface BrokerBalance {
  balance: number;
  account_mode: AccountMode;
}

export interface BrokerConnectResponse {
  connected: boolean;
  state: ConnectState;
  detail: string;
  otp_prompt: string | null;
}

export interface BrokerAssets {
  assets: string[];
  count: number;
}

export const broker = {
  status: () => api<BrokerStatus>("/broker/status"),

  putCredentials: (email: string, password: string, account_mode: AccountMode) =>
    api<{ ok: true }>("/broker/credentials", {
      method: "PUT",
      body: JSON.stringify({ email, password, account_mode }),
    }),

  deleteCredentials: () =>
    api<{ ok: true }>("/broker/credentials", { method: "DELETE" }),

  connect: () =>
    api<BrokerConnectResponse>("/broker/connect", { method: "POST" }),

  submitOtp: (code: string) =>
    api<BrokerStatus>("/broker/otp", {
      method: "POST",
      body: JSON.stringify({ code }),
    }),

  cancelConnect: () =>
    api<{ ok: true }>("/broker/cancel", { method: "POST" }),

  disconnect: () => api<{ ok: true }>("/broker/disconnect", { method: "POST" }),

  setAccountMode: (mode: AccountMode) =>
    api<BrokerStatus>("/broker/account-mode", {
      method: "POST",
      body: JSON.stringify({ mode }),
    }),

  balance: () => api<BrokerBalance>("/broker/balance"),

  assets: (refresh = false) =>
    api<BrokerAssets>(`/broker/assets${refresh ? "?refresh=true" : ""}`),
};

// ---------------------------------------------------------------------------
// Telegram
// ---------------------------------------------------------------------------

export type TelegramLoginState =
  | "idle"
  | "awaiting_code"
  | "awaiting_password"
  | "logged_in"
  | "error";

export interface TelegramStatus {
  state: TelegramLoginState;
  logged_in: boolean;
  awaiting_code: boolean;
  awaiting_password: boolean;
  phone_masked: string | null;
  user_id: number | null;
  username: string | null;
  first_name: string | null;
  last_error: string | null;
}

export interface TelegramDialog {
  chat_id: number;
  title: string;
  chat_type: string;
  username: string | null;
  members_count: number | null;
  is_verified: boolean;
  watched: boolean;
}

export const telegram = {
  status: () => api<TelegramStatus>("/telegram/status"),

  login: (phone: string) =>
    api<TelegramStatus>("/telegram/login", {
      method: "POST",
      body: JSON.stringify({ phone }),
    }),

  submitCode: (code: string) =>
    api<TelegramStatus>("/telegram/code", {
      method: "POST",
      body: JSON.stringify({ code }),
    }),

  submitPassword: (password: string) =>
    api<TelegramStatus>("/telegram/password", {
      method: "POST",
      body: JSON.stringify({ password }),
    }),

  cancel: () => api<{ ok: true }>("/telegram/cancel", { method: "POST" }),

  logout: () => api<{ ok: true }>("/telegram/logout", { method: "POST" }),

  dialogs: (q?: string, limit = 200) => {
    const params = new URLSearchParams();
    if (q) params.set("q", q);
    params.set("limit", String(limit));
    return api<TelegramDialog[]>(`/telegram/dialogs?${params}`);
  },

  watched: () => api<TelegramDialog[]>("/telegram/watched"),

  watch: (d: {
    chat_id: number;
    title: string;
    chat_type: string;
    username: string | null;
    enabled: boolean;
  }) =>
    api<{ ok: true }>("/telegram/watch", {
      method: "POST",
      body: JSON.stringify(d),
    }),

  unwatch: (chatId: number) =>
    api<{ ok: true }>(`/telegram/watch/${chatId}`, { method: "DELETE" }),

  messages: (chatId: number, limit = 20) =>
    api<TelegramMessage[]>(
      `/telegram/messages?chat_id=${chatId}&limit=${limit}`,
    ),
};

export interface TelegramMessage {
  id: number;
  text: string;
  media_kind: "text" | "caption" | "sticker";
  sender_id: number;
  date: string | null;
}

// ---------------------------------------------------------------------------
// Parsers
// ---------------------------------------------------------------------------

export type ParserType = "template" | "regex";
export type TradeMode = "live" | "scheduled" | "auto";

export interface ParserTemplate {
  id: string;
  label: string;
  template: string;
  example: string;
}

export interface MartingalePayload {
  enabled: boolean;
  multiplier: number;
  max_streak: number;
  reset_on_win: boolean;
}

export interface ParserConfigPayload {
  name: string;
  priority: number;
  parser_type: ParserType;
  parser_config: Record<string, unknown>;
  timezone: string;
  timezone_offset_minutes: number;
  asset_aliases: Record<string, string>;
  aggregate_window_seconds: number;
  default_stake: number;
  default_duration_seconds: number;
  trade_mode: TradeMode;
  martingale: MartingalePayload;
  enabled: boolean;
}

export interface ParserConfig extends ParserConfigPayload {
  id: number;
  chat_id: number;
  created_at: string;
  updated_at: string;
}

export interface ParsedSignal {
  asset: string;
  direction: string;
  duration_seconds: number;
  stake: number | null;
  fire_at: string | null;
  raw_text: string;
  parser_id: string;
  matched_groups: Record<string, string>;
  trade_mode: TradeMode;
  asset_raw: string;
  asset_via: string;
}

export interface ParserTestResponse {
  matched: boolean;
  signal: ParsedSignal | null;
  error: string | null;
  error_detail: Record<string, unknown> | null;
}

export interface ParserTestMessage {
  text: string;
  sender_id?: number;
  received_at?: string;
}

export const DEFAULT_PARSER_CONFIG: ParserConfigPayload = {
  name: "default",
  priority: 100,
  parser_type: "template",
  parser_config: { template: "{DIRECTION} {ASSET} {DURATION}" },
  timezone: "UTC",
  timezone_offset_minutes: 0,
  asset_aliases: {},
  aggregate_window_seconds: 0,
  default_stake: 1,
  default_duration_seconds: 60,
  trade_mode: "auto",
  martingale: {
    enabled: false,
    multiplier: 2,
    max_streak: 5,
    reset_on_win: true,
  },
  enabled: true,
};

export const parsers = {
  templates: () => api<ParserTemplate[]>("/parsers/templates"),

  list: (chatId?: number) => {
    const path =
      chatId !== undefined
        ? `/parsers/configs?chat_id=${chatId}`
        : "/parsers/configs";
    return api<ParserConfig[]>(path);
  },

  get: (configId: number) =>
    api<ParserConfig>(`/parsers/configs/${configId}`),

  create: (chatId: number, body: ParserConfigPayload) =>
    api<ParserConfig>(`/parsers/configs`, {
      method: "POST",
      body: JSON.stringify({ chat_id: chatId, ...body }),
    }),

  update: (configId: number, body: ParserConfigPayload) =>
    api<ParserConfig>(`/parsers/configs/${configId}`, {
      method: "PUT",
      body: JSON.stringify(body),
    }),

  remove: (configId: number) =>
    api<{ ok: true }>(`/parsers/configs/${configId}`, { method: "DELETE" }),

  test: (config: ParserConfigPayload, messages: ParserTestMessage[]) =>
    api<ParserTestResponse>("/parsers/test", {
      method: "POST",
      body: JSON.stringify({ config, messages }),
    }),
};
