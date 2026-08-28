"""Escalation triage : rank escalated exceptions by rupee amount x ambiguity instead
of arrival order, so the highest-value, least-certain cases surface first — how a reconciliation
ops team would actually work the queue, not just a dump of unresolved rows."""

from pydantic import BaseModel


class EscalationItem(BaseModel):
    transaction_id: str
    category: str
    confidence: float
    reasoning: str
    amount: int
    priority_score: float
    provider: str  # which narrator backend produced this prediction -- carried through so a
    # human resolving it (the feedback loop) records the confirmation against the right
    # provider, and a mock-derived guess can't count toward the AI-judgment gate just because a
    # human later looked at it.


def build_escalation_item(transaction_id: str, category: str, confidence: float, reasoning: str, amount: int, provider: str) -> EscalationItem:
    ambiguity = 1.0 - confidence
    return EscalationItem(
        transaction_id=transaction_id,
        category=category,
        confidence=confidence,
        reasoning=reasoning,
        amount=amount,
        priority_score=amount * ambiguity,
        provider=provider,
    )


def triage(items: list[EscalationItem]) -> list[EscalationItem]:
    return sorted(items, key=lambda i: i.priority_score, reverse=True)
