import asyncio
import logging
import os
from datetime import datetime, timezone

import discord
from discord import app_commands

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
log = logging.getLogger("yrd-alpha")

TOKEN = os.getenv("DISCORD_TOKEN", "").strip()
GUILD_ID_RAW = os.getenv("GUILD_ID", "").strip()

if not TOKEN:
    raise RuntimeError("DISCORD_TOKEN is missing. Add it as a private Railway service variable.")
if not GUILD_ID_RAW.isdigit():
    raise RuntimeError("GUILD_ID is missing or invalid. It must be the numeric Discord server ID.")

GUILD_ID = int(GUILD_ID_RAW)
GUILD = discord.Object(id=GUILD_ID)

# No privileged Message Content intent is needed for this starter runtime.
intents = discord.Intents.none()
intents.guilds = True

client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)
_started_at = datetime.now(timezone.utc)
_startup_notice_sent = False


def utc_now_text() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def is_server_owner(interaction: discord.Interaction) -> bool:
    return bool(interaction.guild and interaction.user.id == interaction.guild.owner_id)


async def find_text_channel(name: str):
    guild = client.get_guild(GUILD_ID)
    if guild is None:
        return None
    channel = discord.utils.get(guild.text_channels, name=name)
    if channel is not None:
        return channel
    # Cache can be incomplete immediately after reconnect; fetch once as fallback.
    try:
        channels = await guild.fetch_channels()
        return discord.utils.get(channels, name=name)
    except discord.DiscordException:
        log.exception("Could not fetch guild channels")
        return None


async def post_startup_notice_once():
    global _startup_notice_sent
    if _startup_notice_sent:
        return
    channel = await find_text_channel("bot-status")
    if channel is None:
        log.warning("#bot-status was not found; startup notice was not posted.")
        return

    message = (
        "🟢 **YRD Alpha runtime is ONLINE**\n"
        f"Connected: `{utc_now_text()}`\n"
        "Persistent Discord Gateway connection: active.\n"
        "Market scanner integrations: **not connected yet**.\n"
        "Use `/status` to verify the bot and `/scout_test` for an owner-only test post."
    )
    try:
        await channel.send(message, allowed_mentions=discord.AllowedMentions.none())
        _startup_notice_sent = True
    except discord.Forbidden:
        log.error("Bot cannot send in #bot-status. Check channel permissions for the managed bot role.")
    except discord.DiscordException:
        log.exception("Failed to post startup notice")


@client.event
async def on_ready():
    log.info("Logged in as %s (%s)", client.user, client.user.id if client.user else "unknown")
    guild = client.get_guild(GUILD_ID)
    if guild is None:
        log.error("Bot is not connected to guild %s. Check GUILD_ID and bot invitation.", GUILD_ID)
        return

    log.info("Connected to guild: %s (%s)", guild.name, guild.id)
    try:
        # Guild-scoped sync makes the commands appear quickly in this server.
        synced = await tree.sync(guild=GUILD)
        log.info("Synced %d slash command(s) to %s", len(synced), guild.name)
    except discord.DiscordException:
        log.exception("Slash command sync failed")

    await client.change_presence(
        status=discord.Status.online,
        activity=discord.Activity(type=discord.ActivityType.watching, name="YRD Alpha"),
    )
    await post_startup_notice_once()


@tree.command(name="status", description="Check whether the YRD Alpha runtime is online.", guild=GUILD)
async def status(interaction: discord.Interaction):
    uptime = datetime.now(timezone.utc) - _started_at
    seconds = int(uptime.total_seconds())
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    latency_ms = round(client.latency * 1000)
    await interaction.response.send_message(
        "🟢 **YRD Alpha is online**\n"
        f"Latency: `{latency_ms} ms`\n"
        f"Uptime: `{hours}h {minutes}m {secs}s`\n"
        "Scanner: `not connected yet`",
        ephemeral=True,
    )


@tree.command(name="ping", description="Quick bot connection test.", guild=GUILD)
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message(
        f"Pong — `{round(client.latency * 1000)} ms`",
        ephemeral=True,
    )


@tree.command(name="scout_test", description="Owner-only test message in #alpha-scout.", guild=GUILD)
async def scout_test(interaction: discord.Interaction):
    if not is_server_owner(interaction):
        await interaction.response.send_message("Only the server owner can run this test.", ephemeral=True)
        return

    channel = await find_text_channel("alpha-scout")
    if channel is None:
        await interaction.response.send_message("I could not find #alpha-scout.", ephemeral=True)
        return

    embed = discord.Embed(
        title="YRD Alpha Scout — TEST MODE",
        description=(
            "This is a runtime test only. No live market provider is connected to this card.\n\n"
            "**Yurman has NOT reviewed this setup yet.**\n"
            "Research only. Do not enter based solely on this alert."
        ),
        timestamp=datetime.now(timezone.utc),
    )
    embed.add_field(name="Chain", value="TEST", inline=True)
    embed.add_field(name="Contract", value="TEST-NO-CONTRACT", inline=True)
    embed.add_field(name="Data coverage", value="Unavailable — test message", inline=False)
    embed.set_footer(text="YRD Alpha | Runtime connectivity test")

    try:
        await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
        await interaction.response.send_message("Test card sent to #alpha-scout.", ephemeral=True)
    except discord.Forbidden:
        await interaction.response.send_message(
            "I am online, but I do not have permission to send in #alpha-scout.",
            ephemeral=True,
        )


@tree.command(name="post_update", description="Owner-only announcement to #yrd-updates.", guild=GUILD)
@app_commands.describe(message="Text to post in #yrd-updates")
async def post_update(interaction: discord.Interaction, message: str):
    if not is_server_owner(interaction):
        await interaction.response.send_message("Only the server owner can post official updates.", ephemeral=True)
        return
    if len(message) > 1800:
        await interaction.response.send_message("Keep the update under 1,800 characters.", ephemeral=True)
        return

    channel = await find_text_channel("yrd-updates")
    if channel is None:
        await interaction.response.send_message("I could not find #yrd-updates.", ephemeral=True)
        return

    await channel.send(
        "**YRD Alpha update**\n" + message,
        allowed_mentions=discord.AllowedMentions.none(),
    )
    await interaction.response.send_message("Update posted.", ephemeral=True)


async def main():
    async with client:
        await client.start(TOKEN)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
