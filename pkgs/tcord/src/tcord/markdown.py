"""Discord-flavored markdown rendered to :class:`rich.text.Text`.

The message is cut into fenced code blocks and text first. Text is processed line by line for
block prefixes (quotes, headings, subtext, lists), then each line is scanned once for inline
constructs. Closing markers are searched forward with a memo of failed searches per marker, so
unterminated markers never trigger repeated scans of the same text.
"""

import re
import string
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, tzinfo

from rich.style import Style
from rich.text import Text

from .i18n import _, ngettext

_MAX_DEPTH = 8
_NULL = Style()
_BOLD = Style(bold=True)
_ITALIC = Style(italic=True)
_UNDERLINE = Style(underline=True)
_STRIKE = Style(strike=True)
_BOLD_ITALIC = _BOLD + _ITALIC
# Ordered longest first; ``None`` marks spoilers, which are handled separately.
_EMPHASIS: tuple[tuple[str, Style | None], ...] = (
    ("***", _BOLD_ITALIC),
    ("**", _BOLD),
    ("__", _UNDERLINE),
    ("~~", _STRIKE),
    ("||", None),
    ("*", _ITALIC),
    ("_", _ITALIC),
)
_ANGLE = re.compile(
    r"<(?:@!?(?P<user>\d+)|@&(?P<role>\d+)|#(?P<chan>\d+)"
    r"|(?P<anim>a?):(?P<emoji>\w+):(?P<eid>\d+)"
    r"|/(?P<command>[\w-]+(?: [\w-]+){0,2}):\d+"
    r"|t:(?P<ts>-?\d+)(?::(?P<style>[tTdDfFR]))?)>"
)
#: A whole message made only of custom emoji is shown large ("jumbo"), like Discord.
CUSTOM_EMOJI = re.compile(r"<(a?):(\w+):(\d+)>")
JUMBO_LIMIT = 27
_LINK = re.compile(r"\[([^\[\]\n]+)\]\((https?://[^\s)]+)\)")
_URL = re.compile(r"https?://[^\s<>]+")
_EVERYONE = re.compile(r"@(?:everyone|here)\b")
_ESCAPABLE = frozenset(string.punctuation)
_TRIGGERS = frozenset("\\`<[@*_~|h")
_UNITS: tuple[tuple[str, int], ...] = (
    ("second", 1),
    ("minute", 60),
    ("hour", 3600),
    ("day", 86400),
    ("month", 2_592_000),
    ("year", 31_536_000),
)


@dataclass(frozen=True, slots=True)
class MarkdownStyles:
    """Rich style strings used for each construct."""

    code: str = "on grey15"
    link: str = "underline bright_blue"
    mention: str = "bold bright_blue on grey19"
    quote_gutter: str = "dim"
    spoiler: str = "dim"
    subtext: str = "dim"
    heading: str = "bold"


def _no_name(_id: str) -> None:
    return None


def _no_role(_id: str) -> None:
    return None


def _no_emoji(_id: str, _name: str, _animated: bool) -> None:
    return None


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class RenderContext:
    """Resolvers and display options for :func:`render`."""

    user_name: Callable[[str], str | None] = _no_name
    channel_name: Callable[[str], str | None] = _no_name
    role: Callable[[str], tuple[str, int] | None] = _no_role
    #: Inline image for a custom emoji ``(id, name, animated)``; ``None`` shows ``:name:``.
    emoji: Callable[[str, str, bool], Text | None] = _no_emoji
    reveal_spoilers: bool = False
    ascii_only: bool = False
    time_format: str = "24h"
    now: Callable[[], datetime] = _utc_now
    tz: tzinfo | None = None
    styles: MarkdownStyles = field(default_factory=MarkdownStyles)


_DEFAULT_CONTEXT = RenderContext()


def render(content: str, ctx: RenderContext = _DEFAULT_CONTEXT) -> Text:
    """Render a Discord message body into styled text."""
    return _Renderer(ctx).run(content)


def plain(content: str, ctx: RenderContext = _DEFAULT_CONTEXT) -> str:
    """Return the rendered text without styling, for previews and search."""
    return render(content, ctx).plain


def _segments(content: str) -> Iterator[tuple[bool, str]]:
    """Yield ``(is_code, text)`` pairs; code bodies have their language line removed."""
    pos = 0
    search = 0
    while True:
        start = content.find("```", search)
        if start < 0:
            break
        if start > 0 and content[start - 1] == "\\":
            search = start + 1
            continue
        end = content.find("```", start + 3)
        if end < 0:
            break
        if start > pos:
            yield False, content[pos:start]
        yield True, _code_body(content[start + 3 : end])
        pos = search = end + 3
    yield False, content[pos:]


def _code_body(raw: str) -> str:
    newline = raw.find("\n")
    if newline >= 0 and len(raw[:newline].split()) <= 1:
        raw = raw[newline + 1 :]
    if raw.endswith("\n"):
        raw = raw[:-1]
    return raw


