"""Collect stage 4.2B results without rewriting the frozen QA oracle/artifacts."""

import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
QA = ROOT / 'qa/adversarial'
OUT = ROOT / 'qa/regression42b'
matrix = json.loads((QA / 'MATRIX.json').read_text(encoding='utf-8'))
tests = ET.parse(OUT / 'pytest-results.xml').findall('.//testcase')
baseline = json.loads((QA / 'BASELINE.json').read_text(encoding='utf-8'))
prior = [ET.Element('testcase', classname=t['classname'], name=t['name']) for t in baseline['tests']]
identity = lambda t: (t.attrib['classname'], t.attrib['name'])
by_id = {identity(t): t for t in tests}
assert all(identity(t) in by_id for t in prior), 'A previous scenario disappeared'
assert all(t.find('failure') is None and t.find('error') is None and t.find('skipped') is None for t in tests)
original_six = [t['name'] for t in baseline['tests'] if t['outcome'] == 'failed']
assert len(original_six) == 6
results = [json.loads((ROOT / '.pytest_cache/adversarial_results' / (c['id'] + '.json')).read_text(encoding='utf-8'))
           for c in matrix['cases']]
assert all(not r['errors'] for r in results)
old_ids = {identity(t) for t in prior if 'test_adversarial' not in t.attrib['classname']}
assert len(old_ids) == 272
output = dict(matrix_sha256=hashlib.sha256((QA / 'MATRIX.json').read_bytes()).hexdigest(),
              tests=len(tests), passed=len(tests), failures=[], baseline_passed=len(old_ids),
              retained_prior_tests=len(prior), new_tests=len(tests)-len(prior),
              original_six_passed=original_six, scenario_count=len(results),
              metamorphic_passed=sum(r['id'].startswith('meta-') for r in results), scenarios=results)
(OUT / 'RESULTS.json').write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps({k: v for k, v in output.items() if k not in ('scenarios', 'original_six_passed')}, indent=2))
