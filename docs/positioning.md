# Where this fits in Razorpay's own stack

[Razorpay Recon](https://razorpay.com/newsroom/razorpay-pos-launches-industry-first-ai-powered-razorpay-recon-to-automate-reconciliation-for-businesses-boosting-financial-operations-efficiency-by-80/)
(launched December 2024) is real, AI-powered, rule-based batch matching across 200M+ transactions/month
— built for offline POS reconciliation at volume, not for narrating *why* one specific transaction
broke or auditing fee correctness per instrument.
[Settlement Insights](https://razorpay.com/blog/agent-studio-ai-agents-by-razorpay/) (launched as
part of Agent Studio, March 12, 2026) sends a daily WhatsApp settlement summary — genuinely useful,
and genuinely a different job: a summary of what happened, not a causal explanation of *why* a
specific transaction diverges or a fee correctness audit. Neither product classifies an exception's
root cause with a tool-call trace, tracks its own per-category accuracy before trusting itself to
auto-resolve, or separates GST-on-fee into an ITC-ready journal line. This system starts where those
stop — at the moment a settlement needs to become an audited, ERP-ready set of books, not just a
matched or summarized one. `POST /api/transactions/evaluate` is a plausible integration point for
output either product already produces.

The regulatory correction behind the fee-leak detector's design (checking a merchant's own
contracted rate rather than a blanket legal claim) is covered in
[the architecture doc](track04-settlement-reconciliation-copilot.md#9-beyond-the-original-spec-fee-leak-detection-and-erp-posting-added-post-build).

## One trajectory worth naming

Razorpay and NPCI already [launched agentic payments on Claude](https://razorpay.com/blog/agentic-payments-and-npci/)
in February 2026 — Zomato, Swiggy, and Zepto are live in pilot, letting an AI agent complete a food
or grocery order inside a conversation, no app switch, no manual payment entry. As that surface
grows, the reconciliation problem doesn't shrink, it multiplies: a human merchant generates one
settlement per cycle, an agent fleet generates however many the conversation volume demands, with no
person in the loop to notice an anomaly. The same calibrated-autonomy design — earn trust per
exception category before acting on it, escalate everything else with a stated reason — is the right
shape for that world regardless of who (or what) is on the other end of the transaction. This system
doesn't need to be rebuilt for agentic commerce; it needs to keep doing exactly what it already does,
at whatever volume shows up.

A near/medium/longer-term build-next roadmap (Recon integration, per-merchant contract ingestion,
calibration as a shared cross-merchant signal) is condensed to a closing paragraph in the main
[README](../README.md); this file is the detailed positioning behind it.
