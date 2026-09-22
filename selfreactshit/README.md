# SelfReactMute

Mutes a member when they add a reaction to their own message.

## Setup

1. Install/load the cog from the repository.
2. Create or choose a muted role.
3. Configure the cog:

```text
[p]selfreact role @Muted
[p]selfreact channel #moderation
[p]selfreact ignore add @Trusted
[p]selfreact sob add @Member
[p]selfreact duration 10m
[p]selfreact title Self-reaction mute
[p]selfreact message {user_mention} was muted for reacting to their own message.
[p]selfreact enable
```

Check the current settings with:

```text
[p]selfreact show
```

Use `[p]selfreact channel` without a channel argument to listen in all channels again.

Ignored-role commands:

```text
[p]selfreact ignore add @Trusted
[p]selfreact ignore remove @Trusted
[p]selfreact ignore list
[p]selfreact ignore clear
```

Members with any ignored role are not muted for self-reacting. Disable the entire feature with `[p]selfreact enable false`.

The separate `shut up` rule watches one configured user anywhere in the server. When that user replies to another member with `shut up`, the member being replied to is muted with the configured role:

```text
[p]selfreact shutup user @Member
[p]selfreact shutup duration 120
[p]selfreact shutup reason for violating the server rules.
[p]selfreact shutup enable true
[p]selfreact shutup show
[p]selfreact shutup clear
```

The duration is configured in seconds and uses the same configured mute role. It is independent of the self-reaction mute toggle.

To silently clear Unicode `:sob:` reactions from a member's messages:

```text
[p]selfreact sob add @Member
[p]selfreact sob remove @Member
[p]selfreact sob list
[p]selfreact sob clear
```

This cleanup works in every channel across the server, even when self-reaction muting is disabled. It clears the entire `:sob:` reaction from the message, so all users' `:sob:` reactions are removed. The configured listen channel only affects self-reaction muting.

The bot needs **Manage Roles**, and its highest role must be above the configured muted role. The configured role must also deny speaking/sending permissions in the server's channel permissions.

## Message placeholders

- `{user}`: username and discriminator/display tag
- `{user_mention}`: mention of the muted member
- `{duration}`: configured duration, such as `10m`
- `{channel}`: channel mention

Durations can be written as `30s`, `10m`, `2h`, or `1d`, up to 30 days. Active mute expirations are stored and continue after a bot restart.
