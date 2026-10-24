# Tcord

Tcord is a work-in-progress Discord terminal client. Python owns the Textual UI and presentation state; Rust owns Discord authentication, REST transport, gateway work, voice/media work, and other operations that must not block the UI.

> Automating Discord user accounts may violate Discord's Terms of Service. Tcord is not endorsed by Discord, and users are responsible for their own account risk.

## Current base-chat support

- email/password login with MFA/authenticator or backup-code continuation;
- stored-session resume using the operating-system credential store, with memory-only fallback when unavailable;
- QR remote-auth state and payload presentation;
- logout without restarting the application;
- guild text channels and already-existing DMs;
- chronological history and text-message sends;
- a Discord-shaped Textual layout with modal-style and standard focus navigation;
- explicit CAPTCHA gating for new DMs and other unsupported CAPTCHA-required actions.

Voice chat, screen sharing, new-DM creation, attachments, and a system-webview CAPTCHA solver are deferred. Tcord does not write plaintext tokens.

## Architecture

- `pkgs/tcord-core` — Rust, Tokio, and PyO3 extension module (`tcord_core`). It runs the Discord worker and owns credential persistence.
- `pkgs/client` — transport-agnostic Python models, protocols, and async glues consumed by the UI.
- `pkgs/tcord` — Textual application and Click launcher.

The TUI receives models and authentication state from the client facade. Network work is not placed in widget callbacks.

## Development

A Nix shell plus `direnv` is recommended. The toolchain requires Python, Rust/Cargo, `uv`, `maturin`, and `just`.

```text
# Build/install the PyO3 module for local development
env -u VIRTUAL_ENV uv run maturin develop --manifest-path pkgs/tcord-core/Cargo.toml

# Run transport regression tests
PYTHONPATH=pkgs/client/src ./.venv/bin/python -W error::ResourceWarning -m unittest discover -s pkgs/client/tests -v

# Run Textual headless UI tests
(cd pkgs/tcord && env -u VIRTUAL_ENV PYTHONPATH=src uv run --package tcord python -m unittest discover -s tests -v)

# Launch the application
just r
```

## Testing

Tcord uses deterministic in-process mocks and sanitized protocol fixtures instead of Docker/Spacebar/Fosscord fixture containers. This keeps transport and UI tests independent from Discord availability. Manual live-account smoke tests are optional and never belong in CI.

## Reference and licensing boundaries

Tcord is MIT licensed. Endcord is behavior-only reference material: no code or structure may be copied. Concord, Discordo, and Oxicord are GPL-3.0 references; Tcord currently implements its own code and does not incorporate their source. Any future reuse must be reviewed for license compatibility before it is introduced.
