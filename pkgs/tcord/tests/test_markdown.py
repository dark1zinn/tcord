import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Literal

import pytest
from rich.style import Style
from rich.text import Text
from tcord import i18n
from tcord.markdown import MarkdownStyles, RenderContext, plain, render

NOW = datetime(2026, 1, 31, 12, 0, tzinfo=UTC)
TS_FIXED = int(datetime(2026, 1, 31, 15, 5, 9, tzinfo=UTC).timestamp())


@pytest.fixture(autouse=True)
def english() -> None:
    i18n.setup("en")


def styled(text: Text, substring: str) -> Style:
    """Combined style of every span covering the first occurrence of ``substring``."""
    start = text.plain.index(substring)
    end = start + len(substring)
    covering = [
        Style.parse(span.style) if isinstance(span.style, str) else span.style
        for span in text.spans
        if span.start <= start and span.end >= end
    ]
    return Style.combine(covering) if covering else Style()


def has(text: Text, substring: str, style: str | Style) -> bool:
    actual = styled(text, substring)
    expected = Style.parse(style) if isinstance(style, str) else style
    return (actual + expected) == actual


def _no_name(_id: str) -> str | None:
    return None


def _no_role(_id: str) -> tuple[str, int] | None:
    return None


def ctx(
    *,
    user_name: Callable[[str], str | None] = _no_name,
    channel_name: Callable[[str], str | None] = _no_name,
    role: Callable[[str], tuple[str, int] | None] = _no_role,
    time_format: Literal["24h", "12h"] = "24h",
    reveal_spoilers: bool = False,
    ascii_only: bool = False,
    styles: MarkdownStyles | None = None,
) -> RenderContext:
    return RenderContext(
        user_name=user_name,
        channel_name=channel_name,
        role=role,
        time_format=time_format,
        reveal_spoilers=reveal_spoilers,
        ascii_only=ascii_only,
        now=lambda: NOW,
        tz=UTC,
        styles=styles or MarkdownStyles(),
    )


USERS = {"123": "ada", "456": "grace"}
ROLES = {"5": ("mods", 0xFF0000), "6": ("plain", 0)}
CHANNELS = {"7": "general"}


def resolving() -> RenderContext:
    return ctx(
        user_name=USERS.get,
        channel_name=CHANNELS.get,
        role=ROLES.get,
    )


@pytest.mark.parametrize(
    ("content", "expected_plain", "substring", "style"),
    [
        pytest.param("**bold**", "bold", "bold", "bold", id="bold"),
        pytest.param("*italic*", "italic", "italic", "italic", id="italic-star"),
        pytest.param("_italic_", "italic", "italic", "italic", id="italic-underscore"),
        pytest.param("__under__", "under", "under", "underline", id="underline"),
        pytest.param("~~gone~~", "gone", "gone", "strike", id="strike"),
        pytest.param("***both***", "both", "both", "bold italic", id="bold-italic-triple"),
        pytest.param(
            "**bold *both* end**", "bold both end", "both", "bold italic", id="nested-bold-italic"
        ),
        pytest.param(
            "**bold *both* end**", "bold both end", "bold", "bold", id="nested-outer-bold"
        ),
        pytest.param("*a **b** c*", "a b c", "b", "bold italic", id="nested-italic-bold"),
        pytest.param(
            "a *b* and [site](https://x.y/z) end",
            "a b and site end",
            "site",
            f"underline bright_blue {Style(link='https://x.y/z')}",
            id="masked-link-style",
        ),
        pytest.param("[site](https://x.y/z)", "site", "site", "underline", id="masked-link-line"),
        pytest.param(
            "see https://a.b/c.", "see https://a.b/c.", "https://a.b/c", "underline", id="bare-url"
        ),
    ],
)
def test_inline_constructs(content: str, expected_plain: str, substring: str, style: str) -> None:
    text = render(content, ctx())
    assert text.plain == expected_plain
    assert has(text, substring, style)


