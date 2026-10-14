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
value. The controls are named once too: a button is the one button, a
menu the one panel. This document says what the tokens and the controls
are, where they live, and which rules they exist to keep.

The mark, the lockups and their clear space are a separate document:
`brand/brand.md`. It governs the identity; this note governs the
product. The two do not share a palette: the identity's accent
(`#0E7C5B`) is for the mark's dot alone, and the product's emerald is
the token below.

---

## Where the tokens live

Five places, each with a different job. A component reaches for the
one that already says what it means, and adds only what is its own.

**`frontend/src/tailwind.css` — the tokens.** A `:root` block holds the
light values and a `.dark` block the dark ones. It opens with the
neutral scale (`--n-0` to `--n-950`) and names colour by meaning from
it: `--bg`, `--surface`, `--surface-2`, `--surface-3`, `--line`,
`--line-strong`, `--ink`, `--ink-2`, `--ink-3`, `--chrome`, `--scrim`;
then the accent, danger and warning families, the shadows, the chart
series, the type scale, the radii, the durations and easings, and the
stacking order (`--z-*`). An `@theme inline` block at the end gives the
three faces (`--font-sans`, `--font-serif`, `--font-mono`). This file is
the source of truth. A colour that is not defined here does not exist.

Under the heading "Names from before the redesign" the same file keeps
the older names — `--background`, `--foreground`, `--primary`,
`--muted-foreground`, `--border`, `--radius-*`, `--ui-radius-*`,
`--ui-text-*` and the rest — as aliases of the tokens above, so a
component not yet redone keeps working. They are on their way out: new
code spends the new names, and a line goes when its last use does.

**`frontend/src/styles.scss` — the global sheet.** It loads the faces
from Google Fonts, pins Angular Material's system colours to the tokens
so its menus, dialogs and tooltips are our surfaces, and holds the base
rules (the body's type, one serif `h1`, the focus outline, links,
scrollbars), the one set of keyframes (`arrive`, `spin`, `pulse`,
`shimmer`, `blink`) and the rule that stops motion for a person who
asked for less.

**`frontend/src/styles/controls.scss` — the controls.** One of each,
used on every page, as classes a template puts on its own element:
`btn` (with `primary`, `ghost`, `danger`, `danger primary` and the
32px `sm`), `icon-btn`, `input` and `search-field`, the form's pieces
(`form`, `form-section`, `form-pair`, `number-field`, `form-notice`),
`choice-cards`, `secret-field`, `chip` (with `accent`, `live`, `warn`,
`danger`), the skeleton `sk`, the side column (`side-layout`,
`side-item`), a list's toolbar (`toolbar-*`), `table`, and the pieces of
a dialog and of a list of rows to tick. Material's menu, dialog,
tooltip, checkbox and paginator are given their look here once. A
page's stylesheet places these controls; it does not restyle them.

**`frontend/src/styles/markdown.scss` — markdown.** Every `<markdown>`
element, wherever it is — an answer in a chat, a skill, a chapter of
the Guide — is styled here and nowhere else. A page gives it a place
and a width, not a look.

**`frontend/src/app/components/ui/` — the shared pieces, and the page
families.** The parts every page is built from are components here:
the page frame (`app-page`: one title, one sentence, the page), the
list frame (`app-list`), a row, a field, a select, a switch, a
segmented control, the pager, the save bar, the selection bar, the
empty state and the confirm dialog. Two families of pages have a little
more in common than that, and keep it in a file listed in the
`styleUrls` of the components that belong to them:
`frontend/src/app/pages/app/agents/agent-shared.css` for the agents'
pages and `frontend/src/app/pages/admin/admin-shared.css` for the admin
console. Both spend the tokens directly; neither defines names of its
own.

---

## The rules

Five, and they are load-bearing. A change that breaks one of them
changes this note first.

**One colour, and two that warn.** Everything is the neutral scale — a
warm grey, paper and ink — except three hues, each kept to one meaning.
Emerald is meaning only: live, on, chosen, the primary action. Red is
danger, and appears nowhere else. Amber is "needs you": a chip that
waits on the person, a credential not yet settled. There is no indigo,
no slate, no gradient, and no fourth hue introduced to mean "in
progress".

Each hue comes in the same few forms, and a component takes the form
for the job: `--accent` is a fill, with `--accent-fg` on it (white on
emerald is 5.5:1 in the light theme); `--accent-ink` is emerald text and
icons; `--accent-soft` is a chip's background and `--accent-wash` what
is pointed at or a row that is marked. Danger has `--danger`,
`--danger-fg`, `--danger-ink` and `--danger-soft`; amber has `--warn`,
`--warn-ink` and `--warn-soft`. A chart takes the emerald for its
focal series and graded neutrals for the rest (`--chart-1` to
`--chart-5`).

