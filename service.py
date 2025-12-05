from typing import List, Dict, Any
import subprocess
import logging

import onigiri  # engine module
from models import ConfigModel, ProfileModel, TileModel

logger = logging.getLogger(__name__)


class OnigiriService:
    """
    Thin wrapper around the onigiri engine module so the UI
    doesn't call onigiri.* all over the place.
    """
    # noinspection PyMethodMayBeStatic
    def load_config(self) -> ConfigModel:
        """Load config from JSON and wrap it in ConfigModel."""
        raw = onigiri.load_profiles()
        return ConfigModel(raw)

    # noinspection PyMethodMayBeStatic
    def save_config(self, config: ConfigModel) -> None:
        """Persist ConfigModel back to JSON."""
        onigiri.save_profiles(config.to_dict())

    # ----- profile / rules -----

    def apply_profile_rules(self, config: ConfigModel, profile: ProfileModel) -> None:
        """
        Save config and (re)apply KWin rules for this profile.

        Note: apply_profile() now clears ALL Onigiri/KWinTiler rules first,
        so only this profile's layout is active.
        """
        name = profile.name
        if not name:
            raise ValueError("Profile needs a name before applying rules.")

        self.save_config(config)
        onigiri.apply_profile(name)

    def launch_profile_apps(self, config: ConfigModel, profile: ProfileModel) -> None:
        """
        Save config, re-apply KWin rules for this profile and launch all commands.

        This makes sure that any recent geometry changes (including tile gaps,
        layout edits, etc.) are actually reflected in the KWin rules *before*
        we start the apps.
        """
        name = profile.name
        if not name:
            raise ValueError("Profile needs a name before launching apps.")

        # 1) Persist current config (tiles, gap, monitor, etc.)
        self.save_config(config)

        # 2) Re-apply rules for this profile so KWin uses the latest geometry
        try:
            onigiri.apply_profile(name)
        except Exception as e:
            logger.error(
                "Failed to apply rules for profile '%s' before launching apps: %s",
                name,
                e,
            )

        # 3) Launch the configured commands/apps
        onigiri.launch_profile_commands(name)

    # noinspection PyMethodMayBeStatic
    def remove_profile_rules(self, profile: ProfileModel) -> None:
        """
        Remove KWin rules for a profile (used when deleting a profile).
        """
        name = profile.name
        if not name:
            return
        onigiri.remove_profile_rules(name)

    # ----- KWin rules list / toggle -----

    # noinspection PyMethodMayBeStatic
    def list_rules(self) -> List[Dict[str, Any]]:
        return onigiri.list_kwin_rules()

    # noinspection PyMethodMayBeStatic
    def set_rule_enabled(self, rule_id: str, enabled: bool) -> None:
        onigiri.set_rule_enabled(rule_id, enabled)

    # noinspection PyMethodMayBeStatic
    def delete_rule(self, rule_id: str) -> None:
        onigiri.delete_kwin_rule(rule_id)

    # noinspection PyMethodMayBeStatic
    def launch_tile_command(self, tile: TileModel) -> None:
        """
        Launch only this tile's command.
        Uses the command stored on the TileModel.
        """
        cmd = (tile.command or "").strip()
        if not cmd:
            raise ValueError("Tile has no command configured.")

        # Simple implementation: just spawn the command.
        # (This mirrors what the engine does for profile commands.)
        subprocess.Popen(cmd, shell=True)
