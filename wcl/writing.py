"""Rationales and notes (roster-WRITING-GUIDE.md), written from the signals
in wcl.signals and the decisions recorded by wcl.rank.propose_cuts.

A rationale interprets the numbers (rank, comparison, consequence) instead of
repeating the columns: 2-4 clauses joined by semicolons, decisive factor
first, sentence case, no trailing period. Every rationale that ranks output
says by how much: against the role median and against the cut line, so a
decision can be made (or challenged) from the sentence alone.
"""
from __future__ import annotations

import re
import statistics

from wcl.rank import (
    CONFIDENCE_CLOSE_CALL,
    EXCLUDE_LOW_SAMPLE,
    EXCLUDE_SUPPORT_SPEC,
    FIXED_STATUSES,
    STATUS_ESSENTIAL,
    STATUS_KEEP,
    STATUS_REMOVE,
    STATUS_RESERVE,
    _gap_pct,
    display_class_name,
)
from wcl.signals import compute_signals

ORDINALS = {
    1: "first", 2: "second", 3: "third", 4: "fourth", 5: "fifth",
    6: "sixth", 7: "seventh", 8: "eighth", 9: "ninth", 10: "tenth",
}
ROLE_NOUN = {"healer": "healer", "tank": "tank", "dps": "DPS"}

MAX_CLAUSES = 4
RATIONALE_WORDS = (6, 28)
NOTE_WORDS = (15, 40)
MAX_NOTES = 4
# Judgements, first person and plural hacks are banned everywhere; rationales
# also have no subject pronoun.
BANNED = re.compile(r"\b(bad|poor|lazy|terrible|needs to improve|i|we|my|our)\b|\(s\)|!", re.IGNORECASE)
PRONOUN = re.compile(r"\b(he|she|his|her|they|their|them)\b", re.IGNORECASE)
RANK_WORD = re.compile(r"\d|\b(lowest|highest|higher|top|second|third|fourth|fifth|average|above|below|solid|high|low|on par|main|stable)\b", re.IGNORECASE)


# --- Formatting -----------------------------------------------------------


def _pct(value: float | None) -> str:
    if value is None:
        return "-"
    return "100%" if round(value, 1) >= 100 else f"{value:.1f}%"


def _metric(role: str) -> str:
    return "HPS" if role == "healer" else "DPS"


def _names(names: list[str]) -> str:
    """'A', 'A and B', 'A, B and C' (no serial comma)."""
    if len(names) <= 1:
        return "".join(names)
    return ", ".join(names[:-1]) + " and " + names[-1]


def _sentence(clauses: list[str]) -> str:
    clauses = [c for c in clauses if c][:MAX_CLAUSES]
    while len(clauses) > 1 and len(" ".join(clauses).split()) > RATIONALE_WORDS[1]:
        clauses.pop()
    text = "; ".join(clauses)
    return text[:1].upper() + text[1:]


def _raw_gap(a: dict, b: dict) -> float:
    """How far a's shown output is from b's, as % of b's (signed)."""
    base = b["output"] or 0.0
    return ((a["output"] or 0.0) / base - 1) * 100 if base else 0.0


# --- Clauses --------------------------------------------------------------


def _vs_median(sig: dict, role: str) -> str | None:
    """'12% below the DPS median'; None when the player isn't ranked."""
    if "output_vs_median" not in sig:
        return None
    diff = sig["output_vs_median"] * 100
    if abs(diff) < 1:
        return f"on the {_metric(role)} median"
    return f"{abs(diff):.0f}% {'above' if diff > 0 else 'below'} the {_metric(role)} median"


def _ranked_output(sig: dict, role: str, prefer: str) -> str | None:
    """Rank word plus the distance from the role median."""
    out = _output_phrase(sig, role, prefer)
    median = _vs_median(sig, role)
    if out and median:
        return f"{out}, {median}"
    return out


