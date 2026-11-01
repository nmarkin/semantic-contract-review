from dataclasses import replace
from datetime import date
from decimal import Decimal
from itertools import permutations

import pytest

from contract_review import extract_facts, filter_delivery_before, parse_document, resolve_contracts
from contract_review.models import Field, Role
from contract_review.resolution_models import Reason, ResolutionState


AS_OF = date(2030, 3, 10)


def analyze(text, name='arbitrary.txt'):
    return extract_facts(parse_document(text, name))


def base(number='OTHER-900/2', supplier='АО «Новый поставщик»', amount='100 000'):
    money = f'Общая стоимость договора составляет {amount} рублей.' if amount else ''
    return analyze(f'ДОГОВОР ПОСТАВКИ № {number}\n'
                   'Дата: 01.01.2030\nСтатус: подписан обеими сторонами 02.01.2030.\n\n'
                   f'Поставщик: {supplier}.\n\n{money}\n\n'
                   'Поставщик обязуется поставить товар не позднее 01.04.2030.')


def amendment(number='OTHER-900/2', amount='150 000', deadline=None,
              effective='01.03.2030', signed='01.03.2030',
              supplier='АО «Новый поставщик»', status='подписано обеими сторонами',
              title='ДОПОЛНИТЕЛЬНОЕ СОГЛАШЕНИЕ', extra=''):
    content = [f'{title} № 7 К ДОГОВОРУ {number}', 'Дата: 01.02.2030',
               f'Статус: {status}' + (f' {signed}.' if signed else '.'),
               f'Поставщик: {supplier}.', '']
    if amount:
        content.append(f'Общая стоимость договора составляет {amount} рублей.\n')
    if deadline:
        content.append(f'Поставщик обязуется поставить товар не позднее {deadline}.\n')
    if effective:
        content.append(f'Настоящее соглашение вступает в силу {effective}.\n')
    content.append(extra)
    return analyze('\n'.join(content))


def resolve(*documents, as_of=AS_OF):
    return resolve_contracts(documents, as_of).get('OTHER-900/2')


def test_acceptance_on_all_six_original_files(examples):
    report = resolve_contracts(examples.values(), date(2026, 3, 10))
    assert not report.unlinked_documents
    expected = {
        'STN-101': (Decimal('1380000'), date(2026, 4, 30)),
        'STN-102': (Decimal('840000'), date(2026, 3, 20)),
        'STN-103': (None, date(2026, 4, 30)),
    }
    assert {c.contract_number for c in report.contracts} == set(expected)
    for number, (amount, deadline) in expected.items():
        contract = report.get(number)
        assert contract.get(Field.TOTAL_AMOUNT).value == amount
        assert contract.get(Field.TOTAL_AMOUNT).state == (ResolutionState.MISSING if amount is None else ResolutionState.RESOLVED)
        assert contract.get(Field.DELIVERY_DEADLINE).value == deadline
        assert contract.get(Field.DELIVERY_DEADLINE).state == ResolutionState.RESOLVED
        assert contract.get(Field.SUPPLIER).state == ResolutionState.RESOLVED
    amount = report.get('STN-101').get(Field.TOTAL_AMOUNT)
    assert [step.sources[0].value for step in amount.history] == [Decimal('1200000'), Decimal('1380000')]
    assert amount.sources[0].evidence.document_id == examples['02_amendment_signed.txt'].document.id
    assert report.get('STN-101').proposals[0].reason == Reason.UNSIGNED_DRAFT
    assert {c.value for c in report.get('STN-101').proposals[0].candidates} == {Decimal('1450000'), date(2026, 5, 10)}
    assert report.get('STN-102').proposals[0].reason == Reason.SUPPLIER_LETTER
    assert {c.role for c in report.get('STN-102').proposals[0].candidates} == {Role.PROPOSAL, Role.REFERENCED_TERM}
    missing = report.get('STN-103').get(Field.TOTAL_AMOUNT)
    assert missing.explanations
    assert 'спецификацией' in missing.explanations[0].quote
    assert 'не входит' in missing.explanations[0].quote
    missing.explanations[0].validate(examples['04_contract.txt'].document)


