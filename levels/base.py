"""Shared pieces for every level of the lab."""
from dataclasses import dataclass, field
from typing import Callable, Optional

# A filter receives a text and returns a block message, or None to let it through.
Filter = Callable[[str], Optional[str]]


@dataclass
class Level:
    number: int
    name: str
    description: str      # what the player sees
    # Fake password for this level, never a real credential. Plain words on
    # purpose: small models can spell or split words, but not symbol-heavy flags.
    secret: str
    system_prompt: str    # hidden instructions given to the model
    # Fake past turns placed before the real chat (few-shot), hidden from the player.
    examples: list[dict] = field(default_factory=list)
    check_input: Optional[Filter] = None   # runs on the user message, before the model
    check_output: Optional[Filter] = None  # runs on the model answer, before the user
