import asyncio
import logging
import os
from datetime import datetime, timezone

import discord
from discord import app_commands

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")
log = logging.getLogger("yrd-alpha")

TOKEN = os.getenv("DISCORD_TOKEN", "").strip()
GUILD_ID_RAW = os.getenv("GUILD_ID", "").strip()
if not TOKEN:
    raise RuntimeError("DISCORD_TOKEN is missing. Add it as a private Railway service variable.")
if not GUILD_ID_RAW.isdigit():
    raise RuntimeError("GUILD_ID is missing or invalid. It must be the numeric Discord server ID.")

GUILD_ID = int(GUILD_ID_RAW)
GUILD = discord.Object(id=GUILD_ID)
intents = discord.Intents.none()
intents.guilds = True
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)
_started_at = datetime.now(timezone.utc)
_startup_notice_sent = False

DISCLAIMER = "Research only. Not financial advice or a guaranteed return. Meme coins can lose their entire value."
REVIEW_ROLES = {"Yurman / Owner", "Admin"}


def utc_now_text() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def is_reviewer(interaction: discord.Interaction) -> bool:
    if not interaction.guild:
        return False
    if interaction.user.id == interaction.guild.owner_id:
        return True
    return any(role.name in REVIEW_ROLES for role in getattr(interaction.user, "roles", []))


async def find_text_channel(name: str):
    guild = client.get_guild(GUILD_ID)
    if guild is None:
        return None
    channel = discord.utils.get(guild.text_channels, name=name)
    if channel is not None:
        return channel
    try:
        channels = await guild.fetch_channels()
        return discord.utils.get(channels, name=name)
    except discord.DiscordException:
        log.exception("Could not fetch guild channels")
        return None


def field_value(embed: discord.Embed, name: str, default: str = "Unknown") -> str:
    for field in embed.fields:
        if field.name == name:
            return field.value
    return default


async def edit_linked_message(url: str, status: str, color: discord.Color):
    try:
        parts = url.rstrip("/").split("/")
        channel_id, message_id = int(parts[-2]), int(parts[-1])
        channel = client.get_channel(channel_id) or await client.fetch_channel(channel_id)
        message = await channel.fetch_message(message_id)
        if not message.embeds:
            return
        embed = message.embeds[0].copy()
        embed.color = color
        embed.title = f"{status} — {field_value(embed, 'Token')}"
        embed.description = (
            f"**Review status: {status}**\n\n"
            "Monitoring may continue, but this is not an automatic trade instruction.\n"
            f"{DISCLAIMER}"
        )
        await message.edit(embed=embed)
    except (ValueError, IndexError, discord.DiscordException):
        log.exception("Could not update linked candidate message")


