"""Wrapper for netCDF readers."""

from __future__ import annotations

import logging
import os.path
import warnings
from typing import TYPE_CHECKING

import numpy as np
from monty.collections import AttrDict
from monty.dev import deprecated, requires
from monty.functools import lazy_property
from monty.string import marquee

from pymatgen.core.structure import Structure
from pymatgen.core.units import ArrayWithUnit
from pymatgen.core.xcfunc import XcFunc
from pymatgen.electronic_structure.core import Magmom

if TYPE_CHECKING:
    from typing_extensions import Self

try:
    import netCDF4
except ImportError:
    netCDF4 = None
    warnings.warn(
        "Can't import netCDF4. Some features will be disabled unless you pip install netCDF4.",
        stacklevel=2,
    )


logger = logging.getLogger(__name__)

__author__ = "Matteo Giantomassi"
__copyright__ = "Copyright 2013, The Materials Project"
__version__ = "0.1"
__maintainer__ = "Matteo Giantomassi"
__email__ = "gmatteo at gmail.com"
__status__ = "Development"
__date__ = "Feb 21, 2013M"


def _as_reader(file, cls):
    close_it = False
    if not isinstance(file, cls):
        file, close_it = cls(file), True
    return file, close_it


def as_ncreader(file):
    """
    Convert file into a NetcdfReader instance.
    Returns reader, close_it where close_it is set to True
    if we have to close the file before leaving the procedure.
    """
    return _as_reader(file, NetcdfReader)


def as_etsfreader(file):
    """Return an EtsfReader. Accepts filename or EtsfReader."""
    return _as_reader(file, EtsfReader)


class NetcdfReaderError(Exception):
    """Base error class for NetcdfReader."""


class NO_DEFAULT:
    """Signal that read_value should raise an Error."""


class NetcdfReader:
    """
    Wraps and extends netCDF4.Dataset. Read only mode. Supports with statements.

    Additional documentation available at:
        https://unidata.github.io/netcdf4-python/
    """

    Error = NetcdfReaderError

    @requires(netCDF4 is not None, "netCDF4 must be installed to use this class")
    def __init__(self, path):
        """Open the Netcdf file specified by path (read mode)."""
        self.path = os.path.abspath(path)

        try:
            self.rootgrp = netCDF4.Dataset(self.path, mode="r")
        except Exception as exc:
            raise self.Error(f"In file {self.path}: {exc}")

        self.ngroups = len(list(self.walk_tree()))

        # Always return non-masked numpy arrays.
        # Slicing a ncvar returns a MaskedArray and this is really annoying
        # because it can lead to unexpected behavior in e.g. calls to np.matmul!
        # See also https://github.com/Unidata/netcdf4-python/issues/785
        self.rootgrp.set_auto_mask(False)

    def __enter__(self) -> Self:
        """Activated when used in the with statement."""
        return self

    def __exit__(self, type, value, traceback):  # noqa: A002
        """Activated at the end of the with statement. It automatically closes the file."""
        self.rootgrp.close()

    def close(self):
        """Close the file."""
        try:
            self.rootgrp.close()
        except Exception as exc:
            logger.warning(f"Exception {exc} while trying to close {self.path}")

    def walk_tree(self, top=None):
        """
        Navigate all the groups in the file starting from top.
        If top is None, the root group is used.
        """
        if top is None:
            top = self.rootgrp

        values = top.groups.values()
        yield values
        for value in top.groups.values():
            yield from self.walk_tree(value)

    def print_tree(self):
        """Print all the groups in the file."""
        for children in self.walk_tree():
            for child in children:
                print(child)

    def read_dimvalue(self, dimname, path="/", default=NO_DEFAULT):
        """Get the value of a dimension.

        Args:
            dimname: Name of the variable
            path: path to the group.
            default: return `default` if `dimname` is not present and
                `default` is not `NO_DEFAULT` else raise self.Error.
        """
        try:
            dim = self._read_dimensions(dimname, path=path)[0]
            return len(dim)
        except self.Error:
            if default is NO_DEFAULT:
                raise
            return default

    def read_varnames(self, path="/"):
        """List of variable names stored in the group specified by path."""
        if path == "/":
            return list(self.rootgrp.variables)
        group = self.path2group[path]
        return list(group.variables)

    def read_value(self, varname, path="/", cmode=None, default=NO_DEFAULT):
        """Get the values of variable with name varname in the group specified by path.

        Args:
            varname: Name of the variable
            path: path to the group.
            cmode: if cmode=="c", a complex ndarrays is constructed and returned
                (netcdf does not provide native support from complex datatype).
            default: returns default if varname is not present.
                self.Error is raised if default is set to NO_DEFAULT

        Returns:
            numpy array if varname represents an array, scalar otherwise.
        """
        try:
            var = self.read_variable(varname, path=path)
        except self.Error:
            if default is NO_DEFAULT:
                raise
            return default

        if cmode is None:
            # scalar or array
            # getValue is not portable!
            try:
                return var.getValue()[0] if not var.shape else var[:]
            except IndexError:
                return var.getValue() if not var.shape else var[:]

        if var.shape[-1] != 2:
            raise ValueError(f"{var.shape[-1]=}, expect it to be 2")
        if cmode == "c":
            return var[..., 0] + 1j * var[..., 1]
        raise ValueError(f"Wrong value for {cmode=}")

    def read_variable(self, varname, path="/"):
        """Get the variable with name varname in the group specified by path."""
        return self._read_variables(varname, path=path)[0]

    def _read_dimensions(self, *dim_names, **kwargs):
        path = kwargs.get("path", "/")
        try:
            if path == "/":
                return [self.rootgrp.dimensions[dname] for dname in dim_names]
            group = self.path2group[path]
            return [group.dimensions[dname] for dname in dim_names]

        except KeyError:
            raise self.Error(f"In file {self.path}:\nError while reading dimensions: {dim_names} with {kwargs=}")

    def _read_variables(self, *var_names, **kwargs):
        path = kwargs.get("path", "/")
        try:
            if path == "/":
                return [self.rootgrp.variables[vname] for vname in var_names]
            group = self.path2group[path]
            return [group.variables[vname] for vname in var_names]

        except KeyError:
            raise self.Error(f"In file {self.path}:\nError while reading variables: {var_names} with {kwargs=}.")

    def read_keys(self, keys, dict_cls=AttrDict, path="/"):
        """
        Read a list of variables/dimensions from file. If a key is not present the corresponding
        entry in the output dictionary is set to None.
        """
        dct = dict_cls()
        for key in keys:
            try:
                # Try to read a variable.
                dct[key] = self.read_value(key, path=path)
            except self.Error:
                try:
                    # Try to read a dimension.
                    dct[key] = self.read_dimvalue(key, path=path)
                except self.Error:
                    dct[key] = None

        return dct


