from pathlib import Path

import pytest
from conftest import FIXTURES

from gazette.sources import SourcesError, load_sources, slugify

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    ("name", "slug"),
    [
        ("Ouest-France", "ouest-france"),
        ("Sud Ouest", "sud-ouest"),
        ("La Dépêche du Midi", "la-depeche-du-midi"),
        ("Le Dauphiné Libéré", "le-dauphine-libere"),
        ("L'Obs", "l-obs"),
        ("Libération", "liberation"),
    ],
)
def test_slugify(name, slug):
    assert slugify(name) == slug


def test_project_sources_csv_is_valid():
    sources = load_sources(ROOT / "sources.csv", include_inactive=True)
    assert len(sources) == 8
    assert sorted(s.subdir for s in sources) == ["pqn"] * 4 + ["pqr"] * 4
    assert all(s.feed_url.startswith("https://") for s in sources)


def test_inactive_sources_are_skipped():
    slugs = [s.slug for s in load_sources(FIXTURES / "sources.csv")]
    assert slugs == ["journal-regional", "l-echo-national", "gazette-en-panne"]


def test_missing_column_is_rejected(tmp_path):
    bad = tmp_path / "sources.csv"
    bad.write_text("nom;type;acces\nX;PQR;https://x.fr\n", encoding="utf-8")
    with pytest.raises(SourcesError, match="Colonnes manquantes"):
        load_sources(bad)
