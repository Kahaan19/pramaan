"use client";

import { useState } from "react";
import { api } from "@/lib/api";
import { usePolling } from "@/lib/usePolling";
import { BlockedPanel } from "@/components/BlockedPanel";
import { ExplainPanel } from "@/components/ExplainPanel";
import { Header } from "@/components/Header";
import { StepUpQueue } from "@/components/StepUpQueue";
import { TransactionFeed } from "@/components/TransactionFeed";

const FEED_POLL_MS = 3000;
const QUEUE_POLL_MS = 3000;
const CHAIN_POLL_MS = 5000;

export default function DashboardPage() {
  const [selectedKey, setSelectedKey] = useState<string | null>(null);

  const feed = usePolling(() => api.recentTransactions(30), FEED_POLL_MS);
  const queue = usePolling(() => api.stepUpQueue(), QUEUE_POLL_MS);
  const chain = usePolling(() => api.verifyChain(), CHAIN_POLL_MS);

  const transactions = feed.data?.transactions ?? [];
  const blocked = transactions.filter((t) => t.decision === "DENY");

  return (
    <div className="flex min-h-screen flex-col bg-zinc-50 dark:bg-zinc-900">
      <Header chain={chain.data} />

      <main className="mx-auto grid w-full max-w-7xl flex-1 grid-cols-1 gap-4 p-4 lg:grid-cols-2">
        <TransactionFeed
          transactions={transactions}
          loading={feed.loading}
          error={feed.error}
          onSelect={setSelectedKey}
        />

        <StepUpQueue
          pending={queue.data?.pending ?? []}
          loading={queue.loading}
          error={queue.error}
          onResolved={() => {
            queue.refresh();
            feed.refresh();
          }}
          onSelect={setSelectedKey}
        />

        <BlockedPanel blocked={blocked} onSelect={setSelectedKey} />

        <ExplainPanel selectedKey={selectedKey} />
      </main>

      <footer className="border-t border-zinc-200 px-6 py-3 text-center text-[11px] text-zinc-400 dark:border-zinc-800">
        Test mode only. No real payments move. Both /ledger and /demo/step-up endpoints are unauthenticated in this
        demo — see README.
      </footer>
    </div>
  );
}
