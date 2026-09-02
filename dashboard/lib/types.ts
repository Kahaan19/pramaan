export type Decision = "ALLOW" | "STEP_UP" | "DENY";
export type IntegrityStatus = "OK" | "BROKEN" | "UNKNOWN";

export interface TransactionSummary {
  transaction_id: string;
  cart_id: string | null;
  ts: string | null;
  headline: string;
  decision: Decision | null;
  rule_fired: string | null;
  integrity_status: IntegrityStatus;
}

export interface RecentTransactionsResponse {
  transactions: TransactionSummary[];
}

export interface CartItem {
  sku: string;
  qty: number;
  unit_price_paise: number;
}

export interface StepUpSummary {
  cart_id: string;
  user_id: string;
  intent_id: string;
  merchant_id: string;
  amount_paise: number;
  items: CartItem[];
  category: string | null;
  max_amount_paise: number;
  human_present: boolean;
  rule_fired: string;
  reason: string;
  status: string;
  created_at: string;
  expires_at: string;
  intent_expires_at: string;
}

export interface StepUpQueueResponse {
  pending: StepUpSummary[];
}

export interface ExplainResponse {
  found: boolean;
  transaction_id: string | null;
  headline: string;
  narrative: string[];
  integrity_status: IntegrityStatus;
  integrity_findings: string[];
}

export interface ChainFinding {
  kind: string;
  seq: number | null;
  detail: string;
}

export interface VerifyResponse {
  ok: boolean;
  row_count: number;
  head_seq: number | null;
  head_hash: string | null;
  findings: ChainFinding[];
}

export interface Verdict {
  decision: Decision;
  rule_fired: string | null;
  reason: string;
  evaluated_at: string;
  rules_version: number;
  rules_sha256: string;
}

export interface CheckoutRefs {
  status: string;
  amount_paise: number;
  order_id: string | null;
  payment_link_id: string | null;
  payment_id: string | null;
}

export interface DecisionActionResponse {
  verdict?: Verdict;
  checkout?: CheckoutRefs;
}
