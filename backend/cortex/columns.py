"""Cortical columns + lateral inhibition (Law IV).

Domain knowledge organises into columns. Strong activation in one column
inhibits the others — this is focus. Cross-column hubs live in the
ASSOCIATION column and are where genuine synthesis emerges.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Column(str, Enum):
    CODING       = "coding"
    FITNESS      = "fitness"
    HEALTH       = "health"
    GENERAL      = "general"
    ASSOCIATION  = "association"   # cross-domain hub layer

    @classmethod
    def domain_columns(cls) -> list["Column"]:
        return [cls.CODING, cls.FITNESS, cls.HEALTH, cls.GENERAL]


# Keyword hints used to assign nodes to columns when no explicit column is given.
COLUMN_KEYWORDS: dict[Column, tuple[str, ...]] = {
    Column.CODING: (
        "python", "javascript", "code", "function", "class", "api", "bug",
        "compile", "debug", "fastapi", "django", "react", "git", "type",
        "loop", "variable", "syntax", "framework", "library", "package",
    ),
    Column.FITNESS: (
        "exercise", "workout", "rep", "set", "deadlift", "squat", "bench",
        "cardio", "training", "muscle", "strength", "hypertrophy",
        "protein", "calorie", "macro", "recovery",
    ),
    Column.HEALTH: (
        "blood", "pressure", "heart", "disease", "symptom", "medication",
        "dose", "diagnosis", "patient", "clinical", "doctor", "therapy",
        "medical", "vitamin", "deficiency", "infection",
    ),
}


def infer_column(*tokens: str) -> Column:
    """Heuristic — first matching column wins, else GENERAL."""
    text = " ".join(tokens).lower()
    for col, kws in COLUMN_KEYWORDS.items():
        if any(kw in text for kw in kws):
            return col
    return Column.GENERAL


@dataclass
class ColumnState:
    """Aggregate activation per column. Lateral inhibition operates on this."""
    activations: dict[Column, float] = field(
        default_factory=lambda: {c: 0.0 for c in Column}
    )
    inhibition_strength: float = 0.4   # how much winning column suppresses others

    def add(self, column: Column, delta: float) -> None:
        self.activations[column] = max(0.0, self.activations[column] + delta)

    def winner(self) -> Column | None:
        domain = {c: self.activations[c] for c in Column.domain_columns()}
        if not any(v > 0.0 for v in domain.values()):
            return None
        return max(domain, key=lambda c: domain[c])

    def lateral_inhibit(self) -> dict[Column, float]:
        """Return per-column inhibition factor in [0, 1].

        Winning column gets factor 1.0; others get (1 - inhibition_strength * winner_share).
        ASSOCIATION is exempt — cross-domain hubs are NOT suppressed by focus.
        """
        winner = self.winner()
        factors = {c: 1.0 for c in Column}
        if winner is None:
            return factors
        total = sum(self.activations[c] for c in Column.domain_columns()) or 1.0
        winner_share = self.activations[winner] / total
        for c in Column.domain_columns():
            if c is winner:
                continue
            factors[c] = max(0.0, 1.0 - self.inhibition_strength * winner_share)
        return factors
