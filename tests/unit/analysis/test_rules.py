"""The application rule catalogue (CIS §8.3-§8.5, §12.6, §13.2).

Built on the M0 tool-contract verification (tests/contract): that proves the CIS rule sets
against the pinned tools; these tests prove the catalogue implements exactly those rule sets.
"""

import configparser

import pytest
from bandit.core import extension_loader

from analysis.python import rules
from shared.domain.enums import Category, Severity
from shared.domain.models import TEXT_LIMITS
from tests.contract.cis_rules import (
    BANDIT_EXPLICIT,
    BANDIT_SKIPS,
    PYLINT_RULES,
    PYLINT_SEVERITIES,
)
from tests.contract.tools import PYLINTRC

C = Category


def entries(tool: str) -> list[rules.RuleEntry]:
    return [e for e in rules.CATALOGUE.values() if e.tool == tool]


def test_every_enabled_pylint_rule_has_exactly_its_cis_entry() -> None:
    assert [(e.rule_key, e.symbol, e.category.value) for e in entries(rules.PYLINT)] == [
        (f"pylint:{msg_id}", symbol, category) for msg_id, symbol, category in PYLINT_RULES
    ]
    assert {e.rule_key.split(":")[1]: e.severity for e in entries(rules.PYLINT)} == {
        msg_id: Severity(severity) for msg_id, severity in PYLINT_SEVERITIES.items()
    }


def test_catalogue_matches_the_rules_enabled_in_the_rcfile() -> None:
    rc = configparser.ConfigParser()
    rc.read(PYLINTRC, encoding="utf-8")
    enabled = [s.strip() for s in rc["MESSAGES CONTROL"]["enable"].split(",") if s.strip()]
    assert list(rules.enabled_pylint_symbols()) == enabled


def test_bandit_entries_are_exactly_the_cis_explicit_set() -> None:
    assert {e.rule_key.split(":")[1] for e in entries(rules.BANDIT)} == BANDIT_EXPLICIT
    assert BANDIT_EXPLICIT.isdisjoint(BANDIT_SKIPS)
    for entry in entries(rules.BANDIT):
        assert entry.severity is None  # from the §8.4 matrix at run time
        expected = (
            C.BEST_PRACTICE if entry.rule_key in ("bandit:B110", "bandit:B112") else C.SECURITY
        )
        assert entry.category is expected


def test_bandit_entries_exist_in_the_pinned_bandit() -> None:
    manager = extension_loader.MANAGER
    known = set(manager.plugins_by_id) | set(manager.blacklist_by_id)
    assert {e.rule_key.split(":")[1] for e in entries(rules.BANDIT)} <= known


def test_parser_entries_follow_section_8_2() -> None:
    for name in ("syntax-error", "unparseable"):
        entry = rules.CATALOGUE[f"python-parser:{name}"]
        assert (entry.category, entry.severity) == (C.CORRECTNESS, Severity.CRITICAL)
    assert rules.CATALOGUE["python-parser:syntax-error"].title == "Syntax error"
    assert len(entries(rules.PARSER)) == 2


def test_every_key_is_namespaced_by_its_tool() -> None:
    assert len(rules.CATALOGUE) == 2 + 49 + 20
    for key, entry in rules.CATALOGUE.items():
        assert key == entry.rule_key
        assert key.split(":")[0] == entry.tool
        assert entry.tool in (rules.PARSER, rules.PYLINT, rules.BANDIT)


def test_duplicate_keys_are_rejected() -> None:
    entry = rules.CATALOGUE["pylint:W0102"]
    with pytest.raises(ValueError, match="duplicate"):
        rules.build_catalogue([entry, entry])


def test_texts_are_present_and_within_the_domain_limits() -> None:
    for entry in rules.CATALOGUE.values():
        for field in ("title", "impact", "recommendation"):
            text = getattr(entry, field)
            assert text.strip() == text
            assert 1 <= len(text) <= TEXT_LIMITS[field]


def test_rule_sets_do_not_overlap() -> None:
    assert C.SECURITY not in {e.category for e in entries(rules.PYLINT)}
    assert {e.category for e in entries(rules.BANDIT)} <= {C.SECURITY, C.BEST_PRACTICE}


def test_coverage_maps_are_derived_from_the_catalogue() -> None:
    assert rules.covered_categories("pylint") == (
        C.CORRECTNESS, C.PERFORMANCE, C.READABILITY, C.MAINTAINABILITY, C.BEST_PRACTICE,
    )  # fmt: skip
    assert rules.covered_categories("bandit") == (C.SECURITY, C.BEST_PRACTICE)
    assert rules.covered_categories("python-parser") == (C.CORRECTNESS,)
    assert rules.covered_categories("unknown") == ()


def test_escalability_is_derived_from_the_category() -> None:
    for entry in rules.CATALOGUE.values():
        assert entry.escalable == (entry.category in (C.CORRECTNESS, C.SECURITY))
        assert rules.is_escalable(entry.rule_key) == entry.escalable
    assert rules.is_escalable("pylint:W0102")
    assert not rules.is_escalable("pylint:W0702")
    assert rules.is_escalable("bandit:B602")
    assert not rules.is_escalable("bandit:B110")
    assert rules.is_escalable("bandit:B104")  # generic Bandit results are SECURITY
    assert not rules.is_escalable("pylint:W9999")


def test_generic_bandit_texts() -> None:
    entry = rules.bandit_entry("B104", "hardcoded_bind_all_interfaces", 605)
    assert (entry.title, entry.category) == ("Hardcoded bind all interfaces", C.SECURITY)
    assert entry.impact == "This pattern is commonly associated with a security weakness (CWE-605)."
    assert rules.bandit_entry("B999", "", None).title == "B999"
    assert rules.bandit_entry("B602", "anything", 1) is rules.CATALOGUE["bandit:B602"]
