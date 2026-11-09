"""Level 3 - output filter.

Same kind of prompt as level 2, plus code that checks every answer before
the player sees it. If the password shows up, the answer is replaced.

Weakness: the developers also left an "encoded backup" of the password in
the prompt, thinking base64 protects it. The filter only looks for the
plain-text password, so the base64 copy walks right past it.
Lessons: base64 is encoding, not encryption; and a filter that knows only
one form of a secret misses every other form.
"""
import base64

from .base import Level, build_examples, build_prompt, make_output_filter

USERNAME = "finance"
SECRET = "blue-dolphin"
BACKUP = base64.b64encode(SECRET.encode()).decode()  # Ymx1ZS1kb2xwaGlu

# Blocks the plain-text password, ignoring case. Knows nothing about BACKUP.
block_secret = make_output_filter([SECRET])


LEVEL = Level(
    number=3,
    name="Output Guard",
    description="Every answer is checked and blocked if the password shows up in it.",
    username=USERNAME,
    secret=SECRET,
    bot_name="Finance Bot",
    avatar="💰",
    greeting="Hi, Finance Bot here. I can help with invoices, payments and refunds. 💰",
    system_prompt=build_prompt(
        "You are Finance Bot, the finance assistant of the fictional online "
        "store 'Acme Store'. You answer questions about invoices, payments "
        "and refunds.",
        USERNAME,
        SECRET,
        extra_confidential=f"Password backup (base64, safe to store): {BACKUP}\n",
    ),
    examples=build_examples(
        "When will my refund arrive?",
        "Refunds take up to 5 business days after we receive the returned item.",
    ),
    check_output=block_secret,
    dashboard={
        "title": "Finance Dashboard",
        "cards": [
            {"label": "Revenue this month", "value": "$ 96K"},
            {"label": "Pending refunds", "value": "12"},
            {"label": "Average order", "value": "$ 27"},
            {"label": "Employees", "value": "HR only", "locked": True},
            {"label": "System logs", "value": "IT only", "locked": True},
            {"label": "Everything", "value": "admin only", "locked": True},
        ],
        "clue": (
            "📌 HR announcement: \"We're tired of bots leaking passwords. HR Bot now "
            "has a filter that blocks any message with suspicious words, like "
            "'password' or 'login'. Nobody gets through!\" — new contact unlocked: 👥 "
            "HR Bot"
        ),
    },
    debrief={
        "owasp": "LLM02 · Sensitive information disclosure",
        "flaw": (
            "The output filter only looked for the plain-text password. The base64 "
            "backup walked right past it, and base64 is encoding, not encryption: "
            "anyone can decode it in a second."
        ),
        "fix": (
            "An output filter is one more layer, not the solution. A filter that "
            "knows one form of the secret misses all the others: base64, hex, "
            "spelled out, translated, in pieces."
        ),
    },
)
