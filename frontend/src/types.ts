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
  | "genuine_error"
  | "multiway_netting_trap"
  | "narration_explained"
  | "compound_delta";

// Residual architecture (backend app/resolver/). Layer 0 runs over every exception the matching
// engine could not close; the model is handed only what Layer 0 could not finish.
export interface ResidualComponent {
  cause: string;
  amount: number;
  evidence_ref: string;
  why: string;
}

export interface ResidualCase {
  transaction_id: string;
  status: "RESOLVED" | "UNDER_DETERMINED" | "UNMATCHED";
  observed_delta: number;
  ambiguity: number;
  chance_baseline: number;
  candidate_pool_size: number;
  reached_model: boolean;
  provider: string;
  verified: boolean;
  verify_rounds: number;
  components: ResidualComponent[];
  reasoning: string;
  parsimony_choice: ResidualComponent[];
  keyword_choice: ResidualComponent[];
  keyword_ties: number;
}

export interface ResidualReport {
  tolerance: number;
  closed_before_stage: number;
  layer0_resolved: number;
  under_determined: number;
  unmatched: number;
  model_calls: number;
  model_verified: number;
  cases: ResidualCase[];
  total: number;
  deterministic_share: number;
  layer0_share_of_exceptions: number;
  mean_chance_baseline: number;
}

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
  pattern: "blended_rate_overcharge" | "gst_wrong_base" | "gst_wrong_rate" | "gst_miscomputed";
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
  disposition: {
    total: number;
    correctly_resolved: number;
    wrongly_resolved: number;
    missed: number;
    correctly_escalated: number;
  } | null;
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
  residual: ResidualReport | null;
}

export interface RevocationDrillReport {
  category: string;
  threshold: number;
  qualifying_decision_count: number;
  revoked: boolean;
  decisions_survived: number | null;
  amount_survived: number | null;
  revocation_reason: string | null;
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

export interface Gstr2bException {
  transaction_id: string;
  kind: "missing_in_gstr2b" | "amount_mismatch" | "blocked_credit";
  our_itc_amount: number;
  gstr2b_itc_amount: number | null;
  detail: string;
}

export interface Gstr2bMatchReport {
  matched_count: number;
  matched_itc_amount: number;
  exceptions: Gstr2bException[];
  exception_itc_amount: number;
  by_kind: Record<string, number>;
}

export interface Gstr2bResponse {
  formatted: { part_a_eligible_itc: Record<string, unknown>[]; part_b_ineligible_itc: Record<string, unknown>[]; total_eligible_itc: number; supplier_gstin: string };
  match_report: Gstr2bMatchReport;
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
  exact_rate: number;
  median_ape: number;
  mape: number;
  p95_ape: number;
  worst_ape: number;
  n_undefined_ape: number;
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
