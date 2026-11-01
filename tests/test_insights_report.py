from dataclasses import replace
from datetime import date
from decimal import Decimal
from html.parser import HTMLParser
from unittest.mock import Mock
import pytest

from contract_review import extract_facts, parse_document, resolve_contracts
from contract_review.insights import report_insights
from contract_review.models import Field
from contract_review.presentation import attention_items, contract_summary
from contract_review.proposal_view import difference
from contract_review.report import html_report
from contract_review.resolution_models import ResolutionState
from test_app import start, select_contract
from test_resolver import base, amendment, AS_OF


def test_precise_statuses_and_actionable_attention(examples):
    report = resolve_contracts(examples.values(), date(2026, 3, 10))
    assert 'Изменено соглашением' in contract_summary(report.get('STN-101'))
    assert 'Неподписанный проект' in contract_summary(report.get('STN-101'))
    assert 'Исходные условия' in contract_summary(report.get('STN-102'))
    assert 'письмом' in contract_summary(report.get('STN-102'))
    assert 'Нет данных: сумма' in contract_summary(report.get('STN-103'))
    attention = '\n'.join(attention_items(report.contracts))
    assert 'приложение № 1' in attention
    assert 'согласование изменения не подтверждено' in attention
    assert contract_summary(report.get('STN-101')) not in attention


def test_insights_use_history_and_recalculate_by_date_and_bytes(examples):
    report = resolve_contracts(examples.values(), date(2026, 3, 10))
    insights = report_insights(report)
    text = '\n'.join(i.text for i in insights)
    assert '1 200 000 ₽ на 1 380 000 ₽ (+180 000 ₽)' in text
    assert '15.04.2026 на 30.04.2026 (+15 дней)' in text
    assert '20.03.2026' in text and '27.03.2026' in text and 'не согласовано' in text
    assert 'приложение № 1' in text
    documents = {e.document.id: e.document for e in examples.values()}
    for insight in insights:
        for proof in insight.evidence:
            proof.validate(documents[proof.document_id])
    earlier = resolve_contracts(examples.values(), date(2026, 2, 28))
    assert '+180 000' not in '\n'.join(i.text for i in report_insights(earlier))
    changed = [extract_facts(parse_document(e.document.original_text.replace('1 380 000', '1 390 000'), e.document.source_name)) for e in examples.values()]
    assert '+190 000 ₽' in '\n'.join(i.text for i in report_insights(resolve_contracts(changed, date(2026, 3, 10))))


def test_unknown_current_is_never_used_for_insight_difference():
    report = resolve_contracts([base(), amendment(amount='150 000'), amendment(amount='170 000')], AS_OF)
    assert report.contracts[0].get(Field.TOTAL_AMOUNT).state == ResolutionState.REQUIRES_REVIEW
    text = '\n'.join(i.text for i in report_insights(report))
    assert 'требуется проверка' in text and '+50 000' not in text and '+70 000' not in text
    later = amendment(amount='200 000', effective='05.03.2030', signed='05.03.2030')
    text = '\n'.join(i.text for i in report_insights(resolve_contracts([base(), amendment(), amendment(amount='170 000'), later], AS_OF)))
    assert 'установило сумму: 200 000 ₽' in text and '(+' not in text


def test_difference_requires_comparable_confirmed_units():
    before = base().get(Field.TOTAL_AMOUNT).candidates
    after = amendment().get(Field.TOTAL_AMOUNT).candidates
    assert difference(before, after) == '+50 000 ₽'
    assert difference((), after) is None
    assert difference(before, (replace(after[0], unit='USD'),)) is None
    assert difference(before, (replace(after[0], value=date(2030, 1, 1)),)) is None
    assert difference(before, (replace(after[0], value=Decimal('90000')),)) == '-10 000 ₽'


class HtmlAudit(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags, self.ids, self.hrefs, self.texts = [], set(), [], []

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)
        for key, value in attrs:
            assert not key.lower().startswith('on')
            if key == 'id':
                assert value not in self.ids
                self.ids.add(value)
            if key == 'href':
                assert value.startswith('#')
                self.hrefs.append(value[1:])
            assert key not in ('src', 'srcdoc')

    def handle_data(self, data):
        self.texts.append(data)


