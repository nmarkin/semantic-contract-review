"""Stage 3.5B: actual texts through the public pipeline, including all evidence."""

from dataclasses import fields, is_dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock

import pytest
from streamlit.testing.v1 import AppTest

from contract_review.models import Evidence, Field
from contract_review.presentation import SessionProcessor, Upload, process_uploads, source_label
from contract_review.resolution_models import Reason, ResolutionState

ROOT = Path(__file__).resolve().parents[1]
AS_OF = date(2030, 3, 10)


def base(number='ТЕСТ-XYZ/77', amount='100 000', deadline='01.04.2030'):
    return (f'ДОГОВОР ПОСТАВКИ № {number}\nСтатус: подписан обеими сторонами 01.01.2030.\n\n'
            'Поставщик: ООО «Завод».\n\n'
            f'Общая стоимость договора составляет {amount} рублей.\n\n'
            f'Поставщик обязуется поставить товар не позднее {deadline}.')


def change(amount='150 000', effective='01.03.2030', signed='01.03.2030',
           status='подписано обеими сторонами', supplier='ООО «Завод»', extra=''):
    return ('ДОПОЛНИТЕЛЬНОЕ СОГЛАШЕНИЕ № 1 К ДОГОВОРУ ТЕСТ-XYZ/77\nДата: 01.02.2030\n'
            f'Статус: {status} {signed}.\n\nПоставщик: {supplier}.\n\n'
            f'Общая стоимость договора составляет {amount} рублей.\n\n'
            f'Настоящее соглашение вступает в силу {effective}.\n\n{extra}')


def validate_all(result):
    documents = {e.document.id: e.document for e in result.extractions}
    def visit(value):
        if isinstance(value, Evidence):
            value.validate(documents[value.document_id])
            assert documents[value.document_id].original_text[value.start:value.end] == value.quote
        elif is_dataclass(value):
            for field in fields(value):
                visit(getattr(value, field.name))
        elif isinstance(value, tuple):
            for item in value:
                visit(item)
    visit(result)  # sources, warnings, history, not_applied, grouping and candidates


@pytest.mark.parametrize('case', [
    'new', 'changed_base', 'changed_amendment', 'sequential', 'same_date_conflict',
    'amount_only', 'late_draft', 'letter', 'missing_base', 'missing_specification',
    'ambiguous_link', 'supplier_conflict', 'signed_later', 'unknown_effective',
    'duplicate', 'same_name', 'conflicting_values',
])
def test_contract_edge_matrix(case):
    texts = [base()]
    amount, deadline, state, reason = Decimal('100000'), date(2030, 4, 1), ResolutionState.RESOLVED, None
    if case == 'changed_base':
        texts = [base(amount='222 333', deadline='19.04.2030')]
        amount, deadline = Decimal('222333'), date(2030, 4, 19)
    elif case in ('amount_only', 'changed_amendment', 'sequential'):
        texts.append(change())
        amount = Decimal('150000')
        if case == 'changed_amendment':
            texts[-1] = change(amount='170 000', extra='Поставщик обязуется поставить товар не позднее 20.05.2030.')
            amount, deadline = Decimal('170000'), date(2030, 5, 20)
        if case == 'sequential':
            texts.append(change(amount='180 000', effective='05.03.2030', signed='05.03.2030'))
            amount = Decimal('180000')
    elif case == 'same_date_conflict':
        texts += [change(), change(amount='190 000')]
        amount, state, reason = None, ResolutionState.REQUIRES_REVIEW, Reason.SAME_DATE_CONFLICT
    elif case == 'late_draft':
        texts.append(change(status='не подписано', signed='', extra='Дата проекта: 01.04.2030.'))
        reason = Reason.UNSIGNED_DRAFT
    elif case == 'letter':
        texts.append('ПИСЬМО ПОСТАВЩИКА\nК договору ТЕСТ-XYZ/77\n\nПредлагаем перенести срок поставки на 20.05.2030.')
        reason = Reason.SUPPLIER_LETTER
    elif case == 'missing_base':
        texts = [change()]
        amount, deadline, state, reason = None, None, ResolutionState.REQUIRES_REVIEW, Reason.MISSING_BASE
    elif case == 'missing_specification':
        texts = [base().replace('Общая стоимость договора составляет 100 000 рублей.',
                               'Общая стоимость договора определяется спецификацией, которая не входит в комплект.')]
        amount, state = None, ResolutionState.MISSING
    elif case == 'ambiguous_link':
        texts.append(change(extra='Также к договору № OTHER-88.'))
        amount, state, reason = None, ResolutionState.REQUIRES_REVIEW, Reason.AMBIGUOUS_CONTRACT_REFERENCE
    elif case == 'supplier_conflict':
        texts.append(change(supplier='ООО «Другой завод»'))
        amount, state, reason = None, ResolutionState.REQUIRES_REVIEW, Reason.SUPPLIER_CONFLICT
    elif case == 'signed_later':
        texts.append(change(signed='20.03.2030'))
        reason = Reason.SIGNED_AFTER_AS_OF
    elif case == 'unknown_effective':
        texts.append(change(effective='после согласования'))
        amount, state, reason = None, ResolutionState.REQUIRES_REVIEW, Reason.UNCONFIRMED_APPLICABILITY
    elif case == 'duplicate':
        texts *= 2
    elif case == 'same_name':
        texts.append(base(amount='200 000'))
        amount, deadline, state, reason = None, None, ResolutionState.REQUIRES_REVIEW, Reason.MULTIPLE_BASES
    elif case == 'conflicting_values':
        texts[0] += '\nОбщая стоимость договора составляет 300 000 рублей.'
        amount, state, reason = None, ResolutionState.REQUIRES_REVIEW, Reason.AMBIGUOUS_FIELD
    uploads = [Upload('same.txt' if case in ('duplicate', 'same_name') else f'{i}.txt', t.encode()) for i, t in enumerate(texts)]
    result = process_uploads(uploads, AS_OF)
    assert not result.errors
    contract = result.report.get('ТЕСТ-XYZ/77')
    assert contract.get(Field.TOTAL_AMOUNT).value == amount
    assert contract.get(Field.TOTAL_AMOUNT).state == state
    assert contract.get(Field.DELIVERY_DEADLINE).value == deadline
    if reason:
        assert reason in {w.reason for w in contract.warnings}
    if case in ('late_draft', 'letter', 'signed_later', 'supplier_conflict', 'unknown_effective'):
        assert reason in {n.reason for n in contract.not_applied}
    if case == 'sequential':
        assert [h.sources[0].value for h in contract.get(Field.TOTAL_AMOUNT).history] == [Decimal('100000'), Decimal('150000'), Decimal('180000')]
    if case in ('duplicate', 'same_name'):
        assert result.notices
        assert len(result.extractions) == (1 if case == 'duplicate' else 2)
    if case == 'missing_specification':
        assert contract.get(Field.TOTAL_AMOUNT).explanations
    validate_all(result)


