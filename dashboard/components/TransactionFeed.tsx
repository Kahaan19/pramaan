"use client";

import { formatTime } from "@/lib/api";
import type { TransactionSummary } from "@/lib/types";
import { DecisionBadge, IntegrityBadge } from "./DecisionBadge";

interface Props {
  transactions: TransactionSummary[];
  loading: boolean;
  error: string | null;
  onSelect: (key: string) => void;
}

export function TransactionFeed({ transactions, loading, error, onSelect }: Props) {
  return (
    <section className="flex flex-col rounded-xl border border-zinc-200 bg-white shadow-sm dark:border-zinc-800 dark:bg-zinc-950">
      <div className="flex items-center justify-between border-b border-zinc-200 px-4 py-3 dark:border-zinc-800">
        <h2 className="text-sm font-semibold text-zinc-900 dark:text-zinc-100">Live transaction feed</h2>
        {loading && <span className="text-xs text-zinc-400">refreshing…</span>}
      </div>
      {error && <p className="px-4 py-3 text-sm text-red-600">{error}</p>}
      <div className="max-h-[420px] overflow-y-auto">
        {transactions.length === 0 && !loading && (
          <p className="px-4 py-6 text-center text-sm text-zinc-500">No transactions yet.</p>
        )}
        <ul className="divide-y divide-zinc-100 dark:divide-zinc-900">
          {transactions.map((tx) => (
            <li key={tx.transaction_id}>
              <button
                onClick={() => onSelect(tx.cart_id ?? tx.transaction_id)}
                className="flex w-full flex-col gap-1 px-4 py-3 text-left transition hover:bg-zinc-50 dark:hover:bg-zinc-900"
              >
                <div className="flex items-center justify-between gap-2">
                  <div className="flex items-center gap-2">
                    <DecisionBadge decision={tx.decision} />
                    {tx.rule_fired && (
                      <code className="rounded bg-zinc-100 px-1.5 py-0.5 text-[11px] text-zinc-600 dark:bg-zinc-800 dark:text-zinc-400">
                        {tx.rule_fired}
                      </code>
                    )}
                  </div>
                  <span className="shrink-0 text-[11px] tabular-nums text-zinc-400">{formatTime(tx.ts)}</span>
                </div>
                <p className="truncate text-sm text-zinc-700 dark:text-zinc-300">{tx.headline}</p>
                <div className="flex items-center justify-between">
                  <span className="truncate font-mono text-[11px] text-zinc-400">{tx.cart_id ?? tx.transaction_id}</span>
                  {tx.integrity_status !== "OK" && <IntegrityBadge status={tx.integrity_status} />}
                </div>
              </button>
            </li>
          ))}
        </ul>
      </div>
    </section>
  );
}
