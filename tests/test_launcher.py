"""Launcher failure paths, quoting and no unnecessary installation."""

import importlib.util
from pathlib import Path
import socket
from unittest.mock import Mock

import pytest


spec = importlib.util.spec_from_file_location('demo_launcher', Path(__file__).resolve().parents[1] / 'scripts/start_demo.py')
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)


@pytest.fixture(autouse=True)
def isolate_launcher_logs(tmp_path, monkeypatch):
    monkeypatch.setattr(launcher, 'ROOT', tmp_path)


@pytest.fixture
def project(tmp_path, monkeypatch):
    root = tmp_path / 'Проверка договоров with spaces'
    root.mkdir()
    (root / 'requirements.txt').write_text('-e .', encoding='utf-8')
    (root / 'pyproject.toml').write_text('[project]\nname="test"', encoding='utf-8')
    venv = root / '.venv'
    python = venv / 'Scripts/python.exe'
    monkeypatch.setattr(launcher, 'ROOT', root)
    monkeypatch.setattr(launcher, 'VENV', venv)
    monkeypatch.setattr(launcher, 'PYTHON', python)
    monkeypatch.setattr(launcher, 'MARKER', venv / 'demo-install.json')
    return root, python


def test_ready_environment_is_not_reinstalled(project, monkeypatch):
    root, python = project
    python.parent.mkdir(parents=True)
    python.touch()
    monkeypatch.setattr(launcher, 'environment_ready', lambda *args, **kwargs: True)
    run = Mock()
    monkeypatch.setattr(launcher.subprocess, 'run', run)
    assert launcher.ensure_environment()
    assert launcher.ensure_environment()
    assert not run.called
    (root / 'requirements.txt').write_text('-e .\n# changed', encoding='utf-8')
    assert launcher.ensure_environment()
    assert run.call_count == 1
    assert run.call_args.args[0] == [str(python), '-m', 'pip', 'install', '-r', 'requirements.txt']
    assert run.call_args.kwargs['cwd'] == root


def test_missing_environment_created_and_dependencies_installed(project, monkeypatch):
    root, python = project
    def run(command, **kwargs):
        if 'venv' in command:
            python.parent.mkdir(parents=True)
            python.touch()
    run = Mock(side_effect=run)
    monkeypatch.setattr(launcher.subprocess, 'run', run)
    monkeypatch.setattr(launcher, 'environment_ready', Mock(side_effect=[False, True]))
    assert launcher.ensure_environment()
    assert run.call_count == 2
    assert run.call_args_list[0].args[0][-1] == str(root / '.venv')


def test_broken_existing_environment_is_not_overwritten(project, capsys):
    root, _ = project
    (root / '.venv').mkdir()
    assert not launcher.ensure_environment()
    assert 'Переименуйте' in capsys.readouterr().out


def test_occupied_port_is_detected_without_killing_owner():
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        listener.listen()
        assert not launcher.port_available(listener.getsockname()[1])
        assert listener.fileno() >= 0


def test_failed_install_returns_actionable_error(project, monkeypatch, capsys):
    monkeypatch.setattr(launcher.sys, 'argv', ['start_demo.py', '--check'])
    monkeypatch.setattr(launcher, 'ensure_environment', Mock(side_effect=OSError('offline')))
    monkeypatch.chdir(project[0])
    assert launcher.main() == 1
    assert 'Проверьте доступ к интернету' in capsys.readouterr().out


def test_browser_opens_only_after_healthy_local_server(monkeypatch):
    from unittest.mock import MagicMock
    response = MagicMock()
    response.__enter__.return_value.status = 200
    response.__enter__.return_value.read.return_value = b'ok'
    monkeypatch.setattr(launcher, 'urlopen', Mock(return_value=response))
    browser = Mock(return_value=True)
    monkeypatch.setattr(launcher.webbrowser, 'open', browser)
    launcher.open_when_ready('http://127.0.0.1:8502', Mock(poll=Mock(return_value=None)))
    browser.assert_called_once_with('http://127.0.0.1:8502')


