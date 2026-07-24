from typing import Protocol, TypeVar, Generic

# Types adapted from Concord <https://github.com/chojs23/concord>
# Licenced under the GPL-3.0 License (https://github.com/chojs23/concord?tab=GPL-3.0-1-ov-file)
# With source available at https://github.com/chojs23/concord/blob/main/src/discord/ids.rs
# Which itself adapted from Twilight <https://github.com/twilight-rs/twiligh>
# ISC License (ISC)
# Copyright (c) 2025 Twilight Contributors
#
# Permission to use, copy, modify, and/or distribute this software for any
# purpose with or without fee is hereby granted, provided that the above
# copyright notice and this permission notice appear in all copies

# PS: I don't really know if putting all this here is strictly necessary
# since it's technically very far from the original source, maintaining more
# of the core logic/handling than teh actual code, but we never know...

# 1. The Protocol (The "Trait")
# This dictates that any marker class MUST define a mention prefix.
class MentionMarker(Protocol):
    mention_prefix: str

# 2. The "Empty" Marker Classes
# They hold no instance data, just the metadata required by the Protocol.
# TODO: should add missing markers for Message, Guild, ForumTag, Emoji, Attachment and Application (https://github.com/chojs23/concord/blob/main/src/discord/ids.rs)
class UserMarker:
    mention_prefix = "@"

class ChannelMarker:
    mention_prefix = "#"

class RoleMarker:
    mention_prefix = "@&"

# 3. The TypeVar bound to our Protocol
M = TypeVar('M', bound=MentionMarker)

# 4. The Universal Id Wrapper
class Id(Generic[M]):
    # TODO: snowflake must be non-zero unsigned integer, should add checks for this
    def __init__(self, snowflake: int, marker: type[M]):
        self.snowflake = snowflake
        self.marker = marker

    def to_mention(self) -> str:
        # Dynamically formats the mention string using the marker's rules
        return f"<{self.marker.mention_prefix}{self.snowflake}>"

# 5. Creating strict type aliases for the rest of the codebase
UserId = Id[UserMarker]
ChannelId = Id[ChannelMarker]
RoleId = Id[RoleMarker]

# --- Usage Examples ---

# my_user_id: UserId = Id(123456789, UserMarker)
# my_channel_id: ChannelId = Id(987654321, ChannelMarker)

# print(my_user_id.to_mention())     # Output: <@123456789>
# print(my_channel_id.to_mention())  # Output: <#987654321>
