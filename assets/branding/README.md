# AI Project Factory Branding

`icon-concepts/round-1/` contains four first-round visual directions:

- A — Portable Portal: one stable entrance across model providers.
- B — Handoff Fold: project continuity through a portable handoff.
- C — Layered Core: Constitution, Contract, and Goal around one core.
- D — Relay Orbit: multiple agents continuing around one project core.

`icon-concepts/round-2/` narrows the strongest ideas into:

- A2 — Core Gate: a stable entrance around one project core.
- C2 — Open Layers: three project layers opening toward a portable core.
- H2 — Transfer Frame: one core continuing between two agent frames.

**H2 — Transfer Frame is the approved final mark.** Its two open, rotationally
symmetric frames represent different Agents continuing around one portable
project core. The production artwork is deterministic and flat:

- `master/ai-project-factory-h2.svg` is the vector source of truth;
- `master/ai-project-factory-h2-512.png` is the 512 px raster master;
- `previews/ai-project-factory-h2-qa.png` is the light/dark size QA sheet;
- `desktop/ai-project-factory.ico` contains independently rendered
  16/20/24/32/40/48/64/128/256 px frames.

Run `scripts/build_brand_assets.py` to rebuild all four outputs from the same
geometry constants. Small ICO frames use explicit optical stroke, gap, and core
adjustments instead of shrinking one large PNG. Then run
`scripts/deploy_windows_desktop.py`: the existing Desktop shortcut keeps the
same name and launcher target, while its icon points to the new
content-addressed H2 ICO so Windows cannot reuse a stale preview from cache.
