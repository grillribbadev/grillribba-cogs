from redbot.core import commands
import aiohttp
import json

class weather(commands.Cog):


    def __init__(self, bot):
        self.bot = bot
        self.session = aiohttp.ClientSession()

    @commands.command()
    async def weather(self, ctx, *, location: str):
        api_key = 'config.json'
        async with self.session.get(f'http://api.weatherapi.com/v1/current.json?key={api_key}&q={location}') as response:
            if response.status == 200:
                data = await response.json()
                location_name = data['location']['name']
                country = data['location']['country']
                temp_c = data['current']['temp_c']
                condition = data['current']['condition']['text']

                await ctx.send(f'Weather in {location_name}, {country}:\nTemperature: {temp_c}°C\nCondition: {condition}')
            else:
                await ctx.send('Unable to fetch weather information. Please try again later.')



