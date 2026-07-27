import ast
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_source_syntax_is_compatible_with_python_311() -> None:
    for path in (PROJECT_ROOT / "src").glob("*.py"):
        ast.parse(
            path.read_text(encoding="utf-8-sig"),
            filename=str(path),
            feature_version=(3, 11),
        )
