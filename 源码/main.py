"""
压枪脚本 - Recoil Compensator v1.0 (GUI)
========================================
~ 开关          [ / ] 调垂直强度
Shift+[ 向左移  Shift+] 向右移
鼠标左键按住 = 补偿压枪

水平负值 = 向左拉, 正值 = 向右拉
"""

import ctypes
import ctypes.wintypes
import json
import logging
import os
import sys
import threading
import time
import traceback
import tkinter as tk
from tkinter import ttk
from typing import Optional, Callable

from pynput import keyboard, mouse

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s", datefmt="%H:%M:%S"
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
        _fields_ = [("dx", LONG), ("dy", LONG), ("mouseData", DWORD), ("dwFlags", DWORD), ("time", DWORD), ("dwExtraInfo", WPARAM)]

    class INPUT(ctypes.Structure):
        _fields_ = [("type", DWORD), ("mi", MOUSEINPUT)]

    inp = INPUT()
    inp.type = 0
    inp.mi = MOUSEINPUT(dx, dy, 0, 0x0001, 0, 0)
    ctypes.windll.user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(inp))


# ═══════════════════════════════════════════════════════════════
# CONFIG MANAGER
# ═══════════════════════════════════════════════════════════════

DEFAULT_PROFILES = {"默认": {"vertical": 10, "horizontal": 0}}


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
        for name, prof in self._data.get("profiles", {}).items():
            if "horizontal_left" in prof or "horizontal_right" in prof:
                hl = prof.pop("horizontal_left", 0)
                hr = prof.pop("horizontal_right", 0)
                prof["horizontal"] = hr - hl

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
        return dict(self._data["profiles"].get(self.current_profile_name, DEFAULT_PROFILES["默认"]))

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
VK_OB = 219  # [
VK_CB = 221  # ]


class HooksManager:
    def __init__(self):
        self._kb: Optional[keyboard.Listener] = None
        self._ms: Optional[mouse.Listener] = None
        self._shift = False
        self.on_toggle: Optional[Callable] = None
        self.on_v_up: Optional[Callable] = None
        self.on_v_down: Optional[Callable] = None
        self.on_h_left: Optional[Callable] = None
        self.on_h_right: Optional[Callable] = None
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
            self._kb = keyboard.Listener(on_press=self._kp, on_release=self._kr, suppress=False)
            self._kb.daemon = True
            self._kb.start()
            self._kb.join(timeout=0.5)
            logger.info(f"Kb: {'OK' if self.kb_ok else 'FAIL'}")
        if not self.mouse_ok:
            self._ms = mouse.Listener(on_click=self._mc, suppress=False)
            self._ms.daemon = True
            self._ms.start()
            self._ms.join(timeout=0.5)
            logger.info(f"Mouse: {'OK' if self.mouse_ok else 'FAIL'}")

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
            if vk == VK_OB:
                (self.on_h_left if self._shift else self.on_v_down) and (self.on_h_left() if self._shift else self.on_v_down())
                return
            if vk == VK_CB:
                (self.on_h_right if self._shift else self.on_v_up) and (self.on_h_right() if self._shift else self.on_v_up())
                return
        except Exception as e:
            logger.error(f"kp: {e}")

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
            if pressed:
                self.on_md and self.on_md()
            else:
                self.on_mu and self.on_mu()
        except Exception as e:
            logger.error(f"mc: {e}")


# ═══════════════════════════════════════════════════════════════
# GUI (tkinter)
# ═══════════════════════════════════════════════════════════════