@pytest.mark.parametrize('codec', ['utf-8', 'utf-8-sig', 'cp1251', 'koi8-r', 'cp866'])
@pytest.mark.parametrize('newline', ['\n', '\r\n'])
def test_text_variants_have_exact_evidence(codec, newline):
    text = base().replace('№ ', '').replace('«', '"').replace('»', '"')
    text = text.replace('\n', newline).replace('составляет ', 'составляет   ') + newline * 5
    result = process_uploads([Upload('any.txt', text.encode(codec))], AS_OF)
    assert not result.errors
    assert result.report.contracts[0].get(Field.TOTAL_AMOUNT).value == Decimal('100000')
    assert result.extractions[0].document.original_text == ('\ufeff' if codec == 'utf-8-sig' else '') + text
    validate_all(result)


def test_unsupported_numeric_spacing_requires_review_instead_of_guessing():
    result = process_uploads([Upload('spaces.txt', base().replace('100 000', '100   000').encode())], AS_OF)
    value = result.report.contracts[0].get(Field.TOTAL_AMOUNT)
    assert value.state == ResolutionState.REQUIRES_REVIEW and value.value is None
    validate_all(result)


def test_long_document_and_html_remain_literal():
    text = ('Текст & <script>alert(1)</script> ' + 'x' * 5000 + '\n\n') * 100 + base()
    result = process_uploads([Upload('long.txt', text.encode())], AS_OF)
    assert not result.errors
    assert result.report.contracts[0].get(Field.TOTAL_AMOUNT).value == Decimal('100000')
    validate_all(result)


