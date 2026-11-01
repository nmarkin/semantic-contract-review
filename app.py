"""Russian Streamlit UI. Run: python -m streamlit run app.py."""

from datetime import date
from pathlib import Path

import streamlit as st

from contract_review.models import Evidence, Field, ParsedDocument
from contract_review.encoding import ENCODING_LABELS
from contract_review.presentation import (
    FIELD_LABELS, REASON_LABELS, STATE_LABELS, Upload,
    attention_items, document_view_html, format_field, format_value, SessionProcessor,
    report_summary, selection_for, source_label,
)
from contract_review.resolution_models import ResolutionIssue, ResolutionState, ResolvedContract
from contract_review.resolver import filter_delivery_before
from contract_review.proposal_view import (
    DocumentCard, change_lines, document_cards, grouped_source_html, source_groups,
)
from contract_review.insights import contract_insights
from contract_review.report import html_report
from contract_review.table_view import styled_contracts
from contract_review.status_context import issue_description, superseding_changes


@st.dialog("Исходный документ", width="large")
def show_document(document: ParsedDocument, proof: Evidence) -> None:
    st.text(document.source_name)
    st.caption(f"Версия {document.id[:8]} · Абзац {proof.paragraph_id + 1} · символы {proof.start}–{proof.end}. Выбранный фрагмент выделен.")
    # Only our escaped, offset-based HTML and a fixed scroll script reach iframe.
    st.iframe(document_view_html(document, proof), height=540, alt="Исходный TXT с выделенным источником")
    if st.button("Закрыть документ", key="close_source"):
        st.rerun()


def render_issue(issue: ResolutionIssue, documents: dict[str, ParsedDocument], key: str) -> None:
    st.text(f"{REASON_LABELS[issue.reason]}: {issue.message}")
    if issue.evidence:
        with st.expander("Основания предупреждения"):
            render_grouped_sources(issue.evidence, documents, key)


def render_grouped_sources(proofs, documents: dict[str, ParsedDocument], key: str) -> None:
    for index, group in enumerate(source_groups(proofs, documents)):
        document = documents[group.context.document_id]
        st.text(source_label(document, documents))
        st.caption(f'Абзац {group.context.paragraph_id + 1}')
        st.html(grouped_source_html(group, document))
        if st.button('Показать в документе', key=f'{key}_{index}'):
            show_document(document, group.context)


def render_card(card: DocumentCard, documents: dict[str, ParsedDocument], key: str,
                shared_sources=None, shown_sources=None) -> None:
    with st.container(border=True, key=key):
        st.text(card.title)
        st.caption(card.explanation)
        if not card.changes:
            st.text('Однозначные значения изменения не извлечены.')
        for change in card.changes:
            st.text('\n'.join(change_lines(change)))
        for index, group in enumerate(card.sources):
            source_key = (group.context.document_id, group.context.paragraph_id)
            if shown_sources is not None and source_key in shown_sources:
                st.caption('Общий исходный абзац приведен в предыдущей карточке.')
                if st.button('Показать в документе', key=f'{key}_shared_source_{index}'):
                    show_document(documents[source_key[0]], group.context)
            else:
                selected = shared_sources[source_key] if shared_sources else group
                render_grouped_sources(selected.proofs, documents, key + f'_source' + (f'_{index}' if index else ''))
                if shown_sources is not None:
                    shown_sources.add(source_key)
        shown = {(s.context.document_id, s.context.paragraph_id) for s in card.sources}
        status = (*card.status_evidence, *(p for item in card.items for issue in item.issues for p in issue.evidence))
        extra = tuple(dict.fromkeys(p for p in status
                                    if (p.document_id, p.paragraph_id) not in shown))
        if extra:
            with st.expander('Дополнительные основания статуса'):
                render_grouped_sources(extra, documents, key + '_metadata')


