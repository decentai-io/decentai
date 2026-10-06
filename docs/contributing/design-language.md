# The design language

What the interface is allowed to look like, and where each part of that
is named.

Every screen in this platform is assembled from the same few decisions:
which colours exist, which faces set type, which icons are drawn, how
sharp a corner is, and what separates one surface from the next. Each of
those could have been decided again in each component, and that is the
version where the Schedules page's status chips are indigo because indigo
was on hand the day they were written, the chat's code blocks are set in
a face nothing else uses, and a card has a ten-pixel corner beside a
panel with an eight. None of those is a bug. Together they are the
reason an interface feels assembled rather than designed, and a user who
cannot say why still feels it.

So the platform names every one of those decisions exactly once, as a
CSS custom property, and component CSS spends the token rather than the
value. This document says what the tokens are, where they live, and
which rules they exist to keep.

The mark, the lockups and their clear space are a separate document:
`brand/brand.md`. It governs the identity; this note governs the
product.

---

## Where the tokens live

Three layers, each with a different job. A component reaches for the
innermost one that already says what it means.

**`frontend/src/tailwind.css` — the palette and the scales.** A `:root`
block holds the light values and a `.dark` block the dark ones, under
the shadcn names (`--background`, `--foreground`, `--card`, `--primary`,
`--muted-foreground`, `--border`, `--destructive`, and the rest). Below
them an `@theme inline` block exposes the radius and font scales
(`--radius-*`, `--font-sans`, `--font-serif`, `--font-mono`) as ordinary
custom properties, which is why component CSS can spend them directly.
This file is the source of truth. A colour that is not defined here does
not exist — with one exception, said here so that it is not found by
surprise: `styles.scss` sets `--foreground` and `--muted-foreground`
again for the signed-in app, in both themes, and defines `--scrim`.

**`frontend/src/styles.scss` — the bridge and the global shapes.** It
aliases the older Material-era token names (`--p`, `--surf`, `--s1`…) to
the shadcn ones so unmigrated components re-theme for free; it defines
the chat family (`--chat-*`) and the ported pages' radii
(`--ui-radius-*`) as *aliases* of the scales above rather than as
second literals, and their text sizes (`--ui-text-*`) as sizes of
their own; and it
carries the Angular Material M3 theme and the markdown typography that
have nowhere else to go. New code should not add to the bridge.

**`data-shared.css`, `iam-shared.css`, `agent-shared.css` — the page
families.** Each is listed in the `styleUrls` of the components that
belong to it (31, 20 and 7 of them). `data-shared.css` opens with a
`:host` block that gives the families their semantic names — `--ink`,
`--muted`, `--surface`, `--accent-strong` — in terms of the tokens
above; the other two are listed after it and use its names. Those names
exist on a page of a family and nowhere else: the sidebar and the chat
spend the tokens directly. A page in
a family inherits its panels, buttons, lists and empty states from the
shared file and adds only what is genuinely its own.

---

## The rules

Five, and they are load-bearing. A change that breaks one of them
changes this note first.

**Three colours, and the third is an error.** Emerald `#10b981` is the
single identity colour. Everything else is a grey: the neutrals are
the zinc scale, a grey with the faintest blue in it, and no other.
Red is reserved for errors and appears nowhere else. There is no indigo,
no amber, no slate, and no fourth hue introduced to mean "in progress"
or "warning" — `--warning` is deliberately a neutral, because an
interface that spends a colour on attention has one fewer colour left
for identity. Where a state genuinely needs to separate from its
neighbours without a hue, draw a border: the Schedules page's
waiting-on-a-person chip is ruled rather than recoloured, and that is
the pattern to copy.

Emerald at full strength is not a button. `--primary` is 2.5:1 against
white, so a fill that carries `--primary-foreground` uses `--primary-fill`
instead; dots, borders, focus rings and icons stay on `--primary`.

**Three faces.** `--font-serif` (IBM Plex Serif) sets headlines and
display numbers, `--font-sans` (IBM Plex Sans, with Sans Arabic for RTL)
sets everything else, `--font-mono` (IBM Plex Mono) sets labels, data,
identifiers, SQL and code. A hand-rolled stack in a component is always
one of these three spelled differently, and spelling it differently is
how a component ends up rendering in a face the project never loaded.

**One set of icons.** Lucide, through `lucide-angular`, registered
explicitly in `AppModule`'s `LucideAngularModule.pick({…})`. An icon not
in that list does not render, which is the point: the list is the
inventory. Icons are SVG, so they size by `width`/`height` and the
`[size]` input, never by `font-size`. `MatIconModule` is imported
nowhere, and no template should add a `<mat-icon>`.

**One radius scale.** Six steps and nothing between them:

