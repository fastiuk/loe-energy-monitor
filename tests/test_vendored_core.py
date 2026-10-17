"""The Home Assistant integration ships its own copy of loe_core; keep it identical."""

from pathlib import Path

ROOT = Path(__file__).parent.parent
SOURCE = ROOT / "src" / "loe_core"
VENDORED = ROOT / "custom_components" / "loe_outages" / "loe_core"


def test_vendored_core_matches_source():
    source = {p.name: p.read_text(encoding="utf-8") for p in SOURCE.glob("*.py")}
    vendored = {p.name: p.read_text(encoding="utf-8") for p in VENDORED.glob("*.py")}
    assert source == vendored, (
        "custom_components/loe_outages/loe_core is out of date; refresh it with:\n"
        "  rm -rf custom_components/loe_outages/loe_core && "
        "cp -R src/loe_core custom_components/loe_outages/loe_core"
    )
