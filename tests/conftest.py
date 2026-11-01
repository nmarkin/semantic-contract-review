from pathlib import Path

import pytest

from contract_review import extract_facts, parse_txt_bytes


@pytest.fixture(scope="session")
def examples():
    root = Path(__file__).resolve().parents[1]
    paths = sorted((root / "documents").glob("*.txt"))
    assert len(paths) == 6, "Expected all six supplied training documents"
    return {path.name: extract_facts(parse_txt_bytes(path.read_bytes(), path.name)) for path in paths}
