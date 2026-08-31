"""Validate the lightweight macOS app without opening or closing user windows."""
from pathlib import Path
import os
import plistlib
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
