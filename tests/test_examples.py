from datetime import date
from decimal import Decimal

import pytest

from contract_review import extract_facts, parse_document
from contract_review.models import DocumentKind, Field, Role, SigningStatus, State


@pytest.mark.parametrize("name,kind,number,supplier,amount,deadline,signature", [
    ("01_contract.txt", DocumentKind.CONTRACT, "STN-101", 'ООО «Вектор Прибор»', Decimal("1200000"), date(2026, 4, 15), SigningStatus.BOTH_PARTIES),
    ("02_amendment_signed.txt", DocumentKind.SIGNED_AMENDMENT, "STN-101", 'ООО «Вектор Прибор»', Decimal("1380000"), date(2026, 4, 30), SigningStatus.BOTH_PARTIES),
    ("03_contract.txt", DocumentKind.CONTRACT, "STN-102", 'ООО «Контур Электро»', Decimal("840000"), date(2026, 3, 20), SigningStatus.BOTH_PARTIES),
    ("04_contract.txt", DocumentKind.CONTRACT, "STN-103", 'ООО «ПромСнаб Сервис»', None, date(2026, 4, 30), SigningStatus.BOTH_PARTIES),
    ("06_amendment_draft.txt", DocumentKind.UNSIGNED_DRAFT, "STN-101", 'ООО «Вектор Прибор»', Decimal("1450000"), date(2026, 5, 10), SigningStatus.UNSIGNED),
])
def test_supplied_contracts_and_amendments(examples, name, kind, number, supplier, amount, deadline, signature):
    output = examples[name]
    assert output.document.classification.kind == kind
    assert output.document.classification.state == State.CONFIDENT
    for field, expected in [(Field.CONTRACT_NUMBER, number), (Field.SUPPLIER, supplier),
                            (Field.TOTAL_AMOUNT, amount), (Field.DELIVERY_DEADLINE, deadline),
                            (Field.SIGNING_STATUS, signature)]:
        actual = output.get(field)
        if expected is None:
            assert actual.state == State.MISSING
            assert not actual.candidates
        else:
            assert actual.state == State.CONFIDENT, actual
            assert {candidate.value for candidate in actual.candidates} == {expected}


def test_supplier_letter_is_proposal_and_retains_previous_date(examples):
    output = examples['05_supplier_email.txt']
    assert output.document.classification.kind == DocumentKind.SUPPLIER_LETTER
    assert {c.value for c in output.get(Field.CONTRACT_NUMBER).candidates} == {'STN-102'}
    assert output.get(Field.SUPPLIER).candidates[0].value == 'ООО «Контур Электро»'
    deadlines = output.get(Field.DELIVERY_DEADLINE)
    assert deadlines.state == State.AMBIGUOUS
    assert [(c.value, c.role) for c in deadlines.candidates] == [
        (date(2026, 3, 20), Role.REFERENCED_TERM), (date(2026, 3, 27), Role.PROPOSAL),
    ]
    # The letter describes a different, unsigned agreement, not its own signature.
    assert output.get(Field.SIGNING_STATUS).state == State.MISSING
    assert output.get(Field.EFFECTIVE_DATE).state == State.MISSING
    assert output.get(Field.TOTAL_AMOUNT).state == State.MISSING


def test_dates_are_not_invented_from_document_or_review_date(examples):
    for name, output in examples.items():
        assert output.get(Field.DOCUMENT_DATE).state == State.CONFIDENT
        effective = output.get(Field.EFFECTIVE_DATE)
        if name == '02_amendment_signed.txt':
            assert effective.state == State.CONFIDENT
            assert effective.candidates[0].value == date(2026, 3, 1)
        else:
            assert effective.state == State.MISSING
            assert not effective.candidates


@pytest.mark.parametrize("name,role", [
    ('01_contract.txt', Role.ORIGINAL_TERM),
    ('02_amendment_signed.txt', Role.AMENDMENT_TERM),
    ('06_amendment_draft.txt', Role.PROPOSAL),
])
def test_terms_have_roles_and_full_context(examples, name, role):
    for field in (Field.TOTAL_AMOUNT, Field.DELIVERY_DEADLINE):
        candidate = examples[name].get(field).candidates[0]
        assert candidate.role == role
        assert candidate.evidence.quote in candidate.context.quote
    if role == Role.AMENDMENT_TERM:
        assert 'изложить в следующей редакции' in examples[name].get(Field.TOTAL_AMOUNT).candidates[0].context.quote


def test_all_examples_have_exact_evidence_and_filename_independence(examples):
    for output in examples.values():
        output.validate_evidence()
        renamed = extract_facts(parse_document(output.document.original_text, 'completely_misleading_name.txt'))
        assert renamed.document.classification == output.document.classification
        assert renamed.fields == output.fields
        for field in output.fields:
            for candidate in field.candidates:
                e = candidate.evidence
                paragraph = output.document.paragraphs[e.paragraph_id]
                assert output.document.original_text[e.start:e.end] == e.quote
                assert paragraph.text[e.start-paragraph.start:e.end-paragraph.start] == e.quote
