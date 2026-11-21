import pytest
from client.cdn import (
    attachment_preview_url,
    avatar_url,
    cdn_size,
    emoji_url,
    guild_icon_url,
    sticker_url,
)
from client.models import Attachment, Member, Sticker, StickerFormat, User


@pytest.mark.parametrize(("pixels", "size"), [(1, 16), (40, 40), (41, 44), (9999, 4096)])
def test_sizes_round_up_to_allowed_values(pixels: int, size: int) -> None:
    assert cdn_size(pixels) == size


def test_avatar_precedence() -> None:
    user = User("80351110224678912", "nelly", avatar="abc")
    member = Member(user.id, avatar="a_guild")
    assert avatar_url(user, member, "10", 64) == (
        "https://cdn.discordapp.com/guilds/10/users/80351110224678912/avatars/a_guild.gif?size=64"
    )
    assert avatar_url(user, None, "10", 64) == (
        "https://cdn.discordapp.com/avatars/80351110224678912/abc.png?size=64"
    )
    default = avatar_url(User("80351110224678912", "nelly"), None, None, 64)
    index = (80351110224678912 >> 22) % 6
    assert default == f"https://cdn.discordapp.com/embed/avatars/{index}.png"


def test_icons_emoji_and_stickers() -> None:
    assert guild_icon_url("1", "hash", 48).endswith("/icons/1/hash.png?size=48")
    assert emoji_url("9", animated=True, pixels=32).endswith("/emojis/9.gif?size=32")
    assert sticker_url(Sticker("5", "wave", StickerFormat.LOTTIE), 160) is None
    gif = sticker_url(Sticker("5", "wave", StickerFormat.GIF), 160)
    assert gif == "https://media.discordapp.net/stickers/5.gif?size=160"


def test_attachment_previews_keep_signatures_and_animation() -> None:
    image = Attachment(
        "cat.png",
        "https://cdn.discordapp.com/attachments/1/2/cat.png?ex=1&is=2&hm=3",
        10,
        proxy_url="https://media.discordapp.net/attachments/1/2/cat.png?ex=1&is=2&hm=3",
        content_type="image/png",
    )
    assert attachment_preview_url(image, 320, 160) == (
        "https://media.discordapp.net/attachments/1/2/cat.png?ex=1&is=2&hm=3&width=320&height=160"
    )
    gif = Attachment("a.gif", "https://cdn.discordapp.com/a.gif", 1, content_type="image/gif")
    assert attachment_preview_url(gif, 320, 160) == "https://cdn.discordapp.com/a.gif"
