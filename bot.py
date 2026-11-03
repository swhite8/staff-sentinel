"""Run with DISCORD_BOT_TOKEN set; no normal user tokens are used."""
import argparse
import asyncio
import io
import logging
import os
import uuid
from collections import OrderedDict

import discord
from discord import app_commands

from guard_core import Cooldowns, load_config, match_names, message_link, snowflake
from avatar_match import fingerprint, compare

log = logging.getLogger("guard")


def safe(value, limit=1000):
    return discord.utils.escape_markdown(discord.utils.escape_mentions(str(value)))[:limit]


class Guard(discord.Client):
    def __init__(self, config):
        intents = discord.Intents.none()
        intents.guilds = True
        intents.members = True
        super().__init__(intents=intents, allowed_mentions=discord.AllowedMentions.none())
        self.config = config
        self.tree = app_commands.CommandTree(self)
        self.cooldowns = Cooldowns(config["database_path"])
        self.delivery_lock = asyncio.Lock()
        self.avatar_lock = asyncio.Lock()
        self.avatar_cache = OrderedDict()
        self.scope = discord.Object(id=config["guild_id"])
        self.tree.add_command(app_commands.Command(name="report-impersonation", description="Privately report a suspected staff impersonator", callback=self.report), guild=self.scope)
        self.tree.add_command(app_commands.Command(name="staff-safety", description="Learn how to verify staff and report suspicious DMs", callback=self.safety), guild=self.scope)
        self.tree.error(self.command_error)

    async def setup_hook(self):
        await self.tree.sync(guild=self.scope)

    def guidance(self):
        return (
            "**Protect yourself from staff impersonation**\n"
            "Display names and avatars can be copied. Verify the account ID against your server’s official staff directory, "
            "and contact staff through an established server channel before trusting an unsolicited DM. "
            "Never share passwords, login codes, tokens, or payment details.\n"
            "Use `/report-impersonation` in the server to privately send moderators the sender’s numeric account ID, "
            "a message link, and an optional screenshot. Enable Discord Developer Mode to copy account IDs. "
            "The bot cannot read your private conversations. Reports are visible to moderators; screenshots may contain personal information. "
            "You can also block the sender and report the message directly to Discord."
        )

    def mod_channel(self):
        guild = self.get_guild(self.config["guild_id"])
        channel = self.get_channel(self.config["moderator_channel_id"])
        if not guild or not isinstance(channel, discord.TextChannel) or channel.guild.id != guild.id:
            raise RuntimeError("Moderator channel must be a text channel in the configured server")
        if channel.permissions_for(guild.default_role).view_channel:
            raise RuntimeError("Moderator channel is visible to @everyone; refusing to send private reports")
        # Reject access granted to ordinary roles or individual non-moderators.
        for target in channel.overwrites:
            permissions = channel.permissions_for(target)
            is_bot = (
                isinstance(target, discord.Member) and target.id == self.user.id
            ) or (
                isinstance(target, discord.Role) and target.tags is not None
                and target.tags.bot_id == self.user.id
            )
            if permissions.view_channel and not is_bot and not (permissions.manage_messages or permissions.manage_guild or permissions.administrator):
                raise RuntimeError("Moderator channel grants access to a non-moderator")
        permissions = channel.permissions_for(guild.me)
        if not all((permissions.view_channel, permissions.send_messages, permissions.embed_links, permissions.attach_files)):
            raise RuntimeError("Bot needs View Channel, Send Messages, Embed Links, and Attach Files in moderator channel")
        return channel

    async def on_ready(self):
        try:
            self.mod_channel()
        except RuntimeError as exc:
            log.error("Configuration error: %s", exc)
        log.info("Connected as %s; configured server %s", self.user.id, self.config["guild_id"])

    def protected(self, guild):
        names = list(self.config["protected_names"])
        for employee_id in self.config["employee_ids"]:
            member = guild.get_member(employee_id)
            if member:
                names.extend(self.names(member))
        return names

    @staticmethod
    def names(user):
        return [x for x in (user.name, user.global_name, getattr(user, "nick", None)) if x]

    @staticmethod
    def avatars(user):
        # Skip Discord's shared default avatars; compare both custom avatar contexts.
        return [(label, asset) for label, asset in (
            ("global", getattr(user, "avatar", None)),
            ("server", getattr(user, "guild_avatar", None)),
        ) if asset is not None]

    async def avatar_fingerprint(self, asset):
        asset = asset.replace(size=128, format="png", static_format="png")
        key = str(asset.url)
        async with self.avatar_lock:
            if key in self.avatar_cache:
                self.avatar_cache.move_to_end(key)
                return self.avatar_cache[key]
            data = await asyncio.wait_for(asset.read(), timeout=10)
            result = await asyncio.to_thread(fingerprint, data)
            self.avatar_cache[key] = result
            if len(self.avatar_cache) > 512:
                self.avatar_cache.popitem(last=False)
            return result

    async def avatar_matches(self, member):
        if not self.config.get("avatar_matching", True):
            return []
        matches = []
        for candidate_context, asset in self.avatars(member):
            try:
                candidate = await self.avatar_fingerprint(asset)
            except (discord.HTTPException, TimeoutError, OSError, ValueError):
                log.warning("Could not fingerprint avatar for member %s", member.id)
                continue
            for employee_id in sorted(self.config["employee_ids"]):
                employee = member.guild.get_member(employee_id)
                if employee is None:
                    continue
                for staff_context, staff_asset in self.avatars(employee):
                    try:
                        staff = await self.avatar_fingerprint(staff_asset)
                    except (discord.HTTPException, TimeoutError, OSError, ValueError):
                        log.warning("Could not fingerprint staff avatar for %s", employee_id)
                        continue
                    result = compare(candidate, staff, self.config.get("avatar_max_hash_distance", 20), self.config.get("avatar_max_pixel_error", 0.12))
                    if result:
                        matches.append(dict(result, employee_id=employee_id, candidate_context=candidate_context, staff_context=staff_context, candidate_url=str(asset.url), staff_url=str(staff_asset.url)))
        return sorted(matches, key=lambda m: (m["distance"], m["error"]))[:5]

    async def check_member(self, member, reason):
        if member.guild.id != self.config["guild_id"] or member.id in self.config["employee_ids"] or member.id == self.user.id:
            return
        matches = match_names(self.names(member), self.protected(member.guild), self.config["similarity_threshold"])
        avatars = await self.avatar_matches(member)
        if not matches and not avatars:
            return
        key = f"alert:{member.id}"
        avatar_key = f"avatar-alert:{member.id}"
        async with self.delivery_lock:
            name_pending = matches and not self.cooldowns.remaining(key, self.config["alert_cooldown_seconds"])
            avatar_pending = avatars and not self.cooldowns.remaining(avatar_key, self.config["alert_cooldown_seconds"])
            if not name_pending and not avatar_pending:
                return
            embed = self.account_embed(member.id, member, "Possible employee impersonation")
            embed.description = "Name and avatar similarity are review signals, not proof of abuse. No automatic moderation was performed."
            embed.add_field(name="Trigger", value=reason)
            if matches:
                embed.add_field(name="Name matches", value="\n".join(f"{safe(a, 80)} → {safe(b, 80)} ({score:.0%})" for a, b, score in matches), inline=False)
            if avatars:
                embed.add_field(name="Avatar matches", value="\n".join(
                    f"Staff {m['employee_id']}: {m['kind']}; {m['candidate_context']} → {m['staff_context']}; hash distance {m['distance']}/256, pixel error {m['error']:.3f}" for m in avatars
                )[:1024], inline=False)
                embed.set_thumbnail(url=avatars[0]["candidate_url"])
                embed.set_image(url=avatars[0]["staff_url"])
                embed.add_field(name="Avatar comparison", value="Thumbnail: suspected account. Large image: closest matching staff avatar. Metrics are visual differences, not confidence probabilities.", inline=False)
            await self.mod_channel().send(embed=embed)
            if name_pending:
                self.cooldowns.mark(key)
            if avatar_pending:
                self.cooldowns.mark(avatar_key)

    @staticmethod
    def account_embed(account_id, user, title):
        embed = discord.Embed(title=title, colour=discord.Colour.orange(), timestamp=discord.utils.utcnow())
        embed.add_field(name="Account ID", value=str(account_id))
        embed.add_field(name="Profile", value=f"https://discord.com/users/{account_id}")
        created = discord.utils.snowflake_time(account_id)
        embed.add_field(name="Account created", value=discord.utils.format_dt(created, "R"))
        if user:
            embed.add_field(name="Username / display name", value=safe(f"{user.name} / {user.display_name}"), inline=False)
            joined = getattr(user, "joined_at", None)
            embed.add_field(name="Server joined", value=discord.utils.format_dt(joined, "R") if joined else "Not available")
        return embed

    async def on_member_join(self, member):
        if member.guild.id != self.config["guild_id"]:
            return
        try:
            await self.check_member(member, "Member joined")
        except (discord.HTTPException, RuntimeError):
            log.exception("Could not deliver impersonation alert")
        if self.config["welcome_dm"] and not member.bot:
            try:
                await member.send(f"Welcome to {safe(member.guild.name)}!\n\n{self.guidance()}")
            except discord.HTTPException:
                log.info("Welcome DM unavailable for member %s", member.id)

    async def on_member_update(self, before, after):
        if self.names(before) != self.names(after) or self.avatars(before) != self.avatars(after):
            await self.check_member(after, "Server profile changed")

    async def on_user_update(self, before, after):
        if self.names(before) != self.names(after) or self.avatars(before) != self.avatars(after):
            guild = self.get_guild(self.config["guild_id"])
            member = guild.get_member(after.id) if guild else None
            if member:
                await self.check_member(member, "Account profile changed")

    async def safety(self, interaction: discord.Interaction):
        await interaction.response.send_message(self.guidance(), ephemeral=True)

    @app_commands.describe(suspect_id="Sender’s numeric account ID (works even if they left the server)", details="What happened? Do not include passwords or login codes", message_url="Optional Discord message link; moderators may not be able to open a private DM", screenshot="Optional image evidence, up to 8 MiB")
    async def report(self, interaction: discord.Interaction, suspect_id: str, details: app_commands.Range[str, 10, 1500], message_url: str | None = None, screenshot: discord.Attachment | None = None):
        if interaction.guild_id != self.config["guild_id"]:
            await interaction.response.send_message("Use this command in the configured server.", ephemeral=True)
            return
        try:
            account_id = snowflake(suspect_id)
        except ValueError:
            await interaction.response.send_message("Copy the sender’s numeric account ID using Discord Developer Mode.", ephemeral=True)
            return
        if message_url and not message_link(message_url):
            await interaction.response.send_message("Supply a Discord message link, or leave that field empty.", ephemeral=True)
            return
        if screenshot and (screenshot.size > 8 * 1024 * 1024 or screenshot.content_type not in {"image/png", "image/jpeg", "image/webp", "image/gif"}):
            await interaction.response.send_message("Screenshot must be PNG, JPEG, WebP, or GIF, at most 8 MiB.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        async with self.delivery_lock:
            key = f"report:{interaction.user.id}"
            remaining = self.cooldowns.remaining(key, self.config["report_cooldown_seconds"])
            if remaining:
                await interaction.followup.send(f"Please wait {int(remaining) + 1} seconds before another report.", ephemeral=True)
                return
            channel = self.mod_channel()
            reference = uuid.uuid4().hex[:12]
            member = interaction.guild.get_member(account_id)
            embed = self.account_embed(account_id, member, f"Impersonation report · {reference}")
            embed.description = "User-submitted evidence; allegations have not been verified."
            embed.add_field(name="Reporter", value=f"{safe(interaction.user)} ({interaction.user.id})", inline=False)
            embed.add_field(name="Details", value=safe(details[:1000]), inline=False)
            if len(details) > 1000:
                embed.add_field(name="Details continued", value=safe(details[1000:]), inline=False)
            if message_url:
                embed.add_field(name="Submitted message link", value=message_url + "\nPrivate DM links may be inaccessible to moderators.", inline=False)
            files = []
            if screenshot:
                data = await screenshot.read()
                if len(data) > 8 * 1024 * 1024:
                    raise RuntimeError("Screenshot exceeds the upload limit")
                suffix = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp", "image/gif": "gif"}[screenshot.content_type]
                files.append(discord.File(io.BytesIO(data), filename=f"evidence-{reference}.{suffix}"))
            try:
                await channel.send(embed=embed, files=files)
            finally:
                for file in files:
                    file.close()
            self.cooldowns.mark(key)
        await interaction.followup.send(f"Report {reference} was delivered privately to the moderators. You can also block the sender and report the message to Discord.", ephemeral=True)

    async def command_error(self, interaction, error):
        log.error("Command failed (%s)", type(getattr(error, "original", error)).__name__)
        text = "Your request could not be completed. A report may not have been delivered; please contact a moderator directly."
        if interaction.response.is_done():
            await interaction.followup.send(text, ephemeral=True)
        else:
            await interaction.response.send_message(text, ephemeral=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config.json")
    parser.add_argument("--check-config", action="store_true")
    args = parser.parse_args()
    config = load_config(args.config)
    if args.check_config:
        print("Configuration is valid (Discord channel permissions not checked offline).")
        return
    token = os.environ.get("DISCORD_BOT_TOKEN")
    if not token:
        parser.error("Set DISCORD_BOT_TOKEN to your application’s bot token")
    Guard(config).run(token)


if __name__ == "__main__":
    main()
