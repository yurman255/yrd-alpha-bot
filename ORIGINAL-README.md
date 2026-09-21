# YRD Alpha setup — structure installer v0.1

Prepared for Yurman's application **1551296384437854379**.

## What is ready

A standard-library Python installer creates or reconciles 13 categories, 50 text channels, 4 voice channels, the 13 requested main roles in order, and 9 optional alert/language roles. It posts rules, disclosures, service-status notices, and introductory content. It checks the resulting Discord permission overwrites and role hierarchy before reporting success. A saved server map is available for later runtime deployment.

**Not yet applied to your server. Not a deployed scanner or moderation bot.** Offline tests passed; real Discord API execution still requires your token and authorization. A mocked API test is not a substitute for live verification.

## Run on your computer

1. Update the installed bot's permissions using this link. Select **YRD Alpha** and authorize. No Administrator permission is requested. Voice, reactions, and application commands are included because Discord only lets a bot configure permissions it possesses:

   https://discord.com/oauth2/authorize?client_id=1551296384437854379&scope=bot%20applications.commands&permissions=2452737104

2. Extract the ZIP to a folder. Python 3.10 or later is required. Windows download: https://www.python.org/downloads/windows/
3. In Discord, enable **User Settings → Advanced → Developer Mode**. Copy **Server ID** from YRD Alpha's context menu and **User ID** from your own profile/context menu. These identifiers are not passwords.
4. In https://discord.com/developers/applications select **YRD System → Bot → Reset Token**. Keep the generated token private. Do not paste it into ChatGPT, screenshots, Discord messages, or a public document.
5. Double-click `RUN-WINDOWS.cmd`. On macOS/Linux, open a terminal in this folder and run `python3 setup.py --apply`.
6. Paste the token into the local hidden prompt; it will not visibly appear. Enter the server ID and your user ID. Read the printed plan, then type the exact confirmation requested.
7. The installer makes changes only after checking the application, server name, owner ID, permissions, and existing structure. It saves a local pre-change snapshot and a report. Keep that folder private; it contains server metadata but no token.
8. A successful run ends with **STRUCTURE VERIFIED**. Send only `setup-report.json` back to continue, or report the error text without credentials.

The bot can stay offline in the member list: this installer uses Discord's REST API once, not a persistent Gateway connection. A hosting environment is still needed for 24/7 services.

## Permission design and limitations

- Actual Discord ownership stays with Yurman. The named Owner role is a label; it does not transfer server ownership.
- The managed YRD System role must remain above roles it configures. The main human roles retain their requested order below that managed bot role. Notification/language roles are below the main roles.
- `@everyone` has no guild-level permissions. New arrivals see only the read-only start area.
- Verified Member grants member access. Free Member, contributor, trader, paper-trader, alert and language labels grant no access by themselves.
- Premium is private to Premium members and Owner/Admin/Moderator roles. Pricing and payment collection are disabled. Staff must verify a member before manually granting Premium.
- The staff section admits Owner/Admin/Moderator; the approval queue, command center, analytics and bot status admit only Owner/Admin. Support does not inherit all staff information. Ticket-specific access is a future runtime feature.
- Admin and Moderator are deliberately not granted Administrator, Manage Roles, Ban, or Timeout by this structure installer. Future command handlers must check numeric user/role IDs and receive narrowly scoped permissions. Names alone are not authorization. Only the real server owner can use Discord's unrestricted owner privileges.
- Public/member machine feeds are read-only for human roles, except the actual server owner whose platform privileges bypass overwrites. Future bots will post official/admin-reviewed messages through checked commands.
- Restricted and Muted remove message, reaction, attachment, command, and voice participation permissions, even when combined with Premium or staff roles. They retain reading access to areas already permitted by membership. They are not automatic sanctions: no moderation service is running yet. Do not give these users additional roles/member-specific allows; Discord's role allows can override role denies. Native timeouts should be used for timed sanctions once deployed.
- Threads, webhooks, mass mentions, and external-app privileges are not granted by these roles. No one is automatically promoted to staff or Premium.
- The original default general/text/voice channels are preserved and locked to the actual owner and setup bot. Unknown channels, roles, integrations or an Administrator-enabled setup bot cause an early stop for an audit.
- This is a fresh-server installer. A rerun reconciles known names and overwrites managed setup policies, so preserve intentional later customization separately. Duplicate names cause an abort. Do not run concurrently.
- The installer takes a metadata snapshot but does not restore automatically. A snapshot is not a backup of messages or a recurring backup system.
- Discord owners and sufficiently privileged people can still delete messages. Tamper-resistant journal retention requires an external append-only database and restricted retention controls.

## Validation performed

Run `python3 -m unittest -v test_setup.py`.

16 offline tests cover onboarding gates, member/Premium/staff isolation, role combinations with sanctions, read-only feeds, required permissions, wrong-application rejection, missing-permission rejection, and a full mocked install plus rerun with no duplicate roles/channels/starter posts. The token is checked absent from generated state files in the mock test.

The live installer checks fetched permission overwrites and role permissions after applying changes. Before inviting the community, additionally test Discord's role-view feature and separate test member accounts, then complete Gatekeeper and moderation deployment.

## Current blockers

- A private bot-token entry and owner authorization are required to apply the structure.
- Automatic Gatekeeper and moderation remain unfinished Phase 1 work.
- Persistent hosting and three additional bot applications are needed for the planned four identities.
- Authorized provider credentials and permitted API access must be verified before implementing live market sources. No FOMO, Pump.fun, X, Robinhood or other market integration is claimed active.

## Official references checked

- https://docs.discord.com/developers/quick-start/getting-started
- https://docs.discord.com/developers/topics/permissions
- https://docs.discord.com/developers/resources/guild
- https://docs.discord.com/developers/resources/channel
- https://docs.discord.com/developers/topics/rate-limits
