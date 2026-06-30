"""
Recoil compensation engine.

State machine that manages:
  - Active / inactive toggle
  - Vertical, horizontal-left, horizontal-right intensity
  - A compensation thread that moves the mouse while mouse button is held

Threading model:
  - Main thread / TUI reads/writes state via locked properties
  - Hooks thread signals start/stop via Event
  - Compensator worker thread moves the mouse in a tight loop
"""

import threading
import time
import logging
from typing import Optional

from mouse_ctrl import move_relative

logger = logging.getLogger(__name__)

TICK_INTERVAL = 0.010  # 10 ms tick → ~100 movements / sec


class Compensator:
    """Manages recoil-compensation state and runs the movement loop."""

    def __init__(self):
        self._lock = threading.Lock()

        # ── state ──────────────────────────────────────────────
        self._active = False           # user toggle (global on/off)
        self._mouse_down = False       # actual mouse state from hook
        self._intensity_vertical = 10
        self._intensity_h_left = 0
        self._intensity_h_right = 0

        # ── threading ──────────────────────────────────────────
        self._worker: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._mouse_down_event = threading.Event()

    # ── public properties (thread-safe) ────────────────────────

    @property
    def active(self) -> bool:
        return self._active

    @active.setter
    def active(self, value: bool) -> None:
        with self._lock:
            self._active = value
            if not value:
                # deactivating → stop any ongoing compensation
                self._mouse_down = False
                self._mouse_down_event.clear()

    @property
    def mouse_down(self) -> bool:
        return self._mouse_down

    @mouse_down.setter
    def mouse_down(self, value: bool) -> None:
        with self._lock:
            self._mouse_down = value
            if value and self._active:
                self._mouse_down_event.set()
            else:
                self._mouse_down_event.clear()

    @property
    def intensity_vertical(self) -> int:
        return self._intensity_vertical

    @intensity_vertical.setter
    def intensity_vertical(self, value: int) -> None:
        with self._lock:
            self._intensity_vertical = max(0, min(100, value))

    @property
    def intensity_h_left(self) -> int:
        return self._intensity_h_left

    @intensity_h_left.setter
    def intensity_h_left(self, value: int) -> None:
        with self._lock:
            self._intensity_h_left = max(0, min(100, value))

    @property
    def intensity_h_right(self) -> int:
        return self._intensity_h_right

    @intensity_h_right.setter
    def intensity_h_right(self, value: int) -> None:
        with self._lock:
            self._intensity_h_right = max(0, min(100, value))

    def set_intensities(self, v: int, hl: int, hr: int) -> None:
        """Atomically set all three intensities (from config load / TUI)."""
        with self._lock:
            self._intensity_vertical = max(0, min(100, v))
            self._intensity_h_left = max(0, min(100, hl))
            self._intensity_h_right = max(0, min(100, hr))

    def get_intensities(self) -> tuple[int, int, int]:
        with self._lock:
            return (self._intensity_vertical, self._intensity_h_left, self._intensity_h_right)

    # ── worker lifecycle ───────────────────────────────────────

    def start(self) -> None:
        """Start the compensation worker thread."""
        if self._worker and self._worker.is_alive():
            return
        self._stop_event.clear()
        self._mouse_down_event.clear()
        self._worker = threading.Thread(target=self._run, daemon=True, name="comp-worker")
        self._worker.start()
        logger.info("Compensation worker started")

    def stop(self) -> None:
        """Signal the worker to halt and wait for it."""
        self._stop_event.set()
        self._mouse_down_event.set()  # unblock any pending wait
        if self._worker and self._worker.is_alive():
            self._worker.join(timeout=2)
        logger.info("Compensation worker stopped")

    # ── internal worker loop ───────────────────────────────────

    def _run(self) -> None:
        """Worker thread: waits for mouse-down signal, then compensates in a loop."""
        while not self._stop_event.is_set():
            # Wait until mouse is pressed AND script is active
            self._mouse_down_event.wait()

            if self._stop_event.is_set():
                break

            # Compensation loop – runs while mouse is down and we're active
            while not self._stop_event.is_set():
                with self._lock:
                    if not self._active or not self._mouse_down:
                        self._mouse_down_event.clear()
                        break
                    v = self._intensity_vertical
                    hl = self._intensity_h_left
                    hr = self._intensity_h_right

                # Calculate net movement
                dx = hr - hl
                dy = v

                if dx != 0 or dy != 0:
                    move_relative(dx, dy)

                time.sleep(TICK_INTERVAL)

        logger.debug("Compensation worker exited")
