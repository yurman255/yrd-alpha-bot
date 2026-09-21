import asyncio
import logging
import os
import re
from datetime import datetime, timedelta, timezone

import aiohttp
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
_scanner_task = None
_scanner_seen = set()

DISCLAIMER = "Research only. Not financial advice or a guaranteed return. Meme coins can lose their entire value."
REVIEW_ROLES = {"Yurman / Owner", "Admin"}
MODERATOR_ROLES = {"Yurman / Owner", "Admin", "Moderator"}
SUPPORT_ROLES = {"Yurman / Owner", "Admin", "Moderator", "Support"}
ALERT_ROLES = [
    ("🚀 New Launches", "New Launches", discord.ButtonStyle.primary),
    ("✅ Yurman Reviewed", "Yurman Reviewed", discord.ButtonStyle.success),
    ("🚨 Rug Warnings", "Rug Warnings", discord.ButtonStyle.danger),
    ("🐋 Whale Activity", "Whale Activity", discord.ButtonStyle.primary),
    ("📰 Market News", "Market News", discord.ButtonStyle.secondary),
    ("🚨 Emergency Alerts", "Emergency Alerts", discord.ButtonStyle.danger),
    ("💎 Premium Alerts", "Premium Alerts", discord.ButtonStyle.success),
]


def utc_now_text() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def is_reviewer(interaction: discord.Interaction) -> bool:
    if not interaction.guild:
        return False
    if interaction.user.id == interaction.guild.owner_id:
        return True
    return any(role.name in REVIEW_ROLES for role in getattr(interaction.user, "roles", []))


def has_named_role(interaction: discord.Interaction, allowed: set[str]) -> bool:
    if not interaction.guild:
        return False
    if interaction.user.id == interaction.guild.owner_id:
        return True
    return any(role.name in allowed for role in getattr(interaction.user, "roles", []))


async def audit_action(action: str, actor, target: str, details: str):
    channel = await find_text_channel("mod-log")
    if channel is None:
        return
    embed = discord.Embed(title=f"AUDIT — {action}", color=discord.Color.dark_blue(), timestamp=datetime.now(timezone.utc))
    embed.add_field(name="Actor", value=f"{actor} (`{getattr(actor, 'id', 'unknown')}`)", inline=False)
    embed.add_field(name="Target", value=target, inline=False)
    embed.add_field(name="Details", value=details[:1000] or "None", inline=False)
    await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())


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


async def get_or_create_role(guild: discord.Guild, name: str):
    role = discord.utils.get(guild.roles, name=name)
    if role is not None:
        return role
    return await guild.create_role(name=name, reason="YRD Alpha onboarding setup")


class AlertPreferencesView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        for index, (label, role_name, style) in enumerate(ALERT_ROLES):
            button = discord.ui.Button(label=label, style=style, custom_id=f"alerts:toggle:{role_name}", row=0 if index < 4 else 1)
            button.callback = self._callback_for(role_name)
            self.add_item(button)

    def _callback_for(self, role_name: str):
        async def callback(interaction: discord.Interaction):
            if not interaction.guild or not isinstance(interaction.user, discord.Member):
                await interaction.response.send_message("Use this button inside YRD Alpha.", ephemeral=True)
                return
            role = discord.utils.get(interaction.guild.roles, name=role_name)
            if role is None:
                await interaction.response.send_message("That alert role has not been configured yet.", ephemeral=True)
                return
            try:
                if role in interaction.user.roles:
                    await interaction.user.remove_roles(role, reason="Member disabled YRD Alpha alert preference")
                    await interaction.response.send_message(f"Disabled **{role_name}** notifications.", ephemeral=True)
                else:
                    if role_name == "Premium Alerts" and not any(r.name == "YRD Alpha Premium" for r in interaction.user.roles):
                        await interaction.response.send_message("Premium Alerts require the YRD Alpha Premium role.", ephemeral=True)
                        return
                    await interaction.user.add_roles(role, reason="Member enabled YRD Alpha alert preference")
                    await interaction.response.send_message(f"Enabled **{role_name}** notifications.", ephemeral=True)
            except discord.Forbidden:
                await interaction.response.send_message("I cannot manage that role. Move the bot role above the alert roles.", ephemeral=True)
        return callback


