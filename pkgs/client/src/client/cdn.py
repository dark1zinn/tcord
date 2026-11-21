"""Discord CDN and media-proxy URLs for avatars, icons, emoji, stickers and attachments.

Reference: https://docs.discord.food/reference#cdn-formatting
"""

from __future__ import annotations

from urllib.parse import urlencode

from .models import Attachment, EmbedMedia, Member, Sticker, StickerFormat, User

CDN = "https://cdn.discordapp.com"
MEDIA = "https://media.discordapp.net"
#: Sizes the CDN accepts; requests are rounded up to the next one.
_SIZES = (16, 20, 22, 24, 28, 32, 40, 44, 48, 56, 60, 64, 80, 96, 100, 128, 160, 240, 256, 300,
          320, 480, 512, 600, 640, 1024, 1280, 1536, 2048, 3072, 4096)  # fmt: skip


def cdn_size(pixels: int) -> int:
    return next((size for size in _SIZES if size >= pixels), _SIZES[-1])


def _animated(image_hash: str) -> bool:
    return image_hash.startswith("a_")


def avatar_url(user: User, member: Member | None, guild_id: str | None, pixels: int) -> str:
    """Guild avatar when set, else the user avatar, else the default avatar."""
    size = cdn_size(pixels)
    if member is not None and member.avatar and guild_id is not None:
        extension = "gif" if _animated(member.avatar) else "png"
        return (
            f"{CDN}/guilds/{guild_id}/users/{user.id}/avatars/{member.avatar}.{extension}"
            f"?size={size}"
        )
    if user.avatar:
        extension = "gif" if _animated(user.avatar) else "png"
        return f"{CDN}/avatars/{user.id}/{user.avatar}.{extension}?size={size}"
    index = (int(user.id) >> 22) % 6 if user.id.isdigit() else 0
    return f"{CDN}/embed/avatars/{index}.png"


def guild_icon_url(guild_id: str, icon: str, pixels: int) -> str:
    extension = "gif" if _animated(icon) else "png"
    return f"{CDN}/icons/{guild_id}/{icon}.{extension}?size={cdn_size(pixels)}"


def emoji_url(emoji_id: str, *, animated: bool, pixels: int) -> str:
    extension = "gif" if animated else "png"
    return f"{CDN}/emojis/{emoji_id}.{extension}?size={cdn_size(pixels)}"


def sticker_url(sticker: Sticker, pixels: int) -> str | None:
    """Image URL of a sticker; ``None`` for Lottie (vector animation) stickers."""
    if sticker.format == StickerFormat.LOTTIE:
        return None
    if sticker.format == StickerFormat.GIF:
        return f"{MEDIA}/stickers/{sticker.id}.gif?size={cdn_size(pixels)}"
    return f"{MEDIA}/stickers/{sticker.id}.png?size={cdn_size(pixels)}"


def _with_query(url: str, **params: int | str) -> str:
    separator = "&" if "?" in url else "?"
    return f"{url}{separator}{urlencode(params)}"


def attachment_preview_url(attachment: Attachment, width: int, height: int) -> str:
    """A resized preview from the media proxy (keeps the signed query parameters)."""
    base = attachment.proxy_url or attachment.url
    animated = (attachment.content_type or "") == "image/gif"
    if animated:
        return base  # resizing through the proxy drops animation frames
    return _with_query(base, width=width, height=height)


def embed_media_url(media: EmbedMedia) -> str:
    """Proxied URL of embed media; external hosts are only reachable through the proxy."""
    return media.proxy_url or media.url
