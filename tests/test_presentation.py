from dataclasses import replace
from datetime import date
from decimal import Decimal
from html.parser import HTMLParser

import pytest

from contract_review import extract_facts, parse_document, resolve_contracts
from contract_review.models import Evidence, Field
from contract_review.presentation import (
    Upload, document_view_html, format_field, format_value,
    process_uploads, source_html, summary_rows, upload_fingerprint,
)
from contract_review.resolution_models import ResolutionState


@pytest.mark.parametrize('value,expected', [
    (Decimal('1380000'), '1 380 000 ₽'), (Decimal('0'), '0 ₽'),
    (Decimal('1234.56'), '1 234,56 ₽'), (Decimal('900.00'), '900 ₽'),
    (date(2032, 4, 9), '09.04.2032'), (None, 'Нет данных'),
    ('ООО «Поставщик»', 'ООО «Поставщик»'),
])
def test_russian_value_formatting(value, expected):
    assert format_value(value) == expected


def test_review_never_displays_old_or_accidentally_retained_current_value(examples):
    report = resolve_contracts(examples.values(), date(2026, 3, 10))
    current = report.get('STN-101').get(Field.TOTAL_AMOUNT)
    review = replace(current, state=ResolutionState.REQUIRES_REVIEW)
    assert format_field(review) == 'Требуется проверка'
    assert format_field(replace(current, state=ResolutionState.MISSING)) == 'Нет данных'
    assert format_field(current) == '1 380 000 ₽'


def test_summary_keeps_proposal_warning(examples):
    rows = summary_rows(resolve_contracts(examples.values(), date(2026, 3, 10)).contracts)
    assert rows[1]['Срок поставки'] == '20.03.2026'
    assert 'Предложено изменение' in rows[1]['Состояние / предупреждения']
    assert rows[2]['Действующая сумма, ₽'] == 'Нет данных'


class TextCollector(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
        self.marks = []
        self.in_mark = False
        self.tags = []

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)
        if tag == 'mark':
            self.in_mark = True

    def handle_endtag(self, tag):
        if tag == 'mark':
            self.in_mark = False

    def handle_data(self, data):
        self.parts.append(data)
        if self.in_mark:
            self.marks.append(data)


def test_offsets_select_second_identical_quote_and_preserve_crlf():
    text = '\ufeffПервая сумма 123.\r\n\r\nВторая сумма 123.\r\n'
    doc = parse_document(text)
    start = text.rindex('123')  # Construct test evidence; production must use offsets.
    proof = Evidence(doc.id, 1, start, start + 3, '123')
    html = source_html(doc, proof, full_document=True)
    collector = TextCollector()
    collector.feed(html)
    assert ''.join(collector.parts) == text
    assert collector.marks == ['123']
    assert html.index('Вторая сумма') < html.index('<mark')
    assert '\r\n\r\n' in html


def test_all_user_html_is_escaped_in_context_and_full_viewer():
    text = '<script>window.evil=1</script> & <img src=x onerror=alert(1)>\n\n123 <svg onload=alert(2)>'
    doc = parse_document(text)
    paragraph = doc.paragraphs[1]
    proof = Evidence(doc.id, 1, paragraph.start, paragraph.start + 3, '123')
    collector = TextCollector()
    collector.feed(source_html(doc, proof, full_document=True))
    assert ''.join(collector.parts) == text
    assert collector.tags == ['pre', 'mark']
    viewer = document_view_html(doc, proof)
    assert '<img src=x' not in viewer and '<svg onload' not in viewer
    assert '<script>window.evil' not in viewer
    assert '&lt;script&gt;window.evil' in viewer
    assert "scrollIntoView({block:'center'})" in viewer


def test_invalid_source_or_context_is_rejected(examples):
    doc = examples['01_contract.txt'].document
    candidate = examples['01_contract.txt'].get(Field.TOTAL_AMOUNT).candidates[0]
    with pytest.raises(ValueError):
        source_html(doc, replace(candidate.evidence, start=0))
    wrong_context = examples['01_contract.txt'].get(Field.DELIVERY_DEADLINE).candidates[0].context
    with pytest.raises(ValueError, match='context'):
        source_html(doc, candidate.evidence, wrong_context)


def test_batch_failure_is_atomic_and_recoverable(examples):
    valid = Upload('valid.txt', examples['01_contract.txt'].document.original_text.encode())
    broken = Upload('broken.txt', b'\xff\xfe\x00')
    failed = process_uploads([valid, broken], date(2026, 3, 10))
    assert failed.report is None
    assert len(failed.extractions) == 1
    assert failed.errors[0].name == 'broken.txt'
    repaired = process_uploads([valid], date(2026, 3, 10))
    assert repaired.report is not None and not repaired.errors


@pytest.mark.parametrize('data', [b'', b'  \r\n ', b'hello\x00world'])
def test_empty_and_binary_documents_are_errors(data):
    output = process_uploads([Upload('bad.txt', data)], date(2026, 3, 10))
    assert output.report is None and output.errors


def test_unexpected_extraction_error_also_blocks_batch(monkeypatch, examples):
    def fail(_):
        raise RuntimeError('test failure')
    monkeypatch.setattr('contract_review.presentation.extract_facts', fail)
    upload = Upload('failure.txt', examples['01_contract.txt'].document.original_text.encode())
    output = process_uploads([upload], date(2026, 3, 10))
    assert output.report is None
    assert output.errors[0].name == 'failure.txt'


def test_fingerprint_changes_after_any_user_input_change():
    uploads = [Upload('one.txt', b'one')]
    original = upload_fingerprint(uploads, date(2030, 1, 1), 'utf-8')
    assert original != upload_fingerprint([Upload('one.txt', b'two')], date(2030, 1, 1), 'utf-8')
    assert original != upload_fingerprint([Upload('two.txt', b'one')], date(2030, 1, 1), 'utf-8')
    assert original != upload_fingerprint(uploads, date(2030, 1, 2), 'utf-8')
    assert original != upload_fingerprint(uploads, date(2030, 1, 1), 'cp1251')