**Three faces.** `--font-sans` (IBM Plex Sans, with IBM Plex Sans
Arabic for Arabic) sets everything. `--font-serif` (IBM Plex Serif) sets
titles — a page's one `h1`, the greeting, an empty chat's title, the
Guide's cover — and the brand word; never a control or text that is
read. `--font-mono` (IBM Plex Mono) sets code and what is
written for a machine to read: a cron, JSON. Three weights: 400, 500
for labels and buttons, 600 for titles. Size and line come in pairs
from one scale of eight (`--t-xs`/`--lh-xs` to `--t-xl`/`--lh-xl`, with
`--t-read` for text that is read and `--t-code` for code blocks). A
hand-rolled stack in a component is always one of the three faces
spelled differently, and spelling it differently is how a component
ends up rendering in a face the project never loaded.

**One set of icons.** Lucide, through `lucide-angular`, registered
explicitly in `AppModule`'s `LucideAngularModule.pick({…})`. An icon not
in that list does not render, which is the point: the list is the
inventory. Icons are SVG, so they size by `width`/`height` and the
`[size]` input, never by `font-size`. `MatIconModule` is imported
nowhere, and no template should add a `<mat-icon>`.

**One radius scale.** Four steps and nothing between them:

| token | value | for |
|---|---|---|
| `--r-sm` | 6px | small elements: the code toolbar's buttons, a corner inside a control |
| `--r-md` | 10px | buttons, inputs, menu items, cards — the default |
| `--r-lg` | 16px | large surfaces: the composer, a dialog, the live view |
| `--r-full` | 999px | chips, pills, anything fully round |

`--radius-*` and `--ui-radius-*` are aliases of these; `50%` for a
circle is not a radius decision and needs no token. A value that is not
on the scale is drift, not nuance.

**A hairline first, and a shadow only by name.** What separates one
surface from the next is a one-pixel `--line`, and a step between
surfaces (`--surface`, `--surface-2`, `--surface-3`). Three shadows
exist, each for one kind of thing: `--shadow-1` for what rests on the
page and can be pressed or scanned (a button, a list's frame);
`--shadow-2` for a card, which lifts to it when it is pointed at;
`--shadow-float` for what floats over the page (a menu, a dialog, a
picker, the save bar). A shadow written out in a component is a fourth
one. Focus is its own thing: `--focus`, a three-pixel ring in the
accent, drawn as a shadow by the controls and as an outline by
`styles.scss` for everything else the keyboard reaches; no other ring.

---

## Both themes, always

Dark mode is `.dark` on the root element. `frontend/src/theme-init.js`
sets it before the first paint, and
`frontend/src/app/services/theme.service.ts` keeps it from then on: a
person's choice of light or dark, or the system's preference where they
have made none. Nothing else participates: there is no
`prefers-color-scheme` in component CSS, and a component does not need
a dark block of its own if it spends tokens, because every token already
has a dark value.

This is why a colour literal in component CSS is not a small sin. A
literal cannot follow the theme, so the component that carries one is
correct in exactly one mode and wrong in the other — and it fails
silently, because nothing errors and the page still renders.

The same is true, less obviously, of a token that does not exist.
`var(--muted-text, #6b7280)` looks theme-aware and is not: if nothing
defines `--muted-text`, the fallback wins every time, in both modes,
forever. Write `var(--ink-2)` and let it fail loudly if it is wrong.

Two smaller consequences worth knowing. A wash is mixed from a token
with `transparent` (`--accent-soft` is the accent at 10% in light and
14% in dark), never a fixed tint, so it reads on either paper. And a
shadow is tinted from the ink of the light theme (`rgb(23 22 20 / …)`)
and is black in dark, not a blue-grey that reads as a fourth hue at low
alpha.

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

- **`color-mix()` operands.** `color-mix(… var(--accent) 88%, var(--ink))`
  names a direction to mix in, not a colour to paint; so does the `#000`
  that makes a `mask-image` opaque.
- **Comment prose.** Several files explain in a comment which literal
  they used to carry. The first grep requires the literal to sit in a
  declaration — after a property's colon — which leaves prose alone
  without needing to parse comments.
- **`tailwind.css`, `styles.scss` and `styles/`** are where the values
  legitimately live, and are not under `app/`.

`mat-icon-button` is excluded from the fourth grep on purpose: it is
Angular Material's *button* directive, not an icon, and a Lucide icon
sits inside it perfectly well.
