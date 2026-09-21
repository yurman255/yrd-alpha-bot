# YRD Alpha — Hosted Runtime Starter

This package is the missing persistent Discord Gateway runtime. It does **not** replace the existing structure installer and it does **not** claim live market scanning yet.

## What it does now

- Keeps the Discord bot online while the host is running.
- Sets the bot presence to `Watching YRD Alpha`.
- Posts an online notice to `#bot-status` when the runtime starts.
- Adds `/status` and `/ping`.
- Adds owner-only `/scout_test`, which posts a clearly labeled TEST card into `#alpha-scout`.
- Adds owner-only `/post_update` for `#yrd-updates`.
- Does not require the privileged Message Content intent.
- Does not buy/sell tokens, sign wallets, or invent market data.

## Railway deployment

1. Put the files in this folder into a private GitHub repository.
2. In Railway, create a new project/service and connect that GitHub repository.
3. In the Railway service **Variables** tab add:
   - `DISCORD_TOKEN` = the current token from Discord Developer Portal.
   - `GUILD_ID` = the numeric ID of the YRD Alpha server.
4. Do **not** put the token inside `bot.py`, GitHub, screenshots, Discord messages, or ChatGPT.
5. Deploy the service. Railway should detect the root `Dockerfile` automatically.
6. Open Railway logs. A healthy startup includes messages similar to:
   - `Logged in as ...`
   - `Connected to guild: YRD Alpha ...`
   - `Synced 4 slash command(s)`
7. In Discord, confirm the bot is online and run `/status`.
8. As the actual server owner, run `/scout_test`. A TEST card should appear in `#alpha-scout`.

## If the bot stays offline

Check Railway logs first. The common errors are:

- `DISCORD_TOKEN is missing`: add the token in Railway Variables.
- `GUILD_ID is missing or invalid`: add the numeric server ID.
- `Bot is not connected to guild`: the ID is wrong or the bot is not invited to that server.
- `403 / Forbidden` or a permission message: the bot role cannot access/send in the target channel.
- `401 / Improper token`: reset the bot token in the Discord Developer Portal and replace the Railway variable.

Changing a Railway variable requires redeploying/applying the staged change.

## Important separation

The old `setup.py` is a one-time REST installer. It creates roles/channels and can post starter text, then exits. This new `bot.py` holds a persistent Gateway connection so the account can appear online.

Live Scout scanning, moderation, verification, wallet analytics, rug/liquidity/holder monitoring, and external provider integrations are still separate modules to implement after this runtime is verified online.
