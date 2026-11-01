"""TXT parsing and explicit document metadata; filenames are display labels only."""

import hashlib
import re
from dataclasses import replace

from .models import (
    Classification, DocumentKind, FactCandidate, Field, Paragraph,
    ParsedDocument, Role, SigningStatus, State,
)
from .patterns import DATE, DATE_RE, FLAGS, clauses, evidence, lines, parse_date, result

CONTRACT_REFERENCE = re.compile(
    r"\bдоговор(?:а|у|ом|е)?(?:[ \t]+поставки)?[ \t]+"
    r"(?P<marker>№[ \t]*)?(?P<value>[A-ZА-ЯЁ0-9]+(?:[-/][A-ZА-ЯЁ0-9]+)*)", FLAGS,
)


def _is_contract_reference(text: str, match: re.Match[str]) -> bool:
    """Require an identification/reference context for unmarked identifiers.

    A number sign explicitly labels an identifier. Without it, a title, topic,
    or reference preposition is required; e.g. 'стоимость договора 123' is not
    an identifier even though the token contains digits.
    """
    if match['marker']:
        return True
    raw = match['value']
    if not any(char.isdigit() for char in raw):
        return False
    line_start = max(text.rfind('\n', 0, match.start()), text.rfind('\r', 0, match.start())) + 1
    prefix = text[line_start:match.start()].lstrip('\ufeff \t')
    if not prefix or re.fullmatch(r"Тема:\s*", prefix, FLAGS):
        return True
    return bool(re.search(r"\b(?:к|по|согласно|на|и)\s*$", prefix, FLAGS))


SIGNATURE_LABEL = re.compile(r"^[ \t]*Статус[ \t]*:[ \t]*(?P<value>.+)", FLAGS)
DOCUMENT_DATE = re.compile(r"^[ \t]*Дата(?:[ \t]+проекта)?[ \t]*:[ \t]*(?P<value>.+)", FLAGS)
EFFECTIVE = re.compile(r"\bвступ(?:ает|ают|ит|ят|ило|или)\s+в\s+силу\b", FLAGS)
EFFECTIVE_VALUE = re.compile(r"\s+(?:с\s+)?(?P<value>" + DATE + r")", FLAGS)


def split_paragraphs(text: str) -> tuple[Paragraph, ...]:
    """Blank lines delimit paragraphs; CRLF, BOM, spaces and newlines stay intact."""
    paragraphs = []
    start = 0
    # The lookarounds prevent regex backtracking from treating one CRLF as
    # two line breaks. Indentation of the next paragraph must not be consumed.
    newline = r"(?:\r\n|(?<!\r)\n|\r(?!\n))"
    separators = list(re.finditer(newline + r"(?:[ \t]*" + newline + r")+", text))
    for left, right in [(m.start(), m.end()) for m in separators] + [(len(text), len(text))]:
        if text[start:left].strip():
            paragraphs.append(Paragraph(len(paragraphs), start, left, text[start:left]))
        start = right
    return tuple(paragraphs)


def _signature_value(raw: str) -> tuple[SigningStatus | None, str | None]:
    # Negations and partial signatures must be checked before positive substrings.
    if re.search(r"\b(?:будет|будут|долж\w*|если|после|планир\w*|предполаг\w*|"
                 r"возможно|вероятно|неверн\w*)\b|\bне\s+подтвержд", raw, FLAGS):
        return None, "Подписание условно, предполагается или не подтверждено"
    negative = re.compile(r"\b(?:не\s+(?:был[ао]?\s+)?подписан(?:о|а|ы)?\b|"
                          r"неподписан(?:о|а|ы)?\b|подписание\s+не\s+состоялось)", FLAGS)
    negative_matches = list(negative.finditer(raw))
    positive = re.search(r"\bподписан(?:о|а|ы)?\b", negative.sub(" ", raw), FLAGS)
    if negative_matches and positive:
        return None, "Различающиеся утверждения о подписании требуют проверки"
    if negative_matches:
        tail = raw[negative_matches[0].end():]
        if re.match(r"\s+(?:обеими|поставщиком|покупателем|одной)\b", tail, FLAGS):
            return SigningStatus.NOT_BOTH_PARTIES, None
        return SigningStatus.UNSIGNED, None
    if re.search(r"\bподписан\w*\s+(?:только\s+)?одной\s+стороной", raw, FLAGS):
        return SigningStatus.NOT_BOTH_PARTIES, None
    if re.search(r"\bподписан\w*\s+обеими\s+сторонами", raw, FLAGS):
        return SigningStatus.BOTH_PARTIES, None
    if re.search(r"\bподписан(?:о|а|ы)?\b", raw, FLAGS):
        return SigningStatus.SIGNED_UNSPECIFIED, "Не указано подписание обеими сторонами"
    return None, "Статус подписания указан в неподдерживаемой форме"