class OnboardingSessionView(discord.ui.View):
    def __init__(self, member_id: int):
        super().__init__(timeout=300)
        self.member_id = member_id
        self.rules_accepted = False
        self.risk_accepted = False
        self.language = None

    async def check_member(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.member_id:
            await interaction.response.send_message("This private onboarding session belongs to another member.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Accept Rules", style=discord.ButtonStyle.success)
    async def accept_rules(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self.check_member(interaction):
            return
        self.rules_accepted = True
        button.disabled = True
        button.label = "Rules Accepted"
        await interaction.response.edit_message(view=self)

    @discord.ui.button(label="Accept Risk Disclosure", style=discord.ButtonStyle.danger)
    async def accept_risk(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self.check_member(interaction):
            return
        self.risk_accepted = True
        button.disabled = True
        button.label = "Risk Accepted"
        await interaction.response.edit_message(view=self)

    @discord.ui.button(label="English", style=discord.ButtonStyle.primary, row=1)
    async def english(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self.check_member(interaction):
            return
        self.language = "English"
        await interaction.response.send_message("Language selected: English.", ephemeral=True)

    @discord.ui.button(label="Español", style=discord.ButtonStyle.primary, row=1)
    async def spanish(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self.check_member(interaction):
            return
        self.language = "Español"
        await interaction.response.send_message("Idioma seleccionado: Español.", ephemeral=True)

    @discord.ui.button(label="Finish Onboarding", style=discord.ButtonStyle.success, row=2)
    async def finish(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self.check_member(interaction):
            return
        missing = []
        if not self.rules_accepted:
            missing.append("accept the rules")
        if not self.risk_accepted:
            missing.append("accept the risk disclosure")
        if not self.language:
            missing.append("choose a language")
        if missing:
            await interaction.response.send_message("Before finishing, " + ", ".join(missing) + ".", ephemeral=True)
            return
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("Onboarding must be completed inside the server.", ephemeral=True)
            return
        verified = discord.utils.get(interaction.guild.roles, name="Verified Member")
        free = discord.utils.get(interaction.guild.roles, name="Free Member")
        language_role = discord.utils.get(interaction.guild.roles, name=self.language)
        roles = [role for role in (verified, free, language_role) if role]
        try:
            await interaction.user.add_roles(*roles, reason="Completed YRD Alpha Gatekeeper onboarding")
        except discord.Forbidden:
            await interaction.response.send_message("I cannot assign member roles. Move the bot role above Verified Member and Free Member.", ephemeral=True)
            return
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(
            content=f"✅ Onboarding complete in **{self.language}**. You now have Free Member access. Choose notification roles in #choose-your-alerts.",
            view=self,
        )


class VerificationEntryView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Begin Verification", style=discord.ButtonStyle.success, custom_id="gatekeeper:begin")
    async def begin(self, interaction: discord.Interaction, button: discord.ui.Button):
        text = (
            "**YRD Alpha Gatekeeper**\n"
            "1. Read #rules.\n2. Read #risk-disclaimer.\n"
            "3. Accept both below.\n4. Choose English or Español.\n5. Finish onboarding.\n\n"
            "YRD Alpha staff will never DM asking for a seed phrase, private key, or wallet connection."
        )
        await interaction.response.send_message(text, view=OnboardingSessionView(interaction.user.id), ephemeral=True)


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


def money_text(value) -> str:
    try:
        number = float(value or 0)
    except (TypeError, ValueError):
        return "Unavailable"
    if number >= 1_000_000_000:
        return f"${number / 1_000_000_000:.2f}B"
    if number >= 1_000_000:
        return f"${number / 1_000_000:.2f}M"
    if number >= 1_000:
        return f"${number / 1_000:.1f}K"
    return f"${number:,.2f}"


async def publish_live_candidate(pair: dict, engine: str, score: int):
    base = pair.get("baseToken") or {}
    contract = str(base.get("address") or "").strip()
    if not contract or contract in _scanner_seen or await candidate_exists(contract):
        return False
    alpha = await find_text_channel("alpha-scout")
    waiting = await find_text_channel("waiting-for-yurman")
    review = await find_text_channel("scout-review")
    if not all((alpha, waiting, review)):
        return False
    symbol = str(base.get("symbol") or "UNKNOWN").upper()[:20]
    liquidity = float(((pair.get("liquidity") or {}).get("usd")) or 0)
    market_cap = float(pair.get("marketCap") or pair.get("fdv") or 0)
    volume_h24 = float(((pair.get("volume") or {}).get("h24")) or 0)
    h1 = ((pair.get("txns") or {}).get("h1")) or {}
    buys, sells = int(h1.get("buys") or 0), int(h1.get("sells") or 0)
    created_ms = pair.get("pairCreatedAt")
    age_minutes = max(0, int((datetime.now(timezone.utc).timestamp() * 1000 - created_ms) / 60000)) if isinstance(created_ms, (int, float)) else None
    confidence = "HIGH" if score >= 85 else "MEDIUM"
    risk = "HIGH" if liquidity < 100_000 or sells > buys * 1.4 else "MEDIUM"
    embed = discord.Embed(
        title=f"🔎 YRD ALPHA SCOUT — POSSIBLE SETUP: ${symbol}",
        description=(
            f"**Engine:** {engine}\n"
            "DEX market activity passed the current screening threshold.\n\n"
            "⚠️ **Yurman has NOT reviewed this setup yet.**\n"
            "Wallet clustering, holder concentration, contract permissions, and X sentiment are not yet connected. Investigate before acting."
        ),
        color=discord.Color.gold(),
        timestamp=datetime.now(timezone.utc),
    )
    for name, value, inline in [
        ("Token", f"${symbol}", True), ("Market Cap", money_text(market_cap), True),
        ("Liquidity", money_text(liquidity), True), ("Volume", money_text(volume_h24) + " / 24h", True),
        ("Age", f"{age_minutes} minutes" if age_minutes is not None else "Unavailable", True),
        ("Buyers / Sellers", f"{buys} / {sells} (1h)", True), ("Risk", risk, True),
        ("Confidence", confidence, True), ("Signal Score", f"{score}/100", True),
        ("Contract", contract, False), ("Source Coverage", "DEX Screener official public API. Contract, wallet, and X checks unavailable.", False),
    ]:
        embed.add_field(name=name, value=value, inline=inline)
    url = pair.get("url")
    if url:
        embed.add_field(name="Research Link", value=str(url), inline=False)
    embed.set_footer(text=DISCLAIMER)
    public_message = await alpha.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
    waiting_embed = embed.copy()
    waiting_embed.title = f"⏳ WAITING FOR YURMAN — ${symbol}"
    waiting_embed.description = f"Live DEX candidate awaiting private review.\n[Open public Scout alert]({public_message.jump_url})\n\n{DISCLAIMER}"
    waiting_message = await waiting.send(embed=waiting_embed, allowed_mentions=discord.AllowedMentions.none())
    review_embed = embed.copy()
    review_embed.title = f"PRIVATE LIVE SCOUT REVIEW — ${symbol}"
    review_embed.description = "Choose one decision below. Only Yurman or an Admin can use these buttons."
    review_embed.add_field(name="Public Alert", value=public_message.jump_url, inline=False)
    review_embed.add_field(name="Waiting Card", value=waiting_message.jump_url, inline=False)
    await review.send(embed=review_embed, view=ReviewView(), allowed_mentions=discord.AllowedMentions.none())
    _scanner_seen.add(contract)
    return True


def score_pair(pair: dict) -> int:
    liquidity = float(((pair.get("liquidity") or {}).get("usd")) or 0)
    volume = float(((pair.get("volume") or {}).get("h24")) or 0)
    h1 = ((pair.get("txns") or {}).get("h1")) or {}
    buys, sells = int(h1.get("buys") or 0), int(h1.get("sells") or 0)
    change_h1 = float(((pair.get("priceChange") or {}).get("h1")) or 0)
    score = 0
    score += 25 if liquidity >= 100_000 else 18 if liquidity >= 50_000 else 10 if liquidity >= 25_000 else 0
    score += 25 if volume >= 500_000 else 18 if volume >= 150_000 else 10 if volume >= 50_000 else 0
    score += 20 if buys + sells >= 250 else 14 if buys + sells >= 80 else 8 if buys + sells >= 30 else 0
    score += 15 if buys >= max(1, sells) * 1.25 else 8 if buys >= sells else 0
    score += 10 if 3 <= change_h1 <= 80 else 4 if change_h1 > 0 else 0
    if liquidity and volume / liquidity >= 1:
        score += 5
    return min(score, 100)


async def dex_scanner_loop():
    await client.wait_until_ready()
    timeout = aiohttp.ClientTimeout(total=20)
    while not client.is_closed():
        try:
            async with aiohttp.ClientSession(timeout=timeout, headers={"User-Agent": "YRD-Alpha-Scout/1.0"}) as session:
                sources = [
                    ("NEW PROFILE SCANNER", "https://api.dexscreener.com/token-profiles/latest/v1"),
                    ("MOMENTUM SCANNER", "https://api.dexscreener.com/token-boosts/top/v1"),
                ]
                candidates = []
                for engine, endpoint in sources:
                    async with session.get(endpoint) as response:
                        if response.status != 200:
                            log.warning("DEX source %s returned HTTP %s", endpoint, response.status)
                            continue
                        profiles = await response.json()
                    if not isinstance(profiles, list):
                        continue
                    addresses = []
                    for profile in profiles:
                        if profile.get("chainId") == "solana" and profile.get("tokenAddress"):
                            addresses.append(profile["tokenAddress"])
                        if len(addresses) >= 20:
                            break
                    if not addresses:
                        continue
                    token_url = "https://api.dexscreener.com/tokens/v1/solana/" + ",".join(addresses)
                    async with session.get(token_url) as response:
                        if response.status != 200:
                            log.warning("DEX token lookup returned HTTP %s", response.status)
                            continue
                        pairs = await response.json()
                    best_by_token = {}
                    for pair in pairs if isinstance(pairs, list) else []:
                        address = str(((pair.get("baseToken") or {}).get("address")) or "")
                        liquidity = float(((pair.get("liquidity") or {}).get("usd")) or 0)
                        if address and (address not in best_by_token or liquidity > float(((best_by_token[address].get("liquidity") or {}).get("usd")) or 0)):
                            best_by_token[address] = pair
                    for pair in best_by_token.values():
                        score = score_pair(pair)
                        if score >= 60:
                            candidates.append((score, engine, pair))
                posted = 0
                for score, engine, pair in sorted(candidates, key=lambda item: item[0], reverse=True):
                    if await publish_live_candidate(pair, engine, score):
                        posted += 1
                    if posted >= 2:
                        break
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("DEX scanner cycle failed")
            status_channel = await find_text_channel("bot-status")
            if status_channel:
                try:
                    await status_channel.send("⚠️ YRD Alpha Scout data source DEX Screener is temporarily unavailable. Data coverage is incomplete.")
                except discord.DiscordException:
                    pass
        await asyncio.sleep(300)


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
            "DEX Screener public scanner: **active**.\n"
            "Wallet, contract, Axiom, FOMO, Trenchers, and X providers: **not connected yet**.",
            allowed_mentions=discord.AllowedMentions.none(),
        )
        _startup_notice_sent = True
    except discord.DiscordException:
        log.exception("Failed to post startup notice")


@client.event
async def on_ready():
    global _scanner_task
    log.info("Logged in as %s (%s)", client.user, client.user.id if client.user else "unknown")
    guild = client.get_guild(GUILD_ID)
    if guild is None:
        log.error("Bot is not connected to guild %s", GUILD_ID)
        return
    client.add_view(ReviewView())
    client.add_view(VerificationEntryView())
    client.add_view(AlertPreferencesView())
    try:
        synced = await tree.sync(guild=GUILD)
        log.info("Synced %d slash command(s) to %s", len(synced), guild.name)
    except discord.DiscordException:
        log.exception("Slash command sync failed")
    await client.change_presence(status=discord.Status.online, activity=discord.Activity(type=discord.ActivityType.watching, name="YRD Alpha"))
    await post_startup_notice_once()
    if _scanner_task is None or _scanner_task.done():
        _scanner_task = asyncio.create_task(dex_scanner_loop())


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
        "Review workflow: `active`\nDEX scanner: `active`\n"
        "Contract/wallet/X coverage: `not connected`",
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


@tree.command(name="setup_onboarding", description="Install Gatekeeper and alert-preference panels.", guild=GUILD)
async def setup_onboarding(interaction: discord.Interaction):
    if not is_reviewer(interaction):
        await interaction.response.send_message("Only Yurman or an Admin can install onboarding.", ephemeral=True)
        return
    if not interaction.guild:
        await interaction.response.send_message("Run this command inside YRD Alpha.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    try:
        for role_name in ["English", "Español"] + [role_name for _, role_name, _ in ALERT_ROLES]:
            await get_or_create_role(interaction.guild, role_name)
    except discord.Forbidden:
        await interaction.followup.send("I cannot create roles. Give the bot Manage Roles permission and keep its role near the top.", ephemeral=True)
        return

    verification = await find_text_channel("verification")
    alerts = await find_text_channel("choose-your-alerts")
    if verification is None or alerts is None:
        await interaction.followup.send("I need both #verification and #choose-your-alerts.", ephemeral=True)
        return

    gate = discord.Embed(
        title="🛡️ YRD ALPHA GATEKEEPER",
        description=(
            "Complete verification before entering the member areas.\n\n"
            "• Accept the server rules\n• Accept the meme-coin risk disclosure\n"
            "• Choose English or Español\n• Receive Verified Member and Free Member access\n\n"
            "**Staff will never DM asking for your seed phrase, private key, payment, or wallet connection.**"
        ),
        color=discord.Color.green(),
    )
    gate.set_footer(text="Never share wallet secrets with anyone—including staff.")
    await verification.send(embed=gate, view=VerificationEntryView(), allowed_mentions=discord.AllowedMentions.none())

    choices = discord.Embed(
        title="🔔 CHOOSE YOUR YRD ALPHA ALERTS",
        description=(
            "Use the buttons to turn notification roles on or off.\n"
            "Premium Alerts require the YRD Alpha Premium role.\n\n"
            "Alerts report observed activity and risk; they are never guaranteed-profit instructions."
        ),
        color=discord.Color.blue(),
    )
    await alerts.send(embed=choices, view=AlertPreferencesView(), allowed_mentions=discord.AllowedMentions.none())
    await interaction.followup.send("Gatekeeper and alert preferences installed.", ephemeral=True)


@tree.command(name="publish_rules", description="Publish official YRD Alpha rules and risk information.", guild=GUILD)
async def publish_rules(interaction: discord.Interaction):
    if not is_reviewer(interaction):
        await interaction.response.send_message("Only Yurman or an Admin can publish official server information.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    welcome = await find_text_channel("welcome")
    rules = await find_text_channel("rules")
    risk_channel = await find_text_channel("risk-disclaimer")
    how_it_works = await find_text_channel("how-yrd-alpha-works")
    missing = [name for name, channel in [("welcome", welcome), ("rules", rules), ("risk-disclaimer", risk_channel), ("how-yrd-alpha-works", how_it_works)] if channel is None]
    if missing:
        await interaction.followup.send("Missing channel(s): " + ", ".join(missing), ephemeral=True)
        return

    welcome_embed = discord.Embed(
        title="WELCOME TO YRD ALPHA",
        description=(
            "YRD Alpha is Yurman's meme-coin research, education, risk-management, community, and market-monitoring server.\n\n"
            "Scout may publish possible setups before Yurman reviews them. An alert is research—not permission to buy, a promise of profit, or a guarantee.\n\n"
            "Start with #rules, #risk-disclaimer, and #verification."
        ),
        color=discord.Color.blue(),
    )
    welcome_embed.set_footer(text=DISCLAIMER)

    rules_embed = discord.Embed(
        title="YRD ALPHA — OFFICIAL SERVER RULES",
        description=(
            "**1. No scams or phishing.** No wallet drainers, malicious links, fake airdrops, fake presales, or requests for seed phrases/private keys.\n\n"
            "**2. No pump coordination or manipulation.** Do not organize coordinated buying, dumping, wash trading, or deceptive promotion.\n\n"
            "**3. No guaranteed-profit claims.** Do not claim any token, setup, trader, or strategy is guaranteed or risk-free.\n\n"
            "**4. No fake evidence.** Fake P&L screenshots, fabricated transactions, misleading edits, and false research are prohibited.\n\n"
            "**5. No impersonation.** Do not impersonate Yurman, staff, support, projects, influencers, or other members.\n\n"
            "**6. No harassment or harmful content.** No threats, hate, targeted harassment, doxxing, or NSFW content.\n\n"
            "**7. No spam.** No flooding, duplicate messages, mass mentions, unsolicited advertising, or repeated token requests. Cooldowns may apply.\n\n"
            "**8. Disclose conflicts.** If you hold, promote, work for, or were paid by a project, say so clearly.\n\n"
            "**9. Respect moderation.** Escalation may be Warning → Timeout → Restricted → Ban. Serious scams/phishing may receive an immediate ban.\n\n"
            "**10. Keep results honest.** Wins and losses remain visible. Staff will not delete losing reviewed setups to create a false record."
        ),
        color=discord.Color.red(),
    )
    rules_embed.set_footer(text="Using YRD Alpha means agreeing to these rules.")

    risk_embed = discord.Embed(
        title="MEME-COIN RISK DISCLOSURE",
        description=(
            "Meme coins are extremely volatile and speculative. You can lose your entire position, sometimes in seconds.\n\n"
            "Liquidity may disappear; selling can cause severe slippage or may become impossible. Contracts may contain malicious permissions, transfer restrictions, mint/freeze authority, hidden taxes, or honeypot-like behavior. Developers and large holders may sell without warning. Social activity, whale activity, volume, and Scout confidence do not guarantee price appreciation.\n\n"
            "YRD Alpha, Scout alerts, Yurman's reviews, community posts, paper trading, calculators, and educational material are research tools—not personalized financial advice or automatic trade instructions.\n\n"
            "Only risk money you can afford to lose. Verify contracts independently, decide the maximum loss before entry, and protect wallet credentials. Past results do not guarantee future results."
        ),
        color=discord.Color.orange(),
    )
    risk_embed.set_footer(text="Never share your seed phrase or private key. YRD Alpha staff will never ask for them.")

    workflow_embed = discord.Embed(
        title="HOW YRD ALPHA WORKS",
        description=(
            "**1. Token detected or submitted**\n"
            "**2. Scout analyzes available market, wallet, contract, and social signals**\n"
            "**3. Public Scout alert may appear as NOT REVIEWED**\n"
            "**4. Candidate enters the private Yurman review queue**\n"
            "**5. Yurman/Admin selects APPROVE, WATCH, REJECT, HIGH RISK, or RUG WARNING**\n"
            "**6. Monitoring and risk alerts continue when data is available**\n"
            "**7. The journal keeps the result—win or loss**\n"
            "**8. After-action education explains what happened**\n\n"
            "No stage promises profit. Missing or unavailable data must be stated openly."
        ),
        color=discord.Color.green(),
    )
    workflow_embed.set_footer(text=DISCLAIMER)

    await welcome.send(embed=welcome_embed, allowed_mentions=discord.AllowedMentions.none())
    await rules.send(embed=rules_embed, allowed_mentions=discord.AllowedMentions.none())
    await risk_channel.send(embed=risk_embed, allowed_mentions=discord.AllowedMentions.none())
    await how_it_works.send(embed=workflow_embed, allowed_mentions=discord.AllowedMentions.none())
    await interaction.followup.send("Official welcome, rules, risk disclosure, and workflow published.", ephemeral=True)


@tree.command(name="warn", description="Warn a member and record the action.", guild=GUILD)
@app_commands.describe(member="Member to warn", reason="Reason for the warning")
async def warn(interaction: discord.Interaction, member: discord.Member, reason: str):
    if not has_named_role(interaction, MODERATOR_ROLES):
        await interaction.response.send_message("Moderator permission required.", ephemeral=True)
        return
    if member.id == interaction.guild.owner_id or member.bot:
        await interaction.response.send_message("That account cannot be warned with this command.", ephemeral=True)
        return
    await audit_action("WARNING", interaction.user, f"{member} (`{member.id}`)", reason)
    try:
        await member.send(f"You received a warning in YRD Alpha.\nReason: {reason}\nRepeated violations may lead to Timeout → Restricted → Ban.")
    except discord.DiscordException:
        pass
    await interaction.response.send_message(f"Warning recorded for {member.mention}.", ephemeral=True)


@tree.command(name="timeout_member", description="Temporarily timeout a member.", guild=GUILD)
@app_commands.describe(member="Member to timeout", minutes="Timeout length in minutes", reason="Reason for timeout")
async def timeout_member(interaction: discord.Interaction, member: discord.Member, minutes: app_commands.Range[int, 1, 40320], reason: str):
    if not has_named_role(interaction, MODERATOR_ROLES):
        await interaction.response.send_message("Moderator permission required.", ephemeral=True)
        return
    try:
        await member.timeout(datetime.now(timezone.utc) + timedelta(minutes=minutes), reason=reason)
    except discord.Forbidden:
        await interaction.response.send_message("I cannot timeout that member. Check role order and Moderate Members permission.", ephemeral=True)
        return
    await audit_action("TIMEOUT", interaction.user, f"{member} (`{member.id}`)", f"{minutes} minutes — {reason}")
    await interaction.response.send_message(f"{member.mention} timed out for {minutes} minutes.", ephemeral=True)


@tree.command(name="restrict_member", description="Assign or remove the Restricted role.", guild=GUILD)
@app_commands.describe(member="Member to restrict", enabled="True to restrict; False to remove restriction", reason="Reason")
async def restrict_member(interaction: discord.Interaction, member: discord.Member, enabled: bool, reason: str):
    if not has_named_role(interaction, MODERATOR_ROLES):
        await interaction.response.send_message("Moderator permission required.", ephemeral=True)
        return
    role = discord.utils.get(interaction.guild.roles, name="Restricted")
    if role is None:
        await interaction.response.send_message("The Restricted role is missing.", ephemeral=True)
        return
    try:
        if enabled:
            await member.add_roles(role, reason=reason)
        else:
            await member.remove_roles(role, reason=reason)
    except discord.Forbidden:
        await interaction.response.send_message("I cannot manage Restricted. Move the bot role above it.", ephemeral=True)
        return
    action = "RESTRICTED" if enabled else "RESTRICTION REMOVED"
    await audit_action(action, interaction.user, f"{member} (`{member.id}`)", reason)
    await interaction.response.send_message(f"{action}: {member.mention}", ephemeral=True)


@tree.command(name="ban_member", description="Ban a member for a serious violation.", guild=GUILD)
@app_commands.describe(member="Member to ban", reason="Reason for ban", delete_hours="Delete recent message history in hours")
async def ban_member(interaction: discord.Interaction, member: discord.Member, reason: str, delete_hours: app_commands.Range[int, 0, 168] = 0):
    if not has_named_role(interaction, {"Yurman / Owner", "Admin"}):
        await interaction.response.send_message("Only Yurman or an Admin can ban members.", ephemeral=True)
        return
    if member.id == interaction.guild.owner_id:
        await interaction.response.send_message("The server owner cannot be banned.", ephemeral=True)
        return
    await audit_action("BAN", interaction.user, f"{member} (`{member.id}`)", reason)
    try:
        await interaction.guild.ban(member, reason=reason, delete_message_seconds=delete_hours * 3600)
    except discord.Forbidden:
        await interaction.response.send_message("I cannot ban that member. Check role order and Ban Members permission.", ephemeral=True)
        return
    await interaction.response.send_message(f"Banned {member}.", ephemeral=True)


@tree.command(name="report_scam", description="Privately report scam evidence to staff.", guild=GUILD)
@app_commands.describe(subject="User, token, project, or wallet being reported", evidence="Links, wallet addresses, transaction IDs, and what happened")
async def report_scam(interaction: discord.Interaction, subject: str, evidence: str):
    channel = await find_text_channel("scam-evidence")
    if channel is None:
        await interaction.response.send_message("I could not find the private #scam-evidence channel.", ephemeral=True)
        return
    embed = discord.Embed(title="SCAM EVIDENCE REPORT", color=discord.Color.red(), timestamp=datetime.now(timezone.utc))
    embed.add_field(name="Reporter", value=f"{interaction.user} (`{interaction.user.id}`)", inline=False)
    embed.add_field(name="Subject", value=subject[:1000], inline=False)
    embed.add_field(name="Evidence", value=evidence[:1000], inline=False)
    embed.set_footer(text="Staff: preserve screenshots, user IDs, wallet addresses, contracts, links, and timestamps.")
    await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
    await interaction.response.send_message("Your report was sent privately to staff. Do not confront suspected scammers.", ephemeral=True)


TICKET_TYPES = {"membership", "billing", "server", "scout", "scam", "member", "appeal", "premium", "other"}


@tree.command(name="ticket", description="Open a private YRD Alpha support ticket.", guild=GUILD)
@app_commands.describe(category="Membership, billing, server, scout, scam, member, appeal, premium, or other", details="Explain what you need help with")
async def ticket(interaction: discord.Interaction, category: str, details: str):
    category_key = category.lower().strip()
    if category_key not in TICKET_TYPES:
        await interaction.response.send_message("Category must be membership, billing, server, scout, scam, member, appeal, premium, or other.", ephemeral=True)
        return
    guild = interaction.guild
    support_category = discord.utils.get(guild.categories, name="SUPPORT TICKETS")
    try:
        if support_category is None:
            support_category = await guild.create_category("SUPPORT TICKETS", reason="YRD Alpha private support")
        safe_name = re.sub(r"[^a-z0-9-]", "-", interaction.user.name.lower())[:16].strip("-") or "member"
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            interaction.user: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, attach_files=True),
        }
        bot_member = guild.me
        if bot_member:
            overwrites[bot_member] = discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True, read_message_history=True)
        for role in guild.roles:
            if role.name in SUPPORT_ROLES:
                overwrites[role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, manage_messages=True)
        channel = await guild.create_text_channel(
            f"ticket-{safe_name}-{str(interaction.user.id)[-4:]}",
            category=support_category,
            topic=f"YRD-TICKET|{interaction.user.id}|{category_key}",
            overwrites=overwrites,
            reason=f"Support ticket opened by {interaction.user}",
        )
    except discord.Forbidden:
        await interaction.response.send_message("I cannot create private ticket channels. Check Manage Channels permission.", ephemeral=True)
        return
    await channel.send(
        f"{interaction.user.mention} **Private {category_key.title()} Ticket**\n{details}\n\n"
        "YRD Alpha staff will never ask for your seed phrase or private key. Use `/close_ticket` when resolved.",
        allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False),
    )
    await audit_action("TICKET OPENED", interaction.user, channel.mention, f"{category_key}: {details}")
    await interaction.response.send_message(f"Private ticket created: {channel.mention}", ephemeral=True)


@tree.command(name="close_ticket", description="Close and archive the current support ticket.", guild=GUILD)
async def close_ticket(interaction: discord.Interaction):
    channel = interaction.channel
    topic = getattr(channel, "topic", "") or ""
    if not topic.startswith("YRD-TICKET|"):
        await interaction.response.send_message("Use this command inside a YRD Alpha ticket channel.", ephemeral=True)
        return
    parts = topic.split("|")
    creator_id = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
    if interaction.user.id != creator_id and not has_named_role(interaction, SUPPORT_ROLES):
        await interaction.response.send_message("Only the ticket owner or staff can close this ticket.", ephemeral=True)
        return
    creator = interaction.guild.get_member(creator_id)
    try:
        if creator:
            await channel.set_permissions(creator, view_channel=True, send_messages=False, read_message_history=True)
        new_name = channel.name if channel.name.startswith("closed-") else ("closed-" + channel.name)[:100]
        await channel.edit(name=new_name, topic=topic + "|CLOSED", reason="Support ticket archived")
    except discord.Forbidden:
        await interaction.response.send_message("I cannot archive this channel. Check Manage Channels permission.", ephemeral=True)
        return
    await audit_action("TICKET CLOSED", interaction.user, channel.mention, "Ticket archived; history preserved")
    await interaction.response.send_message("Ticket closed and preserved for staff records.")


@tree.command(name="book_yurman", description="Request a call or review with Yurman.", guild=GUILD)
@app_commands.describe(reason="Reason for the request", preferred_time="Preferred date/time and timezone")
async def book_yurman(interaction: discord.Interaction, reason: str, preferred_time: str):
    channel = await find_text_channel("support-log")
    if channel is None:
        await interaction.response.send_message("I could not find the private #support-log channel.", ephemeral=True)
        return
    premium = any(role.name == "YRD Alpha Premium" for role in getattr(interaction.user, "roles", []))
    embed = discord.Embed(title="CALL REQUEST — PREMIUM PRIORITY" if premium else "CALL REQUEST", color=discord.Color.gold(), timestamp=datetime.now(timezone.utc))
    embed.add_field(name="Member", value=f"{interaction.user} (`{interaction.user.id}`)", inline=False)
    embed.add_field(name="Preferred Time", value=preferred_time[:1000], inline=False)
    embed.add_field(name="Reason", value=reason[:1000], inline=False)
    embed.add_field(name="Status", value="Awaiting staff confirmation", inline=False)
    await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
    await interaction.response.send_message("Your request was sent to staff. A time is not confirmed until staff responds.", ephemeral=True)


@tree.command(name="concierge", description="Ask the YRD Assistant a common server or trading-education question.", guild=GUILD)
@app_commands.describe(question="Your question")
async def concierge(interaction: discord.Interaction, question: str):
    q = question.lower()
    if "market cap" in q:
        answer = "Market cap is token price multiplied by circulating supply. It is not the same as liquidity or cash available to sell."
    elif "2x" in q or "double" in q:
        answer = "A 2x means the value doubled: a $50 paper position would become $100 before fees and slippage."
    elif "premium" in q:
        answer = "Premium pricing and payments are not active yet. Staff will announce them officially in #yrd-updates."
    elif "ticket" in q or "support" in q:
        answer = "Use `/ticket` with a category and details to create a private support channel."
    elif "yurman" in q or "call" in q:
        answer = "Yurman may not be available right now. Use `/book_yurman` to send a private request to staff."
    elif "scout" in q:
        answer = "Scout reports possible setups and risk signals. Public alerts may be unreviewed; only Yurman/Admin decisions appear as reviewed. No alert guarantees profit."
    elif "risk" in q or "stop" in q:
        answer = "Use `/risk` before entry to calculate position size, loss at stop, and risk/reward. Decide maximum loss before trading."
    else:
        answer = "I do not have a verified answer for that yet. Use `/ticket` so staff can help without guessing."
    await interaction.response.send_message(f"🧠 **YRD Assistant**\n{answer}\n\n{DISCLAIMER}", ephemeral=True)


@tree.command(name="feedback", description="Send an anonymous suggestion to the community feedback channel.", guild=GUILD)
@app_commands.describe(suggestion="Your suggestion")
async def feedback(interaction: discord.Interaction, suggestion: str):
    channel = await find_text_channel("feedback")
    if channel is None:
        await interaction.response.send_message("I could not find #feedback.", ephemeral=True)
        return
    embed = discord.Embed(title="ANONYMOUS MEMBER FEEDBACK", description=suggestion[:4000], color=discord.Color.purple(), timestamp=datetime.now(timezone.utc))
    await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
    await interaction.response.send_message("Feedback submitted anonymously to the public channel.", ephemeral=True)


@tree.command(name="poll_create", description="Create an official two-option community poll.", guild=GUILD)
@app_commands.describe(question="Poll question", option_one="First option", option_two="Second option")
async def poll_create(interaction: discord.Interaction, question: str, option_one: str, option_two: str):
    if not is_reviewer(interaction):
        await interaction.response.send_message("Only Yurman or an Admin can create official polls.", ephemeral=True)
        return
    channel = interaction.channel
    embed = discord.Embed(title="YRD ALPHA POLL", description=question[:4000], color=discord.Color.blue())
    embed.add_field(name="1️⃣ Option 1", value=option_one[:1000], inline=False)
    embed.add_field(name="2️⃣ Option 2", value=option_two[:1000], inline=False)
    message = await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
    try:
        await message.add_reaction("1️⃣")
        await message.add_reaction("2️⃣")
    except discord.Forbidden:
        pass
    await interaction.response.send_message("Poll posted.", ephemeral=True)


@tree.command(name="pnl_summary", description="Privately calculate personal P&L statistics.", guild=GUILD)
@app_commands.describe(starting_balance="Starting balance", total_wins="Total dollars gained", total_losses="Total dollars lost", wins="Number of wins", losses="Number of losses", largest_drawdown="Largest drawdown in dollars")
async def pnl_summary(interaction: discord.Interaction, starting_balance: float, total_wins: float, total_losses: float, wins: app_commands.Range[int, 0, 100000], losses: app_commands.Range[int, 0, 100000], largest_drawdown: float):
    if min(starting_balance, total_wins, total_losses, largest_drawdown) < 0:
        await interaction.response.send_message("Use positive numbers; enter losses as a positive total.", ephemeral=True)
        return
    trades = wins + losses
    current = starting_balance + total_wins - total_losses
    avg_win = total_wins / wins if wins else 0
    avg_loss = total_losses / losses if losses else 0
    win_rate = wins / trades * 100 if trades else 0
    await interaction.response.send_message(
        "📊 **Private P&L Summary**\n"
        f"Starting balance: `${starting_balance:,.2f}`\nCurrent balance: `${current:,.2f}`\n"
        f"Wins / Losses: `{wins} / {losses}`\nWin rate: `{win_rate:.1f}%`\n"
        f"Average win: `${avg_win:,.2f}`\nAverage loss: `${avg_loss:,.2f}`\n"
        f"Largest drawdown: `${largest_drawdown:,.2f}`\n\nOnly you can see this response.",
        ephemeral=True,
    )


@tree.command(name="incident", description="Record a private operational incident.", guild=GUILD)
@app_commands.describe(title="Short incident title", systems="Systems affected", details="What happened and response so far")
async def incident(interaction: discord.Interaction, title: str, systems: str, details: str):
    if not has_named_role(interaction, MODERATOR_ROLES):
        await interaction.response.send_message("Staff permission required.", ephemeral=True)
        return
    channel = await find_text_channel("incident-room")
    if channel is None:
        await interaction.response.send_message("I could not find #incident-room.", ephemeral=True)
        return
    embed = discord.Embed(title=f"INCIDENT — {title[:200]}", color=discord.Color.orange(), timestamp=datetime.now(timezone.utc))
    embed.add_field(name="Systems Affected", value=systems[:1000], inline=False)
    embed.add_field(name="What Happened / Response", value=details[:1000], inline=False)
    embed.add_field(name="Status", value="OPEN", inline=False)
    embed.add_field(name="Recorded By", value=f"{interaction.user} (`{interaction.user.id}`)", inline=False)
    await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
    await audit_action("INCIDENT OPENED", interaction.user, title, systems)
    await interaction.response.send_message("Incident recorded privately.", ephemeral=True)


async def main():
    async with client:
        await client.start(TOKEN)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
