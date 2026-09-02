"use client";

import { formatTime } from "@/lib/api";
import type { TransactionSummary } from "@/lib/types";

interface Props {
  blocked: TransactionSummary[];
  onSelect: (key: string) => void;
}

/**
 * Deliberately loud: this is the panel meant to make "a rogue agent got
 * caught" visually unmissable in a demo, per ARCHITECTURE.md 5.7's "red
 * Rogue agent blocked panel" spec.
 */
export function BlockedPanel({ blocked, onSelect }: Props) {
  return (
    <section className="flex flex-col rounded-xl border-2 border-red-300 bg-red-50/60 shadow-sm dark:border-red-900 dark:bg-red-950/30">
      <div className="flex items-center justify-between border-b border-red-200 px-4 py-3 dark:border-red-900">
        <h2 className="flex items-center gap-2 text-sm font-bold text-red-800 dark:text-red-300">
          🚫 Rogue agent blocked
        </h2>
        <span className="rounded-full bg-red-600 px-2 py-0.5 text-xs font-bold text-white">{blocked.length}</span>
      </div>
      <div className="max-h-[280px] overflow-y-auto">
        {blocked.length === 0 && (
          <p className="px-4 py-6 text-center text-sm text-red-700/70 dark:text-red-400/70">
            Nothing blocked yet — the gate hasn&apos;t caught anything.
          </p>
        )}
        <ul className="divide-y divide-red-200 dark:divide-red-900/60">
          {blocked.map((tx) => (
            <li key={tx.transaction_id}>
              <button
                onClick={() => onSelect(tx.cart_id ?? tx.transaction_id)}
                className="flex w-full flex-col gap-1 px-4 py-3 text-left transition hover:bg-red-100/60 dark:hover:bg-red-900/30"
              >
                <div className="flex items-center justify-between gap-2">
                  <code className="rounded bg-red-600/10 px-1.5 py-0.5 text-[11px] font-semibold text-red-800 dark:bg-red-500/20 dark:text-red-300">
                    {tx.rule_fired ?? "denied"}
                  </code>
                  <span className="shrink-0 text-[11px] tabular-nums text-red-500/80">{formatTime(tx.ts)}</span>
                </div>
                <p className="text-sm font-medium text-red-900 dark:text-red-200">{tx.headline}</p>
                <span className="truncate font-mono text-[11px] text-red-600/70 dark:text-red-400/70">
                  {tx.cart_id ?? tx.transaction_id}
                </span>
              </button>
            </li>
          ))}
        </ul>
      </div>
    </section>
  );
}
