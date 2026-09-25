# One-Pager PDF: Exact Style Specification

This is the complete visual spec of the *Roster Review* one-page PDF. Follow it exactly. Section 9 contains the literal template that produced the reference PDF; if anything in the prose conflicts with it, the template wins.

---

## 1. Rendering pipeline

- **Build:** author the page as HTML + CSS and render it to PDF with **Playwright / Chromium**:
  ```python
  page.goto("file:///abs/path/onepager.html", wait_until="networkidle")
  page.pdf(path="roster_review.pdf", format="A4", print_background=True)
  ```
- **`print_background=True` is mandatory.** Without it the header gradient, row tints and pills disappear.
- **Page:** A4 portrait (210 × 297 mm), `@page { size: A4; margin: 0 }`. All spacing is handled inside the page.
- **Single page:** after rendering, count the pages with `pypdf`. If there is more than one page, reduce the table cell vertical padding (1.0 → 0.8 mm), then the body font size (8.4 → 8.0 pt), and re-render. Allow at most 5 attempts, then raise an error.
- **Preview:** also export a PNG of the page (`pdftoppm -r 80 -png`) and inspect it.

## 2. Typography

- **Font stack:** `font-family: Carlito, Calibri, Arial, sans-serif`.
  - Carlito is metric-compatible with Calibri; it is narrow and professional.
  - **Do not use Google or web fonts.** They may not load in headless rendering.
  - **Do not use DejaVu Sans.** It is too wide and pushes the table onto a 2nd page.
- **Weights:** Carlito ships only Regular (400) and Bold (700). CSS weights 600 and 800 render as Bold; 500 renders as Regular. Keep the weights below anyway, so the page looks right if a font with more weights is used.
- **Numbers:** all numeric cells use `font-variant-numeric: tabular-nums` and are right-aligned.