def render_contract(contract: ResolvedContract, documents: dict[str, ParsedDocument]) -> None:
    st.subheader(f"Договор {contract.contract_number}")
    st.text(f"Поставщик: {format_field(contract.get(Field.SUPPLIER))}")
    left, right = st.columns(2)
    for column, field in zip((left, right), (Field.TOTAL_AMOUNT, Field.DELIVERY_DEADLINE)):
        resolved = contract.get(field)
        with column.container(border=True, key=f'term_{field.value}_{resolved.state.value}'):
            st.metric(FIELD_LABELS[field], format_field(resolved))
            st.caption(STATE_LABELS[resolved.state])
            proof = None
            if resolved.sources:
                proof = resolved.sources[0].evidence
                if resolved.history and not resolved.history[-1].initial:
                    st.caption("Изменено соглашением с " + format_value(resolved.history[-1].effective_on))
                else:
                    st.caption("Исходное условие договора.")
                st.text(source_label(documents[proof.document_id], documents))
            elif resolved.state == ResolutionState.MISSING:
                proof = next(iter(resolved.explanations), None)
                st.caption("Значение отсутствует в комплекте." +
                           (" Пояснение можно проверить в источнике." if proof else ""))
            else:
                st.caption(resolved.issues[0].message if resolved.issues else "Не удалось подтвердить текущее условие.")
                proof = next((p for issue in resolved.issues for p in issue.evidence), None)
            if proof and st.button("Проверить в документе", key=f'current_{contract.contract_number}_{field.value}_0',
                                   width='stretch'):
                show_document(documents[proof.document_id], proof)

    insights = contract_insights(contract)
    if insights:
        with st.container(border=True, key='insights'):
            st.markdown('**Основные изменения и действия**')
            for insight in insights:
                st.text(insight.text)

    with st.expander("Цитаты и основания"):
        proofs = []
        field_issues = []
        for field in contract.fields:
            if field.sources:
                proofs.extend(c.evidence for c in field.sources)
            elif field.state == ResolutionState.MISSING:
                st.text(FIELD_LABELS[field.field] + ': нет данных в предоставленном комплекте.')
            else:
                st.text(FIELD_LABELS[field.field] + ': текущее значение не подтверждено; прежние условия доступны в истории.')
            proofs.extend(field.explanations)
            field_issues.extend(field.issues)
        render_grouped_sources(proofs, documents, f'citation_{contract.contract_number}')

    with st.expander("История примененных изменений"):
        st.caption("Исторические значения не следует считать текущими. Текущие условия показаны выше.")
        history_proofs = []
        for field in contract.fields:
            st.text(FIELD_LABELS[field.field])
            if not field.history:
                st.caption("Подтвержденной истории нет.")
            for step_index, change in enumerate(field.history):
                label = "Исходное условие" if change.initial else "Примененное изменение"
                when = format_value(change.effective_on) if change.effective_on else "отдельная дата вступления в силу не указана"
                st.text(f"{label} · {when}")
                if change.previous_sources:
                    st.caption("Предыдущее значение: " + format_value(change.previous_sources[0].value))
                for value in dict.fromkeys(c.value for c in change.sources):
                    st.text("Историческое значение: " + format_value(value))
                history_proofs.extend(c.evidence for c in change.sources)
        with st.expander('Источники истории'):
            render_grouped_sources(history_proofs, documents, f'history_{contract.contract_number}')
        for index, issue in enumerate(contract.warnings):
            changes = superseding_changes(contract, issue)
            if changes:
                st.text(issue_description(contract, issue))
                with st.expander('Источники исторической неопределенности'):
                    proofs = (*issue.evidence, *(c.evidence for step in changes for c in step.sources))
                    render_grouped_sources(proofs, documents, f'historical_issue_{contract.contract_number}_{index}')

    proposals = document_cards(contract, contract.proposals)
    if proposals:
        with st.container(key='proposals'), st.expander(f"Несогласованные изменения ({len(proposals)})"):
            st.caption(f"Предложения по договору {contract.contract_number} не применены к действующим условиям.")
            shared = {(g.context.document_id, g.context.paragraph_id): g for g in source_groups(
                (p for card in proposals for group in card.sources for p in group.proofs), documents)}
            shown = set()
            for index, card in enumerate(proposals):
                render_card(card, documents, f'proposal_{contract.contract_number}_{index}', shared, shown)
    else:
        with st.expander("Несогласованные изменения (0)", expanded=False):
            st.caption("Несогласованных изменений не обнаружено")
    remaining = tuple(item for item in contract.not_applied if item not in contract.proposals)
    if remaining:
        with st.expander("Другие непримененные условия и документы"):
            for index, card in enumerate(document_cards(contract, remaining)):
                render_card(card, documents, f'not_applied_{contract.contract_number}_{index}')
    covered = {issue for item in contract.not_applied for issue in item.issues}
    warnings = tuple(dict.fromkeys(i for i in (*contract.warnings, *field_issues)
                                   if i not in covered and not superseding_changes(contract, i)))
    with st.expander(f"Другие предупреждения и причины проверки ({len(warnings)})"):
        if not warnings:
            st.caption("Дополнительных предупреждений нет; основания неприменения указаны в карточках выше.")
        for index, issue in enumerate(warnings):
            render_issue(issue, documents, f'warning_{contract.contract_number}_{index}')


