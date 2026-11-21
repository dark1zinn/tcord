//! Text-chat Discord objects, parsed leniently from REST and gateway JSON.

use serde_json::Value;

use crate::error::{EngineError, Result};
use crate::models::User;

pub type Timestamp = jiff::Timestamp;

#[derive(Clone, Debug, PartialEq)]
pub struct Role {
    pub id: String,
    pub name: String,
    pub color: u32,
    pub position: i64,
    pub permissions: u64,
}

/// A channel permission overwrite. `kind` 0 = role, 1 = member.
#[derive(Clone, Debug, PartialEq)]
pub struct PermissionOverwrite {
    pub id: String,
    pub kind: u8,
    pub allow: u64,
    pub deny: u64,
}

#[derive(Clone, Debug, PartialEq)]
pub struct ThreadMetadata {
    pub archived: bool,
    pub locked: bool,
    pub archive_timestamp: Option<Timestamp>,
}

#[derive(Clone, Debug, PartialEq)]
pub struct Channel {
    pub id: String,
    pub kind: u8,
    pub name: Option<String>,
    pub guild_id: Option<String>,
    pub position: i64,
    pub parent_id: Option<String>,
    pub last_message_id: Option<String>,
    pub topic: Option<String>,
    pub recipients: Vec<User>,
    pub permission_overwrites: Vec<PermissionOverwrite>,
    pub owner_id: Option<String>,
    pub thread_metadata: Option<ThreadMetadata>,
    pub message_count: Option<u32>,
    /// Whether the signed-in user is a member of this thread.
    pub joined: bool,
}

/// A guild member (only the fields chat rendering and permissions need).
#[derive(Clone, Debug, PartialEq)]
pub struct Member {
    pub user_id: String,
    pub nick: Option<String>,
    pub avatar: Option<String>,
    pub roles: Vec<String>,
}

#[derive(Clone, Debug, PartialEq)]
pub struct Guild {
    pub id: String,
    pub name: String,
    pub icon: Option<String>,
    pub owner_id: Option<String>,
    pub channels: Vec<Channel>,
    pub threads: Vec<Channel>,
    pub roles: Vec<Role>,
    /// The signed-in user's membership, when the payload carries it.
    pub me: Option<Member>,
    pub unavailable: bool,
}

#[derive(Clone, Debug, PartialEq)]
pub struct Attachment {
    pub filename: String,
    pub url: String,
    pub proxy_url: Option<String>,
    pub size: u64,
    pub content_type: Option<String>,
    pub width: Option<u32>,
    pub height: Option<u32>,
}

#[derive(Clone, Debug, PartialEq)]
pub struct EmbedMedia {
    pub url: String,
    pub proxy_url: Option<String>,
    pub width: Option<u32>,
    pub height: Option<u32>,
}

#[derive(Clone, Debug, PartialEq)]
pub struct Embed {
    pub kind: String,
    pub title: Option<String>,
    pub description: Option<String>,
    pub url: Option<String>,
    pub image: Option<EmbedMedia>,
    pub thumbnail: Option<EmbedMedia>,
    pub video: Option<EmbedMedia>,
}

/// A sticker reference. `format` 1 PNG, 2 APNG, 3 LOTTIE, 4 GIF.
#[derive(Clone, Debug, PartialEq)]
pub struct Sticker {
    pub id: String,
    pub name: String,
    pub format: u8,
}

#[derive(Clone, Debug, PartialEq)]
pub struct MessageReference {
    pub message_id: String,
    pub channel_id: String,
    pub guild_id: Option<String>,
}

#[derive(Clone, Debug, PartialEq)]
pub struct Message {
    pub id: String,
    pub channel_id: String,
    pub guild_id: Option<String>,
    pub author: User,
    pub member: Option<Member>,
    pub content: String,
    pub timestamp: Timestamp,
    pub edited_timestamp: Option<Timestamp>,
    pub kind: u8,
    pub mention_ids: Vec<String>,
    pub mention_role_ids: Vec<String>,
    pub mention_everyone: bool,
    pub attachments: Vec<Attachment>,
    pub embeds: Vec<Embed>,
    pub stickers: Vec<Sticker>,
    pub referenced: Option<Box<Message>>,
    /// The thread started from this message, if any.
    pub thread: Option<Box<Channel>>,
    pub nonce: Option<String>,
}

fn str_field(value: &Value, key: &str) -> Option<String> {
    match value.get(key)? {
        Value::String(text) => Some(text.clone()),
        Value::Number(number) => Some(number.to_string()),
        _ => None,
    }
}

fn required(value: &Value, key: &str, kind: &str) -> Result<String> {
    str_field(value, key).ok_or_else(|| EngineError::Protocol(format!("{kind} without {key}")))
}