def test_masked_link_carries_url() -> None:
    text = render("[docs](https://example.com/x)", ctx())
    assert text.plain == "docs"
    assert styled(text, "docs").link == "https://example.com/x"


def test_bare_url_trims_trailing_punctuation_from_link() -> None:
    text = render("go https://a.b/c.", ctx())
    assert text.plain == "go https://a.b/c."
    link_spans = [
        (span.start, span.end)
        for span in text.spans
        if isinstance(span.style, Style) and span.style.link
    ]
    assert link_spans == [(3, 16)]


@pytest.mark.parametrize(
    ("content", "expected_plain"),
    [
        pytest.param("**open", "**open", id="unterminated-bold"),
        pytest.param("*a", "*a", id="unterminated-italic"),
        pytest.param("~~x", "~~x", id="unterminated-strike"),
        pytest.param("||x", "||x", id="unterminated-spoiler"),
        pytest.param("`x", "`x", id="unterminated-code"),
        pytest.param("snake_case_name", "snake_case_name", id="intra-word-underscore"),
        pytest.param("2 * 3 * 4", "2 * 3 * 4", id="math-stars"),
    ],
)
def test_unterminated_markers_render_literally(content: str, expected_plain: str) -> None:
    text = render(content, ctx())
    assert text.plain == expected_plain
    assert not styled(text, expected_plain.split()[0]).bold
    assert not styled(text, expected_plain.split()[0]).italic


def test_snake_case_is_not_italic() -> None:
    text = render("snake_case_name", ctx())
    assert not styled(text, "case").italic


@pytest.mark.parametrize(
    ("content", "expected_plain", "hidden"),
    [
        pytest.param("`**not bold**`", "**not bold**", "not bold", id="code-suppresses-bold"),
        pytest.param("`<@123>`", "<@123>", "<@123>", id="code-suppresses-mention"),
        pytest.param("`[a](https://x.y)`", "[a](https://x.y)", "a", id="code-suppresses-link"),
        pytest.param("`http://a.b`", "http://a.b", "http", id="code-suppresses-url"),
    ],
)
def test_code_span_is_verbatim(content: str, expected_plain: str, hidden: str) -> None:
    text = render(content, resolving())
    assert text.plain == expected_plain
    assert has(text, hidden, "on grey15")
    assert not styled(text, hidden).bold
    assert styled(text, hidden).link is None


def test_code_block_is_verbatim_and_drops_language_line() -> None:
    text = render("```py\nx = 1 *y* <@123>\n```", resolving())
    assert text.plain == "x = 1 *y* <@123>"
    assert has(text, "x = 1", "on grey15")
    assert not styled(text, "y").italic


def test_single_line_code_block() -> None:
    text = render("```hello world```", ctx())
    assert text.plain == "hello world"
    assert has(text, "hello world", "on grey15")


def test_text_around_code_block_is_still_formatted() -> None:
    text = render("**a**\n```\nb\n```\n*c*", ctx())
    assert text.plain == "a\nb\nc"
    assert has(text, "a", "bold")
    assert has(text, "c", "italic")


@pytest.mark.parametrize(
    ("content", "expected_plain", "probe", "hidden_style"),
    [
        pytest.param(r"\*not italic\*", "*not italic*", "not italic", "italic", id="escaped-star"),
        pytest.param(r"\<@123>", "<@123>", "<@123>", "bold", id="escaped-mention"),
        pytest.param(r"\`no code\`", "`no code`", "no code", "on grey15", id="escaped-backtick"),
        pytest.param(r"\~\~no\~\~", "~~no~~", "no", "strike", id="escaped-strike"),
    ],
)
def test_backslash_escapes(
    content: str, expected_plain: str, probe: str, hidden_style: str
) -> None:
    text = render(content, resolving())
    assert text.plain == expected_plain
    assert not has(text, probe, hidden_style)


