# Build Spec: Warcraft Logs Roster Analyst (Claude Code agent)

> Paste this file into Claude Code (or save it in an empty repo and say "Build what BUILD.md describes").
> It tells Claude Code to build a small Python toolkit and a Claude Code subagent. The agent takes a
> Warcraft Logs report URL, pulls the data through the official API, ranks every player within their
> role, proposes a roster cut to a target size, and produces a formal one-page PDF.

---

## 1. Goal

Given a report URL such as `https://www.warcraftlogs.com/reports/AbC123xYz` (optionally with `?fight=...`), the agent must:

1. Fetch all boss pulls in the report, both kills and wipes, through the **Warcraft Logs API v2**. Do not scrape the website: the HTML is JS-rendered and full of ads.
2. Build one metrics row per player: role, class/spec, item level, output (DPS/HPS), survival %, active %, damage taken, overheal and pulls attended.
3. Rank every player **from worst (#1) to best (#N)**, comparing each player only against others in the same role.
4. Given a target roster size (default 20) and composition (default 2 tanks / 4 healers / rest DPS), propose who to remove. Protected players are never proposed for removal.
5. Output:
   - a Markdown table in chat (Portuguese or English, depending on how the user asked)
   - a formal **English one-page A4 PDF** for the raid leader
   - a CSV of the raw metrics.

---

## 2. Project layout to create

```
wcl-roster/
├── .claude/
│   ├── agents/wcl-roster-analyst.md     # the subagent (section 7)
│   └── commands/roster.md               # /roster <url> [--target 20] [--protect Name]
├── wcl/
│   ├── auth.py        # OAuth client-credentials token, cached to .token.json with expiry
│   ├── client.py      # GraphQL POST helper with retries + rate-limit handling
│   ├── fetch.py       # report → fights, actors, playerDetails, tables
│   ├── metrics.py     # per-player, per-fight metrics
│   ├── rank.py        # scoring, ranking, cut proposal
│   └── report.py      # Markdown, CSV, HTML → PDF
├── templates/onepager.html.j2
├── config.yaml
├── .env.example       # WCL_CLIENT_ID=, WCL_CLIENT_SECRET=
├── run.py             # CLI entry: python run.py <url> [options]
└── README.md
```

Use Python 3.11+ with `requests`, `jinja2`, `pyyaml`, `pandas` and `playwright`. Chromium is used only for rendering the PDF.

---

## 3. Warcraft Logs API v2

- **Credentials:** the user creates a client at https://www.warcraftlogs.com/api/clients. Read `WCL_CLIENT_ID` and `WCL_CLIENT_SECRET` from `.env`. Never commit them.
- **Token:** `POST https://www.warcraftlogs.com/oauth/token` with `grant_type=client_credentials` and HTTP basic auth.
- **Endpoint:** `POST https://www.warcraftlogs.com/api/v2/client` with header `Authorization: Bearer <token>`.
- **Before writing any query, run a GraphQL introspection query against the live schema** and confirm the field and argument names used below. Adjust to the real schema where it differs, and record the differences in the README.
- **Report code:** the path segment after `/reports/`. Strip `#...` and query strings, but keep `fight=` if the user wants a single fight.

Queries needed (field names to verify via introspection):

```graphql
query ($code: String!) {
  reportData { report(code: $code) {
    title startTime endTime zone { name }
    fights(killType: Encounters) { id name encounterID kill difficulty startTime endTime fightPercentage }
    masterData { actors(type: "Player") { id name type subType server } }
  } }
}
```

```graphql
# Roles, specs and item level (authoritative; never guess class from name colours)
query ($code: String!, $ids: [Int]) {
  reportData { report(code: $code) { playerDetails(fightIDs: $ids) } }
}
```

```graphql
# One call per dataType; returns a JSON blob
query ($code: String!, $ids: [Int], $dt: TableDataType!, $cutoff: Int) {
  reportData { report(code: $code) {
    table(fightIDs: $ids, dataType: $dt, wipeCutoff: $cutoff)
  } }
}
```

- **dataTypes needed:** `DamageDone`, `Healing`, `DamageTaken`, `Deaths`. `Interrupts` and `Dispels` are optional and used for tie-breaks.
- **`wipeCutoff` defaults to 3.** This matches "Ignore Events After Player Deaths = 3" in the web UI, so events after the 3rd death in a pull are ignored. Make it configurable.
- **Pets:** include pet damage and healing in the owner's total. The web UI does this with the "Pets" toggle; confirm the API table already folds pets in, and merge them by `petOwner` if it does not.
- **Per-fight granularity:** fetch the tables **per fight** (or use `startTime`/`endTime` windows) as well as aggregated. Survival and consistency need per-pull data.
- **Rate limits:** respect the points budget returned in `rateLimitData`. Back off on HTTP 429.
- **Caching:** cache raw JSON to `cache/<code>/` so reruns are free.

---

## 4. Metrics (per player)

| Metric | Definition |
|---|---|
| `role` / `class` / `spec` | From `playerDetails` (tanks / healers / dps). If a player changes role between pulls, use the role they had in most pulls. |
| `ilvl` | From `playerDetails` or combatant info. Leave blank if missing; never impute. |
| `pulls_attended` | Number of fights the player was present in. |
| `output` | DPS for tanks and DPS players, HPS (healing + absorbs, excluding overheal) for healers. **Always use per-second rates, never totals**: players swapped in and out (bench rotation) have lower totals for reasons that have nothing to do with performance. |
| `active_pct` | Active time / fight time, as reported by the table. |
| `survival_pct` | For each pull: 100 if the player did not die, otherwise (time of first death − pull start) / pull duration × 100. Average across the pulls attended. |
| `deaths` | Count of deaths (within the cutoff). Also count *early deaths*: those in the first 50% of the pull. |
| `damage_taken` | Total and per-second. Flag DPS players above role mean + 1 SD as possible avoidable-damage issues. |
| `mitigated_pct` | For tanks and as context. |
| `overheal_pct` | Healers only. |
| `interrupts` / `dispels` | Optional, used for tie-breaks. |

Normalise per pull: `output_norm = player_output / median(output of same role in that pull)`. Then average across pulls, weighted by pull duration. This rewards consistency and is robust to hard vs easy bosses.

---

## 5. Scoring and ranking

> **Superseded:** how players are ordered and cut is now defined by `roster-DECISION-GUIDE.md` (throughput first; secondary metrics only decide close cases). The weighted formulas below are kept for history.

All weights live in `config.yaml`.

- **DPS:** `score = 0.60·output_norm + 0.25·(survival_pct/100) + 0.10·(active_pct/100) − 0.05·dtaken_z`, where `dtaken_z` is the z-score of damage taken within the role, clipped to [-2, 2].
- **Healers:** `score = 0.55·hps_norm + 0.25·survival + 0.10·active − 0.10·overheal_pct/100`.
- **Tanks:** they are ranked but marked **Essential** as long as they are within the tank slots. Only compare tanks against each other if there are more tanks than slots.
- **Overall list:** sort all players by a common percentile of their within-role score, so #1 is the worst overall. Tanks that fill required slots are pinned to the bottom (best end) of the list.

Special cases (lessons learned in the manual analysis):

- **Augmentation Evoker** (and any other support spec whose value is not in its own DPS): do not rank by raw DPS. Rank using the API's augmented/attributed damage if the API exposes it. Otherwise exclude the player from the DPS cut and flag "manual review".
- **Protected players** (e.g. the raid leader, via `--protect Name` or `config.protected`): shown in the table with status **"Raid Leader"**, rationale **"Not evaluated"**, and no performance numbers. They are never cut, and they still fill a roster slot. The PDF goes to the raid leader, so do not write commentary on protected players.
- **Low sample:** if `pulls_attended` is below 30% of pulls, flag "low sample" and compare the player only by per-second rates.

---

## 6. Cut proposal

> **Superseded:** how players are ordered and cut is now defined by `roster-DECISION-GUIDE.md` (throughput first; secondary metrics only decide close cases). The weighted formulas below are kept for history.

1. `slots = {tank: 2, healer: 4, dps: target − 6}`, overridable (e.g. 2/5/13).
2. For each role, `to_cut = max(0, present − slots)`. Protected and Manual Review players count as present (they fill a slot) but are never cut. If a role has more uncuttable players than slots, the excess cuts move to another role (DPS first) so the total still reaches the target, with a note.
3. Within each role, cut the lowest scores first.
4. **Near-tie rule:** if two candidates are within 2% on `output_norm`, cut the one with lower survival. If survival is also close (within 2 pp), cut the one whose class is more redundant on the roster (e.g. a fourth Mage before a second Hunter).
5. **Raid buffs and utility check:** after cutting, make sure the roster still has each raid buff and at least 2 Bloodlust sources. Buffs: Fortitude (Priest), Arcane Intellect (Mage), Battle Shout (Warrior), Mark of the Wild (Druid), Mystic Touch (Monk), Chaos Brand (DH), Skyfury (Shaman), Hunter's Mark (Hunter), Blessing of the Bronze (Evoker). If a cut removes the last source of a buff, swap to the next candidate and explain why.
6. In each role that had cuts, mark the lowest-ranked kept players (up to `reserve_count`) as **Reserve**. They stay in the roster and are first in line if more cuts are needed. Roles with no cuts get no Reserve.
7. Every row gets a one-line **rationale** built from its biggest weaknesses and strengths. Use the same style as the example below.

Status values: `Remove`, `Reserve`, `Keep`, `Essential`, `Raid Leader`. (Support specs and very low samples are now Keep, excluded from the cut; see `roster-DECISION-GUIDE.md` §2.)

---

## 7. The subagent: `.claude/agents/wcl-roster-analyst.md`

```markdown
---
name: wcl-roster-analyst
description: Analyse a Warcraft Logs report URL and rank raid players by role to propose roster cuts. Use when the user shares a warcraftlogs.com/reports link or asks who to bench/cut from a raid.
tools: Bash, Read, Write, Edit
---
You are a raid performance analyst. Given a Warcraft Logs report URL:

1. Ask only for what is missing: target roster size (default 20), composition (default 2/4/rest),
   protected players (e.g. raid leader) and wipeCutoff (default 3). Otherwise run with defaults and state them.
2. Run `python run.py <url> --target N --protect "Name" --cutoff 3`.
3. Read `out/<code>/metrics.csv` and sanity-check it: player count, roles, and no player with
   0 pulls. Specs must come from playerDetails, never from name colours.
4. Present the ranked table (worst → best) with: #, Player, Role/Class, Output, Survival,
   Active, Status, Rationale. Then list the proposed composition and 2–3 notes (high damage
   taken, support specs, utility not assessed).
5. Point to `out/<code>/roster_review.pdf` and confirm it is exactly one page.
6. Be fair and factual. Base every rationale on numbers. Never comment on protected players.
   Always state limitations: spec inference, utility and mechanics not captured by logs,
   attendance and attitude not assessed.
```

`.claude/commands/roster.md`: a slash command that delegates `$ARGUMENTS` to the `wcl-roster-analyst` subagent.

---

## 8. One-page PDF (English, formal)

Render `templates/onepager.html.j2` with Playwright (`page.pdf(format="A4", print_background=True)`).

The exact visual spec is `roster-onepager-STYLE-SPEC.md`; it overrides the summary below. Deliberate deviations: the protected player's numbers are shown as dashes, `Removed:` is omitted when empty, notes are capped at 4 (see `roster-WRITING-GUIDE.md` §5), the footer shows a clickable Warcraft Logs link (`?boss=-2&difficulty=0&cutoff=N`, plus `&fight=N` for a single-fight URL) instead of the data-source sentence, and the generation time (`September 2026 14:32`, 24-hour), and the fit loop continues past the spec's two steps (down to 7.6 pt) before failing.

- **Header:** dark navy gradient (`#141a2a → #26324f`), a thin gold rule (`#c9a24a`), eyebrow "ROSTER REVIEW · CONFIDENTIAL", title "Proposed Reduction to a {N}-Player Roster", and a methodology sentence that mentions all pulls, kills and wipes, and the wipe cutoff.
- **KPI cards:** Current roster, Target roster, Proposed removals (red), Final composition "2 · 4 · 14".
- **Table:** #, Player (with a class-colour dot), Role / Class, Output, Survival, Active, Status pill, Rationale.
  - `Remove` rows have a light red background.
  - The protected row is greyed out.
  - Survival below 90% is shown in red, 90–95% in amber.
  - Pills: Remove = red, Reserve = amber, Keep = green, Essential = navy/gold, Raid Leader = grey.
- **Bottom:** two columns, "Proposed composition" (grouped by role, plus the removed players) and "Notes".
- **Footer:** data source and month/year.
- **Fonts:** don't rely on web fonts loading. Use a locally installed narrow sans (Carlito, Calibri, Arial) as the first choice. Wide fallbacks such as DejaVu push the table onto page 2.
- **Enforce one page:** after rendering, count the pages (`pypdf`). If there is more than one, shrink row padding and font size step by step and re-render. Maximum 5 attempts; then fail loudly.
- Also save a PNG preview of the page.

---

## 9. Chat output style

The table is ordered worst → best, with columns **# | Player | Role (Class) | DPS/HPS | Survival | Active | Status | Rationale**. Example rows from the manual analysis:

| # | Player | Role | DPS / HPS | Survival | Active | Status | Rationale |
|---|---|---|---|---|---|---|---|
| 1 | Bewitcheress | DPS (Priest) | 148.8k | 87.9% | 85.5% | Remove | Lowest DPS and item level (318); lowest active time; second-lowest survival |
| 2 | Denixirian | Healer (Evoker) | 165.2k | 98.3% | 64.4% | Remove | Fifth healer; lowest HPS with 39.5% overhealing |
| 5 | Giampanos | DPS (Hunter) | 174.7k | 92.3% | 94.8% | Remove | DPS on par with Shalammage, but a notably higher death rate |
| 16 | Nictocin | Healer (Shaman) | 244.1k | 87.5% | 99.4% | Keep | Second-highest HPS; lowest survival in raid, positioning to review |

After the table:

- the proposed composition
- players to watch: high damage taken or many deaths among players who stay
- limitations.

Discord does not render Markdown tables. When the user says the result is for Discord, offer a code-block version or a PNG of the table.

---

## 10. Acceptance checks

- `python run.py <url>` works end-to-end on a real report, with a cold cache and then a warm cache.
- The player count equals the number of distinct players in the fights (pets and NPCs are excluded; pet damage is merged into the owner).
- Totals are cross-checked against the web UI on one fight: DPS within ±1%, and death counts match with the same wipe cutoff.
- The PDF is exactly one page and the protected player is shown as "Raid Leader" / "Not evaluated".
- Unit tests cover survival % calculation, per-pull normalisation, the near-tie rule and the buff-coverage swap.
