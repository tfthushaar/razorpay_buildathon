"""Escalation triage (spec §6.6): rank escalated exceptions by rupee amount x ambiguity instead
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


def build_escalation_item(transaction_id: str, category: str, confidence: float, reasoning: str, amount: int) -> EscalationItem:
    ambiguity = 1.0 - confidence
    return EscalationItem(
        transaction_id=transaction_id,
        category=category,
        confidence=confidence,
        reasoning=reasoning,
        amount=amount,
        priority_score=amount * ambiguity,
    )


def triage(items: list[EscalationItem]) -> list[EscalationItem]:
    return sorted(items, key=lambda i: i.priority_score, reverse=True)
