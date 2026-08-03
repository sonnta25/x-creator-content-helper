from __future__ import annotations

import asyncio
import logging
import math
import re
import time
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from io import BytesIO
from typing import Any
from urllib.parse import urlencode
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from telegram import (
    BotCommand,
    CopyTextButton,
    ForceReply,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    Update,
)
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from src.ai_service import create_ai_service
from src.automation import AutomationApproval, AutomationApprovalStore
from src.config import Settings
from src.creator_ops import ReplyWatchStore
from src.env_store import update_env_value
from src.media_download_service import (
    DownloadedMedia,
    MediaDownloadError,
    MediaDownloadService,
)
from src.models import (
    GeneratedContent,
    ImageAttachment,
    ReplyTargetDraft,
    TrendPostVariant,
    XSearchResult,
)
from src.reply_target_metrics import ReplyTargetMetricStore
from src.reply_learning import (
    CHECKPOINT_MINUTES,
    MIN_FEEDBACK_SAMPLES_TO_TUNE,
    MIN_FINAL_SAMPLES_TO_TUNE,
    STRATEGIES,
    ReplyLearningStore,
    match_posted_content,
)
from src.trend_source_service import TrendSourceService, summarize_trend_signals
from src.video_frame_service import VideoFrameExtractor
from src.x_search_service import (
    MIN_REPLY_TARGET_ENGAGEMENT_SCORE,
    MIN_REPLY_TARGET_VELOCITY_SCORE,
    MIN_REPLY_TARGET_VIEW_VELOCITY_SCORE,
    MAX_REPLY_TARGET_LANGUAGES,
    SUPPORTED_REPLY_TARGET_LANGUAGES,
    TREND_FALLBACK_QUERIES,
    XSearchService,
    default_english_query,
    extract_tweet_id,
    format_x_results,
    parse_reply_target_languages,
    query_for_language,
    rank_fast_growing_posts,
    rank_viral_video_posts,
    summarize_reply_target_context,
    summarize_reply_video_context,
    summarize_x_context,
)


LOGGER = logging.getLogger(__name__)

AUTO_TREND_CATEGORIES = ("trending", "news", "entertainment", "sport")
AUTO_REPLY_TARGET_FALLBACK_QUERIES = (
    '(breaking news OR politics OR business OR sports OR entertainment OR '
    'technology OR "internet culture" OR crypto OR AI)',
    "breaking news",
    "AI",
)
AUTO_REPLY_TARGET_FALLBACK_QUERIES_BY_LANGUAGE = {
    "en": AUTO_REPLY_TARGET_FALLBACK_QUERIES,
    "ja": (
        "(話題 OR 速報 OR ニュース OR 政治 OR ビジネス OR スポーツ OR "
        "エンタメ OR テクノロジー OR AI OR 暗号資産)",
        "速報",
        "AI",
    ),
}
JAPANESE_HIGH_VALUE_REPLY_QUERY = (
    "(経済 OR 日銀 OR 円相場 OR 株価 OR 速報 OR 政治 OR スポーツ OR 野球 OR "
    "サッカー OR アニメ OR ゲーム OR テクノロジー OR AI)"
)
REPLY_TARGET_MAX_CANDIDATES = 6
REPLY_TARGET_RESULT_LIMIT = 8
REPLY_TARGET_CONTEXT_ITEMS = 3
MIN_REPLY_TARGET_BATCH_ITEMS = 2
MIN_REPLY_TARGET_VOLUME_FALLBACK_VIEWS = 100
REPLY_VIDEO_RESULT_LIMIT = 16
REPLY_VIDEO_CONTEXT_ITEMS = 3
REPLY_VIDEO_MIN_BATCH_ITEMS = 2
REPLY_VIDEO_GLOBAL_LANGUAGES = ("en", "ja", "ko", "es", "pt", "zh-cn")
BOT_RUNTIME_REVISION = "translated-reply-card-v5"
REPLY_TARGET_TREND_TIMEOUT_SECONDS = 20
REPLY_TARGET_SEARCH_TIMEOUT_SECONDS = 30
REPLY_TARGET_REFRESH_TIMEOUT_SECONDS = 12
REPLY_TARGET_REFRESH_LIMIT = 6
TREND_CONTEXT_SIGNAL_ITEMS = 3
TREND_CONTEXT_X_ITEMS = 4
COMMAND_INPUT_TIMEOUT_SECONDS = 5 * 60
COMMAND_INPUT_PROMPTS = {
    "download": (
        "Send a public post, image, carousel, video, or Reel URL.",
        "Paste a post or media URL",
    ),
    "replytargets": (
        "Send a topic to search, or send `auto` to let the bot choose.",
        "Topic or auto",
    ),
    "replyvideo": (
        "Send an optional topic, or send `auto` to hunt fresh viral videos globally.",
        "Topic or auto",
    ),
    "persona": (
        "Send `show`, or update values with:\n"
        "`niche=...; voice=...; audience=...`",
        "show or persona values",
    ),
    "importcookie": (
        "Send the X cookie in this private reply:\n"
        "`auth_token=...; ct0=...`\n"
        "You may prefix it with an account name.",
        "Paste auth_token and ct0",
    ),
    "xremove": (
        "Send the X account name to remove.",
        "Enter account name",
    ),
    "reply": (
        "Send the tweet text or X post link you want to reply to.",
        "Paste tweet text or link",
    ),
    "replyevery": (
        "Send an interval from 5 to 1440 minutes, or send `show`.",
        "Minutes or show",
    ),
    "videoevery": (
        "Send an interval from 3 to 1440 minutes, or send `show`.",
        "Minutes or show",
    ),
    "replybatch": (
        "Send `show`, `targets 2-5`, or `video 2-5`.",
        "show, targets 3, or video 2",
    ),
}

MENU_MAIN = "🏠 Main menu"
MENU_REPLY = "🎯 Viral replies"
MENU_AUTOMATION = "🤖 Automation"
MENU_INSIGHTS = "📊 Tracking & insights"
MENU_X_ACCOUNTS = "🔐 X accounts"
MENU_VIDEO = "🎬 Video tools"
MENU_CREATOR = "⚙️ Creator settings"
MENU_HELP = "❓ Help"
MENU_CANCEL = "✖️ Cancel"

MENU_REPLY_TARGETS = "🎯 Find viral posts"
MENU_REPLY_VIDEO = "🎬 Find viral videos"
MENU_WRITE_REPLY = "💬 Write a standout reply"
MENU_REPLY_SCHEDULE = "⏱️ Reply-target schedule"
MENU_VIDEO_SCHEDULE = "🎬 Reply-video schedule"
MENU_REPLY_BATCH = "🔢 Replies per run"
MENU_REPLY_LANGS = "🌍 Reply languages"
MENU_REPLY_LEARN = "🧠 Performance learning"
MENU_REPLY_REPORT = "📈 Reply report"
MENU_SETUP_CHECK = "🩺 System check"
MENU_IMPORT_COOKIE = "🍪 Import X cookie"
MENU_X_LIST = "👥 Account list"
MENU_X_REMOVE = "🗑️ Remove account"
MENU_DOWNLOAD = "📥 Download video"
MENU_PERSONA = "🎭 Creator persona"

MENU_LAYOUTS: dict[str, tuple[tuple[str, ...], ...]] = {
    "main": (
        (MENU_REPLY, MENU_AUTOMATION),
        (MENU_INSIGHTS, MENU_X_ACCOUNTS),
        (MENU_VIDEO, MENU_CREATOR),
        (MENU_HELP, MENU_CANCEL),
    ),
    "reply": (
        (MENU_REPLY_TARGETS, MENU_REPLY_VIDEO),
        (MENU_WRITE_REPLY,),
        (MENU_MAIN,),
    ),
    "automation": (
        (MENU_REPLY_SCHEDULE, MENU_VIDEO_SCHEDULE),
        (MENU_REPLY_BATCH,),
        (MENU_MAIN,),
    ),
    "insights": (
        (MENU_REPLY_LANGS, MENU_REPLY_LEARN),
        (MENU_REPLY_REPORT, MENU_SETUP_CHECK),
        (MENU_MAIN,),
    ),
    "x_accounts": (
        (MENU_IMPORT_COOKIE, MENU_X_LIST),
        (MENU_X_REMOVE, MENU_MAIN),
    ),
    "video": ((MENU_DOWNLOAD, MENU_MAIN),),
    "creator": ((MENU_PERSONA, MENU_MAIN),),
}

MENU_ACTIONS: dict[str, tuple[str, str]] = {
    MENU_MAIN: ("menu", "main"),
    MENU_REPLY: ("menu", "reply"),
    MENU_AUTOMATION: ("menu", "automation"),
    MENU_INSIGHTS: ("menu", "insights"),
    MENU_X_ACCOUNTS: ("menu", "x_accounts"),
    MENU_VIDEO: ("menu", "video"),
    MENU_CREATOR: ("menu", "creator"),
    MENU_HELP: ("help", ""),
    MENU_CANCEL: ("command", "cancel"),
    MENU_REPLY_TARGETS: ("command", "replytargets"),
    MENU_REPLY_VIDEO: ("command", "replyvideo"),
    MENU_WRITE_REPLY: ("command", "reply"),
    MENU_REPLY_SCHEDULE: ("command", "replyevery"),
    MENU_VIDEO_SCHEDULE: ("command", "videoevery"),
    MENU_REPLY_BATCH: ("command", "replybatch"),
    MENU_REPLY_LANGS: ("command", "replylangs"),
    MENU_REPLY_LEARN: ("command", "replylearn"),
    MENU_REPLY_REPORT: ("command", "replyreport"),
    MENU_SETUP_CHECK: ("command", "setupcheck"),
    MENU_IMPORT_COOKIE: ("command", "importcookie"),
    MENU_X_LIST: ("command", "xaccounts"),
    MENU_X_REMOVE: ("command", "xremove"),
    MENU_DOWNLOAD: ("command", "download"),
    MENU_PERSONA: ("command", "persona"),
}
MENU_BUTTON_PATTERN = re.compile(
    "^(?:" + "|".join(re.escape(label) for label in MENU_ACTIONS) + ")$"
)
TWEETTREND_LANGUAGE_ALIASES = {
    "en": "English",
    "eng": "English",
    "english": "English",
    "vi": "Vietnamese",
    "vn": "Vietnamese",
    "vietnamese": "Vietnamese",
    "tiengviet": "Vietnamese",
    "tieng-viet": "Vietnamese",
    "tieng_viet": "Vietnamese",
}


class _SilentStatus:
    async def edit_text(self, _text: str) -> None:
        return None


@dataclass(frozen=True)
class _PendingCommandInput:
    command: str
    expires_at: float
    prompt_message_id: int | None = None


BOT_COMMANDS = [
    BotCommand("start", "Open the grouped bot menu"),
    BotCommand("menu", "Open the grouped bot menu"),
    BotCommand("help", "Show help and the grouped menu"),
    BotCommand("download", "Download images, videos, carousels, or Reels"),
    BotCommand("replytargets", "Auto-pick or search X posts to reply to"),
    BotCommand("replyvideo", "Find fresh viral videos with low reply competition"),
    BotCommand("persona", "Show or set creator niche, voice, and audience"),
    BotCommand("importcookie", "Save X auth_token and ct0 cookie for X search"),
    BotCommand("xaccounts", "Show imported X cookie accounts"),
    BotCommand("xremove", "Remove an imported X cookie account"),
    BotCommand("reply", "Generate a witty reply from tweet text or an X post link"),
    BotCommand("replyevery", "Set scheduled replytargets interval in minutes"),
    BotCommand("videoevery", "Set scheduled replyvideo interval in minutes"),
    BotCommand("replybatch", "Set replytargets or replyvideo cards per run"),
    BotCommand("replylangs", "Show, add, or remove reply-target languages"),
    BotCommand("replylearn", "Show or control automatic reply learning"),
    BotCommand("replyreport", "Show tracked post and reply performance"),
    BotCommand("setupcheck", "Check X, tracking, scheduling, and learning health"),
    BotCommand("cancel", "Cancel the command currently waiting for input"),
]