| Element | Size | Weight | Colour | Case / tracking | Other |
|---|---|---|---|---|---|
| Body base | 8.4 pt | 400 | `#1d2330` | normal | — |
| Eyebrow ("ROSTER REVIEW · CONFIDENTIAL") | 7.6 pt | 600 | `#e8cf8a` (light gold) | UPPERCASE, letter-spacing 0.22em | — |
| H1 title | 23 pt | 800 | `#ffffff` | letter-spacing −0.01em | margin 1.5 mm top and bottom |
| Header subtitle / methodology | 9 pt | 400 | `#b9c2d8` | normal | line-height 1.45, max-width 165 mm; the key phrases "#1 (lowest performance)" and "#N (highest)" are **bold white** |
| KPI label | 7.2 pt | 600 | `#687089` | UPPERCASE, 0.08em | — |
| KPI value | 14 pt | 800 | `#141a2a` (red `#b3261e` for "Proposed removals") | — | display:block |
| Table header (th) | 7 pt | 700 | `#687089` | UPPERCASE, 0.08em | — |
| Rank (#) | 8.4 pt | 700 | `#8a91a6` | — | column width 6 mm |
| Player name | 8.4 pt | **700** | `#1d2330` | — | nowrap, preceded by a class dot |
| Role ("DPS", "Healer", "Tank") | 8.4 pt | 500 | `#1d2330` | — | nowrap |
| Class after role ("Priest") | 7.6 pt | 400 | `#8a91a6` | — | margin-left 1.2 mm |
| Numeric cells | 8.4 pt | 400 | `#1d2330` | — | right-aligned, tabular-nums |
| Survival < 90% | 8.4 pt | **700** | `#b3261e` (red) | — | — |
| Survival 90–94.9% | 8.4 pt | 600 | `#a86a00` (amber) | — | — |
| Rationale | 7.8 pt | 400 | `#3d4457` | sentence case | wraps |
| Status pill | 7 pt | 700 | see §4 | letter-spacing 0.03em | nowrap |
| Section heading (H2) | 7.6 pt | 800 | `#141a2a` | UPPERCASE, 0.14em | 1 px bottom rule `#e3e6ee` |
| Composition text | 8 pt | 400; labels 700 | `#1d2330`; labels `#141a2a` | — | line-height 1.55 |
| Notes list | 7.8 pt | 400 | `#3d4457` | — | line-height 1.5, 0.8 mm between items |
| Footer | 7 pt | 400 | `#8a91a6` | — | flex, space-between |

## 3. Colour tokens

| Token | Hex | Used for |
|---|---|---|
| navy-900 | `#141a2a` | header gradient start, KPI values, H2, table header bottom rule, Essential pill background |
| navy-700 | `#26324f` | header gradient end |
| gold-600 | `#c9a24a` | gold rule (ends) |
| gold-300 | `#e8cf8a` | gold rule (centre), eyebrow, Essential pill text |
| ink | `#1d2330` | body text |
| ink-2 | `#3d4457` | rationale, notes |
| muted | `#687089` | KPI labels, table header labels |
| muted-2 | `#8a91a6` | rank numbers, class names, footer, protected-row text |
| sub-on-dark | `#b9c2d8` | header subtitle |
| line | `#e3e6ee` | card borders, H2 rule, footer rule |
| line-soft | `#eceef3` | table row separators |
| card-bg | `#f8f9fc` | KPI card background |
| red | `#b3261e` | Remove pill, low survival, removals KPI |
| red-tint | `#fdf1f0` | background of Remove rows |
| amber | `#a86a00` | medium survival |
| grey-tint | `#f4f5f8` | background of the protected (Raid Leader) row |

**Class dots** (2 × 2 mm circle, 1.6 mm right margin, vertical-align +0.2 mm):

| Class | Hex |
|---|---|
| Death Knight | `#C41E3A` |
| Demon Hunter | `#A330C9` |
| Druid | `#FF7C0A` |
| Evoker | `#33937F` |
| Hunter | `#8FB85A` |
| Mage | `#3FC7EB` |
| Monk | `#00C77A` |
| Paladin | `#F48CBA` |
| Priest | `#9A9A9A` |
| Rogue | `#D4C23A` |
| Shaman | `#0070DD` |
| Warlock | `#8788EE` |
| Warrior | `#C69B6D` |

Priest, Rogue, Hunter and Monk are darkened from the official class colours so they stay visible on white.

## 4. Status pills

All pills are `inline-block`, with padding 0.4 mm × 2 mm, border-radius 5 mm (fully rounded), 7 pt, weight 700 and letter-spacing 0.03em.

| Status | Background | Text | Row background |
|---|---|---|---|
| Remove | `#b3261e` | `#ffffff` | `#fdf1f0` |
| Reserve | `#fff1d6` | `#8a5a00` | none |
| Keep | `#e5f4ea` | `#1e6b3a` | none |
| Essential | `#141a2a` | `#e8cf8a` | none |
| Raid Leader (protected) | `#e3e6ee` | `#555d73` | `#f4f5f8`, and all text in the row is `#8a91a6`; rationale reads "Not evaluated" |

## 5. Layout, top to bottom (measurements in mm)

1. **Header band:** full page width (edge to edge).
   - Padding: 7.5 top, 12 sides, 6 bottom.
   - Background: `linear-gradient(120deg, #141a2a, #26324f)`.
   - Along the bottom edge: a 1.2 mm gold rule, `linear-gradient(90deg, #c9a24a, #e8cf8a, #c9a24a)`, drawn with an absolutely positioned `::after`.
   - Contents in order: eyebrow → H1 → subtitle.
2. **Main area:** padding 4.5 top, 12 left/right, 0 bottom.
3. **KPI row:** 4 equal columns with a 3 mm gap and 3.5 mm margin below. Each card has a 1 px `#e3e6ee` border, 2 mm radius, padding 2 × 3.2, background `#f8f9fc`, with the label above and the value below. The cards are, in order:
   - Current roster
   - Target roster
   - Proposed removals (value in red)
   - Final composition (formatted `2 · 4 · 14`, using the middle dot U+00B7)
4. **Table:** 100% width, `border-collapse: collapse`.
   - Header cells: padding 0 × 1.6 × 1.6 (top/sides/bottom), left-aligned (numeric columns right-aligned), 1.5 px `#141a2a` bottom border.
   - Body cells: padding 1.0 × 1.6, 1 px `#eceef3` bottom border, vertical-align middle.
   - Columns in order: `#` · `Player` · `Role / Class` · `Output` · `Survival` · `Active` · `Status` · `Rationale`.
   - The Rationale column takes the remaining width and is the only column allowed to wrap.
   - **Grouped by role:** rows are split into Tanks, Healers and DPS sections, in that order. Each section opens with a full-width group row: the role name styled like an H2 (7.6 pt, 800, uppercase, 0.14em, `#141a2a`), followed by "{kept} of {total} stay", plus " · {n} leave(s)" in bold red when there are cuts. The group row has a 1 px `#141a2a` bottom border. Within a section, rows are ordered by status (Remove, Reserve, Keep, Essential, Raid Leader), then by overall rank ascending (worst first), so that role's cuts are always the top rows. `#` stays the overall rank. `templates/onepager.html.j2` supersedes the §9 reference for this.
5. **Bottom block:** 2 equal columns with a 5 mm gap and 3.5 mm margin above.
   - **Left:** H2 "Proposed composition", then lines with bold labels: `Tanks (n):`, `Healers (n):`, `DPS (n):`, `Removed:`, each followed by comma-separated names.
   - **Right:** H2 "Notes", then a bulleted list of 2–3 items (list padding-left 3.5 mm).
6. **Footer:** margin 3 mm top, 12 mm sides; a 1 px `#e3e6ee` top border with 2 mm padding above the text.
   - Left: "Source: Warcraft Logs — Damage Done, Damage Taken, Healing and Survival views, all pulls combined".
   - Right: "Month YYYY".

## 6. Content and tone rules

- English only. Formal, serious and professional: no slang, no emoji, no exclamation marks.
- **Title:** "Proposed Reduction to a {N}-Player Roster".
- **Eyebrow:** "Roster Review · Confidential".
- **Subtitle template:** "Based on combined data from all pulls (kills and wipes), ignoring events after {cutoff} player deaths. Players are compared within their role: DPS by damage per second, healers by healing per second, tanks as essential slots. Survival and damage taken serve as secondary criteria. Ranked from **#1 (lowest performance)** to **#{total} (highest)**."
- **Rationale:** one line, factual, numbers-based, with clauses separated by semicolons and no trailing full stop. Examples:
  - "Lowest DPS and item level (318); lowest active time; second-lowest survival"
  - "DPS on par with Shalammage, but a notably higher death rate"
- **Number formats:**
  - Output: `148.8k` (one decimal, lowercase k).
  - Percentages: `87.9%`; a perfect score is shown as `100%`, not `100.0%`.
  - Damage amounts in notes: `254m`.
- **Protected player:** status "Raid Leader", rationale "Not evaluated". Never comment on this player's numbers anywhere in the document.

## 7. Row markup (per player)

```html
<tr class="st-{status_slug}">
  <td class="rk">{rank}</td>
  <td class="nm"><span class="dot" style="background:{class_hex}"></span>{name}</td>
  <td class="role">{role}<span>{class}</span></td>
  <td class="num">{output}</td>
  <td class="num {bad|warn|''}">{survival}</td>
  <td class="num">{active}</td>
  <td><span class="pill p-{status_slug}">{status}</span></td>
  <td class="why">{rationale}</td>
</tr>
```

`status_slug` is the status lowercased with spaces removed: `remove`, `reserve`, `keep`, `essential`, `raidleader`.

## 8. Quality checklist before delivering

- Exactly **1 page**; nothing is clipped at the bottom.
- The header gradient, gold rule, row tints and pills are all visible (background printing is on).
- Every player has a class dot, and names never wrap.
- The Remove rows are the only red-tinted rows; the protected row is grey.
- The KPI values match the table: removal count and composition.
- The footer month/year is the current date.

## 9. Reference template (Jinja2, exact CSS)

```html
<!doctype html><html><head><meta charset="utf-8">
<style>
@page{size:A4;margin:0}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:Carlito,Calibri,Arial,sans-serif;color:#1d2330;width:210mm;font-size:8.4pt;-webkit-print-color-adjust:exact;print-color-adjust:exact}
.hdr{background:linear-gradient(120deg,#141a2a,#26324f);color:#fff;padding:7.5mm 12mm 6mm;position:relative}
.hdr:after{content:"";position:absolute;left:0;right:0;bottom:0;height:1.2mm;background:linear-gradient(90deg,#c9a24a,#e8cf8a,#c9a24a)}
.eyebrow{font-size:7.6pt;letter-spacing:.22em;text-transform:uppercase;color:#e8cf8a;font-weight:600}
h1{font-size:23pt;font-weight:800;margin:1.5mm 0 1.5mm;letter-spacing:-.01em}
.sub{color:#b9c2d8;font-size:9pt;max-width:165mm;line-height:1.45}
.sub b{color:#fff}
.main{padding:4.5mm 12mm 0}
.kpis{display:grid;grid-template-columns:repeat(4,1fr);gap:3mm;margin-bottom:3.5mm}
.kpi{border:1px solid #e3e6ee;border-radius:2mm;padding:2mm 3.2mm;background:#f8f9fc}
.kpi b{display:block;font-size:14pt;font-weight:800;color:#141a2a}
.kpi span{font-size:7.2pt;color:#687089;text-transform:uppercase;letter-spacing:.08em;font-weight:600}
.kpi.rm b{color:#b3261e}
table{width:100%;border-collapse:collapse}
th{text-align:left;font-size:7pt;text-transform:uppercase;letter-spacing:.08em;color:#687089;font-weight:700;padding:0 1.6mm 1.6mm;border-bottom:1.5px solid #141a2a}
th.num,td.num{text-align:right}
td{padding:1mm 1.6mm;border-bottom:1px solid #eceef3;vertical-align:middle}
td.rk{font-weight:700;color:#8a91a6;width:6mm}
td.nm{font-weight:700;white-space:nowrap}
.dot{display:inline-block;width:2mm;height:2mm;border-radius:50%;margin-right:1.6mm;vertical-align:.2mm}
td.role{white-space:nowrap;font-weight:500}
td.role span{color:#8a91a6;margin-left:1.2mm;font-size:7.6pt}
td.num{font-variant-numeric:tabular-nums;white-space:nowrap}
td.bad{color:#b3261e;font-weight:700}
td.warn{color:#a86a00;font-weight:600}
td.why{color:#3d4457;font-size:7.8pt}
tr.st-remove td{background:#fdf1f0}
tr.st-raidleader td{background:#f4f5f8;color:#8a91a6}
.pill{display:inline-block;padding:.4mm 2mm;border-radius:5mm;font-size:7pt;font-weight:700;letter-spacing:.03em;white-space:nowrap}
.p-remove{background:#b3261e;color:#fff}
.p-reserve{background:#fff1d6;color:#8a5a00}
.p-keep{background:#e5f4ea;color:#1e6b3a}
.p-essential{background:#141a2a;color:#e8cf8a}
.p-raidleader{background:#e3e6ee;color:#555d73}
.bottom{display:grid;grid-template-columns:1fr 1fr;gap:5mm;margin-top:3.5mm}
h2{font-size:7.6pt;text-transform:uppercase;letter-spacing:.14em;color:#141a2a;font-weight:800;margin-bottom:1.8mm;padding-bottom:1.2mm;border-bottom:1px solid #e3e6ee}
.comp{font-size:8pt;line-height:1.55}
.comp b{color:#141a2a}
ul{padding-left:3.5mm;font-size:7.8pt;line-height:1.5;color:#3d4457}
li{margin-bottom:.8mm}
.foot{margin:3mm 12mm 0;font-size:7pt;color:#8a91a6;display:flex;justify-content:space-between;border-top:1px solid #e3e6ee;padding-top:2mm}
</style></head><body>

<div class="hdr">
  <div class="eyebrow">Roster Review · Confidential</div>
  <h1>Proposed Reduction to a {{ target }}-Player Roster</h1>
  <div class="sub">Based on combined data from all pulls (kills and wipes), ignoring events after {{ cutoff }} player deaths. Players are compared within their role: DPS by damage per second, healers by healing per second, tanks as essential slots. Survival and damage taken serve as secondary criteria. Ranked from <b>#1 (lowest performance)</b> to <b>#{{ players|length }} (highest)</b>.</div>
</div>

<div class="main">
  <div class="kpis">
    <div class="kpi"><span>Current roster</span><b>{{ players|length }}</b></div>
    <div class="kpi"><span>Target roster</span><b>{{ target }}</b></div>
    <div class="kpi rm"><span>Proposed removals</span><b>{{ removed|length }}</b></div>
    <div class="kpi"><span>Final composition</span><b>{{ n_tanks }} · {{ n_healers }} · {{ n_dps }}</b></div>
  </div>

  <table>
    <thead><tr>
      <th>#</th><th>Player</th><th>Role / Class</th>
      <th class="num">Output</th><th class="num">Survival</th><th class="num">Active</th>
      <th>Status</th><th>Rationale</th>
    </tr></thead>
    <tbody>
    {% for p in players %}
      <tr class="st-{{ p.status_slug }}">
        <td class="rk">{{ p.rank }}</td>
        <td class="nm"><span class="dot" style="background:{{ p.class_hex }}"></span>{{ p.name }}</td>
        <td class="role">{{ p.role }}<span>{{ p.cls }}</span></td>
        <td class="num">{{ p.output }}</td>
        <td class="num {{ p.surv_class }}">{{ p.survival }}</td>
        <td class="num">{{ p.active }}</td>
        <td><span class="pill p-{{ p.status_slug }}">{{ p.status }}</span></td>
        <td class="why">{{ p.rationale }}</td>
      </tr>
    {% endfor %}
    </tbody>
  </table>

  <div class="bottom">
    <div>
      <h2>Proposed composition</h2>
      <div class="comp">
        <b>Tanks ({{ n_tanks }}):</b> {{ tanks|join(', ') }}<br>
        <b>Healers ({{ n_healers }}):</b> {{ healers|join(', ') }}<br>
        <b>DPS ({{ n_dps }}):</b> {{ dps|join(', ') }}<br>
        <b>Removed:</b> {{ removed|join(', ') }}
      </div>
    </div>
    <div>
      <h2>Notes</h2>
      <ul>{% for n in notes %}<li>{{ n }}</li>{% endfor %}</ul>
    </div>
  </div>
</div>

<div class="foot">
  <span>Source: Warcraft Logs — Damage Done, Damage Taken, Healing and Survival views, all pulls combined</span>
  <span>{{ month_year }}</span>
</div>
</body></html>
```

`surv_class` is `"bad"` below 90, `"warn"` from 90 to 94.9, and empty otherwise.
