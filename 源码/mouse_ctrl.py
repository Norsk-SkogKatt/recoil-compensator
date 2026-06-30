"""
Low-level mouse control via Windows SendInput API.

This is the most compatible method for game-level mouse simulation.
Moves are relative (delta-based) for recoil compensation.
"""

import ctypes
import ctypes.wintypes
import logging

logger = logging.getLogger(__name__)

# ── Windows types ──────────────────────────────────────────────
LONG = ctypes.wintypes.LONG
DWORD = ctypes.wintypes.DWORD
ULONG_PTR = ctypes.wintypes.WPARAM  # sizeof(void*) on x86/x64

# ── SendInput constants ────────────────────────────────────────
INPUT_MOUSE = 0
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_ABSOLUTE = 0x8000


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", LONG),
        ("dy", LONG),
        ("mouseData", DWORD),
        ("dwFlags", DWORD),
        ("time", DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class INPUT(ctypes.Structure):
    _fields_ = [
        ("type", DWORD),
        ("mi", MOUSEINPUT),
    ]


def move_relative(dx: int, dy: int) -> None:
    """
    Move the mouse cursor by (dx, dy) pixels relative to the current position.

    Uses SendInput for the best game compatibility.
    """
    inp = INPUT()
    inp.type = INPUT_MOUSE
    inp.mi = MOUSEINPUT(dx, dy, 0, MOUSEEVENTF_MOVE, 0, 0)

    result = ctypes.windll.user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(inp))
    if result != 1:
        logger.warning(f"SendInput returned {result} (expected 1)")


def send_click(down: bool) -> None:
    """Send a left mouse button down/up event."""
    flag = MOUSEEVENTF_LEFTDOWN if down else MOUSEEVENTF_LEFTUP
    inp = INPUT()
    inp.type = INPUT_MOUSE
    inp.mi = MOUSEINPUT(0, 0, 0, flag, 0, 0)
    ctypes.windll.user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(inp))
