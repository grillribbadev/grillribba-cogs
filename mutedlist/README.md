# MuteList
Configure which roles count as a "mute" and list all members holding those roles with a reason.
Reasons are taken from your stored records; if missing and allowed, the cog reads the **Audit Log** entry that added the role.

Commands:
- `[p]mutelist roles` — show configured roles
- `[p]mutelist addrole @Role` / `delrole @Role` / `clearroles`
- `[p]mutelist auditscan <true|false>`
- `[p]mutedlist` or `[p]mutelist list` — show members + reasons
- `[p]mutelist setreason @user <text>` — set/override stored reason

Public API (to call from your mute/unmute code):
```py
mutelist = bot.get_cog("MuteList")
await mutelist.record_mute(guild, member, reason="...", moderator=ctx.author, until=None)
await mutelist.clear_mute(guild, member.id)
