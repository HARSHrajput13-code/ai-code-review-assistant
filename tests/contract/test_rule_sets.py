"""The CIS rule sets are complete, consistent and disjoint (CIS §8.1, §8.3-§8.5, §8.8)."""

from bandit.core import extension_loader

from tests.contract.cis_rules import (
    BANDIT_EXPLICIT,
    BANDIT_SKIPS,
    CATEGORIES,
    PYLINT_EXCLUDED,
    PYLINT_RULES,
    PYLINT_SECURITY_OVERLAP,
)

PYLINT_IDS = [msg_id for msg_id, _, _ in PYLINT_RULES]


def bandit_test_ids() -> set[str]:
    manager = extension_loader.MANAGER
    return set(manager.plugins_by_id) | set(manager.blacklist_by_id)


def test_pylint_rule_set_has_49_unique_messages() -> None:
    assert len(PYLINT_IDS) == 49
    assert len(set(PYLINT_IDS)) == 49
    assert len({symbol for _, symbol, _ in PYLINT_RULES}) == 49


def test_every_pylint_rule_has_a_valid_category() -> None:
    assert {category for _, _, category in PYLINT_RULES} <= CATEGORIES


def test_deliberately_excluded_pylint_messages_are_not_enabled() -> None:
    assert PYLINT_EXCLUDED.isdisjoint(PYLINT_IDS)


def test_pylint_does_not_cover_security() -> None:
    # Bandit owns security, so the two rule sets do not overlap (§8.1).
    assert "SECURITY" not in {category for _, _, category in PYLINT_RULES}
    assert PYLINT_SECURITY_OVERLAP.isdisjoint(PYLINT_IDS)


def test_bandit_catalogue_and_skip_ids_exist_in_the_pinned_version() -> None:
    known = bandit_test_ids()
    assert BANDIT_EXPLICIT <= known
    assert set(BANDIT_SKIPS) <= known


def test_skipped_bandit_tests_have_no_catalogue_entries() -> None:
    assert BANDIT_EXPLICIT.isdisjoint(BANDIT_SKIPS)
