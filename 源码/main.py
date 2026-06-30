"""
压枪脚本 - Recoil Compensator v1.0 (All-in-one)
================================================
~ 开关          [ / ] 调垂直强度
Shift+[ 向左移  Shift+] 向右移
鼠标左键按住 = 补偿压枪
鼠标点击按钮 = 操作设定

水平负值 = 向左拉, 正值 = 向右拉
"""

import atexit
import ctypes
import ctypes.wintypes
import curses
import curses.ascii
import json
import logging
import os
import sys
import threading
import time
import traceback
from typing import Optional, Callable

from pynput import keyboard, mouse

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("rc")

# ═══════════════════════════════════════════════════════════════
# UTILITIES
# ═══════════════════════════════════════════════════════════════


def _is_admin() -> bool:
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False


def _single_instance(name: str = "Global\\RecoilCompensator_UniqMutex") -> bool:
    try:
        k32 = ctypes.windll.kernel32
        m = k32.CreateMutexW(None, False, name)
        if k32.GetLastError() == 0xB7:
            k32.CloseHandle(m)
            return False
        return True
    except Exception:
        return True


def _msgbox(title: str, msg: str, icon: int = 0x40) -> None:
    ctypes.windll.user32.MessageBoxW(0, msg, title, icon | 0x1000)


def _cfg_dir() -> str:
    d = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "压枪脚本")
    os.makedirs(d, exist_ok=True)
    return d


def _cfg_path() -> str:
    return os.path.join(_cfg_dir(), "config.json")


def _move_mouse(dx: int, dy: int) -> None:
    LONG = ctypes.wintypes.LONG
    DWORD = ctypes.wintypes.DWORD
    WPARAM = ctypes.wintypes.WPARAM

    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [
            ("dx", LONG), ("dy", LONG), ("mouseData", DWORD),
            ("dwFlags", DWORD), ("time", DWORD), ("dwExtraInfo", WPARAM),
        ]

    class INPUT(ctypes.Structure):
        _fields_ = [("type", DWORD), ("mi", MOUSEINPUT)]

    inp = INPUT()
    inp.type = 0
    inp.mi = MOUSEINPUT(dx, dy, 0, 0x0001, 0, 0)
    ctypes.windll.user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(inp))


# ═══════════════════════════════════════════════════════════════
# CONFIG MANAGER
# ═══════════════════════════════════════════════════════════════

DEFAULT_PROFILES = {
    "默认": {"vertical": 10, "horizontal": 0},
}


class ConfigManager:
    def __init__(self):
        self._path = _cfg_path()
        self._data: dict = {}
        self._load()

    def _load(self) -> None:
        if os.path.isfile(self._path):
            try:
                with open(self._path, "r", encoding="utf-8") as f:
                    self._data = json.load(f)
            except (json.JSONDecodeError, OSError):
                self._data = {}
        else:
            self._data = {}
        self._migrate()
        if "profiles" not in self._data or not isinstance(self._data["profiles"], dict):
            self._data["profiles"] = dict(DEFAULT_PROFILES)
        if "current_profile" not in self._data:
            names = list(self._data["profiles"].keys())
            self._data["current_profile"] = names[0] if names else "默认"
        self._save()

    def _migrate(self) -> None:
        profiles = self._data.get("profiles", {})
        changed = False
        for name, prof in profiles.items():
            if "horizontal_left" in prof or "horizontal_right" in prof:
                hl = prof.pop("horizontal_left", 0)
                hr = prof.pop("horizontal_right", 0)
                prof["horizontal"] = hr - hl
                changed = True
        if changed:
            logger.info("Migrated old profile format -> single horizontal")

    def _save(self) -> None:
        tmp = self._path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self._path)
        except OSError:
            pass

    @property
    def current_profile_name(self) -> str:
        return self._data.get("current_profile", "默认")

    @current_profile_name.setter
    def current_profile_name(self, name: str) -> None:
        if name in self._data["profiles"]:
            self._data["current_profile"] = name
            self._save()

    @property
    def current_profile(self) -> dict:
        return dict(
            self._data["profiles"].get(
                self.current_profile_name, DEFAULT_PROFILES["默认"]
            )
        )

    def list_profiles(self) -> list[str]:
        return list(self._data["profiles"].keys())

    def upsert_profile(self, name: str, v: int, h: int) -> None:
        self._data["profiles"][name] = {"vertical": v, "horizontal": h}
        self._data["current_profile"] = name
        self._save()

    def delete_profile(self, name: str) -> bool:
        if name not in self._data["profiles"]:
            return False
        del self._data["profiles"][name]
        cur = self._data.get("current_profile")
        if cur == name or name not in self._data["profiles"]:
            cand = list(self._data["profiles"].keys())
            self._data["current_profile"] = cand[0] if cand else "默认"
        self._save()
        return True

    def update_values(self, v: int, h: int) -> None:
        name = self.current_profile_name
        if name in self._data["profiles"]:
            self._data["profiles"][name]["vertical"] = v
            self._data["profiles"][name]["horizontal"] = h
            self._save()