def test_filter_returns_current_deadline_and_retains_pending_proposal(examples):
    report = resolve_contracts(examples.values(), date(2026, 3, 10))
    filtered = filter_delivery_before(report, date(2026, 4, 1))
    assert [c.contract_number for c in filtered] == ['STN-102']
    assert filtered[0].get(Field.DELIVERY_DEADLINE).value == date(2026, 3, 20)
    assert filtered[0].proposals[0].reason == Reason.SUPPLIER_LETTER
    assert not filter_delivery_before(report, date(2026, 3, 20))  # Strictly before.


@pytest.mark.parametrize('as_of,amount,deadline', [
    (date(2026, 2, 28), Decimal('1200000'), date(2026, 4, 15)),
    (date(2026, 3, 1), Decimal('1380000'), date(2026, 4, 30)),
    (date(2026, 3, 10), Decimal('1380000'), date(2026, 4, 30)),
])
def test_original_documents_before_on_and_after_effective_date(examples, as_of, amount, deadline):
    actual = resolve_contracts(examples.values(), as_of).get('STN-101')
    assert actual.get(Field.TOTAL_AMOUNT).value == amount
    assert actual.get(Field.DELIVERY_DEADLINE).value == deadline


def test_future_effective_change_is_distinct_from_uncertain_change():
    actual = resolve(base(), amendment(effective='20.03.2030'))
    assert actual.get(Field.TOTAL_AMOUNT).value == Decimal('100000')
    assert actual.not_applied[0].reason == Reason.NOT_EFFECTIVE_YET


def test_later_signature_prevents_backdating_until_actual_signing():
    change = amendment(effective='01.02.2030', signed='20.03.2030')
    actual = resolve(base(), change)
    assert actual.get(Field.TOTAL_AMOUNT).value == Decimal('100000')
    assert actual.not_applied[0].reason == Reason.SIGNED_AFTER_AS_OF
    assert resolve(base(), change, as_of=date(2030, 3, 20)).get(Field.TOTAL_AMOUNT).value == Decimal('150000')


def test_signature_date_missing_is_allowed_without_document_date_substitution():
    change = amendment(signed=None)
    actual = resolve(base(), change)
    assert actual.get(Field.TOTAL_AMOUNT).value == Decimal('150000')
    assert not change.get(Field.SIGNING_DATE).candidates


def test_invalid_signature_date_blocks_potential_change():
    actual = resolve(base(), amendment(signed='31.02.2030'))
    field = actual.get(Field.TOTAL_AMOUNT)
    assert field.state == ResolutionState.REQUIRES_REVIEW
    assert field.value is None
    assert not field.sources
    assert field.history[0].sources[0].value == Decimal('100000')
    assert actual.get(Field.DELIVERY_DEADLINE).state == ResolutionState.RESOLVED


@pytest.mark.parametrize('kwargs,reason', [
    ({'title': 'ПРОЕКТ ДОПОЛНИТЕЛЬНОГО СОГЛАШЕНИЯ', 'status': 'не подписано', 'signed': None}, Reason.UNSIGNED_DRAFT),
    ({'status': 'подписано только одной стороной', 'signed': None}, Reason.UNSIGNED_DRAFT),
])
def test_unsigned_changes_do_not_apply(kwargs, reason):
    actual = resolve(base(), amendment(deadline='10.05.2030', **kwargs))
    assert actual.get(Field.TOTAL_AMOUNT).value == Decimal('100000')
    assert actual.get(Field.DELIVERY_DEADLINE).value == date(2030, 4, 1)
    assert actual.proposals[0].reason == reason


def test_missing_amount_stays_missing_and_does_not_become_zero():
    actual = resolve(base(amount=None))
    assert actual.get(Field.TOTAL_AMOUNT).state == ResolutionState.MISSING
    assert actual.get(Field.TOTAL_AMOUNT).value is None


