import type {
  DecisionActionResponse,
  ExplainResponse,
  RecentTransactionsResponse,
  StepUpQueueResponse,
  VerifyResponse,
} from "./types";

// The control plane (FastAPI) runs on a different origin in dev --
// localhost:8000 by default, per the README's `uvicorn ... --app-dir
// control-plane` instructions. Override with NEXT_PUBLIC_API_BASE_URL if
// it's running elsewhere.
const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

export class ApiError extends Error {
  status: number;
  body: unknown;

  constructor(status: number, message: string, body: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.body = body;
  }
}

async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    cache: "no-store",
  });

  const body = await res.json().catch(() => null);

  if (!res.ok) {
    const detail =
      (body && typeof body === "object" && "detail" in body && (body as { detail: unknown }).detail) ??
      res.statusText;
    throw new ApiError(res.status, typeof detail === "string" ? detail : JSON.stringify(detail), body);
  }

  return body as T;
}

export const api = {
  recentTransactions: (limit = 25) =>
    apiFetch<RecentTransactionsResponse>(`/ledger/recent?limit=${limit}`),

  stepUpQueue: () => apiFetch<StepUpQueueResponse>(`/demo/step-up`),

  explain: (key: string) => apiFetch<ExplainResponse>(`/ledger/${encodeURIComponent(key)}/explain`),

  verifyChain: () => apiFetch<VerifyResponse>(`/ledger/verify`),

  approveStepUp: (cartId: string, actor: string) =>
    apiFetch<DecisionActionResponse>(`/demo/step-up/${encodeURIComponent(cartId)}/approve`, {
      method: "POST",
      body: JSON.stringify({ actor }),
    }),

  denyStepUp: (cartId: string, actor: string) =>
    apiFetch<{ cart_id: string; status: string }>(`/demo/step-up/${encodeURIComponent(cartId)}/deny`, {
      method: "POST",
      body: JSON.stringify({ actor }),
    }),
};

export function formatPaise(paise: number): string {
  return `₹${(paise / 100).toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

export function formatTime(iso: string | null): string {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleTimeString("en-IN", { hour12: false });
  } catch {
    return iso;
  }
}
