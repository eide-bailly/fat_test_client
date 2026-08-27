"""Self-compare regression test for scripts/validate_repoint.py against synthetic fixtures.

Comparing the fixture tree against itself (before == after) exercises identity matching,
connection-registration checking, and override-coverage checking end to end while staying
GUID-free (the .platform logicalId in the fixture is a synthetic placeholder).
"""

from __future__ import annotations

from pathlib import Path

from _fabric_config import load_config
import validate_repoint

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
REPOINT_TREE = FIXTURES_DIR / "repoint_tree" / "fabric"


def test_run_validation_passes_when_before_and_after_trees_are_identical() -> None:
    config = load_config(FIXTURES_DIR / "fabric.yml")

    result = validate_repoint.run_validation(REPOINT_TREE, REPOINT_TREE, config)

    assert result.ok is True
    assert result.messages == []
    assert result.unchanged_count == 1
    assert result.new_item_count == 0


def test_run_validation_treats_missing_before_baseline_as_all_new() -> None:
    config = load_config(FIXTURES_DIR / "fabric.yml")
    nonexistent_before = FIXTURES_DIR / "nonexistent" / "fabric"

    result = validate_repoint.run_validation(nonexistent_before, REPOINT_TREE, config)

    assert result.ok is True
    assert result.messages == []
    assert result.unchanged_count == 0
    assert result.new_item_count == 1
