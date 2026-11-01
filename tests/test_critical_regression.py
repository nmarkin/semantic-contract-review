"""Stage 4.2B: independent negative/boundary oracles for the six QA fixes."""

from datetime import date
from decimal import Decimal
from html import unescape
import re

import pytest

from contract_review import resolve_contracts, filter_delivery_before
from contract_review.models import Field, State
from contract_review.presentation import attention_items, report_summary
from contract_review.proposal_view import document_cards
from contract_review.report import html_report
from contract_review.resolution_models import ResolutionState
from contract_review.status_context import FUTURE_NOTICE, future_notice, superseding_changes
from test_resolver import base, amendment, analyze, resolve
from test_app import start, table, text_values


DISPATCH = 'Стороны согласовали новую дату исполнения обязательств по отгрузке: 09.05.2030.'
COMPLETION = 'Дата завершения поставки изменяется на 09.05.2030.'
PRICE = 'Стороны согласовали увеличить цену договора на 200 000 рублей.'


@pytest.mark.parametrize('clause,field', [
    (DISPATCH, Field.DELIVERY_DEADLINE), (COMPLETION, Field.DELIVERY_DEADLINE),
    (PRICE, Field.TOTAL_AMOUNT),
    ('Увеличить стоимость договора на 200 000 рублей.', Field.TOTAL_AMOUNT),
    ('Цена договора увеличивается на 200 000 рублей.', Field.TOTAL_AMOUNT),
    ('Уменьшить стоимость договора на 10 000 рублей.', Field.TOTAL_AMOUNT),
    ('Увеличить цену договора на 10 процентов.', Field.TOTAL_AMOUNT),
    ('Увеличить стоимость договора на 200 000 рублей, аванс составляет 10 000 рублей.', Field.TOTAL_AMOUNT),
    ('Дата завершения поставки изменяется на 09.05.2030, оплата производится до 20.05.2030.', Field.DELIVERY_DEADLINE),
    pytest.param('Увеличить общую стоимость договора на 200 000 рублей.', Field.TOTAL_AMOUNT, id='increment-total-contract'),
    pytest.param('Общая стоимость договора увеличивается на 200 000 рублей.', Field.TOTAL_AMOUNT, id='increment-total-after'),
    pytest.param('Увеличить итоговую стоимость поставки на 200 000 рублей.', Field.TOTAL_AMOUNT, id='increment-final-supply'),
    pytest.param('Увеличить общую стоимость на 200 000 рублей.', Field.TOTAL_AMOUNT, id='increment-total-short'),
])
def test_intent_is_not_a_value_and_has_exact_evidence(clause, field):
    document = amendment(amount=None, extra=clause)
    extracted = document.get(field)
    assert extracted.state == State.AMBIGUOUS and not extracted.candidates
    proof, = extracted.explanations
    assert proof.quote == clause
    proof.validate(document.document)
    contract = resolve(base(), document)
    current = contract.get(field)
    assert current.state == ResolutionState.REQUIRES_REVIEW and current.value is None and not current.sources
    assert len(current.history) == 1 and current.history[0].initial
    assert any(proof in i.evidence for i in current.issues)
    other = Field.TOTAL_AMOUNT if field == Field.DELIVERY_DEADLINE else Field.DELIVERY_DEADLINE
    assert contract.get(other).state == ResolutionState.RESOLVED


@pytest.mark.parametrize('clause', [
    'Остальные условия остаются без изменений.',
    'Условия не изменяются.',
    'Стоимость договора не изменяется.',
    'Стоимость договора не изменяется и составляет 100 000 рублей.',
    'Срок поставки остается без изменений.',
    'Срок поставки не изменяется: 01.04.2030.',
    'Стороны не согласовали новую дату исполнения обязательств по отгрузке: 09.05.2030.',
    'Дата завершения поставки не изменяется на 09.05.2030.',
    'Стороны не согласовали увеличить цену договора на 200 000 рублей.',
    'Стороны не согласовали увеличить стоимость договора на 200 000 рублей.',
    'Первоначальная стоимость договора составляет 100 000 рублей.',
    'Первоначальный срок поставки: 01.04.2030.',
    'Первоначальная дата завершения поставки: 01.04.2030.',
    'Аванс увеличивается на 200 000 рублей.',
    'Бюджетный лимит увеличивается на 200 000 рублей.',
    'Неустойка составляет 1 процент стоимости просроченной части поставки.',
    'Для расчета неустойки стоимость договора составляет 100 000 рублей.',
    'Стоимость просроченной части поставки увеличивается на 10 000 рублей.',
    pytest.param('Общая стоимость просроченной части поставки составляет 10 000 рублей.', id='overdue-part-total'),
    pytest.param('Увеличить общую стоимость просроченной части поставки на 10 000 рублей.', id='overdue-part-increment'),
    'Срок оплаты изменяется на 09.05.2030.',
    'Срок приемки изменяется на 09.05.2030.',
    'Дата вступления соглашения в силу изменяется на 01.03.2030.',
    'Предлагаем согласовать новую дату исполнения обязательств по отгрузке: 09.05.2030.',
    'Предлагаем увеличить цену договора на 200 000 рублей.',
])
def test_no_change_intent_from_negation_reference_or_other_subject(clause):
    contract = resolve(base(), amendment(amount=None, extra=clause))
    assert contract.get(Field.TOTAL_AMOUNT).value == Decimal('100000')
    assert contract.get(Field.DELIVERY_DEADLINE).value == date(2030, 4, 1)
    for field in (Field.TOTAL_AMOUNT, Field.DELIVERY_DEADLINE):
        assert len(contract.get(field).history) == 1
        assert not contract.get(field).issues
    assert not contract.warnings


