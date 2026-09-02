"use client";

import { useState } from "react";
import { ApiError, api } from "@/lib/api";
import type { ExplainResponse } from "@/lib/types";
import { IntegrityBadge } from "./DecisionBadge";

interface Props {
  selectedKey: string | null;
}

export function ExplainPanel({ selectedKey }: Props) {
  const [input, setInput] = useState("");
  const [result, setResult] = useState<ExplainResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // A row clicked in another panel drives this one -- explain() accepts
  // either a transaction_id or a cart_id (ledger/explain.py::load_entries).
  // Adjusted during render (React's documented pattern for "reset/sync
  // state when a prop changes") rather than in an effect, which would
  // trigger an extra, avoidable re-render.
  const [lastSelectedKey, setLastSelectedKey] = useState<string | null>(null);
  if (selectedKey !== lastSelectedKey) {
    setLastSelectedKey(selectedKey);
    if (selectedKey) {
      setInput(selectedKey);
      void runExplain(selectedKey);
    }
  }

  async function runExplain(key: string) {
    const trimmed = key.trim();
    if (!trimmed) return;
    setLoading(true);
    setError(null);
    try {
      const res = await api.explain(trimmed);
      setResult(res);
    } catch (err) {
      setResult(null);
      setError(err instanceof ApiError ? err.message : "Failed to reach the control plane.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <section className="flex flex-col rounded-xl border border-zinc-200 bg-white shadow-sm dark:border-zinc-800 dark:bg-zinc-950">
      <div className="flex items-center justify-between border-b border-zinc-200 px-4 py-3 dark:border-zinc-800">
        <h2 className="text-sm font-semibold text-zinc-900 dark:text-zinc-100">Explain a transaction</h2>
        {result && <IntegrityBadge status={result.integrity_status} />}
      </div>

      <form
        onSubmit={(e) => {
          e.preventDefault();
          void runExplain(input);
        }}
        className="flex gap-2 border-b border-zinc-200 px-4 py-3 dark:border-zinc-800"
      >
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="transaction_id or cart_id"
          className="flex-1 rounded-md border border-zinc-300 bg-white px-3 py-1.5 font-mono text-xs text-zinc-900 placeholder:text-zinc-400 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-100"
        />
        <button
          type="submit"
          disabled={loading}
          className="rounded-md bg-zinc-900 px-3 py-1.5 text-xs font-semibold text-white transition hover:bg-zinc-700 disabled:opacity-50 dark:bg-zinc-100 dark:text-zinc-900"
        >
          {loading ? "Explaining…" : "Explain"}
        </button>
      </form>

      <div className="max-h-[420px] overflow-y-auto px-4 py-3">
        {error && <p className="text-sm text-red-600">{error}</p>}
        {!error && !result && (
          <p className="text-sm text-zinc-500">
            Click any row in the feed, or paste a cart_id / transaction_id above.
          </p>
        )}
        {result && !result.found && <p className="text-sm text-zinc-500">{result.headline}</p>}
        {result?.found && (
          <div className="flex flex-col gap-3">
            <p className="text-sm font-semibold text-zinc-900 dark:text-zinc-100">{result.headline}</p>
            <ol className="space-y-1.5 border-l-2 border-zinc-200 pl-3 dark:border-zinc-800">
              {result.narrative.map((line, i) => (
                <li key={i} className="text-xs text-zinc-600 dark:text-zinc-400">
                  {line}
                </li>
              ))}
            </ol>
            {result.integrity_status !== "OK" && (
              <div className="rounded-md border border-red-300 bg-red-50 p-2 text-xs text-red-700 dark:border-red-800 dark:bg-red-950 dark:text-red-300">
                <p className="font-semibold">Chain integrity findings</p>
                <ul className="mt-1 list-disc pl-4">
                  {result.integrity_findings.map((finding, i) => (
                    <li key={i}>{finding}</li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        )}
      </div>
    </section>
  );
}
