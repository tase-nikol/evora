from dataclasses import dataclass
from typing import List


@dataclass
class CompatibilityResult:
    is_compatible: bool
    breaking_changes: List[str]
    non_breaking_changes: List[str]