def test_confirmed_amount_and_unknown_deadline_are_independent():
    contract = resolve(base(), amendment(amount='170 000', extra=DISPATCH))
    assert contract.get(Field.TOTAL_AMOUNT).value == Decimal('170000')
    assert contract.get(Field.DELIVERY_DEADLINE).state == ResolutionState.REQUIRES_REVIEW


@pytest.mark.parametrize('as_of,review', [(date(2030, 2, 28), False), (date(2030, 3, 1), True), (date(2030, 3, 2), True)])
def test_intent_obeys_effective_date(as_of, review):
    contract = resolve(base(), amendment(amount=None, signed='01.02.2030', extra=DISPATCH), as_of=as_of)
    field = contract.get(Field.DELIVERY_DEADLINE)
    assert (field.state == ResolutionState.REQUIRES_REVIEW) == review
    assert field.value == (None if review else date(2030, 4, 1))


@pytest.mark.parametrize('clause', [DISPATCH, COMPLETION, PRICE])
def test_unsigned_intent_cannot_change_terms(clause):
    contract = resolve(base(), amendment(amount=None, status='не подписано', title='ПРОЕКТ ДОПОЛНИТЕЛЬНОГО СОГЛАШЕНИЯ', extra=clause))
    assert contract.get(Field.TOTAL_AMOUNT).value == Decimal('100000')
    assert contract.get(Field.DELIVERY_DEADLINE).value == date(2030, 4, 1)
    assert len(contract.proposals) == 1


@pytest.mark.parametrize('same_field', [True, False])
def test_later_definite_change_resolves_only_same_field(same_field):
    uncertain = amendment(amount=None, extra=DISPATCH)
    later = amendment(amount=None if same_field else '180 000',
                      deadline='20.06.2030' if same_field else None,
                      effective='05.03.2030', signed='04.03.2030')
    contract = resolve(base(), uncertain, later)
    current = contract.get(Field.DELIVERY_DEADLINE)
    assert current.value == (date(2030, 6, 20) if same_field else None)
    assert (current.state == ResolutionState.RESOLVED) == same_field
    issue = next(i for i in contract.warnings if uncertain.document.id in i.document_ids)
    steps = superseding_changes(contract, issue)
    assert bool(steps) == same_field
    if same_field:
        assert steps[0].sources[0].evidence.document_id == later.document.id
        assert not attention_items([contract])
        card, = document_cards(contract, contract.not_applied)
        assert 'Историческая' in card.explanation and '05.03.2030' in card.explanation
        assert any(p.document_id == later.document.id for p in card.status_evidence)
        exported = html_report(resolve_contracts([base(), uncertain, later], date(2030, 3, 10)))
        assert 'Историческая неопределенность' in exported
        assert DISPATCH in unescape(re.sub('<[^>]+>', '', exported))
    else:
        assert attention_items([contract])


@pytest.mark.parametrize('as_of,future', [(date(2030, 1, 31), True), (date(2030, 2, 1), False), (date(2030, 2, 2), False)])
def test_document_date_qualifies_cards_insights_and_export(as_of, future):
    document = amendment(status='не подписано', title='ПРОЕКТ ДОПОЛНИТЕЛЬНОГО СОГЛАШЕНИЯ', signed=None)
    report = resolve_contracts([base(), document], as_of)
    contract = report.contracts[0]
    card, = document_cards(contract, contract.proposals)
    assert (FUTURE_NOTICE in card.explanation) == future
    assert (FUTURE_NOTICE in html_report(report)) == future
    assert bool(card.status_evidence) == future
    for p in card.status_evidence:
        p.validate(document.document)
    assert contract.get(Field.TOTAL_AMOUNT).value == Decimal('100000')


