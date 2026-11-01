from datetime import date
from decimal import Decimal

import pytest

from contract_review import extract_facts, parse_document
from contract_review.models import Field, Role, State


def analyze(text):
    return extract_facts(parse_document('ДОГОВОР ПОСТАВКИ № NEW-908/7\n\n' + text))


def test_new_contract_without_changes_to_code():
    output = analyze('Поставщик: АО «Новый завод».\n\n'
                     'Общая стоимость поставки составляет 9\u202f876\u202f543,21 рублей, включая НДС.\n\n'
                     'Поставщик обязуется поставить станки не позднее 17.11.2032.')
    amount = output.get(Field.TOTAL_AMOUNT).candidates[0]
    assert amount.value == Decimal('9876543.21')
    assert amount.evidence.quote == '9\u202f876\u202f543,21'
    assert amount.unit == 'RUB'
    assert output.get(Field.SUPPLIER).candidates[0].value == 'АО «Новый завод»'
    assert output.get(Field.DELIVERY_DEADLINE).candidates[0].value == date(2032, 11, 17)
    assert output.get(Field.CONTRACT_NUMBER).candidates[0].value == 'NEW-908/7'


@pytest.mark.parametrize('text', [
    'Авансовый платеж составляет 200 000 рублей и входит в общую стоимость договора.',
    'Лимит закупки — 700 000 рублей. Этот лимит не является стоимостью договора.',
    'Неустойка составляет 12 000 рублей.',
    'Общая стоимость определяется спецификацией в приложении № 3.',
    'Общая стоимость договора не составляет 400 000 рублей.',
    'Общая стоимость договора не указана.',
])
def test_money_distractors_do_not_become_totals(text):
    actual = analyze(text).get(Field.TOTAL_AMOUNT)
    assert actual.state == State.MISSING
    assert not actual.candidates


@pytest.mark.parametrize('text', [
    'Оплата производится не позднее 18.11.2030.',
    'Дата приемки: 18.11.2030.',
    'Настоящий договор вступает в силу 18.11.2030.',
    'Оплата после поставки производится до 18.11.2030.',
    'Дата приемки 18.11.2030 не изменяет срок поставки.',
    'Срок оплаты — 18.11.2030, а не срок поставки.',
    'Неустойка за просрочку поставки начисляется до 18.11.2030.',
])
def test_non_delivery_dates_are_not_deadlines(text):
    assert not analyze(text).get(Field.DELIVERY_DEADLINE).candidates


def test_payment_and_delivery_in_same_paragraph_remain_distinct():
    output = analyze('Срок поставки: 01.08.2030. Оплата не позднее 20.08.2030.\n'
                     'Дата приемки: 03.08.2030.')
    assert [c.value for c in output.get(Field.DELIVERY_DEADLINE).candidates] == [date(2030, 8, 1)]


@pytest.mark.parametrize('text,field', [
    ('Срок поставки: 31.02.2030.', Field.DELIVERY_DEADLINE),
    ('Срок поставки — в течение 30 дней после оплаты.', Field.DELIVERY_DEADLINE),
    ('Срок поставки: завтра.', Field.DELIVERY_DEADLINE),
    ('Общая стоимость договора составляет 450 000 долларов.', Field.TOTAL_AMOUNT),
    ('Общая стоимость договора составляет 1 20 000 рублей.', Field.TOTAL_AMOUNT),
    ('Поставщик: ООО «Первый» или ООО «Второй».', Field.SUPPLIER),
])
def test_unsupported_or_ambiguous_values_are_explicit(text, field):
    actual = analyze(text).get(field)
    assert actual.state == State.AMBIGUOUS
    assert not actual.candidates
    assert actual.reasons


@pytest.mark.parametrize('text,field,values', [
    ('Общая стоимость договора составляет 100 рублей.\n\nОбщая стоимость договора составляет 200 рублей.', Field.TOTAL_AMOUNT, {Decimal('100'), Decimal('200')}),
    ('Общая стоимость договора составляет 100 рублей или 200 рублей.', Field.TOTAL_AMOUNT, {Decimal('100'), Decimal('200')}),
    ('Срок поставки: 01.08.2030.\n\nСрок поставки: 02.08.2030.', Field.DELIVERY_DEADLINE, {date(2030, 8, 1), date(2030, 8, 2)}),
    ('Срок поставки: 01.08.2030 или 02.08.2030.', Field.DELIVERY_DEADLINE, {date(2030, 8, 1), date(2030, 8, 2)}),
    ('Поставщик: ООО «Первый».\n\nПоставщик: ООО «Второй».', Field.SUPPLIER, {'ООО «Первый»', 'ООО «Второй»'}),
])
def test_competing_values_preserved_without_selection(text, field, values):
    actual = analyze(text).get(field)
    assert actual.state == State.AMBIGUOUS
    assert {c.value for c in actual.candidates} == values


def test_proposal_inside_contract_is_not_original_term():
    output = analyze('Предлагается изменить срок поставки на 01.09.2030.')
    assert output.get(Field.DELIVERY_DEADLINE).candidates[0].role == Role.PROPOSAL


def test_unknown_document_does_not_acquire_legal_role():
    output = extract_facts(parse_document('Общая стоимость договора составляет 123 рублей.'))
    assert output.get(Field.TOTAL_AMOUNT).candidates[0].role == Role.UNCERTAIN


def test_tax_and_advance_in_same_sentence_are_not_total_candidates():
    output = analyze('Общая стоимость договора составляет 300 рублей, включая НДС 50 рублей и аванс 100 рублей.')
    actual = output.get(Field.TOTAL_AMOUNT)
    assert actual.state == State.CONFIDENT
    assert [c.value for c in actual.candidates] == [Decimal('300')]


def test_repeated_supplier_label_on_one_line_preserves_conflict():
    actual = analyze('Поставщик: ООО «Первый». Поставщик: ООО «Второй».').get(Field.SUPPLIER)
    assert actual.state == State.AMBIGUOUS
    assert {c.value for c in actual.candidates} == {'ООО «Первый»', 'ООО «Второй»'}
