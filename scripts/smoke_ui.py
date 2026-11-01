"""Optional browser smoke: python scripts/smoke_ui.py http://127.0.0.1:8502.

Requires Playwright and installed Microsoft Edge; not part of runtime/pytest.
Start Streamlit separately. Screenshots go into .pytest_cache/.
"""

from pathlib import Path
import sys

from playwright.sync_api import expect, sync_playwright

expect.set_options(timeout=30000)


ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / '.pytest_cache'
ARTIFACTS.mkdir(exist_ok=True)
URL = sys.argv[1] if len(sys.argv) > 1 else 'http://127.0.0.1:8501'
sys.stdout.reconfigure(encoding='utf-8')


def no_overflow(page):
    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
    assert page.get_by_test_id('stException').count() == 0


def select_row(page, index):
    table = page.get_by_test_id('stDataFrame')
    table.scroll_into_view_if_needed()
    bounds = table.locator('canvas').first.bounding_box()
    page.mouse.click(bounds['x'] + 24, bounds['y'] + 35 + 42 * index + 21)


with sync_playwright() as p:
    browser = p.chromium.launch(channel='msedge', headless=True)
    page = browser.new_page(viewport={'width': 1280, 'height': 800})
    page.goto(URL, wait_until='networkidle')
    expect(page.get_by_role('button', name='Открыть пример', exact=True)).to_be_visible()
    assert page.get_by_test_id('stDataFrame').count() == 0
    assert page.get_by_test_id('stSelectbox').count() == 0
    no_overflow(page)
    page.screenshot(path=str(ARTIFACTS / 'ux-empty.png'), full_page=True)
    page.get_by_role('button', name='Открыть пример', exact=True).click()
    expect(page.get_by_text('Договор STN-101', exact=True)).to_be_visible(timeout=30000)
    expect(page.get_by_test_id('stMetricValue').filter(has_text='1 380 000 ₽')).to_be_visible()
    expect(page.locator('.st-key-term_total_amount_resolved')).to_have_css('border-top-color', 'rgb(40, 139, 95)')
    expect(page.locator('.st-key-term_total_amount_resolved')).to_have_css('background-color', 'rgb(238, 247, 241)')
    assert page.get_by_test_id('stFileUploader').is_visible() is False
    assert page.get_by_test_id('stExpander').locator('details[open]').count() == 0
    no_overflow(page)
    page.screenshot(path=str(ARTIFACTS / 'ux-loaded.png'), full_page=True)
    page.get_by_role('button', name='Проверить в документе', exact=True).first.click()
    dialog = page.get_by_role('dialog')
    expect(dialog).to_be_visible()
    proof = dialog.frame_locator('iframe').locator('#evidence-target')
    expect(proof).to_have_text('1 380 000')
    bounds = proof.evaluate('(el) => ({top:el.getBoundingClientRect().top,bottom:el.getBoundingClientRect().bottom,height:window.innerHeight})')
    assert bounds['bottom'] > 0 and bounds['top'] < bounds['height']
    page.screenshot(path=str(ARTIFACTS / 'ux-source.png'), full_page=True)
    dialog.get_by_role('button', name='Закрыть документ', exact=True).click()
    expect(dialog).not_to_be_visible()
    select_row(page, 1)
    expect(page.get_by_text('Договор STN-102', exact=True)).to_be_visible()
    page.get_by_text('Фильтр поставок', exact=True).click()
    expect(page.get_by_text('Договор STN-102', exact=True)).to_be_visible()
    page.get_by_test_id('stExpander').filter(has_text='Несогласованные изменения (1)').locator('summary').first.click()
    card = page.locator('.st-key-proposal_STN-102_0')
    expect(card).to_contain_text('Предлагаемый срок: 27.03.2026')
    expect(card).to_contain_text('Действующий срок: 20.03.2026')
    expect(card).to_contain_text('+7 дней')
    expect(card.locator('pre.source-quote').filter(has_text='Коллеги')).to_have_count(1)
    expect(page.locator('pre.source-quote').filter(has_text='Коллеги')).to_have_count(1)
    card.screenshot(path=str(ARTIFACTS / 'ux-proposal.png'))
    with page.expect_download() as exported:
        page.get_by_role('button', name='Скачать отчет', exact=True).click()
    download = exported.value
    destination = ARTIFACTS / 'contract-report.html'
    download.save_as(str(destination))
    assert download.suggested_filename == 'contract-review-2026-03-10.html'
    report = browser.new_page(viewport={'width': 1280, 'height': 800}, java_script_enabled=False)
    network = []
    report.on('request', lambda request: network.append(request.url) if request.url.startswith(('http:', 'https:')) else None)
    report.goto(destination.resolve().as_uri())
    expect(report.get_by_role('heading', name='Действующие условия', exact=True)).to_be_visible()
    assert report.locator('table').inner_text().count('STN-') == 3  # export is not narrowed by UI filter
    assert report.locator('pre.source-quote').filter(has_text='Коллеги').count() == 1
    assert report.locator('script,iframe,img,link').count() == 0 and not network
    target = report.locator('a[href^="#source-"]').first
    href = target.get_attribute('href')
    target.click()
    expect(report.locator(href)).to_be_in_viewport()
    no_overflow(report)
    report.screenshot(path=str(ARTIFACTS / 'ux-report.png'), full_page=False)
    report.close()
    page.get_by_text('Фильтр поставок', exact=True).click()
    expect(page.get_by_text('Строго раньше выбранной даты. Учитываются только подтвержденные действующие сроки.', exact=True)).not_to_be_visible()
    page.wait_for_function("document.querySelector('[data-testid=stDataFrame] canvas').getBoundingClientRect().height > 150")
    expect(page.get_by_text('Договор STN-102', exact=True)).to_be_visible()
    select_row(page, 2)
    expect(page.get_by_text('Договор STN-103', exact=True)).to_be_visible()
    expect(page.get_by_test_id('stMetricValue').filter(has_text='Нет данных')).to_be_visible()
    expect(page.locator('.st-key-term_total_amount_missing')).to_have_css('border-top-color', 'rgb(112, 123, 138)')
    page.get_by_role('button', name='Проверить в документе', exact=True).first.click()
    expect(dialog.frame_locator('iframe').locator('#evidence-target')).to_contain_text('не входит')
    dialog.get_by_role('button', name='Закрыть документ', exact=True).click()
    page.get_by_role('button', name='Изменить документы', exact=True).click()
    expect(page.get_by_test_id('stFileUploader')).to_be_visible()
    files = []
    for index, path in enumerate(sorted((ROOT / 'documents').glob('*.txt'))):
        data = path.read_bytes()
        files.append({'name': path.name, 'mimeType': 'text/plain',
                      'buffer': data.decode().encode('cp1251') if index % 2 else data})
    page.locator('input[type=file]').set_input_files(files)
    expect(page.get_by_text('Открыт учебный пример. Загрузка своих файлов заменит его.', exact=True)).not_to_be_visible()
    page.keyboard.press('Escape')
    expect(page.get_by_test_id('stDataFrame')).to_be_visible()
    assert page.get_by_test_id('stSelectbox').count() == 0
    page.get_by_role('button', name='Изменить документы', exact=True).click()
    page.get_by_role('button', name='Очистить комплект', exact=True).click()
    expect(page.get_by_role('button', name='Открыть пример', exact=True)).to_be_visible()
    assert page.get_by_test_id('stDataFrame').count() == 0
    page.locator('input[type=file]').set_input_files(files + [
        {'name': 'short.txt', 'mimeType': 'text/plain', 'buffer': 'Привет'.encode('cp1251')},
    ])
    expect(page.get_by_test_id('stSelectbox')).to_be_visible()
    assert page.get_by_test_id('stDataFrame').count() == 0
    page.get_by_role('combobox').click()
    page.get_by_role('option', name='Windows-1251', exact=True).click()
    expect(page.get_by_test_id('stDataFrame')).to_be_visible()
    expect(page.get_by_text('Не все документы удалось однозначно связать с договорами.', exact=True)).to_be_visible()
    no_overflow(page)
    page.get_by_role('button', name='Изменить документы', exact=True).click()
    page.get_by_role('button', name='Очистить комплект', exact=True).click()
    expect(page.get_by_role('button', name='Открыть пример', exact=True)).to_be_visible()
    uncertain = []
    for path in sorted((ROOT / 'documents').glob('*.txt')):
        data = path.read_bytes()
        if path.name == '02_amendment_signed.txt':
            data = data.decode().replace('подписано обеими сторонами', 'статус неизвестен').encode()
        uncertain.append({'name': path.name, 'mimeType': 'text/plain', 'buffer': data})
    page.locator('input[type=file]').set_input_files(uncertain)
    expect(page.locator('.st-key-term_total_amount_requires_review')).to_have_css('border-top-color', 'rgb(185, 82, 65)')
    expect(page.locator('.st-key-term_total_amount_requires_review')).to_contain_text('Требуется проверка')
    no_overflow(page)
    page.screenshot(path=str(ARTIFACTS / 'ux-review.png'), full_page=True)
    print('PASS: empty/example, table selection, sources, grouped proposals, offline HTML download, filter, missing amount, mixed encoding, clear, manual repair, semantic status accents; 1280x800 without page overflow.')
    browser.close()
