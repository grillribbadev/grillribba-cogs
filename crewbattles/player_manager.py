import copy
import discord

from .constants import DEFAULT_USER

class PlayerManager:
    """
    Lightweight wrapper around Red's Config for per-user CrewBattles data.
    Methods:
      - get(member) -> dict (always returns a dict copy)
      - save(member, data) -> saves dict
      - all() -> returns mapping of user_id -> dict (may be implementation-dependent)
    """
    def __init__(self, cog):
        self.cog = cog
        # Ensure this points at your cog Config user scope
        # If you already have _conf, keep it; otherwise set it like below:
        self._conf = cog.config

    def _uid(self, user) -> int:
        """Return an int user id from Member/User/int/str safely."""
        if hasattr(user, "id"):
            return int(user.id)
        # allow passing raw ids
        return int(str(user))

    async def get(self, user: discord.abc.User) -> dict:
        """Get a user's data WITHOUT writing defaults back to storage."""
        uid = self._uid(user)
        try:
            stored = await self._conf.user_from_id(uid).all()
        except Exception:
            stored = None

        # If nothing stored, return defaults (do NOT save here)
        if not isinstance(stored, dict) or not stored:
            return copy.deepcopy(DEFAULT_USER)

        # Merge stored onto defaults safely (preserves existing values)
        merged = copy.deepcopy(DEFAULT_USER)
        merged.update(stored)

        # Merge nested haki dict safely
        merged_haki = copy.deepcopy(DEFAULT_USER.get("haki", {}))
        try:
            if isinstance(stored.get("haki"), dict):
                merged_haki.update(stored["haki"])
        except Exception:
            pass
        merged["haki"] = merged_haki

        return merged

    async def save(self, user: discord.abc.User, data: dict):
        """Persist user data (deepcopy to avoid shared references)."""
        uid = self._uid(user)
        if not isinstance(data, dict):
            data = copy.deepcopy(DEFAULT_USER)
        await self._conf.user_from_id(uid).set(copy.deepcopy(data))

    async def all(self) -> dict:
        """Return raw all_users mapping (uid -> dict)."""
        return await self._conf.all_users()