def test_amount_only_amendment_preserves_deadline_and_supplier():
    original = base()
    actual = resolve(original, amendment())
    assert actual.get(Field.TOTAL_AMOUNT).value == Decimal('150000')
    assert actual.get(Field.DELIVERY_DEADLINE).value == date(2030, 4, 1)
    assert actual.get(Field.DELIVERY_DEADLINE).sources[0].evidence.document_id == original.document.id
    assert actual.get(Field.SUPPLIER).sources[0].evidence.document_id == original.document.id


def test_sequential_changes_resolve_per_field_not_per_document():
    first = amendment(amount='170 000', deadline='03.05.2030', effective='01.02.2030')
    second = amendment(amount=None, deadline='05.05.2030', effective='05.02.2030')
    third = amendment(amount='190 000', effective='08.02.2030')
    actual = resolve(third, base(), second, first)
    assert actual.get(Field.TOTAL_AMOUNT).value == Decimal('190000')
    assert actual.get(Field.DELIVERY_DEADLINE).value == date(2030, 5, 5)
    assert [s.effective_on for s in actual.get(Field.TOTAL_AMOUNT).history] == [None, date(2030, 2, 1), date(2030, 2, 8)]
    assert actual.get(Field.TOTAL_AMOUNT).history[-1].previous_sources[0].value == Decimal('170000')


def test_same_date_conflict_has_no_arbitrary_winner():
    actual = resolve(base(), amendment(amount='150 000'), amendment(amount='190 000'))
    field = actual.get(Field.TOTAL_AMOUNT)
    assert field.state == ResolutionState.REQUIRES_REVIEW
    assert field.value is None and not field.sources
    assert len(field.history) == 1
    assert any(i.reason == Reason.SAME_DATE_CONFLICT for i in field.issues)
    assert actual.get(Field.DELIVERY_DEADLINE).state == ResolutionState.RESOLVED


def test_equal_values_at_same_date_keep_both_sources():
    first, second = amendment(), amendment(extra='Остальные условия без изменений.')
    actual = resolve(base(), first, second).get(Field.TOTAL_AMOUNT)
    assert actual.state == ResolutionState.RESOLVED
    assert actual.value == Decimal('150000')
    assert {c.evidence.document_id for c in actual.sources} == {first.document.id, second.document.id}


@pytest.mark.parametrize('kwargs', [
    {'effective': None}, {'effective': '31.02.2030'}, {'effective': 'завтра'},
    {'status': 'подписано', 'signed': None},
])
def test_unconfirmed_applicability_blocks_only_potentially_affected_field(kwargs):
    actual = resolve(base(), amendment(**kwargs))
    assert actual.get(Field.TOTAL_AMOUNT).state == ResolutionState.REQUIRES_REVIEW
    assert actual.get(Field.TOTAL_AMOUNT).value is None
    assert actual.get(Field.DELIVERY_DEADLINE).state == ResolutionState.RESOLVED
    assert any(i.reason == Reason.UNCONFIRMED_APPLICABILITY for i in actual.warnings)


def test_unknown_effective_date_remains_blocking_after_other_dated_changes():
    actual = resolve(base(), amendment(effective=None), amendment(effective='05.03.2030', amount='180 000'))
    assert actual.get(Field.TOTAL_AMOUNT).state == ResolutionState.REQUIRES_REVIEW


def test_later_unambiguous_change_supersedes_dated_conflict():
    actual = resolve(base(), amendment(amount='150 000'), amendment(amount='180 000'),
                     amendment(effective='05.03.2030', amount='200 000'))
    field = actual.get(Field.TOTAL_AMOUNT)
    assert field.state == ResolutionState.RESOLVED
    assert field.value == Decimal('200000')
    assert any(i.reason == Reason.SAME_DATE_CONFLICT for i in actual.warnings)


