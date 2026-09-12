"""Make the command-line output mean the same thing on every platform.

Python picks the encoding for a redirected stream from the platform locale, so
on Windows `detect_signals.py > out.txt` is written in cp1252 while the same
command in the container writes UTF-8. The project's own output already carries
characters outside ASCII -- the middle dot separating a region from a category,
the multiplication sign in "19.9x the typical swing" -- so the two files differ
byte for byte while reporting identical numbers.

The worse half is that cp1252 cannot encode most of what a business dataset
might contain. A rupee sign is entirely plausible for a project whose regions
are Bengaluru, Delhi NCR, Hyderabad and Mumbai, and printing one to a redirected
stream on Windows does not mangle the output -- it raises UnicodeEncodeError and
takes the whole run down.

So the scripts state their encoding rather than inheriting it.
"""

from __future__ import annotations

import sys


def use_utf8_output() -> None:
    """Write UTF-8 on stdout and stderr regardless of the platform locale.

    Safe to call more than once, and safe where the streams have been replaced
    by something without ``reconfigure`` -- a captured buffer under pytest, or a
    pipe wrapper -- in which case there is nothing to set and nothing to fail.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8")
        except (ValueError, OSError):
            # A detached or already-closed stream. Nothing to write to anyway.
            continue
