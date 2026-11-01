"""Conservative rule-based candidates, without resolving currently effective terms."""

import re
from dataclasses import replace
from decimal import Decimal

from .change_intent import NEGATION, PARTIAL_PRICE_SCOPE, change_fields, relative_price_change

from .models import (
    DocumentKind, ExtractionResult, FactCandidate, Field, ParsedDocument, Role,
)
from .patterns import DATE, DATE_RE, FLAGS, clauses, evidence, lines, parse_date, result

SUPPLIER = re.compile(r"\bПоставщик[ \t]*:[ \t]*(?=(?P<value>[^\r\n]+))", FLAGS)
SENDER = re.compile(r"^[ \t]*От[ \t]*:[ \t]*(?P<value>[^\r\n]+)", FLAGS)
TOTAL = re.compile(
    r"\b(?:общ(?:ая|ую)\s+стоимость(?:\s+(?:поставки|договора))?"
    r"|итогов(?:ая|ую)\s+стоимость\s+(?:договора|поставки)"
    r"|(?:стоимость|цена)\s+договора)\b", FLAGS,
)
AMOUNT = re.compile(
    r"(?<![\d.,])(?P<value>(?:\d{1,3}(?:[ \u00a0\u202f]\d{3})+|\d+)(?:[,.]\d{1,2})?)"
    r"(?![\d.,])\s*(?:\([^()\r\n]*\)\s*)?(?P<unit>руб(?:лей|ля|ль)?\b\.?|₽)", FLAGS,
)
AMOUNT_LINK = re.compile(r"^\s*(?:составляет|равна|установлена\s+в\s+размере|на|:|—|-)\s*", FLAGS)
MONEY_DISTRACTOR = re.compile(r"\b(?:аванс\w*|лимит\w*|бюджет\w*|неустойк\w*|пен[ия]\w*)\b", FLAGS)
DELIVERY = re.compile(
    r"\b(?:срок\s+поставки|поставить\b|поставка\b[^.;\r\n]*?осуществляется\b)", FLAGS,
)
TRANSFER = re.compile(r"\s+с\s+(?P<old>" + DATE + r")\s+на\s+(?P<new>" + DATE + r")", FLAGS)
DEADLINE = re.compile(r"(?:\bне\s+позднее\b|\bдо\b|\bна\b|\bили\b|\bлибо\b|:|—|-)\s*(?P<value>" + DATE + r")", FLAGS)
PROPOSAL = re.compile(r"\b(?:предлага\w*|просим|планиру\w*|возможно|если)\b", FLAGS)
UNCHANGED = re.compile(r'\bне\s+измен\w*\b|\b(?:остается|остаются)\s+без\s+изменений\b', FLAGS)
INITIAL_REFERENCE = re.compile(
    r'^\s*Первоначальн\w*\s+(?:стоимость\s+договора|цена\s+договора|срок\s+поставки)'
    r'\s*(?:составля\w*|:|—|-)', FLAGS)


def _role(document: ParsedDocument, clause: str) -> Role:
    if PROPOSAL.search(clause):
        return Role.PROPOSAL
    if document.classification.kind != DocumentKind.CONTRACT and INITIAL_REFERENCE.search(clause):
        return Role.REFERENCED_TERM
    return {
        DocumentKind.CONTRACT: Role.ORIGINAL_TERM,
        DocumentKind.SIGNED_AMENDMENT: Role.AMENDMENT_TERM,
        DocumentKind.UNSIGNED_DRAFT: Role.PROPOSAL,
        DocumentKind.SUPPLIER_LETTER: Role.PROPOSAL,
    }.get(document.classification.kind, Role.UNCERTAIN)


def _supplier_end(raw: str) -> int:
    # Quoted organization names can contain punctuation, including full stops.
    quote = re.match(r'[^«"\r\n]*?(?:«[^»]+»|"[^"]+")', raw)
    if quote:
        return quote.end()
    return len(re.split(r"[.;\r\n]", raw, maxsplit=1)[0].rstrip())