class EtsfReader(NetcdfReader):
    """
    This object reads data from a file written according to the ETSF-IO specifications.

    We assume that the netcdf file contains at least the crystallographic section.
    """

    @lazy_property
    def chemical_symbols(self):
        """Chemical symbols char [number of atom species][symbol length]."""
        charr = self.read_value("chemical_symbols")
        symbols = []
        for v in charr:
            s = "".join(c.decode("utf-8") for c in v)
            symbols.append(s.strip())

        return symbols

    def type_idx_from_symbol(self, symbol):
        """Get the type index from the chemical symbol. Note python convention."""
        return self.chemical_symbols.index(symbol)

    def read_structure(self, cls=Structure):
        """Get the crystalline structure stored in the rootgrp."""
        return structure_from_ncdata(self, cls=cls)

    def read_abinit_xcfunc(self):
        """Read ixc from an Abinit file. Return XcFunc object."""
        ixc = int(self.read_value("ixc"))
        return XcFunc.from_abinit_ixc(ixc)

    def read_abinit_hdr(self):
        """
        Read the variables associated to the Abinit header.

        Return AbinitHeader
        """
        dct = {}
        for hdr_var in _HDR_VARIABLES.values():
            nc_name = hdr_var.etsf_name if hdr_var.etsf_name is not None else hdr_var.name
            if nc_name in self.rootgrp.variables:
                dct[hdr_var.name] = self.read_value(nc_name)
            elif nc_name in self.rootgrp.dimensions:
                dct[hdr_var.name] = self.read_dimvalue(nc_name)
            else:
                raise ValueError(f"Cannot find `{nc_name}` in `{self.path}`")
            # Convert scalars to (well) scalars.
            if hasattr(dct[hdr_var.name], "shape") and not dct[hdr_var.name].shape:
                dct[hdr_var.name] = np.asarray(dct[hdr_var.name]).item()
            if hdr_var.name in ("title", "md5_pseudos", "codvsn"):
                # Convert array of numpy bytes to list of strings
                if hdr_var.name == "codvsn":
                    dct[hdr_var.name] = "".join(bs.decode("utf-8").strip() for bs in dct[hdr_var.name])
                else:
                    dct[hdr_var.name] = [
                        "".join(bs.decode("utf-8") for bs in astr).strip() for astr in dct[hdr_var.name]
                    ]

        return AbinitHeader(dct)


