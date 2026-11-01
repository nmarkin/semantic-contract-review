"""Windows/Edge cold session and actual canvas pixels; optional Playwright.

Starts its own fresh server, uploads all six files without reload, then runs
the full browser smoke. Only that owned subprocess is stopped on completion.
"""
from pathlib import Path
import json
import os
import socket
import subprocess
import sys
import time
from urllib.request import urlopen

from playwright.sync_api import expect, sync_playwright

expect.set_options(timeout=30000)

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / '.pytest_cache'


def pixel(page, row, x=140):
    return page.get_by_test_id('stDataFrame').locator('canvas').first.evaluate('''(canvas, point) => {
        const scale = canvas.width / canvas.getBoundingClientRect().width;
        return [...canvas.getContext('2d').getImageData(Math.round(point[0]*scale),
            Math.round(point[1]*scale), 1, 1).data].slice(0,3);
    }''', [x, 35 + row * 42 + 7])


def select_row(page, row):
    canvas = page.get_by_test_id('stDataFrame').locator('canvas').first
    canvas.scroll_into_view_if_needed()
    box = canvas.bounding_box()
    page.mouse.click(box['x'] + 24, box['y'] + 35 + row * 42 + 21)


def assert_pixel(page, row, rgb, x=140):
    # Canvas painting can lag a Streamlit rerun by a frame.
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        actual = pixel(page, row, x)
        if actual == rgb:
            return
        page.wait_for_timeout(100)
    raise AssertionError((row, x, actual, rgb))


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    ARTIFACTS.mkdir(exist_ok=True)
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    url = f'http://127.0.0.1:{port}'
    log_path = ARTIFACTS / 'cold-server.log'
    errors = []
    console = []
    with log_path.open('w', encoding='utf-8') as log:
        server = subprocess.Popen([sys.executable, '-m', 'streamlit', 'run', 'app.py',
                                   '--server.address=127.0.0.1', f'--server.port={port}',
                                   '--server.headless=true'], cwd=ROOT, stdout=log, stderr=log,
                                  env={**os.environ, 'PYTHONIOENCODING': 'utf-8'})
        try:
            for _ in range(120):
                assert server.poll() is None, log_path.read_text(encoding='utf-8')
                try:
                    if urlopen(url + '/_stcore/health', timeout=1).read() == b'ok':
                        break
                except OSError:
                    pass
                time.sleep(.25)
            else:
                raise AssertionError('Server did not start')
            with sync_playwright() as p:
                browser = p.chromium.launch(channel='msedge', headless=True)
                page = browser.new_page(viewport={'width': 1280, 'height': 800})
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.on('console', lambda message: console.append({'type': message.type, 'text': message.text}))
                navigations = []
                page.on('framenavigated', lambda frame: navigations.append(frame.url) if frame == page.main_frame else None)
                page.goto(url)
                # First browser session, immediately select real files. No example button or refresh.
                page.locator('input[type=file]').set_input_files([str(f) for f in sorted((ROOT / 'documents').glob('*.txt'))])
                expect(page.get_by_text('Договор STN-101', exact=True)).to_be_visible(timeout=30000)
                assert len(navigations) == 1 and not errors
                assert page.get_by_test_id('stException').count() == 0
                # Unselected rows: inspect actual rendered canvas pixels, not Python CSS.
                assert_pixel(page, 1, [255, 240, 206])
                assert_pixel(page, 2, [228, 234, 242])
                assert_pixel(page, 1, [255, 255, 255], x=350)
                select_row(page, 2)
                expect(page.get_by_text('Договор STN-103', exact=True)).to_be_visible()
                assert_pixel(page, 0, [255, 240, 206])
                assert page.locator('.st-key-proposals').count() == 0
                empty = page.get_by_test_id('stExpander').filter(has_text='Несогласованные изменения (0)')
                assert empty.locator('details').get_attribute('open') is None
                empty.locator('summary').click()
                expect(page.get_by_text('Несогласованных изменений не обнаружено', exact=True)).to_be_visible()
                # Sort using the native header; same contract must stay selected.
                canvas = page.get_by_test_id('stDataFrame').locator('canvas').first
                # Opening the zero-proposal expander scrolls the table out of
                # view. Coordinate clicks must target the visible canvas.
                canvas.scroll_into_view_if_needed()
                box = canvas.bounding_box()
                page.mouse.dblclick(box['x'] + 90, box['y'] + 18, delay=150)
                expect(page.get_by_text('Договор STN-103', exact=True)).to_be_visible()
                select_row(page, 2)
                expect(page.get_by_text('Договор STN-101', exact=True)).to_be_visible()
                select_row(page, 0)
                expect(page.get_by_text('Договор STN-103', exact=True)).to_be_visible()
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                page.screenshot(path=str(ARTIFACTS / 'cold-table.png'), full_page=True)
                review_date = page.get_by_test_id('stDateInput').filter(has_text='Условия действуют на дату').locator('input')
                review_date.fill('2025-01-01')
                review_date.press('Enter')
                review_date.press('Escape')
                expect(page.get_by_text('Договор STN-103', exact=True)).to_be_visible()
                expect(page.locator('.st-key-term_delivery_deadline_requires_review')).to_be_visible()
                review_date.fill('2026-03-10')
                review_date.press('Enter')
                review_date.press('Escape')
                expect(page.locator('.st-key-term_delivery_deadline_resolved')).to_be_visible()
                page.get_by_role('button', name='Изменить документы', exact=True).click()
                page.get_by_role('button', name='Очистить комплект', exact=True).click()
                expect(page.get_by_role('button', name='Открыть пример', exact=True)).to_be_visible()
                base = (ROOT / 'documents/01_contract.txt').read_text(encoding='utf-8')
                amendment = (ROOT / 'documents/02_amendment_signed.txt').read_text(encoding='utf-8')
                synthetic = [base.replace('STN-101', 'NEW-701'), base.replace('STN-101', 'NEW-702'),
                             amendment.replace('STN-101', 'NEW-702').replace('подписано обеими сторонами', 'статус неизвестен')]
                page.locator('input[type=file]').set_input_files([
                    {'name': f'synthetic-{i}.txt', 'mimeType': 'text/plain', 'buffer': text.encode()} for i, text in enumerate(synthetic)])
                expect(page.get_by_text('Договор NEW-701', exact=True)).to_be_visible()
                assert_pixel(page, 1, [248, 223, 216])
                assert_pixel(page, 1, [255, 255, 255], x=350)
                select_row(page, 1)
                expect(page.get_by_text('Договор NEW-702', exact=True)).to_be_visible()
                assert_pixel(page, 0, [223, 240, 229])
                assert page.locator('.st-key-proposals').count() == 0
                empty = page.get_by_test_id('stExpander').filter(has_text='Несогласованные изменения (0)')
                assert empty.locator('details').get_attribute('open') is None
                empty.locator('summary').click()
                expect(page.get_by_text('Несогласованных изменений не обнаружено', exact=True)).to_be_visible()
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                page.screenshot(path=str(ARTIFACTS / 'cold-statuses.png'), full_page=True)
                page.get_by_role('button', name='Изменить документы', exact=True).click()
                page.get_by_role('button', name='Очистить комплект', exact=True).click()
                page.get_by_role('button', name='Открыть пример', exact=True).click()
                expect(page.get_by_text('Договор STN-101', exact=True)).to_be_visible()
                assert_pixel(page, 1, [255, 240, 206])
                assert_pixel(page, 2, [228, 234, 242])
                browser.close()
            assert 'Uncaught app execution' not in log_path.read_text(encoding='utf-8')
            print('PASS: cold server → fresh Edge → six uploads → table without refresh; actual pixels for all four statuses, neutral adjacent cells; selection/sort/date/new documents.')
            subprocess.run([sys.executable, str(ROOT / 'scripts/smoke_ui.py'), url], cwd=ROOT, check=True)
        finally:
            (ARTIFACTS / 'cold-browser.json').write_text(json.dumps({'page_errors': errors, 'console': console}, ensure_ascii=False, indent=2), encoding='utf-8')
            server.terminate()
            server.wait(timeout=15)


if __name__ == '__main__':
    main()
