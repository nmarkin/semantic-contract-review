"""Deterministic, fail-closed resolution of the exercise's contract rules."""

from collections import defaultdict
from dataclasses import replace
from datetime import date
from typing import Iterable

from .models import (
    DocumentKind, ExtractionResult, FactCandidate, FactValue, Field, Role, SigningStatus, State,
)
from .patterns import MULTIPLE_VALUES_REASON, candidate_clause
from .resolution_models import (
    AppliedChange, GroupingProblem, NotApplied, Reason, ResolutionIssue,
    ResolutionReport, ResolutionState, ResolvedContract, ResolvedField,
)

TERMS = (Field.TOTAL_AMOUNT, Field.DELIVERY_DEADLINE)
OUTPUT_FIELDS = (Field.SUPPLIER, *TERMS)
NON_OPERATIVE = (Role.PROPOSAL, Role.REFERENCED_TERM)
FieldEvent = tuple[ExtractionResult, tuple[FactCandidate, ...], tuple[ResolutionIssue, ...]]


def _candidate_key(candidate: FactCandidate) -> tuple:
    # This orders evidence for reproducible output, never competing values.
    return (candidate.evidence.document_id, candidate.evidence.start, candidate.evidence.end)


def _value(extraction: ExtractionResult, field: Field) -> FactValue | None:
    result = extraction.get(field)
    if result.state == State.CONFIDENT and result.candidates:
        return result.candidates[0].value
    return None


def _issue(extraction: ExtractionResult, reason: Reason, message: str,
           fields: tuple[Field, ...]) -> ResolutionIssue:
    relevant = (*fields, Field.CONTRACT_NUMBER, Field.SIGNING_STATUS,
                Field.SIGNING_DATE, Field.EFFECTIVE_DATE)
    proof = []
    for field in dict.fromkeys(relevant):
        item = extraction.get(field)
        proof.extend(c.context for c in item.candidates)
        proof.extend(item.explanations)
    return ResolutionIssue(reason, message, (extraction.document.id,), fields,
                           tuple(dict.fromkeys(proof)))


def _affected(extraction: ExtractionResult) -> tuple[Field, ...]:
    """Only potentially operative changes can make a current term uncertain."""
    return tuple(field for field in TERMS if (
        any(c.role not in NON_OPERATIVE for c in extraction.get(field).candidates)
        or (extraction.get(field).state == State.AMBIGUOUS
            and not extraction.get(field).candidates)
        or extraction.get(field).explanations
    ))


def _select(extraction: ExtractionResult, field: Field,
            roles: tuple[Role, ...]) -> tuple[tuple[FactCandidate, ...], tuple[str, ...]]:
    item = extraction.get(field)
    candidates = tuple(sorted((c for c in item.candidates if c.role in roles), key=_candidate_key))
    problems = []
    if item.state == State.AMBIGUOUS:
        # Old/new values and proposals can make extraction ambiguous; resolve
        # only the candidates with the requested semantic role, keeping actual
        # parsing problems (invalid dates, unsupported syntax, etc.) blocking.
        problems.extend(reason for reason in item.reasons if reason != MULTIPLE_VALUES_REASON)
        if not item.reasons:
            problems.append("Неоднозначное извлечение без уточненной причины")
    if any(c.role not in (*roles, *NON_OPERATIVE) for c in item.candidates):
        problems.append("Роль найденного значения не подтверждает действующее условие")
    if len({(c.value, c.unit) for c in candidates}) > 1:
        problems.append("Несколько различных значений изменяемого поля")
    return candidates, tuple(problems)


def _review(field: ResolvedField, issues: tuple[ResolutionIssue, ...]) -> ResolvedField:
    return replace(field, state=ResolutionState.REQUIRES_REVIEW, sources=(),
                   issues=field.issues + issues)


