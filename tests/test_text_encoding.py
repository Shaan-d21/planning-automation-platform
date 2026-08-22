"""Guard user-facing source files against common UTF-8 mojibake."""

from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TEXT_ROOTS = (
    PROJECT_ROOT / "frontend" / "src",
    PROJECT_ROOT / "app" / "api",
    PROJECT_ROOT / "app" / "application",
)
TEXT_SUFFIXES = {".css", ".py", ".ts", ".tsx"}
MOJIBAKE_MARKERS = ("â€", "Â", "Ã", "�")


def test_user_facing_sources_are_valid_utf8_without_mojibake() -> None:
    failures: list[str] = []
    for root in TEXT_ROOTS:
        for path in root.rglob("*"):
            if path.suffix not in TEXT_SUFFIXES or not path.is_file():
                continue
            text = path.read_text(encoding="utf-8")
            markers = [marker for marker in MOJIBAKE_MARKERS if marker in text]
            if markers:
                failures.append(
                    f"{path.relative_to(PROJECT_ROOT)}: {', '.join(markers)}"
                )
    assert not failures, "Mojibake detected:\n" + "\n".join(failures)
