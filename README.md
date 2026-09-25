# WCL Roster Analyst

Pulls a Warcraft Logs report through the official API v2, builds one performance row per
player, ranks players worst -> best within their role, and proposes a roster cut to a target
size. Outputs a Markdown table (for chat), a CSV of raw metrics, and a formal one-page PDF for
the raid leader.

## Setup

1. **Python 3.11+** (developed against 3.13). Install dependencies:
   ```
   pip install requests jinja2 pyyaml pandas pypdf playwright python-dotenv pytest
   python -m playwright install chromium
   ```
   The PDF uses Carlito/Calibri (Calibri ships with Windows). On Linux, install Carlito
   (`apt install fonts-crosextra-carlito`); wide fallbacks such as DejaVu Sans can push the table
   onto a second page.
   Make sure `pip` and `python` resolve to the *same* interpreter (`python -m pip install ...`
   is the safest way to guarantee that) - a mismatch is a common source of `ModuleNotFoundError`
   for a package that "pip says is installed."

2. **Get a Warcraft Logs API client** (needed to call the v2 API):
   - Log in at https://www.warcraftlogs.com, go to https://www.warcraftlogs.com/api/clients,
     and click "Create a Client".
   - Give it any name (e.g. `roster-analyst`). The redirect URL field can be anything, even the
     report URL itself - it's unused by the client-credentials flow this tool uses.
   - Leave "Public Client" unchecked - this tool needs to store the secret privately, on your
     machine, in `.env`.
   - Copy the **Client ID** and **Client Secret** shown immediately after creation (the secret
     is shown once).

3. Copy `.env.example` to `.env` and fill in:
   ```
   WCL_CLIENT_ID=...
   WCL_CLIENT_SECRET=...
   ```
   `.env` is gitignored - never commit it.

## Usage

```
python run.py <report_url> [--target 20] [--composition 2/4/14] [--protect "Name"] [--cutoff 3]
```

Example:
```
python run.py https://www.warcraftlogs.com/reports/VrH3tjbQzWCaywMd --target 20 --protect "RaidLeaderName"
```

### One boss across a character's reports

```
python run.py "https://www.warcraftlogs.com/character/id/86473646?boss=3497" --difficulty heroic --roster team_roster.yaml
```

A character URL (`/character/id/<n>` or `/character/<region>/<server>/<name>`) plus a boss
(`?boss=<encounter id>` or `--boss`) analyses that boss across every report of the character:

- The same pull logged by several raiders is counted once (same difficulty, start within 30s).
- `--difficulty lfr|normal|heroic|mythic` picks the difficulty. Without it, if the logs contain
  more than one, the tool lists the pulls per difficulty and exits (code 2) before fetching any
  per-pull data.
- Only pulls with the character present are used; `--require-player "Name"` picks another
  player, `--all-pulls` drops the requirement.
- The PDF footer lists every source report as a full link.

`--roster team_roster.yaml` (any mode) ranks only the core roster's mains; guests and alts are
left out, and role/class disagreements with the logs are printed. `team_roster.yaml` is
transcribed by hand from the team's wowaudit roster.

Or via the Claude Code subagent: `/roster <report_url> ...` (see `.claude/commands/roster.md`,
which delegates to `.claude/agents/wcl-roster-analyst.md`).

Outputs land in `out/<report_code>/`:
- `metrics.csv` - one row per player, all raw metrics.
- `roster_review.pdf` - the formal one-pager for the raid leader (a second page only when the
  roster doesn't fit on one, see `output.pdf.max_pages`).
- `roster_review.png` - a preview image of that page.

Raw GraphQL responses are cached to `cache/<report_code>/`; a rerun with a warm cache makes zero
network calls (confirmed: a warm-cache run of the sample report completes in ~4s, entirely
Playwright/PDF rendering time).

## API notes / schema deltas (from live introspection, 2026-09-25)

The spec's draft queries were confirmed accurate against the live schema with these concrete
findings (field names matched; the details below are about response *shape*, which introspection
alone doesn't reveal since several fields return an opaque `JSON` scalar):

- `Report.fights(killType: Encounters)` returns both kills and wipes, excluding trash pulls -
  exactly what's needed. Field names (`id`, `kill`, `difficulty`, `startTime`, `endTime`,
  `fightPercentage`, `averageItemLevel`) all match the spec's draft query.
- `playerDetails(fightIDs, includeCombatantInfo: true)` returns
  `{"data": {"playerDetails": {"healers": [...], "tanks": [...], "dps": [...]}}}` (note the extra
  `data` wrapper around the JSON scalar). Each entry has `id`, `name`, `type` (class),
  `specs: [{spec, count}]`, `minItemLevel`, `maxItemLevel`. There is no separate "role" enum -
  the *key* the player appears under (`healers`/`tanks`/`dps`) *is* the role.
- `table(fightIDs, dataType, wipeCutoff)` returns `{"entries": [...], "totalTime": <ms>}`.
  `entries[].total` is a **raw total**, not a rate - `wcl/metrics.py` computes DPS/HPS as
  `total / (totalTime / 1000)`, matching the web UI's convention (rate over the full pull
  duration, not just active time).
