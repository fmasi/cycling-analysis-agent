# Silca pressure-lookup agent prompt

**When to use:** new bike added to the `bikes:` dict in `USER_PROFILE.md`, or any material input change (new tyres, sustained ±2 kg weight shift, new measured F/R split).

**Prerequisite tooling:** a Chrome browser available to the dispatched agent (Playwright, Chromium, or equivalent — verify availability before dispatching).

**Inputs (collected once per surface per bike):**

| Field | Source | Example |
|---|---|---|
| Tyre size (ETRTO) | `bikes[slug].tyres.size_etrto` or `size_mm` | 54-406 (Brompton) / 32 (Tripster) |
| Measured tyre width (mm) | `bikes[slug].tyres.measured_mm` if present | 31.4 (Tripster GP4S) |
| System weight (kg) | `bikes[slug].system_weight_kg_default` | 98.5 (Brompton, commute kit) |
| F/R split (front %) | parse `bikes[slug].fr_split` "40/60" → 40 | 40 (Tripster) |
| Tube type | from `bikes[slug].tyres.tube_type` | TPU (Tripster, 21 Apr 2026+) |
| Surface | one of `bikes[slug].surfaces_supported` | gravel_smooth (Brompton) |

**Agent prompt template (paste into a fresh subagent dispatch with a Chrome browser tool):**

> Open `https://silca.cc/pages/sppc-form` in Chrome. The page is a single-page form ("Silca Professional Pressure Calculator" / SPPC).
>
> Fill in:
> - Rider weight: `{system_weight_kg - bike_weight_kg}` (kg or lb — match the form's unit)
> - Bike weight: `{bike_weight_kg}`
> - Front-wheel weight distribution: `{front_pct}`%
> - Tyre width: `{measured_tyre_width_mm}` (front and rear, same value)
> - Wheel size / rim size: pick the matching standard (for ETRTO 54-406 use the 20" option; for ETRTO 25-622+ use 700c)
> - Surface category: pick the option matching `{surface}` — e.g. "Smooth Pavement", "Worn Pavement", "Poor Pavement", "Gravel". Use the surface mapping below.
> - Tube type: `{tube_type}` (TPU → "Latex/TPU"; butyl → "Butyl"; tubeless → "Tubeless")
>
> Submit the form, wait for the recommendations to render, and capture:
> - Front pressure (psi)
> - Rear pressure (psi)
> - A screenshot of the calculator result page (save to `rides/charts/silca-{bike_slug}-{surface}-{date}.png` for audit trail)
>
> Surface mapping (USER_PROFILE crr_by_surface key → Silca surface category):
> - `tarmac` → "Worn Pavement" (default for typical UK / FR roads)
> - `tarmac_high_pressure` → "Smooth Pavement"
> - `gravel_smooth` → "Gravel" (the lowest Silca gravel option)
> - `gravel_rough` → "Gravel" + drop 2 psi front / 3 psi rear (Silca doesn't differentiate; rider preference)
>
> **Report back** in this exact format:
>
> ```yaml
> silca_lookup:
>   bike: {bike_slug}
>   surface: {surface}
>   inputs:
>     rider_weight_kg: …
>     bike_weight_kg: …
>     front_pct: …
>     tyre_width_mm: …
>     wheel_size: …
>     surface_silca: …
>     tube_type: …
>   outputs:
>     front_psi: …
>     rear_psi: …
>   screenshot: rides/charts/silca-{bike_slug}-{surface}-{date}.png
>   timestamp: {ISO-8601 UTC}
> ```

**Post-processing:** the agent returns the YAML block; paste it into `bikes[slug].tyre_pressure_psi[surface] = {front: …, rear: …}` in `USER_PROFILE.md`. Remove the "indicative" / "not yet validated" warning when all surfaces in `surfaces_supported` have a recorded lookup.

**Manual fallback:** if no Chrome browser is available, run the same inputs through `https://silca.cc/pages/sppc-form` by hand and paste the same YAML block.