def test_nonoperative_roles_inside_signed_amendment_are_not_applied():
    change = amendment(amount=None, extra='Предлагается изменить срок поставки на 01.06.2030.')
    actual = resolve(base(), change)
    assert actual.get(Field.DELIVERY_DEADLINE).value == date(2030, 4, 1)
    assert actual.proposals[0].reason == Reason.NON_OPERATIVE_ROLE


def test_old_value_in_signed_transfer_is_not_an_operative_conflict():
    change = amendment(amount=None, extra='Изменить срок поставки с 01.04.2030 на 01.06.2030.')
    actual = resolve(base(), change)
    assert actual.get(Field.DELIVERY_DEADLINE).state == ResolutionState.RESOLVED
    assert actual.get(Field.DELIVERY_DEADLINE).value == date(2030, 6, 1)
    assert not actual.not_applied and not actual.warnings
    assert any(c.role == Role.REFERENCED_TERM for c in change.get(Field.DELIVERY_DEADLINE).candidates)


def test_ambiguous_modified_field_does_not_silently_retain_previous_as_confirmed():
    change = amendment(extra='Общая стоимость договора составляет 190 000 рублей.')
    actual = resolve(base(), change)
    assert actual.get(Field.TOTAL_AMOUNT).state == ResolutionState.REQUIRES_REVIEW
    assert actual.get(Field.TOTAL_AMOUNT).value is None


def test_missing_specification_in_amendment_requires_review_not_fallback():
    change = amendment(amount=None, extra='Общая стоимость определяется спецификацией в приложении № 2. Приложение отсутствует.')
    actual = resolve(base(), change)
    assert actual.get(Field.TOTAL_AMOUNT).state == ResolutionState.REQUIRES_REVIEW
    assert any('спецификацией' in e.quote for i in actual.get(Field.TOTAL_AMOUNT).issues for e in i.evidence)


def test_supplier_conflict_blocks_amendment_and_exposes_evidence():
    change = amendment(supplier='ООО «Другой поставщик»')
    actual = resolve(base(), change)
    assert actual.get(Field.TOTAL_AMOUNT).state == ResolutionState.REQUIRES_REVIEW
    assert actual.get(Field.SUPPLIER).state == ResolutionState.REQUIRES_REVIEW
    assert actual.get(Field.DELIVERY_DEADLINE).value == date(2030, 4, 1)
    conflict = next(i for i in actual.warnings if i.reason == Reason.SUPPLIER_CONFLICT)
    assert any('Другой поставщик' in e.quote for e in conflict.evidence)


def test_ambiguous_links_are_not_attached_and_mark_affected_contracts():
    change = amendment(extra='Также по договору SECOND-123 изменяется стоимость.')
    report = resolve_contracts([base(), base(number='SECOND-123'), change], AS_OF)
    assert len(report.unlinked_documents) == 1
    problem = report.unlinked_documents[0]
    assert problem.referenced_contracts == ('OTHER-900/2', 'SECOND-123')
    assert problem.issue.reason == Reason.AMBIGUOUS_CONTRACT_REFERENCE
    for contract in report.contracts:
        assert change not in contract.documents
        assert contract.get(Field.TOTAL_AMOUNT).state == ResolutionState.REQUIRES_REVIEW
        assert contract.get(Field.DELIVERY_DEADLINE).state == ResolutionState.RESOLVED


def test_unlinked_documents_are_returned_separately():
    unattached = analyze('ПИСЬМО ПОСТАВЩИКА\nПредлагается изменить срок поставки на 01.06.2030.')
    report = resolve_contracts([base(), unattached], AS_OF)
    assert report.unlinked_documents[0].issue.reason == Reason.NO_CONTRACT_REFERENCE
    assert report.contracts[0].get(Field.DELIVERY_DEADLINE).state == ResolutionState.RESOLVED


def test_amendment_without_main_contract_does_not_create_confirmed_values():
    actual = resolve(amendment())
    assert all(f.state == ResolutionState.REQUIRES_REVIEW and f.value is None for f in actual.fields)
    assert any(i.reason == Reason.MISSING_BASE for i in actual.warnings)


