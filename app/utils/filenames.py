"""File names: what people see vs what storage will accept.

Supabase Storage rejects object keys with many ordinary filename characters —
square brackets, some punctuation, non-ASCII letters — and returns
"InvalidKey". A user uploading "...22_TRUCKS[10586].pdf" got a raw 400 error.
The upload code only stripped slashes and "..", so anything else reached
storage untouched.

Two separate names fix that without losing anything:
  * display_name — what the person uploaded, shown back to them as-is;
  * storage_key_name — letters, digits, dot, dash, underscore only, always
    accepted by storage. The key is never shown, so it can be plain.
"""
import re
import unicodedata

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")
_MAX_KEY = 120


def display_name(filename: str | None, default: str = "document") -> str:
    """The uploaded name with only path tricks removed."""
    name = (filename or "").replace("\\", "/").split("/")[-1].replace("..", "").strip()
    return name or default


def storage_key_name(filename: str | None, default: str = "document") -> str:
    """A storage-safe version of the name, keeping its extension."""
    name = display_name(filename, default)
    # Accented letters become plain ones (é -> e) before anything is dropped.
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    stem, dot, ext = name.rpartition(".")
    if not dot:
        stem, ext = name, ""
    stem = _UNSAFE.sub("_", stem).strip("._-") or default
    ext = _UNSAFE.sub("", ext)[:10]
    stem = re.sub(r"_+", "_", stem)[: _MAX_KEY - len(ext) - 1]
    return f"{stem}.{ext}" if ext else stem
