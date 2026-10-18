"""The Home Assistant integration ships its own copy of ua_outages; keep it identical."""

from pathlib import Path

ROOT = Path(__file__).parent.parent
SOURCE = ROOT / "src" / "ua_outages"
VENDORED = ROOT / "custom_components" / "ukraine_outages" / "ua_outages"


def _files(root: Path) -> dict[str, bytes]:
    return {
        str(p.relative_to(root)): p.read_bytes()
        for p in root.rglob("*")
        if p.is_file() and "__pycache__" not in p.parts
    }


def test_vendored_core_matches_source():
    assert _files(SOURCE) == _files(VENDORED), (
        "custom_components/ukraine_outages/ua_outages is out of date; refresh it with:\n"
        "  rm -rf custom_components/ukraine_outages/ua_outages && "
        "cp -R src/ua_outages custom_components/ukraine_outages/ua_outages"
    )
