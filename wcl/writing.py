"""Rationales and notes (roster-WRITING-GUIDE.md), written from the signals
in wcl.signals and the decisions recorded by wcl.rank.propose_cuts.

A rationale interprets the numbers (rank, comparison, consequence) instead of
repeating the columns: 2-3 clauses joined by semicolons, decisive factor
first, sentence case, no trailing period.
"""
from __future__ import annotations

import re
import statistics

from wcl.rank import (
    CONFIDENCE_CLOSE_CALL,
    EXCLUDE_LOW_SAMPLE,
    EXCLUDE_SUPPORT_SPEC,
    PROTECTED_RATIONALE,
    STATUS_ESSENTIAL,
    STATUS_KEEP,
    STATUS_NOT_EVALUATED,
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

MAX_CLAUSES = 3
RATIONALE_WORDS = (3, 16)
NOTE_WORDS = (15, 30)
MAX_NOTES = 4
UNREMARKABLE = "Consistent, reliable performance"
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


# --- Clauses --------------------------------------------------------------


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


def _remove_rationale(row: dict, sig: dict, rows_by_name: dict) -> str:
    role, metric = row["role"], _metric(row["role"])
    clauses: list[str] = []
    peer = row.get("near_tie_peer")

    if row.get("cut_for_composition"):
        clauses.append(f"cut so the roster keeps {row['cut_for_composition']} coverage")
        clauses.append(_output_phrase(sig, role, "bottom"))
    elif row.get("confidence") == CONFIDENCE_CLOSE_CALL and peer:
        basis = row.get("tie_basis")
        if basis == "survival":
            clauses.append(f"{metric} on par with {peer}, but a notably higher death rate")
        elif basis == "class redundancy" and "class_position" in sig:
            clauses.append(f"{metric} on par with {peer}, but the {_class_clause(sig, row)}")
        else:
            gap = _gap_pct(row, rows_by_name[peer])
            clauses.append(f"{metric} within {gap:.0f}% of {peer}; close call for the raid leader")
    elif "slot_position" in sig:
        clauses.append(f"{ORDINALS.get(sig['slot_position'], str(sig['slot_position']))} {ROLE_NOUN[role]}")
        out = _output_phrase(sig, role, "bottom")
        floor = _merit_floor(row, rows_by_name)
        if out and floor:
            out += f", {_gap_pct(row, floor):.0f}% below {floor['name']}"
        clauses.append(out)
        if role == "healer":
            clauses.append(_overheal_clause(sig))
    else:
        out = _output_phrase(sig, role, "bottom")
        if out and out.startswith("lowest") and sig.get("ilvl_lowest") and sig.get("ilvl_low"):
            out = f"lowest {metric} and item level ({sig['ilvl']:g})"
        clauses.append(out)

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


def _reserve_rationale(row: dict, sig: dict) -> str:
    """Why the player is close to the cut line, then why they stayed."""
    role = row["role"]
    if "slot_position" in sig:
        # In a small role the slot position already says how close the cut was.
        top = sig.get("output_rank_top", 99)
        lead = (
            f"{ORDINALS[top]}-highest {_metric(role)}" if top <= 3
            else f"{ORDINALS.get(sig['slot_position'], str(sig['slot_position']))} {ROLE_NOUN[role]}"
        )
        weakness = _weaknesses(sig, row)
        return _sentence([lead] + (weakness[:1] or ["no deaths" if sig.get("no_deaths") else "next in line for a cut"]))

    out = _output_phrase(sig, role, "bottom") or f"{_metric(role)} near the cut line"
    if sig.get("no_deaths"):
        strength = "no deaths"
    elif sig.get("survival_above_role_median"):
        strength = "good survival"
    elif sig.get("dtaken_low"):
        strength = "low damage taken"
    else:
        strength = None
    if strength:
        return _sentence([f"{out} offset by {strength}"] + _weaknesses(sig, row)[:1])
    return _sentence([out] + _weaknesses(sig, row)[:1] + ["next in line for a cut"])


def _merit_floor(row: dict, rows_by_name: dict) -> dict | None:
    """Lowest player in the role kept on merit (not protected, excluded or kept for coverage)."""
    kept = [
        r for r in rows_by_name.values()
        if r["role"] == row["role"] and r["status"] in (STATUS_KEEP, STATUS_RESERVE)
        and not r.get("exclude_reason") and not r.get("kept_for")
    ]
    return min(kept, key=lambda r: r.get("role_percentile", 0.5)) if kept else None


def _keep_rationale(row: dict, sig: dict) -> str:
    role = row["role"]
    if row.get("kept_for"):
        return _sentence([f"kept for {row['kept_for']} coverage", _output_phrase(sig, role, "bottom")])

    out = _output_phrase(sig, role, "top")
    weaknesses = _weaknesses(sig, row)
    if weaknesses:
        return _sentence([out] + weaknesses[:2])

    strong = out and not out.startswith(("solid", "average", "below", "well below", "lowest"))
    if strong:
        if sig.get("no_deaths") and sig.get("dtaken_low") and out.endswith(("DPS", "HPS")):
            return _sentence([out, "no deaths", "low damage taken"])
        if sig.get("no_deaths"):
            return _sentence([f"{out}, with no deaths" if "margin" in out else f"{out} with no deaths"])
        return _sentence([out])
    if sig.get("no_deaths") and out and out.startswith("solid"):
        return _sentence([out, "no deaths"])
    return UNREMARKABLE


def _essential_rationale(row: dict, sig: dict) -> str:
    mitigation = _pct(sig.get("mitigated_pct"))
    if sig.get("tank_load_top"):
        return _sentence([f"main tank", f"absorbs most damage in raid ({sig['damage_taken_m']}m), {mitigation} mitigation"])
    clauses = [f"stable tanking with {mitigation} mitigation"]
    if sig.get("survival_rank_bottom"):
        clauses.append(_survival_clause(sig))
    return _sentence(clauses)


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


def write_rationale(row: dict, sig: dict, rows_by_name: dict) -> str:
    status = row["status"]
    if status == STATUS_NOT_EVALUATED:
        return PROTECTED_RATIONALE
    if status == STATUS_ESSENTIAL:
        return _essential_rationale(row, sig)
    if row.get("exclude_reason"):
        return _excluded_rationale(row)
    if status == STATUS_REMOVE:
        return _remove_rationale(row, sig, rows_by_name)
    if status == STATUS_RESERVE:
        return _reserve_rationale(row, sig)
    return _keep_rationale(row, sig)


# --- Notes ----------------------------------------------------------------


def _visible_rows(rows: list[dict]) -> list[dict]:
    return [r for r in rows if r["status"] != STATUS_NOT_EVALUATED]


def _caveat_note(rows: list[dict]) -> str | None:
    """Category 1: data caveats that could change a verdict."""
    support = [r for r in rows if r.get("exclude_reason") == EXCLUDE_SUPPORT_SPEC]
    thin = [r for r in rows if r.get("exclude_reason") == EXCLUDE_LOW_SAMPLE]
    marked = [r for r in rows if r.get("low_sample") and not r.get("exclude_reason")]
    parts = []
    if support:
        specs = _names([f"{r['name']} ({r['spec']})" for r in support])
        parts.append(f"{specs} {'was' if len(support) == 1 else 'were'} excluded because the spec's value shows in other players' damage")
    for r in thin + marked:
        parts.append(f"{r['name']} attended only {r['pulls_attended']} of {r['total_pulls']} pulls")
    if not parts:
        return None
    subject = "both verdicts" if len(parts) == 2 else ("these verdicts" if len(parts) > 2 else "that verdict")
    return f"{_names(parts)}; {subject} should be confirmed by the raid leader."


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
    visible = _visible_rows(rows)
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
    if row["status"] == STATUS_NOT_EVALUATED:
        return [] if text == PROTECTED_RATIONALE else [f"{row['name']}: protected player must read '{PROTECTED_RATIONALE}'"]
    words = len(text.split())
    if not RATIONALE_WORDS[0] <= words <= RATIONALE_WORDS[1]:
        problems.append(f"{row['name']}: {words} words (want {RATIONALE_WORDS[0]}-{RATIONALE_WORDS[1]})")
    if text.endswith("."):
        problems.append(f"{row['name']}: trailing period")
    if text[:1] != text[:1].upper():
        problems.append(f"{row['name']}: not sentence case")
    if BANNED.search(text) or PRONOUN.search(text):
        problems.append(f"{row['name']}: banned wording in '{text}'")
    # Nothing to rank for an unremarkable player or a spec the data can't measure.
    exempt = text == UNREMARKABLE or row.get("exclude_reason") == EXCLUDE_SUPPORT_SPEC
    if not exempt and not RANK_WORD.search(text):
        problems.append(f"{row['name']}: no rank word or number")
    return problems


def validate_note(text: str, protected_names: set[str]) -> list[str]:
    problems = []
    words = len(text.split())
    if not NOTE_WORDS[0] <= words <= NOTE_WORDS[1]:
        problems.append(f"note has {words} words (want {NOTE_WORDS[0]}-{NOTE_WORDS[1]}): {text}")
    if not text.endswith("."):
        problems.append(f"note does not end with a period: {text}")
    if BANNED.search(text):
        problems.append(f"banned wording in note: {text}")
    for name in protected_names:
        if name in text:
            problems.append(f"note mentions protected player {name}")
    return problems


def write_texts(rows: list[dict], events: list[str], composition: dict[str, int], config: dict) -> dict:
    """Fill every row's rationale and build the notes. Returns
    {"notes": [...], "problems": [...]} - problems are self-review failures."""
    signals = compute_signals(rows, composition, config)
    rows_by_name = {r["name"]: r for r in rows}
    problems: list[str] = []
    for row in rows:
        row["rationale"] = write_rationale(row, signals.get(row["name"], {}), rows_by_name)
        problems += validate_rationale(row["rationale"], row)
    notes = write_notes(rows, events)
    protected = {r["name"] for r in rows if r["status"] == STATUS_NOT_EVALUATED}
    for note in notes:
        problems += validate_note(note, protected)
    return {"notes": notes, "problems": problems}
