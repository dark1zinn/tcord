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
}

#[derive(Clone, Debug, PartialEq)]
pub struct Guild {
    pub id: String,
    pub name: String,
    pub icon: Option<String>,
    pub channels: Vec<Channel>,
    pub roles: Vec<Role>,
    pub unavailable: bool,
}

#[derive(Clone, Debug, PartialEq)]
pub struct Attachment {
    pub filename: String,
    pub url: String,
    pub size: u64,
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
    pub author_nick: Option<String>,
    pub content: String,
    pub timestamp: Timestamp,
    pub edited_timestamp: Option<Timestamp>,
    pub kind: u8,
    pub mention_ids: Vec<String>,
    pub mention_role_ids: Vec<String>,
    pub mention_everyone: bool,
    pub attachments: Vec<Attachment>,
    pub embed_titles: Vec<String>,
    pub referenced: Option<Box<Message>>,
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

impl User {
    pub fn from_value(value: &Value) -> Result<Self> {
        Ok(Self {
            id: required(value, "id", "user")?,
            username: str_field(value, "username").unwrap_or_default(),
            global_name: str_field(value, "global_name"),
            bot: value.get("bot").and_then(Value::as_bool).unwrap_or(false),
        })
    }
}

impl Role {
    pub fn from_value(value: &Value) -> Result<Self> {
        Ok(Self {
            id: required(value, "id", "role")?,
            name: str_field(value, "name").unwrap_or_default(),
            color: value.get("color").and_then(Value::as_u64).unwrap_or(0) as u32,
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
        })
    }
}

impl Guild {
    /// Parse a REST partial guild or a gateway guild (with or without `properties`).
    pub fn from_value(value: &Value) -> Result<Self> {
        let id = required(value, "id", "guild")?;
        let properties = value.get("properties").unwrap_or(value);
        Ok(Self {
            name: str_field(properties, "name")
                .or_else(|| str_field(value, "name"))
                .unwrap_or_default(),
            icon: str_field(properties, "icon").or_else(|| str_field(value, "icon")),
            channels: list(value, "channels")
                .filter_map(|channel| Channel::from_value(channel, Some(&id), &[]).ok())
                .collect(),
            roles: list(value, "roles")
                .filter_map(|role| Role::from_value(role).ok())
                .collect(),
            unavailable: value
                .get("unavailable")
                .and_then(Value::as_bool)
                .unwrap_or(false),
            id,
        })
    }
}

impl Message {
    pub fn from_value(value: &Value) -> Result<Self> {
        let author = value
            .get("author")
            .ok_or_else(|| EngineError::Protocol("message without author".into()))?;
        let timestamp = parse_time(value, "timestamp")
            .ok_or_else(|| EngineError::Protocol("message without timestamp".into()))?;
        let referenced = match value.get("referenced_message") {
            Some(Value::Object(_)) => value
                .get("referenced_message")
                .and_then(|message| Message::from_value(message).ok())
                .map(Box::new),
            _ => None,
        };
        Ok(Self {
            id: required(value, "id", "message")?,
            channel_id: required(value, "channel_id", "message")?,
            guild_id: str_field(value, "guild_id"),
            author: User::from_value(author)?,
            author_nick: value
                .get("member")
                .and_then(|member| str_field(member, "nick")),
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
            mention_everyone: value
                .get("mention_everyone")
                .and_then(Value::as_bool)
                .unwrap_or(false),
            attachments: list(value, "attachments")
                .map(|attachment| Attachment {
                    filename: str_field(attachment, "filename").unwrap_or_default(),
                    url: str_field(attachment, "url").unwrap_or_default(),
                    size: attachment.get("size").and_then(Value::as_u64).unwrap_or(0),
                })
                .collect(),
            embed_titles: list(value, "embeds")
                .filter_map(|embed| str_field(embed, "title"))
                .collect(),
            referenced,
            nonce: str_field(value, "nonce"),
        })
    }
}
