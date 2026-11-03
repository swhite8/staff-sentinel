import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord

from bot import Guard


class BotTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.bot = Guard({
            "guild_id": 123456789012345678,
            "moderator_channel_id": 234567890123456789,
            "employee_ids": {345678901234567890},
            "protected_names": ["Example Employee"],
            "similarity_threshold": 0.88,
            "welcome_dm": True,
            "report_cooldown_seconds": 60,
            "alert_cooldown_seconds": 3600,
            "database_path": str(Path(self.directory.name) / "state.sqlite3"),
        })
        self.bot._connection.user = SimpleNamespace(id=456789012345678901)
        self.channel = SimpleNamespace(send=AsyncMock())
        self.bot.mod_channel = Mock(return_value=self.channel)

    async def asyncTearDown(self):
        self.bot.cooldowns.db.close()
        await self.bot.close()
        self.directory.cleanup()

    def interaction(self):
        return SimpleNamespace(
            guild_id=self.bot.config["guild_id"],
            guild=SimpleNamespace(get_member=Mock(return_value=None)),
            user=SimpleNamespace(id=567890123456789012),
            response=SimpleNamespace(defer=AsyncMock(), send_message=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock()),
        )

    async def test_commands_registered(self):
        commands = self.bot.tree.get_commands(guild=self.bot.scope)
        self.assertEqual({c.name for c in commands}, {"staff-safety", "report-impersonation"})
        report = next(c for c in commands if c.name == "report-impersonation")
        self.assertEqual(len(report.parameters), 4)

    async def test_public_moderator_channel_rejected(self):
        guild = SimpleNamespace(id=self.bot.config["guild_id"], default_role=object())
        channel = Mock(spec=discord.TextChannel)
        channel.guild = guild
        channel.permissions_for.return_value = SimpleNamespace(view_channel=True)
        self.bot.get_guild = Mock(return_value=guild)
        self.bot.get_channel = Mock(return_value=channel)
        with self.assertRaisesRegex(RuntimeError, "visible to @everyone"):
            Guard.mod_channel(self.bot)

    async def test_ordinary_role_access_rejected(self):
        guild = SimpleNamespace(id=self.bot.config["guild_id"], default_role=object())
        role = Mock(spec=discord.Role)
        role.tags = None
        channel = Mock(spec=discord.TextChannel)
        channel.guild = guild
        channel.overwrites = {role: object()}
        channel.permissions_for.side_effect = [
            SimpleNamespace(view_channel=False),
            SimpleNamespace(view_channel=True, manage_messages=False, manage_guild=False, administrator=False),
        ]
        self.bot.get_guild = Mock(return_value=guild)
        self.bot.get_channel = Mock(return_value=channel)
        with self.assertRaisesRegex(RuntimeError, "non-moderator"):
            Guard.mod_channel(self.bot)

    async def test_report_copies_evidence_and_limits_repeats(self):
        interaction = self.interaction()
        image = SimpleNamespace(size=4, content_type="image/png", read=AsyncMock(return_value=b"test"))
        await self.bot.report(interaction, "678901234567890123", "Suspicious payment request", screenshot=image)
        self.channel.send.assert_awaited_once()
        call = self.channel.send.call_args.kwargs
        self.assertEqual(len(call["files"]), 1)
        self.assertIn("678901234567890123", str(call["embed"].to_dict()))
        self.assertTrue(interaction.response.defer.call_args.kwargs["ephemeral"])
        self.assertTrue(interaction.followup.send.call_args.kwargs["ephemeral"])
        await self.bot.report(interaction, "678901234567890123", "Second report should wait")
        self.channel.send.assert_awaited_once()

    async def test_failed_delivery_does_not_consume_cooldown(self):
        interaction = self.interaction()
        self.channel.send.side_effect = RuntimeError("offline")
        with self.assertRaises(RuntimeError):
            await self.bot.report(interaction, "678901234567890123", "Suspicious payment request")
        self.assertEqual(self.bot.cooldowns.remaining(f"report:{interaction.user.id}", 60), 0)

    async def test_bad_attachment_rejected_before_download(self):
        interaction = self.interaction()
        image = SimpleNamespace(size=1, content_type="text/html", read=AsyncMock())
        await self.bot.report(interaction, "678901234567890123", "Suspicious payment request", screenshot=image)
        image.read.assert_not_awaited()
        self.channel.send.assert_not_awaited()

    async def test_allowlist_and_duplicate_alerts(self):
        guild = SimpleNamespace(id=self.bot.config["guild_id"], get_member=Mock(return_value=None))
        member = SimpleNamespace(id=678901234567890123, guild=guild, name="Example Employee", global_name=None, nick=None, display_name="Example Employee", joined_at=None)
        await self.bot.check_member(member, "test")
        await self.bot.check_member(member, "test again")
        self.channel.send.assert_awaited_once()
        member.id = next(iter(self.bot.config["employee_ids"]))
        await self.bot.check_member(member, "real employee")
        self.channel.send.assert_awaited_once()

    async def test_avatar_only_alert_and_independent_cooldown(self):
        guild = SimpleNamespace(id=self.bot.config["guild_id"], get_member=Mock(return_value=None))
        member = SimpleNamespace(id=678901234567890123, guild=guild, name="Unrelated Name", global_name=None, nick=None, display_name="Unrelated Name", joined_at=None)
        self.bot.cooldowns.mark(f"alert:{member.id}")
        self.bot.avatar_matches = AsyncMock(return_value=[dict(kind="similar image (mirrored)", distance=4, error=0.02, employee_id=345678901234567890, candidate_context="global", staff_context="server", candidate_url="https://cdn.discordapp.com/test.png", staff_url="https://cdn.discordapp.com/staff.png")])
        await self.bot.check_member(member, "Avatar changed")
        await self.bot.check_member(member, "Repeat")
        self.channel.send.assert_awaited_once()
        payload = self.channel.send.call_args.kwargs["embed"].to_dict()
        self.assertIn("mirrored", str(payload))
        self.assertEqual(payload["thumbnail"]["url"], "https://cdn.discordapp.com/test.png")

    async def test_avatar_change_triggers_check(self):
        self.bot.check_member = AsyncMock()
        before = SimpleNamespace(name="Person", global_name=None, avatar="old")
        after = SimpleNamespace(name="Person", global_name=None, avatar="new")
        await self.bot.on_member_update(before, after)
        self.bot.check_member.assert_awaited_once()

    async def test_default_avatars_skipped(self):
        member = SimpleNamespace(id=678901234567890123, guild=SimpleNamespace(get_member=Mock()), avatar=None, guild_avatar=None)
        self.bot.avatar_fingerprint = AsyncMock()
        self.assertEqual(await self.bot.avatar_matches(member), [])
        self.bot.avatar_fingerprint.assert_not_awaited()

    async def test_global_to_staff_server_avatar_and_cache(self):
        import io
        from PIL import Image
        data = io.BytesIO()
        Image.new("RGB", (32, 32), "orange").save(data, format="PNG")
        def asset(url):
            result = Mock(url=url, read=AsyncMock(return_value=data.getvalue()))
            result.replace.return_value = result
            return result
        candidate = asset("https://cdn.discordapp.com/candidate.png")
        staff = asset("https://cdn.discordapp.com/staff.png")
        employee = SimpleNamespace(avatar=None, guild_avatar=staff)
        member = SimpleNamespace(id=678901234567890123, avatar=candidate, guild_avatar=None, guild=SimpleNamespace(get_member=Mock(return_value=employee)))
        matches = await self.bot.avatar_matches(member)
        self.assertEqual(matches[0]["candidate_context"], "global")
        self.assertEqual(matches[0]["staff_context"], "server")
        await self.bot.avatar_matches(member)
        candidate.read.assert_awaited_once()
        staff.read.assert_awaited_once()

    async def test_failed_avatar_download_does_not_block_name_alert(self):
        guild = SimpleNamespace(id=self.bot.config["guild_id"], get_member=Mock(return_value=None))
        member = SimpleNamespace(id=678901234567890123, guild=guild, name="Example Employee", global_name=None, nick=None, display_name="Example Employee", joined_at=None, avatar=object())
        self.bot.avatar_fingerprint = AsyncMock(side_effect=TimeoutError)
        await self.bot.check_member(member, "test")
        self.channel.send.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
