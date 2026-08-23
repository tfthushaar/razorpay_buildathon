import type { AuditEntry, BatchRunResult, CalibrationReport, EvaluateResponse, ResolveResponse } from "./types";

const BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(`${BASE_URL}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
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
}

export const runBatch = (req: RunRequest) =>
  request<BatchRunResult>("/api/run", { method: "POST", body: JSON.stringify(req) });

export const getLatestRun = () => request<BatchRunResult>("/api/runs/latest");

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
