# Brand

The raidlock mark is a padlock whose shackle is three linked nodes: a team holding one lock. Everything here is original, hand-written SVG. Keep it that way: no game art, no borrowed icons, no fantasy styling in the brand files. The raid theme in the web UI is a theme, not the brand.

## Files

| File | Use |
|---|---|
| `raidlock-mark.svg` | Mark in the primary colour, for light backgrounds |
| `raidlock-mark-dark.svg` | Mark in the dark-theme primary, for dark backgrounds |
| `raidlock-mark-mono.svg` | One-colour mark that takes `currentColor` (black when used as an image) |
| `raidlock-wordmark.svg`, `raidlock-wordmark-dark.svg` | "raidlock" as outlined paths, ink colour for light and dark backgrounds |
| `raidlock-lockup.svg`, `raidlock-lockup-dark.svg` | Mark and wordmark side by side, for light and dark backgrounds |
| `social-preview.svg`, `social-preview.png` | 1280 x 640 repository social preview |
| `../../../src/raidlock/web/static/icon.svg`, `icon-32.png` | Favicon: white mark on a primary tile, with a 32 px PNG fallback |

The mark is drawn on a 64-unit grid so it lands on whole pixels at 16 and 32 px. It has no strokes, only filled shapes, so it stays sharp when small.

## Wordmark

The wordmark is lowercase "raidlock" in [Inter](https://rsms.me/inter/) SemiBold (weight 600, optical size 32), with the font's kerning, converted to paths. Inter is under the SIL Open Font License, which allows this. The UI itself keeps the system font stack (`system-ui, -apple-system, "Segoe UI", Roboto, sans-serif`). The wordmark uses Inter instead because the outlines of system fonts such as San Francisco or Segoe UI cannot be redistributed, and a logo needs fixed paths. No SVG here contains `<text>`, so every file renders the same with or without fonts installed. The social preview tagline is Inter Regular, outlined the same way.

## Palette

One primary plus four supporting colours. Light values sit on white `#ffffff`, page `#f6f7f9` and pill `#f1f3f6`; dark values sit on page `#14161b`, panel `#1c1f26` and pill `#262a33`. Ratios are WCAG 2.x contrast ratios; AA for normal text needs 4.5:1.

| Token | Light | Contrast (white / page / pill) | Dark | Contrast (page / panel / pill) | Use |
|---|---|---|---|---|---|
| `--brand-primary` | `#2f5bd3` | 5.90 / 5.51 / 5.31 | `#7ea2ff` | 7.32 / 6.67 / 5.82 | Mark, links, focus, selected tab, primary buttons |
| `--brand-neutral` | `#5d6472` | 5.95 / 5.55 / 5.35 | `#a1a7b3` | 7.49 / 6.83 / 5.95 | Secondary text, borders that carry meaning |
| `--brand-success` | `#17784a` | 5.49 / 5.13 / 4.94 | `#5fd29a` | 9.63 / 8.77 / 7.64 | Done, working, passing verify |
| `--brand-warning` | `#8a5700` | 6.10 / 5.69 / 5.49 | `#f0b44c` | 9.77 / 8.90 / 7.76 | Stalled, approaching a limit |
| `--brand-danger` | `#c3283f` | 5.68 / 5.30 / 5.11 | `#f07a8c` | 6.78 / 6.18 / 5.38 | Blocked, refused, failing verify |

Text on a primary fill: white on `#2f5bd3` is 5.90:1; `#0d0f14` on `#7ea2ff` is 7.76:1. Ink is `#1d2129` on light and `#e6e8ec` on dark.

Rules:

- Use primary for one thing per view that matters most. Do not tint whole panels with it.
- Success, warning and danger mean state, never decoration. Always pair them with a word or icon, not colour alone.
- Use the light values on light backgrounds and the dark values on dark ones. Do not mix them.
- Do not recolour the mark outside these values. On photos or busy backgrounds use the mono mark.
- Leave clear space around the mark at least the diameter of one shackle node.

## In the web UI

`src/raidlock/web/static/app.css` defines the five `--brand-*` tokens in every theme block. The plain theme maps its accent, state and status colours to them, in light and in dark (`prefers-color-scheme`). The raid theme keeps its own gold accent and state colours and carries the dark brand values for anything brand-specific. The header shows the lockup as inline SVG: the mark takes the theme accent and the wordmark takes the ink colour, and the heading keeps the text "raidlock" for screen readers.

## Regenerating PNGs

```bash
rsvg-convert -w 1280 -h 640 docs/assets/brand/social-preview.svg -o docs/assets/brand/social-preview.png
rsvg-convert -w 32 -h 32 src/raidlock/web/static/icon.svg -o src/raidlock/web/static/icon-32.png
```