class ReviewView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        actions = [
            ("APPROVE", discord.ButtonStyle.success, "review:approve"),
            ("WATCH", discord.ButtonStyle.primary, "review:watch"),
            ("REJECT", discord.ButtonStyle.secondary, "review:reject"),
            ("HIGH RISK", discord.ButtonStyle.danger, "review:high-risk"),
            ("RUG WARNING", discord.ButtonStyle.danger, "review:rug-warning"),
        ]
        for label, style, custom_id in actions:
            button = discord.ui.Button(label=label, style=style, custom_id=custom_id)
            button.callback = self._callback_for(label)
            self.add_item(button)

    def _callback_for(self, action: str):
        async def callback(interaction: discord.Interaction):
            await self.handle_review(interaction, action)
        return callback

    async def handle_review(self, interaction: discord.Interaction, action: str):
        if not is_reviewer(interaction):
            await interaction.response.send_message("Only Yurman or an Admin can review Scout candidates.", ephemeral=True)
            return
        if not interaction.message or not interaction.message.embeds:
            await interaction.response.send_message("This review card is missing its candidate data.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        source = interaction.message.embeds[0]
        token = field_value(source, "Token")
        contract = field_value(source, "Contract")
        risk = field_value(source, "Risk")
        confidence = field_value(source, "Confidence")
        market_cap = field_value(source, "Market Cap")
        public_url = field_value(source, "Public Alert", "")
        waiting_url = field_value(source, "Waiting Card", "")

        if action == "RUG WARNING":
            target_name, color, heading = "rug-alerts", discord.Color.red(), "🚨 RUG WARNING"
        elif action == "APPROVE":
            target_name, color, heading = "yrd-reviewed", discord.Color.green(), "✅ YURMAN REVIEWED"
        elif action == "WATCH":
            target_name, color, heading = "yrd-reviewed", discord.Color.blue(), "👀 YURMAN WATCHING"
        elif action == "HIGH RISK":
            target_name, color, heading = "yrd-reviewed", discord.Color.orange(), "⚠️ HIGH RISK"
        else:
            target_name, color, heading = "scout-review", discord.Color.dark_grey(), "❌ REJECTED"

        result = discord.Embed(
            title=f"{heading} — {token}",
            description=(
                f"Decision by {interaction.user.mention}: **{action}**\n"
                "Automatic monitoring may continue. This decision is not a promise of profit or a trade instruction."
            ),
            color=color,
            timestamp=datetime.now(timezone.utc),
        )
        result.add_field(name="Token", value=token, inline=True)
        result.add_field(name="Market Cap", value=market_cap, inline=True)
        result.add_field(name="Risk", value=risk, inline=True)
        result.add_field(name="Confidence", value=confidence, inline=True)
        result.add_field(name="Contract", value=contract, inline=False)
        result.set_footer(text=DISCLAIMER)

        target = await find_text_channel(target_name)
        if target:
            await target.send(embed=result, allowed_mentions=discord.AllowedMentions.none())

        journal = await find_text_channel("trade-journal")
        if journal:
            journal_entry = discord.Embed(
                title=f"📓 JOURNAL — {token}",
                description="Permanent review record. Wins and losses must remain visible.",
                color=color,
                timestamp=datetime.now(timezone.utc),
            )
            journal_entry.add_field(name="Scout Alert", value=public_url or "Unavailable", inline=False)
            journal_entry.add_field(name="Token", value=token, inline=True)
            journal_entry.add_field(name="Market Cap at Review", value=market_cap, inline=True)
            journal_entry.add_field(name="Yurman's Decision", value=action, inline=True)
            journal_entry.add_field(name="Observed Level", value="Not recorded", inline=True)
            journal_entry.add_field(name="Highest Afterwards", value="Monitoring", inline=True)
            journal_entry.add_field(name="Lowest Afterwards", value="Monitoring", inline=True)
            journal_entry.add_field(name="Result", value="Open / monitoring", inline=True)
            journal_entry.add_field(name="Rug Status", value="Warning issued" if action == "RUG WARNING" else "Not confirmed", inline=True)
            journal_entry.add_field(name="Lesson Learned", value="To be completed after the setup ends.", inline=False)
            journal_entry.set_footer(text=DISCLAIMER)
            await journal.send(embed=journal_entry, allowed_mentions=discord.AllowedMentions.none())
        await edit_linked_message(public_url, action, color)
        await edit_linked_message(waiting_url, action, color)

        closed = source.copy()
        closed.color = color
        closed.title = f"REVIEW COMPLETE — {action}"
        closed.description = f"Reviewed by {interaction.user.mention} at {utc_now_text()}."
        try:
            await interaction.message.edit(embed=closed, view=None)
        except discord.DiscordException:
            log.exception("Could not close review card")
        await interaction.followup.send(f"Decision saved: **{action}** for **{token}**.", ephemeral=True)


async def candidate_exists(contract: str) -> bool:
    channel = await find_text_channel("alpha-scout")
    if channel is None:
        return False
    try:
        async for message in channel.history(limit=75):
            for embed in message.embeds:
                if field_value(embed, "Contract", "").lower() == contract.lower():
                    return True
    except discord.DiscordException:
        log.exception("Duplicate check failed")
    return False


async def publish_candidate(
    interaction: discord.Interaction,
    token: str,
    contract: str,
    market_cap: str,
    liquidity: str,
    volume: str,
    age_minutes: int,
    buyers: int,
    sellers: int,
    risk: str,
    confidence: str,
):
    if await candidate_exists(contract):
        await interaction.followup.send("That contract already has a recent Scout alert. Duplicate blocked.", ephemeral=True)
        return
    alpha = await find_text_channel("alpha-scout")
    waiting = await find_text_channel("waiting-for-yurman")
    review = await find_text_channel("scout-review")
    missing = [name for name, channel in [("alpha-scout", alpha), ("waiting-for-yurman", waiting), ("scout-review", review)] if channel is None]
    if missing:
        await interaction.followup.send("Missing channel(s): " + ", ".join(missing), ephemeral=True)
        return

    embed = discord.Embed(
        title=f"🔎 YRD ALPHA SCOUT — POSSIBLE SETUP: {token}",
        description=(
            "A possible setup was submitted for research and review.\n\n"
            "⚠️ **Yurman has NOT reviewed this setup yet.**\n"
            "Do not enter based solely on this alert. Waiting for Yurman's review."
        ),
        color=discord.Color.gold(),
        timestamp=datetime.now(timezone.utc),
    )
    for name, value, inline in [
        ("Token", token.upper(), True), ("Market Cap", market_cap, True), ("Liquidity", liquidity, True),
        ("Volume", volume, True), ("Age", f"{age_minutes} minutes", True),
        ("Buyers / Sellers", f"{buyers} / {sellers}", True), ("Risk", risk, True),
        ("Confidence", confidence, True), ("Contract", contract, False),
    ]:
        embed.add_field(name=name, value=value, inline=inline)
    embed.set_footer(text=DISCLAIMER)
    public_message = await alpha.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())

    waiting_embed = embed.copy()
    waiting_embed.title = f"⏳ WAITING FOR YURMAN — {token.upper()}"
    waiting_embed.description = f"Promising setup awaiting private review.\n[Open public Scout alert]({public_message.jump_url})\n\n{DISCLAIMER}"
    waiting_message = await waiting.send(embed=waiting_embed, allowed_mentions=discord.AllowedMentions.none())

    review_embed = embed.copy()
    review_embed.title = f"PRIVATE SCOUT REVIEW — {token.upper()}"
    review_embed.description = "Choose one decision below. Only Yurman or an Admin can use these buttons."
    review_embed.add_field(name="Public Alert", value=public_message.jump_url, inline=False)
    review_embed.add_field(name="Waiting Card", value=waiting_message.jump_url, inline=False)
    await review.send(embed=review_embed, view=ReviewView(), allowed_mentions=discord.AllowedMentions.none())
    await interaction.followup.send("Candidate posted publicly and sent to the private review queue.", ephemeral=True)


