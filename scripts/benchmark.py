"""Repeatable backend-only measurement; UI batch limits intentionally do not apply."""

from datetime import date
import gc
import json
import platform
from time import perf_counter
import tracemalloc

from contract_review import extract_facts, parse_txt_bytes, resolve_contracts


def run(count):
    documents = []
    for index in range(count):
        data = (f'ДОГОВОР ПОСТАВКИ № LOAD-{index}\nСтатус: подписан обеими сторонами 01.01.2030.\n\n'
                'Поставщик: ООО «Завод».\n\nОбщая стоимость договора составляет 100 000 рублей.\n\n'
                'Поставщик обязуется поставить товар не позднее 01.04.2030.').encode()
        documents.append(extract_facts(parse_txt_bytes(data, f'{index}.txt')))
    report = resolve_contracts(documents, date(2030, 3, 10))
    assert len(report.contracts) == count
    for extraction in documents:
        extraction.validate_evidence()
    return len(documents), len(report.contracts)


if __name__ == '__main__':
    rows = []
    for count in (50, 200, 1000):
        gc.collect()
        started = perf_counter()
        processed, contracts = run(count)
        seconds = perf_counter() - started
        gc.collect()
        tracemalloc.start()
        run(count)  # Separate run: tracing overhead must not affect timings.
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        rows.append(dict(documents=processed, contracts=contracts, seconds=round(seconds, 3),
                         peak_python_mib=round(peak / 1024**2, 2), errors=0))
    print(json.dumps(dict(python=platform.python_version(), platform=platform.platform(),
                          measurements=rows), indent=2))
