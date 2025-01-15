from .starwarsroles import StarWarsRoles  # Import the cog class from the main file

async def setup(bot):
    """
    Add the StarWarsRoles cog to the bot.
    """
    await bot.add_cog(StarWarsRoles(bot))
