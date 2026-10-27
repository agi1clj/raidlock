# Design tokens

The web UI has no build step and no CSS framework. Every page uses the tokens and components below, defined in [`src/raidlock/web/static/app.css`](../../src/raidlock/web/static/app.css). Reuse them; add a token only when no existing one fits, and use it everywhere it applies.

## Color

| Token | Value | Use |
|---|---|---|
| `--bg1`, `--bg2`, `--page` | `#0b0a1a`, `#1b1338`, purple gradient | page background |
| `--ink` | `#f3e9d2` | body text |
| `--muted` | `#b9a98a` | secondary text, subtitles |
| `--accent` | `#f5c451` | gold: brand, active tab, primary action, focus |
| `--accent2` | `#c9902b` | darker gold for gradients and borders |
| `--on-accent` | `#0b0a1a` | text on gold |
| `--panel`, `--panel-solid`, `--card1`, `--card2` | translucent purples | panels and cards |
| `--edge` | `#5b4a86` | borders |
| `--btn`, `--pill`, `--pill-edge` | dark purples | buttons and chips |
| `--toast`, `--sticky` | near-opaque purples | toasts, menus, sticky bars |

Card lanes: `--todo` `#7aa2ff`, `--doing` `#ff9f43`, `--review` `#c08bff`, `--blocked` `#ff5d73`, `--done` `#4ee39a`. Agent states: `--s-working`, `--s-quiet`, `--s-review`, `--s-blocked`, `--s-stalled` `#ffb020`, `--s-idle`. Results: `--ok` / `--ok-edge` and `--no` / `--no-edge`. Never hard-code a lane or state color; use the token.

## Type

- Body: `--font`, the system sans-serif.
- Titles, tabs, levels: `--font-display`, Cinzel 600 and 700, bundled in `static/fonts` under the SIL OFL 1.1.
- Scale: 12 px (meta, chips), 14 px (default), 15–16 px (emphasis, tabs), 18 px (section titles), 22 px (page title). Nothing smaller than 12 px.

## Components

| Class or element | What it is |
|---|---|
| `.tog` | the standard button and link button |
| `details.menu` with `summary.tog` and `.menu-body` | header dropdowns: Sound, Settings, Update, the notification bell |
| `details.block.fold` | a collapsible section with `h2` and `.sub` |
| `.rows` / `.row` | list rows inside sections |
| `#wsbar`, `#realmbar` | the sticky page header with title, status line and tabs |
| `#viewTabs`, `#realmTabs` | underlined page tabs |
| `.badge`, `.lane-h .count` | counts |
| `toast()`, `copyCommand()` in `app.js` | transient messages and copy-to-clipboard buttons |

## Motion and layout

Animations run only in the raid theme with motion allowed: check `motion()` in JavaScript, and guard CSS with `prefers-reduced-motion` and `html[data-anim=off]`. Layouts must work at 390 px wide with a 16 px gutter and no horizontal page scroll. Each element appears in exactly one place; the realm and guild pages share the same header, tabs and components.
