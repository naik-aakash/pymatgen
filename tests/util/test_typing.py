"""This module tests types are as expected and can be imported without circular ImportError."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from pymatgen.core import Composition, DummySpecies, Element, Species
from pymatgen.entries import Entry
from pymatgen.util.typing import CompositionLike, EntryLike, PathLike, SpeciesLike

if TYPE_CHECKING:
    from typing import Any

__author__ = "Janosh Riebesell"
__date__ = "2022-10-20"
__email__ = "janosh@lbl.gov"


def _type_str(some_type: Any) -> str:
    return str(some_type).replace("typing.", "").replace("pymatgen.core.periodic_table.", "")


def test_entry_like():
    # needs to be tested as string to avoid
    # TypeError: issubclass() arg 2 must be a class, a tuple of classes, or a union
    # since EntryLike is defined as Union[] of strings to avoid circular imports
    entries = (
        "Entry",
        "ComputedEntry",
        "ComputedStructureEntry",
        "PDEntry",
        "ExpEntry",
        "TransformedPDEntry",
        "GrandPotPDEntry",
        "CostEntry",
        "GibbsComputedStructureEntry",
    )
    type_str = _type_str(EntryLike)
    for entry in entries:
        assert entry in type_str
    assert Entry.__name__ in str(EntryLike)


def test_species_like():
    assert isinstance("H", SpeciesLike)
    assert isinstance(Element("H"), SpeciesLike)
    assert isinstance(Species("H+"), SpeciesLike)
    assert isinstance(DummySpecies("X"), SpeciesLike)


def test_composition_like():
    assert isinstance("H", CompositionLike)
    assert isinstance(Element("H"), CompositionLike)
    assert isinstance(Species("H+"), CompositionLike)
    assert isinstance(Composition("H"), CompositionLike)
    assert isinstance({"H": 1}, CompositionLike)
    assert isinstance(DummySpecies("X"), CompositionLike)


def test_pathlike():
    assert isinstance("path/to/file", PathLike)
    assert isinstance(Path("path/to/file"), PathLike)
    assert not isinstance(1, PathLike)
    assert not isinstance(1.0, PathLike)
