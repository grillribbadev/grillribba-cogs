import copy
import discord

from .constants import DEFAULT_USER

class PlayerManager:
    """
        Lightweight wrapper around Red's Config for per-guild CrewBattles data.
    Methods:
            - get(member, guild=None) -> dict (always returns a dict copy)
            - save(member, data, guild=None) -> saves dict
            - all(guild) -> returns mapping of user_id -> dict for that guild
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

    def _guild_id(self, guild) -> int | None:
        try:
            return int(getattr(guild, "id", None))
        except Exception:
            return None

    async def get(self, user: discord.abc.User, guild=None) -> dict:
        """Get player data scoped to the given guild (defaults if none stored)."""
        uid = self._uid(user)
        if guild is None:
            guild = getattr(user, "guild", None)

        gid = self._guild_id(guild)
        stored = None
        if gid is not None:
            try:
                stored = await self._conf.member_from_ids(gid, uid).all()
            except Exception:
                stored = None

        # Best-effort migration from legacy global user-scope storage.
        if (not isinstance(stored, dict) or not stored) and gid is not None:
            try:
                legacy = await self._conf.user_from_id(uid).all()
            except Exception:
                legacy = None

            if isinstance(legacy, dict) and legacy:
                # Only migrate if the legacy record looks like it was used.
                if bool(legacy.get("started")):
                    stored = copy.deepcopy(legacy)
                    try:
                        await self._conf.member_from_ids(gid, uid).set(copy.deepcopy(stored))
                    except Exception:
                        pass

        # If nothing stored, return defaults (do NOT save here)
        if not isinstance(stored, dict) or not stored:
            return copy.deepcopy(DEFAULT_USER)

        # Merge stored onto defaults safely (preserves existing values)
        merged = copy.deepcopy(DEFAULT_USER)
        merged.update(stored)

        # merge nested haki safely
        base_haki = copy.deepcopy(DEFAULT_USER.get("haki", {}))
        if isinstance(stored.get("haki"), dict):
            base_haki.update(stored["haki"])
        merged["haki"] = base_haki

        return merged

    async def save(self, user: discord.abc.User, data: dict, guild=None):
        """Persist player data scoped to the given guild (deepcopy to avoid shared references)."""
        uid = self._uid(user)
        if guild is None:
            guild = getattr(user, "guild", None)
        gid = self._guild_id(guild)

        if not isinstance(data, dict):
            data = copy.deepcopy(DEFAULT_USER)

        if gid is None:
            # No guild context; avoid writing global user-scope data.
            return

        await self._conf.member_from_ids(gid, uid).set(copy.deepcopy(data))

    async def all(self, guild) -> dict:
        """Return raw mapping (uid -> dict) for this guild."""
        try:
            return await self._conf.all_members(guild)
        except Exception:
            return {}
