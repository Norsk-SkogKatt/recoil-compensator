"""
Global keyboard & mouse hooks via pynput.

Hotkeys:
  ~ (backtick)      → toggle compensation on/off
  [                  → decrease vertical intensity (script on only)
  ]                  → increase vertical intensity (script on only)
  Shift+[            → decrease horizontal-left intensity (script on only)
  Shift+]            → increase horizontal-right intensity (script on only)

Mouse:
  Left button down   → signal compensator to start pulling
  Left button up     → signal compensator to stop
"""

import logging
from typing import Optional, Callable

from pynput import keyboard, mouse

logger = logging.getLogger(__name__)

# Virtual key codes (US layout; works on most layouts for bracket keys)
VK_TILDE = 192      # ` / ~  key (above Tab)
VK_OPEN_BRACKET = 219   # [
VK_CLOSE_BRACKET = 221  # ]
VK_LSHIFT = 0xA0
VK_RSHIFT = 0xA1


class HooksManager:
    """Manages pynput keyboard and mouse listeners, forwarding events to callbacks."""

    def __init__(self):
        self._kb_listener: Optional[keyboard.Listener] = None
        self._mouse_listener: Optional[mouse.Listener] = None

        # ── state tracking for modifiers ───────────────────────
        self._shift_pressed = False

        # ── callbacks (set by main) ────────────────────────────
        self.on_toggle: Optional[Callable[[], None]] = None
        self.on_vertical_up: Optional[Callable[[], None]] = None
        self.on_vertical_down: Optional[Callable[[], None]] = None
        self.on_h_left_up: Optional[Callable[[], None]] = None
        self.on_h_right_up: Optional[Callable[[], None]] = None
        self.on_mouse_down: Optional[Callable[[], None]] = None
        self.on_mouse_up: Optional[Callable[[], None]] = None

    # ── start / stop ───────────────────────────────────────────

    @property
    def kb_running(self) -> bool:
        """Whether the keyboard listener thread is alive."""
        return bool(self._kb_listener and self._kb_listener.running)

    @property
    def mouse_running(self) -> bool:
        """Whether the mouse listener thread is alive."""
        return bool(self._mouse_listener and self._mouse_listener.running)

    def start(self) -> None:
        """Start both keyboard and mouse listeners."""
        if self._kb_listener is None or not self._kb_listener.running:
            self._kb_listener = keyboard.Listener(
                on_press=self._on_key_press,
                on_release=self._on_key_release,
                suppress=False,
            )
            self._kb_listener.daemon = True
            self._kb_listener.start()
            # Give the thread a moment to initialise the hook
            self._kb_listener.join(timeout=0.5)
            if self._kb_listener.running:
                logger.info("Keyboard listener started and running")
            else:
                logger.error("Keyboard listener FAILED to start (blocked by antivirus/group policy?)")

        if self._mouse_listener is None or not self._mouse_listener.running:
            self._mouse_listener = mouse.Listener(
                on_click=self._on_mouse_click,
                suppress=False,
            )
            self._mouse_listener.daemon = True
            self._mouse_listener.start()
            self._mouse_listener.join(timeout=0.5)
            if self._mouse_listener.running:
                logger.info("Mouse listener started and running")
            else:
                logger.error("Mouse listener FAILED to start")

    def stop(self) -> None:
        """Stop both listeners."""
        if self._kb_listener and self._kb_listener.running:
            self._kb_listener.stop()
        if self._mouse_listener and self._mouse_listener.running:
            self._mouse_listener.stop()
        logger.info("All hooks stopped")

    # ── keyboard handler ───────────────────────────────────────

    def _on_key_press(self, key) -> None:
        """Handle key-down events."""
        try:
            # Track Shift state
            if key in (keyboard.Key.shift, keyboard.Key.shift_l, keyboard.Key.shift_r):
                self._shift_pressed = True
                return

            vk = getattr(key, "vk", None)
            char = getattr(key, "char", None)

            # ~ backtick toggle (match by VK first, then fall back to char)
            if vk == VK_TILDE or char in ('`', '~'):
                if self.on_toggle:
                    self.on_toggle()
                return

            # [ ] brackets – only proceed if we have a VK
            if vk is None:
                return

            if vk == VK_OPEN_BRACKET:
                if self._shift_pressed:
                    if self.on_h_left_up:
                        self.on_h_left_up()
                else:
                    if self.on_vertical_down:
                        self.on_vertical_down()
                return

            if vk == VK_CLOSE_BRACKET:
                if self._shift_pressed:
                    if self.on_h_right_up:
                        self.on_h_right_up()
                else:
                    if self.on_vertical_up:
                        self.on_vertical_up()
                return
        except Exception as e:
            logger.error(f"Key press handler error: {e}")

    def _on_key_release(self, key) -> None:
        """Handle key-up events (only needed for modifier tracking)."""
        try:
            if key in (keyboard.Key.shift, keyboard.Key.shift_l, keyboard.Key.shift_r):
                self._shift_pressed = False
        except Exception as e:
            logger.error(f"Key release handler error: {e}")

    # ── mouse handler ──────────────────────────────────────────

    def _on_mouse_click(self, x, y, button, pressed) -> None:
        """Handle mouse button events."""
        try:
            if button != mouse.Button.left:
                return
            if pressed:
                if self.on_mouse_down:
                    self.on_mouse_down()
            else:
                if self.on_mouse_up:
                    self.on_mouse_up()
        except Exception as e:
            logger.error(f"Mouse click handler error: {e}")
