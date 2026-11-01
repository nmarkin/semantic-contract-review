"""Collect actual QA evidence, never update the independent oracle."""
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
QA = ROOT / 'qa/adversarial'
matrix = json.loads((QA / 'MATRIX.json').read_text(encoding='utf-8'))
results = [json.loads((ROOT / '.pytest_cache/adversarial_results' / (c['id'] + '.json')).read_text(encoding='utf-8'))
           for c in matrix['cases']]
tree = ET.parse(QA / 'pytest-results.xml')
tests = tree.findall('.//testcase')
failures = [dict(name=t.attrib['name'], module=t.attrib['classname'], message=t.find('failure').attrib.get('message', ''))
            for t in tests if t.find('failure') is not None]
old = [t for t in tests if 'test_adversarial' not in t.attrib['classname']]
assert len(old) == 272 and all(t.find('failure') is None and t.find('error') is None for t in old)
output = dict(matrix_sha256=hashlib.sha256((QA / 'MATRIX.json').read_bytes()).hexdigest(),
              tests=len(tests), failures=failures, passed=len(tests)-len(failures),
              baseline_passed=len(old), scenario_count=len(results),
              metamorphic_passed=sum(r['id'].startswith('meta-') and not r['errors'] for r in results),
              scenarios=results)
(QA / 'RESULTS.json').write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps({k: v for k, v in output.items() if k not in ('failures', 'scenarios')}, indent=2))
print('Failures: ' + ', '.join(f['name'] for f in failures))