def structure_from_ncdata(ncdata, site_properties=None, cls=Structure):
    """Read and return a pymatgen structure from a NetCDF file
    containing crystallographic data in the ETSF-IO format.

    Args:
        ncdata: filename or NetcdfReader instance.
        site_properties: Dictionary with site properties.
        cls: The Structure class to instantiate.
    """
    ncdata, close_it = as_ncreader(ncdata)

    # TODO check whether atomic units are used
    lattice = ArrayWithUnit(ncdata.read_value("primitive_vectors"), "bohr").to("ang")

    red_coords = ncdata.read_value("reduced_atom_positions")
    n_atom = len(red_coords)

    znucl_type = ncdata.read_value("atomic_numbers")

    # type_atom[:natom] --> index Between 1 and number of atom species
    type_atom = ncdata.read_value("atom_species")

    try:
        intgden = ncdata.read_value("intgden")
        nspden = intgden.shape[1]
    except NetcdfReaderError:
        intgden = nspden = None

    if intgden is not None:
        if nspden == 2:
            magmoms = Magmom(intgden[:, 1] - intgden[:, 0])
        elif nspden == 4:
            magmoms = [Magmom([intg_at[1], intg_at[2], intg_at[3]]) for intg_at in intgden]
        else:
            magmoms = None
    else:
        magmoms = None

    # Fortran to C index and float --> int conversion.
    species = n_atom * [None]
    for atom in range(n_atom):
        type_idx = type_atom[atom] - 1
        species[atom] = int(znucl_type[type_idx])

    dct = {}
    if site_properties is not None:
        for prop in site_properties:
            dct[prop] = ncdata.read_value(prop)

    if magmoms is not None and "magmom" not in dct:
        dct["magmom"] = magmoms

    structure = cls(lattice, species, red_coords, site_properties=dct)

    # Quick and dirty hack.
    # I need an abipy structure since I need to_abivars and other methods.
    try:
        from abipy.core.structure import Structure as AbipyStructure

        structure.__class__ = AbipyStructure
    except ImportError:
        pass

    if close_it:
        ncdata.close()

    return structure


class _H:
    __slots__ = ("doc", "etsf_name", "name")

    def __init__(self, name, doc, etsf_name=None):
        self.name, self.doc, self.etsf_name = name, doc, etsf_name