def extract_facts(document: ParsedDocument) -> ExtractionResult:
    candidates = {field: [] for field in (Field.SUPPLIER, Field.TOTAL_AMOUNT, Field.DELIVERY_DEADLINE)}
    issues = {field: [] for field in candidates}
    explanations = {field: [] for field in candidates}

    def add(field, value, paragraph, start, end, role, unit=None):
        candidates[field].append(FactCandidate(
            field, value, evidence(document.id, paragraph, start, end),
            evidence(document.id, paragraph), role, unit,
        ))

    def add_date(paragraph, offset, match, group, role):
        value = parse_date(match[group])
        if value is None:
            issues[Field.DELIVERY_DEADLINE].append("Некорректная календарная дата поставки")
        else:
            add(Field.DELIVERY_DEADLINE, value, paragraph, offset + match.start(group),
                offset + match.end(group), role)

    for paragraph in document.paragraphs:
        for offset, line in lines(paragraph):
            matches = list(SUPPLIER.finditer(line))
            # A sender is a supplier only in an explicitly classified supplier letter.
            if document.classification.kind == DocumentKind.SUPPLIER_LETTER:
                sender = SENDER.match(line)
                if sender:
                    matches.append(sender)
            for match in matches:
                raw = match['value']
                length = _supplier_end(raw)
                value = raw[:length].strip()
                if not value or re.fullmatch(r"не\s+указан\w*|не\s+определ[её]н\w*|[-—?]+", value, FLAGS):
                    continue
                if re.search(r"\b(?:или|либо)\b", raw[:length], FLAGS):
                    issues[Field.SUPPLIER].append("Неоднозначное наименование поставщика")
                    continue
                # Alternatives after the first quoted name cannot be silently dropped.
                if re.match(r"\s*(?:или|либо)\b", raw[length:], FLAGS):
                    issues[Field.SUPPLIER].append("Указаны альтернативные поставщики")
                    continue
                start = offset + match.start('value') + len(raw[:length]) - len(raw[:length].lstrip())
                add(Field.SUPPLIER, value, paragraph, start, start + len(value), Role.METADATA)

        for offset, clause in clauses(paragraph):
            role = _role(document, clause)
            total = TOTAL.search(clause)
            if total and not relative_price_change(clause):
                prefix = clause[:total.start()]
                tail = clause[total.end():]
                if MONEY_DISTRACTOR.search(prefix) or NEGATION.search(prefix) or PARTIAL_PRICE_SCOPE.match(tail):
                    pass
                elif UNCHANGED.search(tail) or re.search(r"\bне\s+(?:является|составляет|согласована|установлена|определена)\b", clause, FLAGS):
                    pass
                elif re.search(r"\b(?:определяется\s+спецификацией|не\s+указан\w*)\b", tail, FLAGS):
                    proof = evidence(document.id, paragraph)
                    if proof not in explanations[Field.TOTAL_AMOUNT]:
                        explanations[Field.TOTAL_AMOUNT].append(proof)
                else:
                    link = AMOUNT_LINK.match(tail)
                    if link:
                        remainder = tail[link.end():]
                        amounts = list(AMOUNT.finditer(remainder))
                        first = amounts[0] if amounts else None
                        if first and not remainder[:first.start()].strip():
                            previous_end = 0
                            for index, match in enumerate(amounts):
                                between = remainder[:match.start()]
                                if MONEY_DISTRACTOR.search(between):
                                    continue
                                if index:
                                    gap = remainder[previous_end:match.start()]
                                    if re.search(r"\b(?:НДС|налог\w*|в\s+том\s+числе|из\s+них)\b", gap, FLAGS):
                                        continue
                                    if not re.fullmatch(r"\s*(?:или|либо)\s*", gap, FLAGS):
                                        issues[Field.TOTAL_AMOUNT].append("Дополнительная сумма не связана однозначно с итоговой стоимостью")
                                        continue
                                raw = match['value']
                                value = Decimal(re.sub(r"[ \u00a0\u202f]", "", raw).replace(",", "."))
                                start = offset + total.end() + link.end() + match.start('value')
                                add(Field.TOTAL_AMOUNT, value, paragraph, start, start + len(raw), role, "RUB")
                                previous_end = match.end()
                        else:
                            issues[Field.TOTAL_AMOUNT].append("Стоимость обозначена, но число и валюта не распознаны однозначно")
                    elif re.search(r"\d|\b(?:составляет|равна)\b", tail, FLAGS):
                        issues[Field.TOTAL_AMOUNT].append("Неподдерживаемая формулировка стоимости")

            delivery = DELIVERY.search(clause)
            if not delivery:
                continue
            prefix = clause[:delivery.start()]
            tail = clause[delivery.end():]
            # Only dates in a delivery predicate are candidates. Dates in a payment
            # or acceptance predicate cannot be recovered by searching the paragraph.
            if re.search(r"\b(?:оплат\w*|приемк\w*|приёмк\w*|неустойк\w*)\b", prefix, FLAGS):
                continue
            if re.search(r"\bне\s+(?:обязуется\s+)?$", prefix, FLAGS):
                issues[Field.DELIVERY_DEADLINE].append("Обязательство поставки отрицается")
                continue
            if UNCHANGED.search(tail) or re.search(r"\b(?:не\s+измен\w*|не\s+является|не\s+согласован\w*|не\s+установлен\w*)\b", clause, FLAGS):
                continue
            transfer = TRANSFER.match(tail)
            if transfer:
                add_date(paragraph, offset + delivery.end(), transfer, 'old', Role.REFERENCED_TERM)
                add_date(paragraph, offset + delivery.end(), transfer, 'new', role)
                continue
            matches = list(DEADLINE.finditer(tail))
            accepted = False
            for match in matches:
                between = tail[:match.start()]
                if re.search(r"\b(?:оплат\w*|при[её]мк\w*|вступ\w*|аванс\w*)\b", between, FLAGS):
                    continue
                if re.search(r"\bне\s*$", between, FLAGS):
                    issues[Field.DELIVERY_DEADLINE].append("Указанный срок отрицается")
                    continue
                add_date(paragraph, offset + delivery.end(), match, 'value', role)
                accepted = True
            if not accepted and not re.search(r"\b(?:не\s+указан\w*|не\s+определ[её]н\w*)\b", tail, FLAGS):
                if (DATE_RE.search(tail) or re.search(r"\b(?:дн\w*|недел\w*|месяц\w*)\b", tail, FLAGS)
                        or (delivery.group().lower() == "срок поставки" and tail.strip(" .:\r\n"))):
                    issues[Field.DELIVERY_DEADLINE].append("Срок поставки указан в неподдерживаемой или неоднозначной форме")

    # Intent and value are separate: an unsupported operative change must not
    # silently preserve an older confirmed term. Applicability stays in Resolver.
    for paragraph in document.paragraphs:
        for offset, clause in clauses(paragraph):
            if _role(document, clause) not in (Role.AMENDMENT_TERM, Role.UNCERTAIN):
                continue
            for field in change_fields(clause):
                found = any(c.role not in (Role.PROPOSAL, Role.REFERENCED_TERM)
                            and paragraph.start + offset <= c.evidence.start
                            and c.evidence.end <= paragraph.start + offset + len(clause)
                            for c in candidates[field])
                if not found or (field == Field.TOTAL_AMOUNT and relative_price_change(clause)):
                    issues[field].append('Обнаружено изменение условия, но новое значение не удалось однозначно обработать')
                    explanations[field].append(evidence(document.id, paragraph, offset, offset + len(clause)))

    extracted = {field: result(field, candidates[field], issues[field]) for field in candidates}
    for field, proofs in explanations.items():
        if proofs:
            extracted[field] = replace(extracted[field], explanations=tuple(proofs))
    extracted.update({item.field: item for item in document.metadata})
    output = ExtractionResult(document, tuple(extracted[field] for field in Field))
    output.validate_evidence()
    return output
