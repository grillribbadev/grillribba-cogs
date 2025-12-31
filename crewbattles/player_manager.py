from redbot.core import Config
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
        Return a mapping of user_id -> data dict.
        Tries several Config internals (backwards compatible with multiple Red versions).
        Falls back to {} if none are available.
        """
        # try several likely internal APIs
        candidates = (
            "_get_raw_data",  # older Red
            "_get_all",       # some variants
            "_raw",           # possible internal attr
            "raw",            # less likely
        )
        for name in candidates:
            fn = getattr(self._conf, name, None)
            if not fn:
                continue
            try:
                res = fn()
            except TypeError:
                # maybe it's an async method
                try:
                    res = await fn()
                except Exception:
                    continue
            except Exception:
                continue

            if res is None:
                continue
            # If coroutine returned (rare), await it
            if hasattr(res, "__await__"):
                try:
                    res = await res
                except Exception:
                    continue
            # Expect a dict-like mapping of raw storage
            if isinstance(res, dict):
                return res

        # Last-resort: try to read known users by retrieving all keys from the underlying store
        # If not possible, return empty mapping to avoid crashing leaderboard.
        try:
            # Some Config implementations expose _get_data or similar
            if hasattr(self._conf, "_get_data"):
                d = self._conf._get_data()
                if hasattr(d, "__await__"):
                    d = await d
                if isinstance(d, dict):
                    return d
        except Exception:
            pass

        return {}
