"""Smoke tests: CLI entrypoints respond to --help."""

import subprocess
import sys


def test_app_py_help_runs():
    """`python app.py --help` must exit 0 and list commands."""
    result = subprocess.run(
        [sys.executable, "app.py", "--help"],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"
    assert "balance" in result.stdout
    assert "buy" in result.stdout


def test_module_invocation_help_runs():
    """`python -m pyquotex --help` must exit 0 and list commands."""
    result = subprocess.run(
        [sys.executable, "-m", "pyquotex", "--help"],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"
    assert "balance" in result.stdout
    assert "buy" in result.stdout
