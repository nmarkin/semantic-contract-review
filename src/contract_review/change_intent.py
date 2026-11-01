"""Small clause-local change detectors, independent of value extraction.

These recognize explicit changes to contract price / delivery, not arbitrary
legal language. A matched predicate is evidence of intent, never a new value.
"""

import re

from .models import Field
from .patterns import FLAGS

PRICE = (
    r'(?:(?:цен[ау]|стоимост[ьи])\s+договора|'
    r'(?:общ(?:ая|ую)|итогов(?:ая|ую))\s+стоимост[ьи](?:\s+(?:договора|поставки))?)'
)
DELIVERY = r'(?:срок\s+поставки|дат[ау]\s+завершения\s+поставки|дат[ау]\s+исполнения\s+обязательств\s+по\s+отгрузке)'
CHANGE = r'(?:измен\w*|увелич\w*|уменьш\w*|перен[ео]с\w*|согласовал\w*|установ\w*)'
NEGATION = re.compile(r'\bне\s+' + CHANGE + r'|\bбез\s+изменений\b', FLAGS)
OTHER_SUBJECT = re.compile(
    r'\b(?:аванс\w*|бюджет\w*|лимит\w*|неустойк\w*|пен[ия]\w*|'
    r'просроченн\w*|оплат\w*|при[её]мк\w*|вступ\w*)\b', FLAGS)
RELATIVE_PRICE = re.compile(
    r'\b(?:увелич\w*|уменьш\w*)\s+' + PRICE + r'\s+на\b|'
    r'\b' + PRICE + r'\s+(?:увелич\w*|уменьш\w*)\s+на\b', FLAGS)
PARTIAL_PRICE_SCOPE = re.compile(r'^\s+просроченн\w*\s+части\b', FLAGS)


def relative_price_change(clause: str) -> bool:
    return any(_operative_predicate(clause, match, price=True) for match in RELATIVE_PRICE.finditer(clause))


def _operative_predicate(clause: str, match: re.Match, *, price: bool) -> bool:
    # A trailing payment/advance clause does not negate a preceding price or
    # delivery change. Context *before* the predicate can restrict its scope.
    context = clause[:match.end()]
    return (not NEGATION.search(context) and not OTHER_SUBJECT.search(context)
            and not (price and PARTIAL_PRICE_SCOPE.match(clause[match.end():])))


def change_fields(clause: str) -> tuple[Field, ...]:
    fields = []
    for field, subject in ((Field.TOTAL_AMOUNT, PRICE), (Field.DELIVERY_DEADLINE, DELIVERY)):
        # Bound the relation to a short predicate; a remote mention is not intent.
        before = r'\b' + CHANGE + r'\s+(?:нов(?:ую|ый)\s+)?' + subject + r'\b'
        after = r'\b' + subject + r'\s+' + CHANGE + r'\b'
        if any(_operative_predicate(clause, match, price=field == Field.TOTAL_AMOUNT) for pattern in (before, after)
               for match in re.finditer(pattern, clause, FLAGS)):
            fields.append(field)
    return tuple(fields)
