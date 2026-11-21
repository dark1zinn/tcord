# Roadmap

## Foundation and bindings

- [x] Python workspace packages; pure-Rust `tcord-engine` crate with thin PyO3 `tcord_core` bindings
- [x] Native asyncio awaitables, typed models and typed exceptions across the boundary
- [x] Race-free auth state (logout always wins), keyring-backed sessions with memory fallback
- [x] Deterministic in-process mock testing; no fixture container is required
- [x] Quality gates: rustfmt, clippy, cargo-deny, ruff, ty, pytest; CI on every pull request

## Base chat milestone

- [x] Email/password login, MFA method selection (authenticator, SMS, backup code), session restore, logout
- [x] QR login following remote-auth v2, with expiry and retry
- [x] Discord web-client identity headers, rate-limit buckets and CAPTCHA detection
- [x] Gateway: zlib-stream, heartbeats, resume, invalid-session handling, guild subscriptions
- [x] Browse servers, categories, text channels and existing DMs
- [x] Live history: new/edited/deleted messages, paging older messages, day dividers
- [x] Send, reply, edit, delete, retry with idempotent nonces
- [x] Unread and mention badges, read acks, typing indicators
- [x] CAPTCHA-gated feedback; no CAPTCHA-requiring request is attempted

## Resilience and accessibility

- [x] Reconnect/resume and connection status in the UI
- [x] Keyboard-first navigation: standard, vim and helix presets with per-action overrides
- [x] Settings screen and file, themes (including high contrast), ASCII-only mode, reduced motion, narrow-terminal layout
- [x] Internationalization (gettext): English, Brazilian Portuguese, Spanish
- [ ] Screen-reader review

## CAPTCHA completion

- [ ] System-webview challenge flow (for example WebKitGTK) returning verified challenge results to Rust
- [ ] New DMs and other Discord actions that require CAPTCHA

## Voice and media

- [ ] Voice chat through Rust audio/network components
- [ ] Screen sharing

## Rich media chat

- [x] Terminal images: kitty Unicode placeholders (kitty, Ghostty) with a half-block fallback
- [x] Avatars, server icons, unread pills and mention badges in the server rail
- [x] Image attachments, embeds, GIFs (animated on the highlighted message), stickers, custom and jumbo emoji
- [x] Role-coloured nicknames and spacing between message groups
- [x] Permission-aware channel list (inaccessible channels hidden by default)
- [x] Threads under their parent channel; forum posts with paging
- [ ] Sixel and iTerm2 image protocols
- [ ] Lottie stickers (shown as a text label today)

## Later feature parity

- [ ] Attachment upload, reactions, creating threads and forum posts, member list, profiles, notifications, plugin APIs

## Testing policy

Tests use sanitized hand-authored fixtures, in-process HTTP/WebSocket mocks, Rust boundary tests, and Python protocol fakes. Fixtures must never include account tokens, passwords, QR tickets, private message content, or copied endcord code. Optional live-account checks are manual only and must not run in CI.