async def post_startup_notice_once():
    global _startup_notice_sent
    if _startup_notice_sent:
        return
    channel = await find_text_channel("bot-status")
    if channel is None:
        return
    try:
        await channel.send(
            "🟢 **YRD Alpha Scout is ONLINE**\n"
            f"Connected: `{utc_now_text()}`\n"
            "Phase 2 review workflow: **active**.\n"
            "Live market data providers: **not connected yet**.",
            allowed_mentions=discord.AllowedMentions.none(),
        )
        _startup_notice_sent = True
    except discord.DiscordException:
        log.exception("Failed to post startup notice")


@client.event
async def on_ready():
    log.info("Logged in as %s (%s)", client.user, client.user.id if client.user else "unknown")
    guild = client.get_guild(GUILD_ID)
    if guild is None:
        log.error("Bot is not connected to guild %s", GUILD_ID)
        return
    client.add_view(ReviewView())
    try:
        synced = await tree.sync(guild=GUILD)
        log.info("Synced %d slash command(s) to %s", len(synced), guild.name)
    except discord.DiscordException:
        log.exception("Slash command sync failed")
    await client.change_presence(status=discord.Status.online, activity=discord.Activity(type=discord.ActivityType.watching, name="YRD Alpha"))
    await post_startup_notice_once()


@tree.command(name="status", description="Check whether YRD Alpha Scout is online.", guild=GUILD)
async def status(interaction: discord.Interaction):
    uptime = datetime.now(timezone.utc) - _started_at
    seconds = int(uptime.total_seconds())
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    await interaction.response.send_message(
        "🟢 **YRD Alpha Scout is online**\n"
        f"Latency: `{round(client.latency * 1000)} ms`\n"
        f"Uptime: `{hours}h {minutes}m {secs}s`\n"
        "Review workflow: `active`\nLive scanner: `awaiting data provider`",
        ephemeral=True,
    )


@tree.command(name="ping", description="Quick bot connection test.", guild=GUILD)
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message(f"Pong — `{round(client.latency * 1000)} ms`", ephemeral=True)


