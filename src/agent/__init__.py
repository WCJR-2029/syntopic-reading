"""Agent workflows for trail generation"""

import warnings as _warnings

# Harmless, known runpy quirk: `python -m src.agent.trail_generator` first imports
# this package (which imports trail_generator below), so by the time runpy goes to
# execute trail_generator.py as __main__ it's already in sys.modules and runpy warns.
# The module still runs correctly either way - filter must be installed before the
# `from .trail_generator import TrailGenerator` line below, since that's what causes
# the module to be pre-loaded and this warning to fire during the -m invocation.
_warnings.filterwarnings(
    "ignore",
    category=RuntimeWarning,
    message=r".*found in sys\.modules after import of package.*",
)

from .ideation import IdeationAgent
from .research import ResearchAgent
from .polish import PolishAgent
from .trail_generator import TrailGenerator

__all__ = ["IdeationAgent", "ResearchAgent", "PolishAgent", "TrailGenerator"]
