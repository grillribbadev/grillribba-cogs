class BeriBridge:
    def __init__(self, bot):
        self.bot = bot

    async def reward(self, member, amount):
        beri = self.bot.get_cog("BeriCore")
        if beri and amount > 0:
            await beri.add_beri(member, amount, reason="crew_battle")
