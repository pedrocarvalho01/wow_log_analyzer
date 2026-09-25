# Decision Guide: Who Stays, Who Goes, and Why

This guide describes **how the agent decides** roster cuts. It is the reasoning layer between the metrics (`wcl/metrics.py`) and the text (`roster-WRITING-GUIDE.md`), and it is implemented in `wcl/rank.py`. It supersedes the scoring and cut rules in `wcl-roster-agent-BUILD.md` §5–6. Follow the steps in order. Each step either narrows the candidate list or protects someone from it.

All thresholds below live in `config.yaml`, so they can be tuned without touching code.

---

## 0. Guiding principles

1. **Roles before rankings.** A raid needs a fixed number of tanks and healers. Cuts are decided **within each role**, never across the whole raid. A DPS player is never cut to keep a weaker healer, and vice versa.
2. **Rates, not totals.** Always compare per-second rates (DPS, HPS) over the pulls a player attended. Totals, or rates divided by pulls a player missed, punish players who were benched for some pulls.
3. **Throughput first, reliability second.** Output decides the order. Survival, active time, damage taken and overhealing only decide close cases and add weight to borderline ones.
4. **Consistency beats one good pull.** Use per-pull normalized output averaged across all pulls, never the best pull.
5. **Fixable issues are flagged, not punished.** A top performer with a correctable problem, such as deaths or avoidable damage, stays and gets a note.
6. **Composition must survive the cut.** Raid buffs, Bloodlust and battle res must still be covered afterwards.
7. **When the data can't separate two players, say so.** Flag the call for the raid leader instead of pretending there is certainty.
8. **The logs only show part of a player.** Attendance, attitude, communication, assignments and utility are not in the data. Always state this.

---

## 1. Establish the facts

1. **Count the players** actually present in boss pulls (not NPCs or pets) → `present`. A player counts as present in a pull if they appear in any of the damage, healing or damage-taken tables for it.
2. **Assign each player a role and spec** from `playerDetails`. Never infer these from name colors. If a player switched roles, use the role they played in most pulls.
3. **Set the target composition:** default `2 tanks / 4 healers / (target − 6) DPS`, overridable with `--composition`.
4. **Load the protected list.** Nobody is protected by default, including the raid leader. Protect a player only when the user names them (`--protect Name`).
5. **Compute `to_cut[role] = present[role] − slots[role]`.** A negative value means the role is short. Report that as a risk; never "cut" into a shortage.

> Example from the reference raid: 24 present = 2 tanks, 5 healers, 17 DPS. Target 20 = 2 / 4 / 14.
> That gives **0 tanks, 1 healer and 3 DPS** to cut.

This step alone decided that a healer must go, whatever the DPS numbers were.

### How the numbers are measured

- **Rates use the analyzed part of each pull.** The API truncates each pull at the wipe cutoff (by default, events after 3 player deaths are ignored). DPS, HPS, active time and damage-taken rate are divided by that analyzed time (`totalTime`), not by the full pull length. Dividing by the full pull length understated every rate by roughly a quarter.
- **Healing is already net of overhealing.** The healing table's `total` is effective healing; overhealing % = overheal / (healing + overheal).
- **Mitigation** = damage absorbed / (damage taken + damage absorbed), as the web UI shows it.
- **An instant wipe still counts.** If a player was present in a pull but did no damage or healing (for example, everyone died in the first seconds), that pull counts as 0 output for them rather than being skipped.
- **Survival %** is the share of the full pull a player stayed alive before their first death, averaged over the pulls they attended.
- **Pet damage** is added to the owner's total, or pet classes look weak.

---

## 2. Score within each role

| Role | Order by | Secondary (close cases only) | What does *not* count against them |
|---|---|---|---|
| DPS | Normalized DPS (vs role median, per pull) | Survival, active time, damage taken | Missed pulls (rates cover attended pulls only) |
| Healer | Normalized HPS | Survival, active time, overhealing | Rotating with another healer |
| Tank | Survival (50%), mitigation (30%), share of the most damage any tank took (20%) | — | Low DPS (expected for tanks) |

Tanks are only compared against each other, and only when there are more tanks than tank slots.

**Sanity checks before trusting the scores:**

