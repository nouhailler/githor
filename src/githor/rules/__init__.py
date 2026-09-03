"""Moteur de règles : transforme les données collectées en constats."""

from githor.rules.base import InactivityRule, MarkerRule, Rule, RuleContext, Verdict
from githor.rules.catalog import (
    CATEGORIES,
    CATEGORY_LABELS,
    RULES,
    STALE_AFTER_DAYS,
    rule_labels,
    rules_by_category,
)
from githor.rules.engine import evaluate, open_findings

__all__ = [
    "CATEGORIES",
    "CATEGORY_LABELS",
    "RULES",
    "STALE_AFTER_DAYS",
    "InactivityRule",
    "MarkerRule",
    "Rule",
    "RuleContext",
    "Verdict",
    "evaluate",
    "open_findings",
    "rule_labels",
    "rules_by_category",
]
