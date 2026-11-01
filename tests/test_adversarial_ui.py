"""UI expectations were recorded in qa/adversarial/UI_EXPECTATIONS.json first."""
from datetime import date
from html import unescape
import re
from unittest.mock import Mock

import pytest

from adversarial_support import CASES, by_id, uploads, run, QA, MATRIX
from test_app import start, table, text_values
from contract_review.proposal_view import document_cards, change_lines
from contract_review.report import html_report


def displayed(field):
    if field['state'] == 'requires_review':
        return 'Требуется проверка'
    if field['state'] == 'missing':
        return 'Нет данных'
    if isinstance(field['value'], int):
        return f'{field["value"]:,} ₽'.replace(',', ' ')
    return date.fromisoformat(field['value']).strftime('%d.%m.%Y')


@pytest.mark.parametrize('case', [c for c in CASES if c['id'].startswith('chain-')], ids=lambda c: c['id'])
def test_chain_ui_and_html_snapshots(case):
    app = start(uploads(case))
    app.date_input(key='as_of').set_value(date.fromisoformat(case['as_of'])).run()
    assert not app.exception
    row = table(app).loc['DEMO-900']
    for field, column in [('total_amount', 'Действующая сумма, ₽'), ('delivery_deadline', 'Срок поставки')]:
        assert row[column] == displayed(case['expected'][field])
    color = '#F8DFD8' if case['as_of'] == '2026-03-11' else '#FFF0CE'
    assert color in app.dataframe[0].proto.arrow_data.styler.styles
    report = app.session_state['html_report'][1].decode()
    summary = report.split('<tbody>', 1)[1].split('</tbody>', 1)[0]
    for field in case['expected'].values():
        assert displayed(field) in summary
    if case['as_of'] == '2026-03-11':
        assert '25.04.2026' not in summary and '01.05.2026' not in summary and '03.05.2026' not in summary
        app.checkbox(key='filter_enabled').check().run()
        app.date_input(key='before').set_value(date(2031, 1, 1)).run()
        assert not app.dataframe
    # All generated source anchors are internal and resolve exactly once.
    targets = re.findall(r'href="#(source-\d+)"', report)
    assert targets and all(report.count(f'id="{target}"') == 1 for target in targets)
    source_sections = re.findall(r'<section id="source-\d+">(.*?)</section>', report, re.S)
    for source in source_sections:
        name = re.search(r'<h3>(.*?) · абзац', source).group(1)
        quote = re.search(r'<pre[^>]*>(.*?)</pre>', source, re.S).group(1)
        quote = unescape(quote.replace('<mark>', '').replace('</mark>', ''))
        original = (QA / 'documents' / unescape(name)).read_text(encoding='utf-8')
        assert quote in original


def test_chain_date_changes_do_not_reparse_and_source_dialog_is_exact(monkeypatch):
    import contract_review.presentation as p
    parse, extract = Mock(wraps=p.parse_txt_bytes), Mock(wraps=p.extract_facts)
    monkeypatch.setattr(p, 'parse_txt_bytes', parse)
    monkeypatch.setattr(p, 'extract_facts', extract)
    app = start(uploads(by_id('chain-2026-02-02')))
    for when in ('2026-02-02', '2026-02-06', '2026-02-20', '2026-03-06', '2026-03-11', '2026-03-21'):
        app.date_input(key='as_of').set_value(date.fromisoformat(when)).run()
        assert not app.exception
    assert parse.call_count == extract.call_count == 9
    app.button(key='current_DEMO-900_delivery_deadline_0').click().run()
    content = app.get('iframe')[0].proto.srcdoc
    assert re.search(r'<mark\b[^>]*id="evidence-target"[^>]*>(.*?)</mark>', content).group(1) == '10.05.2026'
    assert 'H.txt' in text_values(app)
    original = (QA / 'documents/H.txt').read_text(encoding='utf-8')
    assert str(original.index('10.05.2026')) in '\n'.join(c.value for c in app.caption)
    app.button(key='close_source').click().run()
    app.button(key='current_DEMO-900_total_amount_0').click().run()
    assert re.search(r'<mark\b[^>]*id="evidence-target"[^>]*>(.*?)</mark>', app.get('iframe')[0].proto.srcdoc).group(1) == '1 250 000'
    assert 'E.txt' in text_values(app) and parse.call_count == extract.call_count == 9