fn parse_time(value: &Value, key: &str) -> Option<Timestamp> {
    value
        .get(key)
        .and_then(Value::as_str)
        .and_then(|text| text.parse::<Timestamp>().ok())
}

fn list<'a>(value: &'a Value, key: &str) -> impl Iterator<Item = &'a Value> {
    value
        .get(key)
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
}

fn flag(value: &Value, key: &str) -> bool {
    value.get(key).and_then(Value::as_bool).unwrap_or(false)
}

fn small(value: &Value, key: &str) -> Option<u32> {
    value
        .get(key)
        .and_then(Value::as_u64)
        .and_then(|number| u32::try_from(number).ok())
}

/// Permission bit sets are decimal strings since API v8 (older payloads use numbers).
fn bits(value: &Value, key: &str) -> u64 {
    match value.get(key) {
        Some(Value::String(text)) => text.parse().unwrap_or(0),
        Some(Value::Number(number)) => number.as_u64().unwrap_or(0),
        _ => 0,
    }
}

impl User {
    pub fn from_value(value: &Value) -> Result<Self> {
        Ok(Self {
            id: required(value, "id", "user")?,
            username: str_field(value, "username").unwrap_or_default(),
            global_name: str_field(value, "global_name"),
            avatar: str_field(value, "avatar"),
            bot: flag(value, "bot"),
        })
    }
}

impl Role {
    pub fn from_value(value: &Value) -> Result<Self> {
        let primary = value
            .get("colors")
            .and_then(|colors| colors.get("primary_color"))
            .and_then(Value::as_u64);
        Ok(Self {
            id: required(value, "id", "role")?,
            name: str_field(value, "name").unwrap_or_default(),
            color: primary
                .or_else(|| value.get("color").and_then(Value::as_u64))
                .unwrap_or(0) as u32,
            position: value.get("position").and_then(Value::as_i64).unwrap_or(0),
            permissions: bits(value, "permissions"),
        })
    }
}

impl PermissionOverwrite {
    fn from_value(value: &Value) -> Option<Self> {
        let kind = match value.get("type") {
            Some(Value::String(text)) if text == "member" => 1,
            Some(Value::String(_)) => 0,
            Some(other) => other.as_u64().unwrap_or(0) as u8,
            None => 0,
        };
        Some(Self {
            id: str_field(value, "id")?,
            kind,
            allow: bits(value, "allow"),
            deny: bits(value, "deny"),
        })
    }
}

impl Member {
    /// Parse a member; `user_id` falls back to the nested user (non-deduplicated payloads).
    pub fn from_value(value: &Value) -> Option<Self> {
        let user_id = str_field(value, "user_id")
            .or_else(|| value.get("user").and_then(|user| str_field(user, "id")))?;
        Some(Self {
            user_id,
            nick: str_field(value, "nick"),
            avatar: str_field(value, "avatar"),
            roles: list(value, "roles")
                .filter_map(|id| id.as_str().map(str::to_owned))
                .collect(),
        })
    }
}

impl Channel {
    /// Parse a channel. `users` resolves deduplicated `recipient_ids` when present.
    pub fn from_value(value: &Value, guild_id: Option<&str>, users: &[User]) -> Result<Self> {
        let mut recipients: Vec<User> = list(value, "recipients")
            .filter_map(|user| User::from_value(user).ok())
            .collect();
        for id in list(value, "recipient_ids").filter_map(Value::as_str) {
            if let Some(user) = users.iter().find(|user| user.id == id) {
                recipients.push(user.clone());
            }
        }
        let thread_metadata = value.get("thread_metadata").map(|meta| ThreadMetadata {
            archived: flag(meta, "archived"),
            locked: flag(meta, "locked"),
            archive_timestamp: parse_time(meta, "archive_timestamp"),
        });
        Ok(Self {
            id: required(value, "id", "channel")?,
            kind: value.get("type").and_then(Value::as_u64).unwrap_or(0) as u8,
            name: str_field(value, "name"),
            guild_id: str_field(value, "guild_id").or_else(|| guild_id.map(str::to_owned)),
            position: value.get("position").and_then(Value::as_i64).unwrap_or(0),
            parent_id: str_field(value, "parent_id"),
            last_message_id: str_field(value, "last_message_id"),
            topic: str_field(value, "topic"),
            recipients,
            permission_overwrites: list(value, "permission_overwrites")
                .filter_map(PermissionOverwrite::from_value)
                .collect(),
            owner_id: str_field(value, "owner_id"),
            thread_metadata,
            message_count: small(value, "message_count"),
            joined: value.get("member").is_some_and(Value::is_object),
        })
    }
}

impl Guild {
    /// Parse a REST partial guild or a gateway guild (with or without `properties`).
    /// `me` is the signed-in user's id, used to pick its member from `members`.
    pub fn from_value(value: &Value) -> Result<Self> {
        Self::parse(value, None)
    }

