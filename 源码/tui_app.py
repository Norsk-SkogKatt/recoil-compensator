"""
Curses-based Terminal UI with mouse support.

Displays recoil compensation status, intensities, weapon profiles.
Supports mouse clicks on buttons for all operations.
Auto-adapts to terminal size.
"""

import curses
import curses.ascii
import logging
from typing import Optional, Callable

logger = logging.getLogger(__name__)

# ── color pairs ────────────────────────────────────────────────
C_NORMAL = 1
C_HIGHLIGHT = 2
C_ACTIVE = 3
C_INACTIVE = 4
C_TITLE = 5
C_BUTTON = 6
C_BUTTON_HOVER = 7
C_BAR = 8
C_HEADER = 9

# ── UI element tags for mouse click detection ──────────────────
TAG_TOGGLE = "toggle"
TAG_V_DEC = "v_dec"
TAG_V_INC = "v_inc"
TAG_HL_DEC = "hl_dec"
TAG_HL_INC = "hl_inc"
TAG_HR_DEC = "hr_dec"
TAG_HR_INC = "hr_inc"
TAG_PROFILE_SWITCH = "profile_switch"
TAG_PROFILE_SAVE = "profile_save"
TAG_PROFILE_NEW = "profile_new"
TAG_PROFILE_DEL = "profile_del"


class ClickRegion:
    """Defines a clickable rectangular region on screen."""

    def __init__(self, tag: str, y: int, x: int, width: int, height: int = 1):
        self.tag = tag
        self.y = y
        self.x = x
        self.width = width
        self.height = height

    def contains(self, my: int, mx: int) -> bool:
        return self.y <= my < self.y + self.height and self.x <= mx < self.x + self.width