def test_future_proposals_are_explicitly_dated_in_user_status():
    case = by_id('chain-2026-02-02')
    report, _, keys = run(case)
    cards = document_cards(report.get('DEMO-900'), report.get('DEMO-900').proposals)
    for card in cards:
        created = MATRIX['documents'][keys[card.document.id]]['created']
        assert created > case['as_of']
        explanation = card.explanation.lower()
        assert date.fromisoformat(created).strftime('%d.%m.%Y') in explanation and re.search('после|будущ|позже', explanation), (
            f'Future proposal {keys[card.document.id]} ({created}) has no temporal qualification: {card.explanation}')


def test_superseded_conflict_is_identified_as_historical_in_user_cards():
    report, _, keys = run(by_id('chain-2026-03-21'))
    contract = report.get('DEMO-900')
    cards = document_cards(contract, [i for i in contract.not_applied if i not in contract.proposals])
    conflicts = [c for c in cards if keys[c.document.id] in ('F', 'G')]
    assert len(conflicts) == 2
    assert all(re.search('истор|прежн|разреш|замен|ранее', c.explanation.lower()) for c in conflicts), [c.explanation for c in conflicts]


def test_two_proposals_one_paragraph_and_mismatched_old_date():
    report, _, _ = run(by_id('two-proposals-one-paragraph'))
    contract = report.get('DEMO-900')
    cards = document_cards(contract, contract.proposals)
    assert len(cards) == 2 and all(len(card.changes) == len(card.sources) == 1 for card in cards)
    assert {c.candidate.value for card in cards for c in card.changes} == {date(2026, 4, 28), date(2026, 4, 30)}
    app = start(uploads(by_id('two-proposals-one-paragraph')))
    quotes = [h.proto.body for h in app.get('html') if 'Предлагаем изменить срок поставки с' in h.proto.body]
    assert len(quotes) == 1
    report, _, _ = run(by_id('letter-wrong-old-date'))
    contract = report.get('DEMO-900')
    card, = document_cards(contract, contract.proposals)
    lines = '\n'.join(change_lines(card.changes[0]))
    assert '+3 дня' in lines and '+8 дней' not in lines and 'Расхождение' in lines


def test_count_represents_two_independent_proposals_not_one_document():
    app = start(uploads(by_id('two-proposals-one-paragraph')))
    labels = [e.label for e in app.expander if e.label.startswith('Несогласованные изменения')]
    assert labels == ['Несогласованные изменения (2)'], labels


def test_two_sessions_same_name_do_not_share_conditions_or_uploads():
    one, two = by_id('same-date-equal').copy(), by_id('same-date-different').copy()
    one['documents'] = [dict(key=k, name='same.txt') for k in ('base', 'same1')]
    two['documents'] = [dict(key=k, name='same.txt') for k in ('base', 'same2')]
    a, b = start(uploads(one)), start(uploads(two))
    assert table(a).loc['DEMO-900', 'Действующая сумма, ₽'] == '1 100 000 ₽'
    assert table(b).loc['DEMO-900', 'Действующая сумма, ₽'] == '1 200 000 ₽'
    a.run()
    assert table(a).loc['DEMO-900', 'Действующая сумма, ₽'] == '1 100 000 ₽'
    assert a.session_state['processor'] is not b.session_state['processor']
    assert b'1 200 000' not in a.session_state['html_report'][1]
    assert b'1 100 000' not in b.session_state['html_report'][1]
