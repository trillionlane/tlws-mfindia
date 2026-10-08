"""AMC factsheet parsers and their shared contract.

One submodule per AMC (``absl.py`` today). The base contract
(``base.AmcFactsheetParser`` + dataclasses) is layout-agnostic so the
loader only ever sees ``SchemeFactsheet`` objects.
"""

from .absl import AbSLParser
from .base import (
    AmcFactsheetParser,
    NoMatchError,
    NameMatcher,
    PerformanceRow,
    QuantMetrics,
    SchemeFactsheet,
    to_num,
)
from .download import AbSLFactsheetClient, FactsheetDocument

__all__ = [
    "AbSLParser",
    "AbSLFactsheetClient",
    "FactsheetDocument",
    "AmcFactsheetParser",
    "NameMatcher",
    "NoMatchError",
    "PerformanceRow",
    "QuantMetrics",
    "SchemeFactsheet",
    "to_num",
]
