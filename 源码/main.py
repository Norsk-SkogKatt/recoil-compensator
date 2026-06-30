"""
Entry point for the Recoil Compensator.

Flow:
  1. Check / request admin privileges
  2. Enforce single-instance
  3. Initialise config, compensator, hooks, TUI
  4. Wire callbacks between all modules
  5. Run TUI (blocking)
  6. Clean shutdown
"""

import curses
import logging
import sys

from utils import ensure_single_instance, show_message_box
from config_mgr import ConfigManager
from compensator import Compensator
from hooks import HooksManager
from tui_app import TuiApp

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("main")


def main() -> None:
    """Application entry point."""

    # ── 1. Single-instance check ───────────────────────────────
    if not ensure_single_instance():
        show_message_box(
            "压枪脚本",
            "程式已在运行中。\n请勿重复开启。",
            icon=0x30,  # warning
        )
        sys.exit(0)

    # ── 3. Initialise components ───────────────────────────────
    config = ConfigManager()
    comp = Compensator()
    hooks = HooksManager()
    app = TuiApp(compensator=comp, config_mgr=config)

    # Load initial intensities from the current profile
    profile = config.current_profile
    comp.set_intensities(
        profile.get("vertical", 10),
        profile.get("horizontal_left", 0),
        profile.get("horizontal_right", 0),
    )

    # ── 4. Wire callbacks ──────────────────────────────────────

    # ── hooks → compensator ──
    _toggle_last = 0.0

    def _toggle():
        """Toggle compensator on/off with debounce.

        Both the global pynput hook AND the TUI key handler may fire
        for a single ~ keypress.  The debounce collapses rapid
        duplicates (< 150 ms) into one toggle.
        """
        nonlocal _toggle_last
        import time as _time
        now = _time.time()
        if now - _toggle_last < 0.15:
            return
        _toggle_last = now
        comp.active = not comp.active
        status = "开启" if comp.active else "关闭"
        logger.info(f"压枪 {status}")

    def _v_up():
        if not comp.active:
            return
        comp.intensity_vertical += 1
        v, hl, hr = comp.get_intensities()
        logger.info(f"垂直强度 ↑ {v}")

    def _v_down():
        if not comp.active:
            return
        comp.intensity_vertical -= 1
        v, hl, hr = comp.get_intensities()
        logger.info(f"垂直强度 ↓ {v}")

    def _hl_up():
        if not comp.active:
            return
        comp.intensity_h_left += 1
        v, hl, hr = comp.get_intensities()
        logger.info(f"左水平强度 ↑ {hl}")

    def _hr_up():
        if not comp.active:
            return
        comp.intensity_h_right += 1
        v, hl, hr = comp.get_intensities()
        logger.info(f"右水平强度 ↑ {hr}")

    hooks.on_toggle = _toggle
    hooks.on_vertical_down = _v_down
    hooks.on_vertical_up = _v_up
    hooks.on_h_left_up = _hl_up
    hooks.on_h_right_up = _hr_up
    hooks.on_mouse_down = lambda: setattr(comp, "mouse_down", True)
    hooks.on_mouse_up = lambda: setattr(comp, "mouse_down", False)

    # ── TUI → compensator / config ──
    app.on_toggle = _toggle
    app.on_vertical_inc = _v_up
    app.on_vertical_dec = _v_down
    app.on_h_left_inc = _hl_up
    app.on_h_left_dec = lambda: (comp.active and setattr(comp, "intensity_h_left", comp.intensity_h_left - 1))
    app.on_h_right_inc = _hr_up
    app.on_h_right_dec = lambda: (comp.active and setattr(comp, "intensity_h_right", comp.intensity_h_right - 1))

    def _profile_switch():
        names = config.list_profiles()
        if not names:
            return
        cur = config.current_profile_name
        idx = (names.index(cur) + 1) % len(names) if cur in names else 0
        config.current_profile_name = names[idx]
        p = config.current_profile
        comp.set_intensities(
            p.get("vertical", 10),
            p.get("horizontal_left", 0),
            p.get("horizontal_right", 0),
        )
        logger.info(f"切换到配置: {names[idx]}")

    def _profile_save():
        v, hl, hr = comp.get_intensities()
        config.update_values(v, hl, hr)
        logger.info(f"配置已保存: {config.current_profile_name}")

    def _profile_new():
        # Start the input mode in TUI
        app.start_new_profile_input()

    def _profile_delete():
        name = config.current_profile_name
        if name == "默认":
            show_message_box("压枪脚本", "无法删除默认配置。")
            return
        config.delete_profile(name)
        p = config.current_profile
        comp.set_intensities(
            p.get("vertical", 10),
            p.get("horizontal_left", 0),
            p.get("horizontal_right", 0),
        )
        logger.info(f"配置已删除: {name}")

    app.on_profile_switch = _profile_switch
    app.on_profile_save = _profile_save
    app.on_profile_new = _profile_new
    app.on_profile_delete = _profile_delete

    def _on_new_profile_name(name: str):
        """Called when the user finishes typing a new profile name."""
        v, hl, hr = comp.get_intensities()
        config.upsert_profile(name, v, hl, hr)
        logger.info(f"新建配置: {name}")

    app.on_new_profile_submit = _on_new_profile_name

    # ── 5. Start workers & run TUI ─────────────────────────────
    hooks.start()
    comp.start()

    logger.info("压枪脚本已启动")

    try:
        curses.wrapper(app.run)
    except KeyboardInterrupt:
        pass
    finally:
        # ── 6. Clean shutdown ──────────────────────────────────
        logger.info("正在关闭...")
        comp.stop()
        hooks.stop()
        # Save current state
        v, hl, hr = comp.get_intensities()
        config.update_values(v, hl, hr)
        logger.info("压枪脚本已退出")


def _run_with_error_pause() -> None:
    """
    Run main() and catch any unhandled exception.
    Prints the full traceback and waits for a key press before closing,
    so the user can read the error in the console.
    """
    try:
        main()
    except Exception:
        import traceback
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
    _run_with_error_pause()
