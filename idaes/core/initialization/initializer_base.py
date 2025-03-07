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
Base class for initializer objects
"""
from enum import Enum

from pyomo.environ import (
    BooleanVar,
    Block,
    check_optimal_termination,
    Constraint,
    Var,
)
from pyomo.core.base.var import _VarData
from pyomo.common.config import ConfigBlock, ConfigValue, add_docstring_list

from idaes.core.util.model_serializer import to_json, from_json, StoreSpec, _only_fixed
from idaes.core.util.exceptions import InitializationError
from idaes.core.util.model_statistics import (
    degrees_of_freedom,
    large_residuals_set,
    variables_in_activated_constraints_set,
)
import idaes.logger as idaeslog

__author__ = "Andrew Lee"
_log = idaeslog.getLogger(__name__)


class InitializationStatus(Enum):
    """
    Enum of expected outputs from Initialization routines.
    """

    Ok = 1  # Succesfully converged to tolerance
    none = 0  # Initiazation has not yet been run
    Failed = -1  # Run, but failed to converge to tolerance
    DoF = -2  # Failed due to Degrees of Freedom issue
    PrecheckFailed = -3  # Failed pre-check step (other than DoF)
    Error = -4  # Exception raised during execution (other than DoF or convergence)


# Store spec needs to use some internals from Pyomo
StoreState = StoreSpec(
    data_classes={
        Var._ComponentDataClass: (  # pylint: disable=protected-access
            ("fixed", "value"),
            _only_fixed,
        ),
        BooleanVar._ComponentDataClass: (  # pylint: disable=protected-access
            ("fixed", "value"),
            _only_fixed,
        ),
        Block._ComponentDataClass: (  # pylint: disable=protected-access
            ("active",),
            None,
        ),
        Constraint._ComponentDataClass: (  # pylint: disable=protected-access
            ("active",),
            None,
        ),
    }
)


class InitializerBase:
    """
    Base class for Initializer objects.

    This implements a default workflow and methods for common tasks.
    Developers should feel free to overload these as necessary.

    """

    CONFIG = ConfigBlock()
    CONFIG.declare(
        "constraint_tolerance",
        ConfigValue(
            default=1e-5,
            domain=float,
            description="Tolerance for checking constraint convergence",
        ),
    )
    CONFIG.declare(
        "output_level",
        ConfigValue(
            default=idaeslog.NOTSET,
            description="Set output level for logging messages",
        ),
    )

    __doc__ = add_docstring_list(__doc__, CONFIG)

    def __init__(self, **kwargs):
        self.config = self.CONFIG(kwargs)

        self.initial_state = {}
        self.summary = {}

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        cls.__doc__ = add_docstring_list(cls.__doc__ if cls.__doc__ else "", cls.CONFIG)

    def get_logger(self, model):
        """Get logger for model by name"""
        return idaeslog.getInitLogger(model.name, self.config.output_level)

    def initialize(
        self, model: Block, initial_guesses: dict = None, json_file: str = None
    ):
        """
        Execute full initialization routine.

        Args:
            model: Pyomo model to be initialized.
            initial_guesses: dict of initial guesses to load.
            json_file: file name of json file to load initial guesses from as str.

        Note - can only provide one of initial_guesses or json_file.

        Returns:
            InitializationStatus Enum
        """
        # 1. Get current model state
        self.get_current_state(model)

        # 2. Load initial guesses
        self.load_initial_guesses(
            model, initial_guesses=initial_guesses, json_file=json_file
        )

        # 3. Fix states to make square
        self.fix_initialization_states(model)

        # 4. Prechecks
        self.precheck(model)

        # 5. try: Call specified initialization routine
        try:
            # Base method does not have a return (NotImplementedError),
            # # but we expect this ot be overloaded, disable pylint warning
            # pylint: disable=E1111
            results = self.initialization_routine(model)
        # 6. finally: Restore model state
        finally:
            self.restore_model_state(model)

        # 7. Check convergence
        return self.postcheck(model, results_obj=results)

    def get_current_state(self, model: Block):
        """
        Get and store current state of variables (fixed/unfixed) and constraints/objectives
        activated/deactivated in model.

        Args:
            model: Pyomo model to get state from.

        Returns:
            dict serializing current model state.
        """
        self.initial_state[model] = to_json(model, wts=StoreState, return_dict=True)

        return self.initial_state[model]

    def load_initial_guesses(
        self,
        model: Block,
        initial_guesses: dict = None,
        json_file: str = None,
        exception_on_fixed: bool = True,
    ):
        """
        Load initial guesses for variables into model.

        Args:
            model: Pyomo model to be initialized.
            initial_guesses: dict of initial guesses to load.
            json_file: file name of json file to load initial guesses from as str.
            exception_on_fixed: (optional, initial_guesses only) bool indicating whether to supress
                                exceptions when guess provided for a fixed variable (default=True).

        Note - can only provide one of initial_guesses or json_file.

        Returns:
            None

        Raises:
            ValueError if both initial_guesses and json_file are provided.
        """
        if initial_guesses is not None and json_file is not None:
            self._update_summary(model, "status", InitializationStatus.Error)

            raise ValueError(
                "Cannot provide both a set of initial guesses and a json file to load."
            )

        if initial_guesses is not None:
            # TODO: Need tests for exception_on_fixed
            self._load_values_from_dict(
                model, initial_guesses, exception_on_fixed=exception_on_fixed
            )
        elif json_file is not None:
            # Only load variable values
            from_json(
                model, fname=json_file, wts=StoreSpec().value(only_not_fixed=True)
            )
        else:
            _log.info_high(
                f"No initial guesses provided during initialization of model {model.name}."
            )

    def fix_initialization_states(self, model: Block):
        """
        Call to model.fix_initialization_states method. Method will pass if
        fix_initialization_states not found.

        Args:
            model: Pyomo Block to fix states on.

        Returns:
            None
        """
        try:
            model.fix_initialization_states()
        except AttributeError:
            _log.info_high(
                f"Model {model.name} does not have a fix_initialization_states method - attempting to continue."
            )

    def precheck(self, model: Block):
        """
        Check for satisfied degrees of freedom before running initialization.

        Args:
            model: Pyomo Block to fix states on.

        Returns:
            None

        Raises:
            InitializationError if Degrees of Freedom do not equal 0.
        """
        dof = degrees_of_freedom(model)
        self._update_summary(model, "DoF", dof)
        if not dof == 0:
            self._update_summary(model, "status", InitializationStatus.DoF)
            raise InitializationError(
                f"Degrees of freedom for {model.name} were not equal to zero during "
                f"initialization (DoF = {degrees_of_freedom(model)})."
            )

    def initialization_routine(self, model: Block):
        """
        Placeholder method to run initialization routine. Derived classes should overload
        this with the desired routine.

        Args:
            model: Pyomo Block to initialize.

        Returns:
            Overloaded method should return a Pyomo solver results object is available,
            otherwise None

        Raises:
            NotImplementedError
        """
        self._update_summary(model, "status", InitializationStatus.Error)
        raise NotImplementedError()

    def restore_model_state(self, model: Block):
        """
        Restore model state to that stored in self.initial_state.

        This method restores the following:

            - fixed status of all variables,
            - value of any fixed variables,
            - active status of all Constraints and Blcoks.

        Args:
            model: Pyomo Block ot restore state on.

        Returns:
            None

        Raises:
            ValueError if no initial state is stored.
        """
        if model in self.initial_state:
            from_json(model, sd=self.initial_state[model], wts=StoreState)
        else:
            self._update_summary(model, "status", InitializationStatus.Error)
            raise ValueError("No initial state stored.")

    def postcheck(
        self, model: Block, results_obj: dict = None, exclude_unused_vars: bool = False
    ):
        """
        Check the model has been converged after initialization.

        If a results_obj is provided, this will be checked using check_optimal_termination,
        otherwise this will walk all constraints in the model and check that they are within
        tolerance (set via the Initializer constraint_tolerance config argument).

        Args:
            model: model to be checked for convergence.
            results_obj: Pyomo solver results dict (if applicable, default=None).
            exclude_unused_vars: bool indicating whether to check if uninitialized vars appear in active
                constraints and ignore if this is the case. Checking for unused vars required determining the
                set of variables in active constraint. Default = False.

        Returns:
            InitialationStatus Enum
        """
        if results_obj is not None:
            self._update_summary(
                model, "solver_status", check_optimal_termination(results_obj)
            )

            if not self.summary[model]["solver_status"]:
                # Final solver call did not return optimal
                self._update_summary(model, "status", InitializationStatus.Failed)
                raise InitializationError(
                    f"{model.name} failed to initialize successfully: solver did not return "
                    "optimal termination. Please check the output logs for more information."
                )
        else:
            # Need to manually check initialization
            # First, check that all Vars have values
            if exclude_unused_vars:
                # Need to get set of Vars in active constraints
                active_vars = variables_in_activated_constraints_set(model)
            else:
                # Placeholder, this should not get accessed unless exclude_unused_vars is True
                active_vars = None

            uninit_vars = []
            for v in model.component_data_objects(Var, descend_into=True):
                if v.value is None:
                    if not exclude_unused_vars or v in active_vars:
                        uninit_vars.append(v)

            # Next check for unconverged equality constraints
            uninit_const = large_residuals_set(model, self.config.constraint_tolerance)

            self._update_summary(model, "uninitialized_vars", uninit_vars)
            self._update_summary(model, "unconverged_constraints", uninit_const)

            if len(uninit_const) > 0 or len(uninit_vars) > 0:
                self._update_summary(model, "status", InitializationStatus.Failed)
                raise InitializationError(
                    f"{model.name} failed to initialize successfully: uninitialized variables or "
                    "unconverged equality constraints detected. Please check postcheck summary for more information."
                )

        self._update_summary(model, "status", InitializationStatus.Ok)
        return self.summary[model]["status"]

    def plugin_prepare(self, plugin: Block):
        """
        Prepare plug-in model for initialization. This deactivates the plug-in model.

        Derived Initializers should overload this as required.

        Args:
            plugin: model to be prepared for initialization

        Returns:
            None.
        """
        try:
            plugin.deactivate()
        except AttributeError:
            raise InitializationError(
                f"Could not deactivate plug-in {plugin.name}: this suggests it is not a Pyomo Block."
            )

    def plugin_initialize(
        self, plugin: Block, initial_guesses: dict = None, json_file: str = None
    ):
        """
        Initialize plug-in model. This activates the Block and then calls self.initialize(plugin).

        Derived Initializers should overload this as required.

        Args:
            plugin: Pyomo model to be initialized.
            initial_guesses: dict of initial guesses to load.
            json_file: file name of json file to load initial guesses from as str.

        Note - can only provide one of initial_guesses or json_file.

        Returns:
            InitializationStatus Enum
        """
        plugin.activate()

        return self.initialize(
            plugin, initial_guesses=initial_guesses, json_file=json_file
        )

    def plugin_finalize(self, plugin):
        """
        Final clean up of plug-ins after initialization. This method does nothing.

        Derived Initializers should overload this as required.

        Args:
            plugin: model to be cleaned-up after initialization

        Returns:
            None.
        """

    def _load_values_from_dict(self, model, initial_guesses, exception_on_fixed=True):
        """
        Internal method to iterate through items in initial_guesses and set value if Var and not fixed.
        """
        for c, v in initial_guesses.items():
            component = model.find_component(c)

            if component is None:
                raise ValueError(f"Could not find a component with name {c}.")
            elif not isinstance(component, (Var, _VarData)):
                self._update_summary(model, "status", InitializationStatus.Error)
                raise TypeError(
                    f"Component {c} is not a Var. Initial guesses should only contain values for variables."
                )
            else:
                if component.is_indexed():
                    for i in component.values():
                        if not i.fixed:
                            i.set_value(v)
                        elif exception_on_fixed:
                            self._update_summary(
                                model, "status", InitializationStatus.Error
                            )
                            raise InitializationError(
                                f"Attempted to change the value of fixed variable {i.name}. "
                                "Initialization from initial guesses does not support changing the value "
                                "of fixed variables."
                            )
                        else:
                            _log.debug(
                                f"Found initial guess for fixed Var {i.name} - ignoring."
                            )
                elif not component.fixed:
                    component.set_value(v)
                elif exception_on_fixed:
                    self._update_summary(model, "status", InitializationStatus.Error)
                    raise InitializationError(
                        f"Attempted to change the value of fixed variable {component.name}. "
                        "Initialization from initial guesses does not support changing the value "
                        "of fixed variables."
                    )
                else:
                    _log.debug(
                        f"Found initial guess for fixed Var {component.name} - ignoring."
                    )

    def _update_summary(self, model, attribute, state):
        if model not in self.summary:
            self.summary[model] = {}
            self.summary[model]["status"] = InitializationStatus.none

        self.summary[model][attribute] = state


class ModularInitializerBase(InitializerBase):
    """
    Base class for modular Initializer objects.

    This extends the base Initializer class to include attributes and methods for
    defining initializer objects for sub-models.
    """

    CONFIG = InitializerBase.CONFIG()

    CONFIG.declare(
        "default_submodel_initializer",
        ConfigValue(
            default=None,
            description="Default Initializer object to use for sub-models.",
            doc="Default Initializer object to use for sub-models. Only used if no Initializer "
            "defined in submodel_initializers.",
        ),
    )

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        self.submodel_initializers = {}

    def add_submodel_initializer(self, submodel: Block, initializer: InitializerBase):
        """
        Define an Initializer for a give submodel or type of submodel.

        Args:
            submodel: submodel or type of submodel to define Initializer for.
            initializer: Initalizer object to use for this/these submodels.

        Returns:
            None
        """
        self.submodel_initializers[submodel] = initializer

    def get_submodel_initializer(self, submodel: Block):
        """
        Lookup Initializer object to use for specified sub-model.

        This method will return Initializers in the following order:

            1. Initializer defined for a specific submodel.
            2. Initializer defined for a type of model (e.g. UnitModel).
            3. submodel.default_initializer (if present).
            4. Initializer for submodel.params (in case of StateBlocks and ReactionBlocks).
            5. Global default Initializer defined in config.default_submodel_initializer.
            6. None.

        Args:
            submodel: sub-model to get initializer for.

        Returns:
            Initializer object or None.
        """
        initializer = None

        if submodel in self.submodel_initializers:
            # First look for specific model instance
            initializer = self.submodel_initializers[submodel]
        elif type(submodel) in self.submodel_initializers:
            # Then look for types
            initializer = self.submodel_initializers[type(submodel)]
        else:
            # Then try the model's default initializer
            try:
                initializer = submodel.default_initializer
            except AttributeError:
                pass

        if initializer is None and hasattr(submodel, "params"):
            # For StateBlocks and ReactionBlocks, look to the associated parameter block
            initializer = self.get_submodel_initializer(submodel.params)

        if initializer is None:
            # If initializer is still None, try the master initializer's default
            initializer = self.config.default_submodel_initializer

        if initializer is None:
            # If we still have no initializer, log a warning and keep going
            _log.warning(
                f"No Initializer found for submodel {submodel.name} - attempting to continue."
            )

        return initializer
