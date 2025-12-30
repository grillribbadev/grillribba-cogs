from redbot.core import Config
from .constants import DEFAULT_USER, MAX_LEVEL
from .utils import exp_to_next

class PlayerManager:
    def __init__(self, cog):
        self.config = Config.get_conf(cog, identifier=882233441)
        self.config.register_user(**DEFAULT_USER)

    async def get(self, user):
        return await self.config.user(user).all()

    async def save(self, user, data):
        while data["level"] < MAX_LEVEL and data["exp"] >= exp_to_next(data["level"]):
            data["exp"] -= exp_to_next(data["level"])
            data["level"] += 1
        await self.config.user(user).set(data)
