"""Immutable values and character offsets into the unmodified source text."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import Enum


class State(str, Enum):
    MISSING = "missing"
    AMBIGUOUS = "ambiguous"
    CONFIDENT = "confident"


class DocumentKind(str, Enum):
    CONTRACT = "contract"
    SIGNED_AMENDMENT = "signed_amendment"
    UNSIGNED_DRAFT = "unsigned_draft"
    SUPPLIER_LETTER = "supplier_letter"
    UNKNOWN = "unknown"


class Field(str, Enum):
    CONTRACT_NUMBER = "contract_number"
    SUPPLIER = "supplier"
    TOTAL_AMOUNT = "total_amount"
    DELIVERY_DEADLINE = "delivery_deadline"
    SIGNING_STATUS = "signing_status"
    SIGNING_DATE = "signing_date"
    EFFECTIVE_DATE = "effective_date"
    DOCUMENT_DATE = "document_date"


class SigningStatus(str, Enum):
    BOTH_PARTIES = "both_parties"
    UNSIGNED = "unsigned"
    NOT_BOTH_PARTIES = "not_both_parties"
    SIGNED_UNSPECIFIED = "signed_unspecified"


class Role(str, Enum):
    ORIGINAL_TERM = "original_term"
    AMENDMENT_TERM = "amendment_term"
    PROPOSAL = "proposal"
    REFERENCED_TERM = "referenced_term"
    METADATA = "metadata"
    UNCERTAIN = "uncertain"


@dataclass(frozen=True)
class Paragraph:
    id: int
    start: int
    end: int
    text: str


@dataclass(frozen=True)
class Evidence:
    document_id: str
    paragraph_id: int
    start: int
    end: int
    quote: str

    def validate(self, document: "ParsedDocument") -> None:
        if self.document_id != document.id:
            raise ValueError("Evidence belongs to a different document")
        paragraph = next((p for p in document.paragraphs if p.id == self.paragraph_id), None)
        if paragraph is None or not paragraph.start <= self.start < self.end <= paragraph.end:
            raise ValueError("Evidence is outside its paragraph")
        if document.original_text[self.start:self.end] != self.quote:
            raise ValueError("Evidence does not match source offsets")
        local_start = self.start - paragraph.start
        local_end = self.end - paragraph.start
        if paragraph.text[local_start:local_end] != self.quote:
            raise ValueError("Evidence does not match paragraph offsets")


FactValue = str | Decimal | date | SigningStatus


@dataclass(frozen=True)
class FactCandidate:
    field: Field
    value: FactValue
    evidence: Evidence
    context: Evidence
    role: Role
    # Unit is explicit only when the source actually specifies it.
    unit: str | None = None


@dataclass(frozen=True)
class FieldResult:
    field: Field
    state: State
    candidates: tuple[FactCandidate, ...] = ()
    reasons: tuple[str, ...] = ()
    explanations: tuple[Evidence, ...] = ()


@dataclass(frozen=True)
class Classification:
    kind: DocumentKind
    state: State
    evidence: tuple[Evidence, ...] = ()
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class ParsedDocument:
    id: str
    source_name: str
    original_text: str
    paragraphs: tuple[Paragraph, ...]
    classification: Classification
    metadata: tuple[FieldResult, ...]

    def metadata_for(self, field: Field) -> FieldResult:
        return next(result for result in self.metadata if result.field == field)


@dataclass(frozen=True)
class ExtractionResult:
    document: ParsedDocument
    fields: tuple[FieldResult, ...]

    def get(self, field: Field) -> FieldResult:
        return next(result for result in self.fields if result.field == field)

    def validate_evidence(self) -> None:
        for evidence in self.document.classification.evidence:
            evidence.validate(self.document)
        for result in (*self.document.metadata, *self.fields):
            for explanation in result.explanations:
                explanation.validate(self.document)
            for candidate in result.candidates:
                candidate.evidence.validate(self.document)
                candidate.context.validate(self.document)
                if candidate.context.paragraph_id != candidate.evidence.paragraph_id:
                    raise ValueError("Candidate and context use different paragraphs")
                if not (candidate.context.start <= candidate.evidence.start
                        < candidate.evidence.end <= candidate.context.end):
                    raise ValueError("Candidate is outside its context")
