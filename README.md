# Tcord

Tcord is a keyboard-first Discord client for the terminal. Python
([Textual](https://textual.textualize.io/)) owns the UI and chat state. Rust owns
authentication, REST, the gateway and credential storage. Network work never runs in UI
callbacks.

> Automating Discord user accounts may violate Discord's Terms of Service. Tcord is not
> endorsed by Discord, and you are responsible for your own account risk.

## Features

- **Login**: email or phone with password, two-factor (authenticator app, SMS or backup
  code), and QR login with a countdown. The session is stored in the system keyring and
  restored on the next launch. Logout works without restarting.
- **Live chat over the gateway**: servers and DMs from READY, new, edited and deleted
  messages, typing indicators, and unread and mention badges. Read acks are sent when you
  view the latest message. The gateway reconnects and resumes on its own.
- **Messages**: the history is grouped by author with a blank line between groups and
  divided by day, and older messages load as you scroll up. Authors show their avatar and
  their nickname in the colour of their highest coloured role. Discord markdown, mentions,
  slash-command mentions and `<t:…>` timestamps are rendered.
- **Media**: image attachments, image and GIF embeds (the raw Tenor/GIPHY link is hidden),
  rich embeds, stickers and custom emoji are drawn in the terminal; an emoji-only message
  shows them large. GIFs and animated emoji, stickers and avatars play only on the
  highlighted message, at up to 8 fps.
- **Servers**: server icons in the rail with Discord-style unread pills and mention badges.
  Channels you can't view are hidden (or, if you choose, listed dimmed with a lock).
- **Threads and forums**: active and joined threads appear under their channel, and forum
  channels list their posts when expanded. Enter on a message that started a thread opens
  it.
- **Message actions**: send, reply, edit, delete and retry. A message shows as pending until
  Discord confirms it. A failed send can be retried with the same nonce, so it is never
  duplicated.
- **Navigation**:
  - standard, vim and helix key presets, with per-action overrides;
  - a quick switcher (`ctrl+k`), the command palette (`ctrl+p`) and a help screen (`F1`).
- **Interface**: three themes (`tcord-dark`, `tcord-light`, `tcord-high-contrast`), plus
  every built-in Textual theme. Cozy and compact density, 12h/24h time, an ASCII-only mode,
  reduced motion, and a layout that adapts to narrow terminals.
- **Languages**: English, Brazilian Portuguese and Spanish.

Not supported yet: voice, screen sharing, attachment upload, reactions, creating threads or
forum posts, Sixel and iTerm2 images, creating new DMs, and solving CAPTCHAs. When Discord
asks for a CAPTCHA, Tcord explains this and suggests QR login. Tcord never writes tokens in
plaintext.

### Images

With `image_protocol = "auto"`, Tcord picks the renderer from the terminal:

- **kitty** (kitty and Ghostty): sharp images through kitty's Unicode placeholders,
  including inline custom emoji.
- **halfblock** (any other truecolor terminal): images drawn with `▀` cells. Inline emoji
  stay as `:name:` text because they would be unreadable at that size.
- **off** (`NO_COLOR`, `TERM=dumb` or the Linux console): text only.

Inside tmux, Tcord asks tmux which terminal is attached. When it is kitty or Ghostty, Tcord
turns on `allow-passthrough` for its own pane while it runs (and restores the previous value
on exit) and sends kitty images through tmux. Set `tmux_passthrough = false` to keep your tmux
settings untouched; kitty images are then used only if passthrough is already on, otherwise
half blocks. GNU screen always uses half blocks.

Media is only downloaded from Discord's CDN and media proxy (redirects included), at most
16 MB per file, and cached under the platform cache directory (`~/.cache/tcord/media` on
Linux, up to 256 MB). Kitty images use at most 192 MB of terminal memory.

## Usage

```text
tcord [--config PATH] [--debug] [--version]
```

- `--config` uses a different settings file.
- `--debug` writes a log to the user log directory (`~/.local/state/tcord/log/tcord.log` on
  Linux).

### Settings

Open the settings screen with `F2`. Changes apply immediately, except the language, which
needs a restart.

Tcord reads the settings file from the first of these that applies:

1. the path given with `--config`;
2. `$TCORD_CONFIG_DIR/settings.toml`;
3. the platform config directory, for example `~/.config/tcord/settings.toml`.

The file is only created on the first save. It is written atomically with owner-only
permissions.

```toml
schema_version = 1

[general]
language = "auto"          # auto | en | pt_BR | es
remember_session = true    # keep the session in the system keyring

[appearance]
theme = "tcord-dark"       # tcord-dark | tcord-light | tcord-high-contrast | any Textual theme
time_format = "24h"        # 24h | 12h
show_timestamps = true
message_density = "cozy"   # cozy | compact
ascii_only = false
reduced_motion = false     # also stops GIF animation
image_protocol = "auto"    # auto | kitty | halfblock | off
show_avatars = true
show_images = true         # attachments, embeds, stickers and custom emoji
animate_media = true       # animate GIFs on the highlighted message
tmux_passthrough = true    # in tmux: allow kitty graphics passthrough for Tcord's pane

[layout]
show_server_rail = true
show_channel_list = true
channel_list_width = 28    # 20..48
show_inaccessible_channels = false  # list channels you can't view, dimmed and locked

[navigation]
preset = "standard"        # standard | vim | helix

[keymap]                   # action id = key sequence(s); see docs/keymaps.md
"cursor.down" = ["j", "down"]
```

Invalid values fall back to their defaults, and unknown keys are ignored. Both produce a
warning at startup. If the file is not valid TOML, Tcord asks for confirmation before
overwriting it.

### Keys

See [docs/keymaps.md](docs/keymaps.md) for every action, the three presets and the override
syntax. `F1` inside the app always shows the bindings currently in effect.

## Architecture

| Path | Role |
|---|---|
| `crates/tcord-engine` | Pure-Rust engine on Tokio:<br>- auth state machine (password, MFA, QR remote-auth v2, session restore)<br>- keyring credential store<br>- REST client with rate-limit buckets and retries<br>- gateway (zlib-stream, heartbeat, resume, op 37 subscriptions, member requests, threads)<br>- media fetcher limited to Discord's CDN hosts, with a size-capped disk cache<br>- web-client identity headers |
| `pkgs/tcord-core` | Thin PyO3 bindings (`tcord_core._native`) that expose engine calls as asyncio awaitables, plus typed stubs and exception classes. |
| `pkgs/client` | UI-agnostic Python layer:<br>- typed models and protocols<br>- glues to the native module<br>- `ChatStore`, which turns READY and gateway events into chat state and change notifications, and computes channel permissions from roles and overwrites<br>- Discord CDN URL helpers |
| `pkgs/tcord` | Textual application: screens, widgets, keymaps, settings, themes, i18n, the in-house image renderer (kitty placeholders and half blocks, built on Pillow) and the `tcord` CLI. |

The Discord build number and Chrome version that Tcord identifies as are pinned in
`crates/tcord-engine/client-identity.json`. The daily `Client identity` workflow
(`.github/workflows/client-identity.yml`) refreshes them and opens a pull request when they
change. This needs the repository setting **Settings → Actions → General → Allow GitHub
Actions to create and approve pull requests**. CI on that bot-created pull request starts once
a maintainer approves the workflow run on the pull request page.

## Development

Use the Nix dev shell through `direnv`. It provides Python 3.14, Rust, `uv`, `maturin`,
`just` and libdbus. Every build recipe checks for free disk space first and runs at lowered
priority with 4 jobs.

```text
just sync          # install workspace packages and dev tools
just core          # build the Rust extension into .venv
just check         # lint (fmt, clippy, ruff, ty), cargo-deny, Rust + Python tests, catalog check
just r             # run Tcord (just r -b runs the packaged binary)
just b             # build a single-file binary with Nuitka
```

Translations use gettext with Babel. Wrap user-visible strings in `_()`, `ngettext()` or
`N_()`, then run:

```text
just i18n-extract i18n-update   # refresh tcord.pot and merge it into each language
# translate pkgs/tcord/src/tcord/locale/<lang>/LC_MESSAGES/tcord.po
just i18n-compile               # rebuild the .mo files (committed)
```

A test fails if any template message is missing from the pt_BR or es catalog.

## Testing

Tests never talk to Discord:

- Rust engine tests use wiremock and an in-process WebSocket gateway.
- The Python client tests run the real extension against local HTTP and WebSocket fakes,
  with an in-memory credential store.
- The TUI is driven by Textual's pilot and a fake client.

Manual tests on a live account are optional and never run in CI.

## Reference and licensing boundaries

Tcord is MIT licensed. Endcord is used only as a behavior reference; no code or structure is
copied from it. Concord, Discordo and Oxicord are GPL-3.0 references, and Tcord includes none
of their source.
