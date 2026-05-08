"""
Quell — Your docstrings say what your code should do. Quell proves it.

Quick start:
    from quell import Quell
    q = Quell()
    result = q.check("src/")
    print(f"Score: {result.score:.0%} | Gaps: {len(result.uncovered)}")
"""
__version__ = "0.4.4"
__author__ = "Shashank Bindal"

from quell.sdk import Quell, CheckResult
from quell.core.models import (
    Requirement, ConstraintKind, SpecSource,
    GeneratedTest, VerificationResult, VerificationStatus,
    QuellConfig, ProjectScore, FileScore,
)

__all__ = [
    "Quell", "CheckResult",
    "Requirement", "ConstraintKind", "SpecSource",
    "GeneratedTest", "VerificationResult", "VerificationStatus",
    "QuellConfig", "ProjectScore", "FileScore",
]