- **Low sample:** attended < 70% of pulls → marked, and named in the notes. Attended < 30% → not ranked and never cut; flagged instead.
- **Support specs** (`attribution_specs` in `config.yaml`, e.g. Augmentation Evoker, whose value appears in others' numbers) → not compared on raw DPS. Excluded from the cut and flagged.
- **Missing item level** → left blank; never imputed.

Excluded players (support specs, too few pulls) show as **Keep**. They are never cut, but they still fill a roster slot.

---

## 3. Identify candidates

For each role with `to_cut > 0`:

1. Sort by normalized output, lowest first; equal values are separated by survival.
2. **Skip protected and excluded players.** They keep their slot, and the next-lowest player becomes a candidate.
   > With `--protect Windson`: Windson scored 3rd-lowest among DPS. Windson was skipped, so Giampanos (next lowest) became the 3rd DPS cut.
3. Take the lowest `to_cut` players as **provisional cuts**.
4. Take the next players (`reserve_count`, default 2) as the **reserve line**.

---

## 4. Test each provisional cut

For each provisional cut, go through these checks in order.

### 4a. Is the gap real? (near-tie test)

Compare the cut player with the lowest player *not* being cut in the same role (ignoring protected and excluded players):

- **Output gap > 5%:** the cut stands on output. Confidence: **clear**.
- **Output gap 2–5%:** borderline. The cut stands; it is **supported** if the kept player has clearly better survival (≥ 3 pp) or every secondary metric favors them, otherwise it is a **close call**.
- **Output gap < 2%:** effectively a tie.
  1. Cut the player with **lower survival**, if the survival gap is ≥ 2 pp.
  2. If survival is also within 2 pp, cut the player whose **class is more duplicated** on the roster (counting both tied players).
  3. If that is also equal, keep the provisional cut.

  A tie is always a **close call**, and the two players are named to each other.

> **With `--protect Windson`, Giampanos vs Shalammage:** 174.7k vs 173.1k DPS, a normalized gap of < 1%, so a tie. Survival was 92.3% vs 96.8% (4.5 pp), so **Giampanos is cut** and Shalammage becomes Reserve.
> Class redundancy pointed the other way (Shalammage is one of 4 Mages). Survival takes priority, but the conflict is recorded and the notes name the decision: "may inform the Giampanos / Shalammage decision".

### 4b. Composition check

After applying all cuts, confirm the roster still has:

- at least one source of every raid buff: Fortitude (Priest), Arcane Intellect (Mage), Battle Shout (Warrior), Mark of the Wild (Druid), Mystic Touch (Monk), Chaos Brand (Demon Hunter), Skyfury (Shaman), Hunter's Mark (Hunter), Blessing of the Bronze (Evoker)
- at least **2 Bloodlust/Heroism sources** (Shaman, Mage, Evoker, Hunter pet).
- at least **2 battle res sources** (Druid, Death Knight, Warlock, Paladin).

If a cut removes a required source, **keep the best removed provider and cut the lowest kept player in the same role instead**, as long as that swap doesn't break another requirement. Record the swap; it appears in the notes. If no swap can fix it, say so in the notes.

> In the reference raid, Bewitcheress (Priest) is cut, but Lifèstream (Priest) keeps Fortitude. Välerjar is the 4th Mage, so that cut costs nothing. Windson (Paladin) is cut, but Saehontas, Ashiea, Atrocion, Panyc and Hexious still cover battle res. All cuts pass.

### 4c. Redundancy as a reinforcing factor

Class redundancy is **never the main reason** for a cut. It is only used to:

- break ties (4a)
- add weight to a cut that performance already supports ("Second-lowest DPS; fourth Mage on the roster")

### 4d. Sample check

Rates cover attended pulls only, so a player who missed pulls is judged fairly on the pulls they played. If the sample is too small (< 30% of pulls), the player is not cut; they are flagged instead.

---

## 5. Protect the right people

Players who should **not** be cut even if a single metric looks bad:

| Situation | Decision | Example |
|---|---|---|
| High output, worst survival | **Keep**, and flag the issue | Nictocin: third-highest HPS but the lowest survival in the raid |
| High output, high damage taken | **Keep**, and suggest an ability breakdown in the notes | Panyc: above-average DPS, highest damage taken among DPS |
| Missed pulls (bench rotation) | Judge on **rates** over attended pulls | Denixirian: 252.9k HPS over 10 of 15 pulls |
| Support spec | **Exclude** from DPS comparison and flag | Erythria (Augmentation) |
| Tanks filling required slots | **Essential**; only compared if tanks exceed slots | Atrocion, Ojian |
| Protected player | Never cut, never commented on | Only players named with `--protect` |

**"Fixable" follows from throughput-first ordering:** survival, damage taken and the like never move a player down the order, so a top-half performer is only cut if a role has to lose more than half its players. In the bottom half, a behavioral issue adds weight to a cut that output already supports.

---

## 6. Healer decisions (special care)

Healer numbers are noisier than DPS. HPS depends on assignments, damage intake and who snipes heals. Before cutting a healer:

1. Compare on **HPS over attended pulls**, never on total healing or on HPS divided by pulls the healer missed.
2. Look at **overhealing**. High HPS with very high overhealing (> 45%) is weaker than it looks; low HPS with low overhealing (< 30%) may mean the player is starved of damage to heal, not that they're weak.
3. Check **active time**. Low active time (< 90%) outside death windows is a real negative.
4. If the two lowest healers are within **5%**, treat it as borderline (4a) and mention assignments in the notes.

> **Hasizawa vs Ashiea:** 216.0k vs 231.3k HPS, a normalized gap of 5.8%, so **Hasizawa is cut (clear)**. Hasizawa's overhealing is low (28.8%) and Ashiea's is high (52.0%), which is why the notes tie the utility caveat to this decision.
>
> **Why rates matter:** Denixirian healed 252.9k HPS over the 10 pulls attended, the second-highest. Dividing the same healing by all 15 pulls gives 165.2k and would wrongly make Denixirian the lowest healer.

---

## 7. Assign statuses

| Status | Rule |
|---|---|
| **Remove** | Final cuts after steps 3–6 |
| **Reserve** | Next `reserve_count` players (default 2) in each role with cuts; first in if someone is unavailable, or if more cuts are needed |
| **Essential** | Tanks within the tank slots |
| **Keep** | Everyone else, including excluded players (support specs, too few pulls) |
| **Raid Leader** | Protected players; shown as "Not evaluated", with no numbers |

Each Remove gets a **confidence** level (in the CSV; it drives the notes and rationale):

- **Clear:** gap > 5% to the lowest player kept on merit.
- **Supported:** gap 2–5%, with clearly better survival or all secondary metrics favoring the kept player.
- **Close call:** gap < 2%, or 2–5% with secondary metrics that disagree. The peer is recorded, and the decision **must** appear in the notes by name.

---

## 8. Final overall ranking (#1 worst → #N best)

1. Convert each player's in-role order to a **percentile within the role**.
2. Sort all players by percentile, lowest first; tie-break equal percentiles by survival.
3. **Protected players** keep the position their numbers give them, shown greyed out with no numbers.
4. **Excluded players** (support specs, too few pulls) are not comparable, so they follow the ranked players.
5. **Tanks** within their slots are placed at the best end, just below the single top performer, because they cannot be compared on the same scale.
6. When a tie is decided on survival or class, the two tied players swap places so the cut player ranks below the kept one.

Every **Remove** row must rank below every Keep or Reserve in the same role, except players swapped for composition coverage. If not, the run stops with an error instead of producing output.

---

## 9. What goes to the notes

From the decisions above, the Notes section gets (phrasing is in `roster-WRITING-GUIDE.md` §5):

- every **close call**, with both names and what could change it (utility, assignments). If there are no close calls, the tightest cut and the player just above it are named instead.
- **patterns among kept players**: ≥ 3 DPS with damage taken > role mean + 1 SD, or ≥ 3 players with 2+ early deaths
- **data caveats** that could change a verdict: a support spec, a low sample
- **composition risks**, only if they are real (a coverage swap, a lost buff, a short role)

---

## 10. Worked example: the reference raid, run with defaults

| Step | Result |
|---|---|
| Facts | 24 present (2 tanks / 5 healers / 17 DPS); target 20 = 2 / 4 / 14 → cut 1 healer and 3 DPS. Nobody protected |
| Healer cut | Lowest HPS is Hasizawa (216.0k), next is Ashiea (231.3k); normalized gap 5.8% → **Remove Hasizawa (clear)**. Reserve: Ashiea, Nictocin |
| DPS order (lowest normalized DPS first) | Välerjar, Bewitcheress, Windson, Shalammage, Giampanos… (Erythria excluded as a support spec) |
| DPS cut 1 | Välerjar: lowest normalized DPS; 4th Mage → **Remove (clear)** |
| DPS cut 2 | Bewitcheress: lowest raw DPS, lowest item level (318), active 85.5%, second-lowest survival in the raid → **Remove (clear)** |
| DPS cut 3 | Windson vs Shalammage (lowest DPS kept): normalized gap 5.9% → **Remove Windson (clear)**. Reserve: Shalammage, Giampanos |
| Composition check | Fortitude kept (Lifèstream); 3 Mages left; Hunter's Mark kept (Giampanos, Darksaiko); Bloodlust and battle res sources kept → pass |
| Protected from cuts | Nictocin (third-highest HPS, lowest survival in the raid); Panyc, Hiroseer, Darksaiko, Nastra (damage taken flagged) |
| Notes generated | Erythria excluded as Augmentation and Denixirian's 10 of 15 pulls; four DPS with high damage taken; utility not assessed, tied to the Hasizawa / Ashiea call |

**Same raid with `--protect Windson`:** Windson shows as Raid Leader, and the third DPS cut becomes Giampanos (close call vs Shalammage, decided on survival; see §4a). The notes then name the Giampanos / Shalammage decision.

---

## 11. Output checklist for decisions

- [ ] Cuts per role equal `present − slots`, and any short role is reported.
- [ ] No protected player was cut or commented on.
- [ ] Every Remove has a confidence level, and close calls appear in the notes by name.
- [ ] The composition check passed, or swaps are documented.
- [ ] No verdict rests on totals or on pulls a player missed.
- [ ] No top-half performer was cut for a single fixable issue.
- [ ] Remove rows are the lowest-ranked in their role (checked automatically).
- [ ] Limitations are stated: specs as reported by the logs, utility and assignments not assessed, attendance and attitude not in the logs.
