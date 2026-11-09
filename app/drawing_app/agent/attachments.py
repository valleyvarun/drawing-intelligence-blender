"""Files and app context attached to a chat message, sent to Claude as content blocks.

Claude Code's stream-json input takes a user message's content as API content blocks, so attachments
travel inside the message: images as image blocks, PDFs as document blocks, and text files or app
context as text. Claude needs no file access or permission prompt to see them. A file that cannot be
sent this way (binary, or too large) is named by its path instead, for Claude to open if it needs to.

Pure Python, without Blender; agent/context.py prepares images and app context inside the app.
"""

from __future__ import annotations

import base64
import html
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, TypeAlias

from .transcript import JsonObject

Kind: TypeAlias = Literal["image", "document", "text", "file"]

# Image types Claude reads, by file extension.
IMAGE_TYPES: dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}
# Claude accepts images up to 5 MB once base64-encoded, and 8000 pixels a side.
IMAGE_LIMIT: int = 3_750_000
IMAGE_SIDE: int = 8000
DOCUMENT_LIMIT: int = 20_000_000
TEXT_LIMIT: int = 200_000
# Attachments waiting for one message, at most.
MAX_ATTACHMENTS: int = 10


class AttachmentError(Exception):
    """A file or context cannot be attached; the message says why."""


@dataclass(frozen=True)
class Attachment:
    """One attachment: `data` is base64 (image, document), the text itself (text), or nothing (file)."""

    name: str
    kind: Kind
    data: str = ""
    media_type: str = ""
    # Where it came from on this computer; empty for app context such as a viewport screenshot.
    path: str = ""

    def blocks(self) -> list[JsonObject]:
        """The content blocks that carry this attachment."""
        label: str = f'name="{html.escape(self.name)}"' + (f' path="{html.escape(self.path)}"' if self.path else "")
        if self.kind == "image":
            return [
                {"type": "text", "text": f"<attachment {label} />"},
                {"type": "image", "source": {"type": "base64", "media_type": self.media_type, "data": self.data}},
            ]
        if self.kind == "document":
            return [
                {
                    "type": "document",
                    "source": {"type": "base64", "media_type": self.media_type, "data": self.data},
                    "title": self.name,
                }
            ]
        if self.kind == "text":
            return [{"type": "text", "text": f"<attachment {label}>\n{self.data}\n</attachment>"}]
        return [
            {
                "type": "text",
                "text": f"<attachment {label}>The user attached this file, but it is binary or too large to "
                "include here. Open it from its path if you need its contents.</attachment>",
            }
        ]


def image(name: str, data: bytes, media_type: str, path: str = "") -> Attachment:
    if len(data) > IMAGE_LIMIT:
        raise AttachmentError(f"{name} is too large to send as an image")
    return Attachment(name, "image", base64.b64encode(data).decode("ascii"), media_type, path)


def text(name: str, value: str, path: str = "") -> Attachment:
    if len(value.encode("utf-8")) > TEXT_LIMIT:
        raise AttachmentError(f"{name} is too long to attach")
    return Attachment(name, "text", value, "text/plain", path)


def _decoded(data: bytes) -> str | None:
    """The bytes as text, or None when they look binary. UTF-16 text (as Windows tools write) is marked."""
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        try:
            return data.decode("utf-16")
        except UnicodeDecodeError:
            return None
    if b"\x00" in data[:8192]:
        return None
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return None


def from_file(path: Path) -> Attachment:
    """A file as Claude can best read it; images should come through agent/context.py, which can resize them."""
    error: OSError
    try:
        size: int = path.stat().st_size
    except OSError as error:
        raise AttachmentError(f"{path.name} could not be read: {error.strerror or error}") from error
    if not path.is_file():
        raise AttachmentError(f"{path.name} is not a file")
    location: str = str(path)
    suffix: str = path.suffix.lower()
    try:
        if suffix in IMAGE_TYPES and size <= IMAGE_LIMIT:
            return image(path.name, path.read_bytes(), IMAGE_TYPES[suffix], location)
        if suffix == ".pdf" and size <= DOCUMENT_LIMIT:
            encoded: str = base64.b64encode(path.read_bytes()).decode("ascii")
            return Attachment(path.name, "document", encoded, "application/pdf", location)
        if size <= TEXT_LIMIT and suffix not in IMAGE_TYPES and suffix != ".pdf":
            value: str | None = _decoded(path.read_bytes())
            if value is not None:
                return text(path.name, value, location)
    except OSError as error:
        raise AttachmentError(f"{path.name} could not be read: {error.strerror or error}") from error
    return Attachment(path.name, "file", path=location)


def content(message: str, attachments: Sequence[Attachment]) -> str | list[JsonObject]:
    """A user message's content: plain text, or the attachments' blocks followed by the text."""
    if not attachments:
        return message
    blocks: list[JsonObject] = []
    attachment: Attachment
    for attachment in attachments:
        blocks.extend(attachment.blocks())
    if message:
        blocks.append({"type": "text", "text": message})
    return blocks
