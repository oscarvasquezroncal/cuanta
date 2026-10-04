# tui — local rules

## Purpose
The Textual app (`cuanta ui`, optionally served on the web via `textual-serve`).

## Local rules
- No hard-coded user-visible strings in widgets — take text from the i18n catalog
  (`tui/i18n.py`, `tui/locales/`); every new key exists in every locale. Enforced by
  `tests/architecture/test_tui_strings.py`.
- Styling goes in `cuanta.tcss`, not inline.
- The TUI talks to use cases through `tui/services.py`; tests swap in fakes
  (`tests/tui/fakes.py`) [UNVERIFIED: fakes path].
- `ARG002` and `RUF012` are relaxed here (Textual handler signatures, class-level bindings).

## Boundaries
- May import `tui`, `application`, `domain`, `bootstrap` only — never `adapters` or `ports`
  directly (`tests/architecture/test_layers.py`).

## Local commands
- `uv run pytest tests/tui -q`
- Intended visual change: `uv run pytest tests/tui --snapshot-update`, then review the SVGs.

## Local gotchas
- Pilot tests race view mounting: wait for the target screen/view before asserting, never
  assert immediately after navigation [UNVERIFIED: from session history].
- `app.notify` parses markup, so `[detect]` and other bracketed text vanish from toasts; pass
  `markup=False` for error text and hints, and test the text with `Toast(note).render().plain`.
