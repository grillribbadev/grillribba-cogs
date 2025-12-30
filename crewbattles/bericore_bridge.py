from redbot.core import bank


class BeriBridge:
    """BeriCore bridge using Red's bank (BeriCore-compatible)."""

    def __init__(self, bot):
        self.bot = bot

    async def reward(self, member, amount: int):
        if amount <= 0:
            return

        try:
            await bank.deposit_credits(member, amount)
        except Exception as e:
            print(f"[CrewBattles] Failed to deposit Beri: {e}")