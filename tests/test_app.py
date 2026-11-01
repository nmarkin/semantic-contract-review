"""Real TXT bytes enter AppTest's file uploader; no fabricated ResolutionReport."""

from datetime import date
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def uploaded_files():
    return [(p.name, p.read_bytes(), 'text/plain') for p in sorted((ROOT / 'documents').glob('*.txt'))]


def start(files=None):
    app = AppTest.from_file(str(ROOT / 'app.py'), default_timeout=20).run()
    if files is not None:
        app.file_uploader[0].set_value(files).run()
    assert not app.exception
    return app


def table(app):
    return app.dataframe[0].value.set_index('Номер договора')


def select_contract(app, number):
    index = list(table(app).index).index(number)
    app.session_state['contracts_table'] = {'selection': {'rows': [index], 'columns': [], 'cells': []}}
    app.run()
    assert not app.exception


def text_values(app):
    return '\n'.join(x.value for x in app.text)


def test_empty_application_and_default_date():
    app = start()
    assert app.session_state['review_date'] == date(2026, 3, 10)
    assert app.file_uploader[0].accept_multiple_files
    assert not app.dataframe
    assert not app.date_input and not app.selectbox
    assert app.button(key='open_example').label == 'Открыть пример'


def test_six_uploaded_documents_table_details_and_sources(uploaded_files):
    app = start(uploaded_files)
    rows = table(app)
    assert list(rows.index) == ['STN-101', 'STN-102', 'STN-103']
    assert rows.loc['STN-101', 'Действующая сумма, ₽'] == '1 380 000 ₽'
    assert rows.loc['STN-101', 'Срок поставки'] == '30.04.2026'
    assert rows.loc['STN-102', 'Действующая сумма, ₽'] == '840 000 ₽'
    assert rows.loc['STN-102', 'Срок поставки'] == '20.03.2026'
    assert rows.loc['STN-103', 'Действующая сумма, ₽'] == 'Нет данных'
    assert any('Документов: 6 · Договоров: 3' in x.value for x in app.caption)
    assert '02_amendment_signed.txt' in text_values(app)
    assert 'Предлагаемая сумма: 1 450 000 ₽' in text_values(app)
    assert any('01.03.2026' in x.value for x in app.caption)
    html = '\n'.join(x.proto.body for x in app.get('html'))
    assert '1 380 000</mark>' in html
    select_contract(app, 'STN-103')
    html = '\n'.join(x.proto.body for x in app.get('html'))
    assert 'определяется спецификацией' in html and 'не входит' in html


def test_filter_keeps_letter_and_distinguishes_old_and_proposed_date(uploaded_files):
    app = start(uploaded_files)
    app.checkbox(key='filter_enabled').check().run()
    app.date_input(key='before').set_value(date(2026, 4, 1)).run()
    assert list(table(app).index) == ['STN-102']
    assert 'Предложено изменение' in table(app).loc['STN-102', 'Состояние / предупреждения']
    text = text_values(app)
    assert 'Упомянутое прежнее значение — контекст: 20.03.2026' in text
    assert 'Предлагаемый срок: 27.03.2026' in text
    app.date_input(key='before').set_value(date(2026, 3, 20)).run()
    assert not app.dataframe
    assert any('Подходящих договоров нет' in x.value for x in app.info)


def test_as_of_changes_current_values(uploaded_files):
    app = start(uploaded_files)
    app.date_input(key='as_of').set_value(date(2026, 2, 28)).run()
    assert table(app).loc['STN-101', 'Действующая сумма, ₽'] == '1 200 000 ₽'
    assert table(app).loc['STN-101', 'Срок поставки'] == '15.04.2026'
    app.date_input(key='as_of').set_value(date(2026, 3, 10)).run()
    assert table(app).loc['STN-101', 'Действующая сумма, ₽'] == '1 380 000 ₽'


def test_modified_bytes_update_amount_deadline_and_filter(uploaded_files):
    app = start(uploaded_files)
    changed = [(name, data.replace(b'1 380 000', b'1 390 000').replace(b'30.04.2026', b'25.03.2026'), mime)
               if name == '02_amendment_signed.txt' else (name, data, mime)
               for name, data, mime in uploaded_files]
    app.file_uploader[0].set_value(changed).run()
    assert not app.exception
    assert table(app).loc['STN-101', 'Действующая сумма, ₽'] == '1 390 000 ₽'
    assert table(app).loc['STN-101', 'Срок поставки'] == '25.03.2026'
    app.checkbox(key='filter_enabled').check().run()
    assert list(table(app).index) == ['STN-101', 'STN-102']


