"""Small shared patterns. Matching never rewrites the source text."""

import re
from datetime import date
from typing import Iterable

from .models import Evidence, FactCandidate, Field, FieldResult, Paragraph, State

FLAGS = re.IGNORECASE
DATE = r"(?<!\d)\d{2}\.\d{2}\.\d{4}(?!\d)"
DATE_RE = re.compile(DATE)
MULTIPLE_VALUES_REASON = "Найдено несколько различных значений; выбор действующего не выполнялся"


def parse_date(raw: str) -> date | None:
    day, month, year = map(int, raw.split("."))
    try:
        return date(year, month, day)
    except ValueError:
        return None


def evidence(document_id: str, paragraph: Paragraph, start: int = 0,
             end: int | None = None) -> Evidence:
    end = len(paragraph.text) if end is None else end
    return Evidence(document_id, paragraph.id, paragraph.start + start,
                    paragraph.start + end, paragraph.text[start:end])


def result(field: Field, candidates: Iterable[FactCandidate],
           issues: Iterable[str] = (), missing_reason: str = "Явное значение не найдено") -> FieldResult:
    items = tuple(candidates)
    reasons = tuple(dict.fromkeys(issues))
    distinct = {(item.value, item.unit) for item in items}
    if reasons or len(distinct) > 1:
        if len(distinct) > 1:
            reasons += (MULTIPLE_VALUES_REASON,)
        return FieldResult(field, State.AMBIGUOUS, items, reasons)
    if items:
        return FieldResult(field, State.CONFIDENT, items)
    return FieldResult(field, State.MISSING, reasons=(missing_reason,))


def lines(paragraph: Paragraph):
    """Nonempty lines with offsets relative to the containing paragraph."""
    for match in re.finditer(r"[^\r\n]+", paragraph.text):
        yield match.start(), match.group()


def clauses(paragraph: Paragraph):
    """Split sentences without splitting numeric dates, decimals or clause numbers."""
    start = 0
    for boundary in re.finditer(r"[!?;](?:\s+|$)|\.(?=\s+[А-ЯЁ«]|\s*$)|\r?\n", paragraph.text):
        end = boundary.end()
        yield start, paragraph.text[start:end]
        start = end
    if start < len(paragraph.text):
        yield start, paragraph.text[start:]


def candidate_clause(candidate: FactCandidate, paragraphs: tuple[Paragraph, ...]) -> tuple[int, int, int]:
    """Address of the source clause, without rewriting text or merging a paragraph."""
    paragraph = next(p for p in paragraphs if p.id == candidate.evidence.paragraph_id)
    for offset, text in clauses(paragraph):
        start = paragraph.start + offset
        if start <= candidate.evidence.start < candidate.evidence.end <= start + len(text):
            return paragraph.id, start, start + len(text)
    # No reliable clause boundary: keep this fact independent, not paragraph-wide.
    return paragraph.id, candidate.evidence.start, candidate.evidence.end