| token | value | for |
|---|---|---|
| `--radius-sm` | 4px | inner elements, small chips |
| `--radius-md` | 6px | dense controls, inline code |
| `--radius-lg` | 8px | cards and controls — the default |
| `--radius-xl` | 12px | panels |
| `--radius-2xl` | 16px | large surfaces: chat bubbles, the composer, hero media |
| `--radius-pill` | 999px | pills, chips, anything fully round |

`--ui-radius-sm`, `--ui-radius-card`, `--ui-radius-pill` and
`--chat-radius` are aliases of these; `50%` for a circle is not a radius
decision and needs no token. A value that is not on the scale is drift,
not nuance.

**Borders define edges.** `--shadow-card` is `none` and is meant to
stay that way: a card, a bubble, a button, a chip or a hovered row is
separated from what is under it by a one-pixel border, and a hover is
felt by the border changing colour, not by the element lifting. The one
exception is a surface that genuinely floats over the entire page — a
modal, the mobile drawer, a Material menu or date-picker panel — which
takes `--shadow-overlay`. That token exists so the exception is countable
and revocable: set it to `none` and the rule becomes absolute. Focus is
its own thing: `--chat-focus-ring`, or for a control the keyboard
reaches a two-pixel outline in `--ring`, and no other ring.

---

## Both themes, always

Dark mode is `.dark` on the root element, set by
`frontend/src/app/services/theme.service.ts`. Nothing else participates:
there is no `prefers-color-scheme` in component CSS, and a component
does not need a dark block of its own if it spends tokens, because every
token already has a dark value.

This is why a colour literal in component CSS is not a small sin. A
literal cannot follow the theme, so the component that carries one is
correct in exactly one mode and wrong in the other — and it fails
silently, because nothing errors and the page still renders.

The same is true, less obviously, of a token that does not exist.
`var(--muted-text, #6b7280)` looks theme-aware and is not: if nothing
defines `--muted-text`, the fallback wins every time, in both modes,
forever. Write `var(--muted-foreground)` and let it fail loudly if it is
wrong. When a component must be written against a design-system name the
platform does not use, alias that name in the family's `:host` block —
`data-shared.css` does exactly this for `--border-color` and
`--text-muted` — rather than leaving a literal behind as a fallback.

Two smaller consequences worth knowing. A wash over the ink tile — the
assistant's code block, which inverts with the theme — must be mixed
from `--chat-user-ink`, because a fixed black wash disappears on it in
one mode. And
a shadow, where one is permitted, is tinted from `--foreground` in
light and is black in dark, not a blue-grey that reads as a fourth hue
at low alpha.

---

## No private palettes

Nothing in the repository may open a palette of its own. If a surface
needs a colour the tokens do not have, the answer is a token, added to
`tailwind.css` in both blocks.

---

## Checking

Each rule is a grep, and each was written because the grep found
something. Run them from `frontend/src`:

```bash
# a colour literal in a component's declaration
grep -rn --include="*.css" -E "^[^/*]*:[^;]*(#[0-9a-fA-F]{3,8}\b|rgba?\([0-9])" app \
  | grep -vE "color-mix|mask-image"

# a fallback literal behind a token: if the token is ever undefined, the
# literal wins silently, in both themes, forever
grep -rn --include="*.css" -oE "var\(--[a-z-]+,\s*(#|rgb)" app

# a font stack spelled by hand instead of taken from the three
grep -rh --include="*.css" -oiE "font-family:[^;}]*" app \
  | grep -vE "var\(--|inherit"

# the other icon set, in templates or in rules left behind by a migration
grep -rn "mat-icon" --include="*.html" --include="*.css" app \
  | grep -v "mat-icon-button"

# a corner that is not on the scale
grep -rn --include="*.css" -E "border-radius:[^;]*[0-9]+px" app

# an edge drawn with a shadow rather than a border
grep -rn --include="*.css" -oE "box-shadow:[^;]*;" app | grep -viE "none|var\("
```

All six return nothing, and that is the standard. Three exemptions are
built into the greps above and are the only ones:

- **`color-mix()` operands.** `color-mix(… var(--primary-fill) 88%, #000)`
  names a direction to mix in, not a colour to paint; so does the `#000`
  that makes a `mask-image` opaque.
- **Comment prose.** Several files explain in a comment which literal
  they used to carry. The first grep requires the literal to sit in a
  declaration — after a property's colon — which leaves prose alone
  without needing to parse comments.
- **`styles.scss` and `tailwind.css`** are where the values legitimately
  live, and are not under `app/`.

`mat-icon-button` is excluded from the fourth grep on purpose: it is
Angular Material's *button* directive, not an icon, and a Lucide icon
sits inside it perfectly well.
