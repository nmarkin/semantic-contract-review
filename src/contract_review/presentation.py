"""UI-independent upload handling, Russian labels, and offset-based source views."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from hashlib import sha256
from html import escape
import logging
import re
from typing import Iterable, Mapping

from .encoding import EncodingCandidate, detect_encoding
from .extractor import extract_facts
from .models import Evidence, ExtractionResult, FactValue, Field, ParsedDocument, Role
from .parser import parse_txt_bytes
from .resolver import resolve_contracts
from .resolution_models import Reason, ResolutionReport, ResolutionState, ResolvedContract, ResolvedField
from .status_context import future_notice, superseding_changes

FIELD_LABELS = {
    Field.SUPPLIER: "Поставщик", Field.TOTAL_AMOUNT: "Действующая сумма, ₽",
    Field.DELIVERY_DEADLINE: "Срок поставки",
}
STATE_LABELS = {
    ResolutionState.RESOLVED: "Подтверждено",
    ResolutionState.MISSING: "Нет данных",
    ResolutionState.REQUIRES_REVIEW: "Требуется проверка",
}
REASON_LABELS = {
    Reason.UNSIGNED_DRAFT: "Неподписанный проект",
    Reason.SUPPLIER_LETTER: "Письмо с предложением",
    Reason.NOT_EFFECTIVE_YET: "Еще не вступило в силу",
    Reason.SIGNED_AFTER_AS_OF: "Подписано после даты проверки",
    Reason.UNCONFIRMED_APPLICABILITY: "Применимость не подтверждена",
    Reason.SUPPLIER_CONFLICT: "Конфликт поставщика",
    Reason.AMBIGUOUS_FIELD: "Неоднозначное значение",
    Reason.SAME_DATE_CONFLICT: "Конфликт изменений на одну дату",
    Reason.NON_OPERATIVE_ROLE: "Предложение или упоминание прежнего условия",
    Reason.NO_CONTRACT_REFERENCE: "Нет ссылки на договор",
    Reason.AMBIGUOUS_CONTRACT_REFERENCE: "Неоднозначная ссылка на договор",
    Reason.MISSING_BASE: "Основной договор отсутствует",
    Reason.MULTIPLE_BASES: "Несколько основных договоров",
}
ROLE_LABELS = {
    Role.PROPOSAL: "Предлагаемое значение",
    Role.REFERENCED_TERM: "Упомянутое прежнее значение — контекст",
    Role.AMENDMENT_TERM: "Условие соглашения",
    Role.ORIGINAL_TERM: "Исходное условие",
    Role.METADATA: "Реквизит документа",
    Role.UNCERTAIN: "Роль значения требует проверки",
}


@dataclass(frozen=True)
class Upload:
    name: str
    data: bytes

    @property
    def id(self) -> str:
        return sha256(self.name.encode('utf-8') + b'\0' + self.data).hexdigest()


@dataclass(frozen=True)
class UploadError:
    name: str
    message: str
    upload_id: str = ''
    alternatives: tuple[EncodingCandidate, ...] = ()


@dataclass(frozen=True)
class UploadResult:
    extractions: tuple[ExtractionResult, ...]
    errors: tuple[UploadError, ...]
    report: ResolutionReport | None
    notices: tuple[str, ...] = ()


MAX_FILES = 200
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_BATCH_BYTES = 20 * 1024 * 1024


def upload_limits(uploads: tuple[Upload, ...]) -> tuple[UploadError, ...]:
    errors = []
    if len(uploads) > MAX_FILES:
        errors.append(UploadError('Комплект документов', f'Можно загрузить не более {MAX_FILES} файлов. Уменьшите комплект.'))
    if sum(len(u.data) for u in uploads) > MAX_BATCH_BYTES:
        errors.append(UploadError('Комплект документов', 'Общий размер превышает 20 МиБ. Уменьшите комплект.'))
    errors.extend(UploadError(u.name, 'Файл больше 2 МиБ. Загрузите меньший TXT.', u.id)
                  for u in uploads if len(u.data) > MAX_FILE_BYTES)
    return tuple(errors)


def _extract_upload(upload: Upload, override: str | None) -> tuple[ExtractionResult | None, UploadError | None]:
    detected = detect_encoding(upload.data, override)
    if detected.encoding is None:
        alternatives = detected.alternatives or detect_encoding(upload.data).alternatives
        return None, UploadError(upload.name, detected.message, upload.id, alternatives)
    try:
        document = parse_txt_bytes(upload.data, source_name=upload.name, encoding=detected.encoding)
        return extract_facts(document), None
    except Exception:
        logging.getLogger(__name__).exception('TXT processing failed: %s', upload.name)
        return None, UploadError(upload.name, 'Не удалось обработать документ. Проверьте исходный TXT и загрузите его заново.', upload.id)


class SessionProcessor:
    """Owned by one session; caches only active files and the latest resolution.

    No global Streamlit cache, disk persistence, or references to other sessions.
    Upload identity includes name and bytes; manual encoding is a separate key.
    """

    def __init__(self) -> None:
        self._files = {}
        self._report_key = None
        self._result = None

    def process(self, uploads: Iterable[Upload], as_of: date,
                encoding_overrides: Mapping[str, str] | None = None) -> UploadResult:
        uploads = tuple(uploads)
        limits = upload_limits(uploads)
        if limits:
            self.__init__()
            return UploadResult((), limits, None)
        overrides = encoding_overrides or {}
        keys = tuple((u.id, overrides.get(u.id)) for u in uploads)
        report_key = (keys, as_of)
        if report_key == self._report_key:
            return self._result
        self._files = {key: value for key, value in self._files.items() if key in keys}
        extractions, errors, notices = [], [], []
        seen_keys, seen_documents = set(), {}
        names = {}
        for upload, key in zip(uploads, keys):
            names.setdefault(upload.name, set()).add(upload.id)
            if key in seen_keys:
                notices.append(f'Дубликат «{upload.name}» пропущен: одинаковые байты и настройки уже обработаны.')
                continue
            seen_keys.add(key)
            if key not in self._files:
                self._files[key] = _extract_upload(upload, key[1])
            extraction, error = self._files[key]
            if error:
                errors.append(error)
            elif extraction.document.id in seen_documents:
                notices.append(f'Дубликат «{upload.name}» учтен один раз вместе с «{seen_documents[extraction.document.id]}»: текст совпадает.')
            else:
                seen_documents[extraction.document.id] = upload.name
                extractions.append(extraction)
        for name, variants in names.items():
            if len(variants) > 1:
                notices.append(f'Имя «{name}» используется для разных файлов. Оба содержимых сохранены; приоритет версии не установлен.')
        report = None
        if extractions and not errors:
            try:
                report = resolve_contracts(extractions, as_of)
            except Exception:
                logging.getLogger(__name__).exception('Contract resolution failed')
                errors.append(UploadError('Комплект документов', 'Не удалось определить условия. Проверьте комплект документов и повторите загрузку.'))
        self._result = UploadResult(tuple(extractions), tuple(errors), report, tuple(notices))
        self._report_key = report_key
        return self._result


def process_uploads(uploads: Iterable[Upload], as_of: date,
                    encoding_overrides: Mapping[str, str] | None = None) -> UploadResult:
    """Never resolve or expose a partial batch as a confirmed report."""
    return SessionProcessor().process(uploads, as_of, encoding_overrides)


def source_label(document: ParsedDocument, documents: Mapping[str, ParsedDocument]) -> str:
    same_name = sum(d.source_name == document.source_name for d in documents.values()) > 1
    return document.source_name + (f' · версия {document.id[:8]}' if same_name else '')


def upload_fingerprint(uploads: Iterable[Upload], as_of: date, encoding: str) -> str:
    """Invalidate widget selections after content, names, date or encoding changes."""
    digest = sha256(f'{as_of.isoformat()}:{encoding}'.encode())
    for upload in uploads:
        for part in (upload.name.encode('utf-8'), upload.data):
            digest.update(len(part).to_bytes(8, 'big'))
            digest.update(part)
    return digest.hexdigest()


def format_value(value: FactValue | None) -> str:
    if value is None:
        return "Нет данных"
    if isinstance(value, Decimal):
        # Keep every extracted decimal digit; never round a contract amount.
        precision = max(0, -value.as_tuple().exponent) if value != value.to_integral_value() else 0
        return format(value, f',.{precision}f').replace(',', ' ').replace('.', ',') + ' ₽'
    if isinstance(value, date):
        return value.strftime('%d.%m.%Y')
    return str(value)


def format_field(field: ResolvedField) -> str:
    return format_value(field.value) if field.state == ResolutionState.RESOLVED else STATE_LABELS[field.state]


def contract_summary(contract: ResolvedContract) -> str:
    notes = []
    if any(f.state == ResolutionState.REQUIRES_REVIEW for f in contract.fields):
        notes.append("Требуется проверка")
    if any(f.state == ResolutionState.MISSING for f in contract.fields):
        notes.append('Нет данных: ' + ', '.join('сумма' if f.field == Field.TOTAL_AMOUNT else 'срок' if f.field == Field.DELIVERY_DEADLINE else 'поставщик'
                                               for f in contract.fields if f.state == ResolutionState.MISSING))
    if not notes:
        notes.append('Изменено соглашением' if any(not h.initial for f in contract.fields for h in f.history) else 'Исходные условия')
    reasons = {p.reason for p in contract.proposals}
    if Reason.UNSIGNED_DRAFT in reasons:
        notes.append('Неподписанный проект')
    if Reason.SUPPLIER_LETTER in reasons:
        notes.append('Предложено изменение письмом')
    if Reason.NON_OPERATIVE_ROLE in reasons:
        notes.append('Отдельное предложение')
    if any(item not in contract.proposals for item in contract.not_applied):
        notes.append('Есть непримененные условия')
    return " · ".join(notes) or "Подтверждено"


def selection_for(contracts: Iterable[ResolvedContract], selected: str | None) -> str | None:
    numbers = [c.contract_number for c in contracts]
    return selected if selected in numbers else next(iter(numbers), None)


def attention_items(contracts: Iterable[ResolvedContract]) -> tuple[str, ...]:
    items = []
    for contract in contracts:
        reasons = []
        for field in contract.fields:
            if field.state == ResolutionState.MISSING:
                reasons.append(missing_description(field))
            elif field.state == ResolutionState.REQUIRES_REVIEW:
                reasons.append(FIELD_LABELS[field.field] + ': ' + '; '.join(dict.fromkeys(i.message for i in field.issues)))
        if any(p.reason == Reason.UNSIGNED_DRAFT for p in contract.proposals):
            reasons.append('Проект не подписан: рассмотрите предложенные условия отдельно от действующих.')
        if any(p.reason == Reason.SUPPLIER_LETTER for p in contract.proposals):
            reasons.append('Письмо содержит предложение: согласование изменения не подтверждено.')
        if any(p.reason == Reason.NON_OPERATIVE_ROLE for p in contract.proposals):
            reasons.append('В документе есть отдельное предложение, которое не применено.')
        covered = {i for p in contract.not_applied for i in p.issues}
        reasons.extend(i.message for p in contract.not_applied if p not in contract.proposals for i in p.issues
                       if not superseding_changes(contract, i))
        reasons.extend(i.message for i in contract.warnings if i not in covered
                       and not any(i in f.issues for f in contract.fields)
                       and not superseding_changes(contract, i))
        reasons.extend(future_notice(contract, p.extraction.document) for p in contract.proposals
                       if future_notice(contract, p.extraction.document))
        if reasons:
            items.append(contract.contract_number + ' — ' + ' '.join(dict.fromkeys(reasons)))
    return tuple(items)


def missing_description(field: ResolvedField) -> str:
    label = 'Сумма договора' if field.field == Field.TOTAL_AMOUNT else FIELD_LABELS[field.field]
    text = label + ' отсутствует в предоставленных документах.'
    quotes = [p.quote for p in field.explanations if re.search(r'спецификац', p.quote, re.I)
              and re.search(r'не\s+входит|отсутствует|не\s+предоставлен', p.quote, re.I)]
    references = {m.group(1) for quote in quotes for m in re.finditer(r'приложени[еиюя]\s*№\s*(\d+(?:[-/]\d+)*)', quote, re.I)}
    if len(references) == 1:
        text += ' Для определения значения требуется приложение № ' + next(iter(references)) + '.'
    elif quotes:
        text += ' Требуется отсутствующая спецификация.'
    return text


def report_summary(report: ResolutionReport, document_count: int) -> str:
    from .proposal_view import document_cards
    proposed = sum(len(document_cards(c, c.proposals)) for c in report.contracts)
    missing = sum(f.state == ResolutionState.MISSING for c in report.contracts for f in c.fields)
    review = sum(f.state == ResolutionState.REQUIRES_REVIEW for c in report.contracts for f in c.fields)
    return (f'Документов: {document_count} · Договоров: {len(report.contracts)} · '
            f'Предложений: {proposed} · Нет данных: {missing} · Требуют проверки: {review}')


def summary_rows(contracts: Iterable[ResolvedContract]) -> list[dict[str, str]]:
    return [{"Номер договора": c.contract_number,
             **{FIELD_LABELS[f.field]: format_field(f) for f in c.fields},
             "Состояние / предупреждения": contract_summary(c)} for c in contracts]


def source_html(document: ParsedDocument, proof: Evidence, context: Evidence | None = None,
                *, full_document: bool = False) -> str:
    """Escape source slices first, then insert our own fixed highlighting markup."""
    proof.validate(document)
    if context is not None:
        context.validate(document)
        if not (context.start <= proof.start < proof.end <= context.end):
            raise ValueError("Source context does not contain evidence")
    if full_document:
        start, end = 0, len(document.original_text)
    elif context is not None:
        start, end = context.start, context.end
    else:
        paragraph = next(p for p in document.paragraphs if p.id == proof.paragraph_id)
        start, end = paragraph.start, paragraph.end
    before = escape(document.original_text[start:proof.start])
    selected = escape(document.original_text[proof.start:proof.end])
    after = escape(document.original_text[proof.end:end])
    return ('<pre style="white-space:pre-wrap;overflow-wrap:anywhere;font:inherit;line-height:1.6;">'
            + before + '<mark id="evidence-target" style="background:#ffeaa0;color:#171717;">'
            + selected + '</mark>' + after + '</pre>')


def document_view_html(document: ParsedDocument, proof: Evidence) -> str:
    """Full TXT in an isolated frame, scrolled to the exact selected source slice."""
    return ('''<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'">
<style>body{margin:20px;background:#fff;color:#222;font:16px system-ui,sans-serif}</style>
</head><body>''' + source_html(document, proof, full_document=True) + '''
<script>document.getElementById('evidence-target').scrollIntoView({block:'center'});</script>
</body></html>''')
