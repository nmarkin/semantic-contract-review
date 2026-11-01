from dataclasses import replace
from datetime import date

import pytest

from contract_review import extract_facts, parse_document, parse_txt_bytes
from contract_review.models import DocumentKind, Field, SigningStatus, State


def test_original_text_and_offsets_preserve_bom_crlf_unicode_and_whitespace():
    raw = '\ufeffДОГОВОР ПОСТАВКИ № АБ-987/4\r\nДата: 09.12.2031\r\n \t\r\n  Поставщик: ООО «Ёж 🦔».\r\n\r\nСрок поставки: 15.12.2031.\r\n'
    document = parse_txt_bytes(raw.encode('utf-8'), 'signed_amendment.txt')
    assert document.original_text == raw
    assert document.classification.kind == DocumentKind.CONTRACT
    assert len(document.paragraphs) == 3
    for paragraph in document.paragraphs:
        assert raw[paragraph.start:paragraph.end] == paragraph.text
    output = extract_facts(document)
    output.validate_evidence()
    assert output.get(Field.SUPPLIER).candidates[0].value == 'ООО «Ёж 🦔»'
    assert output.get(Field.DELIVERY_DEADLINE).candidates[0].value == date(2031, 12, 15)


def test_explicit_encoding_without_silent_replacement():
    raw = 'ДОГОВОР ПОСТАВКИ № П-89'
    data = raw.encode('cp1251')
    with pytest.raises(UnicodeDecodeError):
        parse_txt_bytes(data)
    assert parse_txt_bytes(data, encoding='cp1251').original_text == raw


@pytest.mark.parametrize('text', ['', '\r\n  \n\t', 'Произвольная заметка без условий.'])
def test_no_fabricated_values(text):
    output = extract_facts(parse_document(text, 'contract_signed.txt'))
    assert output.document.classification.kind == DocumentKind.UNKNOWN
    assert all(field.state == State.MISSING and not field.candidates for field in output.fields)


@pytest.mark.parametrize('status,expected,kind,state', [
    ('подписано обеими сторонами', SigningStatus.BOTH_PARTIES, DocumentKind.SIGNED_AMENDMENT, State.CONFIDENT),
    ('не подписано ни одной стороной', SigningStatus.UNSIGNED, DocumentKind.UNSIGNED_DRAFT, State.CONFIDENT),
    ('не подписано обеими сторонами', SigningStatus.NOT_BOTH_PARTIES, DocumentKind.UNSIGNED_DRAFT, State.CONFIDENT),
    ('подписано только одной стороной', SigningStatus.NOT_BOTH_PARTIES, DocumentKind.UNSIGNED_DRAFT, State.CONFIDENT),
    ('подписано', SigningStatus.SIGNED_UNSPECIFIED, DocumentKind.UNKNOWN, State.AMBIGUOUS),
    ('на согласовании', None, DocumentKind.UNKNOWN, State.AMBIGUOUS),
    ('будет подписано обеими сторонами', None, DocumentKind.UNKNOWN, State.AMBIGUOUS),
    ('подписано обеими сторонами, что не подтверждено', None, DocumentKind.UNKNOWN, State.AMBIGUOUS),
    ('не был подписан ни одной стороной', SigningStatus.UNSIGNED, DocumentKind.UNSIGNED_DRAFT, State.CONFIDENT),
    ('не подписано поставщиком', SigningStatus.NOT_BOTH_PARTIES, DocumentKind.UNSIGNED_DRAFT, State.CONFIDENT),
])
def test_signature_negations_and_uncertainty(status, expected, kind, state):
    doc = parse_document('ДОПОЛНИТЕЛЬНОЕ СОГЛАШЕНИЕ № 12 К ДОГОВОРУ НОВ-879\nСтатус: ' + status)
    signature = doc.metadata_for(Field.SIGNING_STATUS)
    assert signature.state == state
    assert [c.value for c in signature.candidates] == ([expected] if expected else [])
    assert doc.classification.kind == kind
    assert doc.metadata_for(Field.CONTRACT_NUMBER).candidates[0].value == 'НОВ-879'


@pytest.mark.parametrize('text', [
    'ПРОЕКТ ДОПОЛНИТЕЛЬНОГО СОГЛАШЕНИЯ\nСтатус: подписано обеими сторонами',
    'ДОПОЛНИТЕЛЬНОЕ СОГЛАШЕНИЕ\nСтатус: не подписано; подписано обеими сторонами',
    'ДОПОЛНИТЕЛЬНОЕ СОГЛАШЕНИЕ\nСтатус: подписано обеими сторонами; не подписано',
    'ДОПОЛНИТЕЛЬНОЕ СОГЛАШЕНИЕ\nСтатус: подписано обеими сторонами\nСтатус: не подписано',
    'ДОПОЛНИТЕЛЬНОЕ СОГЛАШЕНИЕ\nДата: 01.02.2030',
    'ДОГОВОР ПОСТАВКИ\n\nПИСЬМО ПОСТАВЩИКА',
])
def test_classification_abstains_on_conflicting_or_insufficient_evidence(text):
    classification = parse_document(text).classification
    assert classification.kind == DocumentKind.UNKNOWN
    assert classification.state == State.AMBIGUOUS
    assert classification.reasons


