"""Phone number normalization to E.164.

Callers say their numbers in many formats — Deepgram transcribes spoken
digits to numerals, but punctuation, spacing, and country-code presence
vary widely. We strip every non-digit and decide the format from the
digit count.
"""


class InvalidPhoneNumber(ValueError):
    pass


def normalize_phone(raw: str) -> str:
    """Return an E.164-formatted US number, e.g. '+14155550001'.

    Accepts: '(415) 555-0001', '415.555.0001', '14155550001', '+14155550001'.
    Rejects anything that doesn't reduce to a 10-digit or 11-digit-starting-with-1
    sequence.
    """
    if raw is None:
        raise InvalidPhoneNumber("phone number is empty")

    digits = "".join(c for c in raw if c.isdigit())

    if len(digits) == 10:
        return f"+1{digits}"
    if len(digits) == 11 and digits.startswith("1"):
        return f"+{digits}"

    raise InvalidPhoneNumber(
        f"expected a 10-digit US number or 11 digits starting with 1, got {raw!r}"
    )