def _metadata(document_id: str, paragraphs: tuple[Paragraph, ...]):
    fields = (Field.CONTRACT_NUMBER, Field.SIGNING_STATUS, Field.SIGNING_DATE,
              Field.DOCUMENT_DATE, Field.EFFECTIVE_DATE)
    candidates = {field: [] for field in fields}
    issues = {field: [] for field in fields}

    def add(field, value, paragraph, start, end):
        candidates[field].append(FactCandidate(
            field, value, evidence(document_id, paragraph, start, end),
            evidence(document_id, paragraph), Role.METADATA,
        ))

    for paragraph in paragraphs:
        for match in CONTRACT_REFERENCE.finditer(paragraph.text):
            raw = match['value']
            if _is_contract_reference(paragraph.text, match):
                add(Field.CONTRACT_NUMBER, raw, paragraph, *match.span('value'))
        for offset, line in lines(paragraph):
            signature = SIGNATURE_LABEL.match(line)
            if signature:
                value, issue = _signature_value(signature['value'])
                if value is not None:
                    add(Field.SIGNING_STATUS, value, paragraph,
                        offset + signature.start('value'), offset + signature.end('value'))
                if issue:
                    issues[Field.SIGNING_STATUS].append(issue)
                if value == SigningStatus.BOTH_PARTIES and not issue:
                    signed = re.search(r"\bподписан(?:о|а|ы)?\s+обеими\s+сторонами", signature['value'], FLAGS)
                    suffix = signature['value'][signed.end():]
                    # Only the date immediately attached to the signing statement
                    # is a signing date. A document/as-of date is not a substitute.
                    attached = re.match(r"\s+(?P<value>" + DATE + r")", suffix)
                    if attached:
                        signing_date = parse_date(attached['value'])
                        if signing_date is None:
                            issues[Field.SIGNING_DATE].append("Некорректная календарная дата подписания")
                        else:
                            start = offset + signature.start('value') + signed.end() + attached.start('value')
                            add(Field.SIGNING_DATE, signing_date, paragraph, start, start + len(attached['value']))
                        if re.match(r"\s*(?:или|либо|и)\b", suffix[attached.end():], FLAGS):
                            issues[Field.SIGNING_DATE].append("Дата подписания неоднозначна")
                    elif suffix.strip(' .\t') and not re.match(r"[.,]?\s*по\s+состоянию\b", suffix, FLAGS):
                        issues[Field.SIGNING_DATE].append("Дата или уточнение подписания в неподдерживаемой форме")
            label = DOCUMENT_DATE.match(line)
            if label:
                dates = list(DATE_RE.finditer(label['value']))
                if not dates:
                    issues[Field.DOCUMENT_DATE].append("Дата указана в неподдерживаемом формате")
                for match in dates:
                    value = parse_date(match.group())
                    if value is None:
                        issues[Field.DOCUMENT_DATE].append("Некорректная календарная дата документа")
                    else:
                        start = offset + label.start('value') + match.start()
                        add(Field.DOCUMENT_DATE, value, paragraph, start, start + len(match.group()))
        for offset, clause in clauses(paragraph):
            for anchor in EFFECTIVE.finditer(clause):
                if re.search(r"\bне\s*$", clause[:anchor.start()], FLAGS):
                    issues[Field.EFFECTIVE_DATE].append("Вступление в силу отрицается")
                    continue
                match = EFFECTIVE_VALUE.match(clause, anchor.end())
                if match:
                    value = parse_date(match['value'])
                    if DATE_RE.search(clause[match.end():]):
                        issues[Field.EFFECTIVE_DATE].append("В условии вступления в силу указано несколько дат")
                    if value is None:
                        issues[Field.EFFECTIVE_DATE].append("Некорректная дата вступления в силу")
                    elif re.search(r"\b(?:если|при\s+условии|после\s+подписания)\b", clause, FLAGS):
                        issues[Field.EFFECTIVE_DATE].append("Дата вступления в силу зависит от условия")
                    else:
                        add(Field.EFFECTIVE_DATE, value, paragraph,
                            offset + match.start('value'), offset + match.end('value'))
                elif DATE_RE.search(clause[anchor.end():]):
                    issues[Field.EFFECTIVE_DATE].append("Дата не связана однозначно со вступлением в силу")
                elif not re.match(r"\s+(?:только\s+)?после\s+подписания\b", clause[anchor.end():], FLAGS):
                    issues[Field.EFFECTIVE_DATE].append("Дата вступления в силу указана в неподдерживаемой форме")
    return tuple(result(field, candidates[field], issues[field]) for field in fields)


