"""Round-trip regression test for scripts/gen_parameters.py.

Guards against silent drift in the fabric-cicd parameter.yml generation logic: given the
synthetic fixtures/fabric.yml, gen_parameters must reproduce fixtures/parameter.yml
byte-for-byte.
"""

from __future__ import annotations

from pathlib import Path

from _fabric_config import Environment, FabricConfig, load_config
import gen_parameters
import yaml

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


def test_generate_parameters_reproduces_golden_fixture_byte_for_byte() -> None:
    config = load_config(FIXTURES_DIR / "fabric.yml")

    params = gen_parameters.generate_parameters(config)
    rendered = yaml.dump(params, default_flow_style=False, sort_keys=False, allow_unicode=True)

    golden = (FIXTURES_DIR / "parameter.yml").read_text(encoding="utf-8")
    assert rendered == golden


def test_build_item_id_entries_emits_entry_for_differing_guid_in_a_target_env() -> None:
    config = FabricConfig(
        name="sample",
        environments=[
            Environment(name="dev", items={"elt_pipeline": "11111111-0000-0000-0000-000000000010"}),
            Environment(
                name="prod", items={"elt_pipeline": "22222222-0000-0000-0000-000000000010"}
            ),
        ],
    )

    entries = gen_parameters._build_item_id_entries(config)

    assert entries == [
        {
            "find_value": "11111111-0000-0000-0000-000000000010",
            "replace_value": {"prod": "22222222-0000-0000-0000-000000000010"},
        }
    ]


def test_build_item_id_entries_skips_item_not_registered_in_target_env() -> None:
    config = FabricConfig(
        name="sample",
        environments=[
            Environment(
                name="dev", items={"dev_only_item": "11111111-0000-0000-0000-000000000010"}
            ),
            Environment(name="prod", items={}),
        ],
    )

    entries = gen_parameters._build_item_id_entries(config)

    assert entries == []


def test_build_item_id_entries_skips_item_with_identical_guid_across_envs() -> None:
    config = FabricConfig(
        name="sample",
        environments=[
            Environment(name="dev", items={"shared_item": "11111111-0000-0000-0000-000000000010"}),
            Environment(name="prod", items={"shared_item": "11111111-0000-0000-0000-000000000010"}),
        ],
    )

    entries = gen_parameters._build_item_id_entries(config)

    assert entries == []
