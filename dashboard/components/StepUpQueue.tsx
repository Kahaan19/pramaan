"use client";

import { useState } from "react";
import { ApiError, api, formatPaise, formatTime } from "@/lib/api";
import type { StepUpSummary } from "@/lib/types";

interface Props {
  pending: StepUpSummary[];
  loading: boolean;
  error: string | null;
  onResolved: () => void;
  onSelect: (key: string) => void;
}

export function StepUpQueue({ pending, loading, error, onResolved, onSelect }: Props) {
  const [actor, setActor] = useState("operator");

  return (
    <section className="flex flex-col rounded-xl border border-amber-300 bg-amber-50/40 shadow-sm dark:border-amber-900 dark:bg-amber-950/20">
      <div className="flex items-center justify-between border-b border-amber-200 px-4 py-3 dark:border-amber-900">
        <h2 className="text-sm font-semibold text-amber-900 dark:text-amber-300">STEP-UP approval queue</h2>
        <div className="flex items-center gap-2">
          {loading && <span className="text-xs text-amber-500">refreshing…</span>}
          <span className="rounded-full bg-amber-500 px-2 py-0.5 text-xs font-bold text-white">{pending.length}</span>
        </div>
      </div>

      <div className="border-b border-amber-200 px-4 py-2 dark:border-amber-900">
        <label className="flex items-center gap-2 text-xs text-amber-800 dark:text-amber-400">
          Reviewing as
          <input
            value={actor}
            onChange={(e) => setActor(e.target.value)}
            className="rounded border border-amber-300 bg-white px-2 py-1 text-xs text-zinc-900 dark:border-amber-800 dark:bg-zinc-900 dark:text-zinc-100"
          />
        </label>
      </div>

      {error && <p className="px-4 py-3 text-sm text-red-600">{error}</p>}

      <div className="max-h-[520px] overflow-y-auto">
        {pending.length === 0 && !loading && (
          <p className="px-4 py-6 text-center text-sm text-amber-700/70 dark:text-amber-400/70">
            Nothing waiting on a human right now.
          </p>
        )}
        <ul className="divide-y divide-amber-200 dark:divide-amber-900/60">
          {pending.map((req) => (
            <StepUpCard key={req.cart_id} req={req} actor={actor} onResolved={onResolved} onSelect={onSelect} />
          ))}
        </ul>
      </div>
    </section>
  );
}

function StepUpCard({
  req,
  actor,
  onResolved,
  onSelect,
}: {
  req: StepUpSummary;
  actor: string;
  onResolved: () => void;
  onSelect: (key: string) => void;
}) {
  const [busy, setBusy] = useState<"approve" | "deny" | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  async function decide(action: "approve" | "deny") {
    setBusy(action);
    setMessage(null);
    try {
      if (action === "approve") {
        const result = await api.approveStepUp(req.cart_id, actor);
        setMessage(
          result.verdict?.decision === "DENY"
            ? `Vetoed on re-check: ${result.verdict.reason}`
            : `Approved — ${result.checkout?.status ?? "executed"}.`,
        );
      } else {
        await api.denyStepUp(req.cart_id, actor);
        setMessage("Denied.");
      }
    } catch (err) {
      setMessage(err instanceof ApiError ? `Error: ${err.message}` : "Something went wrong.");
    } finally {
      setBusy(null);
      onResolved();
    }
  }

  return (
    <li className="px-4 py-3">
      <div className="flex items-start justify-between gap-2">
        <div>
          <p className="text-sm font-semibold text-zinc-900 dark:text-zinc-100">{formatPaise(req.amount_paise)}</p>
          <button
            onClick={() => onSelect(req.cart_id)}
            className="font-mono text-[11px] text-zinc-500 underline decoration-dotted hover:text-zinc-800 dark:hover:text-zinc-300"
          >
            {req.cart_id}
          </button>
        </div>
        <span className="shrink-0 text-[11px] tabular-nums text-zinc-400">{formatTime(req.created_at)}</span>
      </div>

      <p className="mt-1 text-xs text-zinc-600 dark:text-zinc-400">
        <code className="rounded bg-zinc-100 px-1 py-0.5 dark:bg-zinc-800">{req.rule_fired}</code> — {req.reason}
      </p>

      <dl className="mt-2 grid grid-cols-2 gap-x-3 gap-y-1 text-[11px] text-zinc-500">
        <div>
          <dt className="inline text-zinc-400">buyer </dt>
          <dd className="inline font-mono">{req.user_id}</dd>
        </div>
        <div>
          <dt className="inline text-zinc-400">merchant </dt>
          <dd className="inline font-mono">{req.merchant_id}</dd>
        </div>
        <div>
          <dt className="inline text-zinc-400">intent cap </dt>
          <dd className="inline">{formatPaise(req.max_amount_paise)}</dd>
        </div>
        <div>
          <dt className="inline text-zinc-400">category </dt>
          <dd className="inline">{req.category ?? "—"}</dd>
        </div>
        <div className="col-span-2">
          <dt className="inline text-zinc-400">intent expires </dt>
          <dd className="inline">{new Date(req.intent_expires_at).toLocaleString()}</dd>
        </div>
      </dl>

      <ul className="mt-2 space-y-0.5 text-[11px] text-zinc-500">
        {req.items.map((item, i) => (
          <li key={i} className="font-mono">
            {item.qty}× {item.sku} @ {formatPaise(item.unit_price_paise)}
          </li>
        ))}
      </ul>

      <div className="mt-3 flex items-center gap-2">
        <button
          onClick={() => decide("approve")}
          disabled={busy !== null}
          className="rounded-md bg-emerald-600 px-3 py-1.5 text-xs font-semibold text-white transition hover:bg-emerald-700 disabled:opacity-50"
        >
          {busy === "approve" ? "Approving…" : "Approve"}
        </button>
        <button
          onClick={() => decide("deny")}
          disabled={busy !== null}
          className="rounded-md bg-zinc-200 px-3 py-1.5 text-xs font-semibold text-zinc-800 transition hover:bg-zinc-300 disabled:opacity-50 dark:bg-zinc-800 dark:text-zinc-200 dark:hover:bg-zinc-700"
        >
          {busy === "deny" ? "Denying…" : "Deny"}
        </button>
        {message && <span className="text-[11px] text-zinc-600 dark:text-zinc-400">{message}</span>}
      </div>
    </li>
  );
}