# ═══════════════════════════════════════════════════════════════
# COMPENSATOR
# ═══════════════════════════════════════════════════════════════

TICK_MS = 0.010


class Compensator:
    def __init__(self):
        self._lock = threading.Lock()
        self._active = False
        self._md = False
        self._v = 10
        self._h = 0
        self._worker: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._mev = threading.Event()

    @property
    def active(self) -> bool:
        return self._active

    @active.setter
    def active(self, v: bool) -> None:
        with self._lock:
            self._active = v
            if not v:
                self._md = False
                self._mev.clear()

    @property
    def mouse_down(self) -> bool:
        return self._md

    @mouse_down.setter
    def mouse_down(self, v: bool) -> None:
        with self._lock:
            self._md = v
            if v and self._active:
                self._mev.set()
            else:
                self._mev.clear()

    @property
    def vert(self) -> int:
        return self._v

    @vert.setter
    def vert(self, val: int) -> None:
        with self._lock:
            self._v = max(0, min(100, val))

    @property
    def horiz(self) -> int:
        return self._h

    @horiz.setter
    def horiz(self, val: int) -> None:
        with self._lock:
            self._h = max(-100, min(100, val))

    def set_all(self, v: int, h: int) -> None:
        with self._lock:
            self._v = max(0, min(100, v))
            self._h = max(-100, min(100, h))

    def get_all(self):
        with self._lock:
            return (self._v, self._h)

    def start(self) -> None:
        if self._worker and self._worker.is_alive():
            return
        self._stop.clear()
        self._mev.clear()
        self._worker = threading.Thread(target=self._run, daemon=True)
        self._worker.start()

    def stop(self) -> None:
        self._stop.set()
        self._mev.set()
        if self._worker and self._worker.is_alive():
            self._worker.join(timeout=2)

    def _run(self) -> None:
        while not self._stop.is_set():
            self._mev.wait()
            if self._stop.is_set():
                break
            while not self._stop.is_set():
                with self._lock:
                    if not self._active or not self._md:
                        self._mev.clear()
                        break
                    v, h = self._v, self._h
                if v != 0 or h != 0:
                    _move_mouse(h, v)
                time.sleep(TICK_MS)


# ═══════════════════════════════════════════════════════════════
# HOOKS (pynput)
# ═══════════════════════════════════════════════════════════════

VK_TILDE = 192
VK_OB = 219   # [
VK_CB = 221   # ]