def test_multiple_contract_references_are_not_resolved():
    output = extract_facts(parse_document('ПИСЬМО ПОСТАВЩИКА\nТема: договор X-321 и договор Y-654'))
    assert output.get(Field.CONTRACT_NUMBER).state == State.AMBIGUOUS
    assert {c.value for c in output.get(Field.CONTRACT_NUMBER).candidates} == {'X-321', 'Y-654'}


@pytest.mark.parametrize('clause', [
    'Стоимость договора 123 000 рублей.',
    'Сумма договора 987654 рублей.',
    'Бюджет договора 456 рублей.',
    'Срок договора 30 дней.',
])
def test_amount_or_duration_after_contract_is_not_an_identifier(clause):
    doc = parse_document('ДОГОВОР ПОСТАВКИ № ABC-555\n' + clause)
    actual = doc.metadata_for(Field.CONTRACT_NUMBER)
    assert actual.state == State.CONFIDENT
    assert [c.value for c in actual.candidates] == ['ABC-555']


@pytest.mark.parametrize('text,number', [
    ('ДОГОВОР ПОСТАВКИ № АБ-987/4', 'АБ-987/4'),
    ('ДОГОВОР ПОСТАВКИ 987', '987'),
    ('Тема: договор X-123/7', 'X-123/7'),
    ('По договору 123 предлагаем изменить срок.', '123'),
    ('ДОПОЛНИТЕЛЬНОЕ СОГЛАШЕНИЕ № 7 К ДОГОВОРУ AB-456', 'AB-456'),
    ('Ссылка на договор № ALPHA/BETA', 'ALPHA/BETA'),
])
def test_identification_contexts_remain_supported(text, number):
    actual = parse_document(text).metadata_for(Field.CONTRACT_NUMBER)
    assert actual.state == State.CONFIDENT
    assert [c.value for c in actual.candidates] == [number]


def test_dates_and_invalid_calendar_values():
    doc = parse_document('Дата: 31.02.2030\n\nНастоящее соглашение вступает в силу 30.02.2030.')
    assert doc.metadata_for(Field.DOCUMENT_DATE).state == State.AMBIGUOUS
    assert doc.metadata_for(Field.EFFECTIVE_DATE).state == State.AMBIGUOUS
    assert not doc.metadata_for(Field.EFFECTIVE_DATE).candidates


def test_unrecognized_effective_date_is_not_missing():
    doc = parse_document('Настоящее соглашение вступает в силу завтра.')
    assert doc.metadata_for(Field.EFFECTIVE_DATE).state == State.AMBIGUOUS


def test_alternative_effective_dates_require_review():
    doc = parse_document('Соглашение вступает в силу 01.02.2030 или 03.02.2030.')
    assert doc.metadata_for(Field.EFFECTIVE_DATE).state == State.AMBIGUOUS


def test_signing_date_is_separate_from_document_date():
    doc = parse_document('ДОПОЛНИТЕЛЬНОЕ СОГЛАШЕНИЕ К ДОГОВОРУ X-123\n'
                         'Дата: 01.02.2030\nСтатус: подписано обеими сторонами 07.02.2030.')
    assert doc.metadata_for(Field.DOCUMENT_DATE).candidates[0].value == date(2030, 2, 1)
    signed = doc.metadata_for(Field.SIGNING_DATE)
    assert signed.state == State.CONFIDENT
    assert signed.candidates[0].value == date(2030, 2, 7)
    signed.candidates[0].evidence.validate(doc)


@pytest.mark.parametrize('status,state', [
    ('подписано обеими сторонами.', State.MISSING),
    ('подписано обеими сторонами. По состоянию на 09.02.2030.', State.MISSING),
    ('не подписано обеими сторонами 09.02.2030.', State.MISSING),
    ('подписано обеими сторонами 31.02.2030.', State.AMBIGUOUS),
    ('подписано обеими сторонами 01.02.2030 или 02.02.2030.', State.AMBIGUOUS),
    ('подписано обеими сторонами вчера.', State.AMBIGUOUS),
])
def test_signing_date_never_falls_back_to_document_date(status, state):
    doc = parse_document('Дата: 01.02.2030\nСтатус: ' + status)
    assert doc.metadata_for(Field.SIGNING_DATE).state == state


def test_evidence_validator_rejects_tampering():
    output = extract_facts(parse_document('ДОГОВОР ПОСТАВКИ № X-5\n\nСрок поставки: 01.02.2030.'))
    e = output.get(Field.DELIVERY_DEADLINE).candidates[0].evidence
    for bad in [replace(e, quote='01.03.2030'), replace(e, start=e.start + 1),
                replace(e, document_id='other'), replace(e, paragraph_id=0), replace(e, end=e.start)]:
        with pytest.raises(ValueError):
            bad.validate(output.document)