def clear_uploads() -> None:
    # A new uploader instance clears its files using Streamlit's widget lifecycle.
    st.session_state['upload_generation'] = st.session_state.get('upload_generation', 0) + 1
    st.session_state['active_uploads'] = ()
    st.session_state['encoding_overrides'] = {}
    st.session_state.pop('processor', None)
    st.session_state.pop('html_report', None)
    st.session_state['example_open'] = False
    st.session_state.pop('selected_contract', None)
    st.session_state.pop('visible_contracts', None)
    st.session_state.pop('contracts_table', None)
    st.session_state.pop('example_error', None)


def receive_uploads(widget_key: str) -> None:
    uploads = tuple(Upload(file.name, file.getvalue()) for file in st.session_state.get(widget_key, []))
    st.session_state['active_uploads'] = uploads
    st.session_state['example_open'] = False
    st.session_state['encoding_overrides'] = {key: value for key, value in st.session_state.get('encoding_overrides', {}).items()
                                             if key in {file.id for file in uploads}}


def open_example() -> None:
    try:
        paths = sorted((Path(__file__).resolve().parent / 'documents').glob('*.txt'))
        uploads = tuple(Upload(path.name, path.read_bytes()) for path in paths)
        if not uploads:
            raise ValueError('empty example')
    except (OSError, ValueError):
        st.session_state['example_error'] = 'Не удалось открыть пример. Загрузите TXT через поле выбора файлов.'
        return
    clear_uploads()
    st.session_state['active_uploads'] = uploads
    st.session_state['example_open'] = True


def upload_controls() -> None:
    key = f"uploads_{st.session_state.get('upload_generation', 0)}"
    st.file_uploader("Загрузите комплект TXT-документов", type=['txt'], accept_multiple_files=True,
                     key=key, on_change=receive_uploads, args=(key,), max_upload_size=2,
                     help='До 200 файлов, каждый до 2 МиБ, весь комплект до 20 МиБ.')
    if st.session_state.get('active_uploads'):
        if st.session_state.get('example_open'):
            st.caption("Открыт учебный пример. Загрузка своих файлов заменит его.")
        else:
            st.caption("Новые файлы добавляются к загрузке. Удалите прежнюю версию перед заменой или очистите комплект.")
        st.button("Очистить комплект", key='clear_uploads', on_click=clear_uploads, type='tertiary')


def remember_encoding(upload_id: str, widget_key: str) -> None:
    overrides = dict(st.session_state.get('encoding_overrides', {}))
    choice = st.session_state.get(widget_key)
    if choice:
        overrides[upload_id] = choice
    else:
        overrides.pop(upload_id, None)
    st.session_state['encoding_overrides'] = overrides


def remember_date() -> None:
    st.session_state['review_date'] = st.session_state['as_of']


