from rich.text import Text
from tcord.markdown import CUSTOM_EMOJI, RenderContext, plain, render


def test_custom_emoji_uses_resolver_image_or_name() -> None:
    seen: list[tuple[str, str, bool]] = []

    def emoji(emoji_id: str, name: str, animated: bool) -> Text | None:
        seen.append((emoji_id, name, animated))
        return Text("[img]") if name == "cat" else None

    ctx = RenderContext(emoji=emoji)
    assert plain("hi <:cat:1> and <a:dog:2>", ctx) == "hi [img] and :dog:"
    assert seen == [("1", "cat", False), ("2", "dog", True)]


def test_emoji_inside_code_is_literal() -> None:
    ctx = RenderContext(emoji=lambda *_: Text("[img]"))
    assert plain("`<:cat:1>`", ctx) == "<:cat:1>"


def test_slash_command_mentions() -> None:
    text = render("run </ban user:123> now")
    assert text.plain == "run /ban user now"
    assert "bold" in str(text.spans[0].style)


def test_custom_emoji_pattern_finds_all() -> None:
    assert [m.groups() for m in CUSTOM_EMOJI.finditer("<a:x:1><:y:2>")] == [
        ("a", "x", "1"),
        ("", "y", "2"),
    ]