def _gate(extraction: ExtractionResult, as_of: date, *, base: bool = False) -> tuple[Reason, str] | None:
    """Return a reason and message on rejection; no document-date substitution."""
    classification = extraction.document.classification
    if not base:
        if classification.kind == DocumentKind.UNSIGNED_DRAFT:
            return Reason.UNSIGNED_DRAFT, "Неподписанный проект не изменяет условия"
        if classification.kind == DocumentKind.SUPPLIER_LETTER:
            return Reason.SUPPLIER_LETTER, "Письмо содержит предложение, а не согласованное изменение"
    expected_kind = DocumentKind.CONTRACT if base else DocumentKind.SIGNED_AMENDMENT
    if classification.state != State.CONFIDENT or classification.kind != expected_kind:
        return Reason.UNCONFIRMED_APPLICABILITY, "Тип или статус документа не подтвержден однозначно"
    if _value(extraction, Field.SIGNING_STATUS) != SigningStatus.BOTH_PARTIES:
        return Reason.UNCONFIRMED_APPLICABILITY, "Нет однозначного подтверждения подписания обеими сторонами"
    signed = extraction.get(Field.SIGNING_DATE)
    if signed.state == State.AMBIGUOUS:
        return Reason.UNCONFIRMED_APPLICABILITY, "Указанная дата подписания не установлена надежно"
    signing_date = _value(extraction, Field.SIGNING_DATE)
    if signing_date is not None and signing_date > as_of:
        return Reason.SIGNED_AFTER_AS_OF, "Документ подписан после даты проверки"
    effective = extraction.get(Field.EFFECTIVE_DATE)
    effective_date = _value(extraction, Field.EFFECTIVE_DATE)
    if effective.state == State.AMBIGUOUS or (effective_date is None and not base):
        return Reason.UNCONFIRMED_APPLICABILITY, "Нет однозначной даты вступления в силу"
    if effective_date is not None and effective_date > as_of:
        return Reason.NOT_EFFECTIVE_YET, "Документ еще не вступил в силу на дату проверки"
    return None


def _supplier_conflict(base: ExtractionResult, change: ExtractionResult) -> bool:
    left, right = base.get(Field.SUPPLIER), change.get(Field.SUPPLIER)
    if right.state == State.AMBIGUOUS:
        return True
    return (left.state == State.CONFIDENT and right.state == State.CONFIDENT
            and _value(base, Field.SUPPLIER) != _value(change, Field.SUPPLIER))


def _baseline(base: ExtractionResult, field: Field) -> ResolvedField:
    roles = (Role.METADATA,) if field == Field.SUPPLIER else (Role.ORIGINAL_TERM,)
    sources, problems = _select(base, field, roles)
    explanation = base.get(field).explanations
    if problems:
        issue = _issue(base, Reason.AMBIGUOUS_FIELD, "; ".join(problems), (field,))
        return ResolvedField(field, ResolutionState.REQUIRES_REVIEW,
                             issues=(issue,), explanations=explanation)
    if not sources:
        return ResolvedField(field, ResolutionState.MISSING, explanations=explanation)
    initial = AppliedChange(_value(base, Field.EFFECTIVE_DATE), sources, initial=True)
    return ResolvedField(field, ResolutionState.RESOLVED, sources, (initial,), explanations=explanation)