def test_busy_port_chooses_next_without_touching_owner(monkeypatch):
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        listener.listen()
        occupied = listener.getsockname()[1]
        selected = launcher.choose_port(occupied)
        assert selected > occupied and launcher.port_available(selected)
        assert listener.fileno() >= 0 and not launcher.port_available(occupied)


def test_no_free_port_is_actionable(monkeypatch):
    monkeypatch.setattr(launcher, 'port_available', lambda port: False)
    with pytest.raises(OSError, match='--port'):
        launcher.choose_port(65535)


def test_stale_html_health_does_not_open_browser(monkeypatch):
    from unittest.mock import MagicMock
    response = MagicMock()
    response.__enter__.return_value.status = 200
    response.__enter__.return_value.read.return_value = b'<html>old server</html>'
    monkeypatch.setattr(launcher, 'urlopen', Mock(return_value=response))
    monkeypatch.setattr(launcher.time, 'sleep', lambda _: None)
    browser = Mock()
    monkeypatch.setattr(launcher.webbrowser, 'open', browser)
    launcher.open_when_ready('http://127.0.0.1:8502/demo-new', Mock(poll=Mock(return_value=None)))
    browser.assert_not_called()


def test_exited_server_never_opens_browser(monkeypatch):
    browser = Mock()
    monkeypatch.setattr(launcher.webbrowser, 'open', browser)
    launcher.open_when_ready('http://127.0.0.1:8502/demo-new', Mock(poll=Mock(return_value=1)))
    browser.assert_not_called()


def test_broken_app_stops_before_server_without_reinstall(project, monkeypatch):
    monkeypatch.chdir(project[0])
    monkeypatch.setattr(launcher.sys, 'argv', ['start_demo.py'])
    environment = Mock(return_value=True)
    monkeypatch.setattr(launcher, 'ensure_environment', environment)
    monkeypatch.setattr(launcher, 'application_ready', Mock(return_value=False))
    server = Mock()
    monkeypatch.setattr(launcher.subprocess, 'Popen', server)
    assert launcher.main() == 1
    environment.assert_called_once()
    server.assert_not_called()


def test_each_launch_has_new_route_and_current_interpreter(project, monkeypatch):
    root, python = project
    monkeypatch.chdir(root)
    monkeypatch.setattr(launcher.sys, 'argv', ['start_demo.py'])
    monkeypatch.setattr(launcher, 'ensure_environment', lambda: True)
    monkeypatch.setattr(launcher, 'application_ready', lambda: True)
    monkeypatch.setattr(launcher, 'choose_port', lambda preferred: 8503)
    monkeypatch.setattr(launcher.threading, 'Thread', Mock())
    server = Mock(return_value=Mock(wait=Mock(return_value=0)))
    monkeypatch.setattr(launcher.subprocess, 'Popen', server)
    assert launcher.main() == launcher.main() == 0
    commands = [call.args[0] for call in server.call_args_list]
    assert commands[0] != commands[1]
    for command in commands:
        assert command[0] == str(python) and str(root / 'app.py') in command
        assert command[command.index('--server.port') + 1] == '8503'


def test_real_import_error_is_diagnosed_without_logging_exception_contents(tmp_path, monkeypatch, capsys):
    import sys
    (tmp_path / 'app.py').write_text("raise ImportError('private-contract-12345')", encoding='utf-8')
    monkeypatch.setattr(launcher, 'ROOT', tmp_path)
    monkeypatch.setattr(launcher, 'PYTHON', Path(sys.executable))
    assert not launcher.application_ready()
    output = capsys.readouterr().out
    log = (tmp_path / '.logs/start-demo.log').read_text(encoding='utf-8')
    assert 'ImportError' in output and 'app.py:1' in output
    assert 'private-contract-12345' not in output + log
