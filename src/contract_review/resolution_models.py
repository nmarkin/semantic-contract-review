"""Public, immutable resolution results with original candidates and evidence."""

from dataclasses import dataclass
from datetime import date
from enum import Enum

from .models import Evidence, ExtractionResult, FactCandidate, FactValue, Field, Role


class ResolutionState(str, Enum):
    RESOLVED = "resolved"
    MISSING = "missing"
    REQUIRES_REVIEW = "requires_review"


class Reason(str, Enum):
    UNSIGNED_DRAFT = "unsigned_draft"
    SUPPLIER_LETTER = "supplier_letter"
    NOT_EFFECTIVE_YET = "not_effective_yet"
    SIGNED_AFTER_AS_OF = "signed_after_as_of"
    UNCONFIRMED_APPLICABILITY = "unconfirmed_applicability"
    SUPPLIER_CONFLICT = "supplier_conflict"
    AMBIGUOUS_FIELD = "ambiguous_field"
    SAME_DATE_CONFLICT = "same_date_conflict"
    NON_OPERATIVE_ROLE = "non_operative_role"
    NO_CONTRACT_REFERENCE = "no_contract_reference"
    AMBIGUOUS_CONTRACT_REFERENCE = "ambiguous_contract_reference"
    MISSING_BASE = "missing_base"
    MULTIPLE_BASES = "multiple_bases"


@dataclass(frozen=True)
class ResolutionIssue:
    reason: Reason
    message: str
    document_ids: tuple[str, ...] = ()
    fields: tuple[Field, ...] = ()
    evidence: tuple[Evidence, ...] = ()


@dataclass(frozen=True)
class AppliedChange:
    # None on the baseline means no explicit effective date was given.
    effective_on: date | None
    sources: tuple[FactCandidate, ...]
    previous_sources: tuple[FactCandidate, ...] = ()
    initial: bool = False


@dataclass(frozen=True)
class ResolvedField:
    field: Field
    state: ResolutionState
    sources: tuple[FactCandidate, ...] = ()
    history: tuple[AppliedChange, ...] = ()
    issues: tuple[ResolutionIssue, ...] = ()
    explanations: tuple[Evidence, ...] = ()

    @property
    def value(self) -> FactValue | None:
        """No current value is presented as confirmed while review is needed."""
        return self.sources[0].value if self.state == ResolutionState.RESOLVED else None


@dataclass(frozen=True)
class NotApplied:
    extraction: ExtractionResult
    reason: Reason
    fields: tuple[Field, ...]
    issues: tuple[ResolutionIssue, ...]
    candidates: tuple[FactCandidate, ...] = ()


@dataclass(frozen=True)
class GroupingProblem:
    extraction: ExtractionResult
    referenced_contracts: tuple[str, ...]
    issue: ResolutionIssue


@dataclass(frozen=True)
class ResolvedContract:
    contract_number: str
    fields: tuple[ResolvedField, ...]
    documents: tuple[ExtractionResult, ...]
    not_applied: tuple[NotApplied, ...] = ()
    warnings: tuple[ResolutionIssue, ...] = ()
    as_of: date | None = None

    def get(self, field: Field) -> ResolvedField:
        return next(item for item in self.fields if item.field == field)

    @property
    def proposals(self) -> tuple[NotApplied, ...]:
        return tuple(item for item in self.not_applied if (
            item.reason in (Reason.UNSIGNED_DRAFT, Reason.SUPPLIER_LETTER)
            or (item.reason == Reason.NON_OPERATIVE_ROLE
                and any(c.role == Role.PROPOSAL for c in item.candidates))
        ))


@dataclass(frozen=True)
class ResolutionReport:
    as_of: date
    contracts: tuple[ResolvedContract, ...]
    unlinked_documents: tuple[GroupingProblem, ...] = ()

    def get(self, contract_number: str) -> ResolvedContract:
        return next(item for item in self.contracts if item.contract_number == contract_number)
