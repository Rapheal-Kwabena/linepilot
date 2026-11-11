# LINEPILOT design system

LINEPILOT uses square geometry, dense but readable data views, and restrained color. All interface colors are semantic CSS variables in `apps/web/app/globals.css`. Components should consume those variables instead of embedding arbitrary hexadecimal values.

## Geometry and spacing

- Border radius is **0 px** throughout the application, including dialogs, controls, charts, tooltips, and status marks.
- Borders are 1 px and carry the visual hierarchy. Large floating shadows and translucent panels are excluded.
- The desktop sidebar is 248 px expanded or 68 px collapsed. The top toolbar is 64 px tall.
- Layout follows an 8 px grid with 4 px increments for dense controls. Standard content gutters are 32 px on desktop.
- Interaction targets remain usable at small viewport sizes; complex timelines scroll horizontally instead of being compressed into illegibility.

## Color

| Role | Light | Dark |
| --- | --- | --- |
| Canvas | `#F4F4F0` | `#11161B` |
| Panel | `#FFFFFF` | `#1A2026` |
| Secondary panel | `#F8F9F7` | `#202830` |
| Sidebar | `#171C22` | `#0D1115` |
| Primary text | `#171C22` | `#E9ECEE` |
| Secondary text | `#626B73` | `#A1ABB4` |
| Muted text | `#8A939C` | `#74808B` |
| Border | `#D7DBDE` | `#343E47` |
| Action | `#2155CD` | `#6A91ED` |
| Action hover | `#1942A2` | `#88A7F2` |
| Bottleneck / exception | `#E77735` | `#F19A60` |
| Success | `#247C64` | `#57B69A` |
| Critical | `#BA4141` | `#E07777` |
| Caution | `#BC842E` | `#E0AD59` |

Blue identifies actions and selection. Orange draws attention to operational exceptions. A label or symbol always accompanies state color.

## Typography

- **Barlow Condensed** (500, 600, 700): page and section headings, primary numeric displays.
- **IBM Plex Sans** (400, 500, 600): navigation, forms, tables, general text.
- **IBM Plex Mono** (400, 500, 600): identifiers, times, durations, metrics, CSV previews, event logs. Use tabular numerals.

The desktop page title is approximately 42 px, main numerical highlights 36 px, section headings 25 px, and normal interface text 13–14 px. Small metadata is at least 11 px.

## Interaction

- Controls show hover, focus, disabled, error, and working states where relevant.
- Keyboard focus uses a visible outline. Dialogs and drawers can be dismissed with Escape.
- Timeline colors and patterns are paired with text and tooltips. Charts have textual summaries.
- Short transitions communicate state changes; `prefers-reduced-motion` removes nonessential animation.
- Stored theme preference applies to controls, overlays, and charts on refresh.
