"""Commentary: words for facts the code has already computed.

Facts (counts, shares, evaluations) always come from deterministic code. A *commentator* only
phrases them, and whatever it writes is checked against the facts before it is shown: a number
that is not in the facts is rejected. The template commentator below is the baseline; a hosted
model or a local model can be plugged in through the same interface and must pass the same check."""

from __future__ import annotations

import re
from typing import Protocol

from . import principles

_RANK = {"high": 0, "medium": 1, "low": 2}


# ---- advice from the knowledge base ------------------------------------------------------
def advice_for(cause: str, limit: int = 3) -> list[dict]:
    """The most important principles for a cause, as short advice with their source."""
    books = principles.sources()
    chosen = [r for r in principles.load_all() if cause in r.get("relevant_to", []) and r.get("advice")]
    chosen.sort(key=lambda r: (r.get("priority", 5), _RANK[r["confidence"]], r["id"]))
    return [{
        "text": r["advice"], "principle_id": r["id"],
        "source": {"author": books[r["source"]["book"]]["author"], "title": books[r["source"]["book"]]["title"],
                   "location": r["source"]["location"]},
    } for r in chosen[:limit]]


# ---- facts -------------------------------------------------------------------------------
def build_facts(plan: dict, profile: dict | None = None) -> dict:
    """The numbers a commentator may use, taken from the coach plan and the decisive-blunder profile."""
    focus = plan["weekly_focus"]["area"]
    cause = plan["why_games_were_not_won"][focus]
    facts = {
        "focus": focus,
        "focus_games": cause["games"],
        "games_not_won": plan["games_not_won"],
        "recent_games": plan["recent_window"]["games"],
        "focus_share_pct": round(100 * cause["share_of_non_wins"]),
    }
    if profile and profile.get("games"):
        facts["profile_games"] = profile["games"]
        facts["immediate_loss_pct"] = round(100 * profile["material_or_mate_share"])
        if profile.get("relaxed_clock_share") is not None:
            facts["relaxed_clock_pct"] = round(100 * profile["relaxed_clock_share"])
    return facts


_NUMBER = re.compile(r"\d+(?:\.\d+)?")


def faithfulness(text: str, facts: dict, names: tuple[str, ...] = ()) -> dict:
    """Every number in `text` must be one of the facts (as written, or rounded to a whole number).
    `names` are proper names the text cites (book titles); a digit inside one is not a claim."""
    for name in names:
        text = text.replace(name, "")
    allowed: set[str] = set()
    for v in facts.values():
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            allowed.add(f"{v:g}")
            allowed.add(str(round(v)))
    unsupported = [n for n in _NUMBER.findall(text) if n not in allowed]
    return {"ok": not unsupported, "unsupported_numbers": unsupported}


# ---- commentators ------------------------------------------------------------------------
class Commentator(Protocol):
    name: str

    def comment(self, facts: dict, advice: list[dict]) -> str: ...


class TemplateCommentator:
    """Deterministic baseline: fixed sentences filled with the facts."""

    name = "template"

    _OPENING = {
        "thrown_win": "{focus_games} of your {games_not_won} recent games that were not won started from a winning position.",
        "blunder": "{focus_games} of your {games_not_won} recent games that were not won turned on one costly mistake.",
        "opening": "{focus_games} of your {games_not_won} recent games that were not won were already clearly worse after the opening.",
        "time": "{focus_games} of your {games_not_won} recent games that were not won were lost on the clock with the position still alive.",
        "gradual": "{focus_games} of your {games_not_won} recent games that were not won were a slow decline without one decisive mistake.",
    }

    def comment(self, facts: dict, advice: list[dict]) -> str:
        parts = [self._OPENING[facts["focus"]].format(**facts)]
        if "immediate_loss_pct" in facts:
            parts.append(
                f"Across {facts['profile_games']} such games in your history, the opponent's very next move won material "
                f"or mated after {facts['immediate_loss_pct']}% of your decisive blunders"
                + (f", and in {facts['relaxed_clock_pct']}% you still had plenty of time" if "relaxed_clock_pct" in facts else "")
                + ": the pattern points at a checking habit rather than at speed.")
        if advice:
            a = advice[0]["source"]
            parts.append(f"This is the ground {a['author']} covers in {a['title']}; the first habit to practise is listed below.")
        return " ".join(parts)


BACKENDS: dict[str, Commentator] = {"template": TemplateCommentator()}


def generate(plan: dict, profile: dict | None = None, backend: str = "template") -> dict:
    """Commentary for the weekly focus. Output that fails the faithfulness check is replaced by the
    template baseline, and the rejection is reported rather than hidden."""
    facts = build_facts(plan, profile)
    advice = advice_for(facts["focus"])
    text = BACKENDS[backend].comment(facts, advice)
    titles = tuple(a["source"]["title"] for a in advice)
    check = faithfulness(text, facts, titles)
    result = {"backend": backend, "text": text, "faithfulness": check, "facts": facts}
    if not check["ok"]:
        result["rejected_text"] = text
        result["backend"] = "template"
        result["text"] = BACKENDS["template"].comment(facts, advice)
        result["faithfulness"] = faithfulness(result["text"], facts, titles)
    return result
