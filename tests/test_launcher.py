"""Validate the lightweight macOS app without opening or closing user windows."""
from pathlib import Path
import os
import plistlib
import shutil
import subprocess
import sys

import pytest


PROJECT = Path(__file__).resolve().parents[1]
BUNDLE = PROJECT / "返佣结算.app"
EXECUTABLE = BUNDLE / "Contents" / "MacOS" / "返佣结算"


def test_bundle_metadata_and_executable():
    with (BUNDLE / "Contents" / "Info.plist").open("rb") as stream:
        info = plistlib.load(stream)
    assert info["CFBundlePackageType"] == "APPL"
    assert info["CFBundleExecutable"] == EXECUTABLE.name
    assert os.access(EXECUTABLE, os.X_OK)


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS launcher")
def test_launcher_syntax():
    subprocess.run(["/bin/zsh", "-n", str(EXECUTABLE)], check=True)


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS launcher")
def test_launcher_finds_project_from_unrelated_directory(tmp_path):
    result = subprocess.run(
        [str(EXECUTABLE), "--check"], cwd=tmp_path,
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert "启动环境正常" in result.stdout


@pytest.fixture
def launcher_project(tmp_path):
    """Isolate failures from the real app, environment, and user windows."""
    project = tmp_path / "project with spaces"
    executable = project / "返佣结算.app" / "Contents" / "MacOS" / "返佣结算"
    executable.parent.mkdir(parents=True)
    shutil.copy2(EXECUTABLE, executable)
    (project / "app.py").touch()
    return project, executable


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS launcher")
def test_check_missing_environment_has_actionable_error(launcher_project):
    project, executable = launcher_project
    result = subprocess.run([str(executable), "--check"], capture_output=True, text=True, timeout=10)
    assert result.returncode != 0
    assert "缺少 Python 运行环境" in result.stderr
    assert "requirements.txt" in result.stderr
    assert not (project / "artifacts").exists()


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS launcher")
def test_check_missing_dependency_has_repair_command(launcher_project):
    project, executable = launcher_project
    python = project / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.symlink_to(sys.executable)
    (project / "app.py").write_text("raise ModuleNotFoundError('missing test dependency')\n")
    result = subprocess.run([str(executable), "--check"], capture_output=True, text=True, timeout=10)
    assert result.returncode != 0
    assert "missing test dependency" in result.stderr
    assert "pip install -r requirements.txt" in result.stderr
    assert not (project / "artifacts").exists()


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS launcher")
def test_failed_launch_reports_exit_and_log_without_opening_dialog(launcher_project):
    project, executable = launcher_project
    # Load only function definitions, then run with an alert stub; never open a real alert.
    prefix, body = executable.read_text().split("\ncd -- ", 1)
    script = project / "failure-check.zsh"
    script.write_text(prefix + '\nshow_error() { print -u2 -r -- "$1"; }\n'
                      + 'PROJECT_DIR="${0:A:h}"\ncd -- ' + body)
    python = project / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.symlink_to(sys.executable)
    (project / "app.py").write_text("raise RuntimeError('startup regression')\n")
    result = subprocess.run(["/bin/zsh", str(script)], capture_output=True, text=True, timeout=10)
    assert result.returncode != 0
    assert "launcher.log" in result.stderr
    assert "startup regression" in (project / "artifacts" / "launcher.log").read_text()


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS launcher")
def test_command_launcher_supports_same_environment_check(tmp_path):
    result = subprocess.run([str(PROJECT / "启动返佣结算.command"), "--check"], cwd=tmp_path,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert "启动环境正常" in result.stdout
