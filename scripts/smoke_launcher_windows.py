"""Optional real CMD/Edge checks against an installed, disposable project copy.

Usage: python scripts/smoke_launcher_windows.py ".pytest_cache/<clean copy>"
The copy must be inside .pytest_cache. Only test-owned process trees are stopped.
"""
from pathlib import Path
import os
import re
import subprocess
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    copy = Path(sys.argv[1]).resolve()
    assert copy.is_relative_to(ROOT / '.pytest_cache') and (copy / '.venv/Scripts/python.exe').is_file()
    original = (copy / 'app.py').read_text(encoding='utf-8')
    processes = []
    handles = []

    def launch(name):
        path = ROOT / '.pytest_cache' / f'launcher-{name}.log'
        handle = path.open('w', encoding='utf-8')
        handles.append(handle)
        # CMD interprets quotes itself; list2cmdline's backslash escapes are wrong here.
        process = subprocess.Popen(f'cmd.exe /d /c call "{copy / "Start Demo.cmd"}"',
                                   cwd=os.environ['TEMP'], stdout=handle, stderr=handle, stdin=subprocess.PIPE,
                                   env={**os.environ, 'PYTHONIOENCODING': 'utf-8', 'STREAMLIT_SERVER_FILE_WATCHER_TYPE': 'none'})
        processes.append(process)
        return process, path

    def address(process, log):
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            text = log.read_text(encoding='utf-8', errors='replace')
            match = re.search(r'Адрес приложения передан браузеру: (http://\S+)', text)
            if match:
                assert 'Повторная установка не нужна' in text
                return match[1]
            assert process.poll() is None, text
            time.sleep(.2)
        raise AssertionError(log.read_text(encoding='utf-8', errors='replace'))

    class Occupied(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'old unrelated server')

        def log_message(self, *args):
            pass

    # Fail explicitly if we cannot own 8501; never touch another owner's process.
    busy = ThreadingHTTPServer(('127.0.0.1', 8501), Occupied)
    Thread(target=busy.serve_forever, daemon=True).start()
    try:
        (copy / 'launch_version.py').write_text('VERSION = "Launcher version one"\n', encoding='utf-8')
        (copy / 'app.py').write_text(original.replace('def main() -> None:',
            'def main() -> None:\n    from launch_version import VERSION\n    st.caption(VERSION)'), encoding='utf-8')
        first, first_log = launch('first')
        url1 = address(first, first_log)
        assert ':8501/' not in url1
        with sync_playwright() as p:
            browser = p.chromium.launch(channel='msedge', headless=True)
            old = browser.new_page(viewport={'width': 1280, 'height': 800})
            old.goto(url1)
            expect(old.get_by_text('Launcher version one', exact=True)).to_be_visible()
            old.locator('input[type=file]').set_input_files([str(f) for f in sorted((copy / 'documents').glob('*.txt'))])
            expect(old.get_by_text('Договор STN-101', exact=True)).to_be_visible(timeout=30000)
            (copy / 'launch_version.py').write_text('VERSION = "Launcher updated version two"\n', encoding='utf-8')
            second, second_log = launch('second')
            url2 = address(second, second_log)
            assert url1 != url2 and first.poll() is None
            fresh = browser.new_page(viewport={'width': 1280, 'height': 800})
            fresh.goto(url2)
            expect(fresh.get_by_text('Launcher updated version two', exact=True)).to_be_visible()
            expect(old.get_by_text('Launcher version one', exact=True)).to_be_visible()
            fresh.locator('input[type=file]').set_input_files([str(f) for f in sorted((copy / 'documents').glob('*.txt'))])
            expect(fresh.get_by_text('Договор STN-101', exact=True)).to_be_visible(timeout=30000)
            assert fresh.get_by_test_id('stException').count() == old.get_by_test_id('stException').count() == 0
            fresh.screenshot(path=str(ROOT / '.pytest_cache/launcher-fresh.png'), full_page=True)
            browser.close()
        assert busy.fileno() >= 0
        (copy / 'app.py').write_text("raise ImportError('sensitive-test-sentinel')\n", encoding='utf-8')
        broken, broken_log = launch('broken')
        broken.communicate(b'\n', timeout=60)
        text = broken_log.read_text(encoding='utf-8', errors='replace')
        assert broken.returncode == 1 and 'ImportError' in text and 'window will stay open' in text
        assert 'Устанавливаем зависимости' not in text and 'sensitive-test-sentinel' not in text
        print(f'PASS: CMD from TEMP and Cyrillic path; occupied 8501 retained; repeated launch no install; {url1} → {url2}; stale server stays old, new process imports changed module; six uploads without refresh; failed import exit 1 + retained console.')
    finally:
        (copy / 'app.py').write_text(original, encoding='utf-8')
        busy.shutdown()
        busy.server_close()
        for process in processes:
            if process.poll() is None:
                # These PIDs were created here and remain live; never discover by port.
                subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'], capture_output=True)
                process.wait(timeout=15)
        for handle in handles:
            handle.close()


if __name__ == '__main__':
    main()
