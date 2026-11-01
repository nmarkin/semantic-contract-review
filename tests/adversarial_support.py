"""Read the pre-authored oracle; never derive expected values from the SUT."""
from datetime import date
from decimal import Decimal
import json
from pathlib import Path

from contract_review import parse_document, extract_facts, resolve_contracts, filter_delivery_before
from contract_review.models import Field, Role

ROOT = Path(__file__).resolve().parents[1]
QA = ROOT / 'qa/adversarial'
MATRIX = json.loads((QA / 'MATRIX.json').read_text(encoding='utf-8'))
CASES = MATRIX['cases']


def by_id(key):
    return next(c for c in CASES if c['id'] == key)


def uploads(case):
    return [(item['name'], (QA / MATRIX['documents'][item['key']]['file']).read_bytes(), 'text/plain') for item in case['documents']]


def run(case):
    extractions = [extract_facts(parse_document(data.decode('utf-8'), name)) for name, data, _ in uploads(case)]
    report = resolve_contracts(extractions, date.fromisoformat(case['as_of']))
    keys = {e.document.id: item['key'] for e, item in zip(extractions, case['documents'])}
    return report, extractions, keys


def value(raw):
    if isinstance(raw, date):
        return raw.isoformat()
    if isinstance(raw, Decimal):
        return int(raw) if raw == raw.to_integral_value() else str(raw)
    return raw


def snapshot(report, extractions, keys, number):
    contract = report.get(number)
    fields = {}
    for name in ('total_amount', 'delivery_deadline'):
        field = contract.get(Field(name))
        fields[name] = dict(value=value(field.value), state=field.state.value,
                           sources=sorted({keys[c.evidence.document_id] for c in field.sources}),
                           history=[sorted({keys[c.evidence.document_id] for c in step.sources}) for step in field.history],
                           issues=[i.reason.value for i in field.issues])
    return dict(fields=fields, proposals=sorted({keys[p.extraction.document.id] for p in contract.proposals}),
                warnings=[dict(reason=i.reason.value, message=i.message) for i in contract.warnings],
                not_applied=[dict(document=keys[p.extraction.document.id], reason=p.reason.value) for p in contract.not_applied],
                unlinked=sorted(keys[p.extraction.document.id] for p in report.unlinked_documents),
                extraction={keys[e.document.id]: {f.value: dict(state=e.get(f).state.value,
                    values=[value(c.value) for c in e.get(f).candidates], reasons=list(e.get(f).reasons))
                    for f in (Field.TOTAL_AMOUNT, Field.DELIVERY_DEADLINE)} for e in extractions})


def audit(case, report, extractions, keys):
    actual = snapshot(report, extractions, keys, case['contract'])
    errors = []
    for name, expected in case['expected'].items():
        for attribute in ('value', 'state', 'sources', 'history'):
            wanted = expected[attribute]
            if attribute == 'sources':
                wanted = sorted(wanted)
            if attribute == 'history':
                wanted = [sorted(step) for step in wanted]
            observed = actual['fields'][name][attribute]
            if observed != wanted:
                errors.append(f'{name}.{attribute}: expected {wanted!r}, actual {observed!r}')
    for attribute in ('proposals', 'unlinked'):
        if sorted(case[attribute]) != actual[attribute]:
            errors.append(f'{attribute}: expected {case[attribute]!r}, actual {actual[attribute]!r}')
    reasons = {item['reason'] for item in actual['warnings']}
    if not set(case['required_reasons']) <= reasons:
        errors.append(f'reasons: expected {case["required_reasons"]!r}, actual {sorted(reasons)!r}')
    contract = report.get(case['contract'])
    actual_applied = {keys[c.evidence.document_id] for f in contract.fields if f.field != Field.SUPPLIER
                      for step in f.history if not step.initial for c in step.sources}
    if actual_applied != set(case['applied']):
        errors.append(f'applied: expected {case["applied"]!r}, actual {sorted(actual_applied)!r}')
    documents = {e.document.id: e.document for e in extractions}
    for extraction in extractions:
        extraction.validate_evidence()
    for field in contract.fields:
        if field.state.value == 'resolved':
            assert field.sources, 'resolved without evidence'
        else:
            assert field.value is None and not field.sources, 'uncertain current value exposed'
        for candidate in field.sources:
            candidate.evidence.validate(documents[candidate.evidence.document_id])
            candidate.context.validate(documents[candidate.evidence.document_id])
        for step in field.history:
            for candidate in (*step.sources, *step.previous_sources):
                candidate.evidence.validate(documents[candidate.evidence.document_id])
                assert candidate.role not in (Role.PROPOSAL, Role.REFERENCED_TERM)
                metadata = MATRIX['documents'][keys[candidate.evidence.document_id]]
                for gate in ('signed', 'effective'):
                    assert not metadata[gate] or metadata[gate] <= case['as_of'], 'applied before allowed date'
        for issue in field.issues:
            for proof in issue.evidence:
                proof.validate(documents[proof.document_id])
    for issue in contract.warnings:
        for proof in issue.evidence:
            proof.validate(documents[proof.document_id])
    expected = case['expected']['delivery_deadline']
    for threshold in ('2026-04-15', '2026-04-26', '2026-05-01', '2031-01-01'):
        found = case['contract'] in {c.contract_number for c in filter_delivery_before(report, date.fromisoformat(threshold))}
        wanted = expected['state'] == 'resolved' and expected['value'] < threshold
        if found != wanted:
            errors.append(f'filter before {threshold}: expected {wanted}, actual {found}')
    return actual, errors