def test_multiple_main_contracts_are_not_chosen_by_input_order():
    actual = resolve(base(), base(amount='110 000'), amendment())
    assert all(f.state == ResolutionState.REQUIRES_REVIEW for f in actual.fields)
    assert any(i.reason == Reason.MULTIPLE_BASES for i in actual.warnings)


def test_duplicate_upload_same_document_id_is_idempotent():
    original, change = base(), amendment()
    assert resolve(original, change) == resolve(original, original, change, change)


def test_all_order_permutations_have_identical_results():
    documents = [base(), amendment(), amendment(amount='190 000', effective='05.03.2030'),
                 amendment(title='ПРОЕКТ ДОПОЛНИТЕЛЬНОГО СОГЛАШЕНИЯ', status='не подписано', signed=None)]
    expected = resolve_contracts(documents, AS_OF)
    for ordering in permutations(documents):
        assert resolve_contracts(ordering, AS_OF) == expected


def test_filename_changes_do_not_affect_resolution(examples):
    original = resolve_contracts(examples.values(), date(2026, 3, 10))
    renamed = [analyze(e.document.original_text, f'misleading-{i}.txt') for i, e in enumerate(examples.values())]
    actual = resolve_contracts(reversed(renamed), date(2026, 3, 10))
    for left, right in zip(original.contracts, actual.contracts):
        assert left.contract_number == right.contract_number
        assert left.fields == right.fields
        assert left.warnings == right.warnings
        assert [(i.reason, i.fields, i.candidates) for i in left.not_applied] == [(i.reason, i.fields, i.candidates) for i in right.not_applied]


def test_every_confirmed_value_and_history_step_has_original_evidence(examples):
    report = resolve_contracts(examples.values(), date(2026, 3, 10))
    docs = {e.document.id: e.document for e in examples.values()}
    for contract in report.contracts:
        for field in contract.fields:
            if field.state == ResolutionState.RESOLVED:
                assert field.sources
            candidates = [*field.sources]
            for step in field.history:
                candidates.extend(step.sources)
                candidates.extend(step.previous_sources)
            for candidate in candidates:
                candidate.evidence.validate(docs[candidate.evidence.document_id])
                assert candidate in next(e for e in examples.values() if e.document.id == candidate.evidence.document_id).get(candidate.field).candidates


def test_filter_omits_unconfirmed_deadline():
    report = resolve_contracts([base(), amendment(amount=None, deadline='01.03.2030', effective=None)], AS_OF)
    assert not filter_delivery_before(report, date(2031, 1, 1))


def test_conflicting_document_ids_are_rejected():
    original, other = base(), base(amount='110 000')
    other = analyze(other.document.original_text)
    collision = extract_facts(parse_document(other.document.original_text, document_id=original.document.id))
    with pytest.raises(ValueError, match='document_id'):
        resolve_contracts([original, collision], AS_OF)


def test_corrupt_evidence_is_rejected_before_resolution():
    original = base()
    amount = original.get(Field.TOTAL_AMOUNT)
    bad = replace(amount.candidates[0], evidence=replace(amount.candidates[0].evidence, quote='invented'))
    corrupted = replace(original, fields=tuple(replace(f, candidates=(bad,)) if f.field == Field.TOTAL_AMOUNT else f for f in original.fields))
    with pytest.raises(ValueError, match='Evidence'):
        resolve_contracts([corrupted], AS_OF)


def test_independent_fields_changed_on_same_date_do_not_conflict():
    actual = resolve(base(), amendment(), amendment(amount=None, deadline='15.05.2030'))
    assert actual.get(Field.TOTAL_AMOUNT).value == Decimal('150000')
    assert actual.get(Field.DELIVERY_DEADLINE).value == date(2030, 5, 15)
    assert not any(i.reason == Reason.SAME_DATE_CONFLICT for i in actual.warnings)