- `Healing` table entries include `overheal`; net healing is `total - overheal`.
- `DamageTaken` table entries include `totalReduced` alongside `total`; `mitigated_pct` is
  derived as `(total - totalReduced) / total * 100`.
- `Deaths` table entries are **one row per death event**, each with an absolute `timestamp` (same
  clock as `fight.startTime`/`endTime`) and a `fight` id. Survival% uses the earliest timestamp
  per player per fight.
- Pets appear as separate `table` entries (`type: "Pet"`) with no owner reference on the entry
  itself. The owner mapping is `masterData.actors(type: "Pet")[].petOwner`, which is frequently
  `null` for a given pull (WCL couldn't resolve which player's pet it was); pets with an
  unresolved owner are left out of the merge rather than guessed at.
- `table()` has no per-fight breakdown parameter - each call aggregates over whatever `fightIDs`
  are passed. Per-pull granularity (required for survival% and normalisation) therefore costs one
  `table` call per fight per dataType. This tool fetches at that granularity and derives
  raid-aggregate totals itself by summing pulls, avoiding a second, redundant "aggregate" call.
- A report's `masterData.actors(type: "Player")` list can include actors who never appear in any
  fight's `playerDetails` role buckets (their `subType`/class comes back as `"Unknown"`). These
  are raid-group members who never participated in a scored encounter (bench, disconnected,
  spectating) - they are correctly excluded from the metrics output, not a bug. Verified on the
  sample report: 33 player actors total, 9 with `class: Unknown` and zero pulls, 24 with real
  metrics.
- Player names come through from the API exactly as WCL stores them, including characters like
  `Enzð` or `Välerjar` that look like mojibake but are not - that's the actual name on file. No
  transliteration or cleanup is applied.

## Design decisions not fully specified in the build spec

- **How cuts are decided** follows `roster-DECISION-GUIDE.md`: within DPS and healers, players
  are ordered by normalised output; survival, active time, damage taken and overhealing only
  decide close cases (near-tie under 2%, borderline 2-5%). Every Remove gets a confidence level
  (clear / supported / close call), and close calls are named in the notes.
- **Tank scoring**: tanks are only compared when they exceed their slots, on survival,
  mitigation and share of damage taken (weights in `config.yaml` under `scoring.tank`).
- **Excluded players**: support specs (`attribution_specs`, e.g. Augmentation Evoker) and players
  who attended under `min_sample_pulls_fraction` of pulls are shown as Keep, never cut, and
  still fill a slot. The WCL `table` API does not appear to expose a distinguishable
  "attributed/augmented damage" field, so the tool excludes and flags them rather than guessing.
- **Rates** use the table's `totalTime` (the part of the pull before the wipe cutoff), and a
  player present in a pull but missing from the output table (an instant wipe) counts as 0
  output for that pull. Survival % uses the full pull length.

## Limitations (also stated by the subagent to the end user)

- Specs/roles/item level come only from `playerDetails`, never inferred from name colour.
- Utility assignment (interrupts rotation, dispel rotation, positioning for mechanics) is not
  scored - `Interrupts`/`Dispels` tables are not fetched, and the notes say utility was not assessed.
- Raid-buff and Bloodlust coverage checks only cover the classes listed in `config.yaml`'s
  `raid_buffs`/`bloodlust_classes` - a coverage swap only fires if a cut would remove the *last*
  source of a listed buff.
- Attendance history (across other reports/nights) and player attitude are not assessed - this
  tool only sees the one report it's given.

## Tests

```
python -m pytest tests/ -v
```

Covers survival% calculation (incl. multi-death and fight-start-offset edge cases), per-pull
normalisation (`output_norm` and duration-weighted averaging), the near-tie cut rule
(output-then-survival-then-class-redundancy), and the raid-buff coverage swap.

## Project layout

```
wow_log_analyzer/
├── .claude/
│   ├── agents/wcl-roster-analyst.md
│   └── commands/roster.md
├── wcl/
│   ├── auth.py          # OAuth client-credentials token, cached to .token.json
│   ├── client.py        # GraphQL POST helper, retries + rate-limit backoff
│   ├── introspect.py    # live schema introspection (python -m wcl.introspect)
│   ├── fetch.py         # report -> fights, actors, playerDetails, tables
│   ├── multi.py         # character mode: one boss across reports, dedupe + merge
│   ├── roster.py        # core roster file -> filter ranked players to mains
│   ├── metrics.py       # per-player, per-fight metrics
│   ├── rank.py          # scoring, ranking, cut proposal
│   └── report.py        # Markdown, CSV, HTML -> PDF
├── templates/onepager.html.j2
├── tests/
├── team_roster.yaml     # core roster mains (from wowaudit)
├── config.yaml           # scoring weights, roster defaults, raid buffs
├── .env.example
├── run.py
└── README.md
```