class HooksManager:
    def __init__(self):
        self._kb: Optional[keyboard.Listener] = None
        self._ms: Optional[mouse.Listener] = None
        self._shift = False

        self.on_toggle: Optional[Callable] = None
        self.on_v_up: Optional[Callable] = None
        self.on_v_down: Optional[Callable] = None
        self.on_h_left: Optional[Callable] = None   # Shift+[ = left
        self.on_h_right: Optional[Callable] = None   # Shift+] = right
        self.on_md: Optional[Callable] = None
        self.on_mu: Optional[Callable] = None

    @property
    def kb_ok(self) -> bool:
        return bool(self._kb and self._kb.running)

    @property
    def mouse_ok(self) -> bool:
        return bool(self._ms and self._ms.running)

    def start(self) -> None:
        if not self.kb_ok:
            self._kb = keyboard.Listener(
                on_press=self._kp, on_release=self._kr, suppress=False
            )
            self._kb.daemon = True
            self._kb.start()
            self._kb.join(timeout=0.5)
            logger.info(f"Kb-hook: {'OK' if self.kb_ok else 'FAILED'}")

        if not self.mouse_ok:
            self._ms = mouse.Listener(on_click=self._mc, suppress=False)
            self._ms.daemon = True
            self._ms.start()
            self._ms.join(timeout=0.5)
            logger.info(f"Mouse-hook: {'OK' if self.mouse_ok else 'FAILED'}")

    def stop(self) -> None:
        if self.kb_ok:
            self._kb.stop()
        if self.mouse_ok:
            self._ms.stop()

    def _kp(self, key) -> None:
        try:
            if key in (keyboard.Key.shift, keyboard.Key.shift_l, keyboard.Key.shift_r):
                self._shift = True
                return
            vk = getattr(key, "vk", None)
            ch = getattr(key, "char", None)
            if vk == VK_TILDE or ch in ("`", "~"):
                self.on_toggle and self.on_toggle()
                return
            if vk is None:
                return
            if vk == VK_OB:  # [
                (self.on_h_left if self._shift else self.on_v_down) and (
                    self.on_h_left() if self._shift else self.on_v_down()
                )
                return
            if vk == VK_CB:  # ]
                (self.on_h_right if self._shift else self.on_v_up) and (
                    self.on_h_right() if self._shift else self.on_v_up()
                )
                return
        except Exception as e:
            logger.error(f"kp err: {e}")

    def _kr(self, key) -> None:
        try:
            if key in (keyboard.Key.shift, keyboard.Key.shift_l, keyboard.Key.shift_r):
                self._shift = False
        except Exception:
            pass

    def _mc(self, x, y, button, pressed) -> None:
        try:
            if button != mouse.Button.left:
                return
            (self.on_md if pressed else self.on_mu) and (
                self.on_md() if pressed else self.on_mu()
            )
        except Exception as e:
            logger.error(f"mc err: {e}")


# ═══════════════════════════════════════════════════════════════
# TUI (curses)
# ═══════════════════════════════════════════════════════════════

C_NORM = 1
C_HL = 2
C_GREEN = 3
C_RED = 4
C_YELLOW = 5
C_BTN = 6
C_BAR = 7
C_HDR = 8

T_TG = "tg"
T_VD = "vd"
T_VI = "vi"
T_HD = "hd"
T_HI = "hi"
T_PS = "ps"
T_PV = "pv"
T_PN = "pn"
T_PD = "pd"


class Region:
    __slots__ = ("tag", "y", "x0", "x1")

    def __init__(self, tag: str, y: int, x0: int, x1: int):
        self.tag = tag
        self.y = y
        self.x0 = x0
        self.x1 = x1

    def hit(self, my: int, mx: int) -> bool:
        return self.y == my and self.x0 <= mx <= self.x1