def _resolve_group(number: str, documents: tuple[ExtractionResult, ...],
                   uncertain_links: tuple[GroupingProblem, ...], as_of: date) -> ResolvedContract:
    bases = tuple(e for e in documents if e.document.classification.kind == DocumentKind.CONTRACT)
    warnings: list[ResolutionIssue] = []
    excluded: list[NotApplied] = []
    fields = {field: ResolvedField(field, ResolutionState.MISSING) for field in OUTPUT_FIELDS}
    # Dated events can be superseded. Undated uncertainties cannot be ordered
    # against trusted changes, so they remain blocking after the whole timeline.
    events: dict[Field, dict[date, list[FieldEvent]]] = {field: defaultdict(list) for field in TERMS}
    undated: dict[Field, list[ResolutionIssue]] = {field: [] for field in OUTPUT_FIELDS}
    baseline_usable = False

    def exclude(extraction, reason, message, affected, candidates=()):
        issue = _issue(extraction, reason, message, affected)
        excluded.append(NotApplied(extraction, reason, affected, (issue,), candidates))
        warnings.append(issue)
        return issue

    if len(bases) != 1:
        reason = Reason.MISSING_BASE if not bases else Reason.MULTIPLE_BASES
        issue = ResolutionIssue(reason, "Основной договор отсутствует" if not bases
                                else "Найдено несколько основных договоров; приоритет не установлен",
                                tuple(e.document.id for e in bases), OUTPUT_FIELDS,
                                tuple(c.evidence for e in bases for c in e.get(Field.CONTRACT_NUMBER).candidates))
        warnings.append(issue)
        for field in OUTPUT_FIELDS:
            undated[field].append(issue)
        base = None
    else:
        base = bases[0]
        rejection = _gate(base, as_of, base=True)
        if rejection:
            issue = exclude(base, *rejection, OUTPUT_FIELDS)
            for field in OUTPUT_FIELDS:
                undated[field].append(issue)
        else:
            baseline_usable = True
            fields = {field: _baseline(base, field) for field in OUTPUT_FIELDS}
            warnings.extend(issue for item in fields.values() for issue in item.issues)

    for extraction in documents:
        proposal_clauses = {candidate_clause(c, extraction.document.paragraphs)
                            for field in TERMS for c in extraction.get(field).candidates if c.role == Role.PROPOSAL}
        non_operative = tuple(c for field in TERMS for c in extraction.get(field).candidates
                              if c.role == Role.PROPOSAL or (c.role == Role.REFERENCED_TERM
                                  and candidate_clause(c, extraction.document.paragraphs) in proposal_clauses))
        affected = _affected(extraction)
        if extraction in bases:
            if non_operative:
                proposal_fields = tuple(f for f in TERMS if any(c.field == f for c in non_operative))
                exclude(extraction, Reason.NON_OPERATIVE_ROLE, "Самостоятельное предложение не изменяет действующие условия",
                        proposal_fields, non_operative)
            continue
        rejection = _gate(extraction, as_of)
        if rejection:
            reason, message = rejection
            all_candidates = tuple(c for field in TERMS for c in extraction.get(field).candidates)
            listed_fields = tuple(f for f in TERMS if extraction.get(f).candidates or f in affected)
            issue = exclude(extraction, reason, message, listed_fields, all_candidates)
            if base is not None and _supplier_conflict(base, extraction):
                warnings.append(_issue(extraction, Reason.SUPPLIER_CONFLICT,
                                       "Поставщик непримененного документа отличается от основного договора или неоднозначен",
                                       (Field.SUPPLIER, *affected)))
            if reason == Reason.UNCONFIRMED_APPLICABILITY:
                effective_on = _value(extraction, Field.EFFECTIVE_DATE)
                for field in affected:
                    if effective_on is not None and effective_on <= as_of:
                        events[field][effective_on].append((extraction, (), (issue,)))
                    elif effective_on is None:
                        undated[field].append(issue)
            continue
        if non_operative:
            proposal_fields = tuple(f for f in TERMS if any(c.field == f for c in non_operative))
            exclude(extraction, Reason.NON_OPERATIVE_ROLE, "Самостоятельное предложение не изменяет действующие условия",
                    proposal_fields, non_operative)
        effective_on = _value(extraction, Field.EFFECTIVE_DATE)
        if not baseline_usable:
            exclude(extraction, Reason.UNCONFIRMED_APPLICABILITY,
                    "Нет подтвержденного основного договора для применения изменения", affected)
            continue
        if _supplier_conflict(base, extraction):
            issue = exclude(extraction, Reason.SUPPLIER_CONFLICT,
                            "Поставщик связанного документа противоречит основному договору или неоднозначен",
                            (Field.SUPPLIER, *affected))
            undated[Field.SUPPLIER].append(issue)
            for field in affected:
                events[field][effective_on].append((extraction, (), (issue,)))
            continue
        for field in affected:
            sources, problems = _select(extraction, field, (Role.AMENDMENT_TERM,))
            if not sources and not problems:
                problems = ("Изменяемое значение отсутствует; требуется исходное приложение или уточнение",)
            if problems:
                issue = exclude(extraction, Reason.AMBIGUOUS_FIELD, "; ".join(problems), (field,))
                events[field][effective_on].append((extraction, (), (issue,)))
            else:
                events[field][effective_on].append((extraction, sources, ()))

    for problem in uncertain_links:
        warnings.append(problem.issue)
        extraction = problem.extraction
        rejection = _gate(extraction, as_of)
        if rejection and rejection[0] != Reason.UNCONFIRMED_APPLICABILITY:
            continue  # A known proposal/future change cannot alter current terms.
        for field in _affected(extraction):
            undated[field].append(problem.issue)

    for field in TERMS:
        current = fields[field]
        for effective_on, entries in sorted(events[field].items()):
            sources = tuple(sorted((c for _, cs, _ in entries for c in cs), key=_candidate_key))
            problems = tuple(i for _, _, issues in entries for i in issues)
            if len({(c.value, c.unit) for c in sources}) > 1:
                conflict = ResolutionIssue(Reason.SAME_DATE_CONFLICT,
                                           "Конфликтующие изменения поля с одной датой вступления в силу",
                                           tuple(sorted({e.document.id for e, _, _ in entries})), (field,),
                                           tuple(c.evidence for c in sources))
                warnings.append(conflict)
                problems += (conflict,)
                for extraction, cs, _ in entries:
                    excluded.append(NotApplied(extraction, Reason.SAME_DATE_CONFLICT, (field,), (conflict,), cs))
            if problems:
                current = _review(current, problems)
                # Also retain otherwise valid changes blocked by a simultaneous
                # uncertain change, without claiming they were applied.
                for extraction, cs, own_issues in entries:
                    if cs and not own_issues and not any(i.reason == Reason.SAME_DATE_CONFLICT for i in problems):
                        excluded.append(NotApplied(extraction, Reason.UNCONFIRMED_APPLICABILITY,
                                                   (field,), problems, cs))
            elif sources:
                change = AppliedChange(effective_on, sources, current.sources)
                current = ResolvedField(field, ResolutionState.RESOLVED, sources,
                                        current.history + (change,))
        fields[field] = current
    for field in OUTPUT_FIELDS:
        if undated[field]:
            fields[field] = _review(fields[field], tuple(undated[field]))
    return ResolvedContract(number, tuple(fields[f] for f in OUTPUT_FIELDS), documents,
                            tuple(excluded), tuple(dict.fromkeys(warnings)), as_of)


