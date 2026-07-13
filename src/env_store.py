from __future__ import annotations

from pathlib import Path


def update_env_value(name: str, value: str, env_path: str = ".env") -> None:
    path = Path(env_path)
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    prefix = f"{name}="
    new_line = f"{name}={value}"

    for index, line in enumerate(lines):
        if line.startswith(prefix):
            lines[index] = new_line
            break
    else:
        if lines and lines[-1].strip():
            lines.append("")
        lines.append(new_line)

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