def test_new_contract_and_renamed_files_add_rows_without_special_cases(uploaded_files):
    new = ('ДОГОВОР ПОСТАВКИ № NEW-888/2\nСтатус: подписан обеими сторонами 01.02.2026.\n\n'
           'Поставщик: АО «Новый».\n\nОбщая стоимость договора составляет 987 654 рублей.\n\n'
           'Поставщик обязуется поставить товар не позднее 15.03.2026.').encode()
    files = [(f'renamed-{i}.txt', data, mime) for i, (_, data, mime) in enumerate(reversed(uploaded_files))]
    app = start(files + [('brand-new.txt', new, 'text/plain')])
    assert len(table(app)) == 4
    assert table(app).loc['NEW-888/2', 'Действующая сумма, ₽'] == '987 654 ₽'
    assert table(app).loc['STN-101', 'Действующая сумма, ₽'] == '1 380 000 ₽'


@pytest.mark.parametrize('replacement', [
    'подписано', 'статус неизвестен',
])
def test_unknown_signature_displays_review_not_old_values(uploaded_files, replacement):
    files = [(name, data.decode().replace('подписано обеими сторонами', replacement).encode(), mime)
             if name == '02_amendment_signed.txt' else (name, data, mime)
             for name, data, mime in uploaded_files]
    app = start(files)
    assert table(app).loc['STN-101', 'Действующая сумма, ₽'] == 'Требуется проверка'
    assert table(app).loc['STN-101', 'Срок поставки'] == 'Требуется проверка'
    assert app.metric[0].value == 'Требуется проверка'
    app.checkbox(key='filter_enabled').check().run()
    assert list(table(app).index) == ['STN-102']
    assert any('STN-101' in x.value and 'не включены' in x.value for x in app.warning)


def test_ambiguous_amount_is_not_presented_as_confirmed(uploaded_files):
    files = [(name, data + '\nОбщая стоимость договора составляет 999 000 рублей.'.encode(), mime)
             if name == '02_amendment_signed.txt' else (name, data, mime)
             for name, data, mime in uploaded_files]
    app = start(files)
    assert table(app).loc['STN-101', 'Действующая сумма, ₽'] == 'Требуется проверка'
    assert table(app).loc['STN-101', 'Срок поставки'] == '30.04.2026'


def test_failed_upload_clears_prior_results_and_allows_repair(uploaded_files):
    app = start(uploaded_files)
    app.file_uploader[0].set_value(uploaded_files + [('broken.txt', b'\xff\xfe\x00', 'text/plain')]).run()
    assert not app.exception
    assert not app.dataframe and not app.metric
    assert any('комплект обработан не полностью' in x.value for x in app.error)
    assert 'broken.txt' in text_values(app)
    app.file_uploader[0].set_value(uploaded_files).run()
    assert not app.exception and len(table(app)) == 3
    app.file_uploader[0].clear().run()
    assert not app.dataframe


def test_unknown_document_exposes_grouping_problem(uploaded_files):
    app = start(uploaded_files + [('unknown.txt', 'Неизвестный документ без номера'.encode(), 'text/plain')])
    assert any(x.label == 'Документы с проблемами группировки (1)' for x in app.expander)
    assert 'unknown.txt' in text_values(app)
    assert any('Не все документы' in x.value for x in app.warning)


def test_click_source_opens_full_document_at_exact_evidence(uploaded_files):
    app = start(uploaded_files)
    app.button(key='current_STN-101_total_amount_0').click().run()
    assert not app.exception
    frames = app.get('iframe')
    assert len(frames) == 1
    source = frames[0].proto.srcdoc
    assert '1 380 000</mark>' in source
    assert 'scrollIntoView' in source
    assert 'ДОПОЛНИТЕЛЬНОЕ СОГЛАШЕНИЕ' in source
    app.button(key='close_source').click().run()
    assert not app.exception


def test_wrong_encoding_can_be_corrected_in_ui(uploaded_files):
    name, data, mime = uploaded_files[0]
    app = start([(name, data.decode('utf-8').encode('cp1251'), mime)])
    assert not app.error and not app.selectbox
    app.file_uploader[0].set_value([
        (name, data, mime), ('short.txt', 'Привет'.encode('cp1251'), mime),
    ]).run()
    assert app.error and not app.dataframe
    assert len(app.selectbox) == 1 and len(app.code) >= 2
    app.selectbox[0].select('cp1251').run()
    assert not app.exception and not app.error
    assert table(app).loc['STN-101', 'Действующая сумма, ₽'] == '1 200 000 ₽'


