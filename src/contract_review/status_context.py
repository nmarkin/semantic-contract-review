"""Temporal explanations of an already resolved snapshot; no applicability rules."""

from datetime import date

from .models import Field, ParsedDocument, State
from .resolution_models import AppliedChange, Reason, ResolutionIssue, ResolutionState, ResolvedContract

FUTURE_NOTICE = ('Документ датирован позже даты проверки и приведен для информации. '
                 'Он не подтверждает состояние переговоров на выбранную дату')


def future_notice(contract: ResolvedContract, document: ParsedDocument) -> str:
    created = document.metadata_for(Field.DOCUMENT_DATE)
    if created.state == State.CONFIDENT and created.candidates:
        value = created.candidates[0].value
        if type(value) is date and contract.as_of is not None and value > contract.as_of:
            return f'Дата документа: {value:%d.%m.%Y}. {FUTURE_NOTICE}.'
    return ''


def superseding_changes(contract: ResolvedContract, issue: ResolutionIssue) -> tuple[AppliedChange, ...]:
    if issue.reason not in (Reason.SAME_DATE_CONFLICT, Reason.AMBIGUOUS_FIELD,
                            Reason.UNCONFIRMED_APPLICABILITY) or not issue.fields or not issue.document_ids:
        return ()
    if any(f not in (Field.TOTAL_AMOUNT, Field.DELIVERY_DEADLINE) for f in issue.fields):
        return ()
    documents = {e.document.id: e for e in contract.documents}
    dates = []
    for document_id in issue.document_ids:
        extraction = documents.get(document_id)
        if extraction is None:
            return ()
        effective = extraction.get(Field.EFFECTIVE_DATE)
        if effective.state != State.CONFIDENT or not effective.candidates:
            return ()
        dates.append(effective.candidates[0].value)
    changes = []
    for field in issue.fields:
        current = contract.get(field)
        if current.state != ResolutionState.RESOLVED:
            return ()
        step = next((h for h in current.history if not h.initial and h.effective_on
                     and h.effective_on > max(dates)), None)
        if step is None:
            return ()
        changes.append(step)
    return tuple(changes)


def issue_description(contract: ResolvedContract, issue: ResolutionIssue) -> str:
    changes = superseding_changes(contract, issue)
    if not changes:
        return issue.message
    names = {e.document.id: e.document.source_name for e in contract.documents}
    resolved_by = '; '.join(dict.fromkeys(
        f'{names[c.evidence.document_id]} (с {step.effective_on:%d.%m.%Y})'
        for step in changes for c in step.sources))
    return f'Историческая неопределенность: {issue.message}. Разрешена последующим изменением того же условия: {resolved_by}.'
