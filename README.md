# Staff Sentinel

A single-server moderation bot that flags staff name and avatar lookalikes, accepts private reports, copies screenshot evidence into a restricted moderator channel, and sends safety guidance to newcomers. It does not automate normal user accounts, read other users’ DMs, or take automatic moderation actions.

## Setup

Requires Python 3.11+ and a machine that stays online.

1. Create an application named **Staff Sentinel** and its bot in the [Discord Developer Portal](https://discord.com/developers/applications). Do not include “Discord” in the application name; the application creation form rejects it. Enable **Server Members Intent** on the Bot page. Message Content and Presence intents are not required. Discord may require approval for privileged intents depending on the application's size and eligibility.
2. Install the bot in your server by following the detailed instructions below. Use the `bot` and `applications.commands` scopes, with **View Channels**, **Send Messages**, **Embed Links**, and **Attach Files** permissions. Do not grant Administrator, Ban Members, or Manage Messages to the bot.
3. Create a private moderator text channel. Deny **View Channel** to `@everyone`; allow your moderator roles and the bot to view it. Moderator roles with explicit access must have Manage Messages, Manage Server, or Administrator. The bot refuses delivery if it detects an ordinary role/member with access. Review combined role permissions and channel membership yourself as well; server administrators always have access.
4. Enable Developer Mode in Discord, then copy your server ID, moderator channel ID, and the actual employees’ account IDs. Edit the example configuration; employee IDs are the trusted allowlist. Roles or copied names never make someone trusted. Keep `protected_names` populated with important staff names and aliases so detection still works when an employee is absent or not cached.

### Step 2 in detail: install Staff Sentinel

1. Sign in to the [Developer Portal](https://discord.com/developers/applications) and open **Staff Sentinel**.
2. Open **Installation** in the left sidebar. Under **Installation Contexts**, enable **Guild Install**. User Install is unnecessary for this server moderation bot and can be disabled.
3. Under **Install Link**, select **Discord Provided Link**.
4. Under **Default Install Settings → Guild Install → Scopes**, select **bot** and **applications.commands**.
5. In the bot **Permissions** selector, select **View Channels**, **Send Messages**, **Embed Links**, and **Attach Files**. Save changes if prompted.
6. Copy the URL under **Install Link** and open it in your browser. Select **Add to server** if asked to choose an installation type.
7. Select the target server, continue, review the permissions, and click **Authorize**. Complete any verification prompt. You must own the server or have **Manage Server** permission; if the server is missing, check the signed-in account and your server permissions.
8. Confirm Staff Sentinel appears in the server member list. Continue with setup step 3 to grant it access to the private moderator channel; installation permissions do not override that channel’s privacy settings.

Installation adds the account but does not run the Python bot. It will remain offline until you configure and start `bot.py` using the commands below. Startup registers `/staff-safety` and `/report-impersonation` in the configured server. This implementation uses a Gateway connection; leave **Interactions Endpoint URL** unset.

Reference: [Discord’s official application installation walkthrough](https://docs.discord.com/developers/quick-start/getting-started).

### Step 3 in detail: grant access to the moderator channel

If startup shows `Configuration error: Bot needs View Channel, Send Messages, Embed Links, and Attach Files in moderator channel`, the bot is connected but lacks one or more required permissions in that channel.

1. Right-click your private moderator channel → **Edit Channel**.
2. Open **Permissions**.
3. Select **Staff Sentinel**. If it is not listed, click **Add members or roles** and add the bot.
4. Set each of these permissions to the green **✓ Allow**:
   - **View Channel**
   - **Send Messages**
   - **Embed Links**
   - **Attach Files**
5. Click **Save Changes**.

Keep **View Channel** denied for **@everyone** so reports stay private. The bot does not need Administrator. Ensure the moderator roles with access meet the requirements in setup step 3 above.

Restart the bot to rerun its startup permission check. If the error persists, confirm `moderator_channel_id` in `config.json` matches this channel: right-click the channel → **Copy Channel ID**. Developer Mode must be enabled to copy IDs, as explained below.

### Step 4 in detail: enable Developer Mode and copy IDs

On Discord desktop or in your browser:

1. Click the **gear icon** beside your username at the bottom left to open **User Settings**.
2. Scroll down the settings sidebar and select **Advanced**.
3. Turn on **Developer Mode**.
4. Close settings.

You can now copy the IDs needed for `config.json`:

- **Server ID:** Right-click your server’s icon in the left sidebar → **Copy Server ID**.
- **Moderator channel ID:** Right-click your private moderator channel → **Copy Channel ID**.
- **Employee account ID:** Right-click the employee’s name or avatar → **Copy User ID**. Repeat for each trusted employee.

After copying `config.example.json` to `config.json` as shown below, replace the corresponding fields with your copied IDs:

```json
{
  "guild_id": "YOUR_SERVER_ID",
  "moderator_channel_id": "YOUR_MODERATOR_CHANNEL_ID",
  "employee_ids": [
    "FIRST_EMPLOYEE_USER_ID",
    "SECOND_EMPLOYEE_USER_ID"
  ]
}
```

Replace the placeholders with the numeric IDs, keeping the quotation marks. Include only actual employee accounts in `employee_ids`; remove the second entry if you have only one employee. This example shows only the ID fields: edit them in the existing configuration so the other settings remain present.

### Configure and run

Get your bot token from the Developer Portal:

1. Open the [Discord Developer Portal](https://discord.com/developers/applications) and select **Staff Sentinel**.
2. Click **Bot** in the left sidebar.
3. Under **Token**, click **Reset Token** to generate a token. Confirm the prompt and complete any authentication Discord requests. Resetting invalidates the previous token, so any running instance using it must be updated.
4. Click **Copy** to copy the newly generated bot token. Store it securely; if you lose it, generate another using **Reset Token**.
5. When the terminal commands below prompt for **Discord bot token**, paste the token and press Enter. The input is hidden, so you will not see characters appear while pasting.

Use the token from the **Bot** page, not the Application ID, Public Key, or OAuth2 Client Secret. Treat it like a password: do not paste it into chat, commit it to source control, or put it in `config.json`. The commands below pass it through the `DISCORD_BOT_TOKEN` environment variable for the current terminal session.

```bash
git clone https://github.com/swhite8/staff-sentinel.git
cd staff-sentinel
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp config.example.json config.json
# Edit config.json with real IDs and names before continuing.
.venv/bin/python bot.py --check-config
read -rsp 'Discord bot token: ' DISCORD_BOT_TOKEN
export DISCORD_BOT_TOKEN
.venv/bin/python bot.py
```

Keep the token in your host’s secret manager for unattended operation. Do not use a personal account token. Run one process per configuration; the in-process delivery lock and SQLite cooldown store are intended for one instance. Use a persistent working directory so rate limits survive restarts. Restart after configuration changes. Use a process supervisor with restart-on-failure for continuous hosting.

## Commands and behavior

- `/report-impersonation suspect_id details [message_url] [screenshot]`: reports any numeric account ID, including accounts that left. Confirmation is ephemeral; the report goes only to the configured moderator channel. One accepted report per user per minute by default. Images up to 8 MiB are copied into that channel. Reports are allegations, not verified findings. DM message links usually cannot be opened by moderators; supply screenshots or text as evidence.
- `/staff-safety`: private guidance on checking staff identity, reporting, blocking, and protecting login information.
- New joins and observed username, global display name, server nickname, or custom avatar changes trigger checks. Names are compared against configured names and cached employee names. The real employee IDs are exempt; other bot accounts are still checked. Unicode width, case, punctuation, and spacing are normalized, followed by exact and conservative fuzzy matching. This is not full Unicode confusable matching. Short names use exact matching to reduce noise.
- Avatar matching compares both global and server-specific custom avatars against both custom avatars of cached employee members. Shared Discord default avatars are excluded. It detects identical normalized pictures, horizontal mirrors, and modest resizing, compression, or brightness changes using a 256-bit gradient hash plus normalized RGB pixel error. An avatar match alone can trigger an alert. Alerts show the suspected picture as a thumbnail and the closest staff picture as the large image, with staff ID, avatar context, mirror status, and difference measurements. These are visual measurements, not confidence probabilities. Common logos can cause false positives; heavy crops, overlays, rotations, and extensive edits may be missed. This is image comparison, not facial recognition. Animated avatars use a static first-frame rendition.
- Alerts contain account ID, profile link, account creation time, server join time when available, and matching names or avatars. Repeat name and avatar alerts each have an independent six-hour cooldown by default, so a prior name alert does not suppress the first avatar alert. Staff compare evidence and decide whether to act.
- Welcome DMs explain identity verification and reporting. Closed DMs are handled without interrupting detection. Set `welcome_dm` to `false` if you prefer to publish the guidance in onboarding yourself.

No historical member sweep runs at startup. Events missed while offline are not replayed. A successful connection log does not guarantee channel permissions: resolve any configuration error in the logs before relying on alerts. Guild commands are synced at startup; ordinary members need permission to use application commands where they invoke reporting.

Avatar matching is enabled by default, including for existing configurations. Set `avatar_matching` to `false` to disable it. `avatar_max_hash_distance` defaults to `20` out of 256 bits and `avatar_max_pixel_error` to `0.12` (0 means identical pixels). Both limits must pass for a similar-image match; lower values make matching stricter. Tune with your staff images and unrelated examples. Staff must be present and cached in the configured server for avatar references to be available. Current staff avatars are used automatically; a staff avatar change does not trigger a sweep of other members. Failed avatar downloads are logged and skipped without blocking name-based detection.

## Verification

```bash
.venv/bin/python -m unittest discover -s tests -v
```

Before production use, test in a private server with a non-employee test account: join using a protected name, change its nickname to a similar name, submit a report with screenshot, and verify that a normal member cannot see the moderator channel or report. Test with welcome DMs disabled by the recipient and confirm the bot continues. Repeated name alerts are deliberately suppressed by the cooldown; temporarily lower it for testing.

The bot needs your token and server configuration to perform live verification; offline tests do not exercise Discord delivery.

For avatar testing, use an unrelated test username with a copied staff image, a horizontally mirrored copy, and a recompressed copy. Confirm an avatar-only alert with the correct staff reference, then try an unrelated image and a Discord default avatar. Allow for the separate avatar cooldown between positive tests. Reinstall requirements when upgrading this version to add Pillow.

## Evidence and privacy

SQLite stores only cooldown keys containing user IDs and send timestamps, not report content. Expired keys are pruned on successful delivery after 30 days. Reports and copied images remain as Discord messages until moderators delete them; establish a retention policy and keep channel access restricted. Report details and tokens are not intentionally logged. User text cannot trigger mentions through this bot. The bot does not fetch user-supplied URLs or follow suspicious links; it downloads interaction attachments and Discord avatar assets. Avatar fingerprints are kept in a bounded 512-entry memory cache, not on disk or in an external image service. Avatar comparison previews reference Discord CDN images and may become unavailable after avatar changes; they are not archived evidence. Submitted image content types are checked, but evidence remains untrusted content.

API references: [discord.py API](https://discordpy.readthedocs.io/en/stable/api.html), [interaction commands](https://discordpy.readthedocs.io/en/stable/interactions/api.html), [Discord privileged intents](https://support-dev.discord.com/hc/en-us/articles/6207308062871-What-are-Privileged-Intents).
