from __future__ import annotations
import subprocess
from playwright._impl._driver import compute_driver_executable, get_driver_env
from playwright.async_api import BrowserType as AsyncBrowserType
from playwright.sync_api import BrowserType as SyncBrowserType

__version__ = "0.0.0"
__all__ = ["install"]


def install(
    browser_type: SyncBrowserType | AsyncBrowserType,
    *,
    with_deps: bool = False,
) -> bool:
    """install playwright and deps if needed

    Args:
        browser_type (SyncBrowserType | AsyncBrowserType): `BrowserType` object. Example: `p.chrome`
        with_deps (bool, optional): install with dependencies. Defaults to `False`.

    Returns:
        bool: succeeded or failed
    """
    driver_executable = str(compute_driver_executable())
    args = [driver_executable, "install-deps"]
    env = None
    if browser_type:
        args = [driver_executable, "install", browser_type.name]
        env = get_driver_env()
        if with_deps:
            args.append("--with-deps")

    proc = subprocess.run(args, env=env, capture_output=True, text=True)

    return proc.returncode == 0
