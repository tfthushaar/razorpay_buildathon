import type {
  AuditEntry,
  BacktestReport,
  BatchRunResult,
  CalibrationReport,
  EvaluateResponse,
  JournalExportResponse,
  PayrollCoverageResult,
  PendingForecastResponse,
  Gstr2bResponse,
  QAAnswer,
  RegretReport,
  ResolveResponse,
  RevocationDrillReport,
} from "./types";

const BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let resp: Response;
  try {
    resp = await fetch(`${BASE_URL}${path}`, {
      ...init,
      headers: { "Content-Type": "application/json", ...init?.headers },
    });
  } catch {
    // fetch() itself rejects (before any response exists) when the backend isn't reachable at
    // all -- refused connection, DNS failure, a CORS preflight block. The raw error here is a
    // browser-specific, unhelpful "TypeError: Failed to fetch" with no indication of what to do
    // about it. Surfacing something actionable instead is itself a failure-recovery property, not
    // just cosmetic: the difference between "the backend rejected this request" (handled below)
    // and "there's no backend to talk to" is exactly the distinction a user needs to know whether
    // to fix their input or go start a server.
    throw new Error(`Could not reach the backend at ${BASE_URL} — is it running? (cd backend && python -m uvicorn app.main:app --reload --port 8000)`);
  }
  if (!resp.ok) {
    const body = await resp.text();
    throw new Error(`${resp.status} ${resp.statusText}: ${body}`);
  }
  return resp.json() as Promise<T>;
}

export interface RunRequest {
  seed: number;
  main_n: number;
  stress_n: number;
  threshold: number;
  provider?: string;
  reset_history?: boolean;
  enable_discovery?: boolean;
  enable_multiway_netting?: boolean;
  enable_held_out_variants?: boolean;
  enable_narration_explained?: boolean;
  enable_compound_delta?: boolean;
  held_out_advice_phrasing?: boolean;
}

export const runBatch = (req: RunRequest) =>
  request<BatchRunResult>("/api/run", { method: "POST", body: JSON.stringify(req) });

export const getLatestRun = () => request<BatchRunResult>("/api/runs/latest");

export const getHealth = () => request<{ status: string }>("/api/health");

export interface ThreeSourceColumn {
  column: string;
  correct: number;
  total: number;
  accuracy: number;
  under_determined: number;
  unmatched: number;
  reachable: number;
}

export interface ThreeSourceResult {
  seed: number;
  n_settlements: number;
  n_bank_rows: number;
  held_out_phrasing: boolean;
  columns: ThreeSourceColumn[];
  mcnemar_vs_regex: Record<string, { wins: number; losses: number; p: number; p_conceding_two: number }>;
  note: string;
}

export const evaluateThreeSource = (heldOut: boolean) =>
  request<ThreeSourceResult>("/api/three-source/evaluate", {
    method: "POST",
    body: JSON.stringify({ seed: 42, n: 120, held_out_phrasing: heldOut }),
  });

export const getCalibration = (threshold: number) =>
  request<CalibrationReport>(`/api/calibration?threshold=${threshold}`);

export const resolveEscalation = (transaction_id: string) =>
  request<ResolveResponse>("/api/escalations/resolve", {
    method: "POST",
    body: JSON.stringify({ transaction_id }),
  });

export const getAudit = (runId?: string) =>
  request<AuditEntry[]>(runId ? `/api/audit?run_id=${runId}` : "/api/audit");

export const evaluateScenario = (scenarioJson: object) =>
  request<EvaluateResponse>("/api/transactions/evaluate", {
    method: "POST",
    body: JSON.stringify(scenarioJson),
  });

export const exportJournal = (format: "tally" | "zoho" | "generic") =>
  request<JournalExportResponse>(`/api/journal/export?format=${format}`);

export const getGstr2bMatch = () => request<Gstr2bResponse>("/api/gstr2b");

export const getPendingForecast = (n: number = 10) =>
  request<PendingForecastResponse>(`/api/forecast/pending?n=${n}`);

export const getForecastBacktest = () => request<BacktestReport>("/api/forecast/backtest");

export const getForecastBlindBacktest = () => request<BacktestReport>("/api/forecast/blind-backtest");

export const checkPayrollCoverage = (outflow_amount: number, outflow_date: string, n: number = 10) =>
  request<PayrollCoverageResult>("/api/forecast/payroll-check", {
    method: "POST",
    body: JSON.stringify({ outflow_amount, outflow_date, n }),
  });

export const askSettlementQuestion = (question: string, provider?: string) =>
  request<QAAnswer>("/api/qa/ask", {
    method: "POST",
    body: JSON.stringify({ question, provider }),
  });

export const getRegret = (threshold: number = 0.9) => request<RegretReport>(`/api/regret?threshold=${threshold}`);

export const runRevocationDrill = (category: string = "netting_trap") =>
  request<RevocationDrillReport>("/api/drift/drill", {
    method: "POST",
    body: JSON.stringify({ category }),
  });
