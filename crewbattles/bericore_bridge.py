class BeriBridge:
    """Bridge between CrewBattles and BeriCore."""

    def __init__(self, bot):
        self.bot = bot

    async def reward(self, member, amount: int):
        if amount <= 0:
            return

        bericore = self.bot.get_cog("BeriCore")
        if not bericore:
            return  # BeriCore not loaded

        try:
            # Standard BeriCore API
            await bericore.add_beri(
                member,
                amount,
                reason="crew_battle",
                silent=False,
            )
        except Exception as e:
            # Fail silently but safely
            print(f"[CrewBattles] BeriCore reward failed: {e}")