def test_html_report_is_complete_offline_and_has_resolving_source_links(examples):
    report = resolve_contracts(examples.values(), date(2026, 3, 10))
    html = html_report(report)
    audit = HtmlAudit()
    audit.feed(html)
    assert set(audit.hrefs) <= audit.ids
    assert not {'script', 'iframe', 'link', 'img'} & set(audit.tags)
    assert 'Обработано документов: 6' in html
    assert '1 380 000 ₽' in html and '840 000 ₽' in html and 'Нет данных' in html
    assert 'Примененное изменение' in html and 'Исходное условие' in html
    assert 'Предлагаемый срок: 27.03.2026' in html and 'Действующий срок: 20.03.2026' in html
    assert html.count('Коллеги, по договору') == 1
    assert 'не юридическое заключение' in html and '10.03.2026' in html


def test_html_escapes_names_supplier_and_source_payloads():
    text = ('ДОГОВОР ПОСТАВКИ № SAFE-77\nСтатус: подписан обеими сторонами 01.01.2030.\n\n'
            'Поставщик: ООО «<script>alert(1)</script> & завод».\n\n'
            'Общая стоимость договора составляет 100 рублей. <img src=x onerror=alert(2)>\n\n'
            'Поставщик обязуется поставить товар не позднее 01.04.2030.')
    report = resolve_contracts([extract_facts(parse_document(text, '"><svg onload=alert(3)>.txt'))], AS_OF)
    html = html_report(report)
    audit = HtmlAudit()
    audit.feed(html)
    assert not {'script', 'svg', 'img'} & set(audit.tags)
    assert '&lt;script&gt;' in html and '&lt;img' in html and '&lt;svg' in html and '&amp;' in html


def test_distinct_sources_for_equal_value_survive_report():
    first = amendment(amount='150 000', extra='Источник первый.')
    second = amendment(amount='150 000', extra='Источник второй.')
    first = replace(first, document=replace(first.document, source_name='one.txt'))
    second = replace(second, document=replace(second.document, source_name='two.txt'))
    report = resolve_contracts([base(), first, second], AS_OF)
    assert len(report.contracts[0].get(Field.TOTAL_AMOUNT).sources) == 2
    html = html_report(report)
    assert 'one.txt' in html and 'two.txt' in html


def test_export_rejects_a_quote_with_incorrect_offsets():
    report = resolve_contracts([base()], AS_OF)
    contract = report.contracts[0]
    amount = contract.get(Field.TOTAL_AMOUNT)
    source = amount.sources[0]
    corrupted = replace(source, evidence=replace(source.evidence, start=0))
    field = replace(amount, sources=(corrupted,))
    contract = replace(contract, fields=tuple(field if f.field == Field.TOTAL_AMOUNT else f for f in contract.fields))
    with pytest.raises(ValueError):
        html_report(replace(report, contracts=(contract,)))


def test_ui_export_uses_current_cached_report_without_extraction(monkeypatch):
    import contract_review.presentation as p
    import contract_review.report as exporter
    parse, extract = Mock(wraps=p.parse_txt_bytes), Mock(wraps=p.extract_facts)
    generate = Mock(wraps=exporter.html_report)
    monkeypatch.setattr(p, 'parse_txt_bytes', parse)
    monkeypatch.setattr(p, 'extract_facts', extract)
    monkeypatch.setattr(exporter, 'html_report', generate)
    app = start()
    app.button(key='open_example').click().run()
    assert not app.exception and len(app.get('download_button')) == 1
    original = app.session_state['html_report'][1]
    select_contract(app, 'STN-102')
    app.checkbox(key='filter_enabled').check().run()
    app.button(key='current_STN-102_total_amount_0').click().run()
    app.button(key='close_source').click().run()
    assert generate.call_count == 1 and extract.call_count == 6 and parse.call_count == 6
    assert app.session_state['html_report'][1] == original
    app.date_input(key='as_of').set_value(date(2026, 2, 28)).run()
    assert generate.call_count == 2 and extract.call_count == 6
    assert '28.02.2026' in app.session_state['html_report'][1].decode()
    app.file_uploader[0].set_value([('broken.txt', b'\xff\xfe\x00', 'text/plain')]).run()
    assert not app.get('download_button') and not app.dataframe
