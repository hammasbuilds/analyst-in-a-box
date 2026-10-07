# Motion notes

## What the research said

- Material Design 3 motion: https://m3.material.io/styles/motion/easing-and-duration/tokens-specs . The page did not
  render through the fetch tool, so the tokens below are the published M3 values as I know them, not read live today:
  standard easing `cubic-bezier(.2, 0, 0, 1)`, emphasized decelerate `cubic-bezier(.05, .7, .1, 1)`, short durations
  50 to 200 ms, medium 250 to 400 ms.
- Apple HIG motion: https://developer.apple.com/design/human-interface-guidelines/motion . Principle taken: motion
  must be purposeful, brief, optional, and never the only carrier of meaning.
- A web search for how Stripe/Linear-class dashboards animate returned only secondary summaries (for example
  https://tessl.io/registry/skills/github/MengTo/Skills/animation-systems and
  https://skills.cat/skills/dylantarre/design-system-skills/animation-principles). Their consistent numbers:
  hover and press 120 to 200 ms, state changes 180 to 260 ms, press scale about 0.97 to 0.98 and never below 0.95,
  ease-out on entry, no linear easing except spinners, nothing over 500 ms. These are not first-party Stripe, Linear,
  Mercury or Ramp specifications; treat them as house-style guidance.

## Decisions for Analyst-in-a-Box

| Token | Value |
| --- | --- |
| `--dur-fast` / `--dur-base` / `--dur-slow` | 120 / 200 / 320 ms |
| `--ease` | `cubic-bezier(.2, 0, 0, 1)` |
| `--ease-out` | `cubic-bezier(.05, .7, .1, 1)` |
| `--ease-spring` | `cubic-bezier(.34, 1.56, .64, 1)` (press spring-back, toast, chip pop) |

Palette is unchanged: emerald accent, teal, gold. Gold is used for the toast countdown hairline and the active
nav bar; emerald for focus rings, ripples and the card spotlight.

## What was added

- Buttons: hover lift and brighten, press scale 0.97 with spring-back, ripple from the pointer, spinner while the
  request the button started is in flight, green or red ring flash when it settles.
- Cards and KPIs: shadow elevation and 2 px lift on hover, emerald spotlight following the pointer (fine pointers
  only), staggered entrance (45 ms per item, capped at 12).
- Inputs: focus ring that animates in, accent border once a field has text, shake plus `aria-invalid` on an empty
  submit, chips pop in.
- Feedback: toast springs up with a success or error icon and a gold hairline that counts down; KPI numbers roll up
  from zero over 1.1 s; skeleton shimmer was already present.
- Delight: charts draw in (line charts and sparklines wipe left to right, bars grow from their baseline).
- Accessibility: `prefers-reduced-motion` turns off entrances, chart draw-in, hover movement and count-up; state
  changes (busy, flash, toast) still show. Only transform, opacity, box-shadow and clip-path are animated.

## How it is wired

`static/motion.js` is delegated, so pages need no changes: it wraps `fetch` to attribute a request to the button
clicked within the previous 150 ms, watches `#app` with a MutationObserver for new cards and KPIs, and exposes
`Motion.shake(el)` for invalid input.

## Visual redesign

Research (read 2026-10-07; the design-system pages are third-party write-ups, not first-party specs):

- Linear's own redesign post, https://linear.app/now/how-we-redesigned-the-linear-ui : themes generated in LCH so equal
  lightness looks equal, chrome made more neutral, Inter Display for headings and plain Inter for body, contrast raised
  (text lighter in dark mode, darker in light mode).
- Summary of Linear's system: https://open-design.ai/plugins/design-system-linear-app/ : near-achromatic dark surfaces,
  hierarchy carried by white-opacity steps, one brand accent used sparingly, tight negative letter-spacing on display sizes.
- Mercury: https://www.925studios.co/blog/mercury-design-breakdown : indigo-black (about #171721), never pure black; green means
  growth, red and yellow are kept for errors and warnings.
- Vercel Geist: https://blakecrosley.com/guides/design/vercel : dark treated as the default, density over decoration.
- 2026 fintech trends: https://abduzeedo.com/gudrix-agencys-fintech-ui-design-ai-product-app and
  https://trends.daisyui.com/trend/glassmorphism/ : deep dark palettes with high-contrast type, gradients as the most
  requested device, frosted panels (about 16 px blur) with semi-transparent borders.

The 5 moves taken from them:

1. Ink, not black: a midnight-indigo base (#070817) with a fixed aurora mesh (violet, money-green, rose) behind everything,
   so depth comes from light rather than from borders. Light mode is lavender paper with the same mesh, never flat white.
2. Layered glass: cards are translucent with a 16 px blur, a 1 px semi-transparent border and an inner top highlight; hover
   adds a violet ring and a pointer spotlight.
3. One loud display face for numbers and headings: Sora (bundled, offline) with tight negative tracking for the hero, KPI
   values and card titles; Manrope stays for text.
4. Colour with a job: green stays the money colour (revenue, up-deltas, bars); electric violet is the action colour
   (buttons, focus, active nav); amber and rose are warm accents. Each KPI owns one hue that tints its hairline, glow
   corner and sparkline.
5. Data ink that glows: chart bars and areas use gradient fills, lines get a soft glow and end dots, grids are dashed and
   faint; empty states carry an inline SVG illustration; the SQL panel is always ink so code pops in both themes.

Palette (dark / light): ink #070817 / lavender #f1effb; text #eef0ff / #12132e; muted #a6abd3 / #50557c; violet action
#b9a8ff text, button gradient #7d4fff to #5a3df0; money green #3ee8a5 / #047857; amber #ffb454; rose #ff6fa5; cyan #3dd6f5.
Categorical set c1..c6: green, violet, amber, rose, cyan, orange. Motion tokens and behaviour are unchanged.
