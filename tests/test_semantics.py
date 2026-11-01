from datetime import date
from decimal import Decimal

from test_resolver import base, amendment, resolve
from contract_review.models import Field, Role
from contract_review.resolution_models import Reason
from contract_review import resolve_contracts, parse_document, extract_facts
from contract_review.proposal_view import document_cards, change_lines, grouped_source_html
from test_app import start, select_contract


def test_applied_transfer_keeps_context_without_false_rejection():
    document = amendment(amount=None, extra='Срок поставки с 01.04.2030 на 30.04.2030.')
    contract = resolve(base(), document)
    assert not contract.not_applied and not contract.warnings
    assert contract.get(Field.DELIVERY_DEADLINE).value == date(2030, 4, 30)
    assert len(contract.get(Field.DELIVERY_DEADLINE).history) == 2
    assert [c.role for c in document.get(Field.DELIVERY_DEADLINE).candidates] == [Role.REFERENCED_TERM, Role.AMENDMENT_TERM]
    document.validate_evidence()


def test_applied_transfer_and_separate_proposal_same_paragraph_are_distinct():
    document = amendment(amount='150 000', extra=(
        'Срок поставки с 01.04.2030 на 30.04.2030. '
        'Предлагаем изменить срок поставки с 30.04.2030 на 20.05.2030.'))
    contract = resolve(base(), document)
    assert contract.get(Field.TOTAL_AMOUNT).value == Decimal('150000')
    assert contract.get(Field.DELIVERY_DEADLINE).value == date(2030, 4, 30)
    assert len(contract.not_applied) == 1
    proposal = contract.proposals[0]
    assert proposal.reason == Reason.NON_OPERATIVE_ROLE
    assert {c.value for c in proposal.candidates} == {date(2030, 4, 30), date(2030, 5, 20)}
    assert all(c.role != Role.AMENDMENT_TERM for c in proposal.candidates)
    assert len(contract.warnings) == 1


def test_rejected_transfer_keeps_old_and_new_evidence():
    document = amendment(amount=None, signed='20.03.2030', extra='Срок поставки с 01.04.2030 на 30.04.2030.')
    contract = resolve(base(), document)
    assert contract.get(Field.DELIVERY_DEADLINE).value == date(2030, 4, 1)
    assert contract.not_applied[0].reason == Reason.SIGNED_AFTER_AS_OF
    assert {c.role for c in contract.not_applied[0].candidates} == {Role.REFERENCED_TERM, Role.AMENDMENT_TERM}


def test_letter_groups_dates_and_source_once(examples):
    contract = resolve_contracts(examples.values(), date(2026, 3, 10)).get('STN-102')
    cards = document_cards(contract, contract.proposals)
    assert len(cards) == 1 and len(cards[0].changes) == 1 and len(cards[0].sources) == 1
    assert len(cards[0].sources[0].proofs) == 2
    text = '\n'.join(change_lines(cards[0].changes[0]))
    assert 'Действующий срок: 20.03.2026' in text and 'Предлагаемый срок: 27.03.2026' in text
    assert '+7 дней' in text
    html = grouped_source_html(cards[0].sources[0], cards[0].document)
    assert html.count('Коллеги') == 1 and html.count('<mark>') == 2


def test_draft_has_two_changes_in_one_document_card(examples):
    contract = resolve_contracts(examples.values(), date(2026, 3, 10)).get('STN-101')
    cards = document_cards(contract, contract.proposals)
    assert len(cards) == 1 and len(cards[0].changes) == 2
    assert {c.candidate.field for c in cards[0].changes} == {Field.TOTAL_AMOUNT, Field.DELIVERY_DEADLINE}


