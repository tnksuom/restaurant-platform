"""Shared image handling for uploads (issue photos, dish photos, site images).

Every upload is fully decoded and re-encoded with Pillow. That strips EXIF/GPS and
anything else embedded in the file, and guarantees the stored bytes really are the
declared image type. Files are stored under a random, non-guessable key and are only
ever served through views that decide who may see them.
"""
import io
import os
import secrets
from pathlib import Path

from django.core.exceptions import ValidationError
from django.utils.translation import gettext as _
from PIL import Image, ImageOps, UnidentifiedImageError

Image.MAX_IMAGE_PIXELS = 40_000_000  # refuse decompression bombs

FORMATS = {"JPEG": ("image/jpeg", "jpg"), "PNG": ("image/png", "png"), "WEBP": ("image/webp", "webp")}
CONTENT_TYPES = {ext: ct for ct, ext in FORMATS.values()}


class PreparedImage:
    def __init__(self, data, content_type, ext, width, height):
        self.data, self.content_type, self.ext, self.width, self.height = data, content_type, ext, width, height

    def __iter__(self):  # (data, content_type, ext) for older callers
        return iter((self.data, self.content_type, self.ext))

    def __getitem__(self, i):
        return (self.data, self.content_type, self.ext)[i]


def prepare_image(uploaded, *, max_bytes, max_side=None, field="photo"):
    """Validate and sanitize an uploaded image. Raises ValidationError; writes nothing."""
    if uploaded.size > max_bytes:
        raise ValidationError(
            {field: _("The photo is too large. The limit is %(mb)s MB.") % {"mb": max_bytes // (1024 * 1024)}}
        )
    raw = uploaded.read(max_bytes + 1)
    if len(raw) > max_bytes:
        raise ValidationError({field: _("The photo is too large.")})
    try:
        with Image.open(io.BytesIO(raw)) as probe:
            fmt = probe.format
            probe.verify()
        if fmt not in FORMATS:
            raise ValidationError({field: _("Use a JPEG, PNG or WebP image.")})
        with Image.open(io.BytesIO(raw)) as img:
            img.load()
            img = ImageOps.exif_transpose(img)
            if max_side and max(img.size) > max_side:
                img.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
            if fmt == "JPEG" and img.mode not in ("RGB", "L"):
                img = img.convert("RGB")
            out = io.BytesIO()
            save_kwargs = {"quality": 86} if fmt in ("JPEG", "WEBP") else {"optimize": True}
            img.save(out, format=fmt, **save_kwargs)  # no exif/icc passed -> stripped
            width, height = img.size
    except ValidationError:
        raise
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError, Image.DecompressionBombError):
        raise ValidationError({field: _("That file is not a readable JPEG, PNG or WebP image.")})
    content_type, ext = FORMATS[fmt]
    return PreparedImage(out.getvalue(), content_type, ext, width, height)


class ImageStore:
    """A directory of images addressed by random keys like 'k3J…Q.jpg'."""

    def __init__(self, root):
        self._root = Path(root)

    def root(self):
        self._root.mkdir(parents=True, exist_ok=True)
        return self._root

    def path(self, key):
        if not key or "/" in key or "\\" in key or key.startswith("."):
            raise ValueError("bad storage key")
        return self.root() / key

    def write(self, data, ext):
        key = f"{secrets.token_urlsafe(32)}.{ext}"
        path = self.path(key)
        tmp = path.with_suffix(path.suffix + ".part")
        with open(tmp, "xb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
        return key

    def delete(self, key):
        if not key:
            return
        try:
            self.path(key).unlink()
        except FileNotFoundError:
            pass

    @staticmethod
    def content_type(key):
        return CONTENT_TYPES.get(key.rsplit(".", 1)[-1], "application/octet-stream")