def test_clear_batch_button_removes_files_and_previous_results(uploaded_files):
    app = start(uploaded_files)
    app.button(key='clear_uploads').click().run()
    assert not app.exception
    assert not app.dataframe
    assert app.file_uploader[0].value == []
    assert not any(b.key == 'clear_uploads' for b in app.button)


def test_table_selection_survives_date_filter_and_rerun(uploaded_files):
    app = start(uploaded_files)
    assert app.session_state['selected_contract'] == 'STN-101'
    assert not app.selectbox
    select_contract(app, 'STN-102')
    app.date_input(key='as_of').set_value(date(2026, 2, 28)).run()
    assert app.session_state['selected_contract'] == 'STN-102'
    assert app.metric[0].value == '840 000 ₽'
    app.checkbox(key='filter_enabled').check().run()
    app.date_input(key='before').set_value(date(2026, 4, 1)).run()
    assert app.session_state['selected_contract'] == 'STN-102'
    app.checkbox(key='filter_enabled').uncheck().run()
    app.run()
    assert app.session_state['selected_contract'] == 'STN-102'
    assert len(app.session_state['active_uploads']) == 6
    assert len(table(app)) == 3


def test_clear_then_example_starts_with_first_row(uploaded_files):
    app = start(uploaded_files)
    select_contract(app, 'STN-103')
    app.button(key='clear_uploads').click().run()
    app.button(key='open_example').click().run()
    assert not app.exception
    assert app.session_state['selected_contract'] == 'STN-101'


def test_example_reads_actual_bytes_and_can_be_replaced(monkeypatch, uploaded_files):
    original = Path.read_bytes
    def changed(path):
        data = original(path)
        return data.replace(b'1 380 000', b'1 390 000') if path.name == '02_amendment_signed.txt' else data
    monkeypatch.setattr(Path, 'read_bytes', changed)
    app = start()
    app.button(key='open_example').click().run()
    assert not app.exception
    assert table(app).loc['STN-101', 'Действующая сумма, ₽'] == '1 390 000 ₽'
    select_contract(app, 'STN-102')
    app.run()
    assert len(app.session_state['active_uploads']) == 6
    app.file_uploader[0].set_value([uploaded_files[0]]).run()
    assert len(table(app)) == 1
    assert table(app).loc['STN-101', 'Действующая сумма, ₽'] == '1 200 000 ₽'
    assert not app.session_state['example_open']


def test_mixed_encodings_need_no_controls_and_manual_choice_survives_reruns(uploaded_files):
    files = [(name, data.decode().encode('cp1251') if index % 2 else data, mime)
             for index, (name, data, mime) in enumerate(uploaded_files)]
    app = start(files)
    assert not app.selectbox and not app.error
    assert table(app).loc['STN-101', 'Действующая сумма, ₽'] == '1 380 000 ₽'
    files.append(('short.txt', 'Привет'.encode('cp1251'), 'text/plain'))
    app.file_uploader[0].set_value(files).run()
    app.selectbox[0].select('cp1251').run()
    app.run()  # manual widget was removed; durable choice must remain
    app.date_input(key='as_of').set_value(date(2026, 2, 28)).run()
    select_contract(app, 'STN-102')
    assert not app.error and not app.selectbox
    assert len(app.session_state['encoding_overrides']) == 1
    app.file_uploader[0].set_value(files[:-1] + [('short.txt', 'Пока'.encode('cp1251'), 'text/plain')]).run()
    assert app.error and not app.dataframe  # same filename does not reuse old choice
    assert not app.session_state['encoding_overrides']


def test_details_are_collapsed_but_current_sources_are_one_click(uploaded_files):
    app = start(uploaded_files)
    assert all(not x.proto.expanded for x in app.expander)
    assert len([b for b in app.button if b.label == 'Проверить в документе']) == 2
    select_contract(app, 'STN-103')
    app.button(key='current_STN-103_total_amount_0').click().run()
    assert not app.exception
    assert 'не входит' in app.get('iframe')[0].proto.srcdoc


def test_unexpected_error_is_human_readable_and_blocks_results(monkeypatch, uploaded_files):
    def fail(_):
        raise RuntimeError('SECRET technical detail')
    monkeypatch.setattr('contract_review.presentation.extract_facts', fail)
    app = start(uploaded_files)
    assert app.error and not app.dataframe and not app.metric
    messages = '\n'.join(x.value for x in [*app.error, *app.caption, *app.text])
    assert 'SECRET' not in messages and 'RuntimeError' not in messages
    assert 'загрузите' in messages
