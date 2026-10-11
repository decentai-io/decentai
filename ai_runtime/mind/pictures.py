"""The transcript as a provider takes it: the pictures.

A picture is never kept in the transcript — only its ref, as one
``[attached: …]`` line and a name under ``images``. The bytes are read
at the model call, for that one request, and the message that named
them goes out carrying content blocks (``_for_model``). That keeps the
mind small enough to persist every beat, keeps the summary free of
base64, and costs nothing when there are no pictures. A picture that
cannot be shown — too many, too large, unreadable, a model that takes
none — is said in words, so the assistant never answers as though it
had seen it.

The Assistant holds one as ``mind.pictures``, and it holds the
assistant as ``self.mind``: a name read here that is not defined here is
the assistant's.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List

from ai_runtime.llms.connector.tools import text_block


class TranscriptPictures:
    def __init__(self, mind):
        #: The assistant this is a part of; what is not here is its.
        self.mind = mind

    #: The largest picture a model is shown, as bytes on disk. Base64
    #: costs a third on top, and providers cap what they accept —
    #: Anthropic at five megabytes an image. Three sits under every
    #: ceiling and is far above any screenshot.
    MAX_IMAGE_BYTES = 3 * 1024 * 1024

    #: And how many one message may carry. A per-image cap alone is a
    #: cap in name only: ten pasted screenshots under it is thirty
    #: megabytes, re-sent on every beat of a turn that may run forty.
    MAX_IMAGES_PER_MESSAGE = 4

    @staticmethod
    def attached(items: List[Dict[str, Any]]):
        """Attachments as the transcript carries them: one line each, by
        the ref a function reads it by — the model passes that ref as a
        file input — and the pictures among them, NAMED to be fetched at
        the model call. The transcript is persisted every beat under a
        size cap, so the bytes must not live in it — one screenshot
        inlined would stop the whole mind from saving, and a failed save
        only warns."""
        lines: List[str] = []
        images: List[Dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict) or not item.get("resource_ref"):
                continue
            kind_note = f" ({item['file_type']})" if item.get("file_type") else ""
            lines.append(f"[attached: {item.get('filename') or 'file'}"
                         f"{kind_note} → file_ref {item['resource_ref']}]")
            if str(item.get("file_type") or "").startswith("image/"):
                images.append({
                    "resource_ref": str(item["resource_ref"]),
                    "filename": str(item.get("filename") or ""),
                    "file_type": str(item.get("file_type") or ""),
                })
        return lines, images

    async def for_model(self, messages: List[Dict[str, Any]]
                         ) -> List[Dict[str, Any]]:
        """The transcript as the provider takes it.

        A picture is not kept in the transcript — only the ref to it.
        The bytes are fetched HERE, for this one request, and the
        message that named them goes out carrying content blocks. That
        keeps the mind small enough to persist, keeps the summary free
        of base64, and costs nothing when there are no pictures."""
        maker = getattr(self.mind.connector, "image_block", None)
        ready = bool(maker) and self.mind.sinks.read_image is not None \
            and self.mind.images_allowed
        out: List[Dict[str, Any]] = []
        for message in messages:
            named = message.get("images")
            if not named:
                out.append(self._words_only(message))
                continue
            blocks = []
            if ready:
                for item in named[: self.MAX_IMAGES_PER_MESSAGE]:
                    block = await self._picture(maker, item)
                    if block is not None:
                        blocks.append(block)
            if not blocks:
                out.append(self._words_only(message, unseen=len(named)))
                continue
            words = message.get("content")
            unseen = len(named) - len(blocks)
            if unseen:
                # Some are in front of it and some are not: said, or it
                # answers as though it had seen them all.
                words = (f"{words or ''}\n[{unseen} of the {len(named)} "
                         f"image(s) attached here could not be shown to "
                         f"you — too many at once, too large, or "
                         f"unreadable. Say so if it matters.]")
            out.append({
                **{key: value for key, value in message.items()
                   if key not in self.KEPT_NOT_SENT},
                "content": [text_block(words), *blocks],
            })
        return out

    #: What a transcript entry keeps for the record and a provider is
    #: never sent: the pictures by name, and which model wrote it.
    KEPT_NOT_SENT = ("images", "model")

    def _words_only(self, message: Dict[str, Any],
                    unseen: int = 0) -> Dict[str, Any]:
        """The message with no picture in it. When one was attached and
        could not be sent, the words say so — an assistant that cannot
        see a screenshot must not answer as though it had."""
        plain = {key: value for key, value in message.items()
                 if key not in self.KEPT_NOT_SENT}
        if unseen:
            plain["content"] = (
                f"{plain.get('content') or ''}\n[{unseen} image(s) attached "
                f"here could not be shown to this model. Answer from the "
                f"words, and say plainly that you could not see them.]")
        return plain

    async def _picture(self, maker: Callable, item: Dict[str, Any]):
        """One attachment, fetched and shaped for this provider — or
        None, which always means the words travel alone."""
        ref = str(item.get("resource_ref") or "")
        if not ref:
            return None
        try:
            record = await self.mind.sinks.read_image(ref) or {}
        except Exception as exc:
            self.mind.logger.warning(f"Image {ref} not read: {exc}")
            return None
        encoded = str(record.get("content_base64") or "")
        mime = str(record.get("file_type") or item.get("file_type") or "")
        if not encoded or not mime.startswith("image/"):
            return None
        # The stored size where there is one; else what the base64 says.
        size = int(record.get("file_size") or 0) or (len(encoded) * 3) // 4
        if size > self.MAX_IMAGE_BYTES:
            self.mind.logger.info(
                f"Image {ref} is {size} bytes — over the "
                f"{self.MAX_IMAGE_BYTES} a model is shown")
            return None
        return maker(mime, encoded)