class TuiApp:
    def __init__(self, comp: Compensator, cfg: ConfigManager):
        self.comp = comp
        self.cfg = cfg
        self.scr: Optional[curses.window] = None
        self.rows, self.cols = 24, 80
        self.regions: list[Region] = []

        self.active = False
        self.v = 10
        self.h = 0
        self.profile = "默认"
        self.kbok = False
        self.mok = False

        self.on_toggle: Optional[Callable] = None
        self.on_v_inc: Optional[Callable] = None
        self.on_v_dec: Optional[Callable] = None
        self.on_h_inc: Optional[Callable] = None
        self.on_h_dec: Optional[Callable] = None
        self.on_pswitch: Optional[Callable] = None
        self.on_psave: Optional[Callable] = None
        self.on_pnew: Optional[Callable] = None
        self.on_pdel: Optional[Callable] = None
        self.on_newp_submit: Optional[Callable[[str], None]] = None

        self._inp = False
        self._buf: list[str] = []

    def run(self, scr) -> None:
        self.scr = scr
        self._colours()
        self._mouse()
        self.rows, self.cols = scr.getmaxyx()
        scr.timeout(200)

        while True:
            self._snap()
            self.rows, self.cols = scr.getmaxyx()
            self.regions.clear()
            self._draw()
            scr.refresh()

            key = scr.getch()
            if key == curses.KEY_RESIZE:
                self.rows, self.cols = scr.getmaxyx()
                continue
            if key == 27:
                if self._inp:
                    self._inp = False
                    self._buf = []
                else:
                    break
            if self._inp:
                self._inkey(key)
            else:
                self._nkey(key)

    def _colours(self) -> None:
        curses.start_color()
        curses.use_default_colors()
        curses.init_pair(C_NORM, -1, -1)
        curses.init_pair(C_HL, curses.COLOR_CYAN, -1)
        curses.init_pair(C_GREEN, curses.COLOR_GREEN, -1)
        curses.init_pair(C_RED, curses.COLOR_RED, -1)
        curses.init_pair(C_YELLOW, curses.COLOR_YELLOW, -1)
        curses.init_pair(C_BTN, curses.COLOR_BLACK, curses.COLOR_WHITE)
        curses.init_pair(C_BAR, curses.COLOR_GREEN, curses.COLOR_GREEN)
        curses.init_pair(C_HDR, curses.COLOR_WHITE, curses.COLOR_BLUE)

    def _mouse(self) -> None:
        try:
            curses.mousemask(curses.ALL_MOUSE_EVENTS | curses.REPORT_MOUSE_POSITION)
        except curses.error:
            pass
        if self.scr:
            self.scr.keypad(True)

    def _snap(self) -> None:
        self.active = self.comp.active
        self.v, self.h = self.comp.get_all()
        self.profile = self.cfg.current_profile_name

    @property
    def lm(self) -> int:
        return 3

    @property
    def bw(self) -> int:
        return max(5, self.cols - 32)

    def _draw(self) -> None:
        s = self.scr
        s.erase()
        y = 0
        y = self._header(y)
        y += 1
        y = self._status(y)
        y += 1
        y = self._bars(y)
        y += 1
        y = self._profile(y)
        y += 1
        self._sep(y)
        y += 1
        self._help(y)
        if self._inp:
            self._input_box()

    def _header(self, y: int) -> int:
        t = " 压枪脚本 - Recoil Compensator v1.0 "
        try:
            self.scr.attron(curses.color_pair(C_HDR))
            self.scr.addstr(y, 0, " " * self.cols)
            self.scr.addstr(y, max(0, (self.cols - len(t)) // 2), t)
            self.scr.attroff(curses.color_pair(C_HDR))
        except curses.error:
            pass
        return y + 1

    def _status(self, y: int) -> int:
        s = self.scr
        lm = self.lm
        try:
            label = "● 开启中" if self.active else "○ 已关闭"
            cl = C_GREEN if self.active else C_RED
            s.attron(curses.color_pair(cl) | curses.A_BOLD)
            s.addstr(y, lm, "状态: ")
            s.addstr(y, lm + 6, label)
            s.attroff(curses.color_pair(cl) | curses.A_BOLD)

            btn = "[点击切换]"
            bx = self.cols - lm - len(btn)
            s.attron(curses.color_pair(C_BTN))
            s.addstr(y, bx, btn)
            s.attroff(curses.color_pair(C_BTN))
            self.regions.append(Region(T_TG, y, bx, bx + len(btn) - 1))
        except curses.error:
            pass
        return y + 1

    def _draw_bar(self, y: int, x: int, w: int, val: int, mx: int = 100) -> None:
        fill = int((abs(val) / mx) * w) if mx > 0 else 0
        fill = min(fill, w)
        bar = "█" * fill + "░" * (w - fill)
        try:
            self.scr.addstr(y, x, bar[:w])
        except curses.error:
            pass

    def _bars(self, y: int) -> int:
        s = self.scr
        lm = self.lm
        w = self.bw

        items = [
            ("垂直:", self.v, T_VD, T_VI, 100),
            ("水平:", self.h, T_HD, T_HI, 100),
        ]

        for i, (lbl, val, td, ti, mx) in enumerate(items):
            cy = y + i
            try:
                s.addstr(cy, lm, lbl)
                db = "[-]"
                s.attron(curses.color_pair(C_BTN))
                s.addstr(cy, lm + 8, db)
                s.attroff(curses.color_pair(C_BTN))
                self.regions.append(Region(td, cy, lm + 8, lm + 8 + len(db) - 1))
                self._draw_bar(cy, lm + 13, w, val, mx)
                prefix = "-" if val < 0 else "+" if val > 0 else " "
                vs = f"{prefix}{abs(val):3d}"
                s.addstr(cy, lm + 13 + w + 1, vs)
                ib = "[+]"
                s.attron(curses.color_pair(C_BTN))
                s.addstr(cy, lm + 13 + w + 1 + len(vs) + 1, ib)
                s.attroff(curses.color_pair(C_BTN))
                x1 = lm + 13 + w + 1 + len(vs) + 1
                self.regions.append(Region(ti, cy, x1, x1 + len(ib) - 1))
            except curses.error:
                pass
        return y + len(items)

    def _profile(self, y: int) -> int:
        s = self.scr
        lm = self.lm
        try:
            s.addstr(y, lm, "当前武器:")
            s.attron(curses.A_BOLD | curses.color_pair(C_HL))
            s.addstr(y, lm + 10, f" {self.profile} ")
            s.attroff(curses.A_BOLD | curses.color_pair(C_HL))
        except curses.error:
            pass

        by = y + 1
        btns = [
            (T_PS, " [切换] "),
            (T_PV, " [保存] "),
            (T_PN, " [新建] "),
            (T_PD, " [删除] "),
        ]
        bx = lm
        for tag, text in btns:
            try:
                s.attron(curses.color_pair(C_BTN))
                s.addstr(by, bx, text)
                s.attroff(curses.color_pair(C_BTN))
                self.regions.append(Region(tag, by, bx, bx + len(text) - 1))
                bx += len(text) + 1
            except curses.error:
                pass
        return by + 1

    def _sep(self, y: int) -> None:
        try:
            self.scr.addstr(y, 1, "─" * (self.cols - 2), curses.color_pair(C_NORM))
        except curses.error:
            pass

    def _help(self, y: int) -> None:
        lines = [
            " ~ 开关  |  [ 减弱垂直  ] 增强垂直  |  Shift+[ 左移  Shift+] 右移",
            " 鼠标左键按住 = 启动压枪补偿  |  Esc = 退出  |  可用滑鼠点击按钮",
        ]
        for i, line in enumerate(lines):
            try:
                self.scr.addstr(y + i, self.lm, line, curses.color_pair(C_NORM))
            except curses.error:
                pass

    def _input_box(self) -> None:
        s = self.scr
        y = self.rows - 4
        prompt = "输入新配置名称 (武器名字):"
        inp = "".join(self._buf)
        try:
            s.attron(curses.color_pair(C_HL) | curses.A_BOLD)
            s.addstr(y, self.lm, prompt)
            s.attroff(curses.color_pair(C_HL) | curses.A_BOLD)
            s.addstr(y + 1, self.lm + 2, inp + "_")
            s.addstr(y + 2, self.lm, "Enter=确认  Esc=取消")
        except curses.error:
            pass

    def _nkey(self, key: int) -> None:
        if key == curses.KEY_MOUSE:
            self._mclick()
            return
        if key in (ord("~"), 96):
            self.on_toggle and self.on_toggle()
        elif key in (ord("["), 219):
            self.on_v_dec and self.on_v_dec()
        elif key in (ord("]"), 221):
            self.on_v_inc and self.on_v_inc()
        elif key in (ord("{"), 123):
            self.on_h_inc and self.on_h_inc()
        elif key in (ord("}"), 125):
            self.on_h_dec and self.on_h_dec()

    def _inkey(self, key: int) -> None:
        if key in (10, 13):
            name = "".join(self._buf).strip()
            if name and self.on_newp_submit:
                self.on_newp_submit(name)
            self._inp = False
            self._buf = []
        elif key == 27:
            self._inp = False
            self._buf = []
        elif key in (curses.KEY_BACKSPACE, 127, 8):
            if self._buf:
                self._buf.pop()
        elif 32 <= key <= 126:
            self._buf.append(chr(key))

    def _mclick(self) -> None:
        try:
            _, mx, my, _, _ = curses.getmouse()
        except curses.error:
            return
        for r in self.regions:
            if r.hit(my, mx):
                self._route(r.tag)
                return

    def _route(self, tag: str) -> None:
        m = {
            T_TG: self.on_toggle,
            T_VD: self.on_v_dec,
            T_VI: self.on_v_inc,
            T_HD: self.on_h_dec,
            T_HI: self.on_h_inc,
            T_PS: self.on_pswitch,
            T_PV: self.on_psave,
            T_PN: self._newp,
            T_PD: self.on_pdel,
        }
        cb = m.get(tag)
        cb and cb()

    def _newp(self) -> None:
        self._inp = True
        self._buf = []


# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════

def main() -> None:
    if not _single_instance():
        _msgbox("压枪脚本", "程式已在运行中。\n请勿重复开启。", 0x30)
        sys.exit(0)

    cfg = ConfigManager()
    comp = Compensator()
    hooks = HooksManager()
    app = TuiApp(comp, cfg)

    p = cfg.current_profile
    comp.set_all(p.get("vertical", 10), p.get("horizontal", 0))

    _ts = 0.0

    def _toggle():
        nonlocal _ts
        t = time.time()
        if t - _ts < 0.15:
            return
        _ts = t
        comp.active = not comp.active
        logger.info(f"{'开启' if comp.active else '关闭'}")

    def _v_up():
        if comp.active:
            comp.vert += 1
            logger.info(f"V↑ {comp.vert}")

    def _v_down():
        if comp.active:
            comp.vert -= 1
            logger.info(f"V↓ {comp.vert}")

    def _h_left():
        """Shift+[ → lower horizontal (more left)"""
        if comp.active:
            comp.horiz -= 1
            logger.info(f"H← {comp.horiz}")

    def _h_right():
        """Shift+] → raise horizontal (more right)"""
        if comp.active:
            comp.horiz += 1
            logger.info(f"H→ {comp.horiz}")

    hooks.on_toggle = _toggle
    hooks.on_v_up = _v_up
    hooks.on_v_down = _v_down
    hooks.on_h_left = _h_left
    hooks.on_h_right = _h_right
    hooks.on_md = lambda: setattr(comp, "mouse_down", True)
    hooks.on_mu = lambda: setattr(comp, "mouse_down", False)

    app.on_toggle = _toggle
    app.on_v_inc = _v_up
    app.on_v_dec = _v_down
    app.on_h_inc = _h_right
    app.on_h_dec = _h_left

    def _pswitch():
        names = cfg.list_profiles()
        if not names:
            return
        cur = cfg.current_profile_name
        idx = (names.index(cur) + 1) % len(names) if cur in names else 0
        cfg.current_profile_name = names[idx]
        p2 = cfg.current_profile
        comp.set_all(p2.get("vertical", 10), p2.get("horizontal", 0))
        logger.info(f"Profile→{names[idx]}")

    def _psave():
        v, h = comp.get_all()
        cfg.update_values(v, h)
        logger.info(f"Saved:{cfg.current_profile_name}")

    def _pnew():
        app._newp()

    def _pdel():
        n = cfg.current_profile_name
        if n == "默认":
            _msgbox("压枪脚本", "无法删除默认配置。")
            return
        cfg.delete_profile(n)
        p2 = cfg.current_profile
        comp.set_all(p2.get("vertical", 10), p2.get("horizontal", 0))
        logger.info(f"Deleted:{n}")

    def _on_newp(name: str):
        v, h = comp.get_all()
        cfg.upsert_profile(name, v, h)
        logger.info(f"New profile:{name}")

    app.on_pswitch = _pswitch
    app.on_psave = _psave
    app.on_pnew = _pnew
    app.on_pdel = _pdel
    app.on_newp_submit = _on_newp

    hooks.start()
    comp.start()
    logger.info("Startup OK")

    try:
        curses.wrapper(app.run)
    except KeyboardInterrupt:
        pass
    finally:
        logger.info("Shutting down...")
        comp.stop()
        hooks.stop()
        cfg.update_values(*comp.get_all())
        logger.info("Exited")


def _entry() -> None:
    try:
        main()
    except Exception:
        traceback.print_exc()
        print("\n" + "=" * 50)
        print("程式发生错误，上述为详细错误信息。")
        print("请截图或记录后向开发者反馈。")
        print("=" * 50)
        print("\n按任意键关闭窗口...", end="", flush=True)
        try:
            import msvcrt
            msvcrt.getch()
        except ImportError:
            input()
        sys.exit(1)


if __name__ == "__main__":
    _entry()
