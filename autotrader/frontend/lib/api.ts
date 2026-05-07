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

export interface BrokerStatus {
  configured: boolean;
  connected: boolean;
  email_masked: string | null;
  account_mode: AccountMode;
  connected_at: string | null;
  last_error: string | null;
}

export interface BrokerBalance {
  balance: number;
  account_mode: AccountMode;
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

  connect: () => api<{ connected: boolean; detail: string }>("/broker/connect", { method: "POST" }),

  disconnect: () => api<{ ok: true }>("/broker/disconnect", { method: "POST" }),

  setAccountMode: (mode: AccountMode) =>
    api<BrokerStatus>("/broker/account-mode", {
      method: "POST",
      body: JSON.stringify({ mode }),
    }),

  balance: () => api<BrokerBalance>("/broker/balance"),
};
