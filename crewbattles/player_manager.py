from redbot.core import Config
from .constants import DEFAULT_USER

class PlayerManager:
    """
    Lightweight wrapper around Red's Config for per-user CrewBattles data.
    Methods:
      - get(member) -> dict (always returns a dict copy)
      - save(member, data) -> saves dict
      - all() -> returns mapping of user_id -> dict (may be expensive)
    """
    def __init__(self, cog):
        # unique identifier for storage; change if you need to reset storage
        self._conf = Config.get_conf(cog, identifier=0xC0FFEE1234567890, force_registration=True)
        self._conf.register_user(**DEFAULT_USER)

    async def get(self, member):
        data = await self._conf.user(member).all()
        if not data:
            # ensure shape
            return dict(DEFAULT_USER)
        # return shallow copy so callers mutate saved object explicitly
        return dict(data)

    async def save(self, member, data):
        # ensure keys exist by merging with defaults
        merged = dict(DEFAULT_USER)
        merged.update(data or {})
        await self._conf.user(member).set(merged)

    async def all(self):
        """
        Return the raw storage mapping for all users. May be implementation-dependent;
        use carefully (admin/leaderboard only).
        """
        try:
            return await self._conf._get_raw_data()  # internal; may work in Red
        except Exception:
            return {}
