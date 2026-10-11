import pytest

from chess_coach import commentary as c

PLAN = {
    "weekly_focus": {"area": "thrown_win"},
    "why_games_were_not_won": {"thrown_win": {"games": 19, "share_of_non_wins": 0.37}},
    "games_not_won": 52,
    "recent_window": {"games": 100},
}
PROFILE = {"games": 886, "material_or_mate_share": 0.46, "relaxed_clock_share": 0.92}


def test_advice_is_ordered_by_priority_and_names_its_source():
    items = c.advice_for("thrown_win", limit=3)
    # Priority 1 comes first; between equals the higher-confidence principle leads (lft-017 is "high", lft-010 "medium").
    assert [i["principle_id"] for i in items[:2]] == ["lft-017", "lft-010"]
    assert items[0]["source"]["author"] == "Dan Heisman"
    assert items[0]["text"] and "Looking for Trouble" in items[0]["source"]["title"]


def test_a_cause_without_literature_coverage_has_no_advice_yet():
    assert c.advice_for("time") == []
    assert c.advice_for("opening") == []


def test_facts_come_only_from_the_plan_and_the_profile():
    f = c.build_facts(PLAN, PROFILE)
    assert (f["focus_games"], f["games_not_won"], f["focus_share_pct"]) == (19, 52, 37)
    assert (f["profile_games"], f["immediate_loss_pct"], f["relaxed_clock_pct"]) == (886, 46, 92)
    assert "profile_games" not in c.build_facts(PLAN, None)
    assert "profile_games" not in c.build_facts(PLAN, {"games": 0})


def test_faithfulness_accepts_supplied_numbers_and_rejects_invented_ones():
    facts = {"focus_games": 19, "share": 36.7}
    assert c.faithfulness("19 games, 36.7 percent, about 37", facts) == {"ok": True, "unsupported_numbers": []}
    bad = c.faithfulness("In 87% of 19 games", facts)
    assert bad == {"ok": False, "unsupported_numbers": ["87"]}


def test_the_template_commentary_is_faithful_and_cites_the_evidence():
    out = c.generate(PLAN, PROFILE)
    assert out["backend"] == "template" and out["faithfulness"]["ok"]
    assert "19 of your 52" in out["text"] and "46%" in out["text"] and "92%" in out["text"]
    assert "Heisman" in out["text"] or "Dan Heisman" in out["text"]


@pytest.mark.parametrize("area", ["thrown_win", "blunder", "opening", "time", "gradual"])
def test_every_cause_has_a_faithful_sentence(area):
    plan = {**PLAN, "weekly_focus": {"area": area}, "why_games_were_not_won": {area: {"games": 7, "share_of_non_wins": 0.5}}}
    out = c.generate(plan, None)
    assert out["faithfulness"]["ok"] and "7 of your 52" in out["text"]


def test_a_backend_that_invents_numbers_is_rejected_and_replaced(monkeypatch):
    class Liar:
        name = "liar"

        def comment(self, facts, advice):
            return "You lost 99% of your games from winning positions."

    monkeypatch.setitem(c.BACKENDS, "liar", Liar())
    out = c.generate(PLAN, PROFILE, backend="liar")
    assert out["backend"] == "template" and out["faithfulness"]["ok"]
    assert out["rejected_text"] == "You lost 99% of your games from winning positions."
