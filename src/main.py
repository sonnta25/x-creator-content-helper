from __future__ import annotations

from src.bot import ContentBot
from src.config import Settings


def main() -> None:
    settings = Settings.from_env()
    app = ContentBot(settings).build_application()
    app.run_polling(allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    main()
