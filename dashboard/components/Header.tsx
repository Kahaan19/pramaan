import type { VerifyResponse } from "@/lib/types";
import { IntegrityBadge } from "./DecisionBadge";

export function Header({ chain }: { chain: VerifyResponse | null }) {
  return (
    <header className="flex flex-wrap items-center justify-between gap-3 border-b border-zinc-200 bg-white px-6 py-4 dark:border-zinc-800 dark:bg-zinc-950">
      <div>
        <h1 className="text-lg font-bold tracking-tight text-zinc-900 dark:text-zinc-100">
          Pramaan <span className="font-normal text-zinc-400">— governance control plane</span>
        </h1>
        <p className="text-xs text-zinc-500">Every money action: mandate-bound, policy-gated, ledger-audited.</p>
      </div>
      {chain && (
        <div className="flex items-center gap-3 text-xs text-zinc-500">
          <span>
            {chain.row_count} ledger rows · head seq {chain.head_seq ?? "—"}
          </span>
          <IntegrityBadge status={chain.ok ? "OK" : "BROKEN"} />
        </div>
      )}
    </header>
  );
}
