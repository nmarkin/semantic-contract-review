"""Pure views of unapplied facts. A document card is not an applicability decision."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from html import escape
from typing import Iterable, Mapping
import re

from .models import Evidence, FactCandidate, Field, ParsedDocument, Role
from .patterns import MULTIPLE_VALUES_REASON, candidate_clause
from .presentation import REASON_LABELS, format_field, format_value
from .resolution_models import NotApplied, Reason, ResolvedContract, ResolvedField, ResolutionState
from .status_context import future_notice, issue_description, superseding_changes


@dataclass(frozen=True)
class SourceGroup:
    context: Evidence
    proofs: tuple[Evidence, ...]


@dataclass(frozen=True)
class ProposedChange:
    candidate: FactCandidate
    referenced: tuple[FactCandidate, ...]
    current: ResolvedField
    comparable: bool


@dataclass(frozen=True)
class DocumentCard:
    document: ParsedDocument
    title: str
    explanation: str
    changes: tuple[ProposedChange, ...]
    sources: tuple[SourceGroup, ...]
    items: tuple[NotApplied, ...]
    status_evidence: tuple[Evidence, ...] = ()


def source_groups(proofs: Iterable[Evidence], documents: Mapping[str, ParsedDocument]) -> tuple[SourceGroup, ...]:
    grouped: dict[tuple[str, int], list[Evidence]] = {}
    for proof in dict.fromkeys(proofs):
        proof.validate(documents[proof.document_id])
        grouped.setdefault((proof.document_id, proof.paragraph_id), []).append(proof)
    result = []
    for (document_id, paragraph_id), items in grouped.items():
        paragraph = next(p for p in documents[document_id].paragraphs if p.id == paragraph_id)
        context = Evidence(document_id, paragraph_id, paragraph.start, paragraph.end, paragraph.text)
        result.append(SourceGroup(context, tuple(items)))
    return tuple(result)


def grouped_source_html(group: SourceGroup, document: ParsedDocument) -> str:
    """One original paragraph with all selected offsets, escaped before markup."""
    group.context.validate(document)
    spans = []
    for proof in group.proofs:
        proof.validate(document)
        if not group.context.start <= proof.start < proof.end <= group.context.end:
            raise ValueError('Evidence outside source group')
        spans.append((proof.start, proof.end))
    merged = []
    for start, end in sorted(set(spans)):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    parts, cursor = [], group.context.start
    for start, end in merged:
        parts.extend((escape(document.original_text[cursor:start]), '<mark>',
                      escape(document.original_text[start:end]), '</mark>'))
        cursor = end
    parts.append(escape(document.original_text[cursor:group.context.end]))
    return '<pre class="source-quote" style="white-space:pre-wrap;overflow-wrap:anywhere;font:inherit">' + ''.join(parts) + '</pre>'


def _proposal_packages(candidates, document):
    """One clause's alternatives stay together; repeated field predicates split.

    Disjoint amount/deadline clauses form one package unless explicitly marked
    independent. This is a limited structural rule, not semantic inference.
    """
    clauses = {}
    for candidate in sorted(candidates, key=lambda c: c.evidence.start):
        clauses.setdefault(candidate_clause(candidate, document.paragraphs), []).append(candidate)
    packages, fields = [], set()
    for (_, start, end), group in clauses.items():
        changed = {c.field for c in group if c.role != Role.REFERENCED_TERM}
        if not changed:
            continue
        separate = re.search(r'\b(?:отдельно|независим\w*|другое\s+предложение)\b',
                             document.original_text[start:end], re.I)
        if not packages or fields & changed or separate:
            packages.append([])
            fields = set()
        packages[-1].extend(group)
        fields.update(changed)
    return packages or [list(candidates)]


def document_cards(contract: ResolvedContract, items: Iterable[NotApplied]) -> tuple[DocumentCard, ...]:
    grouped: dict[str, list[NotApplied]] = {}
    for item in items:
        grouped.setdefault(item.extraction.document.id, []).append(item)
    cards = []
    for entries in grouped.values():
        extraction = entries[0].extraction
        document = extraction.document
        candidates = tuple(dict.fromkeys(c for item in entries for c in (
            item.candidates or tuple(c for f in item.fields for c in item.extraction.get(f).candidates))))
        changes = []
        for candidate in candidates:
            if candidate.role == Role.REFERENCED_TERM:
                continue
            clause = candidate_clause(candidate, document.paragraphs)
            peers = [c for c in candidates if c.field == candidate.field
                     and candidate_clause(c, document.paragraphs) == clause]
            refs = tuple(c for c in peers if c.role == Role.REFERENCED_TERM)
            # Independent proposals remain independent even within one paragraph.
            # Ambiguous alternatives in a single clause are not used for arithmetic.
            values = {(c.value, c.unit) for c in peers if c.role != Role.REFERENCED_TERM}
            reasons = extraction.get(candidate.field).reasons
            comparable = len(values) == 1 and not any(r != MULTIPLE_VALUES_REASON for r in reasons)
            changes.append(ProposedChange(candidate, refs, contract.get(candidate.field), comparable))
        reasons = tuple(dict.fromkeys(item.reason for item in entries))
        is_proposal = any(c.role == Role.PROPOSAL for c in candidates)
        if Reason.UNSIGNED_DRAFT in reasons:
            title, explanation = 'Неподписанный проект', 'Проект не подписан. Предложенные условия не применены.'
        elif Reason.SUPPLIER_LETTER in reasons:
            title = 'Предложен перенос поставки' if all(c.field == Field.DELIVERY_DEADLINE for c in candidates) else 'Предложение поставщика'
            explanation = 'Не согласовано. Предложение не изменяет действующие условия.'
        elif is_proposal and Reason.NON_OPERATIVE_ROLE in reasons:
            title, explanation = 'Отдельное предложение в документе', 'Не согласовано. Предложенные условия не применены; другие условия документа оцениваются отдельно.'
        else:
            title = 'Непримененные условия'
            explanation = '. '.join(dict.fromkeys(issue_description(contract, i) for entry in entries for i in entry.issues)) or '. '.join(REASON_LABELS[r] for r in reasons)
        temporal = future_notice(contract, document)
        status_proofs = [c.evidence for c in document.metadata_for(Field.DOCUMENT_DATE).candidates] if temporal else []
        if temporal:
            explanation = temporal + ' ' + explanation
        status_proofs.extend(c.evidence for entry in entries for issue in entry.issues
                             for step in superseding_changes(contract, issue) for c in step.sources)
        packages = _proposal_packages(candidates, document) if all(e in contract.proposals for e in entries) else [candidates]
        for package in packages:
            proofs = [c.evidence for c in package]
            if not proofs:
                proofs = [proof for item in entries for issue in item.issues for proof in issue.evidence
                          if proof.document_id == document.id]
            cards.append(DocumentCard(document, title, explanation,
                                      tuple(c for c in changes if c.candidate in package),
                                      source_groups(proofs, {document.id: document}), tuple(entries),
                                      tuple(dict.fromkeys(status_proofs))))
    return tuple(cards)


def difference(current: tuple[FactCandidate, ...], proposed: tuple[FactCandidate, ...]) -> str | None:
    """Caller provides confirmed history/current sources and reliable target facts."""
    if len({(c.value, c.unit) for c in current}) != 1 or len({(c.value, c.unit) for c in proposed}) != 1:
        return None
    before, after = current[0], proposed[0]
    if before.unit != after.unit:
        return None
    if type(before.value) is date and type(after.value) is date:
        days = (after.value - before.value).days
        n = abs(days)
        word = 'дней' if 11 <= n % 100 <= 14 else ('день' if n % 10 == 1 else 'дня' if n % 10 in (2, 3, 4) else 'дней')
        return f'{days:+d} {word}'
    if isinstance(before.value, Decimal) and isinstance(after.value, Decimal) and before.unit == 'RUB':
        delta = after.value - before.value
        return ('+' if delta >= 0 else '') + format_value(delta)
    return None


def change_lines(change: ProposedChange) -> tuple[str, ...]:
    candidate, current = change.candidate, change.current
    is_date = candidate.field == Field.DELIVERY_DEADLINE
    label = 'срок' if is_date else 'сумма' if candidate.field == Field.TOTAL_AMOUNT else 'поставщик'
    current_label = 'Действующая сумма' if label == 'сумма' else f'Действующий {label}'
    target_label = ('Предлагаемая сумма' if label == 'сумма' else f'Предлагаемый {label}') if candidate.role == Role.PROPOSAL else f'Непримененное значение ({label})'
    lines = [f'{current_label}: {format_field(current)}', f'{target_label}: {format_value(candidate.value)}']
    if change.referenced:
        values = ', '.join(dict.fromkeys(format_value(c.value) for c in change.referenced))
        lines.append('Упомянутое прежнее значение — контекст: ' + values)
        if current.state == ResolutionState.RESOLVED and any(c.value != current.value for c in change.referenced):
            lines.append('Расхождение: прежнее значение в документе отличается от подтвержденного действующего условия.')
    if candidate.role == Role.PROPOSAL and current.state == ResolutionState.RESOLVED and change.comparable:
        delta = difference(current.sources, (candidate,))
        if delta is not None:
            lines.append('Разница с действующим условием: ' + delta)
    if not change.comparable:
        lines.append('Сравнение не рассчитано: формулировка содержит неоднозначность или несколько вариантов.')
    return tuple(lines)