def _below_floor_clause(row: dict, lines: dict) -> str | None:
    """For a cut: how far below the lowest shown output that stays."""
    floor = lines.get("floor")
    if floor is None:
        return None
    metric = _metric(row["role"])
    gap = _raw_gap(row, floor)
    if gap < 0:
        return f"{abs(gap):.0f}% below the lowest {metric} kept ({floor['name']})"
    # Ahead on the raw number, behind once each pull's difficulty is normalised.
    return f"ahead of {floor['name']} on raw {metric} but behind across comparable pulls"


def _cut_line_clause(row: dict, lines: dict) -> str | None:
    """Distance to the cut line for anyone not being cut: how far above the
    best player cut in the role, or below the lowest kept if they'd be cut."""
    cut_top, floor = lines.get("cut_top"), lines.get("floor")
    if cut_top is None or row is cut_top:
        return None
    metric = _metric(row["role"])
    gap = _raw_gap(row, cut_top)
    if gap >= 0:
        return f"{gap:.0f}% above the cut line ({cut_top['name']})"
    if floor is not None and floor is not row:
        return f"{abs(_raw_gap(row, floor)):.0f}% below the lowest {metric} kept ({floor['name']})"
    return f"{abs(gap):.0f}% below the cut line ({cut_top['name']})"


def _output_phrase(sig: dict, role: str, prefer: str) -> str | None:
    """Rank word for the player's output. `prefer` is "bottom" for players
    near the cut line and "top" for everyone else."""
    if "output_rank_top" not in sig:
        return None
    metric = _metric(role)
    top, bottom = sig["output_rank_top"], sig["output_rank_bottom"]
    if prefer == "bottom" and bottom <= 3:
        return f"{'lowest' if bottom == 1 else ORDINALS[bottom] + '-lowest'} {metric}"
    if top == 1:
        return f"highest {metric}" + (" by a clear margin" if sig.get("clear_lead") else "")
    if top <= 3:
        return f"{ORDINALS[top]}-highest {metric}"
    if prefer != "bottom" and bottom <= 1:
        return f"lowest {metric}"
    diff = sig["output_vs_median"]
    if diff > 0.08:
        return f"high {metric}"
    if diff > 0.03:
        return f"above-average {metric}"
    if diff >= -0.03:
        return f"solid {metric}" if prefer == "top" else f"average {metric}"
    if diff >= -0.08:
        return f"below-average {metric}"
    return f"well below-average {metric}"


def _survival_clause(sig: dict) -> str | None:
    rank = sig.get("survival_rank_bottom")
    if rank is None:
        return None
    if rank == 1:
        return "lowest survival in the raid"
    if rank == 2:
        return "second-lowest survival in the raid"
    if sig.get("survival_bottom_quartile"):
        return "among the higher death rates"
    return None


def _dtaken_clause(sig: dict, role: str) -> str | None:
    if not sig.get("dtaken_high"):
        return None
    amount = f"{sig['damage_taken_m']}m"
    if sig.get("dtaken_highest"):
        detail = f"{amount}, {_pct(sig['mitigated_pct'])} mitigated" if sig.get("mitigation_low") else amount
        return f"highest damage taken among {ROLE_NOUN[role]} ({detail})"
    return f"high damage taken ({amount})"


def _overheal_clause(sig: dict) -> str | None:
    if sig.get("overheal_high"):
        return f"high overhealing ({_pct(sig['overheal_pct'])})"
    if sig.get("overheal_low"):
        return "low overhealing"
    return None


def _class_clause(sig: dict, row: dict) -> str | None:
    if "class_position" not in sig:
        return None
    return f"{ORDINALS.get(sig['class_position'], str(sig['class_position']))} {display_class_name(row['class'])} on the roster"


def _sample_clause(sig: dict) -> str | None:
    if "sample" not in sig:
        return None
    return f"attended {sig['sample'][0]} of {sig['sample'][1]} pulls"