def _closer(s: str, marker: str, start: int) -> int:
    """Index of a closing ``marker`` after ``start`` that ends a run, or -1."""
    char = marker[0]
    size = len(marker)
    length = len(s)
    j = s.find(marker, start)
    while j >= 0:
        end = j + size
        if (
            j > start
            and s[j - 1] not in (char, "\\")
            and not s[j - 1].isspace()
            and (end == length or s[end] != char)
            and not (char == "_" and end < length and s[end].isalnum())
        ):
            return j
        j = s.find(marker, j + 1)
    return -1


def _clock(dt: datetime, twelve_hour: bool, seconds: bool) -> str:
    tail = f":{dt.second:02d}" if seconds else ""
    if not twelve_hour:
        return f"{dt.hour:02d}:{dt.minute:02d}{tail}"
    hour = dt.hour % 12 or 12
    suffix = "AM" if dt.hour < 12 else "PM"
    return f"{hour}:{dt.minute:02d}{tail} {suffix}"


def _relative(delta: float) -> str:
    past = delta < 0
    seconds = abs(delta)
    unit, size = "second", 1
    for name, span in _UNITS:
        if seconds >= span:
            unit, size = name, span
    count = int(seconds // size)
    if unit == "second":
        text = (
            ngettext("{n} second ago", "{n} seconds ago", count)
            if past
            else ngettext("in {n} second", "in {n} seconds", count)
        )
    elif unit == "minute":
        text = (
            ngettext("{n} minute ago", "{n} minutes ago", count)
            if past
            else ngettext("in {n} minute", "in {n} minutes", count)
        )
    elif unit == "hour":
        text = (
            ngettext("{n} hour ago", "{n} hours ago", count)
            if past
            else ngettext("in {n} hour", "in {n} hours", count)
        )
    elif unit == "day":
        text = (
            ngettext("{n} day ago", "{n} days ago", count)
            if past
            else ngettext("in {n} day", "in {n} days", count)
        )
    elif unit == "month":
        text = (
            ngettext("{n} month ago", "{n} months ago", count)
            if past
            else ngettext("in {n} month", "in {n} months", count)
        )
    else:
        text = (
            ngettext("{n} year ago", "{n} years ago", count)
            if past
            else ngettext("in {n} year", "in {n} years", count)
        )
    return text.format(n=count)


def _trim_url(url: str) -> str:
    while url.endswith((".", ",", ";", ":", "!", "?")) or (
        url.endswith(")") and url.count(")") > url.count("(")
    ):
        url = url[:-1]
    return url


class _Renderer:
    def __init__(self, ctx: RenderContext) -> None:
        self.ctx = ctx
        styles = ctx.styles
        self.code = Style.parse(styles.code)
        self.link = Style.parse(styles.link)
        self.mention = Style.parse(styles.mention)
        self.quote_gutter = Style.parse(styles.quote_gutter)
        self.spoiler = Style.parse(styles.spoiler)
        self.subtext = Style.parse(styles.subtext)
        self.heading = Style.parse(styles.heading)
        self.gutter = "|" if ctx.ascii_only else "▎"
        self.bullet = "*" if ctx.ascii_only else "•"
        self.mask = "#" if ctx.ascii_only else "▒"
        self.out = Text()
        self.big_quote = False

    def run(self, content: str) -> Text:
        line_start = True
        for is_code, chunk in _segments(content):
            if is_code:
                self.out.append(chunk, self.code)
                line_start = False
            else:
                self._text(chunk, line_start)
                line_start = chunk.endswith("\n") or (chunk == "" and line_start)
        return self.out

    def _text(self, chunk: str, line_start: bool) -> None:
        for index, line in enumerate(chunk.split("\n")):
            if index:
                self.out.append("\n")
            self._line(line, line_start if index == 0 else True)

    def _line(self, line: str, at_start: bool) -> None:
        quoted = self.big_quote
        content = line
        if at_start:
            if line.startswith(">>> "):
                self.big_quote = quoted = True
                content = line[4:]
            elif line.startswith("> "):
                quoted = True
                content = line[2:]
        if quoted:
            self.out.append(self.gutter + " ", self.quote_gutter)
        base = _NULL
        if at_start:
            for marker in ("### ", "## ", "# "):
                if content.startswith(marker):
                    base = self.heading
                    content = content[len(marker) :]
                    break
            else:
                if content.startswith("-# "):
                    base = self.subtext
                    content = content[3:]
                elif content.startswith(("- ", "* ")):
                    self.out.append(self.bullet + " ")
                    content = content[2:]
        self._inline(content, base, 0)

    def _emit(self, s: str, lit: int, end: int, base: Style) -> None:
        if lit < end:
            self.out.append(s[lit:end], base)

    def _inline(self, s: str, base: Style, depth: int) -> None:
        failed: set[str] = set()
        i = lit = 0
        while i < len(s):
            end = self._construct(s, i, lit, base, depth, failed)
            if end < 0:
                i += 1
            else:
                i = lit = end
        self._emit(s, lit, len(s), base)

    def _construct(
        self, s: str, i: int, lit: int, base: Style, depth: int, failed: set[str]
    ) -> int:
        """Emit the construct starting at ``i`` and return its end, or -1 if none starts there."""
        ch = s[i]
        if ch not in _TRIGGERS:
            return -1
        if ch == "\\":
            if i + 1 < len(s) and s[i + 1] in _ESCAPABLE:
                self._emit(s, lit, i, base)
                self.out.append(s[i + 1], base)
                return i + 2
            return -1
        if ch == "`":
            j = s.find("`", i + 1)
            if j > i + 1:
                self._emit(s, lit, i, base)
                self.out.append(s[i + 1 : j], base + self.code)
                return j + 1
            return -1
        if ch == "<":
            match = _ANGLE.match(s, i)
            if match is None:
                return -1
            self._emit(s, lit, i, base)
            self._angle(match, base)
            return match.end()
        if ch == "[":
            match = _LINK.match(s, i)
            if match is None or depth >= _MAX_DEPTH:
                return -1
            self._emit(s, lit, i, base)
            url = match.group(2)
            self._inline(match.group(1), base + self.link + Style(link=url), depth + 1)
            return match.end()
        if ch == "h":
            match = _URL.match(s, i)
            if match is None:
                return -1
            url = _trim_url(match.group(0))
            if len(url) <= len("https://"):
                return -1
            self._emit(s, lit, i, base)
            self.out.append(url, base + self.link + Style(link=url))
            return i + len(url)
        if ch == "@":
            match = _EVERYONE.match(s, i)
            if match is None:
                return -1
            self._emit(s, lit, i, base)
            self.out.append(match.group(0), base + self.mention)
            return match.end()
        return self._emphasis(s, i, lit, base, depth, failed)

    def _emphasis(self, s: str, i: int, lit: int, base: Style, depth: int, failed: set[str]) -> int:
        for marker, style in _EMPHASIS:
            if marker in failed or not s.startswith(marker, i):
                continue
            if marker[0] == "_" and i > 0 and s[i - 1].isalnum():
                continue
            inner_start = i + len(marker)
            if inner_start >= len(s) or s[inner_start].isspace():
                continue
            if depth >= _MAX_DEPTH:
                return -1
            close = _closer(s, marker, inner_start)
            if close < 0:
                failed.add(marker)
                continue
            self._emit(s, lit, i, base)
            inner = s[inner_start:close]
            if style is None:
                self._spoiler(inner, base, depth)
            else:
                self._inline(inner, base + style, depth + 1)
            return close + len(marker)
        return -1

    def _spoiler(self, inner: str, base: Style, depth: int) -> None:
        style = base + self.spoiler
        if self.ctx.reveal_spoilers:
            self._inline(inner, style, depth + 1)
        else:
            self.out.append(self.mask * len(inner), style)

    def _angle(self, match: re.Match[str], base: Style) -> None:
        ctx = self.ctx
        if (user_id := match.group("user")) is not None:
            name = ctx.user_name(user_id)
            text = f"@{name}" if name else _("@unknown-user")
            self.out.append(text, base + self.mention)
        elif (role_id := match.group("role")) is not None:
            found = ctx.role(role_id)
            if found is None:
                self.out.append(_("@unknown-role"), base + self.mention)
            else:
                name, color = found
                style = base + self.mention
                if color:
                    style = style + Style(color=f"#{color & 0xFFFFFF:06x}")
                self.out.append(f"@{name}", style)
        elif (chan_id := match.group("chan")) is not None:
            name = ctx.channel_name(chan_id)
            text = f"#{name}" if name else _("#unknown-channel")
            self.out.append(text, base + self.mention)
        elif (emoji := match.group("emoji")) is not None:
            image = ctx.emoji(match.group("eid"), emoji, bool(match.group("anim")))
            if image is not None:
                self.out.append_text(image)
            else:
                self.out.append(f":{emoji}:", base)
        elif (command := match.group("command")) is not None:
            self.out.append(f"/{command}", base + self.mention)
        else:
            raw_ts = match.group("ts")
            text = self._timestamp(int(raw_ts), match.group("style") or "f")
            self.out.append(match.group(0) if text is None else text, base)

    def _timestamp(self, unix: int, style: str) -> str | None:
        try:
            dt = datetime.fromtimestamp(unix, self.ctx.tz)
        except OverflowError, OSError, ValueError:
            return None
        if style == "R":
            return _relative(unix - self.ctx.now().timestamp())
        twelve = self.ctx.time_format == "12h"
        short = _clock(dt, twelve, seconds=False)
        if style == "t":
            return short
        if style == "T":
            return _clock(dt, twelve, seconds=True)
        if style == "d":
            return f"{dt.year:04d}-{dt.month:02d}-{dt.day:02d}"
        long_date = f"{dt:%B} {dt.day}, {dt.year}"
        if style == "D":
            return long_date
        if style == "f":
            return f"{long_date} {short}"
        return f"{dt:%A}, {long_date} {short}"
