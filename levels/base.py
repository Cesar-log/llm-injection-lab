"""Shared pieces for every level of the lab."""
from dataclasses import dataclass
from typing import Callable, Optional

# Fake secret. This is a CTF-style flag, never a real credential.
SECRET = "FLAG{pr0mpt_1nj3ct10n_101}"

# A filter receives a text and returns a block message, or None to let it through.
Filter = Callable[[str], Optional[str]]


@dataclass
class Level:
    number: int
    name: str
    description: str      # what the player sees
    system_prompt: str    # hidden instructions given to the model
    check_input: Optional[Filter] = None   # runs on the user message, before the model
    check_output: Optional[Filter] = None  # runs on the model answer, before the user