def test_escaped_star_is_not_italic() -> None:
    text = render(r"\*not italic\*", ctx())
    assert text.plain == "*not italic*"
    assert not styled(text, "not").italic


def test_user_mention_resolves_with_resolver_hit() -> None:
    text = render("hi <@123> and <@!456>", resolving())
    assert text.plain == "hi @ada and @grace"
    assert has(text, "@ada", "bold bright_blue on grey19")
    assert has(text, "@grace", "bold bright_blue on grey19")


def test_user_mention_unknown_without_resolver_hit() -> None:
    text = render("<@999>", ctx())
    assert text.plain == "@unknown-user"
    assert has(text, "@unknown-user", "bold bright_blue on grey19")


def test_channel_mention() -> None:
    assert render("<#7>", resolving()).plain == "#general"
    assert render("<#8>", resolving()).plain == "#unknown-channel"


def test_role_mention_uses_role_color() -> None:
    text = render("<@&5>", resolving())
    assert text.plain == "@mods"
    assert has(text, "@mods", "#ff0000")
    assert has(text, "@mods", "bold")


def test_role_mention_without_color_keeps_mention_style() -> None:
    text = render("<@&6>", resolving())
    assert text.plain == "@plain"
    assert has(text, "@plain", "bold bright_blue on grey19")


def test_unknown_role_mention() -> None:
    assert render("<@&404>", resolving()).plain == "@unknown-role"


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        pytest.param("<:wave:1234>", ":wave:", id="static-emoji"),
        pytest.param("<a:dance:5678>", ":dance:", id="animated-emoji"),
    ],
)
def test_custom_emoji(content: str, expected: str) -> None:
    assert render(content, ctx()).plain == expected


@pytest.mark.parametrize(
    ("style", "expected"),
    [
        pytest.param("", "January 31, 2026 15:05", id="default-f"),
        pytest.param("t", "15:05", id="short-time"),
        pytest.param("T", "15:05:09", id="long-time"),
        pytest.param("d", "2026-01-31", id="short-date"),
        pytest.param("D", "January 31, 2026", id="long-date"),
        pytest.param("f", "January 31, 2026 15:05", id="date-time"),
        pytest.param("F", "Saturday, January 31, 2026 15:05", id="full-date-time"),
    ],
)
def test_timestamp_absolute_styles(style: str, expected: str) -> None:
    suffix = f":{style}" if style else ""
    assert render(f"<t:{TS_FIXED}{suffix}>", ctx()).plain == expected


@pytest.mark.parametrize(
    ("style", "expected"),
    [
        pytest.param("t", "3:05 PM", id="short-time-12h"),
        pytest.param("T", "3:05:09 PM", id="long-time-12h"),
        pytest.param("f", "January 31, 2026 3:05 PM", id="date-time-12h"),
    ],
)
def test_timestamp_12h(style: str, expected: str) -> None:
    text = render(f"<t:{TS_FIXED}:{style}>", ctx(time_format="12h"))
    assert text.plain == expected


def test_timestamp_12h_midnight() -> None:
    midnight = int(datetime(2026, 1, 31, 0, 0, tzinfo=UTC).timestamp())
    assert render(f"<t:{midnight}:t>", ctx(time_format="12h")).plain == "12:00 AM"


@pytest.mark.parametrize(
    ("offset", "expected"),
    [
        pytest.param(3 * 3600 + 300 + 9, "in 3 hours", id="future-hours"),
        pytest.param(300, "in 5 minutes", id="future-minutes"),
        pytest.param(-3 * 3600, "3 hours ago", id="past-hours"),
        pytest.param(-2 * 86400, "2 days ago", id="past-days"),
        pytest.param(-1, "1 second ago", id="past-one-second"),
        pytest.param(40, "in 40 seconds", id="future-seconds"),
    ],
)
def test_timestamp_relative(offset: int, expected: str) -> None:
    unix = int(NOW.timestamp()) + offset
    assert render(f"<t:{unix}:R>", ctx()).plain == expected


