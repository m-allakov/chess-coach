import json
from pathlib import Path

import pytest

from chess_coach import principles as p

ROOT = Path(__file__).resolve().parents[1]
BOOKS = {b["key"] for b in json.loads((ROOT / "docs/pedagogy/sources.json").read_text(encoding="utf-8"))["books"]}


def good(**over):
    rec = {"id": "tst-001", "claim": "Check what the last move changed before replying.", "kind": "process_step",
           "source": {"book": "heisman-looking-for-trouble", "location": "Introduction"},
           "evidence": "author_claim", "status": "candidate", "confidence": "high",
           "app_hooks": ["drill.threat_check"], "testable_prediction": None}
    return {**rec, **over}


def test_a_well_formed_record_is_valid():
    assert p.validate_record(good(), BOOKS) == []


@pytest.mark.parametrize("over, fragment", [
    ({"id": "bad id"}, "id must look like"),
    ({"kind": "opinion"}, "kind must be one of"),
    ({"claim": "word " * 80}, "longer than"),
    ({"source": {"book": "no-such-book", "location": "x"}}, "unknown source book"),
    ({"app_hooks": ["drill.nonexistent"]}, "unknown app hook"),
    ({"status": "untestable", "testable_prediction": "something"}, "untestable principle must not"),
    ({"quote": "one two three four five six seven eight nine ten eleven twelve thirteen"}, "quote is longer"),
    ({"audience": {"min_rating": 2000, "max_rating": 1000}}, "min <= max"),
])
def test_invalid_records_are_reported(over, fragment):
    assert any(fragment in e for e in p.validate_record(good(**over), BOOKS))


def test_missing_required_fields_are_reported():
    rec = good()
    del rec["claim"]
    assert p.validate_record(rec) == ["missing field: claim"]


def test_duplicate_ids_and_dangling_conflicts_are_found(tmp_path):
    f = tmp_path / "x.jsonl"
    a, b = good(), good(conflicts_with=["tst-099"])
    f.write_text(json.dumps(a) + "\n" + json.dumps(b) + "\n", encoding="utf-8")
    found = p.validate_file(f, BOOKS)
    assert any("duplicate id" in e for e in found["tst-001"])
    assert any("unknown id tst-099" in e for e in found["tst-001"])


def test_unparseable_lines_are_reported(tmp_path):
    f = tmp_path / "x.jsonl"
    f.write_text("{not json}\n", encoding="utf-8")
    assert p.validate_file(f)["line 1"] == ["not valid JSON"]


SOURCE = ("The remedy is simple enough: before every move, imagine passing, and ask what the opponent "
          "would do with the extra tempo, because the checks and captures matter most.")


def test_copy_check_catches_a_lifted_sentence_and_ignores_a_paraphrase():
    lifted = "Players should imagine passing, and ask what the opponent would do with the extra tempo today."
    own_words = "Picture yourself skipping a turn and see which forcing replies the other side gains."
    assert p.shared_run(lifted, SOURCE, 8) == "imagine passing and ask what the opponent would"
    assert p.shared_run(own_words, SOURCE, 8) is None


def test_every_shipped_principle_file_is_valid():
    files = sorted((ROOT / "docs/pedagogy/principles").glob("*.jsonl"))
    assert files, "the pilot principle file should exist"
    for f in files:
        assert p.validate_file(f, BOOKS) == {}, f.name
