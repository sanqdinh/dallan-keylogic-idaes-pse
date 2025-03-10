#################################################################################
# The Institute for the Design of Advanced Energy Systems Integrated Platform
# Framework (IDAES IP) was produced under the DOE Institute for the
# Design of Advanced Energy Systems (IDAES).
#
# Copyright (c) 2018-2023 by the software owners: The Regents of the
# University of California, through Lawrence Berkeley National Laboratory,
# National Technology & Engineering Solutions of Sandia, LLC, Carnegie Mellon
# University, West Virginia University Research Corporation, et al.
# All rights reserved.  Please see the files COPYRIGHT.md and LICENSE.md
# for full copyright and license information.
#################################################################################
"""
Span-Wager property package for CO2.
"""

__author__ = "John Eslick"

# Import IDAES
from idaes.core import declare_process_block_class

import idaes.logger as idaeslog

# Some of these are convenience imports
# pylint: disable=W0611
from idaes.models.properties.general_helmholtz import (
    helmholtz_available,
    HelmholtzParameterBlockData,
    HelmholtzParameterBlock,
    HelmholtzStateBlockData,
    HelmholtzThermoExpressions,
    PhaseType,
    StateVars,
    AmountBasis,
)

from idaes.models.properties.general_helmholtz.helmholtz_state import _StateBlock


# Logger
_log = idaeslog.getLogger(__name__)


def swco2_available():
    """Make sure the compiled IAPWS-95 functions are available. Yes, in Windows
    the .so extention is still used.
    """
    return helmholtz_available()


def htpx(T=None, P=None, x=None):
    """
    Convenience function to calculate enthalpy from temperature and
    either pressure or vapor fraction. This function can be used for inlet
    streams and initialization where temperature is known instead of enthalpy.
    User must provided values for two of T, P, or x.

    Args:
        T: Temperature with units (between 200 and 3000 K)
        P: Pressure with units (between 1 and 1e9 Pa), None if saturated vapor
        x: Vapor fraction [mol vapor/mol total] (between 0 and 1), None if
           superheated or subcooled

    Returns:
        Total molar enthalpy [J/mol].
    """
    prop = HelmholtzParameterBlock(pure_component="CO2")
    prop.construct()
    return prop.htpx(T=T, p=P, x=x)


@declare_process_block_class("SWCO2ParameterBlock")
class SWCO2ParameterBlockData(HelmholtzParameterBlockData):
    """Span-Wager Parameter Block class"""

    CONFIG = HelmholtzParameterBlockData.CONFIG()
    CONFIG.pure_component = "CO2"
    # TODO: Might need to refactor this
    # pylint: disable=protected-access
    CONFIG.get("pure_component")._default = "CO2"


@declare_process_block_class(
    "SWCO2StateBlock",
    block_class=_StateBlock,
)
class SWCO2StateBlockData(HelmholtzStateBlockData):
    """Span-Wager State Block class."""
