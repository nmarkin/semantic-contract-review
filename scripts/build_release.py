"""Build an allowlisted, reviewable source ZIP; never copy a whole workspace."""

import hashlib
import json
from pathlib import Path
import re
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo


ROOT = Path(__file__).resolve().parents[1]
PREFIX = 'semantic-contract-review'
EXACT = (
    'README.md', 'app.py', 'pyproject.toml', 'requirements.txt',
    'requirements-dev.txt', 'Start Demo.cmd', '.gitignore', '.gitattributes',
    '.streamlit/config.toml', 'docs/requirements.md', 'docs/release.md',
    'docs/images/demo.png', 'qa/release/REPORT.md',
    'qa/adversarial/MATRIX.json', 'qa/adversarial/INPUT_SHA256.json',
    'qa/adversarial/UI_EXPECTATIONS.json', 'qa/adversarial/BASELINE.json',
)
PATTERNS = (
    'src/contract_review/*.py', 'tests/*.py', 'scripts/*.py',
    'documents/*.txt', 'qa/adversarial/documents/*.txt',
)
SENSITIVE = (
    re.compile(r'\b[A-Za-z]:[\\/](?!/)'),
    re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),
    re.compile(r'\b(?:ghp_|github_pat_|sk-proj-)[A-Za-z0-9_-]{20,}'),
    re.compile(r'\bAKIA[A-Z0-9]{16}\b'),
)


def payload(root: Path) -> dict[str, bytes]:
    paths = {root / name for name in EXACT}
    for pattern in PATTERNS:
        found = set(root.glob(pattern))
        if not found:
            raise ValueError(f'Empty required group: {pattern}')
        paths.update(found)
    if len(list(root.glob('documents/*.txt'))) != 6:
        raise ValueError('The release must include the six original training documents')
    result = {}
    for path in sorted(paths):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
            raise ValueError(f'Unsafe source: {relative}')
        content = path.read_bytes()
        if path.suffix != '.png':
            text = content.decode('utf-8-sig')
            if any(pattern.search(text) for pattern in SENSITIVE):
                # Do not print potentially sensitive content.
                raise ValueError(f'Publication scan requires review: {relative}')
        result[relative] = content
    return result


def main() -> None:
    files = payload(ROOT)
    output = ROOT / 'dist'
    output.mkdir(exist_ok=True)
    archive = output / f'{PREFIX}.zip'
    with ZipFile(archive, 'w', ZIP_DEFLATED, compresslevel=9) as target:
        for name, content in sorted(files.items()):
            item = ZipInfo(f'{PREFIX}/{name}')
            item.compress_type = ZIP_DEFLATED
            item.external_attr = 0o100644 << 16
            target.writestr(item, content)
    with ZipFile(archive) as check:
        assert check.testzip() is None
        assert {n.removeprefix(PREFIX + '/'): check.read(n) for n in check.namelist()} == files
    summary = dict(
        archive=archive.name, bytes=archive.stat().st_size, file_count=len(files),
        sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),
        files={name: hashlib.sha256(data).hexdigest() for name, data in sorted(files.items())},
    )
    (output / 'release-manifest.json').write_text(json.dumps(summary, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({k: v for k, v in summary.items() if k != 'files'}, indent=2))


if __name__ == '__main__':
    main()