def resolve_contracts(extractions: Iterable[ExtractionResult], as_of: date) -> ResolutionReport:
    """Resolve only supported, evidenced terms on an explicitly supplied date.

    Ambiguous references are returned separately and can flag affected known
    contracts for review, but never attach a document to an arbitrary contract.
    """
    if type(as_of) is not date:
        raise TypeError("as_of must be a datetime.date, not a string or datetime")
    unique: dict[str, ExtractionResult] = {}
    for extraction in extractions:
        extraction.validate_evidence()
        previous = unique.get(extraction.document.id)
        if previous is not None:
            if (previous.document.original_text != extraction.document.original_text
                    or previous.fields != extraction.fields
                    or previous.document.classification != extraction.document.classification):
                raise ValueError("Conflicting documents or extractions share the same document_id")
            continue
        unique[extraction.document.id] = extraction
    groups: dict[str, list[ExtractionResult]] = defaultdict(list)
    problems = []
    for extraction in sorted(unique.values(), key=lambda e: e.document.id):
        reference = extraction.get(Field.CONTRACT_NUMBER)
        numbers = tuple(sorted({str(c.value) for c in reference.candidates}))
        if reference.state == State.CONFIDENT and len(numbers) == 1:
            groups[numbers[0]].append(extraction)
        else:
            reason = Reason.AMBIGUOUS_CONTRACT_REFERENCE if numbers or reference.state == State.AMBIGUOUS else Reason.NO_CONTRACT_REFERENCE
            issue = _issue(extraction, reason, "Нет однозначной ссылки на договор", _affected(extraction))
            problems.append(GroupingProblem(extraction, numbers, issue))
    contracts = tuple(_resolve_group(number, tuple(documents),
                                    tuple(p for p in problems if number in p.referenced_contracts), as_of)
                      for number, documents in sorted(groups.items()))
    return ResolutionReport(as_of, contracts, tuple(problems))


def filter_delivery_before(report: ResolutionReport, before: date) -> tuple[ResolvedContract, ...]:
    """Strict comparison of confirmed deadlines; full records retain proposals."""
    if type(before) is not date:
        raise TypeError("before must be a datetime.date")
    return tuple(contract for contract in report.contracts
                 if contract.get(Field.DELIVERY_DEADLINE).state == ResolutionState.RESOLVED
                 and contract.get(Field.DELIVERY_DEADLINE).value < before)
