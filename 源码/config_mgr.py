"""
Configuration manager: weapon profile CRUD with JSON persistence.

Each profile stores:
  - name: weapon name (user-defined)
  - vertical: downward pull intensity
  - horizontal_left: leftward pull intensity
  - horizontal_right: rightward pull intensity
"""

import json
import os
import logging
from typing import Optional

from utils import get_config_path

logger = logging.getLogger(__name__)

DEFAULT_PROFILES = {
    "默认": {
        "vertical": 10,
        "horizontal_left": 0,
        "horizontal_right": 0,
    }
}


class ConfigManager:
    """Manages weapon recoil-compensation profiles stored in a JSON config file."""

    def __init__(self):
        self._path = get_config_path()
        self._data: dict = {}
        self._load()

    # ── persistence ────────────────────────────────────────────

    def _load(self) -> None:
        """Load config from disk, or create defaults if missing/corrupt."""
        if os.path.isfile(self._path):
            try:
                with open(self._path, "r", encoding="utf-8") as f:
                    self._data = json.load(f)
                logger.info(f"Config loaded from {self._path}")
            except (json.JSONDecodeError, OSError) as e:
                logger.warning(f"Corrupt config, resetting: {e}")
                self._data = {}
        else:
            self._data = {}

        # Ensure required keys
        if "profiles" not in self._data or not isinstance(self._data["profiles"], dict):
            self._data["profiles"] = dict(DEFAULT_PROFILES)
        if "current_profile" not in self._data:
            profile_names = list(self._data["profiles"].keys())
            self._data["current_profile"] = profile_names[0] if profile_names else "默认"
            if self._data["current_profile"] not in self._data["profiles"]:
                self._data["profiles"] = dict(DEFAULT_PROFILES)
                self._data["current_profile"] = "默认"
        self._save()

    def _save(self) -> None:
        """Write current config to disk atomically."""
        tmp = self._path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self._path)
        except OSError as e:
            logger.error(f"Failed to save config: {e}")

    # ── profile access ─────────────────────────────────────────

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
        """Return the current profile dict (a copy to prevent accidental mutation)."""
        return dict(self._data["profiles"].get(self.current_profile_name, DEFAULT_PROFILES["默认"]))

    def get_profile(self, name: str) -> Optional[dict]:
        prof = self._data["profiles"].get(name)
        return dict(prof) if prof else None

    def list_profiles(self) -> list[str]:
        return list(self._data["profiles"].keys())

    # ── profile CRUD ───────────────────────────────────────────

    def upsert_profile(self, name: str, vertical: int, h_left: int, h_right: int) -> None:
        """Create or update a weapon profile."""
        self._data["profiles"][name] = {
            "vertical": vertical,
            "horizontal_left": h_left,
            "horizontal_right": h_right,
        }
        # Auto-select newly created profile
        self._data["current_profile"] = name
        self._save()

    def delete_profile(self, name: str) -> bool:
        """Delete a profile. Returns True if deleted, False if not found."""
        if name not in self._data["profiles"]:
            return False
        del self._data["profiles"][name]

        # Point current to another profile if we deleted the active one
        if self._data.get("current_profile") == name or name not in self._data["profiles"]:
            candidates = list(self._data["profiles"].keys())
            self._data["current_profile"] = candidates[0] if candidates else "默认"
        self._save()
        return True

    def rename_profile(self, old_name: str, new_name: str) -> bool:
        """Rename a profile. Returns True on success."""
        if old_name not in self._data["profiles"] or new_name in self._data["profiles"]:
            return False
        self._data["profiles"][new_name] = self._data["profiles"].pop(old_name)
        if self._data.get("current_profile") == old_name:
            self._data["current_profile"] = new_name
        self._save()
        return True

    def update_values(self, vertical: int, h_left: int, h_right: int) -> None:
        """Update current profile values in-place."""
        name = self.current_profile_name
        if name in self._data["profiles"]:
            self._data["profiles"][name]["vertical"] = vertical
            self._data["profiles"][name]["horizontal_left"] = h_left
            self._data["profiles"][name]["horizontal_right"] = h_right
            self._save()
