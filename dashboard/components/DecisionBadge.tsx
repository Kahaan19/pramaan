import type { Decision } from "@/lib/types";

const STYLES: Record<Decision, string> = {
  ALLOW: "bg-emerald-100 text-emerald-800 border-emerald-300 dark:bg-emerald-950 dark:text-emerald-300 dark:border-emerald-800",
  STEP_UP: "bg-amber-100 text-amber-800 border-amber-300 dark:bg-amber-950 dark:text-amber-300 dark:border-amber-800",
  DENY: "bg-red-100 text-red-800 border-red-300 dark:bg-red-950 dark:text-red-300 dark:border-red-800",
};

const LABELS: Record<Decision, string> = {
  ALLOW: "ALLOW",
  STEP_UP: "STEP-UP",
  DENY: "BLOCKED",
};

export function DecisionBadge({ decision }: { decision: Decision | null }) {
  if (!decision) {
    return (
      <span className="inline-flex items-center rounded-full border border-zinc-300 bg-zinc-100 px-2.5 py-0.5 text-xs font-semibold text-zinc-600 dark:border-zinc-700 dark:bg-zinc-800 dark:text-zinc-400">
        UNKNOWN
      </span>
    );
  }
  return (
    <span className={`inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-semibold ${STYLES[decision]}`}>
      {LABELS[decision]}
    </span>
  );
}

export function IntegrityBadge({ status }: { status: "OK" | "BROKEN" | "UNKNOWN" }) {
  if (status === "OK") {
    return (
      <span className="inline-flex items-center gap-1 rounded-full border border-emerald-300 bg-emerald-50 px-2 py-0.5 text-[11px] font-medium text-emerald-700 dark:border-emerald-800 dark:bg-emerald-950 dark:text-emerald-400">
        ✓ chain intact
      </span>
    );
  }
  if (status === "BROKEN") {
    return (
      <span className="inline-flex items-center gap-1 rounded-full border border-red-400 bg-red-50 px-2 py-0.5 text-[11px] font-bold text-red-700 dark:border-red-700 dark:bg-red-950 dark:text-red-400">
        ⚠ TAMPERED
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1 rounded-full border border-zinc-300 bg-zinc-50 px-2 py-0.5 text-[11px] text-zinc-500 dark:border-zinc-700 dark:bg-zinc-900">
      ? unknown
    </span>
  );
}
