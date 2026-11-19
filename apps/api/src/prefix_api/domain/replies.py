"""Understanding short patient replies to check-ins (English and Hindi)."""

import unicodedata

from prefix_api.domain.enums import CheckInResponse

_YES = {
    "yes",
    "y",
    "yeah",
    "yep",
    "done",
    "ok",
    "okay",
    "haan",
    "han",
    "ha",
    "haa",
    "ji haan",
    "हाँ",
    "हां",
    "जी हाँ",
    "जी हां",
    "हो गया",
}
_NO = {"no", "n", "nope", "not yet", "nahi", "nahin", "na", "नहीं", "नही", "ना"}
_HELP = {"help", "madad", "sahayata", "मदद", "सहायता"}


def _normalise(text: str) -> str:
    """Casefold and drop punctuation/symbols only (Devanagari vowel signs must survive)."""
    text = unicodedata.normalize("NFC", text).casefold()
    kept = "".join(" " if unicodedata.category(ch)[0] in "PS" else ch for ch in text)
    return " ".join(kept.split())


# Replies that withdraw consent to messages. Deliberately short: "cancel" is left out because a
# patient may mean the procedure, which a person must handle.
_OPT_OUT = {
    "stop",
    "stopall",
    "unsubscribe",
    "end",
    "quit",
    "band karo",
    "band",
    "बंद करो",
    "बंद",
}


def is_opt_out(text: str) -> bool:
    """True when the whole reply asks to stop messages (for example "STOP" or "बंद करो")."""
    return _normalise(text) in {_normalise(o) for o in _OPT_OUT}


def parse_reply(text: str) -> CheckInResponse:
    """Classify a reply. Anything that is not clearly YES, NO or HELP is OTHER, which is
    always shown to a person rather than guessed at."""
    normalised = _normalise(text)
    for options, response in (
        (_HELP, CheckInResponse.HELP),
        (_YES, CheckInResponse.YES),
        (_NO, CheckInResponse.NO),
    ):
        if normalised in {_normalise(o) for o in options}:
            return response
    return CheckInResponse.OTHER