def _classify(document: ParsedDocument) -> Classification:
    headings = []
    for paragraph in document.paragraphs:
        for offset, raw_line in lines(paragraph):
            line = raw_line.lstrip("\ufeff \t")
            kind = None
            if re.match(r"ПРОЕКТ\s+ДОПОЛНИТЕЛЬНОГО\s+СОГЛАШЕНИЯ\b", line, FLAGS):
                kind = DocumentKind.UNSIGNED_DRAFT
            elif re.match(r"ДОПОЛНИТЕЛЬНОЕ\s+СОГЛАШЕНИЕ\b", line, FLAGS):
                kind = DocumentKind.SIGNED_AMENDMENT
            elif re.match(r"ДОГОВОР\s+ПОСТАВКИ\b", line, FLAGS):
                kind = DocumentKind.CONTRACT
            elif re.match(r"ПИСЬМО\s+ПОСТАВЩИКА\b", line, FLAGS):
                kind = DocumentKind.SUPPLIER_LETTER
            if kind:
                headings.append((kind, evidence(document.id, paragraph, offset, offset + len(raw_line))))
    kinds = {kind for kind, _ in headings}
    proof = tuple(item for _, item in headings)
    if len(kinds) != 1:
        return Classification(DocumentKind.UNKNOWN, State.AMBIGUOUS if kinds else State.MISSING,
                              proof, ("Нет единственного распознаваемого заголовка документа",))
    kind = headings[0][0]
    signature = document.metadata_for(Field.SIGNING_STATUS)
    statuses = {candidate.value for candidate in signature.candidates}
    if kind in (DocumentKind.UNSIGNED_DRAFT, DocumentKind.SIGNED_AMENDMENT):
        proof += tuple(candidate.evidence for candidate in signature.candidates)
        if signature.state == State.AMBIGUOUS:
            return Classification(DocumentKind.UNKNOWN, State.AMBIGUOUS, proof, signature.reasons)
        if kind == DocumentKind.UNSIGNED_DRAFT:
            if SigningStatus.BOTH_PARTIES in statuses:
                return Classification(DocumentKind.UNKNOWN, State.AMBIGUOUS, proof,
                                      ("Заголовок проекта противоречит статусу подписания",))
        elif statuses == {SigningStatus.BOTH_PARTIES}:
            pass
        elif SigningStatus.UNSIGNED in statuses or SigningStatus.NOT_BOTH_PARTIES in statuses:
            kind = DocumentKind.UNSIGNED_DRAFT
        else:
            return Classification(DocumentKind.UNKNOWN, State.AMBIGUOUS, proof,
                                  ("Подписание соглашения обеими сторонами не подтверждено",))
    return Classification(kind, State.CONFIDENT, proof)


def parse_document(text: str, source_name: str = "", *, document_id: str | None = None) -> ParsedDocument:
    """Offsets use Python Unicode characters, end-exclusive, never byte offsets.

    The default ID is a content hash. Callers may supply their own unique upload ID.
    Identical text has the same default ID regardless of the filename.
    """
    if not isinstance(text, str):
        raise TypeError("Expected decoded str; use parse_txt_bytes for uploads")
    document_id = document_id or hashlib.sha256(text.encode("utf-8")).hexdigest()
    paragraphs = split_paragraphs(text)
    document = ParsedDocument(document_id, source_name, text, paragraphs,
                              Classification(DocumentKind.UNKNOWN, State.MISSING),
                              _metadata(document_id, paragraphs))
    return replace(document, classification=_classify(document))


def parse_txt_bytes(data: bytes, source_name: str = "", *, encoding: str = "utf-8",
                    document_id: str | None = None) -> ParsedDocument:
    """Strict explicit decoding; no guessing, replacement characters or newline conversion.

    UTF-8 BOM is retained. A different source encoding can be explicitly selected.
    """
    return parse_document(data.decode(encoding, errors="strict"), source_name, document_id=document_id)
