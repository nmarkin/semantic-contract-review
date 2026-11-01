"""Unmasked adversarial failures are intentional evidence for stage 4.2B."""
import hashlib
import json
from pathlib import PureWindowsPath

import pytest

from adversarial_support import ROOT, QA, CASES, run, audit


@pytest.mark.parametrize('case', CASES, ids=lambda c: c['id'])
def test_independent_oracle_and_invariants(case):
    report, extractions, keys = run(case)
    actual, errors = audit(case, report, extractions, keys)
    directory = ROOT / '.pytest_cache/adversarial_results'
    directory.mkdir(parents=True, exist_ok=True)
    (directory / (case['id'] + '.json')).write_text(json.dumps(dict(id=case['id'], actual=actual, errors=errors),
                                                            ensure_ascii=False, indent=2), encoding='utf-8')
    assert not errors, '\n'.join(errors)


def test_frozen_inputs_and_oracle_are_unchanged():
    hashes = json.loads((QA / 'INPUT_SHA256.json').read_text(encoding='utf-8'))
    # The frozen manifest was authored on Windows; interpret its separators on any OS.
    assert all(hashlib.sha256(QA.joinpath(*PureWindowsPath(path).parts).read_bytes()).hexdigest() == digest
               for path, digest in hashes.items())
