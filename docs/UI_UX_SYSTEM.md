# Market Monitor UI/UX System

_Current as of the protected Preview UI overhaul (2026-08-15). This describes the
implemented interface, not a future design concept._

## 1. Product principles

Market Monitor is a daily market-intelligence tool. The interface optimizes for fast,
truthful decisions rather than decoration:

1. Search, Exports, and Competitor Sync receive the strongest hierarchy and the shortest
   navigation path.
2. Current trustworthy evidence is visually primary; degraded evidence remains visible
   and is never made to look current.
3. Dense comparison belongs in structured rows and tables, not a wall of oversized cards.
4. Provenance and lifecycle status use progressive disclosure. The primary view answers
   the business question; technical run IDs remain available in details.
5. Motion confirms state changes and never pretends that unknown backend progress exists.

## 2. Foundations

The shared tokens are implemented in `frontend/src/styles/system.css`.

### Color

- `--bg` / `--bg-elevated`: graphite-black application foundation.
- `--surface`, `--surface-raised`, `--surface-subtle`: the three supported content
  elevations. Do not add another surface for every metadata value.
- `--accent`: calm blue for navigation, focus, and primary actions.
- `--success`: trustworthy/complete.
- `--warning`: partial, suspicious, or degraded—but still potentially usable.
- `--danger`: failed or destructive.
- Muted text has two levels: `--muted` and `--muted-strong`.

Status must include text or an icon; color alone is never the only signal. Purple/neon,
rainbow badges, and decorative gradients are outside this system.

### Typography

The stack is bundled/system-native: Inter when locally available, then the Apple/Segoe UI
system stack. No remote font request is required.

- Page titles: 25–34px, one strong weight, tight tracking.
- Major metrics: 19–27px with tabular numerals where relevant.
- Section headings: 17–20px.
- Product/competitor titles: 11–14px depending on density.
- Body: 11–14px.
- Metadata: 8–11px, never used for critical warning copy.
- Eyebrows are uppercase, 9–10px, and reserved for page/section orientation.

### Spacing, borders, and elevation

The spacing scale is 4, 8, 12, 16, 20, 24, and 32px. Primary pages use 36–48px desktop
page gutters, 20px tablet gutters, and 14px mobile gutters.

Supported radii are 7px, 11px, and 16px. A panel may contain rows, but rows should not
contain another stack of independent rounded cards. Shadows are reserved for the shell,
major workflow panels, menus, and autocomplete.

## 3. Application shell

`components/layout/Sidebar.tsx` owns navigation and the page header.

- Desktop uses a 236px compact sidebar grouped into **Market** and **Workspace**.
- The three daily workflows are always first: Search, Competitors, Exports.
- Mobile uses a fixed product header, a four-item daily-workflow tab bar, and a drawer for
  secondary destinations. Page CSS must never hide or resize the shell directly.
- The protected Preview environment is identified in the shell without making fixture
  data look like a toy demo.
- Main content always has `min-width: 0`; horizontal application overflow is a defect.

## 4. Reusable primitives

`components/ui/index.tsx` owns `Button`, `Card`, fields, tables, modal, stock indicator,
and loading/error/empty states. `PageHeader` belongs to the shell.

- Use `Button` variants for primary, secondary, ghost, and destructive actions.
- Use a semantic status indicator with a text label for lifecycle/completeness.
- Use the shared field label/input/select treatments. Do not inline a new focus style.
- Use skeletons for page/row loading and a button-local spinner for actions.
- Empty states give a useful next action; errors state what remained unchanged when that
  distinction matters.

Create a new primitive only when it has multiple real consumers or centralizes a domain
semantic. Do not build one-off abstraction wrappers around static copy.

## 5. Status language

| Meaning | Tone | Examples |
|---|---|---|
| Trustworthy/terminal complete | success | reliable price, complete catalog |
| Active/accepted | info | queued, running, durable request accepted |
| Usable with caveat | warning | partial, suspicious empty, stale evidence |
| Failed/destructive | danger | acquisition failure, delete action |
| Unknown/legacy/disabled | muted | no V2 lineage, inactive source |

Queued is never styled or worded as completed. Partial is not a failure. Suspicious empty
must explain that stored data was not replaced. Cached data is identified as stored, not
fresh.

## 6. Workflow composition

### Search

The search field is the dominant initial action without a marketing-sized hero. Results
follow: matched product → trustworthy currency range → current/trustworthy sellers → older
or degraded evidence → technical details. Reliable low and observed low remain separate.
Autocomplete preserves combobox semantics, keyboard navigation, 250ms debounce, stable
loading geometry, and visible focus.

Repeated degraded evidence uses compact default labels such as `Legacy data · observed
12d ago` and `Sync failed · stored price 3d ago`. The complete backend-owned warning is
preserved in Evidence details. At desktop widths the seller identity and listing evidence
share one compact comparison row; mobile keeps the stacked composition.

### Exports

Exports reads as four steps: choose source → set collection → verify outcome → download.
Live and stored source consequences are visible before preparation. The outcome panel has
distinct complete, partial, suspicious-empty, failed, loading, and stored compositions;
they are not one panel with only a badge-color change.

### Competitor Sync

The page opens with one compact command/readiness bar. Desktop uses a seven-column
operations table for competitor, status, last success, products, coverage, note, and
action. Technical lifecycle IDs, timestamps, attempts, acquisition evidence, and full
reasons expand one row at a time. Configuration and delete actions live behind the row
action menu. Queued work distinguishes awaiting dispatch, waiting for a runner, and
dispatch recovery; the recovery action retries the same durable request. No fake
percentage progress is shown. Mobile uses compact two-column rows and collapsible details
rather than squeezing the desktop table.

## 7. Responsive strategy

Required review widths are 1440×900, 1280×800, 1024×768, 768×1024, and 390×844.

- Desktop prioritizes aligned numbers and dense rows.
- Tablet removes the sidebar and keeps workflow panels in one or two columns as space
  permits.
- Mobile stacks hierarchy, not merely columns. Dense Sync rows become structured cards;
  Search metrics use a compact two-column grid; Export steps become two rows.
- Fixed mobile navigation receives bottom content clearance.
- Menus, suggestions, fields, and long competitor/product names must stay inside the
  viewport. Validate `documentElement.scrollWidth === clientWidth`.

## 8. Motion and accessibility

Transitions are 120–180ms for hover, focus, drawer, and pressed states. Loading shimmer
and spinner animations are short and contextual. `prefers-reduced-motion` reduces all
motion to effectively zero.

Preserve landmarks, real labels, native fieldsets/radios, semantic headings, tab order,
combobox/listbox roles, target descriptions for external links, and visible focus. Do not
replace semantic controls with clickable `div` elements.

## 9. Performance discipline

Routes are lazy-loaded in `App.tsx`; chart-heavy and secondary pages must not inflate the
daily workflow entry chunk. Prefer CSS/native interaction to animation or component-suite
dependencies. Any new runtime UI dependency requires measured bundle evidence and a reason
the existing React, CSS, and Lucide stack cannot satisfy the need.

## 10. Change rule

Substantial new UI or redesign work must use these tokens, shell behavior, status language,
and responsive checks. If a product requirement conflicts with this system, update this
document in the same change so future work has one current authority.
