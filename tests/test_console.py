"""The command line writes UTF-8 wherever it runs.

These go through a subprocess with its output redirected to a file, because that
is the only arrangement where the bug appears: Python picks a redirected
stream's encoding from the platform locale, and pytest has already replaced
sys.stdout by the time a test body runs.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# A rupee sign, a middle dot and a multiplication sign: the first is absent from
# cp1252 entirely, and the other two are what the briefings already print.
SAMPLE = "cost ₹100 · 19.9×"


def _run(source: str, target: Path) -> int:
    with target.open("wb") as handle:
        return subprocess.run(
            [sys.executable, "-c", source],
            stdout=handle,
            stderr=subprocess.DEVNULL,
            cwd=ROOT,
        ).returncode


def test_redirected_output_is_utf8_whatever_the_locale(tmp_path: Path) -> None:
    """The bytes on disk must be the same ones the container would write."""
    target = tmp_path / "out.txt"
    code = _run(
        "import sys; sys.path.insert(0, 'src')\n"
        "from rootsignal.console import use_utf8_output\n"
        "use_utf8_output()\n"
        f"print({SAMPLE!r})\n",
        target,
    )
    assert code == 0, "writing a rupee sign to a redirected stream must not fail"
    assert target.read_bytes().decode("utf-8").strip() == SAMPLE
    assert b"\xe2\x82\xb9" in target.read_bytes(), "rupee sign encoded as UTF-8"


def test_a_script_survives_a_character_the_locale_cannot_encode(tmp_path: Path) -> None:
    """Without this, the run does not mangle the output -- it dies.

    On Windows a character outside cp1252 raises UnicodeEncodeError from print
    itself, so a single unusual product name would take down a report that had
    already done all of its work.
    """
    target = tmp_path / "out.txt"
    code = _run(
        "import sys; sys.path.insert(0, 'src')\n"
        "from rootsignal.console import use_utf8_output\n"
        "use_utf8_output()\n"
        "print('\\u20b9' * 100)\n",
        target,
    )
    assert code == 0


def test_calling_it_twice_is_harmless(tmp_path: Path) -> None:
    """Scripts may be imported and run in the same process."""
    target = tmp_path / "out.txt"
    code = _run(
        "import sys; sys.path.insert(0, 'src')\n"
        "from rootsignal.console import use_utf8_output\n"
        "use_utf8_output(); use_utf8_output()\n"
        f"print({SAMPLE!r})\n",
        target,
    )
    assert code == 0
    assert target.read_bytes().decode("utf-8").strip() == SAMPLE


def test_every_command_line_entry_point_sets_its_encoding() -> None:
    """The structural guard, so a new script does not quietly inherit cp1252.

    generate_sample_data.py is exempt: it deliberately imports nothing from the
    package -- it writes the data the package reads -- and its output is ASCII.
    """
    exempt = {"generate_sample_data.py"}
    missing = [
        path.name
        for path in sorted((ROOT / "scripts").glob("*.py"))
        if path.name not in exempt
        and "use_utf8_output" not in path.read_text(encoding="utf-8")
    ]
    assert not missing, f"{missing} do not call use_utf8_output()"
