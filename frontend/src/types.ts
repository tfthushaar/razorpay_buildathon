// Mirrors the backend's pydantic models (app/pipeline.py, app/calibration/calibrator.py,
// app/matching/escalation.py) field-for-field. Keep in sync by hand — no codegen for a build
// this size, per the cost/scope-discipline the whole project is built around.

export type Category =
  | "clean_match"
  | "timing_lag"
  | "fee_deduction"
  | "partial_refund"
  | "duplicate_refund"
  | "netting_trap"
  | "currency_rounding"
  | "genuine_error";

export interface CategoryCalibration {
  category: string;
  n: number;
  correct: number;
  accuracy: number;
  ci_lower: number;
  ci_upper: number;
  decision: "auto_resolve" | "escalate";
  reason: string;
  amount_total: number;
  distinct_amount_total: number;
  amount_at_risk: number;
  mock_n: number;
  distinct_transaction_count: number;
  ewma_accuracy: number;
  drift_alert: boolean;
}

export interface CalibrationReport {
  threshold: number;
  categories: CategoryCalibration[];
}

export interface EscalationItem {
  transaction_id: string;
  category: string;
  confidence: number;
  reasoning: string;
  amount: number;
  priority_score: number;
  provider: string;
}

export interface StressScorecard {
  total: number;
  deterministic_correct: number;
  narrated_total: number;
  narrated_correctly_handled: number;
  wrongly_auto_resolved: number;
}

export interface FeeLeakFinding {
  transaction_id: string;
  rail: string;
  pattern: "blended_rate_overcharge" | "gst_wrong_base";
  pattern_label: string;
  contracted_fee: number;
  actual_fee: number;
  fee_variance: number;
  contracted_gst: number;
  actual_gst: number;
  gst_variance: number;
  total_impact: number;
  dispute_template: string;
}

export interface FeeLeakReport {
  findings: FeeLeakFinding[];
  total_fee_recovery: number;
  total_gst_correction: number;
  by_pattern: Record<string, number>;
}

export interface BatchRunResult {
  run_id: string;
  seed: number;
  threshold: number;
  provider: string;
  total_transactions: number;
  total_amount: number;
  amount_reconciled: number;
  escalated_count: number;
  calibration: CalibrationReport;
  escalations: EscalationItem[];
  baseline_clean_count: number;
  baseline_false_negative_timing_lag: number;
  baseline_false_positive_rounding: number;
  deterministic_only_resolved_count: number;
  deterministic_only_amount_reconciled: number;
  stress: StressScorecard;
  fee_leak_report: FeeLeakReport;
  total_itc_separated: number;
  elapsed_seconds: number;
  narrated_count: number;
  transactions_per_second: number;
  category_proposals: CategoryProposal[];
}

export interface RegretReport {
  threshold: number;
  realized_regret_amount: number;
  realized_regret_transaction_count: number;
  auto_resolved_transaction_count: number;
  minutes_per_manual_review_assumption: number;
  estimated_analyst_hours_saved: number;
}

export interface CategoryProposal {
  transaction_id: string;
  proposed_name: string | null;
  hypothesis: string;
  supporting_evidence: string[];
  confidence: number;
  provider: string;
}

export interface JournalExportResponse {
  format: "tally" | "zoho" | "generic";
  content: string;
  entry_count: number;
  finalized_count: number;
  pending_count: number;
}

export interface SettlementPrediction {
  transaction_id: string;
  rail: string;
  captured_amount: number;
  predicted_fee: number;
  predicted_tax: number;
  predicted_net_amount: number;
  captured_at: string;
  predicted_date_low: string;
  predicted_date_high: string;
}

export interface AgedBucket {
  label: string;
  amount: number;
  count: number;
}

export interface WorkingCapitalReport {
  as_of: string;
  total_unsettled_net: number;
  total_unsettled_gross: number;
  by_rail: Record<string, number>;
  at_sla_risk_amount: number;
  aged_buckets: AgedBucket[];
}

export interface PendingForecastResponse {
  predictions: SettlementPrediction[];
  working_capital: WorkingCapitalReport;
}

export interface ForwardCurvePoint {
  settlement_date: string;
  predicted_amount: number;
  actual_amount: number;
}

export interface BacktestReport {
  n: number;
  mape: number;
  interval_coverage: number;
  forward_curve: ForwardCurvePoint[];
}

export interface PayrollCoverageResult {
  outflow_amount: number;
  outflow_date: string;
  predicted_available_amount: number;
  clears: boolean;
  shortfall_amount: number;
}

export interface ResolveResponse {
  transaction_id: string;
  predicted_category: string;
  confirmed_true_label: string;
  was_correct: boolean;
  updated_calibration: CalibrationReport;
}

export interface EvaluatedTransaction {
  transaction_id: string;
  resolution: string;
  category: string | null;
  confidence: number | null;
  reasoning: string | null;
  tool_calls: Record<string, unknown>[];
}

export interface EvaluateResponse {
  results: EvaluatedTransaction[];
}

export interface QAToolCallRecord {
  tool: string;
  arguments: Record<string, unknown>;
  result: Record<string, unknown>;
}

export interface QAAnswer {
  question: string;
  answer: string;
  cited_transaction_ids: string[];
  tool_calls: QAToolCallRecord[];
  provider: string;
}

export interface AuditEntry {
  id: number;
  run_id: string;
  transaction_id: string;
  decision: string;
  category: string | null;
  confidence: number | null;
  reasoning: string | null;
  tool_calls_json: string;
  order_id: string;
  payment_id: string;
  settlement_id: string;
  ledger_id: string;
  refund_ids_json: string;
  timestamp: string;
}