_HDR_VARIABLES = (
    # Scalars
    _H("bantot", "total number of bands (sum of nband on all kpts and spins)"),
    _H("date", "starting date"),
    _H("headform", "format of the header"),
    _H("intxc", "input variable"),
    _H("ixc", "input variable"),
    _H("mband", "maxval(hdr%nband)", etsf_name="max_number_of_states"),
    _H("natom", "input variable", etsf_name="number_of_atoms"),
    _H("nkpt", "input variable", etsf_name="number_of_kpoints"),
    _H("npsp", "input variable"),
    _H("nspden", "input variable", etsf_name="number_of_components"),
    _H("nspinor", "input variable", etsf_name="number_of_spinor_components"),
    _H("nsppol", "input variable", etsf_name="number_of_spins"),
    _H("nsym", "input variable", etsf_name="number_of_symmetry_operations"),
    _H("ntypat", "input variable", etsf_name="number_of_atom_species"),
    _H("occopt", "input variable"),
    _H("pertcase", "the index of the perturbation, 0 if GS calculation"),
    _H("usepaw", "input variable (0=norm-conserving PSPs, 1=paw)"),
    _H("usewvl", "input variable (0=plane-waves, 1=wavelets)"),
    _H("kptopt", "input variable (defines symmetries used for k-point sampling)"),
    _H("pawcpxocc", "input variable"),
    _H(
        "nshiftk_orig",
        "original number of shifts given in input (changed in inkpts, the actual value is nshiftk)",
    ),
    _H("nshiftk", "number of shifts after inkpts."),
    _H("icoulomb", "input variable."),
    _H("ecut", "input variable", etsf_name="kinetic_energy_cutoff"),
    _H("ecutdg", "input variable (ecut for NC PSPs, pawecutdg for paw)"),
    _H("ecutsm", "input variable"),
    _H("ecut_eff", "ecut*dilatmx**2 (dilatmx is an input variable)"),
    _H("etot", "EVOLVING variable"),
    _H("fermie", "EVOLVING variable", etsf_name="fermi_energy"),
    _H("residm", "EVOLVING variable"),
    _H("stmbias", "input variable"),
    _H("tphysel", "input variable"),
    _H("tsmear", "input variable"),
    _H("nelect", "number of electrons (computed from pseudos and charge)"),
    _H("charge", "input variable"),
    # Arrays
    _H("qptn", "qptn(3) the wavevector, in case of a perturbation"),
    # _H("rprimd", "rprimd(3,3) EVOLVING variables", etsf_name="primitive_vectors"),
    # _H(ngfft, "ngfft(3) input variable",  number_of_grid_points_vector1"
    # _H("nwvlarr", "nwvlarr(2) the number of wavelets for each resolution.", etsf_name="number_of_wavelets"),
    _H("kptrlatt_orig", "kptrlatt_orig(3,3) Original kptrlatt"),
    _H("kptrlatt", "kptrlatt(3,3) kptrlatt after inkpts."),
    _H("istwfk", "input variable istwfk(nkpt)"),
    _H("lmn_size", "lmn_size(npsp) from PSPs"),
    _H("nband", "input variable nband(nkpt*nsppol)", etsf_name="number_of_states"),
    _H(
        "npwarr",
        "npwarr(nkpt) array holding npw for each k point",
        etsf_name="number_of_coefficients",
    ),
    _H("pspcod", "pscod(npsp) from PSPs"),
    _H("pspdat", "psdat(npsp) from PSPs"),
    _H("pspso", "pspso(npsp) from PSPs"),
    _H("pspxc", "pspxc(npsp) from PSPs"),
    _H("so_psp", "input variable so_psp(npsp)"),
    _H("symafm", "input variable symafm(nsym)"),
    # _H(symrel="input variable symrel(3,3,nsym)",  etsf_name="reduced_symmetry_matrices"),
    _H("typat", "input variable typat(natom)", etsf_name="atom_species"),
    _H(
        "kptns",
        "input variable kptns(nkpt, 3)",
        etsf_name="reduced_coordinates_of_kpoints",
    ),
    _H("occ", "EVOLVING variable occ(mband, nkpt, nsppol)", etsf_name="occupations"),
    _H(
        "tnons",
        "input variable tnons(nsym, 3)",
        etsf_name="reduced_symmetry_translations",
    ),
    _H("wtk", "weight of kpoints wtk(nkpt)", etsf_name="kpoint_weights"),
    _H("shiftk_orig", "original shifts given in input (changed in inkpts)."),
    _H("shiftk", "shiftk(3,nshiftk), shiftks after inkpts"),
    _H("amu", "amu(ntypat) ! EVOLVING variable"),
    # _H("xred", "EVOLVING variable xred(3,natom)", etsf_name="reduced_atom_positions"),
    _H("zionpsp", "zionpsp(npsp) from PSPs"),
    _H(
        "znuclpsp",
        "znuclpsp(npsp) from PSPs. Note the difference between (znucl|znucltypat) and znuclpsp",
    ),
    _H("znucltypat", "znucltypat(ntypat) from alchemy", etsf_name="atomic_numbers"),
    _H("codvsn", "version of the code"),
    _H("title", "title(npsp) from PSPs"),
    _H(
        "md5_pseudos",
        "md5pseudos(npsp), md5 checksums associated to pseudos (read from file)",
    ),
    # _H(type(pawrhoij_type), allocatable :: pawrhoij(:) ! EVOLVING variable, only for paw
)
_HDR_VARIABLES = {h.name: h for h in _HDR_VARIABLES}  # type: ignore[assignment]


class AbinitHeader(AttrDict):
    """Stores the values reported in the Abinit header."""

    # def __init__(self, *args, **kwargs):
    #    super().__init__(*args, **kwargs)
    #    for k, v in self.items():
    #        v.__doc__ = _HDR_VARIABLES[k].doc

    def __str__(self):
        return self.to_str()

    def to_str(self, verbose=0, title=None, **kwargs):
        """String representation. kwargs are passed to `pprint.pformat`.

        Args:
            verbose: Verbosity level
            title: Title string.
        """
        from pprint import pformat

        header_str = pformat(self, **kwargs)
        if title is not None:
            return "\n".join([marquee(title, mark="="), header_str])
        return header_str

    # to_string alias required for backwards compatibility
    # PLEASE DO NOT REMOVE THIS LINE AS THIS API HAS BEEN AROUND FOR SEVERAL YEARS
    @deprecated(to_str)
    def to_string(self, *args, **kwargs):
        return self.to_str(*args, **kwargs)
