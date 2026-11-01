"""Windows demo bootstrap. Only the standard library is required to start."""

import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time
from urllib.request import urlopen
import webbrowser
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[1]
VENV = ROOT / '.venv'
PYTHON = VENV / 'Scripts' / 'python.exe'
MARKER = VENV / 'demo-install.json'


def announce(message: str) -> None:
    """Persist launcher events only. Never copy app output or uploaded data."""
    print(message, flush=True)
    try:
        directory = ROOT / '.logs'
        directory.mkdir(exist_ok=True)
        with (directory / 'start-demo.log').open('a', encoding='utf-8') as log:
            log.write(f'{datetime.now().isoformat(timespec="seconds")} {message}\n')
    except OSError:
        print('Не удалось записать журнал запуска; диагностика доступна в этом окне.', flush=True)


def manifest_hash(root: Path) -> str:
    return hashlib.sha256(b'\0'.join((root / name).read_bytes()
                                   for name in ('requirements.txt', 'pyproject.toml'))).hexdigest()


def environment_ready(python: Path, root: Path, *, verbose: bool = False) -> bool:
    # Metadata checks every installed runtime requirement, not just Streamlit.
    # Application imports are checked separately: broken code needs no pip retry.
    check = '''import importlib.metadata as m, pathlib, sys, tomllib
from packaging.requirements import Requirement
from packaging.markers import default_environment
import contract_review, streamlit
assert sys.version_info >= (3, 11)
assert pathlib.Path(contract_review.__file__).resolve().is_relative_to(pathlib.Path.cwd() / 'src')
project = tomllib.loads(pathlib.Path('pyproject.toml').read_text(encoding='utf-8'))['project']
assert m.version(project['name']) == project['version']
for raw in project['dependencies']:
    requirement = Requirement(raw)
    assert m.version(requirement.name) in requirement.specifier, raw
environment = default_environment()
environment['extra'] = ''
for distribution in m.distributions():
    for raw in distribution.requires or []:
        requirement = Requirement(raw)
        if requirement.marker and not requirement.marker.evaluate(environment):
            continue
        assert m.version(requirement.name) in requirement.specifier, raw
'''
    result = subprocess.run([str(python), '-c', check], cwd=root, capture_output=True,
                            text=True, encoding='utf-8', errors='backslashreplace')
    if result.returncode and verbose:
        print(result.stdout + result.stderr)
    return result.returncode == 0


def application_ready() -> bool:
    # Fresh process, before starting HTTP. Health alone does not execute app.py.
    check = '''import traceback, sys
from pathlib import Path
try:
    import app
    from streamlit.testing.v1 import AppTest
    session = AppTest.from_file('app.py', default_timeout=30).run()
    if session.exception:
        print('DEMO_DIAG: Ошибка создания первой сессии; проверьте app.py.', file=sys.stderr)
        sys.exit(1)
except Exception as error:
    # No source lines, exception values, locals or document content in diagnostics.
    print('DEMO_DIAG: ' + type(error).__name__, file=sys.stderr)
    for frame in traceback.extract_tb(error.__traceback__):
        path = Path(frame.filename)
        location = path.relative_to(Path.cwd()) if path.is_relative_to(Path.cwd()) else path.name
        print(f'DEMO_DIAG: {location}:{frame.lineno} ({frame.name})', file=sys.stderr)
    sys.exit(1)
'''
    result = subprocess.run([str(PYTHON), '-c', check], cwd=ROOT, capture_output=True,
                            text=True, encoding='utf-8', errors='backslashreplace',
                            env={**os.environ, 'PYTHONIOENCODING': 'utf-8'}, timeout=60)
    if result.returncode:
        announce('Проверка приложения не прошла: ошибка импорта или первой сессии. '
                 'Проверьте целостность app.py и src/contract_review; повторная установка не исправляет код.')
        # The subprocess emits safe stack locations, not exception values.
        for line in result.stderr.splitlines():
            if line.startswith('DEMO_DIAG: '):
                announce(line.removeprefix('DEMO_DIAG: '))
        return False
    announce('Импорт приложения и создание первой сессии проверены.')
    return True