class TuiApp:
    """Main TUI application using curses with mouse support."""

    def __init__(self, compensator=None, config_mgr=None):
        self._stdscr: Optional[curses.window] = None
        self._regions: list[ClickRegion] = []

        # ── references to shared state ─────────────────────────
        self.compensator = compensator
        self.config_mgr = config_mgr

        # ── terminal dimensions (cached) ───────────────────────
        self._rows = 24
        self._cols = 80

        # ── display state (snapshots updated each frame) ───────
        self.active = False
        self.vertical = 10
        self.h_left = 0
        self.h_right = 0
        self.current_profile = "默认"
        self.profile_list: list[str] = []

        # ── callbacks (set by main) ────────────────────────────
        self.on_toggle: Optional[Callable[[], None]] = None
        self.on_vertical_inc: Optional[Callable[[], None]] = None
        self.on_vertical_dec: Optional[Callable[[], None]] = None
        self.on_h_left_inc: Optional[Callable[[], None]] = None
        self.on_h_left_dec: Optional[Callable[[], None]] = None
        self.on_h_right_inc: Optional[Callable[[], None]] = None
        self.on_h_right_dec: Optional[Callable[[], None]] = None
        self.on_profile_switch: Optional[Callable[[], None]] = None
        self.on_profile_save: Optional[Callable[[], None]] = None
        self.on_profile_new: Optional[Callable[[], None]] = None
        self.on_profile_delete: Optional[Callable[[], None]] = None
        self.on_new_profile_submit: Optional[Callable[[str], None]] = None

        # ── input state ────────────────────────────────────────
        self._needs_new_profile_name: Optional[Callable[[str], None]] = None
        self._input_buffer: list[str] = []
        self._input_prompt: str = ""

    # ── entry point ────────────────────────────────────────────

    def run(self, stdscr) -> None:
        """Main TUI loop (called via curses.wrapper)."""
        self._stdscr = stdscr
        self._init_colors()
        self._setup_mouse()
        self._rows, self._cols = stdscr.getmaxyx()

        stdscr.timeout(200)  # ms – allows responsive polling

        while True:
            self._refresh_state()
            self._rows, self._cols = stdscr.getmaxyx()
            self._regions.clear()
            self._draw()
            stdscr.refresh()

            key = stdscr.getch()
            if key == curses.KEY_RESIZE:
                self._rows, self._cols = stdscr.getmaxyx()
                continue
            if key == 27:  # ESC
                if self._needs_new_profile_name:
                    self._cancel_input()
                else:
                    break

            if self._needs_new_profile_name:
                self._handle_input_key(key)
            else:
                self._handle_key(key)

    def _refresh_state(self) -> None:
        """Sync display state from compensator and config_mgr on each frame."""
        if self.compensator:
            self.active = self.compensator.active
            self.vertical, self.h_left, self.h_right = self.compensator.get_intensities()
        if self.config_mgr:
            self.current_profile = self.config_mgr.current_profile_name
            self.profile_list = self.config_mgr.list_profiles()

    # ── setup ──────────────────────────────────────────────────

    def _init_colors(self) -> None:
        curses.start_color()
        curses.use_default_colors()
        #                  pair   fg          bg
        curses.init_pair(C_NORMAL, -1, -1)
        curses.init_pair(C_HIGHLIGHT, curses.COLOR_CYAN, -1)
        curses.init_pair(C_ACTIVE, curses.COLOR_GREEN, -1)
        curses.init_pair(C_INACTIVE, curses.COLOR_RED, -1)
        curses.init_pair(C_TITLE, curses.COLOR_YELLOW, -1)
        curses.init_pair(C_BUTTON, curses.COLOR_BLACK, curses.COLOR_WHITE)
        curses.init_pair(C_BUTTON_HOVER, curses.COLOR_WHITE, curses.COLOR_BLUE)
        curses.init_pair(C_BAR, curses.COLOR_GREEN, curses.COLOR_GREEN)
        curses.init_pair(C_HEADER, curses.COLOR_WHITE, curses.COLOR_BLUE)

    def _setup_mouse(self) -> None:
        try:
            curses.mousemask(curses.ALL_MOUSE_EVENTS | curses.REPORT_MOUSE_POSITION)
        except curses.error:
            pass  # mouse not available in this terminal
        if self._stdscr:
            self._stdscr.keypad(True)

    # ── main drawing ───────────────────────────────────────────

    def _draw(self) -> None:
        std = self._stdscr
        std.erase()

        top = self._draw_header()
        top = self._draw_status(top)
        top = self._draw_intensity_section(top)
        top = self._draw_profile_section(top)
        self._draw_help(top + 1)

        if self._needs_new_profile_name:
            self._draw_input_prompt()

    def _draw_header(self) -> int:
        """Draw title bar. Returns next available y."""
        y = 0
        title = " 压枪脚本 Recoil Compensator v1.0 "
        # centered with background
        try:
            self._stdscr.attron(curses.color_pair(C_HEADER))
            self._stdscr.addstr(y, 0, " " * self._cols)
            self._stdscr.addstr(y, max(0, (self._cols - len(title)) // 2), title)
            self._stdscr.attroff(curses.color_pair(C_HEADER))
        except curses.error:
            pass
        return y + 2

    def _draw_status(self, y: int) -> int:
        """Draw toggle status. Returns next y."""
        std = self._stdscr
        try:
            label = "● 开启中" if self.active else "○ 已关闭"
            color = C_ACTIVE if self.active else C_INACTIVE
            std.attron(curses.color_pair(color) | curses.A_BOLD)
            std.addstr(y, 2, label)
            std.attroff(curses.color_pair(color) | curses.A_BOLD)

            btn = " [点击切换] "
            std.attron(curses.color_pair(C_BUTTON))
            std.addstr(y, 20, btn)
            std.attroff(curses.color_pair(C_BUTTON))
            self._regions.append(ClickRegion(TAG_TOGGLE, y, 20, len(btn)))
        except curses.error:
            pass
        return y + 2

    def _draw_bar(self, y: int, x: int, width: int, value: int, max_val: int = 100) -> None:
        """Draw a horizontal bar representing value/max_val."""
        fill = int((value / max_val) * width) if max_val > 0 else 0
        fill = min(fill, width)
        bar = "█" * fill + "░" * (width - fill)
        try:
            self._stdscr.addstr(y, x, bar[:width])
        except curses.error:
            pass

    def _draw_intensity_section(self, y: int) -> int:
        """Draw the three intensity rows. Returns next y."""
        labels = [
            ("垂直强度:", self.vertical, TAG_V_DEC, TAG_V_INC, "v"),
            ("左水平强度:", self.h_left, TAG_HL_DEC, TAG_HL_INC, "hl"),
            ("右水平强度:", self.h_right, TAG_HR_DEC, TAG_HR_INC, "hr"),
        ]
        bar_width = min(30, self._cols - 30)

        for i, (label, value, tag_dec, tag_inc, _) in enumerate(labels):
            cy = y + i
            try:
                self._stdscr.addstr(cy, 2, label)

                # Decrease button
                dec_btn = " [-] "
                self._stdscr.attron(curses.color_pair(C_BUTTON))
                self._stdscr.addstr(cy, 16, dec_btn)
                self._stdscr.attroff(curses.color_pair(C_BUTTON))
                self._regions.append(ClickRegion(tag_dec, cy, 16, len(dec_btn)))

                # Bar
                self._draw_bar(cy, 22, bar_width, value)

                # Value number
                val_str = f" {value:3d} "
                self._stdscr.addstr(cy, 22 + bar_width + 1, val_str)

                # Increase button
                inc_btn = " [+] "
                self._stdscr.attron(curses.color_pair(C_BUTTON))
                self._stdscr.addstr(cy, 22 + bar_width + 1 + len(val_str), inc_btn)
                self._stdscr.attroff(curses.color_pair(C_BUTTON))
                self._regions.append(ClickRegion(tag_inc, cy, 22 + bar_width + 1 + len(val_str), len(inc_btn)))
            except curses.error:
                pass

        return y + len(labels) + 2

    def _draw_profile_section(self, y: int) -> int:
        """Draw profile management area. Returns next y."""
        std = self._stdscr
        try:
            std.addstr(y, 2, "当前武器:")
            std.attron(curses.A_BOLD | curses.color_pair(C_HIGHLIGHT))
            std.addstr(y, 14, f" {self.current_profile} ")
            std.attroff(curses.A_BOLD | curses.color_pair(C_HIGHLIGHT))
        except curses.error:
            pass

        buttons_y = y + 1
        btn_defs = [
            (TAG_PROFILE_SWITCH, " [切换配置] "),
            (TAG_PROFILE_SAVE, " [保存修改] "),
            (TAG_PROFILE_NEW, " [新建配置] "),
            (TAG_PROFILE_DEL, " [删除配置] "),
        ]
        bx = 2
        for tag, btn_text in btn_defs:
            try:
                std.attron(curses.color_pair(C_BUTTON))
                std.addstr(buttons_y, bx, btn_text)
                std.attroff(curses.color_pair(C_BUTTON))
                self._regions.append(ClickRegion(tag, buttons_y, bx, len(btn_text)))
                bx += len(btn_text) + 2
            except curses.error:
                pass

        return buttons_y + 2

    def _draw_help(self, y: int) -> None:
        """Draw help text at the bottom."""
        help_lines = [
            "  ~ 开关 /  [ 减弱垂直  ] 增强垂直",
            "  Shift+[ 增加左水平  Shift+] 增加右水平",
            "  按住鼠标左键 = 启动压枪补偿",
            "  Esc 退出程式  |  可用滑鼠点击所有按钮",
        ]
        try:
            for i, line in enumerate(help_lines):
                self._stdscr.attron(curses.color_pair(C_NORMAL))
                self._stdscr.addstr(y + i, 2, line)
                self._stdscr.attroff(curses.color_pair(C_NORMAL))
        except curses.error:
            pass

    def _draw_input_prompt(self) -> None:
        """Draw the input box for new profile name."""
        if not self._needs_new_profile_name:
            return
        y = self._rows - 4
        prompt = self._input_prompt
        current = "".join(self._input_buffer)
        try:
            self._stdscr.attron(curses.color_pair(C_HIGHLIGHT) | curses.A_BOLD)
            self._stdscr.addstr(y, 2, prompt)
            self._stdscr.attroff(curses.color_pair(C_HIGHLIGHT) | curses.A_BOLD)
            self._stdscr.addstr(y + 1, 4, current + "_")
            self._stdscr.addstr(y + 2, 2, "Enter=确认  Esc=取消")
        except curses.error:
            pass

    # ── input handling ─────────────────────────────────────────

    def _handle_key(self, key: int) -> None:
        """Handle keyboard input (non-input mode).

        NOTE: Hotkeys (~ [ ] Shift+[ Shift+]) are handled by the
        GLOBAL pynput hooks in hooks.py, NOT here.  This avoids the
        double-fire bug (hook + TUI both processing the same key).
        Only mouse clicks are processed here.
        """
        if key == curses.KEY_MOUSE:
            self._handle_mouse()
            return

        # All other keyboard input deliberately ignored to prevent
        # double-firing with the global pynput hook.

    def _handle_input_key(self, key: int) -> None:
        """Handle keyboard input when in text-input mode (new profile name)."""
        if key == 10 or key == 13:  # Enter
            name = "".join(self._input_buffer).strip()
            if name and self._needs_new_profile_name:
                self._needs_new_profile_name(name)
            self._cancel_input()
        elif key == 27:  # Esc
            self._cancel_input()
        elif key == curses.KEY_BACKSPACE or key == 127 or key == 8:
            if self._input_buffer:
                self._input_buffer.pop()
        elif 32 <= key <= 126:
            self._input_buffer.append(chr(key))

    def _handle_mouse(self) -> None:
        """Process a mouse event."""
        try:
            _, mx, my, _, bstate = curses.getmouse()
        except curses.error:
            return

        # Check click regions (any button event counts as click)
        for region in self._regions:
            if region.contains(my, mx):
                self._dispatch_click(region.tag)
                return

    def _dispatch_click(self, tag: str) -> None:
        """Route a click tag to the appropriate callback."""
        mapping = {
            TAG_TOGGLE: self.on_toggle,
            TAG_V_DEC: self.on_vertical_dec,
            TAG_V_INC: self.on_vertical_inc,
            TAG_HL_DEC: self.on_h_left_dec,
            TAG_HL_INC: self.on_h_left_inc,
            TAG_HR_DEC: self.on_h_right_dec,
            TAG_HR_INC: self.on_h_right_inc,
            TAG_PROFILE_SWITCH: self.on_profile_switch,
            TAG_PROFILE_SAVE: self.on_profile_save,
            TAG_PROFILE_NEW: self._start_new_profile,
            TAG_PROFILE_DEL: self.on_profile_delete,
        }
        cb = mapping.get(tag)
        if cb:
            cb()

    # ── input helpers ──────────────────────────────────────────

    def start_new_profile_input(self) -> None:
        """Begin interactive new-profile-name input."""
        self._input_prompt = " 输入新配置名称 (武器名字):"
        self._input_buffer = []
        self._needs_new_profile_name = self._submit_new_profile

    def _submit_new_profile(self, name: str) -> None:
        """Called when the user finishes typing a name and presses Enter."""
        name = name.strip()
        if name and self.on_new_profile_submit:
            self.on_new_profile_submit(name)

    def _cancel_input(self) -> None:
        self._needs_new_profile_name = None
        self._input_buffer = []
        self._input_prompt = ""

    @property
    def pending_profile_name(self) -> str:
        """Return the name the user typed (valid only during input)."""
        return "".join(self._input_buffer).strip()
