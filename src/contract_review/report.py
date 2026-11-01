"""Standalone HTML from a resolved snapshot. No parser, network, or JavaScript."""

from html import escape

from .insights import report_insights
from .models import Evidence, Field
from .presentation import FIELD_LABELS, contract_summary, format_field, format_value, source_label
from .proposal_view import change_lines, document_cards, grouped_source_html, SourceGroup
from .resolution_models import ResolutionReport
from .status_context import issue_description, superseding_changes


def html_report(report: ResolutionReport) -> str:
    documents = {e.document.id: e.document for c in report.contracts for e in c.documents}
    documents.update({p.extraction.document.id: p.extraction.document for p in report.unlinked_documents})
    sources: dict[tuple[str, int], list[Evidence]] = {}

    def links(proofs) -> str:
        result, seen = [], set()
        for proof in proofs:
            document = documents[proof.document_id]
            proof.validate(document)
            key = (proof.document_id, proof.paragraph_id)
            collected = sources.setdefault(key, [])
            if proof not in collected:
                collected.append(proof)
            if key not in seen:
                index = list(sources).index(key) + 1
                label = f'{source_label(document, documents)}, абзац {proof.paragraph_id + 1}'
                result.append(f'<a href="#source-{index}">{escape(label)}</a>')
                seen.add(key)
        return ' · '.join(result)

    def paragraph(text, proofs=()) -> str:
        references = links(proofs)
        return '<p>' + escape(text) + ('<br><small>' + references + '</small>' if references else '') + '</p>'

    body = ['<h1>Проверка договоров поставки</h1>',
            paragraph(f'Условия действуют на дату: {format_value(report.as_of)}. Обработано документов: {len(documents)}.'),
            paragraph('Отчет по всему комплекту; фильтр поставок в приложении не ограничивает экспорт.'),
            '<h2>Действующие условия</h2><table><thead><tr><th>Договор</th><th>Поставщик</th><th>Сумма</th><th>Срок</th><th>Состояние</th></tr></thead><tbody>']
    for contract in report.contracts:
        body.append('<tr><td>' + escape(contract.contract_number) + '</td>')
        for field in (Field.SUPPLIER, Field.TOTAL_AMOUNT, Field.DELIVERY_DEADLINE):
            value = contract.get(field)
            references = links([c.evidence for c in value.sources] + list(value.explanations)
                               + [p for issue in value.issues for p in issue.evidence])
            body.append(f'<td class="{escape(value.state.value)}">' + escape(format_field(value)) +
                        ('<br><small>' + references + '</small>' if references else '') + '</td>')
        body.append('<td>' + escape(contract_summary(contract)) + '</td></tr>')
    body.append('</tbody></table><h2>Основные изменения и действия</h2>')
    for insight in report_insights(report):
        body.append(paragraph(insight.contract_number + ' — ' + insight.text, insight.evidence))
    for contract in report.contracts:
        body.append('<h2>Договор ' + escape(contract.contract_number) + '</h2>')
        for label, items in (('Несогласованные предложения', contract.proposals),
                             ('Другие непримененные условия и документы', tuple(i for i in contract.not_applied if i not in contract.proposals))):
            if not items:
                continue
            body.append('<h3>' + escape(label) + '</h3>')
            for card in document_cards(contract, items):
                body.append('<section class="proposal"><h4>' + escape(card.title) + '</h4>')
                body.append(paragraph(card.explanation, card.status_evidence))
                for change in card.changes:
                    for line in change_lines(change):
                        body.append(paragraph(line))
                proofs = [p for group in card.sources for p in group.proofs]
                body.append(paragraph('Источник предложенных или непримененных условий', proofs))
                metadata = [p for item in card.items for issue in item.issues for p in issue.evidence
                            if (p.document_id, p.paragraph_id) not in {(q.document_id, q.paragraph_id) for q in proofs}]
                if metadata:
                    body.append('<details><summary>Основания статуса</summary>' + links(metadata) + '</details>')
                body.append('</section>')
        covered = {i for item in contract.not_applied for i in item.issues}
        warnings = tuple(dict.fromkeys(i for i in (*contract.warnings, *(i for f in contract.fields for i in f.issues))
                                       if i not in covered and not superseding_changes(contract, i)))
        if warnings:
            body.append('<h3>Другие предупреждения и причины проверки</h3>')
            for issue in warnings:
                body.append(paragraph(issue_description(contract, issue), issue.evidence))
        body.append('<details><summary>История примененных условий</summary>')
        body.append(paragraph('Историческое значение не обязательно является текущим. Действующие условия указаны в сводной таблице.'))
        for issue in contract.warnings:
            changes = superseding_changes(contract, issue)
            if changes:
                body.append(paragraph(issue_description(contract, issue),
                                      (*issue.evidence, *(c.evidence for step in changes for c in step.sources))))
        for field in contract.fields:
            for step in field.history:
                title = 'Исходное условие' if step.initial else 'Примененное изменение'
                value = format_value(step.sources[0].value) if step.sources else 'Нет данных'
                when = format_value(step.effective_on) if step.effective_on else 'отдельная дата не указана'
                body.append(paragraph(f'{FIELD_LABELS[field.field]} — {title}: {value}; {when}',
                                      (c.evidence for c in (*step.previous_sources, *step.sources))))
        body.append('</details>')
    if report.unlinked_documents:
        body.append('<h2>Документы без однозначной привязки</h2>')
        for item in report.unlinked_documents:
            body.append(paragraph(source_label(item.extraction.document, documents) + ': ' + item.issue.message, item.issue.evidence))
    body.append('<h2>Источники</h2>')
    for index, ((document_id, paragraph_id), proofs) in enumerate(sources.items(), 1):
        document = documents[document_id]
        source = next(p for p in document.paragraphs if p.id == paragraph_id)
        context = Evidence(document_id, paragraph_id, source.start, source.end, source.text)
        label = f'{source_label(document, documents)} · абзац {paragraph_id + 1}'
        body.append(f'<section id="source-{index}"><h3>' + escape(label) + '</h3>')
        body.append(grouped_source_html(SourceGroup(context, tuple(proofs)), document))
        positions = ', '.join(f'{p.start}–{p.end}' for p in proofs)
        body.append(paragraph('Диапазоны Evidence [start, end), в символах Python: ' + positions))
        body.append('</section>')
    body.append('<h2>Ограничения проверки</h2>' + paragraph(
        'Прототип автоматизированной сверки предоставленных TXT, не юридическое заключение. '
        'Поддерживаются ограниченные формулировки; отсутствующие приложения не восстанавливаются. '
        'Предложения не считаются действующими условиями. Неоднозначные сведения требуют проверки исходных документов.'))
    return '''<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'">
<title>Проверка договоров поставки</title><style>
body{max-width:1100px;margin:32px auto;padding:0 20px;font:16px system-ui,sans-serif;color:#252A31;line-height:1.5}
table{width:100%;border-collapse:collapse;table-layout:fixed}td,th{padding:10px;border:1px solid #dde2e6;text-align:left;overflow-wrap:anywhere}
th{background:#f2f4f6}h2{margin-top:32px}small{font-size:12px}a{color:#326b92;overflow-wrap:anywhere}
.resolved{background:#eef7f1}.missing{background:#f2f4f6}.requires_review{background:#fff0ed}
.proposal{border-left:3px solid #bf8023;padding:8px 20px;background:#fff6e7;margin:12px 0}
pre{padding:16px;border:1px solid #dde2e6;border-radius:8px}mark{background:#ffedb8;color:#252a31}
details{margin:16px 0}section{scroll-margin-top:16px}p{overflow-wrap:anywhere}
</style></head><body>''' + ''.join(body) + '</body></html>'