def test_independent_proposals_in_one_paragraph_are_not_merged():
    document = amendment(amount=None, extra=(
        'Предлагаем изменить срок поставки с 01.04.2030 на 10.04.2030. '
        'Предлагаем изменить срок поставки с 12.04.2030 на 20.04.2030.'))
    contract = resolve(base(), document)
    cards = document_cards(contract, contract.proposals)
    assert len(cards) == 2 and all(len(card.changes) == len(card.sources) == 1 for card in cards)
    assert cards[0].changes[0].referenced[0].value == date(2030, 4, 1)
    assert cards[1].changes[0].referenced[0].value == date(2030, 4, 12)
    lines = '\n'.join(change_lines(cards[1].changes[0]))
    assert 'Действующий срок: 01.04.2030' in lines and 'Расхождение' in lines
    assert '+19 дней' in lines  # not +8 from the letter's untrusted old date


def test_no_arithmetic_for_unknown_current_or_ambiguous_proposal():
    contract = resolve(amendment(extra='Предлагаем изменить срок поставки на 10.05.2030.'))
    for card in document_cards(contract, contract.proposals):
        assert all('Разница' not in '\n'.join(change_lines(c)) for c in card.changes)
    contract = resolve(base(), amendment(amount=None, extra='Предлагаем изменить срок поставки на 10.05.2030 или 20.05.2030.'))
    card, = document_cards(contract, contract.proposals)
    assert len(card.changes) == 2
    assert all(not c.comparable and 'Разница' not in '\n'.join(change_lines(c)) for c in card.changes)


def test_ui_letter_has_one_quote_and_one_document_link():
    app = start()
    app.button(key='open_example').click().run()
    select_contract(app, 'STN-102')
    html = '\n'.join(x.proto.body for x in app.get('html'))
    assert html.count('Коллеги, по договору') == 1
    links = [b for b in app.button if b.key and b.key.startswith('proposal_STN-102_0_source')]
    assert len(links) == 1
    links[0].click().run()
    assert 'Коллеги' in app.get('iframe')[0].proto.srcdoc


def test_grouped_quotes_preserve_html_crlf_and_exact_offsets():
    original = amendment(amount=None, extra='Предлагаем изменить срок поставки с 01.04.2030 на 20.04.2030.\n<script>alert(1)</script> & <b>x</b>')
    document = extract_facts(parse_document(original.document.original_text.replace('\n', '\r\n')))
    contract = resolve(base(), document)
    card, = document_cards(contract, contract.proposals)
    html = grouped_source_html(card.sources[0], card.document)
    assert '<script>' not in html and '&lt;script&gt;' in html and '&amp;' in html
    assert '\r\n' in html
    for proof in card.sources[0].proofs:
        proof.validate(card.document)


def test_current_and_history_each_render_shared_paragraph_once():
    text = base().document.original_text.replace('рублей.\n\nПоставщик обязуется', 'рублей. Поставщик обязуется')
    app = start([('one.txt', text.encode(), 'text/plain')])
    app.date_input(key='as_of').set_value(date(2030, 3, 10)).run()
    assert not app.exception
    html = '\n'.join(x.proto.body for x in app.get('html'))
    assert html.count('Общая стоимость договора составляет') == 2  # current + historical view, not once per field


def test_applied_and_proposed_same_document_card_contains_only_proposal():
    document = amendment(amount='150 000', extra='Предлагаем изменить срок поставки на 10.05.2030.')
    contract = resolve(base(), document)
    card, = document_cards(contract, contract.proposals)
    assert len(card.changes) == 1 and card.changes[0].candidate.field == Field.DELIVERY_DEADLINE
    assert '150 000' not in '\n'.join(line for c in card.changes for line in change_lines(c))


def test_future_agreement_with_proposal_does_not_lose_rejection_reason():
    document = amendment(amount='150 000', effective='01.04.2030', extra='Предлагаем изменить срок поставки на 10.05.2030.')
    contract = resolve(base(), document)
    card, = document_cards(contract, contract.not_applied)
    assert card.title == 'Непримененные условия'
    assert 'еще не вступил в силу' in card.explanation
    assert {c.candidate.role for c in card.changes} == {Role.AMENDMENT_TERM, Role.PROPOSAL}
