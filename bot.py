import os

import discord
from discord.ext import commands


class FireEmblemBot(commands.Bot):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self) -> None:
        await self.load_extension("cogs.battle")
        await self.tree.sync()


def load_token() -> str:
    token = os.getenv("DISCORD_BOT_TOKEN")
    if token:
        return token

    if os.path.exists(".env"):
        with open(".env", "r", encoding="utf-8") as env_file:
            for raw_line in env_file:
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                if key.strip() == "DISCORD_BOT_TOKEN":
                    token = value.strip().strip("\"'")
                    if token:
                        return token

    raise RuntimeError(
        "Set DISCORD_BOT_TOKEN in your environment, or add DISCORD_BOT_TOKEN=... to a .env file."
    )


def main() -> None:
    bot = FireEmblemBot()
    bot.run(load_token())


if __name__ == "__main__":
    main()
