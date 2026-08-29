/**
 * The page map, kept in one place so navigation, routing and titles cannot drift apart.
 *
 * Grouped by the question a reader is asking rather than by subsystem. That ordering is also the
 * order a sceptical reviewer asks them in, which is why "Findings" is the landing page and "Probe"
 * is last.
 *
 *   findings   what did you learn            the argument, from committed evidence, no backend
 *   reconcile  what does it actually do      the analyst's working view: queue, and money found
 *   autonomy   how much does it do alone     residual, calibration, revocation
 *   evidence   how do I know it works        baseline, stress, and the range beyond reconciliation
 *   probe      can I break it                hand-crafted scenarios, free-text Q&A, audit trail
 *
 * `needsRun` marks pages that render a batch result. Those show a slim run-context strip; the run
 * controls themselves live on `reconcile` alone, so they are not repeated on four pages.
 */
export const PAGES = [
  { id: "findings", label: "Findings", question: "what I learned", needsRun: false },
  { id: "reconcile", label: "Reconcile", question: "what it does", needsRun: true },
  { id: "autonomy", label: "Autonomy", question: "what it does alone", needsRun: true },
  { id: "evidence", label: "Evidence", question: "how I know", needsRun: true },
  { id: "probe", label: "Probe", question: "try to break it", needsRun: true },
] as const;

export type PageId = (typeof PAGES)[number]["id"];

export const PAGE_IDS = PAGES.map((p) => p.id) as readonly PageId[];

export const DEFAULT_PAGE: PageId = "findings";