def remember_selection() -> None:
    rows = st.session_state.get('contracts_table', {}).get('selection', {}).get('rows', [])
    numbers = st.session_state.get('visible_contracts', ())
    if rows and 0 <= rows[0] < len(numbers):
        st.session_state['selected_contract'] = numbers[rows[0]]


def main() -> None:
    st.set_page_config(page_title="Проверка договоров поставки", layout="wide")
    # Stable, explicitly named app container; no generated Streamlit classes.
    accents = {'resolved': ('#288B5F', '#EEF7F1'), 'missing': ('#707B8A', '#F2F4F6'), 'requires_review': ('#B95241', '#FFF0ED')}
    rules = ''.join(f'.st-key-term_{field}_{state}{{border-top:3px solid {color};background:{surface};}}'
                    for field in ('total_amount', 'delivery_deadline') for state, (color, surface) in accents.items())
    st.html('<style>.st-key-workspace{max-width:1160px;margin:0 auto;}' + rules +
            '.st-key-proposals{border-left:3px solid #BF8023;padding-left:12px;background:#FFF6E7;border-radius:8px;}'
            '.st-key-attention{border-left:3px solid #BF8023;background:#FFF6E7;}'
            '.st-key-attention_review{border-left:3px solid #B95241;background:#FFF0ED;}'
            '.source-quote mark{background:#ffedb8;color:#252a31;}</style>')
    with st.container(key='workspace'):
        workspace()