@tree.command(name="scout_candidate", description="Submit a Scout candidate for public alert and Yurman review.", guild=GUILD)
@app_commands.describe(token="Token symbol", contract="Token contract address", market_cap="Example: $250K", liquidity="Example: $80K", volume="Example: $150K", age_minutes="Token age in minutes", buyers="Unique/recent buyers", sellers="Unique/recent sellers", risk="LOW, MEDIUM, HIGH, or EXTREME", confidence="LOW, MEDIUM, or HIGH")
async def scout_candidate(interaction: discord.Interaction, token: str, contract: str, market_cap: str, liquidity: str, volume: str, age_minutes: app_commands.Range[int, 0, 1000000], buyers: app_commands.Range[int, 0, 100000000], sellers: app_commands.Range[int, 0, 100000000], risk: str, confidence: str):
    if not is_reviewer(interaction):
        await interaction.response.send_message("Only Yurman or an Admin can submit candidates right now.", ephemeral=True)
        return
    risk, confidence = risk.upper().strip(), confidence.upper().strip()
    if risk not in {"LOW", "MEDIUM", "HIGH", "EXTREME"} or confidence not in {"LOW", "MEDIUM", "HIGH"}:
        await interaction.response.send_message("Risk must be LOW/MEDIUM/HIGH/EXTREME and confidence must be LOW/MEDIUM/HIGH.", ephemeral=True)
        return
    if len(token) > 20 or len(contract) > 200:
        await interaction.response.send_message("Token or contract value is too long.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    await publish_candidate(interaction, token, contract, market_cap, liquidity, volume, age_minutes, buyers, sellers, risk, confidence)


@tree.command(name="scout_test", description="Create an owner-only test candidate and review card.", guild=GUILD)
async def scout_test(interaction: discord.Interaction):
    if not is_reviewer(interaction):
        await interaction.response.send_message("Only Yurman or an Admin can run this test.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    await publish_candidate(interaction, "$TEST", f"TEST-{int(datetime.now(timezone.utc).timestamp())}", "$250K", "$80K", "$150K", 12, 125, 73, "MEDIUM", "MEDIUM")


@tree.command(name="post_update", description="Post an official update to #yrd-updates.", guild=GUILD)
@app_commands.describe(message="Text to post in #yrd-updates")
async def post_update(interaction: discord.Interaction, message: str):
    if not is_reviewer(interaction):
        await interaction.response.send_message("Only Yurman or an Admin can post official updates.", ephemeral=True)
        return
    if len(message) > 1800:
        await interaction.response.send_message("Keep the update under 1,800 characters.", ephemeral=True)
        return
    channel = await find_text_channel("yrd-updates")
    if channel is None:
        await interaction.response.send_message("I could not find #yrd-updates.", ephemeral=True)
        return
    await channel.send("**YRD Alpha update**\n" + message, allowed_mentions=discord.AllowedMentions.none())
    await interaction.response.send_message("Update posted.", ephemeral=True)


@tree.command(name="risk", description="Calculate risk before entering a position.", guild=GUILD)
@app_commands.describe(bankroll="Your total trading bankroll in dollars", entry="Planned position size in dollars", stop_percent="Stop distance as a percent", target_percent="Optional profit target as a percent")
async def risk(interaction: discord.Interaction, bankroll: float, entry: float, stop_percent: float, target_percent: float = 40.0):
    if bankroll <= 0 or entry <= 0 or stop_percent <= 0 or target_percent <= 0:
        await interaction.response.send_message("All values must be greater than zero.", ephemeral=True)
        return
    if entry > bankroll:
        await interaction.response.send_message("The planned position cannot be larger than the bankroll.", ephemeral=True)
        return
    if stop_percent > 100:
        await interaction.response.send_message("Stop percent cannot exceed 100%.", ephemeral=True)
        return
    dollars_at_risk = entry * stop_percent / 100
    target_profit = entry * target_percent / 100
    rr = target_percent / stop_percent
    embed = discord.Embed(
        title="🧮 YRD RISK — PLAN BEFORE ENTRY",
        description="Decide the maximum acceptable loss before entering. This calculator does not predict outcomes.",
        color=discord.Color.blue(),
    )
    embed.add_field(name="Bankroll", value=f"${bankroll:,.2f}", inline=True)
    embed.add_field(name="Position Size", value=f"${entry:,.2f} ({entry / bankroll * 100:.1f}% of bankroll)", inline=True)
    embed.add_field(name="Loss at Stop", value=f"${dollars_at_risk:,.2f} ({dollars_at_risk / bankroll * 100:.1f}% of bankroll)", inline=True)
    embed.add_field(name="Selected Target", value=f"+{target_percent:.1f}% = ${target_profit:,.2f}", inline=True)
    embed.add_field(name="Risk / Reward", value=f"1 : {rr:.2f}", inline=True)
    embed.add_field(name="Profit Reference", value=f"1R: ${dollars_at_risk:,.2f}\n2R: ${dollars_at_risk * 2:,.2f}\n3R: ${dollars_at_risk * 3:,.2f}", inline=True)
    embed.set_footer(text=DISCLAIMER)
    await interaction.response.send_message(embed=embed, ephemeral=True)


@tree.command(name="paper_open", description="Open a fake-money paper trade.", guild=GUILD)
@app_commands.describe(token="Token symbol", amount="Fake dollars used", entry_price="Simulated entry price", stop_percent="Planned stop percent", take_profit_percent="Planned target percent")
async def paper_open(interaction: discord.Interaction, token: str, amount: float, entry_price: float, stop_percent: float = 20.0, take_profit_percent: float = 40.0):
    if amount <= 0 or entry_price <= 0 or stop_percent <= 0 or stop_percent > 100 or take_profit_percent <= 0:
        await interaction.response.send_message("Use positive values and a stop between 0% and 100%.", ephemeral=True)
        return
    channel = await find_text_channel("paper-trades")
    if channel is None:
        await interaction.response.send_message("I could not find #paper-trades.", ephemeral=True)
        return
    embed = discord.Embed(
        title=f"🧪 PAPER TRADE OPEN — {token.upper()}",
        description="Fake-money practice only. No real order was placed.",
        color=discord.Color.blurple(),
        timestamp=datetime.now(timezone.utc),
    )
    embed.add_field(name="Member", value=interaction.user.mention, inline=True)
    embed.add_field(name="Fake Position", value=f"${amount:,.2f}", inline=True)
    embed.add_field(name="Entry Price", value=f"{entry_price:.12g}", inline=True)
    embed.add_field(name="Stop", value=f"-{stop_percent:.2f}%", inline=True)
    embed.add_field(name="Target", value=f"+{take_profit_percent:.2f}%", inline=True)
    embed.add_field(name="Status", value="OPEN", inline=True)
    embed.set_footer(text=f"YRD-PAPER|{interaction.user.id}|OPEN")
    message = await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
    await interaction.response.send_message(f"Paper trade opened. Trade ID: `{message.id}`", ephemeral=True)


@tree.command(name="paper_close", description="Close one of your fake-money paper trades.", guild=GUILD)
@app_commands.describe(trade_id="Trade ID returned by /paper_open", exit_price="Simulated exit price")
async def paper_close(interaction: discord.Interaction, trade_id: str, exit_price: float):
    if not trade_id.isdigit() or exit_price <= 0:
        await interaction.response.send_message("Enter a valid numeric Trade ID and a positive exit price.", ephemeral=True)
        return
    channel = await find_text_channel("paper-trades")
    if channel is None:
        await interaction.response.send_message("I could not find #paper-trades.", ephemeral=True)
        return
    try:
        message = await channel.fetch_message(int(trade_id))
    except discord.NotFound:
        await interaction.response.send_message("Paper trade not found in #paper-trades.", ephemeral=True)
        return
    except discord.DiscordException:
        await interaction.response.send_message("I could not load that paper trade.", ephemeral=True)
        return
    if not message.embeds:
        await interaction.response.send_message("That message is not a paper trade.", ephemeral=True)
        return
    embed = message.embeds[0].copy()
    footer = embed.footer.text or ""
    expected_prefix = f"YRD-PAPER|{interaction.user.id}|"
    if not footer.startswith(expected_prefix) and not is_reviewer(interaction):
        await interaction.response.send_message("You can only close your own paper trades.", ephemeral=True)
        return
    if footer.endswith("|CLOSED"):
        await interaction.response.send_message("That paper trade is already closed.", ephemeral=True)
        return
    try:
        amount = float(field_value(embed, "Fake Position").replace("$", "").replace(",", ""))
        entry_price = float(field_value(embed, "Entry Price"))
    except ValueError:
        await interaction.response.send_message("The saved paper trade data is invalid.", ephemeral=True)
        return
    return_percent = (exit_price / entry_price - 1) * 100
    pnl = amount * return_percent / 100
    result = "WIN" if pnl > 0 else "LOSS" if pnl < 0 else "BREAKEVEN"
    embed.title = embed.title.replace("OPEN", f"CLOSED — {result}")
    embed.color = discord.Color.green() if pnl > 0 else discord.Color.red() if pnl < 0 else discord.Color.greyple()
    embed.add_field(name="Exit Price", value=f"{exit_price:.12g}", inline=True)
    embed.add_field(name="Return", value=f"{return_percent:+.2f}%", inline=True)
    embed.add_field(name="Fake P&L", value=f"${pnl:+,.2f}", inline=True)
    embed.set_footer(text=f"YRD-PAPER|{interaction.user.id}|CLOSED")
    await message.edit(embed=embed)
    results = await find_text_channel("paper-leaderboard")
    if results:
        recap = discord.Embed(title=f"🧪 PAPER RESULT — {result}", color=embed.color, timestamp=datetime.now(timezone.utc))
        recap.add_field(name="Member", value=interaction.user.mention, inline=True)
        recap.add_field(name="Token", value=embed.title.split("—")[1].strip() if "—" in embed.title else "Unknown", inline=True)
        recap.add_field(name="Fake P&L", value=f"${pnl:+,.2f} ({return_percent:+.2f}%)", inline=True)
        recap.description = "Leaderboard values reward consistency and risk control—not claimed real profits."
        await results.send(embed=recap, allowed_mentions=discord.AllowedMentions.none())
    wins_losses = await find_text_channel("wins-and-losses")
    if wins_losses:
        await wins_losses.send(f"🧪 {interaction.user.mention} closed a paper trade: **{result}**, fake P&L `${pnl:+,.2f}`. Wins and losses stay visible.", allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False))
    await interaction.response.send_message(f"Paper trade closed: **{result}**, fake P&L `${pnl:+,.2f}`.", ephemeral=True)


@tree.command(name="paper_stats", description="View your recent fake-money paper-trading statistics.", guild=GUILD)
async def paper_stats(interaction: discord.Interaction):
    channel = await find_text_channel("paper-trades")
    if channel is None:
        await interaction.response.send_message("I could not find #paper-trades.", ephemeral=True)
        return
    pnls = []
    async for message in channel.history(limit=300, oldest_first=True):
        if not message.embeds:
            continue
        embed = message.embeds[0]
        if (embed.footer.text or "") != f"YRD-PAPER|{interaction.user.id}|CLOSED":
            continue
        value = field_value(embed, "Fake P&L", "")
        try:
            pnls.append(float(value.replace("$", "").replace(",", "").replace("+", "")))
        except ValueError:
            continue
    wins = sum(1 for value in pnls if value > 0)
    losses = sum(1 for value in pnls if value < 0)
    running = peak = max_drawdown = 0.0
    for value in pnls:
        running += value
        peak = max(peak, running)
        max_drawdown = max(max_drawdown, peak - running)
    total = sum(pnls)
    win_rate = wins / len(pnls) * 100 if pnls else 0
    await interaction.response.send_message(
        "🧪 **Your Paper Statistics**\n"
        f"Closed trades: `{len(pnls)}`\nWins / Losses: `{wins} / {losses}`\n"
        f"Win rate: `{win_rate:.1f}%`\nFake net P&L: `${total:+,.2f}`\n"
        f"Largest fake drawdown: `${max_drawdown:,.2f}`\n"
        "Consistency and drawdown control matter more than one lucky trade.",
        ephemeral=True,
    )


@tree.command(name="emergency_alert", description="Send an owner/admin risk alert.", guild=GUILD)
@app_commands.describe(alert_type="RUG, LIQUIDITY, WHALE, CONTRACT, or NETWORK", token="Token or market name", details="Observed facts and source context")
async def emergency_alert(interaction: discord.Interaction, alert_type: str, token: str, details: str):
    if not is_reviewer(interaction):
        await interaction.response.send_message("Only Yurman or an Admin can issue emergency alerts.", ephemeral=True)
        return
    alert_type = alert_type.upper().strip()
    target_map = {"RUG": "rug-alerts", "LIQUIDITY": "liquidity-alerts", "WHALE": "whale-watch", "CONTRACT": "rug-alerts", "NETWORK": "network-status"}
    if alert_type not in target_map:
        await interaction.response.send_message("Type must be RUG, LIQUIDITY, WHALE, CONTRACT, or NETWORK.", ephemeral=True)
        return
    channel = await find_text_channel(target_map[alert_type])
    if channel is None:
        await interaction.response.send_message(f"I could not find #{target_map[alert_type]}.", ephemeral=True)
        return
    embed = discord.Embed(title=f"🚨 {alert_type} ALERT — {token}", description=details, color=discord.Color.red(), timestamp=datetime.now(timezone.utc))
    embed.add_field(name="Required Action", value="Investigate before acting. This alert is not an automatic buy or sell instruction.", inline=False)
    embed.set_footer(text=DISCLAIMER)
    await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
    await interaction.response.send_message(f"Alert posted in #{target_map[alert_type]}.", ephemeral=True)


async def main():
    async with client:
        await client.start(TOKEN)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
