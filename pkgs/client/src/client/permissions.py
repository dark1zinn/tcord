"""Discord permission computation for the signed-in member.

Follows Discord's documented order: owner and ADMINISTRATOR short-circuit; base permissions
are @everyone plus every member role; channel overwrites apply @everyone, then all role
overwrites combined, then the member overwrite. Threads use their parent's permissions.
"""

from __future__ import annotations

from collections.abc import Iterable

from .models import Channel, Guild

ADMINISTRATOR = 1 << 3
VIEW_CHANNEL = 1 << 10
SEND_MESSAGES = 1 << 11
READ_MESSAGE_HISTORY = 1 << 16
CONNECT = 1 << 20
SEND_MESSAGES_IN_THREADS = 1 << 38
ALL = (1 << 64) - 1

ROLE_OVERWRITE = 0
MEMBER_OVERWRITE = 1


def base_permissions(guild: Guild, user_id: str, roles: Iterable[str]) -> int:
    if guild.owner_id == user_id:
        return ALL
    by_id = {role.id: role for role in guild.roles}
    everyone = by_id.get(guild.id)
    permissions = everyone.permissions if everyone is not None else 0
    for role_id in roles:
        role = by_id.get(role_id)
        if role is not None:
            permissions |= role.permissions
    return ALL if permissions & ADMINISTRATOR else permissions


def channel_permissions(guild: Guild, channel: Channel, user_id: str, roles: Iterable[str]) -> int:
    role_ids = set(roles)
    permissions = base_permissions(guild, user_id, role_ids)
    if permissions == ALL:
        return ALL
    overwrites = {(item.kind, item.id): item for item in channel.permission_overwrites}
    everyone = overwrites.get((ROLE_OVERWRITE, guild.id))
    if everyone is not None:
        permissions = (permissions & ~everyone.deny) | everyone.allow
    allow = deny = 0
    for item in channel.permission_overwrites:
        if item.kind == ROLE_OVERWRITE and item.id in role_ids:
            allow |= item.allow
            deny |= item.deny
    permissions = (permissions & ~deny) | allow
    member = overwrites.get((MEMBER_OVERWRITE, user_id))
    if member is not None:
        permissions = (permissions & ~member.deny) | member.allow
    if not permissions & VIEW_CHANNEL:
        return 0  # an unviewable channel grants nothing else
    return permissions