def test_uncertain_change_without_potential_terms_does_not_block_fields():
    actual = resolve(base(), amendment(amount=None, effective=None, extra='Остальные условия без изменений.'))
    assert all(f.state == ResolutionState.RESOLVED for f in actual.fields)
    assert actual.not_applied[0].reason == Reason.UNCONFIRMED_APPLICABILITY


def test_ambiguous_letter_link_is_reported_without_changing_current_terms():
    letter = analyze('ПИСЬМО ПОСТАВЩИКА\nТема: договор OTHER-900/2 и договор SECOND-123\n\n'
                     'Предлагается изменить срок поставки на 01.06.2030.')
    report = resolve_contracts([base(), letter], AS_OF)
    assert report.unlinked_documents
    assert report.contracts[0].get(Field.DELIVERY_DEADLINE).state == ResolutionState.RESOLVED


def test_ambiguous_future_amendment_link_does_not_block_present_value():
    change = amendment(effective='20.03.2030', extra='Также по договору SECOND-123 изменяется стоимость.')
    report = resolve_contracts([base(), change], AS_OF)
    assert report.unlinked_documents
    assert report.contracts[0].get(Field.TOTAL_AMOUNT).value == Decimal('100000')


def test_unavailable_main_contract_prevents_applying_amendment():
    future_base = analyze(base().document.original_text.replace('02.01.2030', '20.03.2030'))
    actual = resolve(future_base, amendment())
    assert all(f.state == ResolutionState.REQUIRES_REVIEW and not f.sources for f in actual.fields)
    assert all(not f.history for f in actual.fields)


def test_confirmed_and_uncertain_change_on_same_date_are_both_withheld():
    uncertain = amendment(amount='180 000', signed='31.02.2030')
    actual = resolve(base(), amendment(), uncertain)
    assert actual.get(Field.TOTAL_AMOUNT).state == ResolutionState.REQUIRES_REVIEW
    assert len(actual.get(Field.TOTAL_AMOUNT).history) == 1
    assert len(actual.not_applied) == 2


def test_ambiguity_in_baseline_can_be_replaced_by_explicit_later_term():
    original = analyze(base().document.original_text + '\nОбщая стоимость договора составляет 110 000 рублей.')
    actual = resolve(original, amendment())
    assert actual.get(Field.TOTAL_AMOUNT).value == Decimal('150000')
    assert actual.get(Field.TOTAL_AMOUNT).history[0].initial is False
    assert any(i.reason == Reason.AMBIGUOUS_FIELD for i in actual.warnings)


def test_new_number_and_supplier_require_no_production_changes():
    number = 'FRESH-77/8'
    supplier = 'ИП «Совсем новый»'
    report = resolve_contracts([base(number=number, supplier=supplier),
                                amendment(number=number, supplier=supplier, amount='765 432')], AS_OF)
    assert report.get(number).get(Field.SUPPLIER).value == supplier
    assert report.get(number).get(Field.TOTAL_AMOUNT).value == Decimal('765432')


def test_empty_input_and_explicit_date_parameter():
    assert resolve_contracts([], AS_OF).contracts == ()
    with pytest.raises(TypeError, match='as_of'):
        resolve_contracts([base()], '10.03.2030')


def test_supplier_conflict_in_proposal_is_reported_without_changing_terms():
    proposal = amendment(title='ПРОЕКТ ДОПОЛНИТЕЛЬНОГО СОГЛАШЕНИЯ', status='не подписано',
                         signed=None, supplier='ООО «Чужой поставщик»')
    actual = resolve(base(), proposal)
    assert actual.get(Field.TOTAL_AMOUNT).value == Decimal('100000')
    assert actual.get(Field.SUPPLIER).state == ResolutionState.RESOLVED
    assert actual.proposals[0].reason == Reason.UNSIGNED_DRAFT
    assert any(i.reason == Reason.SUPPLIER_CONFLICT for i in actual.warnings)
