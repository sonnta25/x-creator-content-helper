from pathlib import Path

from src.env_store import update_env_value


def test_update_env_value_replaces_existing_value(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text(
        "TELEGRAM_BOT_TOKEN=123:ABC\nX_COOKIE=old\nX_SEARCH_LIMIT=8\n",
        encoding="utf-8",
    )

    update_env_value("X_COOKIE", "auth_token=abc; ct0=def", str(env_path))

    assert env_path.read_text(encoding="utf-8") == (
        "TELEGRAM_BOT_TOKEN=123:ABC\n"
        "X_COOKIE=auth_token=abc; ct0=def\n"
        "X_SEARCH_LIMIT=8\n"
    )


def test_update_env_value_appends_missing_value(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text("TELEGRAM_BOT_TOKEN=123:ABC\n", encoding="utf-8")

    update_env_value("X_COOKIE", "auth_token=abc; ct0=def", str(env_path))

    assert env_path.read_text(encoding="utf-8") == (
        "TELEGRAM_BOT_TOKEN=123:ABC\n\n"
        "X_COOKIE=auth_token=abc; ct0=def\n"
    )
