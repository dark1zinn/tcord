# Keymaps

Tcord is fully keyboard-driven. Pick a preset in **Settings → Navigation** (`F2`) or in
`settings.toml`, then override single actions in the `[keymap]` table.

## Presets

- **standard**: Discord-like. There are no modes. Arrows, Home/End, PageUp/PageDown, Enter
  and Tab keep their usual meaning in the focused pane. Typing a character while the message
  list is focused moves to the composer and inserts it.
- **vim**: modal. Outside the composer you are in `NORMAL` mode: unbound characters do
  nothing and `j`/`k`, `g g`/`G`, `ctrl+d`/`ctrl+u` move. `i` or `a` enters `INSERT` (the
  composer), and `Escape` returns to `NORMAL`.
- **helix**: modal like vim, with helix-style motions (`g e` bottom, `g n`/`g p` servers) and
  a `space` leader (`space f` switcher, `space ?` palette, `space h` help, `space s`
  settings).

The status bar shows the mode (`NORMAL`/`INSERT`) and any pending chord, for example `g`.
A chord left unfinished for 1 second is dropped. `F1` opens the help screen, which always
lists the bindings currently in effect.

`ctrl+q` (quit), `ctrl+k` (switcher) and `F1` (help) also work while you type in the
composer.

## Actions

"native" means the preset does not handle the action. The focused widget's own key does it
(arrows, Home/End, PageUp/PageDown, Enter).

| Action | Description | standard | vim | helix |
|---|---|---|---|---|
| `app.quit` | Quit | `ctrl+q` | `ctrl+q` | `ctrl+q` |
| `app.switcher` | Quick switcher | `ctrl+k` | `ctrl+k`, `/` | `ctrl+k`, `space f` |
| `app.palette` | Command palette | `ctrl+p` | `ctrl+p`, `:` | `ctrl+p`, `space ?` |
| `app.help` | Keyboard shortcuts | `f1` | `f1`, `?` | `f1`, `space h` |
| `app.settings` | Settings | `f2` | `f2` | `f2`, `space s` |
| `toggle.rail` | Toggle server rail | `alt+g` | `alt+g` | `alt+g` |
| `toggle.channels` | Toggle channel list | `alt+c` | `alt+c` | `alt+c` |
| `focus.rail` | Focus server rail | `alt+1` | `alt+1` | `alt+1` |
| `focus.channels` | Focus channel list | `alt+2` | `alt+2` | `alt+2` |
| `focus.messages` | Focus messages | `alt+3` | `alt+3` | `alt+3` |
| `focus.composer` | Focus composer | `alt+4` | `alt+4`, `i`, `a` | `alt+4`, `i`, `a` |
| `focus.left` | Focus pane to the left | — | `ctrl+w h` | `ctrl+w h` |
| `focus.right` | Focus pane to the right | — | `ctrl+w l` | `ctrl+w l` |
| `cursor.down` | Move down | native | `j` | `j` |
| `cursor.up` | Move up | native | `k` | `k` |
| `cursor.top` | Go to top | native | `g g` | `g g` |
| `cursor.bottom` | Go to bottom | native | `G` | `g e` |
| `page.down` | Page down | native | `ctrl+d` | `ctrl+d` |
| `page.up` | Page up | native | `ctrl+u` | `ctrl+u` |
| `item.open` | Open | native | `l` | `l` |
| `item.back` | Back | `escape` | `h`, `escape` | `h`, `escape` |
| `guild.next` | Next server | `ctrl+alt+down` | `g t` | `g n` |
| `guild.prev` | Previous server | `ctrl+alt+up` | `g T` | `g p` |
| `channel.next` | Next channel | `alt+down` | `] c` | `] c` |
| `channel.prev` | Previous channel | `alt+up` | `[ c` | `[ c` |
| `channel.next_unread` | Next unread channel | `alt+shift+down` | `] u` | `] u` |
| `channel.prev_unread` | Previous unread channel | `alt+shift+up` | `[ u` | `[ u` |
| `message.reply` | Reply | `r` | `r` | `r` |
| `message.edit` | Edit message | `e` | `e` | `c` |
| `message.delete` | Delete message | `delete` | `d d` | `d` |
| `message.retry` | Retry failed message | `R` | `R` | `R` |
| `composer.send` | Send | `enter` | `enter` | `enter` |
| `composer.newline` | New line | `ctrl+j`, `alt+enter`, `shift+enter` | same | same |
| `composer.cancel` | Cancel reply or edit | `escape` | `escape` | `escape` |
| `composer.edit_last` | Edit last message (empty composer) | `up` | `up` | `up` |

## Overrides

Each entry replaces **all** of the preset's keys for that one action:

```toml
[navigation]
preset = "vim"

[keymap]
"cursor.down" = ["j", "down"]
"app.switcher" = "ctrl+t"        # a single string is accepted too
"message.delete" = ["space d"]   # multi-key chords are space-separated
```

Key syntax:

- A single printable character (`"G"`, `"?"`, `"["`) matches the typed character, so `G`
  means shift+g.
- Anything longer is a key name with optional modifiers, such as `ctrl+d`, `alt+shift+down`,
  `escape`, `enter`, `space`, `f1` or `delete`. Modifier order doesn't matter.

Tcord ignores an override and shows a warning at startup when:

- the action id is unknown;
- a key is invalid;
- the override conflicts with another action, either by reusing the same sequence or by
  being a prefix of it (for example `g` versus `g g`).

Composer actions are only matched while you type, so they never conflict with list
navigation keys.