def _weaknesses(sig: dict, row: dict) -> list[str]:
    """Caveats for players who stay, most important first."""
    survival = _survival_clause(sig)
    dtaken = _dtaken_clause(sig, row["role"])
    if survival == "among the higher death rates" and dtaken and dtaken.startswith("high damage"):
        merged = f"high death rate and damage taken ({sig['damage_taken_m']}m)"
        items = [merged]
    else:
        items = [survival, dtaken]
    if row["role"] == "healer" and sig.get("overheal_high"):
        items.append(_overheal_clause(sig))
    if sig.get("lowest_active"):
        items.append("lowest active time")
    items.append(_sample_clause(sig))
    return [i for i in items if i]


# --- Rationale per status -------------------------------------------------


def _remove_rationale(row: dict, sig: dict, rows_by_name: dict, lines: dict) -> str:
    role, metric = row["role"], _metric(row["role"])
    clauses: list[str] = []
    peer = row.get("near_tie_peer")

    if row.get("cut_for_composition"):
        clauses.append(f"cut so the roster keeps {row['cut_for_composition']} coverage")
        clauses.append(_output_phrase(sig, role, "bottom"))
    elif row.get("confidence") == CONFIDENCE_CLOSE_CALL and peer:
        basis = row.get("tie_basis")
        gap = _gap_pct(row, rows_by_name[peer])
        if basis == "survival":
            clauses.append(f"{metric} within {gap:.0f}% of {peer}, but a notably higher death rate")
        elif basis == "class redundancy" and "class_position" in sig:
            clauses.append(f"{metric} within {gap:.0f}% of {peer}, but the {_class_clause(sig, row)}")
        else:
            clauses.append(f"{metric} within {gap:.0f}% of {peer}; close call for the raid leader")
        clauses.append(_vs_median(sig, role))
    elif "slot_position" in sig:
        clauses.append(
            f"{ORDINALS.get(sig['slot_position'], str(sig['slot_position']))} {ROLE_NOUN[role]} "
            f"for {sig['slots']} slots"
        )
        out = _output_phrase(sig, role, "bottom")
        floor = _below_floor_clause(row, lines)
        clauses.append(f"{out}, {floor}" if out and floor else out)
        if role == "healer":
            clauses.append(_overheal_clause(sig))
    else:
        out = _ranked_output(sig, role, "bottom")
        if out and out.startswith("lowest") and sig.get("ilvl_lowest") and sig.get("ilvl_low"):
            out = f"lowest {metric} and item level ({sig['ilvl']:g})"
        clauses.append(out)
        clauses.append(_below_floor_clause(row, lines))

    said = " ".join(filter(None, clauses))
    supporting = [
        _survival_clause(sig) if "death rate" not in said and "survival" not in said else None,
        f"low item level ({sig['ilvl']:g})" if sig.get("ilvl_low") and "item level" not in " ".join(filter(None, clauses)) else None,
        "lowest active time" if sig.get("lowest_active") else None,
        _class_clause(sig, row) if "on the roster" not in " ".join(filter(None, clauses)) else None,
        _dtaken_clause(sig, role),
        _sample_clause(sig),
    ]
    return _sentence([c for c in clauses + supporting if c])


def _reserve_rationale(row: dict, sig: dict, lines: dict) -> str:
    """Why the player is close to the cut line, by how much, then why they stayed."""
    role = row["role"]
    line = _cut_line_clause(row, lines)
    if "slot_position" in sig:
        # In a small role the slot position already says how close the cut was.
        top = sig.get("output_rank_top", 99)
        lead = (
            f"{ORDINALS[top]}-highest {_metric(role)}" if top <= 3
            else f"{ORDINALS.get(sig['slot_position'], str(sig['slot_position']))} {ROLE_NOUN[role]}"
        )
        weakness = _weaknesses(sig, row)
        return _sentence([lead, line] + (weakness[:2] or ["no deaths" if sig.get("no_deaths") else "next in line for a cut"]))

    out = _ranked_output(sig, role, "bottom") or f"{_metric(role)} near the cut line"
    if sig.get("no_deaths"):
        strength = "no deaths"
    elif sig.get("survival_above_role_median"):
        strength = "good survival"
    elif sig.get("dtaken_low"):
        strength = "low damage taken"
    else:
        strength = None
    if strength:
        return _sentence([out, line, f"offset by {strength}"] + _weaknesses(sig, row)[:1])
    return _sentence([out, line] + _weaknesses(sig, row)[:1] + ["next in line for a cut"])