def ensure_environment() -> bool:
    if sys.version_info < (3, 11):
        print('Нужен Python 3.11 или новее: https://www.python.org/downloads/windows/')
        return False
    if not PYTHON.exists():
        if VENV.exists():
            print('Папка .venv существует, но Python в ней отсутствует. Переименуйте эту папку и повторите запуск.')
            return False
        print('Создаем локальное окружение .venv…', flush=True)
        subprocess.run([sys.executable, '-m', 'venv', str(VENV)], check=True)
    signature = manifest_hash(ROOT)
    try:
        installed = json.loads(MARKER.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        installed = {}
    if not isinstance(installed, dict):
        installed = {}
    ready = environment_ready(PYTHON, ROOT)
    if not ready or (installed and installed.get('manifest') != signature):
        announce('Устанавливаем зависимости. При первом запуске нужен доступ к интернету…')
        subprocess.run([str(PYTHON), '-m', 'pip', 'install', '-r', 'requirements.txt'], cwd=ROOT, check=True)
        if not environment_ready(PYTHON, ROOT, verbose=True):
            print('Проверка окружения не прошла. Выполните .venv\\Scripts\\python.exe -m pip check для диагностики.')
            return False
    else:
        announce('Окружение готово. Повторная установка не нужна.')
    MARKER.write_text(json.dumps({'manifest': signature}), encoding='utf-8')
    return True


def port_available(port: int) -> bool:
    with socket.socket() as listener:
        if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            listener.bind(('127.0.0.1', port))
        except OSError:
            return False
    return True


def choose_port(preferred: int) -> int:
    for port in range(preferred, min(preferred + 100, 65536)):
        if port_available(port):
            if port != preferred:
                announce(f'Порт {preferred} занят. Новый сервер будет запущен на порту {port}. '
                         'Прежний процесс продолжает работу; используйте новую вкладку.')
            return port
    raise OSError('Свободный порт не найден. Укажите другой начальный порт через --port.')


def open_when_ready(url: str, process: subprocess.Popen) -> None:
    for _ in range(120):
        if process.poll() is not None:
            return
        try:
            with urlopen(url + '/_stcore/health', timeout=1) as response:
                if response.status == 200 and response.read() == b'ok' and process.poll() is None:
                    if not webbrowser.open(url):
                        announce('Откройте адрес вручную: ' + url)
                    else:
                        announce('Адрес приложения передан браузеру: ' + url)
                    return
        except OSError:
            pass
        time.sleep(.5)
    announce('Сервер запускается дольше обычного. Проверьте сообщения в консоли и откройте ' + url)


def main() -> int:
    parser = argparse.ArgumentParser(description='Локальный запуск проверки договоров')
    parser.add_argument('--port', type=int, default=8501, help='Локальный порт (по умолчанию 8501)')
    parser.add_argument('--check', action='store_true', help='Только подготовить и проверить окружение')
    args = parser.parse_args()
    os.chdir(ROOT)
    if not 1 <= args.port <= 65535:
        print('Порт должен быть числом от 1 до 65535.')
        return 1
    try:
        if not ensure_environment():
            return 1
        if not application_ready():
            return 1
        if args.check:
            print('Проверка установки завершена успешно.', flush=True)
            return 0
        port = choose_port(args.port)
        # Unique route prevents another server winning a bind race from being
        # mistaken for this launch. HTTP 200 from a stale root is insufficient.
        route = 'demo-' + uuid4().hex[:12]
        url = f'http://127.0.0.1:{port}/{route}'
        announce('Запускаем приложение: ' + url + '\nДля остановки нажмите Ctrl+C.')
        process = subprocess.Popen([str(PYTHON), '-m', 'streamlit', 'run', str(ROOT / 'app.py'),
                                    '--server.address', '127.0.0.1', '--server.port', str(port),
                                    '--server.baseUrlPath', route, '--server.headless', 'true'], cwd=ROOT,
                                   env={**os.environ, 'PYTHONIOENCODING': 'utf-8'})
        threading.Thread(target=open_when_ready, args=(url, process), daemon=True).start()
        try:
            code = process.wait()
            announce(f'Сервер завершился, код {code}. ' + ('Проверьте сообщения выше; повторите запуск.' if code else ''))
            return code
        except KeyboardInterrupt:
            process.terminate()
            process.wait(timeout=10)
            return 0
    except (OSError, subprocess.SubprocessError) as error:
        announce(f'Не удалось подготовить или запустить приложение ({type(error).__name__}).\n'
                 'Проверьте доступ к интернету, свободное место и сообщения выше. Права администратора не нужны.')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
