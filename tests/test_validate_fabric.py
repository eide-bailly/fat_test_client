"""Pass/fail regression tests for scripts/validate_fabric.py against synthetic fixtures."""

from __future__ import annotations

from pathlib import Path

from _fabric_config import validate_config

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


def test_validate_config_passes_for_valid_fabric_yml() -> None:
    valid, errors = validate_config(FIXTURES_DIR / "fabric.yml")

    assert valid is True
    assert errors == []


def test_validate_config_fails_for_fabric_yml_missing_required_name() -> None:
    valid, errors = validate_config(FIXTURES_DIR / "invalid_fabric.yml")

    assert valid is False
    assert errors
    assert any("name" in error for error in errors)
