from .ai_cog import AICharacter  

async def setup(bot):
   
    await bot.add_cog(AICharacter(bot))  
