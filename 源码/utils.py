"""
Utility functions: admin check, elevation, single-instance, notifications.
"""

import ctypes
import ctypes.wintypes
import sys
import os
import logging

logger = logging.getLogger(__name__)


def is_admin() -> bool:
    """Check if the current process is running with administrator privileges."""
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False


def request_admin() -> None:
    """
    Re-launch the current script with administrator privileges via runas.
    Exits the current process after launching.
    """
    if is_admin():
        return

    script = sys.argv[0]
    params = " ".join(f'"{a}"' if " " in a else a for a in sys.argv[1:])

    ctypes.windll.shell32.ShellExecuteW(
        None,
        "runas",
        sys.executable,
        f'"{script}" {params}',
        None,
        1,  # SW_SHOWNORMAL
    )
    sys.exit(0)


def show_message_box(title: str, message: str, icon: int = 0x40) -> int:
    """
    Show a Windows message box.
    icon: 0x40=info, 0x30=warning, 0x10=error
    Returns the clicked button ID.
    """
    return ctypes.windll.user32.MessageBoxW(0, message, title, icon | 0x1000)


def ensure_single_instance(mutex_name: str = "Global\\RecoilCompensator_UniqMutex") -> bool:
    """
    Ensure only one instance of this application runs.
    Uses a named kernel Mutex. Returns True if this is the first instance.
    Returns False if another instance is already running.

    On failure (e.g., sandbox restriction), returns True to let it proceed.
    """
    try:
        kernel32 = ctypes.windll.kernel32
        mutex = kernel32.CreateMutexW(None, False, mutex_name)
        last_error = kernel32.GetLastError()

        if last_error == 0xB7:  # ERROR_ALREADY_EXISTS
            kernel32.CloseHandle(mutex)
            return False
        return True
    except Exception as e:
        logger.warning(f"Single-instance check failed, allowing proceed: {e}")
        return True


def get_config_dir() -> str:
    """Get the directory for storing config files (in APPDATA)."""
    appdata = os.environ.get("APPDATA", os.path.expanduser("~"))
    config_dir = os.path.join(appdata, "压枪脚本")
    os.makedirs(config_dir, exist_ok=True)
    return config_dir


def get_config_path() -> str:
    """Get the full path to the config JSON file."""
    return os.path.join(get_config_dir(), "config.json")
