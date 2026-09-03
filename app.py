"""Compatibility shim. The CLI now lives in pyquotex.cli.

Kept so that documented usage `python app.py <command>` continues to work.
"""

from pyquotex.cli.__main__ import cli_main

if __name__ == "__main__":
    cli_main()
