export const rupees = (paise: number): string =>
  `₹${(paise / 100).toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

export const pct = (fraction: number, digits = 1): string => `${(fraction * 100).toFixed(digits)}%`;

export const categoryLabel = (category: string): string =>
  category
    .split("_")
    .map((w) => w[0].toUpperCase() + w.slice(1))
    .join(" ");

export interface ToolCallRecord {
  tool: string;
  arguments: Record<string, unknown>;
  result: Record<string, unknown>;
}

// Shared by AuditLogView and EscalationQueue -- both render the same
// tool_calls_json blob from an audit entry, so the parsing (and its failure mode) lives in one
// place rather than being copy-pasted per component.
export function parseToolCalls(json: string): ToolCallRecord[] {
  try {
    const parsed = JSON.parse(json);
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}