@pytest.mark.parametrize('date_line', ['', 'Дата: 01.02.2030\nДата: 02.02.2030', 'Дата: 31.02.2030'])
def test_unknown_or_ambiguous_document_date_is_not_invented(date_line):
    document = amendment(status='не подписано', title='ПРОЕКТ ДОПОЛНИТЕЛЬНОГО СОГЛАШЕНИЯ')
    document = analyze(document.document.original_text.replace('Дата: 01.02.2030', date_line))
    contract = resolve(base(), document, as_of=date(2030, 1, 31))
    assert not future_notice(contract, document.document)


def test_repeated_identical_document_date_is_still_reliable():
    document = amendment(status='не подписано', title='ПРОЕКТ ДОПОЛНИТЕЛЬНОГО СОГЛАШЕНИЯ')
    document = analyze(document.document.original_text.replace('Дата: 01.02.2030', 'Дата: 01.02.2030\nДата: 01.02.2030'))
    contract = resolve(base(), document, as_of=date(2030, 1, 31))
    assert FUTURE_NOTICE in future_notice(contract, document.document)


@pytest.mark.parametrize('separate', [False, True])
def test_unified_package_vs_explicit_independent_fields(separate):
    document = amendment(amount=None, extra=(
        'Предлагаем установить стоимость договора на 170 000 рублей. '
        + ('Отдельно ' if separate else '') + 'предлагаем изменить срок поставки на 09.05.2030.'))
    report = resolve_contracts([base(), document], date(2030, 3, 10))
    contract = report.contracts[0]
    cards = document_cards(contract, contract.proposals)
    assert len(cards) == (2 if separate else 1)
    assert sum(len(c.changes) for c in cards) == 2
    assert f'Предложений: {len(cards)}' in report_summary(report, 2)


def test_intent_ui_source_export_and_filter_agree():
    documents = [base(), amendment(amount='170 000', extra=DISPATCH)]
    app = start([(f'input-{i}.txt', e.document.original_text.encode(), 'text/plain') for i, e in enumerate(documents)])
    app.date_input(key='as_of').set_value(date(2030, 3, 10)).run()
    assert not app.exception
    row = table(app).loc['OTHER-900/2']
    assert row['Срок поставки'] == 'Требуется проверка' and row['Действующая сумма, ₽'] == '170 000 ₽'
    app.button(key='current_OTHER-900/2_delivery_deadline_0').click().run()
    html = app.get('iframe')[0].proto.srcdoc
    mark = re.search(r'<mark\b[^>]*>(.*?)</mark>', html, re.S).group(1)
    assert unescape(mark) == DISPATCH
    assert 'input-1.txt' in text_values(app)
    app.button(key='close_source').click().run()
    exported = app.session_state['html_report'][1].decode()
    summary = exported.split('<tbody>')[1].split('</tbody>')[0]
    assert 'Требуется проверка' in summary and '01.04.2030' not in summary
    assert DISPATCH in unescape(re.sub('<[^>]+>', '', exported))
    report = resolve_contracts(documents, date(2030, 3, 10))
    assert not filter_delivery_before(report, date(2031, 1, 1))
    app.checkbox(key='filter_enabled').check().run()
    app.date_input(key='before').set_value(date(2031, 1, 1)).run()
    assert not app.dataframe


@pytest.mark.parametrize('same_field', [True, False])
def test_historical_conflict_is_not_a_current_warning_only_if_same_field_resolved(same_field):
    first = amendment(amount=None, deadline='10.05.2030')
    second = amendment(amount=None, deadline='20.05.2030')
    later = amendment(amount=None if same_field else '180 000', deadline='20.06.2030' if same_field else None,
                      effective='05.03.2030', signed='04.03.2030')
    contract = resolve(base(), first, second, later)
    cards = document_cards(contract, contract.not_applied)
    assert len(cards) == 2
    assert all(('Историческая' in card.explanation) == same_field for card in cards)
    assert bool(attention_items([contract])) != same_field
    assert all(bool(card.status_evidence) == same_field for card in cards)
    assert bool(contract.get(Field.DELIVERY_DEADLINE).issues) != same_field


@pytest.mark.parametrize('as_of,review', [(date(2030, 3, 9), False), (date(2030, 3, 10), True)])
def test_intent_obeys_signing_date(as_of, review):
    contract = resolve(base(), amendment(amount=None, signed='10.03.2030', extra=DISPATCH), as_of=as_of)
    assert (contract.get(Field.DELIVERY_DEADLINE).state == ResolutionState.REQUIRES_REVIEW) == review