def test_session_cache_invalidation_and_isolation(monkeypatch):
    import contract_review.presentation as p
    parse, extract, resolve = Mock(wraps=p.parse_txt_bytes), Mock(wraps=p.extract_facts), Mock(wraps=p.resolve_contracts)
    monkeypatch.setattr(p, 'parse_txt_bytes', parse)
    monkeypatch.setattr(p, 'extract_facts', extract)
    monkeypatch.setattr(p, 'resolve_contracts', resolve)
    processor = SessionProcessor()
    uploads = [Upload('base.txt', base().encode()), Upload('change.txt', change().encode())]
    first = processor.process(uploads, AS_OF)
    assert processor.process(uploads, AS_OF) is first
    assert (parse.call_count, extract.call_count, resolve.call_count) == (2, 2, 1)
    processor.process(uploads, date(2030, 2, 1))
    assert (parse.call_count, extract.call_count, resolve.call_count) == (2, 2, 2)
    uploads[1] = Upload('change.txt', change(amount='333 000').encode())
    processor.process(uploads, AS_OF)
    assert (parse.call_count, extract.call_count, resolve.call_count) == (3, 3, 3)
    processor.process(uploads, AS_OF, {uploads[1].id: 'utf-8'})
    assert (parse.call_count, extract.call_count, resolve.call_count) == (4, 4, 4)
    SessionProcessor().process(uploads, AS_OF)
    assert (parse.call_count, extract.call_count, resolve.call_count) == (6, 6, 5)
    processor.process([], AS_OF)
    assert not processor._files  # deleted documents do not remain cached


@pytest.mark.parametrize('limit', ['MAX_FILES', 'MAX_FILE_BYTES', 'MAX_BATCH_BYTES'])
def test_limits_block_whole_batch_before_parsing(monkeypatch, limit):
    import contract_review.presentation as p
    monkeypatch.setattr(p, limit, 1)
    parse = Mock(side_effect=AssertionError('must reject before parsing'))
    monkeypatch.setattr(p, 'parse_txt_bytes', parse)
    result = process_uploads([Upload('a.txt', b'ab'), Upload('b.txt', b'cd')], AS_OF)
    assert result.errors and result.report is None and not result.extractions
    assert not parse.called


def test_same_name_versions_have_distinct_source_labels():
    result = process_uploads([Upload('same.txt', base().encode()), Upload('same.txt', base(amount='200 000').encode())], AS_OF)
    documents = {e.document.id: e.document for e in result.extractions}
    assert len({source_label(d, documents) for d in documents.values()}) == 2


def test_duplicate_ambiguous_upload_does_not_duplicate_widget():
    app = AppTest.from_file(str(ROOT / 'app.py')).run()
    file = ('short.txt', 'Привет'.encode('cp1251'), 'text/plain')
    app.file_uploader[0].set_value([file, file]).run()
    assert not app.exception and len(app.selectbox) == 1
    assert any('Дубликат' in x.value for x in app.text)
    app.selectbox[0].select('cp1251').run()
    assert not app.exception and not app.error


def test_ui_over_limit_hides_previous_report(monkeypatch):
    app = AppTest.from_file(str(ROOT / 'app.py')).run()
    app.button(key='open_example').click().run()
    assert app.dataframe
    monkeypatch.setattr('contract_review.presentation.MAX_FILES', 5)
    app.run()
    assert not app.exception and app.error and not app.dataframe and not app.metric
    assert any('Уменьшите комплект' in x.value for x in app.caption)


def test_repeated_bytes_under_new_name_and_encoding_are_explicit_duplicates():
    text = base()
    result = process_uploads([Upload('first.txt', text.encode()), Upload('copy.txt', text.encode('cp1251'))], AS_OF)
    assert len(result.extractions) == 1 and len(result.report.contracts) == 1
    assert any('текст совпадает' in notice for notice in result.notices)
    validate_all(result)


def test_ui_actions_do_not_reextract_and_new_session_is_independent(monkeypatch):
    import contract_review.presentation as p
    extract, resolve = Mock(wraps=p.extract_facts), Mock(wraps=p.resolve_contracts)
    monkeypatch.setattr(p, 'extract_facts', extract)
    monkeypatch.setattr(p, 'resolve_contracts', resolve)
    app = AppTest.from_file(str(ROOT / 'app.py')).run()
    app.button(key='open_example').click().run()
    assert (extract.call_count, resolve.call_count) == (6, 1)
    app.checkbox(key='filter_enabled').check().run()
    app.checkbox(key='filter_enabled').uncheck().run()
    app.session_state['contracts_table'] = {'selection': {'rows': [1], 'columns': [], 'cells': []}}
    app.run()
    app.button(key='current_STN-102_total_amount_0').click().run()
    app.button(key='close_source').click().run()
    assert not app.exception
    assert (extract.call_count, resolve.call_count) == (6, 1)
    app.date_input(key='as_of').set_value(date(2026, 2, 28)).run()
    assert (extract.call_count, resolve.call_count) == (6, 2)
    other = AppTest.from_file(str(ROOT / 'app.py')).run()
    assert not other.dataframe and 'processor' not in other.session_state
    other.button(key='open_example').click().run()
    assert (extract.call_count, resolve.call_count) == (12, 3)
    app.button(key='clear_uploads').click().run()
    assert 'processor' not in app.session_state