def _strengths(sig: dict) -> list[str]:
    items = []
    if sig.get("no_deaths"):
        items.append("no deaths")
    elif sig.get("survival_above_role_median"):
        items.append("survival above the role median")
    if sig.get("dtaken_low"):
        items.append("low damage taken")
    return items


def _keep_rationale(row: dict, sig: dict, lines: dict) -> str:
    role = row["role"]
    if row.get("kept_for"):
        return _sentence([f"kept for {row['kept_for']} coverage", _ranked_output(sig, role, "bottom"),
                          _cut_line_clause(row, lines)])

    out = _ranked_output(sig, role, "top")
    return _sentence([out, _cut_line_clause(row, lines)] + _weaknesses(sig, row)[:2] + _strengths(sig))


def _fixed_rationale(row: dict, sig: dict, lines: dict) -> str:
    """A fixed player is described as if evaluated, so the raid leader sees
    where the numbers would have put them."""
    if row["role"] == "tank":
        return _essential_rationale(row, sig)
    if row.get("exclude_reason"):
        return _excluded_rationale(row)
    out = _ranked_output(sig, row["role"], "bottom")
    extras = [_class_clause(sig, row)] + _weaknesses(sig, row) + _strengths(sig)
    return _sentence([out, _cut_line_clause(row, lines)] + extras)


def _essential_rationale(row: dict, sig: dict) -> str:
    mitigation = _pct(sig.get("mitigated_pct"))
    survival = _survival_clause(sig) or ("no deaths" if sig.get("no_deaths") else None)
    if sig.get("tank_load_top"):
        return _sentence(["main tank", f"absorbs most damage in raid ({sig['damage_taken_m']}m), {mitigation} mitigation",
                          survival])
    share = f"{sig.get('tank_share', 0.0) * 100:.0f}% of tank damage"
    return _sentence([f"off-tank, took {sig['damage_taken_m']}m ({share}) with {mitigation} mitigation", survival])


def _excluded_rationale(row: dict) -> str:
    if row["exclude_reason"] == EXCLUDE_SUPPORT_SPEC:
        return _sentence([
            f"{row['spec']} {display_class_name(row['class'])}",
            "own DPS understates contribution, excluded from the cut",
        ])
    return _sentence([
        f"attended {row['pulls_attended']} of {row['total_pulls']} pulls",
        "too few to rank, excluded from the cut",
    ])


def write_rationale(row: dict, sig: dict, rows_by_name: dict, lines: dict | None = None) -> str:
    status = row["status"]
    lines = lines or {}
    if status in FIXED_STATUSES:
        return _fixed_rationale(row, sig, lines)
    if status == STATUS_ESSENTIAL:
        return _essential_rationale(row, sig)
    if row.get("exclude_reason"):
        return _excluded_rationale(row)
    if status == STATUS_REMOVE:
        return _remove_rationale(row, sig, rows_by_name, lines)
    if status == STATUS_RESERVE:
        return _reserve_rationale(row, sig, lines)
    return _keep_rationale(row, sig, lines)


def cut_lines(rows: list[dict]) -> dict[str, dict]:
    """Per role: the best player cut on merit ("cut_top") and the lowest kept
    on merit ("floor") - the two sides of the cut line."""
    lines: dict[str, dict] = {}
    for role in ("tank", "healer", "dps"):
        cuts = [r for r in rows if r["role"] == role and r["status"] == STATUS_REMOVE
                and not r.get("cut_for_composition")]
        kept = [r for r in rows if r["role"] == role and r["status"] in (STATUS_KEEP, STATUS_RESERVE)
                and not r.get("exclude_reason") and not r.get("kept_for")]
        lines[role] = {
            "cut_top": max(cuts, key=lambda r: r["output"] or 0.0) if cuts else None,
            "floor": min(kept, key=lambda r: r["output"] or 0.0) if kept else None,
        }
    return lines


