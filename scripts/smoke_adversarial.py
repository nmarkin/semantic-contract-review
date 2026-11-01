"""Stage 4.2B: real Edge assertions for the repaired P0/P1 scenarios."""
from pathlib import Path
import json
import os
import socket
import subprocess
import sys
import time
from urllib.request import urlopen

from playwright.sync_api import expect, sync_playwright

# Cold sessions and rerenders can exceed Playwright's 5 s assertion default on
# a busy Windows workstation. Assertions still wait for the exact UI outcome.
expect.set_options(timeout=30000)

ROOT = Path(__file__).resolve().parents[1]
QA = ROOT / 'qa/adversarial'
MATRIX = json.loads((QA / 'MATRIX.json').read_text(encoding='utf-8'))
OUT = ROOT / 'qa/regression42b/browser'


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    OUT.mkdir(exist_ok=True, parents=True)
    observations = {'page_errors': [], 'console_errors': [], 'checks': [], 'verified_semantics': []}
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    url = f'http://127.0.0.1:{port}'
    with (OUT / 'server.log').open('w', encoding='utf-8') as log:
        server = subprocess.Popen([sys.executable, '-m', 'streamlit', 'run', 'app.py', '--server.address=127.0.0.1',
                                   f'--server.port={port}', '--server.headless=true'], cwd=ROOT, stdout=log, stderr=log,
                                  env={**os.environ, 'PYTHONIOENCODING': 'utf-8'})
        try:
            for _ in range(120):
                assert server.poll() is None
                try:
                    if urlopen(url + '/_stcore/health', timeout=1).read() == b'ok':
                        break
                except OSError:
                    pass
                time.sleep(.25)
            with sync_playwright() as p:
                browser = p.chromium.launch(channel='msedge', headless=True)
                page = browser.new_page(viewport={'width': 1280, 'height': 800})
                page.on('pageerror', lambda error: observations['page_errors'].append(str(error)))
                page.on('console', lambda message: observations['console_errors'].append(message.text) if message.type == 'error' else None)
                page.goto(url)

                def load(keys):
                    if page.get_by_role('button', name='Изменить документы', exact=True).count():
                        page.get_by_role('button', name='Изменить документы', exact=True).click()
                        page.get_by_role('button', name='Очистить комплект', exact=True).click()
                    expect(page.get_by_role('button', name='Открыть пример', exact=True)).to_be_visible(timeout=30000)
                    page.locator('input[type=file]').set_input_files([str(QA / MATRIX['documents'][key]['file']) for key in keys])
                    expect(page.get_by_test_id('stDataFrame')).to_be_visible(timeout=30000)
                    expect(page.get_by_text('Договор DEMO-900', exact=True)).to_be_visible()

                def as_of(value):
                    widget = page.get_by_test_id('stDateInput').filter(has_text='Условия действуют на дату').locator('input')
                    widget.fill(value)
                    widget.press('Enter')
                    widget.press('Escape')

                def clean_and_capture(name):
                    assert page.get_by_test_id('stException').count() == 0
                    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                    page.screenshot(path=str(OUT / f'{name}.png'), full_page=True, animations='disabled')
                    observations['checks'].append(name)

                def zero_expander():
                    block = page.get_by_test_id('stExpander').filter(has_text='Несогласованные изменения (0)')
                    expect(block).to_be_visible()
                    assert block.locator('details').get_attribute('open') is None
                    assert page.locator('.st-key-proposals').count() == 0
                    assert block.evaluate('''el => {
                        for (let p=el; p; p=p.parentElement) {
                            if (getComputedStyle(p).backgroundColor === 'rgb(255, 246, 231)') return false;
                        } return true;
                    }''')
                    block.locator('summary').click()
                    expect(block.get_by_text('Несогласованных изменений не обнаружено', exact=True)).to_be_visible()

                load(['base', *'ABCDEFGH'])
                as_of('2026-03-11')
                expect(page.locator('.st-key-term_delivery_deadline_requires_review')).to_contain_text('Требуется проверка')
                expect(page.locator('.st-key-term_total_amount_resolved')).to_contain_text('1 250 000 ₽')
                clean_and_capture('chain-conflict')
                page.get_by_text('Фильтр поставок', exact=True).click()
                expect(page.get_by_text('Подходящих договоров нет. Измените дату или отключите фильтр.', exact=True)).to_be_visible()
                assert page.get_by_test_id('stDataFrame').count() == 0
                page.get_by_text('Фильтр поставок', exact=True).click()
                as_of('2026-03-21')
                expect(page.locator('.st-key-term_delivery_deadline_resolved')).to_contain_text('10.05.2026')
                page.locator('.st-key-term_delivery_deadline_resolved').get_by_role('button', name='Проверить в документе').click()
                dialog = page.get_by_role('dialog')
                expect(dialog.get_by_text('H.txt', exact=True)).to_be_visible()
                expect(dialog.frame_locator('iframe').locator('#evidence-target')).to_have_text('10.05.2026')
                page.screenshot(path=str(OUT / 'exact-H-source.png'), full_page=True, animations='disabled')
                dialog.get_by_role('button', name='Закрыть документ').click()
                unapplied = page.get_by_test_id('stExpander').filter(has_text='Другие непримененные условия и документы').first
                unapplied.locator('summary').first.click()
                expect(unapplied).to_contain_text('Историческая неопределенность')
                expect(unapplied).to_contain_text('H.txt (с 20.03.2026)')
                observations['verified_semantics'].append({'id': 'historical-conflict', 'text': unapplied.inner_text()})
                clean_and_capture('chain-resolved-with-history')
                with page.expect_download() as event:
                    page.get_by_role('button', name='Скачать отчет').click()
                download = event.value
                download.save_as(OUT / 'chain-report.html')
                html = (OUT / 'chain-report.html').read_text(encoding='utf-8')
                summary = html.split('<tbody>', 1)[1].split('</tbody>', 1)[0]
                assert '1 250 000 ₽' in summary and '10.05.2026' in summary and 'Требуется проверка' not in summary
                as_of('2026-02-02')
                expect(page.locator('.st-key-term_total_amount_resolved')).to_contain_text('1 000 000 ₽')
                proposals = page.locator('.st-key-proposals')
                proposals.locator('summary').first.click()
                expect(proposals).to_contain_text('28.04.2026')
                expect(proposals).to_contain_text('Документ датирован позже даты проверки')
                expect(proposals).to_contain_text('12.02.2026')
                expect(proposals).to_contain_text('25.02.2026')
                observations['verified_semantics'].append({'id': 'future-proposals', 'text': proposals.inner_text()})
                clean_and_capture('future-proposals')

                load(['base'])
                as_of('2026-03-11')
                zero_expander()
                clean_and_capture('zero-confirmed')
                load(['base', 'F', 'G'])
                expect(page.locator('.st-key-term_delivery_deadline_requires_review')).to_be_visible()
                zero_expander()
                clean_and_capture('zero-review')
                load(['base', 'wrong_supplier'])
                expect(page.locator('.st-key-term_total_amount_requires_review')).to_be_visible()
                zero_expander()
                clean_and_capture('supplier-conflict')
                load(['base', 'many_proposals'])
                page.locator('.st-key-proposals').locator('summary').first.click()
                card = page.locator('.st-key-proposal_DEMO-900_0')
                expect(card).to_contain_text('28.04.2026')
                expect(page.locator('.st-key-proposal_DEMO-900_1')).to_contain_text('30.04.2026')
                expect(page.locator('.st-key-proposals pre.source-quote').filter(has_text='Предлагаем изменить срок поставки с')).to_have_count(1)
                expect(page.locator('.st-key-proposals').locator('summary').first.locator('p')).to_have_text('Несогласованные изменения (2)')
                observations['verified_semantics'].append({'id': 'independent-proposal-count',
                    'text': page.locator('.st-key-proposals').locator('summary').first.inner_text()})
                clean_and_capture('two-proposals-one-quote')
                for key, field, other, unchanged in (
                    ('unknown-completion', 'delivery_deadline', 'total_amount', '1 000 000 ₽'),
                    ('price-increment', 'total_amount', 'delivery_deadline', '15.04.2026'),
                ):
                    load(['base', key])
                    expect(page.locator(f'.st-key-term_{field}_requires_review')).to_contain_text('Требуется проверка')
                    expect(page.locator(f'.st-key-term_{other}_resolved')).to_contain_text(unchanged)
                    observations['verified_semantics'].append({'id': key, 'text': page.get_by_test_id('stMetric').all_inner_texts()})
                    clean_and_capture(key)
                load(['base', 'unknown-dispatch'])
                review = page.locator('.st-key-term_delivery_deadline_requires_review')
                expect(review).to_contain_text('Требуется проверка')
                expect(review).not_to_contain_text('15.04.2026')
                expect(page.locator('.st-key-term_total_amount_resolved')).to_contain_text('1 000 000 ₽')
                review.get_by_role('button', name='Проверить в документе').click()
                dialog = page.get_by_role('dialog')
                expect(dialog.get_by_text('unknown-dispatch.txt', exact=True)).to_be_visible()
                expect(dialog.frame_locator('iframe').locator('#evidence-target')).to_have_text(
                    'Стороны согласовали новую дату исполнения обязательств по отгрузке: 09.05.2030.')
                page.screenshot(path=str(OUT / 'exact-intent-source.png'), full_page=True, animations='disabled')
                dialog.get_by_role('button', name='Закрыть документ').click()
                with page.expect_download() as event:
                    page.get_by_role('button', name='Скачать отчет').click()
                event.value.save_as(OUT / 'intent-report.html')
                html = (OUT / 'intent-report.html').read_text(encoding='utf-8')
                summary = html.split('<tbody>', 1)[1].split('</tbody>', 1)[0]
                assert 'Требуется проверка' in summary and '15.04.2026' not in summary
                assert 'Обнаружено изменение условия' in html
                observations['verified_semantics'].append({'id': 'unsupported-requires-review', 'text': page.get_by_test_id('stMetric').all_inner_texts()})
                clean_and_capture('unsupported-requires-review')
                page.get_by_text('Фильтр поставок', exact=True).click()
                expect(page.get_by_text('Подходящих договоров нет. Измените дату или отключите фильтр.', exact=True)).to_be_visible()
                assert not observations['page_errors'] and not observations['console_errors']
                browser.close()
            print('PASS browser mechanics: ' + ', '.join(observations['checks']))
            print('PASS semantic regressions: ' + ', '.join(r['id'] for r in observations['verified_semantics']))
        finally:
            (OUT / 'observations.json').write_text(json.dumps(observations, ensure_ascii=False, indent=2), encoding='utf-8')
            server.terminate()
            server.wait(timeout=15)


if __name__ == '__main__':
    main()