def workspace() -> None:
    st.session_state.setdefault('review_date', date(2026, 3, 10))
    uploads = st.session_state.get('active_uploads', ())
    if not uploads:
        with st.container(width=700):
            st.title("Проверка договоров поставки")
            st.caption("Узнайте действующие суммы и сроки. Проверьте каждое условие в исходном документе.")
            st.caption("Учебный прототип. В публичное демо загружайте только учебные документы без конфиденциальных данных.")
            with st.container(border=True):
                upload_controls()
            st.button("Открыть пример", key='open_example', on_click=open_example)
            if st.session_state.get('example_error'):
                st.error(st.session_state['example_error'])
        return
    title, params, actions = st.columns([5, 2, 2], vertical_alignment='bottom')
    title.subheader("Проверка договоров")
    as_of = params.date_input("Условия действуют на дату", value=st.session_state['review_date'],
                              format='DD.MM.YYYY', key='as_of', on_change=remember_date)
    with actions.popover("Изменить документы", width='stretch'):
        upload_controls()
    if 'processor' not in st.session_state:
        st.session_state['processor'] = SessionProcessor()
    batch = st.session_state['processor'].process(uploads, as_of, st.session_state.get('encoding_overrides', {}))
    if batch.notices:
        with st.expander(f'Замечания к загрузке ({len(batch.notices)})', expanded=True):
            for notice in batch.notices:
                st.text(notice)
    if batch.errors:
        st.error("Результаты не сформированы: комплект обработан не полностью. Исправьте отмеченные файлы, чтобы продолжить.")
        for error in batch.errors:
            with st.container(border=True):
                st.text(error.name)
                st.caption(error.message)
                if error.alternatives:
                    key = f'encoding_{error.upload_id}'
                    st.selectbox("Как прочитать этот файл", list(ENCODING_LABELS), index=None,
                                 placeholder="Выберите кодировку", format_func=ENCODING_LABELS.get,
                                 key=key, on_change=remember_encoding, args=(error.upload_id, key))
                    with st.expander("Сравнить варианты прочтения", expanded=True):
                        for candidate in error.alternatives:
                            st.caption(ENCODING_LABELS[candidate.encoding])
                            st.code(candidate.preview, language=None, wrap_lines=True)
                else:
                    st.caption("Откройте «Изменить документы», удалите проблемный файл и загрузите исправленную версию.")
        return
    report = batch.report
    if report is None:
        st.info("Нет документов для проверки.")
        return
    documents = {}
    for extraction in batch.extractions:
        documents.setdefault(extraction.document.id, extraction.document)
    summary, download = st.columns([5, 1], vertical_alignment='center')
    summary.caption(report_summary(report, len(batch.extractions)))
    cached = st.session_state.get('html_report')
    if cached is None or cached[0] is not report:
        cached = (report, html_report(report).encode('utf-8'))
        st.session_state['html_report'] = cached
    download.download_button('Скачать отчет', data=cached[1], file_name=f'contract-review-{report.as_of.isoformat()}.html',
                             mime='text/html', on_click='ignore', key='download_report',
                             help='Весь комплект на дату проверки. Фильтр поставок не ограничивает отчет.')
    heading, toggle, filter_date = st.columns([5, 2, 2], vertical_alignment='bottom')
    heading.subheader("Действующие условия")
    enabled = toggle.checkbox("Фильтр поставок", key='filter_enabled')
    before = filter_date.date_input("Поставка раньше даты", value=date(as_of.year + as_of.month // 12, as_of.month % 12 + 1, 1),
                                    format='DD.MM.YYYY', key='before', disabled=not enabled)
    contracts = filter_delivery_before(report, before) if enabled else report.contracts
    if enabled:
        unconfirmed = [c.contract_number for c in report.contracts
                       if c.get(Field.DELIVERY_DEADLINE).state != ResolutionState.RESOLVED]
        st.caption("Строго раньше выбранной даты. Учитываются только подтвержденные действующие сроки.")
        if unconfirmed:
            st.warning("В подтвержденные результаты фильтра не включены договоры с отсутствующим или неподтвержденным сроком: "
                       + ', '.join(unconfirmed))
    selected = selection_for(contracts, st.session_state.get('selected_contract'))
    if contracts:
        numbers = tuple(c.contract_number for c in contracts)
        if st.session_state.get('visible_contracts') != numbers or 'contracts_table' not in st.session_state:
            st.session_state['contracts_table'] = {'selection': {'rows': [numbers.index(selected)], 'columns': [], 'cells': []}}
        st.session_state['visible_contracts'] = numbers
        event = st.dataframe(styled_contracts(contracts), hide_index=True, width='stretch', key='contracts_table',
                             on_select=remember_selection, selection_mode='single-row-required', row_height=42,
                             column_config={
                                 'Номер договора': st.column_config.TextColumn('Договор', width=120),
                                 'Поставщик': st.column_config.TextColumn('Поставщик', width=220),
                                 'Действующая сумма, ₽': st.column_config.TextColumn('Сумма', width=170),
                                 'Срок поставки': st.column_config.TextColumn('Срок', width=140),
                                 'Состояние / предупреждения': st.column_config.TextColumn('Состояние', width=330),
                             })
        selected = numbers[event.selection.rows[0]]
        st.session_state['selected_contract'] = selected
    else:
        st.info("Подходящих договоров нет. Измените дату или отключите фильтр." if report.contracts
                else "Договоры для сводной таблицы не найдены. Проверьте заголовки и ссылки в документах.")
    attention = attention_items(contracts)
    if attention or report.unlinked_documents:
        needs_review = any(f.state == ResolutionState.REQUIRES_REVIEW for c in contracts for f in c.fields)
        with st.container(border=True, key='attention_review' if needs_review else 'attention'):
            st.markdown("**Что требует внимания**")
            if attention:
                st.text('\n'.join(attention))
            if report.unlinked_documents:
                st.warning("Не все документы удалось однозначно связать с договорами.")
                with st.expander(f"Документы с проблемами группировки ({len(report.unlinked_documents)})"):
                    for index, problem in enumerate(report.unlinked_documents):
                        st.text(problem.extraction.document.source_name)
                        render_issue(problem.issue, documents, f'grouping_{index}')
                        if problem.referenced_contracts:
                            st.text("Возможные договоры: " + ', '.join(problem.referenced_contracts))
                        with st.expander("Оригинальный текст несвязанного документа"):
                            st.code(problem.extraction.document.original_text, language=None, wrap_lines=True)
    if selected:
        render_contract(next(c for c in contracts if c.contract_number == selected), documents)


if __name__ == '__main__':
    main()
