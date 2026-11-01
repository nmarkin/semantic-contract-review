from dataclasses import replace
from datetime import date
from unittest.mock import Mock

import pytest

from contract_review import resolve_contracts
from contract_review.models import Field
from contract_review.resolution_models import ResolutionState
from contract_review.table_view import ContractStatus, contract_status, styled_contracts
from test_app import start, select_contract
from test_resolver import base, amendment, resolve


def test_example_colors(examples):
    contracts = resolve_contracts(examples.values(), date(2026, 3, 10)).contracts
    assert [contract_status(c) for c in contracts] == [ContractStatus.PROPOSAL, ContractStatus.PROPOSAL, ContractStatus.MISSING]


@pytest.mark.parametrize('state,proposal,expected', [
    (ResolutionState.RESOLVED, False, ContractStatus.CONFIRMED),
    (ResolutionState.RESOLVED, True, ContractStatus.PROPOSAL),
    (ResolutionState.MISSING, True, ContractStatus.MISSING),
    (ResolutionState.REQUIRES_REVIEW, True, ContractStatus.REQUIRES_REVIEW),
])
def test_status_priority(state, proposal, expected):
    documents = [base()]
    if proposal:
        documents.append(amendment(status='не подписано', signed=None))
    contract = resolve(*documents)
    contract = replace(contract, fields=tuple(replace(f, state=state) if f.field == Field.TOTAL_AMOUNT else f for f in contract.fields))
    assert contract_status(contract) == expected


def test_review_wins_over_missing():
    contract = resolve(base(amount=None), amendment(amount=None, deadline='15.04.2030', status='статус неизвестен'))
    assert contract.get(Field.TOTAL_AMOUNT).state == ResolutionState.MISSING
    assert contract_status(contract) == ContractStatus.REQUIRES_REVIEW


def test_only_identifier_cells_are_styled(examples):
    contracts = resolve_contracts(examples.values(), date(2026, 3, 10)).contracts
    styler = styled_contracts(contracts)._compute()
    assert set(styler.ctx) == {(0, 0), (1, 0), (2, 0)}
    assert styler.ctx[(0, 0)][0] == ('background-color', '#FFF0CE')
    assert styler.ctx[(2, 0)][0] == ('background-color', '#E4EAF2')


@pytest.mark.parametrize('review', [False, True])
def test_zero_proposals_has_no_amber_container_or_false_explanation(review):
    docs = [base()]
    if review:
        docs.append(amendment(status='статус неизвестен'))
    app = start([(f'{i}.txt', d.document.original_text.encode(), 'text/plain') for i, d in enumerate(docs)])
    app.date_input(key='as_of').set_value(date(2030, 3, 10)).run()
    assert not app.exception
    assert any(c.value == 'Несогласованных изменений не обнаружено' for c in app.caption)
    empty, = [e for e in app.expander if e.label == 'Несогласованные изменения (0)']
    assert not empty.proto.expanded
    assert not any('не применены к действующим' in c.value for c in app.caption)
    assert not any(getattr(b.proto, 'id', '').endswith('-proposals') for b in app.get('flex_container'))
    if review:
        assert any(e.label == 'Другие непримененные условия и документы' for e in app.expander)


@pytest.mark.parametrize('count', [1, 2])
def test_proposal_count_is_cards_not_fact_candidates(count):
    docs = [base()] + [amendment(status='не подписано', amount=str(150000 + i), deadline='10.05.2030') for i in range(count)]
    app = start([(f'{i}.txt', d.document.original_text.encode(), 'text/plain') for i, d in enumerate(docs)])
    app.date_input(key='as_of').set_value(date(2030, 3, 10)).run()
    assert not app.exception
    assert any(e.label == f'Несогласованные изменения ({count})' for e in app.expander)


def test_colors_update_for_date_and_new_upload_without_selection_reparse(monkeypatch):
    import contract_review.presentation as p
    parse = Mock(wraps=p.parse_txt_bytes)
    monkeypatch.setattr(p, 'parse_txt_bytes', parse)
    app = start()
    app.button(key='open_example').click().run()
    original = app.dataframe[0].proto.arrow_data.styler.styles
    select_contract(app, 'STN-102')
    assert parse.call_count == 6 and app.dataframe[0].proto.arrow_data.styler.styles == original
    # Before signatures the same rows need review, without parsing again.
    app.date_input(key='as_of').set_value(date(2025, 1, 1)).run()
    assert '#F8DFD8' in app.dataframe[0].proto.arrow_data.styler.styles and parse.call_count == 6
    app.file_uploader[0].set_value([('new.txt', base().document.original_text.encode(), 'text/plain')]).run()
    app.date_input(key='as_of').set_value(date(2030, 3, 10)).run()
    assert '#DFF0E5' in app.dataframe[0].proto.arrow_data.styler.styles and parse.call_count == 7

