"""Guard against stray copies of the package shadowing the real one (a patch folder once did exactly that)."""

from pathlib import Path

from fitly import storage
from fitly.scoring import novelty, pipeline

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = (ROOT / "src" / "fitly").resolve()


def test_core_modules_are_imported_from_the_project_package():
    for mod in (pipeline, novelty, storage):
        assert PACKAGE in Path(mod.__file__).resolve().parents, mod.__file__


def test_no_competing_copies_of_core_modules_in_the_tree():
    skip = {".venv", "_archive", "backups"}
    for mod in (pipeline, novelty, storage):
        name = Path(mod.__file__).name
        found = [p for p in ROOT.rglob(name) if not (skip & set(p.relative_to(ROOT).parts))]
        assert [p.resolve() for p in found] == [Path(mod.__file__).resolve()], found
