"""Private storage for checklist issue photos (see core.images for how images are sanitized).

Files live outside any public web root and are served only through a permission-checked view.
"""
from django.conf import settings

from core.images import ImageStore
from core.images import prepare_image as _prepare


def _store():
    return ImageStore(settings.PRIVATE_UPLOAD_ROOT)


def storage_root():
    return _store().root()


def path_for(key):
    return _store().path(key)


def prepare_image(uploaded):
    """Returns (bytes, content_type, ext). Raises ValidationError; nothing is written."""
    return tuple(_prepare(uploaded, max_bytes=settings.ISSUE_PHOTO_MAX_BYTES))


def write_private(data, ext):
    return _store().write(data, ext)


def delete_private(key):
    _store().delete(key)
