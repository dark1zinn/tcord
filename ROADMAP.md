# Roadmap

## Foundation and bindings

- [x] Python workspace packages and Rust/PyO3 `tcord_core` extension
- [x] Cancellable Rust-owned authentication worker and safe Python state facade
- [x] Deterministic in-process mock testing; no fixture container is required

## Base chat milestone

- [x] Email/password login, MFA-code continuation, stored-session resume, QR remote-auth state, and logout
- [x] Textual login/chat surface with modal-style navigation and standard focus keys
- [x] Browse guild text channels and existing DMs
- [x] Read chronological history and send text messages
- [x] CAPTCHA-gated new-DM feedback; no creation request is attempted

## Resilience and accessibility

- [ ] Gateway events, reconnect/resume, unread indicators, and message update/delete handling
- [ ] Keyboard/keymap customization, screen-reader review, and terminal-size accessibility work

## CAPTCHA completion

- [ ] System-webview challenge flow (for example WebKitGTK) returning verified challenge results to Rust
- [ ] New DMs and other Discord actions that require CAPTCHA

## Voice and media

- [ ] Voice chat through Rust audio/network components
- [ ] Screen sharing

## Later feature parity

- [ ] Attachments, reactions, mentions, profiles, notifications, terminal image protocols, packaging, and plugin APIs

## Testing policy

Tests use sanitized hand-authored fixtures, in-process HTTP/WebSocket mocks, Rust boundary tests, and Python protocol fakes. Fixtures must never include account tokens, passwords, QR tickets, private message content, or copied endcord code. Optional live-account checks are manual only and must not run in CI.
