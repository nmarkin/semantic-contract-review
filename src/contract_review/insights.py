"""Deterministic, evidenced observations; no extraction or applicability decisions."""

from dataclasses import dataclass

from .models import Evidence, Field, Role
from .presentation import FIELD_LABELS, format_field, format_value, missing_description
from .proposal_view import difference, document_cards
from .resolution_models import Reason, ResolvedContract, ResolutionReport, ResolutionState
from .status_context import future_notice


@dataclass(frozen=True)
class Insight:
    contract_number: str
    text: str
    evidence: tuple[Evidence, ...]


def contract_insights(contract: ResolvedContract) -> tuple[Insight, ...]:
    insights = []
    for field in contract.fields:
        if field.state == ResolutionState.MISSING:
            insights.append(Insight(contract.contract_number, missing_description(field), field.explanations))
        elif field.state == ResolutionState.REQUIRES_REVIEW:
            insights.append(Insight(contract.contract_number,
                                    FIELD_LABELS[field.field] + ': требуется проверка. ' + '; '.join(dict.fromkeys(i.message for i in field.issues)),
                                    tuple(dict.fromkeys(p for i in field.issues for p in i.evidence))))
        elif field.field in (Field.TOTAL_AMOUNT, Field.DELIVERY_DEADLINE):
            for step in field.history:
                if step.initial or not step.sources:
                    continue
                label = 'сумму' if field.field == Field.TOTAL_AMOUNT else 'срок поставки'
                when = f' с {format_value(step.effective_on)}' if step.effective_on else ''
                delta = difference(step.previous_sources, step.sources)
                if delta is None:
                    text = f'Примененное соглашение{when} установило {label}: {format_value(step.sources[0].value)}.'
                else:
                    text = (f'Примененное соглашение{when} изменило {label} с {format_value(step.previous_sources[0].value)} '
                            f'на {format_value(step.sources[0].value)} ({delta}).')
                insights.append(Insight(contract.contract_number, text,
                                        tuple(dict.fromkeys(c.evidence for c in (*step.previous_sources, *step.sources)))))
    for card in document_cards(contract, contract.proposals):
        temporal = future_notice(contract, card.document)
        if any(i.reason == Reason.UNSIGNED_DRAFT for i in card.items):
            proofs = tuple(dict.fromkeys(c.evidence for i in card.items
                                         for c in i.extraction.get(Field.SIGNING_STATUS).candidates))
            insights.append(Insight(contract.contract_number, (temporal + ' ' if temporal else '') + 'Проект соглашения не подписан. Предложенные условия не действуют.', proofs + card.status_evidence))
        else:
            for change in card.changes:
                if change.candidate.role != Role.PROPOSAL or not change.comparable:
                    continue
                label = 'Предложен срок поставки' if change.candidate.field == Field.DELIVERY_DEADLINE else 'Предложена сумма'
                text = f'{label}: {format_value(change.candidate.value)}. '
                if change.current.state == ResolutionState.RESOLVED:
                    text += f'Подтвержденное действующее значение — {format_field(change.current)}. '
                else:
                    text += 'Действующее значение не подтверждено. '
                text += 'Изменение не согласовано.'
                if change.current.state == ResolutionState.RESOLVED and any(c.value != change.current.value for c in change.referenced):
                    text += ' Упомянутое прежнее значение расходится с действующим.'
                proofs = tuple(dict.fromkeys(c.evidence for c in (change.candidate, *change.referenced, *change.current.sources)))
                insights.append(Insight(contract.contract_number, (temporal + ' ' if temporal else '') + text, proofs + card.status_evidence))
    return tuple(insights)


def report_insights(report: ResolutionReport) -> tuple[Insight, ...]:
    return tuple(item for contract in report.contracts for item in contract_insights(contract))