class GuiApp:
    def __init__(self, comp: Compensator, cfg: ConfigManager, hooks: HooksManager):
        self.comp = comp
        self.cfg = cfg
        self.hooks = hooks

        self.root = tk.Tk()
        self.root.title("压枪脚本 v1.0")
        self.root.resizable(False, False)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        # icon
        try:
            self.root.iconbitmap(default="")
        except Exception:
            pass

        # ── state snap ──
        self._active = False
        self._v = 10
        self._h = 0
        self._profile = "默认"

        # ── GUI build ──
        self._build()

        # ── refresh timer ──
        self._update_status()

        # ── callbacks ──
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

    # ── build GUI ──
    def _build(self) -> None:
        f = ttk.Frame(self.root, padding=10)
        f.grid(row=0, column=0, sticky="nsew")

        # -- status --
        sf = ttk.LabelFrame(f, text="状态", padding=5)
        sf.grid(row=0, column=0, columnspan=4, sticky="ew", pady=(0, 8))

        self._status_lbl = ttk.Label(sf, text="● 已关闭", font=("", 12, "bold"))
        self._status_lbl.pack(side="left", padx=(5, 10))

        self._toggle_btn = ttk.Button(sf, text="开启", width=8, command=self._on_toggle_click)
        self._toggle_btn.pack(side="right", padx=5)

        # -- vertical intensity --
        ttk.Label(f, text="垂直强度:").grid(row=1, column=0, sticky="w", padx=(0, 5))
        self._v_dec = ttk.Button(f, text="─", width=3, command=self._on_v_dec)
        self._v_dec.grid(row=1, column=1, padx=2)
        self._v_lbl = ttk.Label(f, text="10", width=5, anchor="center", font=("", 11))
        self._v_lbl.grid(row=1, column=2, padx=5)
        self._v_inc = ttk.Button(f, text="＋", width=3, command=self._on_v_inc)
        self._v_inc.grid(row=1, column=3, padx=2)

        # -- horizontal intensity --
        ttk.Label(f, text="水平偏移:").grid(row=2, column=0, sticky="w", padx=(0, 5), pady=(5, 0))
        self._h_dec = ttk.Button(f, text="─", width=3, command=self._on_h_dec)
        self._h_dec.grid(row=2, column=1, padx=2, pady=(5, 0))
        self._h_lbl = ttk.Label(f, text="0", width=5, anchor="center", font=("", 11))
        self._h_lbl.grid(row=2, column=2, padx=5, pady=(5, 0))
        self._h_inc = ttk.Button(f, text="＋", width=3, command=self._on_h_inc)
        self._h_inc.grid(row=2, column=3, padx=2, pady=(5, 0))

        # -- profile --
        pf = ttk.LabelFrame(f, text="武器配置", padding=5)
        pf.grid(row=3, column=0, columnspan=4, sticky="ew", pady=(8, 0))

        self._profile_lbl = ttk.Label(pf, text="默认", font=("", 10, "bold"))
        self._profile_lbl.pack(side="left", padx=5)

        btn_f = ttk.Frame(pf)
        btn_f.pack(side="right")
        for txt, cb in [("切换", self._on_pswitch), ("保存", self._on_psave),
                        ("新建", self._on_pnew), ("删除", self._on_pdel)]:
            ttk.Button(btn_f, text=txt, width=6, command=cb).pack(side="left", padx=2)

        # -- key hint --
        hint = ttk.Label(f, text="~ 开关  [↓垂直  ]↑垂直  Shift+[←  Shift+]→\n鼠标左键按住 = 压枪补偿",
                         foreground="gray", justify="center")
        hint.grid(row=4, column=0, columnspan=4, pady=(8, 0))

        # -- new-profile input popup --
        self._newp_win: Optional[tk.Toplevel] = None

        # keyboard bindings
        self.root.bind("<Key>", self._on_key)

    # ── update loop ──
    def _update_status(self) -> None:
        try:
            self._active = self.comp.active
            self._v, self._h = self.comp.get_all()
            self._profile = self.cfg.current_profile_name

            # status
            if self._active:
                self._status_lbl.config(text="● 已开启", foreground="green")
                self._toggle_btn.config(text="关闭")
            else:
                self._status_lbl.config(text="● 已关闭", foreground="red")
                self._toggle_btn.config(text="开启")

            # values
            self._v_lbl.config(text=str(self._v))
            self._h_lbl.config(text=str(self._h))
            self._profile_lbl.config(text=self._profile)
        except Exception:
            pass
        finally:
            try:
                self.root.after(200, self._update_status)
            except Exception:
                pass

    # ── callbacks ──
    def _on_toggle_click(self) -> None:
        self.on_toggle and self.on_toggle()

    def _on_v_dec(self) -> None:
        self.on_v_dec and self.on_v_dec()

    def _on_v_inc(self) -> None:
        self.on_v_inc and self.on_v_inc()

    def _on_h_dec(self) -> None:
        self.on_h_dec and self.on_h_dec()

    def _on_h_inc(self) -> None:
        self.on_h_inc and self.on_h_inc()

    def _on_pswitch(self) -> None:
        self.on_pswitch and self.on_pswitch()

    def _on_psave(self) -> None:
        self.on_psave and self.on_psave()

    def _on_pnew(self) -> None:
        if self._newp_win and self._newp_win.winfo_exists():
            self._newp_win.lift()
            return
        self._newp_win = tk.Toplevel(self.root)
        self._newp_win.title("新建配置")
        self._newp_win.resizable(False, False)
        self._newp_win.transient(self.root)
        self._newp_win.grab_set()

        ttk.Label(self._newp_win, text="武器名字:").pack(padx=10, pady=(10, 0))
        entry = ttk.Entry(self._newp_win, width=25)
        entry.pack(padx=10, pady=5)
        entry.focus_set()

        def _submit():
            name = entry.get().strip()
            if name:
                self.on_newp_submit and self.on_newp_submit(name)
            self._newp_win.destroy()
            self._newp_win = None

        def _cancel():
            self._newp_win.destroy()
            self._newp_win = None

        bf = ttk.Frame(self._newp_win)
        bf.pack(pady=(0, 10))
        ttk.Button(bf, text="确认", command=_submit).pack(side="left", padx=5)
        ttk.Button(bf, text="取消", command=_cancel).pack(side="left", padx=5)
        entry.bind("<Return>", lambda e: _submit())
        entry.bind("<Escape>", lambda e: _cancel())

        cx = self.root.winfo_x() + self.root.winfo_width() // 2 - 75
        cy = self.root.winfo_y() + self.root.winfo_height() // 2 - 50
        self._newp_win.geometry(f"250x110+{cx}+{cy}")

    def _on_pdel(self) -> None:
        self.on_pdel and self.on_pdel()

    def _on_key(self, event: tk.Event) -> None:
        """Handle keyboard input in the GUI window."""
        if event.keysym == "asciitilde" or event.keysym == "grave":
            self.on_toggle and self.on_toggle()
        elif event.keysym == "bracketleft":
            self.on_v_dec and self.on_v_dec()
        elif event.keysym == "bracketright":
            self.on_v_inc and self.on_v_inc()
        elif event.keysym == "braceleft":
            self.on_h_dec and self.on_h_dec()
        elif event.keysym == "braceright":
            self.on_h_inc and self.on_h_inc()

    def _on_close(self) -> None:
        self.root.quit()
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


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
    app = GuiApp(comp, cfg, hooks)

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
        if comp.active:
            comp.horiz -= 1
            logger.info(f"H← {comp.horiz}")

    def _h_right():
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
        logger.info(f"P→{names[idx]}")

    def _psave():
        cfg.update_values(*comp.get_all())
        logger.info(f"Saved:{cfg.current_profile_name}")

    def _pnew():
        pass  # triggered by GUI popup

    def _pdel():
        n = cfg.current_profile_name
        if n == "默认":
            _msgbox("压枪脚本", "无法删除默认配置。")
            return
        cfg.delete_profile(n)
        p2 = cfg.current_profile
        comp.set_all(p2.get("vertical", 10), p2.get("horizontal", 0))
        logger.info(f"Del:{n}")

    def _on_newp(name: str):
        cfg.upsert_profile(name, *comp.get_all())
        logger.info(f"New:{name}")

    app.on_pswitch = _pswitch
    app.on_psave = _psave
    app.on_pnew = _pnew
    app.on_pdel = _pdel
    app.on_newp_submit = _on_newp

    hooks.start()
    comp.start()
    logger.info("OK")

    try:
        app.run()
    except Exception:
        logger.exception("GUI error")
    finally:
        comp.stop()
        hooks.stop()
        cfg.update_values(*comp.get_all())
        logger.info("Exit")


def _entry() -> None:
    try:
        main()
    except Exception:
        traceback.print_exc()
        print("\n" + "=" * 50)
        print("程式发生错误，上述为详细错误信息。")
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