class ContentBot:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.ai = create_ai_service(settings)
        self.x_search = XSearchService(settings)
        self.trend_sources = TrendSourceService(settings, self.x_search)
        self.media_downloader = MediaDownloadService(settings)
        self.video_frame_extractor = VideoFrameExtractor()
        self._download_semaphore = asyncio.Semaphore(1)
        self._x_account_error_notices: dict[str, str] = {}
        self.approvals = AutomationApprovalStore(settings.automation_approvals_path)
        self.reply_target_metrics = ReplyTargetMetricStore(
            settings.reply_target_metrics_path
        )
        self.reply_learning = ReplyLearningStore(
            settings.reply_learning_path,
            enabled=settings.reply_learning_enabled,
        )
        self.reply_watch = ReplyWatchStore(settings.reply_watch_path)
        self.approval_chat_id = settings.telegram_approval_chat_id
        self._application: Application | None = None
        self._automation_running: set[str] = set()
        self._automation_tasks: set[asyncio.Task[None]] = set()
        self._pending_inputs: dict[tuple[int, int], _PendingCommandInput] = {}
        self._reply_tracking_task: asyncio.Task[None] | None = None

    def build_application(self) -> Application:
        async def post_init(app: Application) -> None:
            self._application = app
            await _set_bot_commands(app)
            bridge = getattr(self.ai, "bridge", None)
            if bridge is not None:
                bridge.set_automation_handler(self)
                await bridge.start()
            self._reply_tracking_task = asyncio.create_task(
                self._reply_tracking_loop(),
                name="reply-tracking",
            )
            self._automation_tasks.add(self._reply_tracking_task)
            self._reply_tracking_task.add_done_callback(self._automation_tasks.discard)

        async def post_shutdown(app: Application) -> None:
            del app
            for task in tuple(self._automation_tasks):
                task.cancel()
            if self._automation_tasks:
                await asyncio.gather(*self._automation_tasks, return_exceptions=True)
            bridge = getattr(self.ai, "bridge", None)
            if bridge is not None:
                await bridge.stop()
            await self.trend_sources.aclose()
            self._application = None

        app = (
            Application.builder()
            .token(self.settings.telegram_bot_token)
            .post_init(post_init)
            .post_shutdown(post_shutdown)
            .build()
        )
        app.add_handler(
            MessageHandler(filters.COMMAND, self._command_started),
            group=-1,
        )
        app.add_handler(CommandHandler("start", self.start))
        app.add_handler(CommandHandler("menu", self.start))
        app.add_handler(CommandHandler("help", self.start))
        app.add_handler(CommandHandler("download", self.download))
        app.add_handler(CommandHandler("replytargets", self.replytargets))
        app.add_handler(CommandHandler("replyvideo", self.replyvideo))
        app.add_handler(CommandHandler("persona", self.persona))
        app.add_handler(CommandHandler("importcookie", self.importcookie))
        app.add_handler(CommandHandler("xaccounts", self.xaccounts))
        app.add_handler(CommandHandler("xremove", self.xremove))
        app.add_handler(CommandHandler("reply", self.reply))
        app.add_handler(CommandHandler("replyevery", self.replyevery))
        app.add_handler(CommandHandler("videoevery", self.videoevery))
        app.add_handler(CommandHandler("replybatch", self.replybatch))
        app.add_handler(CommandHandler("replylangs", self.replylangs))
        app.add_handler(CommandHandler("replylearn", self.replylearn))
        app.add_handler(CommandHandler("replyreport", self.replyreport))
        app.add_handler(CommandHandler("setupcheck", self.setupcheck))
        app.add_handler(CommandHandler("cancel", self.cancel))
        app.add_handler(
            CallbackQueryHandler(self.automation_approval, pattern=r"^automation:")
        )
        app.add_handler(
            MessageHandler(filters.Regex(MENU_BUTTON_PATTERN), self.menu_action)
        )
        app.add_handler(
            MessageHandler(
                filters.TEXT & ~filters.COMMAND,
                self.pending_command_input,
            )
        )
        return app

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        del context
        self._clear_pending_input(update)
        await update.effective_message.reply_text(
            "✨ X Creator Assistant\n\n"
            "Features are organized into groups. Choose an option below.\n\n"
            "• Find viral posts or videos and write standout replies\n"
            "• Automate /replytargets and /replyvideo independently\n"
            "• Track performance, learn from results, and manage X accounts",
            reply_markup=_menu_keyboard("main"),
        )

    async def menu_action(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        message = update.effective_message
        if message is None:
            return
        action = MENU_ACTIONS.get(str(message.text or "").strip())
        if action is None:
            return
        action_type, value = action
        self._clear_pending_input(update)
        if action_type == "menu":
            titles = {
                "main": "🏠 Main menu",
                "reply": "🎯 Viral replies",
                "automation": "🤖 Automation",
                "insights": "📊 Tracking & insights",
                "x_accounts": "🔐 X accounts",
                "video": "🎬 Video tools",
                "creator": "⚙️ Creator settings",
            }
            await message.reply_text(
                titles[value] + "\nChoose a feature:",
                reply_markup=_menu_keyboard(value),
            )
            return
        if action_type == "help":
            await self.start(update, context)
            return
        handler = getattr(self, value, None)
        if handler is None:
            await message.reply_text("This feature is currently unavailable.")
            return
        context.args = []
        await handler(update, context)

    async def _command_started(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        del context
        message = update.effective_message
        if message is None:
            return
        first_token = str(message.text or "").split(maxsplit=1)[0].lower()
        command = first_token.split("@", 1)[0]
        if command != "/cancel":
            self._clear_pending_input(update)

    async def _request_command_input(self, update: Update, command: str) -> None:
        message = update.effective_message
        key = _pending_input_key(update)
        prompt = COMMAND_INPUT_PROMPTS.get(command)
        if message is None or key is None or prompt is None:
            return
        text, placeholder = prompt
        sent = await message.reply_text(
            f"{text}\n\nSend /cancel to stop.",
            reply_markup=ForceReply(
                selective=True,
                input_field_placeholder=placeholder,
            ),
        )
        self._pending_inputs[key] = _PendingCommandInput(
            command=command,
            expires_at=time.monotonic() + COMMAND_INPUT_TIMEOUT_SECONDS,
            prompt_message_id=getattr(sent, "message_id", None),
        )

    async def pending_command_input(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        message = update.effective_message
        key = _pending_input_key(update)
        if message is None or key is None:
            return
        pending = self._pending_inputs.pop(key, None)
        if pending is None:
            return
        if time.monotonic() > pending.expires_at:
            await message.reply_text(
                "That command input request expired. Select the command again."
            )
            return

        payload = str(message.text or "").strip()
        context.args = payload.split()
        handler = getattr(self, pending.command, None)
        if handler is None:
            await message.reply_text("That pending command is no longer available.")
            return
        await handler(update, context)

    async def cancel(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        del context
        cancelled = self._clear_pending_input(update)
        message = update.effective_message
        if message is not None:
            await message.reply_text(
                "Cancelled the pending command."
                if cancelled
                else "No command is currently waiting for input."
            )

    def _clear_pending_input(self, update: Update) -> bool:
        key = _pending_input_key(update)
        if key is None:
            return False
        return self._pending_inputs.pop(key, None) is not None

    async def download(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        message = update.effective_message
        source_url = _extract_media_url(_command_payload(message, context))
        if not source_url:
            await self._request_command_input(update, "download")
            return

        status = await message.reply_text("Downloading media from the post...")
        media: DownloadedMedia | None = None
        try:
            async with self._download_semaphore:
                media = await asyncio.to_thread(self.media_downloader.download, source_url)
            await status.edit_text(
                f"Downloaded {len(media.paths)} {media.media_kind} "
                f"({_format_file_size(media.size_bytes)} total). Sending to Telegram..."
            )
            await message.chat.send_action(ChatAction.UPLOAD_DOCUMENT)
            for index, path in enumerate(media.paths, start=1):
                item_label = (
                    f"Prepared {media.media_kind}"
                    if len(media.paths) == 1
                    else f"Prepared media file {index}/{len(media.paths)}"
                )
                caption = _truncate_text(
                    f"{item_label}\n\n"
                    f"Source reference: {media.source_url}\n"
                    "Only republish content you own or have permission to use.",
                    self.settings.telegram_caption_limit,
                )
                with path.open("rb") as document:
                    await message.reply_document(
                        document=document,
                        filename=path.name,
                        caption=caption,
                        read_timeout=60,
                        write_timeout=300,
                        connect_timeout=30,
                        pool_timeout=30,
                    )
            await status.delete()
        except MediaDownloadError as exc:
            await status.edit_text(f"Download failed: {exc}")
        except Exception as exc:
            if media is None:
                await status.edit_text(_friendly_error(exc))
            else:
                await status.edit_text(
                    "The video was downloaded, but Telegram could not send the file. "
                    f"Details: {_exception_detail(exc)}"
                )
        finally:
            if media is not None:
                await asyncio.to_thread(media.cleanup)

    async def automationhere(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        del context
        chat = update.effective_chat
        message = update.effective_message
        if chat is None or message is None:
            return
        if chat.type != "private":
            await message.reply_text(
                "For approval security, use /automationhere in a private chat with this bot."
            )
            return
        self.approval_chat_id = int(chat.id)
        self.settings = replace(
            self.settings,
            telegram_approval_chat_id=self.approval_chat_id,
        )
        update_env_value("TELEGRAM_APPROVAL_CHAT_ID", str(self.approval_chat_id))
        await message.reply_text(
            "Automation approvals will be sent to this chat.\n"
            "Use /replyevery for /replytargets and /videoevery for /replyvideo."
        )

    async def replyevery(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        chat = update.effective_chat
        message = update.effective_message
        if chat is None or message is None:
            return
        if chat.type != "private":
            await message.reply_text("Use /replyevery in a private chat with this bot.")
            return
        if self.approval_chat_id is not None and int(chat.id) != self.approval_chat_id:
            await message.reply_text(
                "Only TELEGRAM_APPROVAL_CHAT_ID can change this schedule."
            )
            return
        if not context.args:
            await self._request_command_input(update, "replyevery")
            return
        if str(context.args[0]).strip().lower() in {"show", "current"}:
            current = self.settings.telegram_reply_targets_minutes
            value = f"{current} minutes" if current is not None else "Chrome extension setting"
            await message.reply_text(f"Current /replytargets interval: {value}.")
            return
        try:
            minutes = int(context.args[0])
        except (TypeError, ValueError):
            await message.reply_text("Use a whole number, for example: /replyevery 30")
            return
        if minutes < 5 or minutes > 1440:
            await message.reply_text("Interval must be between 5 and 1440 minutes.")
            return
        self.settings = replace(
            self.settings,
            telegram_reply_targets_minutes=minutes,
            telegram_reply_targets_updated_at=int(datetime.now(UTC).timestamp() * 1000),
        )
        update_env_value("TELEGRAM_REPLY_TARGETS_MINUTES", str(minutes))
        update_env_value(
            "TELEGRAM_REPLY_TARGETS_UPDATED_AT",
            str(self.settings.telegram_reply_targets_updated_at),
        )
        await message.reply_text(
            f"Scheduled /replytargets interval set to {minutes} minutes. "
            "Chrome will sync it within about 30 seconds."
        )

    async def replylangs(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        chat = update.effective_chat
        message = update.effective_message
        if chat is None or message is None:
            return
        if chat.type != "private":
            await message.reply_text("Use /replylangs in a private chat with this bot.")
            return
        if self.approval_chat_id is not None and int(chat.id) != self.approval_chat_id:
            await message.reply_text(
                "Only TELEGRAM_APPROVAL_CHAT_ID can change reply languages."
            )
            return
        raw = " ".join(context.args).strip()
        if not raw or raw.lower() in {"show", "current", "status"}:
            languages = parse_reply_target_languages(
                self.settings.reply_target_languages
            )
            await message.reply_text(
                "Reply-target languages: "
                f"{', '.join(languages)}\n\n"
                "Examples:\n"
                "/replylangs add ko es\n"
                "/replylangs remove ja\n"
                "/replylangs set en ja ko id"
            )
            return

        action, separator, values = raw.partition(" ")
        action = action.strip().lower()
        if action not in {"add", "remove", "set"} or not separator or not values.strip():
            await message.reply_text(
                "Usage: /replylangs show|add <codes>|remove <codes>|set <codes>"
            )
            return
        try:
            languages = _updated_reply_target_languages(
                self.settings.reply_target_languages,
                action,
                values,
            )
        except RuntimeError as exc:
            await message.reply_text(str(exc))
            return

        serialized = ",".join(languages)
        update_env_value("REPLY_TARGET_LANGUAGES", serialized)
        self.settings = replace(
            self.settings,
            reply_target_languages=serialized,
        )
        await message.reply_text(
            f"Reply-target languages updated: {serialized}\n"
            "Saved to .env and will sync to scheduled Chrome scans within about 30 seconds."
        )

    async def replybatch(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        chat = update.effective_chat
        message = update.effective_message
        if chat is None or message is None:
            return
        if chat.type != "private":
            await message.reply_text("Use /replybatch in a private chat with this bot.")
            return
        if self.approval_chat_id is not None and int(chat.id) != self.approval_chat_id:
            await message.reply_text(
                "Only TELEGRAM_APPROVAL_CHAT_ID can change reply batch sizes."
            )
            return
        raw = " ".join(context.args).strip().lower()
        if not raw:
            await self._request_command_input(update, "replybatch")
            return
        if raw in {"show", "current", "status"}:
            await message.reply_text(
                "Reply cards per run\n"
                f"- /replytargets: {self.settings.reply_target_batch_size}\n"
                f"- /replyvideo: {self.settings.reply_video_batch_size}\n\n"
                "Change with /replybatch targets 2 or /replybatch video 2."
            )
            return

        scope, separator, raw_size = raw.partition(" ")
        aliases = {
            "target": "targets",
            "targets": "targets",
            "replytarget": "targets",
            "replytargets": "targets",
            "video": "video",
            "replyvideo": "video",
        }
        selected_scope = aliases.get(scope)
        if selected_scope is None or not separator or not raw_size.strip():
            await message.reply_text(
                "Usage: /replybatch show|targets <2-5>|video <2-5>"
            )
            return
        try:
            batch_size = int(raw_size.strip())
        except (TypeError, ValueError):
            await message.reply_text("Batch size must be a whole number from 2 to 5.")
            return
        if batch_size < 2 or batch_size > 5:
            await message.reply_text("Batch size must be between 2 and 5 replies.")
            return

        if selected_scope == "targets":
            update_env_value("REPLY_TARGET_BATCH_SIZE", str(batch_size))
            self.settings = replace(
                self.settings,
                reply_target_batch_size=batch_size,
            )
            label = "/replytargets"
        else:
            update_env_value("REPLY_VIDEO_BATCH_SIZE", str(batch_size))
            self.settings = replace(
                self.settings,
                reply_video_batch_size=batch_size,
            )
            label = "/replyvideo"
        await message.reply_text(
            f"{label} will request {batch_size} reply card(s) per run. "
            "Saved to .env and applied immediately to manual and scheduled runs."
        )

    async def videoevery(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        chat = update.effective_chat
        message = update.effective_message
        if chat is None or message is None:
            return
        if chat.type != "private":
            await message.reply_text("Use /videoevery in a private chat with this bot.")
            return
        if self.approval_chat_id is not None and int(chat.id) != self.approval_chat_id:
            await message.reply_text(
                "Only TELEGRAM_APPROVAL_CHAT_ID can change this schedule."
            )
            return
        if not context.args:
            await self._request_command_input(update, "videoevery")
            return
        if str(context.args[0]).strip().lower() in {"show", "current"}:
            current = self.settings.telegram_reply_video_minutes
            value = f"{current} minutes" if current is not None else "Chrome extension setting"
            await message.reply_text(f"Current /replyvideo interval: {value}.")
            return
        try:
            minutes = int(context.args[0])
        except (TypeError, ValueError):
            await message.reply_text("Use a whole number, for example: /videoevery 5")
            return
        if minutes < 3 or minutes > 1440:
            await message.reply_text("Interval must be between 3 and 1440 minutes.")
            return
        self.settings = replace(
            self.settings,
            telegram_reply_video_minutes=minutes,
            telegram_reply_video_updated_at=int(datetime.now(UTC).timestamp() * 1000),
        )
        update_env_value("TELEGRAM_REPLY_VIDEO_MINUTES", str(minutes))
        update_env_value(
            "TELEGRAM_REPLY_VIDEO_UPDATED_AT",
            str(self.settings.telegram_reply_video_updated_at),
        )
        await message.reply_text(
            f"Scheduled /replyvideo interval set to {minutes} minutes. "
            "Chrome will sync it within about 30 seconds."
        )

    async def get_automation_config(self) -> dict[str, Any]:
        return {
            "reply_targets_minutes": self.settings.telegram_reply_targets_minutes,
            "reply_targets_updated_at": self.settings.telegram_reply_targets_updated_at,
            "reply_video_minutes": self.settings.telegram_reply_video_minutes,
            "reply_video_updated_at": self.settings.telegram_reply_video_updated_at,
            "automation_running": bool(self._automation_running),
            "creator_timezone": self.settings.creator_timezone,
            "reply_target_languages": self.settings.reply_target_languages,
            "extension_bridge_timeout_seconds": (
                self.settings.extension_bridge_timeout_seconds
            ),
        }

    async def replylearn(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        message = update.effective_message
        raw = " ".join(context.args).strip()
        action = raw.lower() or "status"
        if action == "on":
            self.reply_learning.set_enabled(True)
            update_env_value("REPLY_LEARNING_ENABLED", "true")
            await message.reply_text(
                "Automatic reply tracking and bounded learning are ON."
            )
            return
        if action == "off":
            self.reply_learning.set_enabled(False)
            update_env_value("REPLY_LEARNING_ENABLED", "false")
            await message.reply_text(
                "Automatic reply tracking and learning are OFF. Existing history was kept."
            )
            return
        if action == "rollback":
            rolled_back = self.reply_learning.rollback()
            await message.reply_text(
                "Rolled back to the previous strategy-weight version."
                if rolled_back
                else "No earlier strategy-weight version is available."
            )
            return
        if action.startswith("username "):
            username = raw.split(maxsplit=1)[1].strip().lstrip("@")
            if not re.fullmatch(r"[A-Za-z0-9_]{1,15}", username):
                await message.reply_text("Invalid X username.")
                return
            update_env_value("X_OWNER_USERNAME", username)
            self.settings = replace(self.settings, x_owner_username=username)
            await message.reply_text(
                f"Tracking account set to @{username}. The bot will discover posted replies automatically."
            )
            return
        if action != "status":
            await message.reply_text(
                "Usage: /replylearn status|on|off|rollback|username @name"
            )
            return
        status = self.reply_learning.status()
        owner = (
            f"@{self.settings.x_owner_username}"
            if self.settings.x_owner_username
            else "not configured"
        )
        weights = "\n".join(
            f"- {name}: {status.weights[name] * 100:.1f}%"
            for name in STRATEGIES
        )
        last_tuned = status.last_tuned_at or "not yet"
        await message.reply_text(
            "Automatic reply learning\n"
            f"State: {'ON' if status.enabled else 'OFF'}\n"
            f"Tracking account: {owner}\n"
            f"Waiting/discovered/measured/unmatched: "
            f"{status.waiting}/{status.tracking}/{status.measured}/{status.unmatched}\n"
            f"Weight version: {status.version}\n"
            f"Last tuned: {last_tuned}\n"
            f"Auto-tuning starts after {MIN_FEEDBACK_SAMPLES_TO_TUNE} approval feedback "
            f"events or {MIN_FINAL_SAMPLES_TO_TUNE} measured 24h outcomes.\n\n"
            f"Strategy weights:\n{weights}"
        )

    async def replyreport(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        message = update.effective_message
        raw = " ".join(context.args).strip().lower() or "30d"
        if raw not in {"7d", "30d"}:
            await message.reply_text("Usage: /replyreport [7d|30d]")
            return
        days = int(raw[:-1])
        report = self.reply_learning.report(days)
        strategy_lines = "\n".join(
            f"- {name}: {values['count']} measured, avg {values['average_score']:.1f}/100"
            for name, values in report["by_strategy"].items()
        )
        await message.reply_text(
            f"Reply performance - last {days} days\n"
            f"Posted/tracked: {report['posted']}\n"
            f"Replies/posts: {report['replies']}/{report['posts']}\n"
            f"Completed 24h measurement: {report['measured']}\n"
            f"Author replies detected: {report['author_replies']}\n"
            f"Follower lift during tracked post windows: "
            f"{report['follower_window_lift']} (account-level proxy)\n"
            f"Average outcome score: {report['average_score']:.1f}/100\n\n"
            f"By strategy:\n{strategy_lines}"
        )

    async def setupcheck(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        del context
        message = update.effective_message
        learning = self.reply_learning.status()
        accounts: list[dict] = []
        account_error = ""
        try:
            accounts = await self.x_search.accounts_info()
        except Exception as exc:
            account_error = _friendly_error(exc)
        healthy_accounts = [
            row
            for row in accounts
            if bool(row.get("active")) and not _account_error_text(row)
        ]
        approvals = self.approvals.items()
        stale_mobile = sum(
            approval.status == "mobile_approved"
            and datetime.now(UTC) - approval.created_at > timedelta(hours=2)
            for approval in approvals
        )
        blockers = []
        if not self.settings.x_owner_username:
            blockers.append("Set the posting account with /replylearn username @name.")
        if not accounts:
            blockers.append("Import an authorized X session with /importcookie.")
        elif not healthy_accounts:
            blockers.append("X account pool has no error-free active account; run /xaccounts.")
        if self.approval_chat_id is None:
            blockers.append("Set TELEGRAM_APPROVAL_CHAT_ID in .env.")
        state = "READY" if not blockers else "NEEDS ATTENTION"
        detail = "\n".join(f"- {item}" for item in blockers) or "- No blocking setup issue found."
        await message.reply_text(
            f"Creator bot health: {state}\n\n"
            f"Runtime revision: {BOT_RUNTIME_REVISION}\n"
            f"X account records/healthy: {len(accounts)}/{len(healthy_accounts)}\n"
            f"Tracking username: "
            f"{('@' + self.settings.x_owner_username) if self.settings.x_owner_username else 'missing'}\n"
            f"Learning: {'ON' if learning.enabled else 'OFF'}; "
            f"{learning.measured} measured, {learning.waiting} waiting\n"
            f"Reply schedule: {self.settings.telegram_reply_targets_minutes or 'extension default'} minutes\n"
            f"Video-reply schedule: {self.settings.telegram_reply_video_minutes or 'extension default (5)'} minutes\n"
            f"Reply cards per run: targets {self.settings.reply_target_batch_size}; "
            f"video {self.settings.reply_video_batch_size}\n"
            f"Timezone: {self.settings.creator_timezone}\n"
            f"Stale mobile approvals: {stale_mobile}\n"
            f"Mode/languages: {self.settings.reply_target_mode}; "
            f"{self.settings.reply_target_languages}\n\n"
            f"Actions:\n{detail}"
            + (f"\n\nAccount check error: {account_error}" if account_error else "")
        )

    async def today(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        message = update.effective_message
        mode = (
            context.args[0].strip().lower()
            if context.args
            else self.settings.reply_target_mode
        )
        if mode not in {"balanced", "reach", "qualified", "relationship"}:
            await message.reply_text(
                "Usage: /today [balanced|reach|qualified|relationship]"
            )
            return
        status = await message.reply_text(
            f"Building today's creator queue in {mode} mode..."
        )
        try:
            max_age = self.settings.reply_target_max_age_minutes
            languages = parse_reply_target_languages(
                self.settings.reply_target_languages
            )
            search_query, results, selection_note = await self._get_reply_target_context(
                "",
                status,
                max_age_minutes=max_age,
                languages=languages,
                mode=mode,
            )
            ready, watching = self.reply_watch.classify(results)
            watching_total = len(
                self.reply_watch.candidates_for_refresh(
                    limit=1_000,
                    languages=languages,
                    max_age_minutes=max_age,
                )
            )
            remaining_cap = max(
                0,
                self.settings.creator_daily_reply_cap
                - _reply_approvals_created_today(
                    self.approvals.items(),
                    timezone_name=self.settings.creator_timezone,
                ),
            )
            reply_batch, promoted_count = _select_reply_draft_batch(
                ready,
                watching,
                capacity=remaining_cap,
                max_items=2,
            )
            await status.edit_text(
                "Today's queue\n"
                f"Reply batch: {len(reply_batch)} "
                f"({promoted_count} early qualified)\n"
                f"Watching now/total: {len(watching)}/{watching_total}\n"
                f"Daily reply capacity remaining: {remaining_cap}\n"
                f"Selection: {selection_note.strip() or 'standard thresholds'}\n"
                "Preparing one original post and any reply-now drafts..."
            )
            approver_user_id = (
                update.effective_user.id
                if update.effective_user is not None
                else message.chat.id
            )
            sent_replies = await self._create_reply_approvals(
                reply_batch,
                query=search_query,
                chat_id=message.chat.id,
                approver_user_id=approver_user_id,
            )

            contexts = await self._get_trend_contexts_for_tweettrend3(
                "auto",
                status,
                count=1,
            )
            sent_posts = 0
            if contexts:
                topic, x_context, source, selected_category = contexts[0]
                generated = (
                    await self.ai.generate_trend_posts_batch(
                        [(topic, x_context)],
                        output_language=self.settings.content_language,
                    )
                )[0]
                approval = self.approvals.create(
                    kind="post",
                    text=generated.text,
                    chat_id=message.chat.id,
                    approver_user_id=approver_user_id,
                    target_label=topic,
                    metadata={
                        "image_prompt": generated.image_prompt,
                        "source": source,
                        "category": selected_category,
                    },
                )
                await self._send_approval(
                    approval,
                    reason=f"{source} | {selected_category} | {topic}",
                )
                sent_posts = 1
            await status.edit_text(
                "Today's queue is ready.\n"
                f"Reply cards: {sent_replies}\n"
                f"Original post cards: {sent_posts}\n"
                f"Watching for confirmation: {watching_total}\n\n"
                "Use Alternative/Shorter only when needed; images are generated on demand."
            )
        except Exception as exc:
            await status.edit_text(_friendly_error(exc))
        finally:
            await self._notify_x_account_errors(message)

    async def _create_reply_approvals(
        self,
        results: list[XSearchResult],
        *,
        query: str,
        chat_id: int,
        approver_user_id: int,
        video_mode: bool = False,
        visual_attachments: list[ImageAttachment] | None = None,
    ) -> int:
        if not results:
            return 0
        requested_batch_size = (
            self.settings.reply_video_batch_size
            if video_mode
            else self.settings.reply_target_batch_size
        )
        selected = results[:requested_batch_size]
        strategy_by_url = {
            result.url: self.reply_learning.choose_strategy()
            for result in selected
        }
        reply_context = (
            summarize_reply_video_context(
                selected,
                max_items=requested_batch_size,
            )
            if video_mode
            else summarize_reply_target_context(
                selected,
                max_items=requested_batch_size,
            )
        )
        generation_options: dict[str, Any] = {
            "strategy_by_url": strategy_by_url,
        }
        if video_mode:
            generation_options["video_mode"] = True
            generation_options["visual_attachments"] = visual_attachments or []
        drafts = await self.ai.generate_reply_targets(
            query,
            reply_context,
            **generation_options,
        )
        sent = 0
        for draft in drafts:
            target_url = _format_reply_target_link(draft)
            if self.approvals.has_active_target(target_url):
                continue
            result = _result_for_url(selected, target_url)
            strategy = strategy_by_url.get(target_url, draft.strategy)
            approval = self.approvals.create(
                kind="reply",
                text=_format_reply_target_reply(draft),
                chat_id=chat_id,
                approver_user_id=approver_user_id,
                target_url=target_url,
                target_label=draft.target,
                metadata=_reply_tracking_metadata(
                    result,
                    strategy,
                    source_type="replyvideo" if video_mode else "replytargets",
                )
                | {
                    "source_summary_vi": draft.source_summary_vi,
                    "reply_translation_vi": draft.reply_translation_vi,
                },
            )
            await self._send_approval(approval, reason=draft.reason)
            if not video_mode:
                self.reply_watch.mark_drafted(target_url)
            sent += 1
        return sent

    async def _reply_tracking_loop(self) -> None:
        while True:
            try:
                await self._process_reply_tracking_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                LOGGER.exception("Automatic reply tracking pass failed")
            # Author responses are high-value and time-sensitive. Wake at least
            # every five minutes even when metric snapshots use a slower cadence.
            poll_minutes = min(5, self.settings.reply_tracking_poll_minutes)
            await asyncio.sleep(max(60, poll_minutes * 60))

    async def _process_reply_tracking_once(
        self,
        *,
        now: datetime | None = None,
    ) -> None:
        if not self.reply_learning.enabled:
            return
        current = now or datetime.now(UTC)
        for approval in self.approvals.items():
            if (
                approval.kind in {"reply", "post"}
                and approval.status in {"mobile_approved", "completed"}
            ):
                self.reply_learning.register_approval(approval)

        waiting = self.reply_learning.records("waiting")
        owner = self.settings.x_owner_username.strip().lstrip("@")
        if waiting and owner:
            timeline = await self.x_search.user_tweets_and_replies(owner, limit=60)
            for record in waiting:
                match = match_posted_content(record, timeline)
                if match is not None:
                    self.reply_learning.mark_discovered(record["approval_id"], match)
                    self.approvals.finish_mobile(
                        record["approval_id"],
                        published=True,
                    )
                    continue
                approved_at = datetime.fromisoformat(str(record["approved_at"]))
                if current - approved_at > timedelta(minutes=90):
                    self.reply_learning.mark_unmatched(record["approval_id"])
                    self.approvals.finish_mobile(
                        record["approval_id"],
                        published=False,
                    )

        for record in self.reply_learning.records("tracking"):
            checkpoint = self.reply_learning.due_checkpoint(record, now=current)
            author_response: XSearchResult | None = None
            should_watch_author = (
                self.reply_learning.author_response_check_due(
                    record,
                    now=current,
                )
            )
            if should_watch_author:
                try:
                    direct_replies = await self.x_search.tweet_replies(
                        int(record["reply_id"]),
                        limit=20,
                    )
                    self.reply_learning.mark_author_response_checked(
                        record["approval_id"],
                        checked_at=current,
                    )
                    root_author = str(record.get("root_author") or "").casefold()
                    author_response = next(
                        (
                            item
                            for item in direct_replies
                            if item.in_reply_to_tweet_id == int(record["reply_id"])
                            and item.username.casefold() == root_author
                        ),
                        None,
                    )
                except Exception:
                    LOGGER.warning(
                        "Could not check author response for approval %s",
                        record.get("approval_id"),
                        exc_info=True,
                    )
                if author_response is not None:
                    self.reply_learning.mark_author_response(
                        record["approval_id"],
                        author_response,
                        detected_at=current,
                    )
                    await self._create_author_followup(record, author_response)
                    self.reply_learning.mark_followup_created(record["approval_id"])

            if checkpoint is None:
                continue
            reply = await self.x_search.tweet_by_id(int(record["reply_id"]))
            if reply is None:
                continue
            root = (
                await self.x_search.tweet_by_id(int(record["target_id"]))
                if record.get("kind") == "reply" and record.get("target_id")
                else None
            )
            self.reply_learning.add_snapshot(
                record["approval_id"],
                checkpoint_minutes=checkpoint,
                reply=reply,
                root=root,
                author_replied=bool(record.get("author_replied") or author_response),
                captured_at=current,
            )
        tuned = self.reply_learning.maybe_tune(now=current)
        if tuned and self._application is not None and self.approval_chat_id is not None:
            status = self.reply_learning.status()
            await self._application.bot.send_message(
                chat_id=self.approval_chat_id,
                text=(
                    "Reply learning updated strategy weights after enough 24h samples. "
                    f"Version: {status.version}. Use /replylearn rollback to undo."
                ),
            )

    async def _create_author_followup(
        self,
        record: dict[str, Any],
        author_response: XSearchResult,
    ) -> None:
        if self._application is None:
            return
        original = self.approvals.get(str(record.get("approval_id") or ""))
        if original is None:
            return
        generated = await self.ai.generate_reply_from_text(author_response.text)
        approval = self.approvals.create(
            kind="reply",
            text=generated.text,
            chat_id=original.chat_id,
            approver_user_id=original.approver_user_id,
            target_url=author_response.url,
            target_label=f"@{author_response.username} follow-up",
            metadata=_reply_tracking_metadata(
                author_response,
                "author_specific_question",
            )
            | {
                "relationship_followup": True,
                "relationship_parent_approval_id": str(record.get("approval_id") or ""),
                "author_response_text": author_response.text,
                "author_response_url": author_response.url,
            },
        )
        await self._send_approval(
            approval,
            reason="The original author replied to you; continuing now can strengthen the conversation.",
        )

    async def automation_approval(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        del context
        query = update.callback_query
        if query is None or query.message is None:
            return
        parts = str(query.data or "").split(":", 2)
        if len(parts) != 3 or parts[0] != "automation":
            await query.answer("Invalid approval request.", show_alert=True)
            return
        decision, approval_id = parts[1], parts[2]
        if decision not in {
            "approve",
            "mobile",
            "continue",
            "reject",
            "skip",
            "stop",
            "alternative",
            "shorter",
            "visual",
        }:
            await query.answer("Unknown approval action.", show_alert=True)
            return
        answered = False
        try:
            existing = self.approvals.get(approval_id)
            if existing is None:
                raise RuntimeError("Unknown approval request.")
            if decision in {"continue", "stop"} and not bool(
                existing.metadata.get("relationship_followup")
            ):
                raise RuntimeError("This action is only available for an author follow-up.")
            if decision in {"alternative", "shorter"}:
                if existing.kind != "reply":
                    raise RuntimeError("This quick edit is only available for replies.")
                await query.answer("Generating a revised reply...")
                answered = True
                instruction = (
                    "Write a genuinely different angle using another supported detail. "
                    "Do not merely swap synonyms."
                    if decision == "alternative"
                    else "Make it shorter and faster to read while preserving the specific point."
                )
                revised = await self.ai.generate_reply_revision(
                    str(existing.metadata.get("root_text") or existing.target_label),
                    existing.text,
                    instruction,
                )
                revised_text = str(getattr(revised, "reply", revised)).strip()
                revised_translation = str(
                    getattr(revised, "reply_translation_vi", "") or ""
                ).strip()
                approval = self.approvals.update_text(existing.id, revised_text)
                self.approvals.update_metadata(
                    existing.id,
                    revision_count=int(existing.metadata.get("revision_count", 0)) + 1,
                    last_revision=decision,
                    reply_translation_vi=revised_translation,
                )
                await query.edit_message_text(
                    _approval_message_text(approval, reason="Revised on request"),
                    reply_markup=_approval_keyboard(approval),
                )
                return
            if decision == "visual":
                prompt = str(existing.metadata.get("image_prompt") or "").strip()
                if existing.kind != "post" or not prompt:
                    raise RuntimeError("No visual prompt is available for this approval.")
                await query.answer("Generating the visual...")
                answered = True
                image = await self.ai.generate_image(prompt)
                await query.message.reply_photo(
                    photo=_as_photo(image),
                    caption="On-demand visual for this post draft.",
                )
                return
            approval = self.approvals.decide(
                approval_id,
                approve=decision in {"approve", "mobile", "continue"},
                chat_id=query.message.chat.id,
                user_id=query.from_user.id,
                destination="mobile",
            )
            if decision == "stop" and bool(
                approval.metadata.get("relationship_followup")
            ):
                parent_id = str(
                    approval.metadata.get("relationship_parent_approval_id") or ""
                )
                if parent_id:
                    self.reply_learning.mark_conversation_stopped(parent_id)
            self.reply_learning.record_feedback(
                approval,
                approved=decision in {"approve", "mobile", "continue"},
            )
            if (
                approval.status == "mobile_approved"
                and approval.kind in {"reply", "post"}
            ):
                self.reply_learning.register_approval(approval)
            await query.answer()
            answered = True
            original = str(query.message.text or "").strip()
            mobile_note = _mobile_approval_note(approval)
            await query.edit_message_text(
                (
                    f"{original}\n\n{mobile_note}".strip()
                    if approval.status == "mobile_approved"
                    else (
                        f"{original}\n\nConversation stopped. No further follow-up "
                        "will be suggested for this exchange."
                        if decision == "stop"
                        else f"{original}\n\nRejected."
                    )
                ),
                reply_markup=(
                    _approval_keyboard(approval, include_decisions=False)
                    if approval.status == "mobile_approved"
                    else None
                ),
            )
        except Exception as exc:
            error = _friendly_error(exc)
            if not answered:
                await query.answer(error, show_alert=True)
            else:
                await query.message.reply_text(
                    f"Approval was saved, but Telegram could not refresh this card: {error}\n"
                    "Tap the approval button again to retry opening the mobile action."
                )

    async def trigger_replytargets(self, payload: dict[str, Any]) -> dict[str, Any]:
        query = str(payload.get("query", "")).strip()
        max_age_minutes = _reply_target_max_age_minutes(
            payload.get("reply_target_max_age_minutes"),
            default=self.settings.reply_target_max_age_minutes,
        )
        languages = parse_reply_target_languages(
            payload.get("reply_target_languages"),
            default=self.settings.reply_target_languages,
        )
        return self._spawn_automation(
            "replytargets",
            lambda: self._run_scheduled_replytargets(
                query,
                max_age_minutes,
                languages,
            ),
        )

    async def trigger_replyvideo(self, payload: dict[str, Any]) -> dict[str, Any]:
        query = str(payload.get("query", "")).strip()
        return self._spawn_automation(
            "replyvideo",
            lambda: self._run_scheduled_replyvideo(query),
        )

    async def trigger_tweettrend3(self, payload: dict[str, Any]) -> dict[str, Any]:
        category = str(payload.get("category", "auto")).strip().lower() or "auto"
        if category not in {"auto", "best", *AUTO_TREND_CATEGORIES}:
            raise RuntimeError("tweettrend3 category must be auto, trending, news, sport, or entertainment.")
        return self._spawn_automation(
            "tweettrend3",
            lambda: self._run_scheduled_tweettrend3(category),
        )

    async def next_approved_action(self) -> dict[str, Any] | None:
        approval = self.approvals.claim_next()
        return approval.as_extension_payload() if approval is not None else None

    async def finish_approved_action(
        self,
        approval_id: str,
        *,
        success: bool,
        error: str = "",
    ) -> None:
        approval = self.approvals.finish(
            approval_id,
            success=success,
            error=error,
        )
        if self._application is None:
            return
        if success:
            if approval.kind in {"reply", "post"}:
                self.reply_learning.register_approval(approval)
            detail = "Reply draft" if approval.kind == "reply" else "Post draft"
            text = f"{detail} opened and filled in X. Review it, then click the final X button."
        else:
            text = f"Could not fill the approved {approval.kind} in X: {error or 'unknown error'}"
        await self._application.bot.send_message(chat_id=approval.chat_id, text=text)

    def _spawn_automation(self, kind: str, factory) -> dict[str, Any]:
        if self._application is None:
            raise RuntimeError("Telegram bot is not ready.")
        if self.approval_chat_id is None:
            raise RuntimeError("No approval chat configured. Set TELEGRAM_APPROVAL_CHAT_ID in .env.")
        if self._automation_running:
            active = next(iter(self._automation_running))
            return {
                "ok": True,
                "status": "already-running",
                "kind": kind,
                "active_kind": active,
            }

        self._automation_running.add(kind)

        async def runner() -> None:
            try:
                await factory()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                LOGGER.exception("Scheduled %s failed", kind)
                if self._application is not None and self.approval_chat_id is not None:
                    await self._application.bot.send_message(
                        chat_id=self.approval_chat_id,
                        text=f"Scheduled /{kind} failed: {_friendly_error(exc)}",
                    )
            finally:
                self._automation_running.discard(kind)

        task = asyncio.create_task(runner(), name=f"automation-{kind}")
        self._automation_tasks.add(task)
        task.add_done_callback(self._automation_tasks.discard)
        return {"ok": True, "status": "accepted", "kind": kind}

    async def _run_scheduled_replytargets(
        self,
        query: str,
        max_age_minutes: int,
        languages: list[str],
    ) -> None:
        if self._application is None or self.approval_chat_id is None:
            raise RuntimeError("Automation chat is not ready.")
        status = await self._application.bot.send_message(
            chat_id=self.approval_chat_id,
            text=(
                "Scheduled /replytargets started.\n"
                f"Engine: {BOT_RUNTIME_REVISION}.\n"
                f"Lookback: {max_age_minutes} minutes.\n"
                f"Languages: {', '.join(languages)}."
            ),
        )
        try:
            search_query, results, selection_note = await self._get_reply_target_context(
                query,
                status,
                max_age_minutes=max_age_minutes,
                languages=languages,
            )
            if not results:
                await status.edit_text(_no_reply_targets_message(
                    search_query,
                    auto=not query,
                    max_age_minutes=max_age_minutes,
                    diagnostic=selection_note,
                ))
                return
            ready, watching = self.reply_watch.classify(results)
            watching_total = (
                len(
                    self.reply_watch.candidates_for_refresh(
                        limit=1_000,
                        languages=languages,
                        max_age_minutes=max_age_minutes,
                    )
                )
                if not query
                else len(watching)
            )
            remaining_cap = max(
                0,
                self.settings.creator_daily_reply_cap
                - _reply_approvals_created_today(
                    self.approvals.items(),
                    timezone_name=self.settings.creator_timezone,
                ),
            )
            confirmed_count = len(ready)
            if remaining_cap <= 0:
                await status.edit_text(
                    "Scheduled /replytargets reached today's reply-card cap. "
                    f"Confirmed now: {confirmed_count}. Watching total: {watching_total}. "
                    f"The cap resets with the next creator day in "
                    f"{self.settings.creator_timezone}."
                )
                return
            reply_batch, promoted_count = _select_reply_draft_batch(
                ready,
                watching,
                capacity=remaining_cap,
                max_items=self.settings.reply_target_batch_size,
            )
            if not reply_batch:
                if remaining_cap < MIN_REPLY_TARGET_BATCH_ITEMS:
                    await status.edit_text(
                        "Scheduled /replytargets has only one reply-card slot left today. "
                        "No Gemini job was spent because reply batches require at least two "
                        f"slots. The cap resets in {self.settings.creator_timezone}."
                    )
                    return
                await status.edit_text(
                    "Scheduled scan has fewer than two eligible candidates, so no Gemini "
                    f"job was spent. Eligible now: {len(ready) + len(watching)}. "
                    f"Watching now/total: {len(watching)}/{watching_total}. The next scan "
                    "will re-fetch those tweet IDs and fill a two-reply batch."
                )
                return
            await status.edit_text(
                f"Found a {len(reply_batch)}-reply batch "
                f"({promoted_count} early qualified); "
                f"watching now/total {len(watching)}/{watching_total}. "
                f"Selection: {selection_note.strip() or 'standard thresholds'}. "
                "Generating one batch..."
            )
            sent = await self._create_reply_approvals(
                reply_batch,
                query=search_query,
                chat_id=self.approval_chat_id,
                approver_user_id=self.approval_chat_id,
            )
            if sent:
                await _delete_message_safely(status)
            else:
                await status.edit_text(
                    "Scheduled /replytargets finished, but every returned target already "
                    "has an active approval card."
                )
        except Exception:
            await _delete_message_safely(status)
            raise

    async def _run_scheduled_replyvideo(self, query: str = "") -> None:
        if self._application is None or self.approval_chat_id is None:
            raise RuntimeError("Automation chat is not ready.")
        status = await self._application.bot.send_message(
            chat_id=self.approval_chat_id,
            text=(
                "Scheduled /replyvideo started. Hunting fresh global and Vietnamese "
                "videos with low reply competition..."
            ),
        )
        try:
            search_label, results, selection_note = await self._get_reply_video_context(
                query,
                status,
            )
            remaining_cap = max(
                0,
                self.settings.creator_daily_reply_cap
                - _reply_approvals_created_today(
                    self.approvals.items(),
                    timezone_name=self.settings.creator_timezone,
                ),
            )
            if remaining_cap < REPLY_VIDEO_MIN_BATCH_ITEMS:
                await status.edit_text(
                    "Scheduled /replyvideo needs two remaining reply-card slots. "
                    f"The daily cap resets in {self.settings.creator_timezone}."
                )
                return
            batch, visual_attachments, skipped_ungrounded = (
                await self._prepare_reply_video_evidence(
                    results,
                    status,
                    max_items=min(
                        self.settings.reply_video_batch_size,
                        remaining_cap,
                    ),
                )
            )
            if len(batch) < REPLY_VIDEO_MIN_BATCH_ITEMS:
                await status.edit_text(
                    "Scheduled /replyvideo searched strict, warm and fill tiers but found "
                    f"only {len(batch)} distinct eligible video(s). No Gemini job was spent; "
                    f"{skipped_ungrounded} ungrounded video(s) could not be analyzed. "
                    "The next 3-5 minute scan will try fresh results."
                )
                return
            await status.edit_text(
                f"Found {len(batch)} video reply targets. {selection_note} "
                "Generating one grounded reply batch..."
            )
            sent = await self._create_reply_approvals(
                batch,
                query=search_label,
                chat_id=self.approval_chat_id,
                approver_user_id=self.approval_chat_id,
                video_mode=True,
                visual_attachments=visual_attachments,
            )
            if sent:
                await _delete_message_safely(status)
            else:
                await status.edit_text(
                    "Scheduled /replyvideo finished, but all returned videos already have "
                    "active approval cards."
                )
        except Exception:
            await _delete_message_safely(status)
            raise

    async def _run_scheduled_tweettrend3(self, category: str) -> None:
        if self._application is None or self.approval_chat_id is None:
            raise RuntimeError("Automation chat is not ready.")
        status = _SilentStatus()
        contexts = await self._get_trend_contexts_for_tweettrend3(category, status)
        generated_posts = await self.ai.generate_trend_posts_batch(
            [(topic, x_context) for topic, x_context, _source, _category in contexts],
            output_language=self.settings.content_language,
        )
        for (topic, _x_context, source, selected_category), generated in zip(
            contexts,
            generated_posts,
        ):
            approval = self.approvals.create(
                kind="post",
                text=generated.text,
                chat_id=self.approval_chat_id,
                approver_user_id=self.approval_chat_id,
                target_label=topic,
                metadata={
                    "image_prompt": generated.image_prompt,
                    "source": source,
                    "category": selected_category,
                },
            )
            await self._send_approval(
                approval,
                reason=f"{source} - {selected_category} - {topic}",
            )

    async def _send_approval(
        self,
        approval: AutomationApproval,
        *,
        reason: str = "",
    ) -> None:
        if self._application is None:
            raise RuntimeError("Telegram bot is not ready.")
        body = _approval_message_text(approval, reason=reason)
        await self._application.bot.send_message(
            chat_id=approval.chat_id,
            text=body[:4096],
            reply_markup=_approval_keyboard(approval),
        )

    async def tweet(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.effective_message
        topic = " ".join(context.args).strip()
        if not topic:
            await self._request_command_input(update, "tweet")
            return

        await message.chat.send_action(ChatAction.TYPING)
        status = await message.reply_text("Writing a Vietnamese post from your topic...")
        try:
            generated = await self.ai.generate_topic_post(topic)
            await status.delete()
            approval = self.approvals.create(
                kind="post",
                text=generated.text,
                chat_id=message.chat.id,
                approver_user_id=(
                    update.effective_user.id
                    if update.effective_user is not None
                    else message.chat.id
                ),
                target_label=generated.topic or topic,
                metadata={"image_prompt": generated.image_prompt, "source": "user topic"},
            )
            await self._send_approval(approval, reason="User topic")
        except Exception as exc:
            await status.edit_text(_friendly_error(exc))

    async def tweettrend3(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.effective_message
        category, output_language = _parse_tweettrend3_args(context.args)

        await message.chat.send_action(ChatAction.TYPING)
        status_text = (
            "Finding current trends around your creator niche..."
            if category in {"auto", "best"}
            else f"Finding hot X trends in {category}..."
        )
        status_text = f"{status_text}\nOutput language: Vietnamese"
        status = await message.reply_text(status_text)
        try:
            contexts = await self._get_trend_contexts_for_tweettrend3(category, status)
            total = len(contexts)
            approver_user_id = (
                update.effective_user.id if update.effective_user is not None else message.chat.id
            )
            await status.edit_text(
                f"Generating {total} distinct topics in one Gemini batch...\n"
                f"Language: {output_language}\n\n"
                "If extension Auto Run is OFF, open its popup and click Run next job."
            )
            generated_posts = await self.ai.generate_trend_posts_batch(
                [(topic, x_context) for topic, x_context, _source, _category in contexts],
                output_language=output_language,
            )
            for index, (
                (topic, _x_context, source, selected_category),
                generated,
            ) in enumerate(zip(contexts, generated_posts), start=1):
                variant = TrendPostVariant(
                    angle=topic,
                    text=generated.text,
                    hashtags=[],
                    image_prompt=generated.image_prompt,
                    score="",
                )
                approval = self.approvals.create(
                    kind="post",
                    text=_format_trend_variant_copy(variant),
                    chat_id=message.chat.id,
                    approver_user_id=approver_user_id,
                    target_label=topic,
                    metadata={
                        "image_prompt": generated.image_prompt,
                        "source": source,
                        "category": selected_category,
                    },
                )
                await self._send_trend_variant(
                    message,
                    variant,
                    index,
                    approval=approval,
                    approval_reason=(
                        f"{source} | {selected_category} | {variant.angle}"
                    ),
                )
            await status.edit_text(
                f"Language: {output_language}\n"
                f"All {total} topic-based tweet drafts sent."
            )
        except Exception as exc:
            await status.edit_text(_friendly_error(exc))
        finally:
            await self._notify_x_account_errors(message)

    async def retweet(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.effective_message
        source, visual_note = _parse_retweet_args(_command_payload(message, context))
        if not source:
            await self._request_command_input(update, "retweet")
            return

        tweet_id = extract_tweet_id(source)
        if tweet_id is None:
            await message.reply_text("Could not read a tweet ID from that link.")
            return

        await message.chat.send_action(ChatAction.TYPING)
        status = await message.reply_text("Fetching source X post...")
        try:
            result = await self.x_search.tweet_by_id(tweet_id)
            if result is None or not result.text:
                await status.edit_text("Could not find readable content for that X post.")
                return

            media_urls = result.media_urls or []
            await status.edit_text("Writing an original remix from the source post...")
            generated = await self.ai.generate_retweet_remix(
                result.url,
                result.text,
                media_urls,
                visual_note=visual_note,
            )
            await status.delete()
            approval = self.approvals.create(
                kind="post",
                text=generated.text,
                chat_id=message.chat.id,
                approver_user_id=(
                    update.effective_user.id
                    if update.effective_user is not None
                    else message.chat.id
                ),
                target_label=f"Remix of @{result.username}",
                metadata={
                    "image_prompt": generated.image_prompt,
                    "source": result.url,
                },
            )
            await self._send_approval(approval, reason=f"Original remix of {result.url}")
        except Exception as exc:
            await status.edit_text(_friendly_error(exc))
        finally:
            await self._notify_x_account_errors(message)

    async def dailybrief(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.effective_message
        category = (context.args[0].strip().lower() if context.args else "trending")

        await message.chat.send_action(ChatAction.TYPING)
        status = await message.reply_text(f"Building daily brief from {category}...")
        try:
            topic, x_context, source, _results = await self._get_trend_context(category, status)
            await status.edit_text(f"Writing daily tweet options from: {topic}")
            variants = await self.ai.generate_daily_brief(category, topic, source, x_context)
            await status.edit_text(
                f"Source: {source}\n"
                f"Category: {category}\n"
                f"Topic: {topic}\n\n"
                "Sending daily tweet options with optional images..."
            )
            for index, variant in enumerate(variants, start=1):
                approval = self.approvals.create(
                    kind="post",
                    text=_format_trend_variant_copy(variant),
                    chat_id=message.chat.id,
                    approver_user_id=(
                        update.effective_user.id
                        if update.effective_user is not None
                        else message.chat.id
                    ),
                    target_label=variant.angle or topic,
                    metadata={
                        "image_prompt": variant.image_prompt,
                        "source": source,
                        "category": category,
                    },
                )
                await self._send_trend_variant(
                    message,
                    variant,
                    index,
                    label="Daily tweet",
                    approval=approval,
                    approval_reason=f"{source} | {category} | {topic}",
                )
            await status.edit_text(
                f"Source: {source}\nCategory: {category}\nTopic: {topic}\n\n"
                "Daily tweet options sent."
            )
        except Exception as exc:
            await status.edit_text(_friendly_error(exc))
        finally:
            await self._notify_x_account_errors(message)

    async def tweetx(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.effective_message
        topic = " ".join(context.args).strip()
        if not topic:
            await self._request_command_input(update, "tweetx")
            return

        await message.chat.send_action(ChatAction.TYPING)
        status = await message.reply_text("Searching X for live context...")
        try:
            search_query = default_english_query(topic)
            results = await self.x_search.search(search_query)
            if not results:
                await status.edit_text(f"No X posts found for: {search_query}")
                return

            await status.edit_text("Writing a tweet from the X context...")
            generated = await self.ai.generate_topic_post_from_x_context(
                topic,
                summarize_x_context(results, max_items=TREND_CONTEXT_X_ITEMS),
            )
            await status.delete()
            approval = self.approvals.create(
                kind="post",
                text=generated.text,
                chat_id=message.chat.id,
                approver_user_id=(
                    update.effective_user.id
                    if update.effective_user is not None
                    else message.chat.id
                ),
                target_label=generated.topic or topic,
                metadata={
                    "image_prompt": generated.image_prompt,
                    "source": f"X search: {search_query}",
                },
            )
            await self._send_approval(
                approval,
                reason=f"Live X context: {search_query}",
            )
        except Exception as exc:
            await status.edit_text(_friendly_error(exc))
        finally:
            await self._notify_x_account_errors(message)

    async def xsearch(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.effective_message
        query = " ".join(context.args).strip()
        if not query:
            await message.reply_text("Usage: /xsearch <X search query>")
            return

        status = await message.reply_text("Searching X...")
        try:
            search_query = default_english_query(query)
            results = await self.x_search.search(search_query)
            await _send_text_chunks(
                status,
                f"X results for: {search_query}\n\n{format_x_results(results)}",
            )
        except Exception as exc:
            await status.edit_text(_friendly_error(exc))
        finally:
            await self._notify_x_account_errors(message)

    async def replytargets(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.effective_message
        query = " ".join(context.args).strip()
        if query.lower() == "auto":
            query = ""

        await message.chat.send_action(ChatAction.TYPING)
        max_age_minutes = self.settings.reply_target_max_age_minutes
        languages = parse_reply_target_languages(
            self.settings.reply_target_languages
        )
        status = await message.reply_text(
            f"Finding fast-moving reply targets from the last {max_age_minutes} minutes "
            f"in {', '.join(languages)}..."
            if query
            else (
                "Comparing hot topics and late breakouts from the last "
                f"{max_age_minutes} minutes in {', '.join(languages)}..."
            )
        )
        try:
            search_query, results, auto_note = await self._get_reply_target_context(
                query,
                status,
                max_age_minutes=max_age_minutes,
                languages=languages,
            )
            if not results:
                await status.edit_text(
                    _no_reply_targets_message(
                        search_query,
                        auto=not query,
                        max_age_minutes=max_age_minutes,
                        diagnostic=auto_note,
                    )
                )
                return
            ready, watching = self.reply_watch.classify(results)
            watching_total = (
                len(
                    self.reply_watch.candidates_for_refresh(
                        limit=1_000,
                        languages=languages,
                        max_age_minutes=max_age_minutes,
                    )
                )
                if not query
                else len(watching)
            )
            remaining_cap = max(
                0,
                self.settings.creator_daily_reply_cap
                - _reply_approvals_created_today(
                    self.approvals.items(),
                    timezone_name=self.settings.creator_timezone,
                ),
            )
            confirmed_count = len(ready)
            if remaining_cap <= 0:
                await status.edit_text(
                    "Today's reply-card cap has been reached. "
                    f"Confirmed now: {confirmed_count}. Watching total: {watching_total}. "
                    f"The cap resets with the next creator day in "
                    f"{self.settings.creator_timezone}."
                )
                return
            reply_batch, promoted_count = _select_reply_draft_batch(
                ready,
                watching,
                capacity=remaining_cap,
                max_items=self.settings.reply_target_batch_size,
            )
            if not reply_batch:
                if remaining_cap < MIN_REPLY_TARGET_BATCH_ITEMS:
                    await status.edit_text(
                        "Only one reply-card slot remains today. No Gemini job was spent "
                        "because reply batches require at least two slots."
                    )
                    return
                await status.edit_text(
                    "Fewer than two eligible candidates are available, so no Gemini job "
                    f"was spent. Eligible now: {len(ready) + len(watching)}. Watching "
                    f"now/total: {len(watching)}/{watching_total}."
                )
                return
            await status.edit_text(
                f"Drafting a {len(reply_batch)}-reply batch "
                f"({promoted_count} early qualified); "
                f"watching now/total {len(watching)}/{watching_total}. "
                f"Selection: {auto_note.strip() or 'standard thresholds'}..."
            )
            approver_user_id = (
                update.effective_user.id if update.effective_user is not None else message.chat.id
            )
            sent = await self._create_reply_approvals(
                reply_batch,
                query=search_query,
                chat_id=message.chat.id,
                approver_user_id=approver_user_id,
            )
            await status.edit_text(
                f"Sent {sent} reply card(s). Watching total: {watching_total}."
            )
        except Exception as exc:
            await status.edit_text(_friendly_error(exc))
        finally:
            await self._notify_x_account_errors(message)

    async def replyvideo(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.effective_message
        query = " ".join(context.args).strip()
        if query.lower() == "auto":
            query = ""
        await message.chat.send_action(ChatAction.TYPING)
        status = await message.reply_text(
            "Hunting fresh global and Vietnamese videos, prioritizing view velocity "
            "and low reply competition..."
        )
        try:
            search_label, results, selection_note = await self._get_reply_video_context(
                query,
                status,
            )
            remaining_cap = max(
                0,
                self.settings.creator_daily_reply_cap
                - _reply_approvals_created_today(
                    self.approvals.items(),
                    timezone_name=self.settings.creator_timezone,
                ),
            )
            if remaining_cap < REPLY_VIDEO_MIN_BATCH_ITEMS:
                await status.edit_text(
                    "At least two daily reply-card slots are required for /replyvideo. "
                    f"The cap resets in {self.settings.creator_timezone}."
                )
                return
            batch, visual_attachments, skipped_ungrounded = (
                await self._prepare_reply_video_evidence(
                    results,
                    status,
                    max_items=min(
                        self.settings.reply_video_batch_size,
                        remaining_cap,
                    ),
                )
            )
            if len(batch) < REPLY_VIDEO_MIN_BATCH_ITEMS:
                await status.edit_text(
                    "Only "
                    f"{len(batch)} distinct eligible video(s) remained after strict, warm "
                    "and fill searches. "
                    f"{skipped_ungrounded} ungrounded video(s) could not be analyzed. "
                    "No Gemini job was spent; try again in 3-5 minutes."
                )
                return
            await status.edit_text(
                f"Drafting {len(batch)} video replies. {selection_note}"
            )
            approver_user_id = (
                update.effective_user.id
                if update.effective_user is not None
                else message.chat.id
            )
            sent = await self._create_reply_approvals(
                batch,
                query=search_label,
                chat_id=message.chat.id,
                approver_user_id=approver_user_id,
                video_mode=True,
                visual_attachments=visual_attachments,
            )
            await status.edit_text(f"Sent {sent} viral-video reply card(s).")
        except Exception as exc:
            await status.edit_text(_friendly_error(exc))
        finally:
            await self._notify_x_account_errors(message)

    async def persona(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.effective_message
        raw_args = " ".join(context.args).strip()
        if not raw_args:
            await self._request_command_input(update, "persona")
            return
        if raw_args.lower() in {"show", "current"}:
            await message.reply_text(_format_persona(self.settings))
            return

        try:
            updates = _parse_persona_args(raw_args)
            update_env_value("CREATOR_NICHE", updates.get("niche", self.settings.creator_niche))
            update_env_value("CREATOR_VOICE", updates.get("voice", self.settings.creator_voice))
            update_env_value("TARGET_AUDIENCE", updates.get("audience", self.settings.target_audience))
            self.settings = replace(
                self.settings,
                creator_niche=updates.get("niche", self.settings.creator_niche),
                creator_voice=updates.get("voice", self.settings.creator_voice),
                target_audience=updates.get("audience", self.settings.target_audience),
            )
            await self.trend_sources.aclose()
            self.ai = create_ai_service(self.settings)
            self.x_search = XSearchService(self.settings)
            self.trend_sources = TrendSourceService(self.settings, self.x_search)
            await message.reply_text(f"Persona updated.\n\n{_format_persona(self.settings)}")
        except Exception as exc:
            await message.reply_text(_friendly_error(exc))

    async def importcookie(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.effective_message
        raw_args = " ".join(context.args).strip()
        if not raw_args:
            chat = update.effective_chat
            if chat is not None and chat.type != "private":
                await message.reply_text(
                    "For cookie security, select /importcookie in a private chat with this bot."
                )
                return
            await self._request_command_input(update, "importcookie")
            return

        account_name, cookie = _parse_importcookie_args(raw_args, self.settings.x_account_name)
        if not _looks_like_x_cookie(cookie):
            await message.reply_text(
                "Cookie is missing auth_token or ct0. Expected format:\n"
                "/importcookie auth_token=YOUR_AUTH_TOKEN; ct0=YOUR_CT0\n"
                "/importcookie account2 auth_token=YOUR_AUTH_TOKEN; ct0=YOUR_CT0"
            )
            return

        await _delete_message_safely(message)
        status = await message.chat.send_message("Saving X cookie...")
        try:
            saved_name = await self.x_search.import_cookie_account(account_name, cookie)
            if saved_name == self.settings.x_account_name:
                update_env_value("X_COOKIE", cookie)
                self.settings = replace(self.settings, x_cookie=cookie)
                await self.trend_sources.aclose()
                self.x_search = XSearchService(self.settings)
                self.trend_sources = TrendSourceService(self.settings, self.x_search)
            await status.edit_text(
                f"X cookie saved for account: {saved_name}\n"
                "twscrape will rotate across active accounts automatically.\n\n"
                "Try:\n"
                "/tweetx AI agents\n\n"
                "For security, I tried to delete the Telegram message that contained the cookie."
            )
        except Exception as exc:
            await status.edit_text(_friendly_error(exc))

    async def xaccounts(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        del context
        message = update.effective_message
        status = await message.reply_text("Checking X accounts...")
        try:
            accounts = await self.x_search.accounts_info()
            await status.edit_text(_format_x_accounts(accounts))
            await self._notify_x_account_errors(message, accounts)
        except Exception as exc:
            await status.edit_text(_friendly_error(exc))

    async def xremove(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.effective_message
        account_name = " ".join(context.args).strip()
        if not account_name:
            await self._request_command_input(update, "xremove")
            return

        status = await message.reply_text(f"Removing X account: {account_name}...")
        try:
            removed_name = await self.x_search.remove_cookie_account(account_name)
            self._x_account_error_notices.pop(removed_name, None)
            if removed_name == self.settings.x_account_name:
                update_env_value("X_COOKIE", "")
                self.settings = replace(self.settings, x_cookie="")
                await self.trend_sources.aclose()
                self.x_search = XSearchService(self.settings)
                self.trend_sources = TrendSourceService(self.settings, self.x_search)
            await status.edit_text(
                f"Removed X account: {removed_name}\n\n"
                "Use /xaccounts to verify the active account list."
            )
        except Exception as exc:
            await status.edit_text(_friendly_error(exc))

    async def reply(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.effective_message
        source = _command_payload(message, context)
        if not source:
            await self._request_command_input(update, "reply")
            return

        await message.chat.send_action(ChatAction.TYPING)
        tweet_id = extract_tweet_id(source)
        status_text = (
            "Fetching source X post..."
            if tweet_id is not None
            else "Writing a reply from the pasted tweet..."
        )
        status = await message.reply_text(status_text)
        try:
            tweet_text = source
            if tweet_id is not None:
                result = await self.x_search.tweet_by_id(tweet_id)
                if result is None or not result.text:
                    await status.edit_text("Could not find readable content for that X post.")
                    return
                tweet_text = result.text
                await status.edit_text("Writing a reply from the X post...")
            generated = await self.ai.generate_reply_from_text(tweet_text)
            await status.delete()
            await message.reply_text(generated.text)
        except Exception as exc:
            await status.edit_text(_friendly_error(exc))
        finally:
            if tweet_id is not None:
                await self._notify_x_account_errors(message)

    async def _get_trend_context(
        self,
        category: str,
        status,
    ) -> tuple[str, str, str, list[XSearchResult]]:
        await status.edit_text(
            f"Scanning X, Google Trends, and RSS sources for {category}..."
        )
        signals, errors = await self.trend_sources.collect(category)
        if not signals:
            await status.edit_text(
                "Multi-source trend scan returned no items. Falling back to hot X search..."
            )
            fallback_query = ""
            results: list[XSearchResult] = []
            try:
                fallback_query, results = await self.x_search.trend_fallback_search(category)
            except Exception as exc:
                errors.append(f"X hot search fallback: {exc}")
            if not results:
                detail = f" Details: {'; '.join(errors[-3:])}" if errors else ""
                raise RuntimeError(
                    "No Google/RSS/X trend context found. Try /tweettrend3 news, "
                    "refresh X cookies, or add RSS feeds with TREND_RSS_URLS."
                    f"{detail}"
                )
            return (
                f"hot X discussion in {category}",
                summarize_x_context(results, max_items=TREND_CONTEXT_X_ITEMS),
                f"X hot search fallback ({fallback_query})",
                results,
            )

        lead = signals[0]
        await status.edit_text(
            f"Enriching multi-source trend with recent X context: {lead.title}"
        )
        search_query = ""
        results: list[XSearchResult] = []
        try:
            search_query, results = await self.x_search.search_recent(
                lead.title,
                since_minutes=24 * 60,
                limit=min(self.settings.x_search_limit, TREND_CONTEXT_X_ITEMS),
                product="Latest",
            )
        except Exception as exc:
            errors.append(f"X enrichment: {exc}")

        context_parts = [
            "Multi-source trend context:\n"
            + summarize_trend_signals(signals, max_items=TREND_CONTEXT_SIGNAL_ITEMS)
        ]
        if results:
            context_parts.append(
                f"Recent X context for {search_query}:\n"
                f"{summarize_x_context(results, max_items=TREND_CONTEXT_X_ITEMS)}"
            )
        if errors:
            context_parts.append("Source notes:\n" + "\n".join(f"- {error}" for error in errors[-4:]))

        return (
            lead.title,
            "\n\n".join(context_parts),
            f"multi-source trend scan ({lead.source})",
            results,
        )

    async def _get_auto_trend_context(
        self,
        status,
    ) -> tuple[str, str, str, list[XSearchResult], str]:
        best: tuple[str, str, str, list[XSearchResult], str] | None = None
        errors: list[str] = []

        for category in AUTO_TREND_CATEGORIES:
            try:
                await status.edit_text(f"Checking hot X trends in {category}...")
                topic, x_context, source, results = await self._get_trend_context(
                    category,
                    status,
                )
            except Exception as exc:
                errors.append(f"{category}: {exc}")
                continue

            candidate = (topic, x_context, source, results, category)
            if best is None or _trend_context_score(candidate) > _trend_context_score(best):
                best = candidate

        if best is not None:
            return best

        detail = "; ".join(errors[-3:]) if errors else "no category returned usable context"
        raise RuntimeError(
            "No auto trend context found. Try /tweettrend3 news or /tweettrend3 entertainment. "
            f"Details: {detail}"
        )

    async def _get_trend_contexts_for_tweettrend3(
        self,
        category: str,
        status,
        count: int = 3,
    ) -> list[tuple[str, str, str, str]]:
        categories = AUTO_TREND_CATEGORIES if category in {"auto", "best"} else (category,)
        candidates: list[tuple[Any, str, list[Any], list[str]]] = []

        # Auto mode should find conversations in the configured content lane,
        # not merely the largest general-interest trends of the day.
        if category in {"auto", "best"}:
            await status.edit_text(
                f"Finding current trends around your niche: {self.settings.creator_niche}..."
            )
            niche_signals, niche_errors = await self.trend_sources.collect_niche(
                self.settings.creator_niche
            )
            for signal in niche_signals:
                candidates.append((signal, "niche", niche_signals, niche_errors))

        niche_topic_count = len({_trend_topic_key(item[0].title) for item in candidates})
        if niche_topic_count < count:
            await status.edit_text(
                "Scanning X, Google Trends, and RSS categories concurrently..."
            )
            category_results = await asyncio.gather(
                *(self.trend_sources.collect(item) for item in categories),
                return_exceptions=True,
            )
            for selected_category, response in zip(categories, category_results):
                if isinstance(response, Exception):
                    continue
                signals, errors = response
                for signal in signals:
                    candidates.append((signal, selected_category, signals, errors))

        selected: list[tuple[Any, str, list[Any], list[str]]] = []
        seen_topics: set[str] = set()
        for candidate in sorted(
            candidates,
            key=lambda item: (item[1] == "niche", item[0].score),
            reverse=True,
        ):
            topic_key = _trend_topic_key(candidate[0].title)
            if not topic_key or topic_key in seen_topics:
                continue
            seen_topics.add(topic_key)
            selected.append(candidate)
            if len(selected) == count:
                break

        await status.edit_text(
            f"Enriching {len(selected)} selected trend(s) with X context concurrently..."
        )

        async def enrich_selected(
            item: tuple[Any, str, list[Any], list[str]],
        ) -> tuple[str, str, str, str]:
            signal, selected_category, signals, errors = item
            search_query = ""
            results: list[XSearchResult] = []
            notes = list(errors)
            try:
                search_query, results = await self.x_search.search_recent(
                    signal.title,
                    since_minutes=24 * 60,
                    limit=min(self.settings.x_search_limit, TREND_CONTEXT_X_ITEMS),
                    product="Latest",
                )
            except Exception as exc:
                notes.append(f"X enrichment: {exc}")

            related_signals = [signal] + [item for item in signals if item != signal]
            context_parts = [
                "Multi-source trend context:\n"
                + summarize_trend_signals(
                    related_signals,
                    max_items=TREND_CONTEXT_SIGNAL_ITEMS,
                )
            ]
            if results:
                context_parts.append(
                    f"Recent X context for {search_query}:\n"
                    f"{summarize_x_context(results, max_items=TREND_CONTEXT_X_ITEMS)}"
                )
            if notes:
                context_parts.append(
                    "Source notes:\n" + "\n".join(f"- {note}" for note in notes[-4:])
                )
            return (
                signal.title,
                "\n\n".join(context_parts),
                f"multi-source trend scan ({signal.source})",
                selected_category,
            )

        contexts = list(await asyncio.gather(*(enrich_selected(item) for item in selected)))
        if contexts:
            return contexts

        # A source outage may leave only the existing hot-X fallback. Keep that
        # one useful topic rather than fabricating three copies of the same topic.
        if category in {"auto", "best"}:
            topic, x_context, source, _results, selected_category = await self._get_auto_trend_context(status)
        else:
            topic, x_context, source, _results = await self._get_trend_context(category, status)
            selected_category = category
        return [(topic, x_context, source, selected_category)]

    async def _get_reply_target_context(
        self,
        query: str,
        status,
        *,
        max_age_minutes: int = 360,
        languages: list[str] | tuple[str, ...] | str | None = None,
        mode: str | None = None,
    ) -> tuple[str, list[XSearchResult], str]:
        selected_mode = mode or self.settings.reply_target_mode
        selected_languages = parse_reply_target_languages(
            languages,
            default=self.settings.reply_target_languages,
        )
        if query:
            candidates = _expand_reply_target_query(query, selected_languages)
            watched_rows: list[dict[str, Any]] = []
        else:
            candidates = await self._auto_reply_target_queries(
                selected_languages,
                mode=selected_mode,
            )
            watched_rows = self.reply_watch.candidates_for_refresh(
                limit=REPLY_TARGET_REFRESH_LIMIT,
                languages=selected_languages,
                states=("watching", "ready"),
                max_age_minutes=max_age_minutes,
            )
        candidates = _dedupe_queries(candidates)[:REPLY_TARGET_MAX_CANDIDATES]
        last_search_query = query or "auto hot topics"

        await status.edit_text(
            "Comparing fresh conversation momentum...\n"
            f"Queries: {len(candidates)} (serialized to protect the X session pool)\n"
            f"Mode: {selected_mode}\n"
            f"Lookback: {max_age_minutes} minutes\n"
            f"Languages: {', '.join(selected_languages)}\n"
            f"Previously watched to recheck: {len(watched_rows)}"
        )
        # A single logical lane still checks Top and Latest. Serializing lanes
        # prevents a small cookie pool from being exhausted by simultaneous
        # SearchTimeline leases, which twscrape otherwise reports as if no
        # account existed.
        semaphore = asyncio.Semaphore(1)
        search_failures: list[str] = []

        async def search_one(
            candidate: str,
        ) -> tuple[str, str, list[XSearchResult]] | None:
            try:
                async with semaphore:
                    search_query, recent_results = await asyncio.wait_for(
                        self._search_reply_target_pool(
                            candidate,
                            max_age_minutes=max_age_minutes,
                        ),
                        timeout=REPLY_TARGET_SEARCH_TIMEOUT_SECONDS,
                    )
            except Exception as exc:
                search_failures.append(
                    f"{_truncate_text(candidate, 80)}: {_truncate_text(_friendly_error(exc), 180)}"
                )
                return None
            return candidate, search_query, recent_results

        responses = await asyncio.gather(
            *(search_one(candidate) for candidate in candidates)
        )
        searched = [item for item in responses if item is not None]
        if not searched and search_failures:
            samples = " | ".join(search_failures[:3])
            raise RuntimeError(
                f"All {len(candidates)} reply-target search lanes failed. "
                f"This is an X/twscrape account or transport problem, not a lack of viral "
                f"tweets. First errors: {samples}"
            )
        if searched:
            last_search_query = searched[-1][1]

        # Compare every configured language/topic in one pool. Snapshot deltas
        # replace lifetime averages once the same post has been observed twice.
        combined_results, search_query_by_url = _combine_reply_target_results(searched)
        current_urls = {result.url for result in combined_results if result.url}
        refreshed = await self._refresh_watched_reply_targets(
            watched_rows,
            exclude_urls=current_urls,
        )
        if refreshed:
            combined_results = _merge_reply_target_search_products(
                [combined_results, refreshed]
            )
            for result in refreshed:
                search_query_by_url.setdefault(result.url, "persisted watchlist refresh")
        combined_results = self.reply_target_metrics.observe(combined_results)
        strict_results = self._rank_reply_target_pool(
            combined_results,
            max_age_minutes=max_age_minutes,
            relaxed=False,
        )
        results = list(strict_results)
        fallback_level = ""
        if len(results) < MIN_REPLY_TARGET_BATCH_ITEMS:
            # Re-rank the same fetched posts with relaxed momentum thresholds
            # before lowering the configured view floor.
            relaxed_results = self._rank_reply_target_pool(
                combined_results,
                max_age_minutes=max_age_minutes,
                relaxed=True,
            )
            previous_count = len(results)
            results = _merge_reply_target_search_products(
                [results, relaxed_results]
            )[:5]
            if len(results) > previous_count:
                fallback_level = "relaxed momentum"

        if len(results) < MIN_REPLY_TARGET_BATCH_ITEMS:
            configured_views = max(0, self.settings.reply_target_min_views)
            volume_view_floor = min(
                configured_views,
                max(
                    MIN_REPLY_TARGET_VOLUME_FALLBACK_VIEWS,
                    configured_views // 4,
                ),
            )
            volume_results = self._rank_reply_target_pool(
                combined_results,
                max_age_minutes=max_age_minutes,
                relaxed=True,
                min_view_count=volume_view_floor,
                allow_view_only_signal=True,
            )
            previous_count = len(results)
            results = _merge_reply_target_search_products(
                [results, volume_results]
            )[:5]
            if len(results) > previous_count:
                fallback_level = (
                    f"volume fallback ({volume_view_floor}+ views when visible)"
                )

        if len(results) < MIN_REPLY_TARGET_BATCH_ITEMS:
            # The user explicitly prefers receiving a real two-reply batch on
            # every healthy scan. Exhaust the already fetched, still-fresh root
            # posts before returning empty. This tier accepts any visible view
            # signal, but keeps age, root-post, active-approval and dedupe gates.
            minimum_batch_results = self._rank_reply_target_pool(
                combined_results,
                max_age_minutes=max_age_minutes,
                relaxed=True,
                min_view_count=0,
                allow_view_only_signal=True,
            )
            previous_count = len(results)
            results = _merge_reply_target_search_products(
                [results, minimum_batch_results]
            )[:5]
            if len(results) > previous_count:
                fallback_level = "minimum-batch fallback (any visible view signal)"

        results = await self._enrich_reply_thread_context(results)
        results = self._apply_reply_target_mode(results, selected_mode)
        if results:
            selected_query = search_query_by_url.get(results[0].url, last_search_query)
            if fallback_level:
                return (
                    selected_query,
                    results,
                    f"Selected with {fallback_level} to fill a two-reply batch.\n",
                )
            note = (
                "Selected by momentum across the requested topic and languages.\n"
                if query
                else "Selected by momentum across current topics and languages.\n"
            )
            return selected_query, results, note

        diagnostic = (
            f"Fetched {len(combined_results)} unique root posts from "
            f"{len(searched)} successful search responses; {len(search_failures)} "
            "search lane(s) failed. Fewer than two posts remained after the age, "
            "root-post, active-approval, deduplication, and visible-signal gates."
        )
        return last_search_query, [], diagnostic

    async def _get_reply_video_context(
        self,
        query: str,
        status,
    ) -> tuple[str, list[XSearchResult], str]:
        strict_age = self.settings.reply_video_max_age_minutes
        fill_age = min(90, max(strict_age, strict_age * 2))
        lanes = _reply_video_search_queries(query)
        await status.edit_text(
            "Scanning fresh X video lanes...\n"
            f"Lanes: {', '.join(label for label, _query in lanes)}\n"
            f"Strict window: {strict_age} minutes; emergency fill window: {fill_age} minutes\n"
            "Target mix: two global videos plus one Vietnamese video when available."
        )
        semaphore = asyncio.Semaphore(4)

        async def search_one(
            label: str,
            lane_query: str,
            product: str,
        ) -> tuple[str, str, list[XSearchResult]] | None:
            try:
                async with semaphore:
                    search_query, results = await asyncio.wait_for(
                        self.x_search.search_recent(
                            _reply_target_root_query(lane_query),
                            since_minutes=fill_age,
                            limit=REPLY_VIDEO_RESULT_LIMIT,
                            product=product,
                        ),
                        timeout=REPLY_TARGET_SEARCH_TIMEOUT_SECONDS,
                    )
                return label, search_query, results
            except Exception:
                return None

        responses = await asyncio.gather(
            *(
                search_one(label, lane_query, product)
                for label, lane_query in lanes
                for product in ("Top", "Latest")
            )
        )
        searched = [item for item in responses if item is not None]
        if not searched:
            raise RuntimeError(
                "Every /replyvideo X search lane failed. Check the twscrape account/cookie."
            )
        combined, search_query_by_url = _combine_reply_target_results(searched)
        combined = self.reply_target_metrics.observe(combined)
        configured_floor = max(0, self.settings.reply_video_min_views)
        strict = rank_viral_video_posts(
            combined,
            max_items=24,
            max_age_minutes=strict_age,
            min_view_count=configured_floor,
            min_like_count_when_views_missing=150,
            min_view_velocity=300.0,
        )
        warm_floor = min(configured_floor, max(2_000, configured_floor // 5))
        warm = rank_viral_video_posts(
            combined,
            max_items=24,
            max_age_minutes=strict_age,
            min_view_count=warm_floor,
            min_like_count_when_views_missing=60,
            min_view_velocity=60.0,
        )
        fill_floor = min(warm_floor, max(500, configured_floor // 30))
        fill = rank_viral_video_posts(
            combined,
            max_items=24,
            max_age_minutes=fill_age,
            min_view_count=fill_floor,
            min_like_count_when_views_missing=20,
            min_view_velocity=0.0,
        )
        candidates = _merge_reply_target_search_products([strict, warm, fill])
        candidates = [
            result
            for result in candidates
            if not self.approvals.has_active_target(result.url)
        ]
        selected = _select_reply_video_mix(
            candidates,
            max_items=8,
        )
        strict_urls = {item.url for item in strict}
        warm_urls = {item.url for item in warm}
        if selected and all(item.url in strict_urls for item in selected):
            tier = f"Strict: <= {strict_age}m, {configured_floor:,}+ views, 300+ views/min."
        elif selected and all(item.url in warm_urls for item in selected):
            tier = f"Warm fallback: <= {strict_age}m, {warm_floor:,}+ views."
        else:
            tier = f"Fill fallback: <= {fill_age}m, {fill_floor:,}+ views."
        search_label = (
            search_query_by_url.get(selected[0].url, "viral video lanes")
            if selected
            else "viral video lanes"
        )
        return search_label, selected, tier

    async def _prepare_reply_video_evidence(
        self,
        results: list[XSearchResult],
        status,
        *,
        max_items: int = REPLY_VIDEO_CONTEXT_ITEMS,
    ) -> tuple[list[XSearchResult], list[ImageAttachment], int]:
        prepared: list[XSearchResult] = []
        attachments: list[ImageAttachment] = []
        skipped = 0
        for result in results:
            quality = _video_context_quality(result)
            if quality != "visual_required":
                reliable_caption = _is_reliable_video_context_text(result.text)
                reliable_descriptions = [
                    description
                    for description in (result.media_descriptions or [])
                    if _is_reliable_video_context_text(
                        description,
                        media_description=True,
                    )
                ]
                prepared.append(
                    replace(
                        result,
                        text=result.text if reliable_caption else "",
                        video_context_quality=quality,
                        media_descriptions=(
                            reliable_descriptions
                            if quality == "grounded_text"
                            else []
                        ),
                    )
                )
            elif not self.settings.reply_video_frame_analysis:
                skipped += 1
                continue
            else:
                remaining_attachment_slots = 5 - len(attachments)
                if remaining_attachment_slots < 2:
                    skipped += 1
                    continue
                reserve_for_second_visual = (
                    2
                    if not prepared and max_items >= REPLY_VIDEO_MIN_BATCH_ITEMS
                    else 0
                )
                frame_count = min(
                    self.settings.reply_video_frame_count,
                    max(2, remaining_attachment_slots - reserve_for_second_visual),
                )
                await status.edit_text(
                    "A selected video has no reliable caption or media description. "
                    f"Downloading it and extracting {frame_count} representative frames..."
                )
                downloaded: DownloadedMedia | None = None
                try:
                    async with self._download_semaphore:
                        downloaded = await asyncio.to_thread(
                            self.media_downloader.download,
                            result.url,
                        )
                    prefix = f"candidate-{len(prepared) + 1}-{result.id}"
                    frames = await asyncio.to_thread(
                        self.video_frame_extractor.extract,
                        downloaded.path,
                        prefix=prefix,
                        max_frames=frame_count,
                    )
                except Exception as exc:
                    LOGGER.info(
                        "Skipping ungrounded replyvideo candidate %s: %s",
                        result.url,
                        exc,
                    )
                    skipped += 1
                    continue
                finally:
                    if downloaded is not None:
                        await asyncio.to_thread(downloaded.cleanup)
                attachments.extend(frames)
                prepared.append(
                    replace(
                        result,
                        text="",
                        video_context_quality="visual_frames",
                        media_descriptions=[],
                        visual_frame_names=[frame.name for frame in frames],
                    )
                )
            if len(prepared) >= max_items:
                break
        return prepared[:max_items], attachments, skipped

    async def _refresh_watched_reply_targets(
        self,
        rows: list[dict[str, Any]],
        *,
        exclude_urls: set[str] | None = None,
    ) -> list[XSearchResult]:
        excluded = exclude_urls or set()
        semaphore = asyncio.Semaphore(3)

        async def fetch(row: dict[str, Any]) -> XSearchResult | None:
            if str(row.get("url") or "") in excluded:
                return None
            try:
                tweet_id = int(row.get("tweet_id"))
                async with semaphore:
                    return await asyncio.wait_for(
                        self.x_search.tweet_by_id(tweet_id),
                        timeout=REPLY_TARGET_REFRESH_TIMEOUT_SECONDS,
                    )
            except Exception:
                return None

        refreshed = await asyncio.gather(*(fetch(row) for row in rows))
        return [result for result in refreshed if isinstance(result, XSearchResult)]

    async def _enrich_reply_thread_context(
        self,
        results: list[XSearchResult],
    ) -> list[XSearchResult]:
        semaphore = asyncio.Semaphore(3)

        async def enrich(result: XSearchResult) -> XSearchResult:
            try:
                async with semaphore:
                    replies = await asyncio.wait_for(
                        self.x_search.tweet_replies(result.id, limit=12),
                        timeout=12,
                    )
            except Exception:
                return result
            non_author = [
                item
                for item in replies
                if item.username.casefold() != result.username.casefold()
            ]
            top_reply_likes = max(
                (item.like_count for item in non_author),
                default=0,
            )
            author_has_replied = any(
                item.username.casefold() == result.username.casefold()
                and item.id != result.id
                for item in replies
            )
            return replace(
                result,
                top_reply_like_count=top_reply_likes,
                root_author_has_replied=author_has_replied,
            )

        return list(await asyncio.gather(*(enrich(result) for result in results[:5])))

    def _apply_reply_target_mode(
        self,
        results: list[XSearchResult],
        mode: str,
    ) -> list[XSearchResult]:
        niche_terms = {
            token.casefold()
            for token in re.findall(
                r"[A-Za-z0-9+#]{3,}",
                f"{self.settings.creator_niche} {self.settings.target_audience}",
            )
        }
        adjusted: list[XSearchResult] = []
        for result in results:
            text_terms = {
                token.casefold()
                for token in re.findall(r"[A-Za-z0-9+#]{3,}", result.text)
            }
            overlap = len(niche_terms & text_terms)
            affinity = min(100.0, overlap * 22.0)
            relationship = self.reply_learning.relationship_strength(result.username)
            if mode == "reach":
                score = (result.viral_score * 0.82) + (
                    result.thread_availability_score * 0.18
                )
            elif mode == "qualified":
                score = (
                    result.reply_opportunity_score * 0.58
                    + affinity * 0.27
                    + result.breakout_ratio * 15.0
                )
            elif mode == "relationship":
                score = (
                    result.reply_opportunity_score * 0.52
                    + relationship * 0.30
                    + result.thread_availability_score * 0.18
                )
            else:
                score = (
                    result.reply_opportunity_score * 0.75
                    + affinity * 0.15
                    + relationship * 0.10
                )
            top_reply_penalty = min(
                16.0,
                math.log10(max(result.top_reply_like_count, 1)) * 4.0,
            )
            if result.root_author_has_replied:
                score += 6.0
            score -= top_reply_penalty
            adjusted.append(
                replace(
                    result,
                    reply_opportunity_score=min(max(score, 0.0), 100.0),
                    audience_affinity_score=affinity,
                    relationship_score=relationship,
                )
            )
        return sorted(
            adjusted,
            key=lambda item: item.reply_opportunity_score,
            reverse=True,
        )

    async def _search_reply_target_pool(
        self,
        query: str,
        *,
        max_age_minutes: int,
    ) -> tuple[str, list[XSearchResult]]:
        freshness_minutes = _reply_target_max_age_minutes(
            max_age_minutes,
            default=self.settings.reply_target_max_age_minutes,
        )
        root_query = _reply_target_root_query(query)
        result_limit = min(
            REPLY_TARGET_RESULT_LIMIT,
            max(self.settings.x_search_limit, 8),
        )
        searches: list[tuple[str, list[XSearchResult]] | Exception] = []
        # Keep Top + Latest coverage, but do not lease two SearchTimeline
        # accounts at once. Cookie-only deployments commonly have just one
        # currently usable account after X rate-limits another session.
        for product in ("Top", "Latest"):
            try:
                searches.append(
                    await self.x_search.search_recent(
                        root_query,
                        since_minutes=freshness_minutes,
                        limit=result_limit,
                        product=product,
                    )
                )
            except Exception as exc:
                searches.append(exc)
        successful = [
            item
            for item in searches
            if isinstance(item, tuple) and len(item) == 2
        ]
        if not successful:
            first_error = next(
                (item for item in searches if isinstance(item, Exception)),
                RuntimeError("X search returned no response."),
            )
            raise first_error
        search_query = str(successful[0][0])
        pools = [list(item[1]) for item in successful]
        return search_query, _merge_reply_target_search_products(pools)

    def _rank_reply_target_pool(
        self,
        recent_results: list[XSearchResult],
        *,
        relaxed: bool = False,
        max_age_minutes: int = 360,
        min_view_count: int | None = None,
        allow_view_only_signal: bool = False,
    ) -> list[XSearchResult]:
        freshness_minutes = _reply_target_max_age_minutes(
            max_age_minutes,
            default=self.settings.reply_target_max_age_minutes,
        )
        ranked = rank_fast_growing_posts(
            recent_results,
            max_items=5,
            max_age_minutes=freshness_minutes,
            min_engagement_score=0 if relaxed else MIN_REPLY_TARGET_ENGAGEMENT_SCORE,
            min_velocity_score=0 if relaxed else MIN_REPLY_TARGET_VELOCITY_SCORE,
            min_view_velocity_score=(
                0 if relaxed else MIN_REPLY_TARGET_VIEW_VELOCITY_SCORE
            ),
            # Standard scans use the configured floor. The final volume
            # fallback may explicitly lower it after exhausting the same
            # fetched pool with normal and relaxed momentum thresholds.
            min_view_count=(
                self.settings.reply_target_min_views
                if min_view_count is None
                else max(0, min_view_count)
            ),
            # This is a capped reach bonus, not a hard gate. A smaller account
            # with clear post-level breakout should outrank a large but idle one.
            min_author_followers=self.settings.reply_target_min_author_followers,
            allow_view_only_signal=allow_view_only_signal,
        )
        unseen = [
            result
            for result in ranked
            if not self.approvals.has_active_target(result.url)
        ]
        return unseen

    async def _auto_reply_target_queries(
        self,
        languages: list[str] | tuple[str, ...] | str | None = None,
        *,
        mode: str = "balanced",
    ) -> list[str]:
        # /replytargets is deliberately independent from CREATOR_NICHE. Its job
        # is reach discovery across today's largest conversations; niche-led
        # discovery remains the responsibility of /tweettrend3.
        selected_languages = parse_reply_target_languages(
            languages,
            default=self.settings.reply_target_languages,
        )
        try:
            trends = await asyncio.wait_for(
                self.x_search.trends("trending", limit=4),
                timeout=REPLY_TARGET_TREND_TIMEOUT_SECONDS,
            )
        except Exception:
            trends = []
        trend_names = _dedupe_queries([trend.name for trend in trends if trend.name])[:4]
        queries = [
            _query_for_languages(trend_name, selected_languages)
            for trend_name in trend_names
        ]
        if mode in {"qualified", "balanced"}:
            niche_query = " OR ".join(
                f'"{part.strip()}"'
                for part in re.split(r"[,;]", self.settings.creator_niche)
                if part.strip()
            )
            if niche_query:
                queries.insert(
                    0,
                    _query_for_languages(f"({niche_query})", selected_languages),
                )
        if "ja" in selected_languages:
            # Keep one Japanese high-value lane inside the six-query execution budget
            # even when personalized trends consume the other discovery slots.
            queries.append(query_for_language(JAPANESE_HIGH_VALUE_REPLY_QUERY, "ja"))
        lanes: dict[str, list[str]] = {}
        for language in selected_languages:
            fallback = AUTO_REPLY_TARGET_FALLBACK_QUERIES_BY_LANGUAGE.get(
                language,
                ("AI", "news", "sports", "technology"),
            )
            lanes[language] = _dedupe_queries(
                [query_for_language(topic, language) for topic in fallback]
            )

        # Search each trend once across every configured language, then spend the
        # remaining budget round-robin on localized broad discovery.
        lane_index = 0
        while len(queries) < 12:
            added = False
            for language in selected_languages:
                lane = lanes.get(language, [])
                if lane_index < len(lane):
                    queries.append(lane[lane_index])
                    added = True
            if not added:
                break
            lane_index += 1
        return _dedupe_queries(queries)[:12]

    async def _notify_x_account_errors(
        self,
        message,
        accounts: list[dict] | None = None,
    ) -> None:
        try:
            account_rows = accounts if accounts is not None else await self.x_search.accounts_info()
        except Exception:
            return

        notifications = _x_account_error_notifications(
            account_rows,
            self._x_account_error_notices,
        )
        for notification in notifications:
            try:
                await message.reply_text(notification)
            except Exception:
                return

    async def _send_optional_image(
        self,
        message,
        generated: GeneratedContent,
        label: str,
    ) -> None:
        if not self.settings.generate_images:
            return

        status = await message.reply_text(f"Generating the {label} image...")
        try:
            image = await self.ai.generate_image(generated.image_prompt)
        except Exception as exc:
            await status.edit_text(
                f"Could not generate image: {_friendly_error(exc)}\n\n"
                f"Image prompt:\n{generated.image_prompt}"
            )
            return
        await status.delete()
        await message.reply_photo(
            photo=_as_photo(image),
            caption=_caption_for_generated(generated, self.settings.telegram_caption_limit),
        )

    async def _send_trend_variant(
        self,
        message,
        variant: TrendPostVariant,
        index: int,
        label: str = "Option",
        approval: AutomationApproval | None = None,
        approval_reason: str = "",
    ) -> None:
        copy_text = _format_trend_variant_copy(variant)
        if approval is None:
            await message.reply_text(copy_text)
        else:
            await self._send_approval(approval, reason=approval_reason)
            # Approval cards expose an on-demand visual button. Avoid spending a
            # Gemini image job before the user chooses this post.
            return

        if not self.settings.generate_images:
            return

        try:
            image = await self.ai.generate_image(variant.image_prompt)
        except Exception as exc:
            await message.reply_text(
                f"Could not generate image: {_friendly_error(exc)}\n\n"
                f"Image prompt:\n{variant.image_prompt}"
            )
            return

        await message.reply_photo(photo=_as_photo(image))


def _as_photo(image: bytes) -> BytesIO:
    buffer = BytesIO(image)
    buffer.name = "generated.png"
    buffer.seek(0)
    return buffer


def _menu_keyboard(menu_name: str = "main") -> ReplyKeyboardMarkup:
    layout = MENU_LAYOUTS.get(menu_name, MENU_LAYOUTS["main"])
    return ReplyKeyboardMarkup(
        [list(row) for row in layout],
        resize_keyboard=True,
        is_persistent=True,
        one_time_keyboard=False,
        input_field_placeholder="Choose a feature from the menu...",
    )


async def _set_bot_commands(app: Application) -> None:
    await app.bot.set_my_commands(BOT_COMMANDS)


def _caption_for_generated(generated: GeneratedContent, limit: int) -> str:
    caption = f"Topic: {generated.topic}\n\n{generated.text}"
    if len(caption) <= limit:
        return caption
    return caption[: limit - 3].rstrip() + "..."


def _format_trend_variant_copy(variant: TrendPostVariant) -> str:
    text = variant.text.strip()
    hashtags = [
        hashtag.strip()
        for hashtag in variant.hashtags
        if hashtag.strip() and hashtag.strip().lower() not in text.lower()
    ]
    if hashtags:
        text = f"{text}\n\n{' '.join(hashtags)}"
    return text.strip()


def _format_reply_target_reply(draft: ReplyTargetDraft) -> str:
    return draft.reply.strip()


def _format_reply_target_link(draft: ReplyTargetDraft) -> str:
    return draft.url.strip()


def _result_for_url(
    results: list[XSearchResult],
    target_url: str,
) -> XSearchResult | None:
    target_id = extract_tweet_id(target_url)
    return next(
        (
            result
            for result in results
            if result.url == target_url or (target_id is not None and result.id == target_id)
        ),
        None,
    )


def _select_reply_draft_batch(
    ready: list[XSearchResult],
    watching: list[XSearchResult],
    *,
    capacity: int,
    max_items: int = REPLY_TARGET_CONTEXT_ITEMS,
    minimum_items: int = MIN_REPLY_TARGET_BATCH_ITEMS,
) -> tuple[list[XSearchResult], int]:
    """Build a quality-ranked batch and use top watching candidates only as fillers."""
    available_slots = min(max(0, capacity), max(0, max_items))
    if available_slots < minimum_items:
        return [], 0

    selected: list[XSearchResult] = []
    seen: set[str] = set()
    for result in ready[:available_slots]:
        key = result.url or str(result.id)
        if key in seen:
            continue
        seen.add(key)
        selected.append(result)

    promoted_count = 0
    if len(selected) < minimum_items:
        for result in watching:
            key = result.url or str(result.id)
            if key in seen:
                continue
            seen.add(key)
            selected.append(result)
            promoted_count += 1
            if len(selected) >= minimum_items:
                break

    if len(selected) < minimum_items:
        return [], 0
    return selected[:available_slots], promoted_count


def _reply_tracking_metadata(
    result: XSearchResult | None,
    strategy: str,
    *,
    source_type: str = "replytargets",
) -> dict[str, object]:
    metadata: dict[str, object] = {
        "reply_strategy": strategy,
        "source_type": source_type,
    }
    if result is None:
        return metadata
    metadata.update(
        {
            "language": result.language,
            "root_text": result.text,
            "root_author": result.username,
            "root_author_id": result.author_id,
            "root_views": result.view_count,
            "root_replies": result.reply_count,
            "reply_opportunity_score": result.reply_opportunity_score,
            "viral_score": result.viral_score,
            "top_reply_like_count": result.top_reply_like_count,
            "root_author_has_replied": result.root_author_has_replied,
            "has_video": result.has_video,
            "video_context_quality": result.video_context_quality,
            "visual_frame_count": len(result.visual_frame_names or []),
        }
    )
    return metadata


def _mobile_x_intent_url(approval: AutomationApproval) -> str:
    params = {"text": approval.text}
    if approval.kind == "reply":
        tweet_id = extract_tweet_id(approval.target_url)
        if tweet_id is not None:
            params["in_reply_to"] = str(tweet_id)
    return f"https://x.com/intent/tweet?{urlencode(params)}"


# Vietnamese text grows substantially after URL encoding. Leave headroom below
# Telegram's practical button URL limit instead of sending an invalid keyboard.
MOBILE_X_INTENT_SAFE_LENGTH = 1800


def _mobile_x_open_url(approval: AutomationApproval) -> str:
    intent_url = _mobile_x_intent_url(approval)
    if len(intent_url) <= MOBILE_X_INTENT_SAFE_LENGTH:
        return intent_url
    if approval.kind == "reply" and approval.target_url:
        return approval.target_url
    return "https://x.com/compose/post"


def _mobile_approval_note(approval: AutomationApproval) -> str:
    if _mobile_x_open_url(approval) == _mobile_x_intent_url(approval):
        return "Approved. Tap Open X on phone to open the pre-filled draft."
    return (
        "Approved. The draft is too long for a safe X pre-fill link; copy it above, "
        "then tap Open X on phone and paste it."
    )


def _approval_message_text(
    approval: AutomationApproval,
    *,
    reason: str = "",
) -> str:
    if approval.kind == "reply":
        metadata = approval.metadata or {}
        if metadata.get("relationship_followup"):
            author = str(metadata.get("root_author") or "author").lstrip("@")
            response_text = _truncate_text(
                str(metadata.get("author_response_text") or "").strip(),
                700,
            )
            response_url = str(
                metadata.get("author_response_url") or approval.target_url
            ).strip()
            response_block = (
                f"@{author} replied:\n{response_text}"
                if response_text
                else f"@{author} replied to your post."
            )
            return (
                f"{response_block}\n"
                f"{response_url}\n\n"
                f"Suggested follow-up:\n{approval.text}"
            ).strip()
        source_summary = _truncate_text(
            str(metadata.get("source_summary_vi") or "").strip(),
            700,
        )
        reply_translation = _truncate_text(
            str(metadata.get("reply_translation_vi") or "").strip(),
            700,
        )
        blocks = [approval.target_url]
        if source_summary:
            blocks.append(f"Tóm tắt bài viết:\n{source_summary}")
        if reply_translation:
            blocks.append(f"Bản dịch reply:\n{reply_translation}")
        blocks.append(f"Reply gốc:\n{approval.text}")
        return "\n\n".join(item for item in blocks if item).strip()

    context_lines = []
    if approval.target_label:
        context_lines.append(f"Topic: {approval.target_label}")
    if reason:
        context_lines.append(f"Source: {reason}")
    context_text = "\n".join(context_lines)
    return (
        f"{context_text}\n\n{approval.text}\n\n"
        "Approve on mobile to continue."
    ).strip()


def _approval_keyboard(
    approval: AutomationApproval,
    *,
    include_decisions: bool = True,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    relationship_followup = bool(approval.metadata.get("relationship_followup"))
    if include_decisions:
        rows.append(
            [
                InlineKeyboardButton(
                    (
                        "Continue conversation"
                        if relationship_followup
                        else "Approve on mobile"
                    ),
                    callback_data=(
                        f"automation:continue:{approval.id}"
                        if relationship_followup
                        else f"automation:mobile:{approval.id}"
                    ),
                )
            ]
        )
        if approval.kind == "reply":
            rows.append(
                [
                    InlineKeyboardButton(
                        "Alternative",
                        callback_data=f"automation:alternative:{approval.id}",
                    ),
                    InlineKeyboardButton(
                        "Shorter",
                        callback_data=f"automation:shorter:{approval.id}",
                    ),
                ]
            )
        elif approval.kind == "post" and approval.metadata.get("image_prompt"):
            rows.append(
                [
                    InlineKeyboardButton(
                        "Generate visual",
                        callback_data=f"automation:visual:{approval.id}",
                    )
                ]
            )
    else:
        rows.append(
            [
                InlineKeyboardButton(
                    "Open X on phone",
                    url=_mobile_x_open_url(approval),
                )
            ]
        )
    final_row: list[InlineKeyboardButton] = []
    if len(approval.text) <= 256:
        final_row.append(
            InlineKeyboardButton(
                "Copy draft",
                copy_text=CopyTextButton(approval.text),
            )
        )
    if include_decisions:
        final_row.append(
            InlineKeyboardButton(
                "Stop here" if relationship_followup else "Reject",
                callback_data=(
                    f"automation:stop:{approval.id}"
                    if relationship_followup
                    else f"automation:skip:{approval.id}"
                ),
            )
        )
    if final_row:
        rows.append(final_row)
    return InlineKeyboardMarkup(rows)


def _trend_context_score(
    candidate: tuple[str, str, str, list[XSearchResult], str],
) -> tuple[int, int]:
    _topic, x_context, _source, results, _category = candidate
    return len(results), len(x_context)


def _dedupe_queries(queries: list[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for query in queries:
        clean_query = " ".join(str(query).split())
        if not clean_query:
            continue
        key = clean_query.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(clean_query)
    return deduped


def _query_for_languages(query: str, languages: list[str]) -> str:
    clean_query = " ".join(str(query or "").strip().split())
    if not clean_query:
        raise RuntimeError("Search query was empty.")
    if re.search(r"\blang:[\w-]+\b", clean_query, flags=re.IGNORECASE):
        return clean_query
    language_terms = " OR ".join(f"lang:{language}" for language in languages)
    return f"{clean_query} ({language_terms})"


def _reply_video_search_queries(topic: str = "") -> list[tuple[str, str]]:
    clean_topic = " ".join(str(topic or "").strip().split())

    def with_topic(filters: str) -> str:
        return f"({clean_topic}) {filters}" if clean_topic else filters

    global_languages = " OR ".join(
        f"lang:{language}" for language in REPLY_VIDEO_GLOBAL_LANGUAGES
    )
    return [
        (
            "global",
            with_topic(
                f"filter:videos min_faves:200 ({global_languages})"
            ),
        ),
        ("English", with_topic("filter:videos min_faves:300 lang:en")),
        ("Japanese", with_topic("filter:videos min_faves:150 lang:ja")),
        ("Vietnamese", with_topic("filter:videos min_faves:80 lang:vi")),
    ]


VIDEO_CONTEXT_GARBAGE = {
    "video",
    "image",
    "embedded video",
    "attached video",
    "watch",
    "watch this",
    "must watch",
    "wow",
    "lol",
    "lmao",
    "omg",
    "crazy",
    "insane",
    "viral",
    "fyp",
    "xem di",
    "xem nay",
    "hay qua",
    "dinh",
    "動画",
    "見て",
    "やばい",
    "すごい",
}


def _video_context_quality(result: XSearchResult) -> str:
    reliable_caption = _is_reliable_video_context_text(result.text)
    reliable_description = any(
        _is_reliable_video_context_text(description, media_description=True)
        for description in (result.media_descriptions or [])
    )
    if reliable_description:
        return "grounded_text"
    if reliable_caption:
        return "caption_only"
    return "visual_required"


def _is_reliable_video_context_text(
    value: str,
    *,
    media_description: bool = False,
) -> bool:
    text = re.sub(r"https?://\S+", " ", str(value or ""), flags=re.IGNORECASE)
    text = text.replace("#", " ").replace("@", " ")
    normalized = " ".join(text.casefold().split()).strip(" .,!?:;_-|/\\")
    if not normalized:
        return False
    ascii_folded = (
        normalized.replace("đ", "d")
        .replace("á", "a").replace("à", "a").replace("ả", "a")
        .replace("ã", "a").replace("ạ", "a")
        .replace("í", "i").replace("ì", "i")
        .replace("ó", "o").replace("ò", "o")
        .replace("ú", "u").replace("ù", "u")
        .replace("ý", "y").replace("ỳ", "y")
    )
    if normalized in VIDEO_CONTEXT_GARBAGE or ascii_folded in VIDEO_CONTEXT_GARBAGE:
        return False
    boilerplate = (
        "video attached",
        "video is attached",
        "video included",
        "media unavailable",
        "no description",
        "click to watch",
        "tap to watch",
    )
    if media_description and any(phrase in ascii_folded for phrase in boilerplate):
        return False
    alphanumeric = [char for char in normalized if char.isalnum()]
    if len(alphanumeric) < (8 if media_description else 5):
        return False
    if len(set(alphanumeric)) < 4:
        return False
    words = re.findall(r"[^\W_]+", normalized, flags=re.UNICODE)
    contains_cjk = bool(re.search(r"[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]", normalized))
    if not contains_cjk and len(words) < 2:
        return False
    return True


def _select_reply_video_mix(
    results: list[XSearchResult],
    *,
    max_items: int = REPLY_VIDEO_CONTEXT_ITEMS,
) -> list[XSearchResult]:
    """Aim for 2 global + 1 Vietnamese while preserving score order within lanes."""
    vietnamese = [item for item in results if item.language.casefold() == "vi"]
    global_items = [item for item in results if item.language.casefold() != "vi"]
    selected = global_items[:2]
    if vietnamese and len(selected) < max_items:
        selected.append(vietnamese[0])
    seen = {item.url or str(item.id) for item in selected}
    for item in results:
        key = item.url or str(item.id)
        if key in seen:
            continue
        selected.append(item)
        seen.add(key)
        if len(selected) >= max_items:
            break
    return selected[:max_items]


def _reply_target_root_query(query: str) -> str:
    clean_query = " ".join(str(query or "").strip().split())
    clean_query = re.sub(
        r"(?<![-\w])is:(?:reply|retweet)\b",
        "",
        clean_query,
        flags=re.IGNORECASE,
    )
    clean_query = " ".join(clean_query.split())
    for operator in ("-is:reply", "-is:retweet"):
        if operator not in clean_query.lower():
            clean_query = f"{clean_query} {operator}".strip()
    return clean_query


def _merge_reply_target_search_products(
    pools: list[list[XSearchResult]],
) -> list[XSearchResult]:
    merged: dict[str, XSearchResult] = {}
    order: list[str] = []
    for pool in pools:
        for result in pool:
            identity = result.url or str(result.id)
            existing = merged.get(identity)
            if existing is None:
                merged[identity] = result
                order.append(identity)
                continue
            if _visible_metric_total(result) > _visible_metric_total(existing):
                merged[identity] = result
    return [merged[identity] for identity in order]


def _visible_metric_total(result: XSearchResult) -> int:
    return (
        (result.view_count or 0)
        + result.like_count
        + result.retweet_count
        + result.quote_count
        + result.reply_count
    )


def _combine_reply_target_results(
    searched: list[tuple[str, str, list[XSearchResult]]],
) -> tuple[list[XSearchResult], dict[str, str]]:
    combined: list[XSearchResult] = []
    seen: set[str] = set()
    search_query_by_url: dict[str, str] = {}
    for _candidate, search_query, results in searched:
        for result in results:
            key = result.url or str(result.id)
            search_query_by_url.setdefault(result.url, search_query)
            if key in seen:
                continue
            seen.add(key)
            combined.append(result)
    return combined, search_query_by_url


def _trend_topic_key(topic: str) -> str:
    return " ".join(
        part for part in "".join(char.lower() if char.isalnum() else " " for char in topic).split()
        if len(part) > 2
    )


def _reply_target_interval_minutes(value: Any, *, default: int) -> int:
    try:
        minutes = int(value)
    except (TypeError, ValueError):
        minutes = default
    return min(1440, max(5, minutes))


def _reply_target_max_age_minutes(value: Any, *, default: int) -> int:
    try:
        minutes = int(value)
    except (TypeError, ValueError):
        minutes = default
    return min(1440, max(30, minutes))


def _expand_reply_target_query(query: str, languages: list[str]) -> list[str]:
    if re.search(r"\blang:[\w-]+\b", query, flags=re.IGNORECASE):
        return [query]
    return [query_for_language(query, language) for language in languages]


def _no_reply_targets_message(
    search_query: str,
    auto: bool,
    max_age_minutes: int = 360,
    diagnostic: str = "",
) -> str:
    freshness_minutes = _reply_target_max_age_minutes(
        max_age_minutes,
        default=360,
    )
    intro = (
        "No reply-ready posts found in the last "
        f"{freshness_minutes} minutes after trying several topics."
        if auto
        else (
            "No strong reply targets found in the last "
            f"{freshness_minutes} minutes "
            f"for: {search_query}"
        )
    )
    detail = " ".join(str(diagnostic or "").strip().split())
    diagnostic_text = f"\n\nScan diagnostics: {detail}" if detail else ""
    return (
        f"{intro}{diagnostic_text}\n\n"
        "The bot compared the configured languages and relaxed momentum thresholds "
        "without accepting older posts. Check X cookies/account limits, try again "
        "later, or use a specific topic such as `/replytargets crypto`."
    )


async def _send_text_chunks(message, text: str, limit: int = 3900) -> None:
    chunks = [text[i : i + limit] for i in range(0, len(text), limit)] or [""]
    await message.edit_text(chunks[0])
    for chunk in chunks[1:]:
        await message.reply_text(chunk)


async def _delete_message_safely(message) -> None:
    try:
        await message.delete()
    except Exception:
        pass


def _command_payload(message, context: ContextTypes.DEFAULT_TYPE) -> str:
    raw_text = message.text or message.caption or ""
    if raw_text:
        clean = raw_text.strip()
        if not clean.startswith("/"):
            return clean
        parts = clean.split(maxsplit=1)
        return parts[1].strip() if len(parts) > 1 else ""
    return " ".join(getattr(context, "args", None) or []).strip()


def _pending_input_key(update: Update) -> tuple[int, int] | None:
    chat = update.effective_chat
    user = update.effective_user
    if chat is None or user is None:
        return None
    return int(chat.id), int(user.id)


def _extract_media_url(raw_args: str) -> str:
    match = re.search(r"https?://[^\s<>\"']+", raw_args, flags=re.IGNORECASE)
    if match is None:
        return ""
    return match.group(0).rstrip(".,;:!?)]}")


def _truncate_text(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    if limit <= 3:
        return text[:limit]
    return text[: max(0, limit - 3)].rstrip() + "..."


def _format_file_size(size_bytes: int) -> str:
    size_mb = size_bytes / (1024 * 1024)
    if size_mb >= 1:
        return f"{size_mb:.1f} MB"
    return f"{max(1, round(size_bytes / 1024))} KB"


def _parse_retweet_args(raw_args: str) -> tuple[str, str]:
    clean = raw_args.strip()
    if not clean:
        return "", ""

    link, separator, visual_note = clean.partition("|")
    if separator:
        return link.strip(), visual_note.strip()

    parts = clean.split(maxsplit=1)
    if len(parts) == 1:
        return parts[0].strip(), ""
    return parts[0].strip(), parts[1].strip()


def _parse_tweettrend3_args(args: list[str]) -> tuple[str, str]:
    category = "auto"
    output_language = "Vietnamese"
    for arg in args:
        clean = arg.strip()
        if not clean:
            continue
        normalized = clean.lower()
        language = TWEETTREND_LANGUAGE_ALIASES.get(normalized)
        if language is not None:
            continue
        if category == "auto":
            category = normalized
    return category, output_language


def _looks_like_x_cookie(cookie: str) -> bool:
    parts = {
        item.split("=", 1)[0].strip().lower()
        for item in cookie.split(";")
        if "=" in item
    }
    return {"auth_token", "ct0"}.issubset(parts)


def _parse_importcookie_args(raw_args: str, default_account: str) -> tuple[str, str]:
    first, separator, rest = raw_args.partition(" ")
    if "=" in first or not separator:
        return default_account, raw_args.strip()
    return first.strip(), rest.strip()


def _parse_persona_args(raw_args: str) -> dict[str, str]:
    updates: dict[str, str] = {}
    aliases = {
        "niche": "niche",
        "voice": "voice",
        "audience": "audience",
        "target": "audience",
        "target_audience": "audience",
    }
    for part in raw_args.split(";"):
        key, separator, value = part.strip().partition("=")
        if not separator:
            raise RuntimeError(
                "Usage: /persona niche=...; voice=...; audience=..."
            )
        normalized = aliases.get(key.strip().lower())
        if normalized is None:
            raise RuntimeError(
                "Persona keys must be niche, voice, or audience."
            )
        clean_value = value.strip()
        if not clean_value:
            raise RuntimeError(f"Persona value for {key.strip()} cannot be empty.")
        updates[normalized] = clean_value
    return updates


def _format_persona(settings: Settings) -> str:
    return (
        "Creator persona:\n"
        f"- Niche: {settings.creator_niche}\n"
        f"- Voice: {settings.creator_voice}\n"
        f"- Audience: {settings.target_audience}\n\n"
        "Update with:\n"
        "/persona niche=...; voice=...; audience=..."
    )


def _updated_reply_target_languages(
    current_value: str,
    action: str,
    requested_value: str,
) -> list[str]:
    aliases = {
        "jp": "ja",
        "kr": "ko",
        "vn": "vi",
        "cn": "zh-cn",
        "zh": "zh-cn",
    }
    requested: list[str] = []
    unsupported: list[str] = []
    for raw_code in re.split(r"[\s,;]+", requested_value):
        code = raw_code.strip().lower()
        if not code:
            continue
        code = aliases.get(code, code)
        if code not in SUPPORTED_REPLY_TARGET_LANGUAGES:
            unsupported.append(raw_code.strip())
            continue
        if code not in requested:
            requested.append(code)
    if unsupported:
        raise RuntimeError(
            "Unsupported X language code(s): " + ", ".join(unsupported) + ". "
            "Examples: en, ja, ko, es, pt, id, vi, fr, de, th, zh-cn."
        )
    if not requested:
        raise RuntimeError("Provide at least one supported X language code.")

    current = parse_reply_target_languages(
        current_value,
        max_languages=MAX_REPLY_TARGET_LANGUAGES,
    )
    if action == "set":
        updated = requested
    elif action == "add":
        updated = current + [code for code in requested if code not in current]
    elif action == "remove":
        updated = [code for code in current if code not in requested]
    else:
        raise RuntimeError("Language action must be add, remove, or set.")

    if not updated:
        raise RuntimeError("At least one reply-target language must remain enabled.")
    if len(updated) > MAX_REPLY_TARGET_LANGUAGES:
        raise RuntimeError(
            f"Use at most {MAX_REPLY_TARGET_LANGUAGES} reply-target languages so each scan "
            "keeps enough search coverage."
        )
    return updated


def _reply_approvals_created_today(
    approvals: list[AutomationApproval],
    *,
    timezone_name: str = "Asia/Ho_Chi_Minh",
) -> int:
    try:
        timezone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        timezone = UTC
    today = datetime.now(timezone).date()
    ignored_statuses = {"rejected", "expired", "failed", "not_found"}
    return sum(
        1
        for approval in approvals
        if approval.kind == "reply"
        and approval.created_at.astimezone(timezone).date() == today
        and approval.status not in ignored_statuses
    )


def _format_x_accounts(accounts: list[dict]) -> str:
    if not accounts:
        return "No X accounts imported yet. Use /importcookie first."

    lines = ["X accounts:"]
    for account in accounts:
        active = "active" if account.get("active") else "inactive"
        logged_in = "cookie" if account.get("logged_in") else "cookie-only"
        total_req = account.get("total_req", 0)
        last_used = account.get("last_used") or "never"
        error = account.get("error_msg")
        line = (
            f"- {account.get('username')} - {active}, {logged_in}, "
            f"requests={total_req}, last_used={last_used}"
        )
        if error and error != "None":
            line += f", error={error}"
        lines.append(line)
    return "\n".join(lines)


def _x_account_error_notifications(
    accounts: list[dict],
    seen_errors: dict[str, str],
) -> list[str]:
    notifications: list[str] = []
    active_names: set[str] = set()
    for account in accounts:
        name = str(account.get("username") or account.get("name") or "").strip()
        if not name:
            continue
        active_names.add(name)
        error = _account_error_text(account)
        if not error:
            seen_errors.pop(name, None)
            continue
        if seen_errors.get(name) == error:
            continue
        seen_errors[name] = error
        notifications.append(_format_x_account_error_notification(name, error))

    for removed_name in set(seen_errors) - active_names:
        seen_errors.pop(removed_name, None)
    return notifications


def _account_error_text(account: dict) -> str:
    raw = account.get("error_msg")
    if raw is None or str(raw).strip() in {"", "None", "none", "null"}:
        return ""
    return " ".join(str(raw).split())


def _format_x_account_error_notification(account_name: str, error: str) -> str:
    return (
        "X account warning:\n"
        f"- Account: {account_name}\n"
        f"- Error: {error}\n\n"
        f"To remove it: /xremove {account_name}\n"
        f"To refresh it: /importcookie {account_name} auth_token=...; ct0=..."
    )


def _friendly_error(exc: Exception) -> str:
    text = _exception_detail(exc)
    if exc.__traceback__ is not None:
        LOGGER.error("Bot command failed", exc_info=(type(exc), exc, exc.__traceback__))
    else:
        LOGGER.error("Bot command failed: %s", text)
    if "X search is not configured" in text:
        return (
            "X search is not configured. Use `/importcookie auth_token=...; ct0=...` "
            "or `/importcookie account2 auth_token=...; ct0=...`."
        )
    if "twscrape is not installed" in text:
        return text
    if "No account available" in text:
        return (
            "No active X scraping account is available. Add another cookie with "
            "`/importcookie account2 auth_token=...; ct0=...`, or wait if all "
            "accounts are temporarily rate-limited."
        )
    if "X search failed" in text or "reply-target search lanes failed" in text:
        return (
            "Could not search X. No Gemini or Chrome bridge job was started. Check the "
            "imported X cookies, account rate limits, and network access. "
            f"Details: {text}"
        )
    if "browser session error" in text.lower():
        return _strip_exception_prefix(text)
    if "prompt instructions instead of" in text.lower():
        return (
            "The AI returned prompt/instruction text instead of final content, so I blocked it. "
            "Try the command again. If this came from an X link, use another post or paste only "
            "the real tweet text."
        )
    if "extension bridge timed out waiting for chrome" in text.lower():
        return _strip_exception_prefix(text).removesuffix(" <- TimeoutError")
    if "gemini image file input was not found" in text.lower():
        return (
            "Gemini's attachment control was not detected. Reload Chrome extension "
            "version 0.8.1 or newer, keep one signed-in Gemini tab open, and retry "
            "/replyvideo. The bridge endpoint itself is working because Chrome already "
            "claimed this job. "
            f"Details: {text}"
        )
    if (
        "readtimeout" in text.lower()
        or "timeouterror" in text.lower()
        or "deadline exceeded" in text.lower()
    ):
        return (
            "The request timed out. This usually means the Chrome extension bridge, "
            "Gemini is still working. Open Chrome, check the extension popup, "
            "and click Run next job if Auto Run is off. "
            f"Details: {text}"
        )
    if "connect" in text.lower() or "connection" in text.lower():
        return (
            "Could not connect to the local Chrome extension bridge. Make sure the bot "
            "is running, Chrome is open, and the extension Bridge URL/token match `.env`."
        )
    if "404" in text or "not found" in text.lower():
        return (
            "The extension bridge endpoint was not found. Reload the Chrome extension "
            "and confirm it points to the bot bridge URL."
        )
    return f"Could not process the request: {text}"


def _exception_detail(exc: Exception) -> str:
    details: list[str] = []
    current: BaseException | None = exc
    seen: set[int] = set()

    while current is not None and id(current) not in seen:
        seen.add(id(current))
        message = str(current).strip()
        name = type(current).__name__
        details.append(f"{name}: {message}" if message else name)
        current = current.__cause__ or current.__context__

    return " <- ".join(details) or type(exc).__name__


def _strip_exception_prefix(text: str) -> str:
    prefix = "RuntimeError: "
    if text.startswith(prefix):
        return text[len(prefix) :]
    return text
