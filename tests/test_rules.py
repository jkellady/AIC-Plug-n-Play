"""Tests for src/rules.py, using a small synthetic rules.toml fixture."""
from pathlib import Path

import pytest

from src.rules import (
    RuleValidationError,
    UnknownRuleError,
    load_rules,
)

SYNTHETIC_TOML = """
[meta]
schema_version = 1
allowed_status = ["CONFIRMED", "ASSUMED", "OPEN"]

[rules.fake_confirmed]
label = "A confirmed fake rule"
value = 10
status = "CONFIRMED"
question = "F1"
source = "Fake source"

[rules.fake_assumed]
label = "An assumed fake rule"
value = "x"
status = "ASSUMED"
question = "F2"
source = ""

[rules.fake_open]
label = "An open fake rule"
value = "a"
options = ["a", "b", "c"]
status = "OPEN"
question = "F3"
source = ""
"""


def _write_toml(path: Path, text: str) -> Path:
    toml_path = path / "rules.toml"
    toml_path.write_text(text, encoding="utf-8")
    return toml_path


def test_get_status_question(tmp_path):
    rules = load_rules(_write_toml(tmp_path, SYNTHETIC_TOML))
    assert rules.get("fake_confirmed") == 10
    assert rules.status("fake_confirmed") == "CONFIRMED"
    assert rules.question("fake_open") == "F3"


def test_is_confirmed_true_only_if_all_confirmed(tmp_path):
    rules = load_rules(_write_toml(tmp_path, SYNTHETIC_TOML))
    assert rules.is_confirmed(["fake_confirmed"]) is True
    assert rules.is_confirmed(["fake_confirmed", "fake_assumed"]) is False
    assert rules.is_confirmed(["fake_assumed", "fake_open"]) is False


def test_unknown_rule_id_raises_for_get(tmp_path):
    rules = load_rules(_write_toml(tmp_path, SYNTHETIC_TOML))
    with pytest.raises(UnknownRuleError):
        rules.get("does_not_exist")


def test_unknown_rule_id_raises_for_status(tmp_path):
    rules = load_rules(_write_toml(tmp_path, SYNTHETIC_TOML))
    with pytest.raises(UnknownRuleError):
        rules.status("does_not_exist")


def test_unknown_rule_id_raises_for_question(tmp_path):
    rules = load_rules(_write_toml(tmp_path, SYNTHETIC_TOML))
    with pytest.raises(UnknownRuleError):
        rules.question("does_not_exist")


def test_unknown_rule_id_raises_for_is_confirmed_never_returns_true_silently(tmp_path):
    rules = load_rules(_write_toml(tmp_path, SYNTHETIC_TOML))
    with pytest.raises(UnknownRuleError):
        rules.is_confirmed(["fake_confirmed", "does_not_exist"])


def test_confirmed_without_source_fails_validation(tmp_path):
    bad_toml = SYNTHETIC_TOML.replace(
        'status = "CONFIRMED"\nquestion = "F1"\nsource = "Fake source"',
        'status = "CONFIRMED"\nquestion = "F1"\nsource = ""',
    )
    with pytest.raises(RuleValidationError):
        load_rules(_write_toml(tmp_path, bad_toml))


def test_status_not_in_allowed_status_fails_validation(tmp_path):
    bad_toml = SYNTHETIC_TOML.replace('status = "ASSUMED"', 'status = "MAYBE"')
    with pytest.raises(RuleValidationError):
        load_rules(_write_toml(tmp_path, bad_toml))


def test_value_not_in_options_fails_validation(tmp_path):
    bad_toml = SYNTHETIC_TOML.replace('value = "a"\noptions', 'value = "z"\noptions')
    with pytest.raises(RuleValidationError):
        load_rules(_write_toml(tmp_path, bad_toml))


def test_changing_value_in_file_changes_get_result_with_no_code_change(tmp_path):
    rules_before = load_rules(_write_toml(tmp_path, SYNTHETIC_TOML))
    assert rules_before.get("fake_confirmed") == 10

    changed_toml = SYNTHETIC_TOML.replace(
        '[rules.fake_confirmed]\nlabel = "A confirmed fake rule"\nvalue = 10',
        '[rules.fake_confirmed]\nlabel = "A confirmed fake rule"\nvalue = 99',
    )
    rules_after = load_rules(_write_toml(tmp_path, changed_toml))
    assert rules_after.get("fake_confirmed") == 99


def test_find_unread_rules_flags_label_without_a_reader(tmp_path):
    from src.rules import find_unread_rules
    src = tmp_path / "src"
    src.mkdir()
    (src / "engine.py").write_text('x = rules.get("read_rule")\n', encoding="utf-8")
    toml = tmp_path / "rules.toml"
    toml.write_text(
        '[meta]\nallowed_status = ["ASSUMED"]\n'
        '[rules.read_rule]\nvalue = 1\nstatus = "ASSUMED"\nused_in = "day engine"\n'
        '[rules.ghost_rule]\nvalue = 1\nstatus = "ASSUMED"\nused_in = "day engine"\n'
        '[rules.later_rule]\nvalue = 1\nstatus = "ASSUMED"\nused_in = "not built yet"\n',
        encoding="utf-8",
    )
    assert find_unread_rules(toml, src) == ["ghost_rule"]


def test_real_rules_labels_match_code():
    from src.rules import find_unread_rules
    assert find_unread_rules() == []

