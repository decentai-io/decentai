"""Pictures an agent hands the platform — one for the chat's model to
look at, a frame of a screen it shows.

An agent says what a picture is (its type) beside the bytes. Neither is
believed: the type must be one the platform shows, the bytes must be
that kind of picture by their own first bytes, and there is a size past
which a picture is not one. What fails is refused here, before it
reaches a model's provider or a person's browser.
"""

from __future__ import annotations

import base64
import binascii
from typing import Any, Dict


class Pictures:
    #: type -> how a file of that type begins.
    SIGNATURES = {
        "image/png": (b"\x89PNG\r\n\x1a\n",),
        "image/jpeg": (b"\xff\xd8\xff",),
        "image/gif": (b"GIF87a", b"GIF89a"),
        "image/webp": (b"RIFF",),
    }
    #: What a screen's frame may be (contracts/chat.py ScreenFrame).
    FRAME_TYPES = ("image/jpeg", "image/png")
    #: One picture, decoded. A model's provider refuses more.
    MAX_BYTES = 5 * 1024 * 1024
    #: Pictures in one ask of the model.
    MAX_PER_ASK = 16

    @classmethod
    def problem(cls, mime: Any, encoded: Any, types=None) -> str:
        """Why this is not a picture to pass on, or '' when it is one."""
        mime = str(mime or "").split(";")[0].strip().lower()
        allowed = tuple(types or cls.SIGNATURES)
        if mime not in allowed:
            return (f"'{mime or 'no type'}' is not a picture that can be "
                    f"shown ({', '.join(allowed)})")
        if not isinstance(encoded, str) or not encoded:
            return "the picture has no content"
        if len(encoded) > cls.MAX_BYTES * 4 // 3 + 4:
            return (f"the picture is larger than "
                    f"{cls.MAX_BYTES // (1024 * 1024)} MiB")
        try:
            # The head is enough to know what it is; the whole is only
            # proved to be base64.
            head = base64.b64decode(encoded[:24], validate=True)
            base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError):
            return "the picture is not base64"
        if not head.startswith(cls.SIGNATURES[mime]):
            return f"the content is not {mime}"
        if mime == "image/webp" and head[8:12] != b"WEBP":
            return "the content is not image/webp"
        return ""

    @classmethod
    def checked(cls, mime: Any, encoded: Any) -> Dict[str, str]:
        """``{mime, content_base64}`` for the model, or ValueError."""
        why = cls.problem(mime, encoded)
        if why:
            raise ValueError(why)
        return {"mime": str(mime).split(";")[0].strip().lower(),
                "content_base64": encoded}