def test_timestamp_invalid_renders_literally() -> None:
    assert render("<t:99999999999999999999:f>", ctx()).plain == "<t:99999999999999999999:f>"


def test_spoiler_is_masked_by_default() -> None:
    text = render("||secret||", ctx())
    assert text.plain == "▒" * 6
    assert has(text, "▒▒▒▒▒▒", "dim")


def test_spoiler_is_revealed_when_requested() -> None:
    text = render("||secret||", ctx(reveal_spoilers=True))
    assert text.plain == "secret"
    assert has(text, "secret", "dim")


def test_ascii_only_variants() -> None:
    assert render("||ab||", ctx(ascii_only=True)).plain == "##"
    assert render("> q", ctx(ascii_only=True)).plain == "| q"
    assert render("- one\n* two", ctx(ascii_only=True)).plain == "* one\n* two"


def test_quote_gutter() -> None:
    text = render("> quoted", ctx())
    assert text.plain == "▎ quoted"
    assert has(text, "▎", "dim")


def test_block_quote_covers_rest_of_message() -> None:
    text = render(">>> first\nsecond\n# third", ctx())
    assert text.plain == "▎ first\n▎ second\n▎ third"


def test_quote_marker_needs_a_space() -> None:
    assert render(">quoted", ctx()).plain == ">quoted"


@pytest.mark.parametrize(
    ("content", "expected_plain", "substring"),
    [
        pytest.param("# Title", "Title", "Title", id="h1"),
        pytest.param("## Sub", "Sub", "Sub", id="h2"),
        pytest.param("### Third", "Third", "Third", id="h3"),
        pytest.param("-# small print", "small print", "small print", id="subtext"),
    ],
)
def test_block_prefixes(content: str, expected_plain: str, substring: str) -> None:
    text = render(content, ctx())
    assert text.plain == expected_plain
    if content.startswith("-#"):
        assert has(text, substring, "dim")
    else:
        assert has(text, substring, "bold")


def test_hash_without_space_is_not_heading() -> None:
    text = render("#notheading", ctx())
    assert text.plain == "#notheading"
    assert not styled(text, "notheading").bold


def test_lists_use_bullet() -> None:
    assert render("- one\n* two", ctx()).plain == "• one\n• two"


def test_mentions_inside_heading_keep_heading_style() -> None:
    text = render("# hi <@123>", resolving())
    assert text.plain == "hi @ada"
    assert has(text, "@ada", "bold")


def test_everyone_and_here_are_highlighted() -> None:
    text = render("@everyone and @here", ctx())
    assert text.plain == "@everyone and @here"
    assert has(text, "@everyone", "bold bright_blue on grey19")
    assert has(text, "@here", "bold bright_blue on grey19")


def test_plain_matches_render() -> None:
    content = "**a** <@123> ```\ncode\n``` [x](https://y.z)"
    assert plain(content, resolving()) == render(content, resolving()).plain


def test_custom_styles_are_used() -> None:
    styles = MarkdownStyles(code="reverse")
    text = render("`x`", ctx(styles=styles))
    assert has(text, "x", "reverse")


def test_default_styles_applied_without_theme() -> None:
    assert has(render("**b**", ctx()), "b", "bold")


def test_performance_4000_chars_mixed_markers() -> None:
    chunk = "**b** *i* `c` <@123> [l](https://x.y) ||s|| ~~x~~ __u__ _i_ \\* *open > q\n"
    content = (chunk * (4000 // len(chunk) + 1))[:4000]
    started = time.perf_counter()
    text = render(content, resolving())
    assert time.perf_counter() - started < 0.2
    assert len(text.plain) > 0


def test_performance_4000_chars_unterminated_markers() -> None:
    content = ("*_~|" * 1000)[:4000]
    started = time.perf_counter()
    text = render(content, resolving())
    assert time.perf_counter() - started < 0.2
    assert 0 < len(text.plain) <= len(content)
