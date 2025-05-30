"""This module implements a neutron diffraction (ND) pattern calculator."""

from __future__ import annotations

import math
import os
from typing import TYPE_CHECKING

import numpy as np
import orjson

from pymatgen.analysis.diffraction.core import (
    AbstractDiffractionPatternCalculator,
    DiffractionPattern,
    get_unique_families,
)
from pymatgen.symmetry.analyzer import SpacegroupAnalyzer

if TYPE_CHECKING:
    from pymatgen.core import Structure

__author__ = "Yuta Suzuki"
__copyright__ = "Copyright 2018, The Materials Project"
__version__ = "0.1"
__maintainer__ = "Yuta Suzuki"
__email__ = "resnant@outlook.jp"
__date__ = "4/19/18"

# This table was cited from "Neutron Data Booklet" 2nd ed (Old City 2003).
with open(
    os.path.join(os.path.dirname(__file__), "neutron_scattering_length.json"),
    "rb",
) as file:
    ATOMIC_SCATTERING_LEN = orjson.loads(file.read())


class NDCalculator(AbstractDiffractionPatternCalculator):
    """
    Computes the powder neutron diffraction pattern of a crystal structure.
    This code is a slight modification of XRDCalculator in
    pymatgen.analysis.diffraction.xrd. See it for details of the algorithm.
    Main changes by using neutron instead of X-ray are as follows:

    1. Atomic scattering length is a constant.
    2. Polarization correction term of Lorentz factor is unnecessary.

    Reference:
    Marc De Graef and Michael E. McHenry, Structure of Materials 2nd ed,
    Chapter13, Cambridge University Press 2003.
    """

    def __init__(self, wavelength=1.54184, symprec: float = 0, debye_waller_factors=None):
        """Initialize the ND calculator with a given radiation.

        Args:
            wavelength (float): The wavelength of neutron in angstroms.
                Defaults to 1.54, corresponds to Cu K_alpha x-ray radiation.
            symprec (float): Symmetry precision for structure refinement. If
                set to 0, no refinement is done. Otherwise, refinement is
                performed using spglib with provided precision.
            debye_waller_factors ({element symbol: float}): Allows the
                specification of Debye-Waller factors. Note that these
                factors are temperature dependent.
        """
        self.wavelength = wavelength
        self.symprec = symprec
        self.debye_waller_factors = debye_waller_factors or {}

    def get_pattern(self, structure: Structure, scaled=True, two_theta_range=(0, 90)):
        """
        Calculates the powder neutron diffraction pattern for a structure.

        Args:
            structure (Structure): Input structure
            scaled (bool): Whether to return scaled intensities. The maximum
                peak is set to a value of 100. Defaults to True. Use False if
                you need the absolute values to combine ND plots.
            two_theta_range ([float of length 2]): Tuple for range of
                two_thetas to calculate in degrees. Defaults to (0, 90). Set to
                None if you want all diffracted beams within the limiting
                sphere of radius 2 / wavelength.

        Returns:
            DiffractionPattern: ND pattern
        """
        if self.symprec:
            finder = SpacegroupAnalyzer(structure, symprec=self.symprec)
            structure = finder.get_refined_structure()

        wavelength = self.wavelength
        lattice = structure.lattice
        is_hex = lattice.is_hexagonal()

        # Obtained from Bragg condition. Note that reciprocal lattice
        # vector length is 1 / d_hkl.
        min_r, max_r = (
            (0, 2 / wavelength)
            if two_theta_range is None
            else [2 * math.sin(math.radians(t / 2)) / wavelength for t in two_theta_range]
        )

        # Obtain crystallographic reciprocal lattice points within range
        recip_lattice = lattice.reciprocal_lattice_crystallographic
        recip_pts = recip_lattice.get_points_in_sphere([[0, 0, 0]], [0, 0, 0], max_r)
        if min_r:
            recip_pts = [pt for pt in recip_pts if pt[1] >= min_r]

        # Create a flattened array of coeffs, frac_coords and occus. This is
        # used to perform vectorized computation of atomic scattering factors
        # later. Note that these are not necessarily the same size as the
        # structure as each partially occupied specie occupies its own
        # position in the flattened array.
        _coeffs = []
        _frac_coords = []
        _occus = []
        _dwfactors = []

        for site in structure:
            for sp, occu in site.species.items():
                try:
                    c = ATOMIC_SCATTERING_LEN[sp.symbol]
                except KeyError:
                    raise ValueError(
                        f"Unable to calculate ND pattern as there is no scattering coefficients for {sp.symbol}."
                    )
                _coeffs.append(c)
                _dwfactors.append(self.debye_waller_factors.get(sp.symbol, 0))
                _frac_coords.append(site.frac_coords)
                _occus.append(occu)

        coeffs = np.array(_coeffs)
        frac_coords = np.array(_frac_coords)
        occus = np.array(_occus)
        dw_factors = np.array(_dwfactors)
        peaks: dict[float, list[float | list[tuple[int, ...]]]] = {}
        two_thetas: list[float] = []

        for hkl, g_hkl, ind, _ in sorted(recip_pts, key=lambda i: (i[1], -i[0][0], -i[0][1], -i[0][2])):
            # Force miller indices to be integers
            hkl = [round(i) for i in hkl]
            if g_hkl != 0:
                d_hkl = 1 / g_hkl

                # Bragg condition
                theta = math.asin(wavelength * g_hkl / 2)

                # s = sin(theta) / wavelength = 1 / 2d = |ghkl| / 2 (d =
                # 1/|ghkl|)
                s = g_hkl / 2

                # Calculate Debye-Waller factor
                dw_correction = np.exp(-dw_factors * (s**2))

                # Vectorized computation of g.r for all fractional coords and
                # hkl
                g_dot_r = np.dot(frac_coords, np.transpose([hkl])).T[0]

                # Structure factor = sum of atomic scattering factors (with
                # position factor exp(2j * pi * g.r and occupancies).
                # Vectorized computation.
                f_hkl = np.sum(coeffs * occus * np.exp(2j * np.pi * g_dot_r) * dw_correction)

                # Lorentz polarization correction for hkl
                lorentz_factor = 1 / (math.sin(theta) ** 2 * math.cos(theta))

                # Intensity for hkl is modulus square of structure factor
                i_hkl = (f_hkl * f_hkl.conjugate()).real

                two_theta = math.degrees(2 * theta)

                if is_hex:
                    # Use Miller-Bravais indices for hexagonal lattices
                    hkl = (hkl[0], hkl[1], -hkl[0] - hkl[1], hkl[2])
                # Deal with floating point precision issues
                ind = np.where(np.abs(np.subtract(two_thetas, two_theta)) < self.TWO_THETA_TOL)
                if len(ind[0]) > 0:
                    peaks[two_thetas[ind[0][0]]][0] += i_hkl * lorentz_factor
                    peaks[two_thetas[ind[0][0]]][1].append(tuple(hkl))  # type: ignore[union-attr]
                else:
                    peaks[two_theta] = [i_hkl * lorentz_factor, [tuple(hkl)], d_hkl]
                    two_thetas.append(two_theta)

        # Scale intensities so that the max intensity is 100
        max_intensity = max(v[0] for v in peaks.values())
        x = []
        y = []
        hkls = []
        d_hkls = []
        for key in sorted(peaks):
            v = peaks[key]
            fam = get_unique_families(v[1])
            if v[0] / max_intensity * 100 > self.SCALED_INTENSITY_TOL:  # type: ignore[operator]
                x.append(key)
                y.append(v[0])
                hkls.append([{"hkl": hkl, "multiplicity": mult} for hkl, mult in fam.items()])
                d_hkls.append(v[2])
        nd = DiffractionPattern(x, y, hkls, d_hkls)
        if scaled:
            nd.normalize(mode="max", value=100)
        return nd