# --- Notes ----------------------------------------------------------------


def _caveat_note(rows: list[dict]) -> str | None:
    """Category 1: data caveats that could change a verdict."""
    support = [r for r in rows if r.get("exclude_reason") == EXCLUDE_SUPPORT_SPEC]
    thin = [r for r in rows if r.get("exclude_reason") == EXCLUDE_LOW_SAMPLE]
    marked = [r for r in rows if r.get("low_sample") and not r.get("exclude_reason")]
    parts = []
    if support:
        specs = _names([f"{r['name']} ({r['spec']})" for r in support])
        parts.append(f"{specs} {'is' if len(support) == 1 else 'are'} excluded as the spec's value shows in others' damage")
    by_attendance: dict[tuple[int, int], list[str]] = {}
    for r in thin + marked:
        by_attendance.setdefault((r["pulls_attended"], r["total_pulls"]), []).append(r["name"])
    for (attended, total), names in sorted(by_attendance.items()):
        parts.append(f"{_names(names)} attended only {attended} of {total} pulls")
    if not parts:
        return None
    subject = "both verdicts" if len(parts) == 2 else ("these verdicts" if len(parts) > 2 else "that verdict")
    return f"{'; '.join(parts)}; {subject} should be confirmed by the raid leader."


def _pattern_note(rows: list[dict]) -> str | None:
    """Category 2: an actionable pattern among players who stay."""
    staying = [r for r in rows if r["status"] in (STATUS_KEEP, STATUS_RESERVE) and not r.get("exclude_reason")]
    dps_pool = [r for r in rows if r["role"] == "dps" and not r.get("exclude_reason")]
    heavy = sorted(
        (r for r in staying if r["role"] == "dps" and r.get("high_damage_taken")),
        key=lambda r: -(r["damage_taken_total"] or 0),
    )
    if len(heavy) >= 3 and dps_pool:
        avg = statistics.mean((r["damage_taken_total"] or 0) for r in dps_pool) / 1e6
        return (
            f"{_names([r['name'] for r in heavy])} take well above the DPS average (~{avg:.0f}m); "
            "an ability breakdown may reveal avoidable damage."
        )
    early = [r for r in staying if (r.get("early_deaths") or 0) >= 2]
    if len(early) >= 3:
        return (
            f"{_names([r['name'] for r in early])} each died early in two or more pulls; "
            "reviewing those deaths may reveal a shared mechanic to fix."
        )
    return None


def _decision_pairs(rows: list[dict]) -> list[tuple[str, str]]:
    """Every close call; if there are none, the tightest cut in each role
    against the lowest player kept on merit."""
    pairs = [
        (r["name"], r["near_tie_peer"]) for r in rows
        if r["status"] == STATUS_REMOVE and r.get("confidence") == CONFIDENCE_CLOSE_CALL and r.get("near_tie_peer")
    ]
    if pairs:
        return pairs
    tightest = None
    for role in ("dps", "healer", "tank"):
        cuts = [r for r in rows if r["role"] == role and r["status"] == STATUS_REMOVE and not r.get("cut_for_composition")]
        kept = [r for r in rows if r["role"] == role and r["status"] in (STATUS_KEEP, STATUS_RESERVE)
                and not r.get("exclude_reason") and not r.get("kept_for")]
        if not cuts or not kept:
            continue
        cut = max(cuts, key=lambda r: r["output_norm"] or 0.0)
        floor = min(kept, key=lambda r: r.get("role_percentile", 0.5))
        gap = _gap_pct(cut, floor)
        if tightest is None or gap < tightest[0]:
            tightest = (gap, cut["name"], floor["name"])
    return [tightest[1:]] if tightest else []


