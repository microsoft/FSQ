# FSQ Control Plane design

## Direction

A light, minimal developer workspace: neutral surfaces, graphite typography,
compact controls, and restrained indigo emphasis. Hierarchy comes from alignment,
spacing, and fine borders. The interface supports repeated test operation,
reading source, and comparing evidence. Every page belongs to the same workspace.

The module [SPEC](./SPEC.md) owns supported behavior and layout constraints.
This document provides component recipes within that contract.

## References

Original FSQ adaptation informed by two analyses in
[VoltAgent/awesome-design-md](https://github.com/VoltAgent/awesome-design-md),
at commit 8147538b4226ae41e2487a9179e3bcc1f68e8554:

- [Vercel](https://github.com/VoltAgent/awesome-design-md/blob/8147538b4226ae41e2487a9179e3bcc1f68e8554/design-md/vercel/DESIGN.md):
  neutral canvas, fine separators, clear typography, sparse decoration.
- [Linear](https://github.com/VoltAgent/awesome-design-md/blob/8147538b4226ae41e2487a9179e3bcc1f68e8554/design-md/linear.app/DESIGN.md):
  compact rhythm, restrained indigo, crisp navigation and state hierarchy.

The reference documents analyze marketing sites. FSQ uses their visual principles
at application density with local system fonts and its existing icons. No reference
document, proprietary typeface, logo, or third-party brand asset is bundled.

## Palette

| Role | Value | Use |
| --- | --- | --- |
| Canvas | #fafafa | Ordinary page backgrounds |
| Elevated / soft | #f6f6f8 | Sidebar, toolbars, code blocks |
| Surface | #ffffff | Content and controls |
| Ink | #18181b | Headings, values, body text |
| Secondary | #62626b | Descriptions and metadata |
| Divider | #e5e5e8 | Structural separators |
| Control border | #85858f | Editable fields and outlined actions |
| Accent | #5e6ad2 | Primary actions and active selection |
| Accent hover / focus | #4f5abd | Hover, keyboard focus |
| Accent surface | #eeeffb | Selected rows and active context |
| Success | #237348 / #eff8f2 | Ready, passed, completed |
| Warning | #8b5e13 / #fff8eb | Attention and unavailable prerequisites |
| Danger | #b82e38 / #fff1f2 | Errors and destructive actions |

Colors are centralized in src/styles/tokens.css. Status always includes a word or
icon. Primary buttons retain white text against the indigo background; secondary
actions use graphite text on white. Control borders and focus rings are stronger
than non-interactive panel dividers.

## Type and spacing

- System sans: -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif.
- Monospace: ui-monospace, SFMono-Regular, Consolas, monospace for paths,
  commands, source, and logs.
- Body 14px; metadata and status 12px; compact controls 12–13px;
  subsection headings 16px, section headings 18px, dialog titles 20px and page headings 24px.
- Body weight 400, controls 500, headings and important values 600.
- Use 4 / 8 / 12 / 16 / 20 / 24 / 28 / 32px spacing. Labels sit close to their
  fields; separate workflow regions with more space than rows inside a region.
- Controls use 6–8px corners and ordinary panels 10–12px. Small status chips may
  use 4–6px corners. Selected-Action screenshot edges and the full-bleed browser stay square.

## Component recipes

### Shell and navigation

Use a pale sidebar, bundled FSQ light SVG logo, aligned icons, and a white context
bar. A selected item has a soft indigo fill, darker indigo text, and a vertical
edge cue. Workspace names elide with safe platform metadata on the following
line. Order the primary pages Home, Test Runner, Runs, then the Workspaces group. Keep one Settings entry at the bottom, using the existing Provider page. Runs stays Coming soon. Workspaces only expands/collapses; it never navigates or clears state. Current Workspace uses a checkmark and accessible description, while only the current page uses page-current styling. The shell owns the mobile drawer.

### Home (overview feature)

The start page has a centered 1120px maximum content area. Before selection, show
one Workspace chooser/create region. After selection, show a compact identity bar
with Change Workspace and Configure Workspace; reveal the chooser on demand.
Place two equally weighted task regions side by side: Record new case from a goal,
and Browse Cases for existing YAML. Buttons stay content-sized. Keep the safe global
Provider summary in a compact lower region. Stack these regions on narrow screens.
No fixed three-step onboarding list or fabricated Case/Run history is shown.

### Workspace

One identity area shows name, full path, platform labels and visible Record new
case. Files and Configuration share the same content tabs directly underneath.
Files uses a 260px desktop tree with 32px rows, shared Lucide file/folder icons,
and independent content scrolling. One file header contains the breadcrumb,
readonly metadata and eligible Replay Case action together. Metadata does not
vanish for Cases. Markdown has Preview/Code tabs; YAML has its source viewer
without extra Code or YAML toolbars. Configuration shares the Workspace identity,
then uses platform tabs and the existing private-detail edit workflow.

### Test Runner (devices feature)

Preparation uses a wide composer beside environment checks. Workspace/platform/
target selection sits in the page, not in the global title bar. Explore/Strict
Replay use descriptive radio-mode cards. Start and its first blocking explanation
sit together at the composer edge. Unselected conditions are neutral Not checked,
not red failures. There is no empty live-evidence workbench before a run exists.

During execution and terminal review, selectors collapse into a run context bar.
It displays the frozen Workspace/platform/target, backend status and available
Cancel or Save yaml/New run actions. The activity/Case-step column sits beside a
larger Screen/UI Tree/Logs panel, with independent desktop scrolling. Only Screen
uses the grid. Original selected screenshot edges, replay sizing, logs, source
preservation and Action selection behavior remain unchanged.

### Settings (config feature) and dialogs

Use the same type hierarchy and field treatments as Workspace. Provider details
are aligned label/value rows. Dialogs have an opaque white surface, modest shadow,
clear heading, body, and action group. Secrets remain masked by default, and
visibility controls remain native, named, and keyboard operable.

## Interaction and responsive behavior

Hover changes color or surface over 120ms, without moving or resizing controls.
Keyboard focus has a visible 2px indigo outline. Reduced motion disables transitions
and spinner animation. Loading and errors preserve input context and explain the
available next action. Busy and disabled controls retain recognizable geometry.

Desktop panels contain long content and scrolling. The existing shell drawer
breakpoint and Workspace tree stacking at 820px remain authoritative. Narrow forms
stack fields and action groups; wrapped status labels never collide with controls.
Review desktop and narrow screenshots, keyboard navigation, long content, and the
browser console whenever shared styles change.


## Shared controls

- Standard buttons/inputs/selects/picker triggers: 40px minimum. Compact toolbar
  actions: 32px. Use the same tier within one action row and 44px effective coarse
  pointer targets. Busy labels keep a stable action area.
- Button variants: primary, secondary, quiet and destructive. One group emphasizes
  its principal action. Status chips have no button hover affordance.
- ContentTabs owns roving keyboard focus and underline selection; feature owners
  retain selected state, eligibility and transitions. Reuse it for Workspace,
  platform, Markdown and evidence views.
- Fields keep visible labels, optional/required guidance and local errors. Native
  selects retain platform/target semantics; Case choices keep their directory tree.
- Disabled Start always has a nearby reason. Retry never starts execution. Secret
  controls keep show/hide names, masking and cleanup. Focus returns to a connected
  initiating control after cancellation.

## Consistency details

Typography roles and line heights come from tokens.css: page 24/32, dialog 20/28, section 18/26, subsection 16/24, body 14/21, label 13/20 and metadata 12/18. Weights are 400 for values, 500 for controls and 600 for headings. Browser-default heading and legend margins are reset.

The supplied FSQ light SVG is bundled locally without altering its artwork. Workspace platform indicators have their own wrapped row below identity; Record new case occupies a separate action column. Target headings are subordinate to Platform configuration. Full revision identifiers live inside Configuration details rather than dominating the summary.

Create, add, edit and Provider fields share control geometry and 6px label gaps, 16px field gaps and 24px section spacing. Add/edit stay left-aligned with Configuration content at a 640px maximum measure. Target sections stack vertically. Environment rows retain masked values and aligned visibility/delete controls. Dialogs use 12px corners and 24px padding (16px on narrow screens) with bounded scrolling.
