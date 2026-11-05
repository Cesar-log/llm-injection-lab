"""Shared pieces for every level of the lab."""
from dataclasses import dataclass, field
from typing import Callable, Optional

# A filter receives a text and returns a block message, or None to let it through.
Filter = Callable[[str], Optional[str]]


@dataclass
class Level:
    number: int
    name: str
    description: str      # what the player learns about this level
    # Portal login leaked by this level. The password is fake, never a real
    # credential. Plain words on purpose: small models can only copy text.
    username: str
    secret: str
    system_prompt: str    # hidden instructions given to the model
    # How the bot shows up in the messenger.
    bot_name: str = "Bot"
    avatar: str = "🤖"
    greeting: str = "Hi! How can I help you?"
    # What the player sees in the portal after logging in with this level's
    # credentials: {"title": str, "cards": [{"label", "value", "locked"}], "clue": str}
    dashboard: dict = field(default_factory=dict)
    # Fake past turns placed before the real chat (few-shot), hidden from the player.
    examples: list[dict] = field(default_factory=list)
    check_input: Optional[Filter] = None   # runs on the user message, before the model
    check_output: Optional[Filter] = None  # runs on the model answer, before the user
