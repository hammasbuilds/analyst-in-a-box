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
