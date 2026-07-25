from typing import List, Optional, Union
from dataclasses import dataclass

from .ids import MessageId, GuildId, ChannelId, UserId, RoleId, EmojiId, AttachmentId

@dataclass
class MessageInfo:
    guild_id: Optional[GuildId]
    channel_id: ChannelId
    message_id: MessageId
    nonce: Optional[MessageId]
    author_id: UserId
    author: str
    author_avatar_url: Optional[str]
    author_is_bot: bool
    author_role_ids: List[RoleId]
    message_kind: MessageKind
    interaction: Optional[MessageInteractionInfo]
    reference: Optional[MessageReferenceInfo]
    reply: Optional[ReplyInfo]
    poll: Optional[PollInfo]
    pinned: bool
    reactions: List[ReactionInfo]
    content: str | None
    sticker_names: List[str]
    mentions: List[MentionInfo]
    mention_everyone: bool
    mention_roles: List[RoleId]
    flags: int # u64
    attachments: List[AttachmentInfo]
    embeds: List[EmbedInfo]
    forwarded_snapshots: List[MessageSnapshotInfo]
    edited_timestamp: Optional[str]

@dataclass
class ReplyReference:
    message_id: MessageId
    mention_author: bool

@dataclass
class MessageKind:
    code: int # u8

@dataclass
class MessageInteractionInfo:
    user_id: Optional[UserId]
    user: str
    command_name: Optional[str]

@dataclass
class MessageReferenceInfo:
    guild_id: GuildId
    channel_id: ChannelId
    message_id: MessageId

@dataclass
class ReplyInfo:
    author_id: Optional[UserId]
    author: str
    content: Optional[str]
    sticker_names: List[str]
    mentions: List[MentionInfo]

@dataclass
class MentionInfo:
    user_id: UserId
    guild_nick: Optional[str]
    display_name: str

@dataclass
class PollInfo:
    question: str
    answers: List[PollAnswerInfo]
    allow_multiselect: bool
    results_finalized: Optional[bool]
    total_votes: Optional[int] # u64

@dataclass
class PollAnswerInfo:
    answer_id: int # u8
    text: str
    vote_count: Optional[int] # u64
    me_voted: bool

@dataclass
class ReactionInfo:
    emoji: ReactionEmoji
    count: int # u64
    me: bool

@dataclass(frozen=True)
class UnicodeReactionEmoji:
    value: str

@dataclass(frozen=True)
class CustomReactionEmoji:
    id: EmojiId
    name: Optional[str]
    animated: bool

ReactionEmoji = Union[UnicodeReactionEmoji,  CustomReactionEmoji]

@dataclass
class AttachmentInfo:
    id: AttachmentId
    filename: str
    url: str
    proxy_url: str
    content_type: Optional[str]
    size: int # u64
    width: Optional[int] # u64
    height: Optional[int] # u64
    description: Optional[str]

@dataclass
class EmbedInfo:
    color: Optional[int] # u32
    provider_name: Optional[str]
    author_name: Optional[str]
    title: Optional[str]
    description: Optional[str]
    timestamp: Optional[str]
    fields: List[EmbedFieldInfo]
    footer_text: Optional[str]
    url: Optional[str]
    thumbnail_url: Optional[str]
    thumbnail_proxy_url: Optional[str]
    thumbnail_width: Optional[int] # u64
    thumbnail_height: Optional[int]
    image_url: Optional[str]
    image_proxy_url: Optional[str]
    image_width: Optional[int] # u64
    image_height: Optional[int] # u64
    video_url: Optional[str]

@dataclass
class EmbedFieldInfo:
    name: str
    value: str

@dataclass
class MessageSnapshotInfo:
    content: Optional[str]
    sticker_names: List[str]
    mentions: List[MentionInfo]
    attachments: List[AttachmentInfo]
    embeds: List[EmbedInfo]
    source_channel_id: Optional[ChannelId]
    timestamp: Optional[str]