def _unmeasured_note(rows: list[dict]) -> str | None:
    """Category 3: what the analysis did not measure, tied to a named decision."""
    pairs = _decision_pairs(rows)
    if not pairs:
        return None
    decisions = _names([f"{a} / {b}" for a, b in pairs])
    noun = "decision" if len(pairs) == 1 else "decisions"
    return (
        "Utility (interrupts, dispels, assignments) was not assessed "
        f"and may inform the {decisions} {noun}."
    )


def _composition_note(rows: list[dict], events: list[str]) -> str | None:
    """Category 4: composition risk after the cuts, only when real."""
    kept_for = [r for r in rows if r.get("kept_for")]
    if kept_for:
        r = kept_for[0]
        swapped = next((x["name"] for x in rows if x.get("cut_for_composition") == r["kept_for"]), None)
        return (
            f"{r['name']} was kept instead of {swapped} because the cut would leave the roster short of "
            f"{r['kept_for']}; the swap should be confirmed by the raid leader."
        )
    return events[0] if events else None


def _limitation_note(rows: list[dict]) -> str:
    removed = [r["name"] for r in rows if r["status"] == STATUS_REMOVE]
    if not removed:
        return (
            "Attendance history, communication and attitude are not in the logs "
            "and should be weighed alongside these results."
        )
    return (
        "Attendance history, communication and attitude are not in the logs "
        f"and should be weighed before the {_names(removed)} cuts are confirmed."
    )


def write_notes(rows: list[dict], events: list[str]) -> list[str]:
    """2-4 bullets, one per category, in priority order (guide §5)."""
    visible = rows
    notes = [
        n for n in (
            _caveat_note(visible),
            _pattern_note(visible),
            _unmeasured_note(visible),
            _composition_note(visible, events),
        ) if n
    ]
    if len(notes) < 2:
        notes.append(_limitation_note(visible))
    return notes[:MAX_NOTES]


# --- Self-review (guide §9, the checks that can be automated) -------------


def validate_rationale(text: str, row: dict) -> list[str]:
    problems = []
    words = len(text.split())
    if not RATIONALE_WORDS[0] <= words <= RATIONALE_WORDS[1]:
        problems.append(f"{row['name']}: {words} words (want {RATIONALE_WORDS[0]}-{RATIONALE_WORDS[1]})")
    if text.endswith("."):
        problems.append(f"{row['name']}: trailing period")
    if text[:1] != text[:1].upper():
        problems.append(f"{row['name']}: not sentence case")
    if BANNED.search(text) or PRONOUN.search(text):
        problems.append(f"{row['name']}: banned wording in '{text}'")
    # Nothing to rank for a spec the data can't measure.
    exempt = row.get("exclude_reason") == EXCLUDE_SUPPORT_SPEC
    if not exempt and not RANK_WORD.search(text):
        problems.append(f"{row['name']}: no rank word or number")
    return problems


def validate_note(text: str) -> list[str]:
    problems = []
    words = len(text.split())
    if not NOTE_WORDS[0] <= words <= NOTE_WORDS[1]:
        problems.append(f"note has {words} words (want {NOTE_WORDS[0]}-{NOTE_WORDS[1]}): {text}")
    if not text.endswith("."):
        problems.append(f"note does not end with a period: {text}")
    if BANNED.search(text):
        problems.append(f"banned wording in note: {text}")
    return problems


def write_texts(rows: list[dict], events: list[str], composition: dict[str, int], config: dict) -> dict:
    """Fill every row's rationale and build the notes. Returns
    {"notes": [...], "problems": [...]} - problems are self-review failures."""
    signals = compute_signals(rows, composition, config)
    rows_by_name = {r["name"]: r for r in rows}
    lines = cut_lines(rows)
    problems: list[str] = []
    for row in rows:
        row["rationale"] = write_rationale(row, signals.get(row["name"], {}), rows_by_name, lines[row["role"]])
        problems += validate_rationale(row["rationale"], row)
    notes = write_notes(rows, events)
    for note in notes:
        problems += validate_note(note)
    return {"notes": notes, "problems": problems}