    pub fn parse(value: &Value, me: Option<&str>) -> Result<Self> {
        let id = required(value, "id", "guild")?;
        let properties = value.get("properties").unwrap_or(value);
        let member = me.and_then(|me| {
            list(value, "members")
                .filter_map(Member::from_value)
                .find(|member| member.user_id == me)
        });
        Ok(Self {
            name: str_field(properties, "name")
                .or_else(|| str_field(value, "name"))
                .unwrap_or_default(),
            icon: str_field(properties, "icon").or_else(|| str_field(value, "icon")),
            owner_id: str_field(properties, "owner_id").or_else(|| str_field(value, "owner_id")),
            channels: list(value, "channels")
                .filter_map(|channel| Channel::from_value(channel, Some(&id), &[]).ok())
                .collect(),
            threads: list(value, "threads")
                .filter_map(|thread| Channel::from_value(thread, Some(&id), &[]).ok())
                .collect(),
            roles: list(value, "roles")
                .filter_map(|role| Role::from_value(role).ok())
                .collect(),
            me: member,
            unavailable: flag(value, "unavailable"),
            id,
        })
    }
}

impl EmbedMedia {
    fn from_value(value: &Value) -> Option<Self> {
        Some(Self {
            url: str_field(value, "url")?,
            proxy_url: str_field(value, "proxy_url"),
            width: small(value, "width"),
            height: small(value, "height"),
        })
    }
}

impl Embed {
    fn from_value(value: &Value) -> Self {
        let media = |key: &str| value.get(key).and_then(EmbedMedia::from_value);
        Self {
            kind: str_field(value, "type").unwrap_or_else(|| "rich".into()),
            title: str_field(value, "title"),
            description: str_field(value, "description"),
            url: str_field(value, "url"),
            image: media("image"),
            thumbnail: media("thumbnail"),
            video: media("video"),
        }
    }
}

impl Message {
    pub fn from_value(value: &Value) -> Result<Self> {
        let author = value
            .get("author")
            .ok_or_else(|| EngineError::Protocol("message without author".into()))?;
        let author = User::from_value(author)?;
        let timestamp = parse_time(value, "timestamp")
            .ok_or_else(|| EngineError::Protocol("message without timestamp".into()))?;
        let referenced = match value.get("referenced_message") {
            Some(message @ Value::Object(_)) => Message::from_value(message).ok().map(Box::new),
            _ => None,
        };
        let guild_id = str_field(value, "guild_id");
        let member = value.get("member").map(|member| Member {
            user_id: author.id.clone(),
            nick: str_field(member, "nick"),
            avatar: str_field(member, "avatar"),
            roles: list(member, "roles")
                .filter_map(|id| id.as_str().map(str::to_owned))
                .collect(),
        });
        Ok(Self {
            id: required(value, "id", "message")?,
            channel_id: required(value, "channel_id", "message")?,
            member,
            content: str_field(value, "content").unwrap_or_default(),
            timestamp,
            edited_timestamp: parse_time(value, "edited_timestamp"),
            kind: value.get("type").and_then(Value::as_u64).unwrap_or(0) as u8,
            mention_ids: list(value, "mentions")
                .filter_map(|user| str_field(user, "id"))
                .collect(),
            mention_role_ids: list(value, "mention_roles")
                .filter_map(|id| id.as_str().map(str::to_owned))
                .collect(),
            mention_everyone: flag(value, "mention_everyone"),
            attachments: list(value, "attachments")
                .map(|attachment| Attachment {
                    filename: str_field(attachment, "filename").unwrap_or_default(),
                    url: str_field(attachment, "url").unwrap_or_default(),
                    proxy_url: str_field(attachment, "proxy_url"),
                    size: attachment.get("size").and_then(Value::as_u64).unwrap_or(0),
                    content_type: str_field(attachment, "content_type"),
                    width: small(attachment, "width"),
                    height: small(attachment, "height"),
                })
                .collect(),
            embeds: list(value, "embeds").map(Embed::from_value).collect(),
            stickers: list(value, "sticker_items")
                .filter_map(|sticker| {
                    Some(Sticker {
                        id: str_field(sticker, "id")?,
                        name: str_field(sticker, "name").unwrap_or_default(),
                        format: sticker
                            .get("format_type")
                            .and_then(Value::as_u64)
                            .unwrap_or(1) as u8,
                    })
                })
                .collect(),
            referenced,
            thread: value
                .get("thread")
                .and_then(|thread| Channel::from_value(thread, guild_id.as_deref(), &[]).ok())
                .map(Box::new),
            nonce: str_field(value, "nonce"),
            guild_id,
            author,
        })
    }
}
