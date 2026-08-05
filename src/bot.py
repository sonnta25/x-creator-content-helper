from __future__ import annotations

import asyncio
import hashlib
import logging
import math
import re
import time
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from difflib import SequenceMatcher
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
from src.models import ImageAttachment, ReplyTargetDraft, XSearchResult
from src.reply_target_metrics import ReplyTargetMetricStore
from src.reply_learning import (
    EXPERIMENT_VARIANTS,
    MIN_FEEDBACK_SAMPLES_TO_TUNE,
    MIN_FINAL_SAMPLES_TO_TUNE,
    STRATEGIES,
    ReplyLearningStore,
    match_posted_content,
)
from src.video_frame_service import VideoFrameExtractor
from src.x_search_service import (
    MIN_REPLY_TARGET_ENGAGEMENT_SCORE,
    MIN_REPLY_TARGET_VELOCITY_SCORE,
    MIN_REPLY_TARGET_VIEW_VELOCITY_SCORE,
    MAX_REPLY_TARGET_LANGUAGES,
    SUPPORTED_REPLY_TARGET_LANGUAGES,
    XSearchService,
    extract_tweet_id,
    parse_reply_target_languages,
    query_for_language,
    rank_fast_growing_posts,
    rank_viral_video_posts,
    summarize_reply_target_context,
    summarize_reply_video_context,
)
from src.revenue_ops import (
    MONETIZATION_RED,
    MONETIZATION_YELLOW,
    RevenueOpsStore,
    assess_monetization_safety,
    reply_farming_guardrails,
)


LOGGER = logging.getLogger(__name__)

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
BOT_RUNTIME_REVISION = "revenue-ops-v2"
REPLY_TARGET_TREND_TIMEOUT_SECONDS = 20
REPLY_TARGET_SEARCH_TIMEOUT_SECONDS = 30
REPLY_TARGET_REFRESH_TIMEOUT_SECONDS = 12
REPLY_TARGET_REFRESH_LIMIT = 6
GLOBAL_REPLY_DELIVERY_QUEUE_ID = "global-reply-delivery"
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
    "replycap": (
        "Send `show`, `daily 1-2000`, or `author 1-25`.",
        "show, daily 500, or author 5",
    ),
    "watchauthor": (
        "Send `list`, `add @name`, `remove @name`, `block @name`, "
        "`unblock @name`, or `auto on|off|status`.",
        "list, add @name, block @name, or auto status",
    ),
    "money": (
        "Send `status`, `report 90d`, `payout YYYY-MM-DD amount USD`, or "
        "`set premium|stripe|identity|2fa on|off`.",
        "status or payout details",
    ),
}

MENU_MAIN = "🏠 Main menu"
MENU_SESSION = "🚀 Start reply session"
MENU_INBOX = "💬 Conversation inbox"
MENU_PERFORMANCE = "📈 Performance"
MENU_SETTINGS = "⚙️ Settings"
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
MENU_REPLY_CAP = "🛡️ Reply limits"
MENU_REPLY_LANGS = "🌍 Reply languages"
MENU_REPLY_LEARN = "🧠 Performance learning"
MENU_REPLY_REPORT = "📈 Reply report"
MENU_SETUP_CHECK = "🩺 System check"
MENU_IMPORT_COOKIE = "🍪 Import X cookie"
MENU_X_LIST = "👥 Account list"
MENU_X_REMOVE = "🗑️ Remove account"
MENU_DOWNLOAD = "📥 Download media"
MENU_PERSONA = "🎭 Creator persona"
MENU_REPLY_GOAL = "🧭 Creator goal"
MENU_GOAL_SHOW = "📋 Goal status"
MENU_GOAL_QUALIFY = "🎯 Qualify"
MENU_GOAL_EARN = "💰 Earn"
MENU_GOAL_NETWORK = "🤝 Network"
MENU_WATCH_AUTHOR = "⭐ Author watchlist"
MENU_MONEY = "💵 Monetization"
MENU_RISK = "🛡️ Reply safety"
MENU_RISK_SHOW = "📋 Current mode"
MENU_RISK_STRICT = "🔒 Strict"
MENU_RISK_BALANCED = "⚖️ Balanced"
MENU_RISK_OPEN = "🔓 Open"
MENU_PACE = "⏱️ Adaptive pace"
MENU_PACE_SHOW = "📋 Pace status"
MENU_PACE_CONSERVATIVE = "🐢 Conservative"
MENU_PACE_ADAPTIVE = "⚙️ Adaptive"
MENU_PACE_HIGH = "⚡ High"
MENU_PACE_PAUSE = "⏸ Pause"
MENU_PACE_RESUME = "▶️ Resume"
MENU_EXPERIMENTS = "🧪 Experiments"
MENU_EXPERIMENTS_SHOW = "📋 Experiment status"
MENU_EXPERIMENTS_ON = "✅ Experiments on"
MENU_EXPERIMENTS_OFF = "🚫 Experiments off"
MENU_PROFILE_AUDIT = "👤 Profile audit"
MENU_WINS = "🏆 Winning insights"

MENU_LAYOUTS: dict[str, tuple[tuple[str, ...], ...]] = {
    "main": (
        (MENU_SESSION,),
        (MENU_INBOX, MENU_PERFORMANCE),
        (MENU_SETTINGS, MENU_HELP),
        (MENU_CANCEL,),
    ),
    "settings": (
        (MENU_REPLY, MENU_AUTOMATION),
        (MENU_INSIGHTS, MENU_X_ACCOUNTS),
        (MENU_VIDEO, MENU_CREATOR),
        (MENU_HELP, MENU_CANCEL),
        (MENU_MAIN,),
    ),
    "reply": (
        (MENU_REPLY_TARGETS, MENU_REPLY_VIDEO),
        (MENU_WRITE_REPLY,),
        (MENU_MAIN,),
    ),
    "automation": (
        (MENU_REPLY_SCHEDULE, MENU_VIDEO_SCHEDULE),
        (MENU_REPLY_BATCH, MENU_REPLY_CAP),
        (MENU_PACE,),
        (MENU_MAIN,),
    ),
    "insights": (
        (MENU_REPLY_LANGS, MENU_REPLY_LEARN),
        (MENU_REPLY_REPORT, MENU_SETUP_CHECK),
        (MENU_EXPERIMENTS, MENU_WINS),
        (MENU_MAIN,),
    ),
    "x_accounts": (
        (MENU_IMPORT_COOKIE, MENU_X_LIST),
        (MENU_X_REMOVE, MENU_MAIN),
    ),
    "video": ((MENU_DOWNLOAD, MENU_MAIN),),
    "creator": (
        (MENU_PERSONA, MENU_REPLY_GOAL),
        (MENU_WATCH_AUTHOR, MENU_PROFILE_AUDIT),
        (MENU_MONEY, MENU_RISK),
        (MENU_MAIN,),
    ),
    "risk": (
        (MENU_RISK_SHOW,),
        (MENU_RISK_STRICT, MENU_RISK_BALANCED),
        (MENU_RISK_OPEN,),
        (MENU_CREATOR, MENU_MAIN),
    ),
    "goal": (
        (MENU_GOAL_SHOW,),
        (MENU_GOAL_QUALIFY, MENU_GOAL_EARN),
        (MENU_GOAL_NETWORK,),
        (MENU_CREATOR, MENU_MAIN),
    ),
    "pace": (
        (MENU_PACE_SHOW,),
        (MENU_PACE_CONSERVATIVE, MENU_PACE_ADAPTIVE),
        (MENU_PACE_HIGH,),
        (MENU_PACE_PAUSE, MENU_PACE_RESUME),
        (MENU_AUTOMATION, MENU_MAIN),
    ),
    "experiments": (
        (MENU_EXPERIMENTS_SHOW,),
        (MENU_EXPERIMENTS_ON, MENU_EXPERIMENTS_OFF),
        (MENU_INSIGHTS, MENU_MAIN),
    ),
}

MENU_ACTIONS: dict[str, tuple[str, str]] = {
    MENU_MAIN: ("menu", "main"),
    MENU_SESSION: ("command", "session"),
    MENU_INBOX: ("command", "inbox"),
    MENU_PERFORMANCE: ("command", "replyreport"),
    MENU_SETTINGS: ("menu", "settings"),
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
    MENU_REPLY_CAP: ("command", "replycap"),
    MENU_REPLY_LANGS: ("command", "replylangs"),
    MENU_REPLY_LEARN: ("command", "replylearn"),
    MENU_REPLY_REPORT: ("command", "replyreport"),
    MENU_SETUP_CHECK: ("command", "setupcheck"),
    MENU_IMPORT_COOKIE: ("command", "importcookie"),
    MENU_X_LIST: ("command", "xaccounts"),
    MENU_X_REMOVE: ("command", "xremove"),
    MENU_DOWNLOAD: ("command", "download"),
    MENU_PERSONA: ("command", "persona"),
    MENU_REPLY_GOAL: ("menu", "goal"),
    MENU_GOAL_SHOW: ("command_args", "replygoal show"),
    MENU_GOAL_QUALIFY: ("command_args", "replygoal qualify"),
    MENU_GOAL_EARN: ("command_args", "replygoal earn"),
    MENU_GOAL_NETWORK: ("command_args", "replygoal network"),
    MENU_WATCH_AUTHOR: ("command", "watchauthor"),
    MENU_MONEY: ("command", "money"),
    MENU_RISK: ("menu", "risk"),
    MENU_RISK_SHOW: ("command_args", "risk show"),
    MENU_RISK_STRICT: ("command_args", "risk strict"),
    MENU_RISK_BALANCED: ("command_args", "risk balanced"),
    MENU_RISK_OPEN: ("command_args", "risk open"),
    MENU_PACE: ("menu", "pace"),
    MENU_PACE_SHOW: ("command_args", "pace show"),
    MENU_PACE_CONSERVATIVE: ("command_args", "pace conservative"),
    MENU_PACE_ADAPTIVE: ("command_args", "pace adaptive"),
    MENU_PACE_HIGH: ("command_args", "pace high"),
    MENU_PACE_PAUSE: ("command_args", "pace pause"),
    MENU_PACE_RESUME: ("command_args", "pace resume"),
    MENU_EXPERIMENTS: ("menu", "experiments"),
    MENU_EXPERIMENTS_SHOW: ("command_args", "experiments status"),
    MENU_EXPERIMENTS_ON: ("command_args", "experiments on"),
    MENU_EXPERIMENTS_OFF: ("command_args", "experiments off"),
    MENU_PROFILE_AUDIT: ("command", "profileaudit"),
    MENU_WINS: ("command", "wins"),
}
MENU_BUTTON_PATTERN = re.compile(
    "^(?:" + "|".join(re.escape(label) for label in MENU_ACTIONS) + ")$"
)
@dataclass(frozen=True)
class _PendingCommandInput:
    command: str
    expires_at: float
    prompt_message_id: int | None = None


@dataclass(frozen=True)
class _ReplyApprovalCreationResult:
    created: int = 0
    ai_drafts: int = 0
    filtered_author_limit: int = 0
    filtered_language_limit: int = 0
    filtered_active: int = 0
    filtered_missing_source: int = 0
    filtered_closed: int = 0
    filtered_duplicate: int = 0
    blocked_reason: str = ""

    def __bool__(self) -> bool:
        return self.created > 0

    def diagnostic(self) -> str:
        parts = []
        if self.blocked_reason:
            parts.append(self.blocked_reason)
        parts.append(f"AI drafts: {self.ai_drafts}")
        filters = (
            ("already active", self.filtered_active),
            ("URL not in the selected pool", self.filtered_missing_source),
            ("stale after refresh", self.filtered_closed),
            ("similar to a recent reply", self.filtered_duplicate),
            ("per-author limit", self.filtered_author_limit),
            ("Japanese safety limit", self.filtered_language_limit),
        )
        parts.extend(f"{label}: {count}" for label, count in filters if count)
        parts.append(f"cards created: {self.created}")
        return "; ".join(parts)


BOT_COMMANDS = [
    BotCommand("start", "Open the grouped bot menu"),
    BotCommand("menu", "Open the grouped bot menu"),
    BotCommand("help", "Show help and the grouped menu"),
    BotCommand("download", "Download images, videos, carousels, or Reels"),
    BotCommand("session", "Run one guided viral-reply work session"),
    BotCommand("inbox", "Open author and verified-audience follow-ups"),
    BotCommand("replygoal", "Set qualify, earn, or network scoring goal"),
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
    BotCommand("replycap", "Set daily and per-author reply-card ceilings"),
    BotCommand("replylangs", "Show, add, or remove reply-target languages"),
    BotCommand("replylearn", "Show or control automatic reply learning"),
    BotCommand("replyreport", "Show tracked post and reply performance"),
    BotCommand("setupcheck", "Check X, tracking, scheduling, and learning health"),
    BotCommand("watchauthor", "Manage priority author watchlist"),
    BotCommand("money", "Track monetization eligibility and payouts"),
    BotCommand("risk", "Set reply and monetization safety mode"),
    BotCommand("pace", "Set adaptive reply-card pacing"),
    BotCommand("experiments", "Control and review reply format experiments"),
    BotCommand("profileaudit", "Audit profile conversion readiness"),
    BotCommand("wins", "Show winning reply insights"),
    BotCommand("cancel", "Cancel the command currently waiting for input"),
]


class ContentBot:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.ai = create_ai_service(settings)
        self.x_search = XSearchService(settings)
        self.media_downloader = MediaDownloadService(settings)
        self.video_frame_extractor = VideoFrameExtractor()
        self._download_semaphore = asyncio.Semaphore(1)
        self._x_account_error_notices: dict[str, str] = {}
        self.approvals = AutomationApprovalStore(settings.automation_approvals_path)
        self._approval_migration = self.approvals.migrate_reply_only(
            stale_mobile_hours=settings.stale_mobile_approval_hours
        )
        self.reply_target_metrics = ReplyTargetMetricStore(
            settings.reply_target_metrics_path
        )
        self.reply_learning = ReplyLearningStore(
            settings.reply_learning_path,
            enabled=settings.reply_learning_enabled,
        )
        self.reply_watch = ReplyWatchStore(settings.reply_watch_path)
        self.revenue_ops = RevenueOpsStore(settings.revenue_ops_path)
        self.approval_chat_id = settings.telegram_approval_chat_id
        self._application: Application | None = None
        self._automation_running: set[str] = set()
        self._automation_tasks: set[asyncio.Task[None]] = set()
        self._delayed_approval_tasks: dict[str, asyncio.Task[None]] = {}
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
            await self._restore_reply_delivery_queues()

        async def post_shutdown(app: Application) -> None:
            del app
            for task in tuple(self._automation_tasks):
                task.cancel()
            if self._automation_tasks:
                await asyncio.gather(*self._automation_tasks, return_exceptions=True)
            bridge = getattr(self.ai, "bridge", None)
            if bridge is not None:
                await bridge.stop()
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
        app.add_handler(CommandHandler("session", self.session))
        app.add_handler(CommandHandler("inbox", self.inbox))
        app.add_handler(CommandHandler("replygoal", self.replygoal))
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
        app.add_handler(CommandHandler("replycap", self.replycap))
        app.add_handler(CommandHandler("replylangs", self.replylangs))
        app.add_handler(CommandHandler("replylearn", self.replylearn))
        app.add_handler(CommandHandler("replyreport", self.replyreport))
        app.add_handler(CommandHandler("setupcheck", self.setupcheck))
        app.add_handler(CommandHandler("watchauthor", self.watchauthor))
        app.add_handler(CommandHandler("money", self.money))
        app.add_handler(CommandHandler("risk", self.risk))
        app.add_handler(CommandHandler("pace", self.pace))
        app.add_handler(CommandHandler("experiments", self.experiments))
        app.add_handler(CommandHandler("profileaudit", self.profileaudit))
        app.add_handler(CommandHandler("wins", self.wins))
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
            "Start one guided reply session, handle high-value conversations, or review "
            "performance. Revenue safety, pacing, watchlists, and automation controls "
            "are under Settings.\n\n"
            f"Current goal: {self.settings.creator_goal}\n"
            "Final posting on X always remains manual.",
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
                "settings": "⚙️ Settings",
                "reply": "🎯 Viral replies",
                "automation": "🤖 Automation",
                "insights": "📊 Tracking & insights",
                "x_accounts": "🔐 X accounts",
                "video": "🎬 Video tools",
                "creator": "⚙️ Creator settings",
                "risk": "🛡️ Reply safety",
                "goal": "🧭 Creator goal",
                "pace": "⏱️ Adaptive pace",
                "experiments": "🧪 Experiments",
            }
            await message.reply_text(
                titles[value] + "\nChoose a feature:",
                reply_markup=_menu_keyboard(value),
            )
            return
        if action_type == "help":
            await self.start(update, context)
            return
        if action_type == "command_args":
            command, *args = value.split()
            handler = getattr(self, command, None)
            if handler is None:
                await message.reply_text("This feature is currently unavailable.")
                return
            context.args = args
            await handler(update, context)
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

    async def replygoal(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        message = update.effective_message
        raw = " ".join(context.args).strip().lower()
        if not raw:
            await message.reply_text(
                "Choose the ranking goal used for new reply candidates.",
                reply_markup=_menu_keyboard("goal"),
            )
            return
        if raw in {"show", "current"}:
            await message.reply_text(
                f"Creator goal: {self.settings.creator_goal}\n\n"
                "qualify = impressions and eligibility\n"
                "earn = higher-value verified/Premium audience proxy\n"
                "network = author responses and repeat relationships",
                reply_markup=_menu_keyboard("goal"),
            )
            return
        if raw not in {"qualify", "earn", "network"}:
            await message.reply_text("Usage: /replygoal qualify|earn|network|show")
            return
        update_env_value("CREATOR_GOAL", raw)
        self.settings = replace(self.settings, creator_goal=raw)
        await message.reply_text(
            f"Creator goal set to {raw}. New target rankings use it immediately.",
            reply_markup=_menu_keyboard("goal"),
        )

    async def watchauthor(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        message = update.effective_message
        raw = " ".join(context.args).strip()
        if not raw:
            await self._request_command_input(update, "watchauthor")
            return
        action, _, value = raw.partition(" ")
        action = action.lower()
        if action in {"list", "show"}:
            rows = self.revenue_ops.watch_author_rows()
            blocked = self.revenue_ops.blocked_authors()
            if not rows:
                await message.reply_text(
                    "Author watchlist is empty. Automatic learning is "
                    f"{'ON' if self.revenue_ops.auto_watch_enabled else 'OFF'}. "
                    "Use /watchauthor add @username to pin one."
                )
                return
            lines = []
            for row in rows[:20]:
                username = str(row["username"])
                portfolio = self.reply_learning.author_portfolio(username)
                lines.append(
                    f"- @{username} [{str(row['kind']).upper()}]: "
                    f"{portfolio['replies']} replies, "
                    f"median {portfolio['median_views']:,} views, "
                    f"author response {portfolio['author_response_rate']:.0%}, "
                    f"relationship {portfolio['relationship_strength']:.0f}/100"
                )
            await message.reply_text(
                "Priority authors\n"
                f"Auto learning: {'ON' if self.revenue_ops.auto_watch_enabled else 'OFF'}; "
                f"blocked: {len(blocked)}\n"
                + "\n".join(lines)
            )
            return
        if action in {"add", "pin"} and value:
            username = self.revenue_ops.pin_watch_author(value)
            await message.reply_text(
                f"Pinned @{username}. The bot will never auto-demote this author. Auto "
                "discovery and /session will prioritize fresh opportunities from them."
            )
            return
        if action == "remove" and value:
            removed = self.revenue_ops.remove_watch_author(value)
            await message.reply_text(
                f"Removed @{value.strip().lstrip('@')}. It may be learned again; use "
                f"/watchauthor block @{value.strip().lstrip('@')} to prevent that."
                if removed
                else "That author was not on the watchlist."
            )
            return
        if action == "block" and value:
            username = self.revenue_ops.block_watch_author(value)
            await message.reply_text(
                f"Blocked @{username} from automatic watchlist promotion and removed it "
                "from the current priority list."
            )
            return
        if action == "unblock" and value:
            removed = self.revenue_ops.unblock_watch_author(value)
            await message.reply_text(
                f"Unblocked @{value.strip().lstrip('@')}."
                if removed
                else "That author was not blocked."
            )
            return
        if action == "auto":
            setting = value.strip().lower()
            if setting in {"on", "off"}:
                self.revenue_ops.set_auto_watch_enabled(setting == "on")
                changes = self.revenue_ops.refresh_auto_authors(
                    self.reply_learning.author_portfolios()
                )
                await message.reply_text(
                    f"Automatic author learning: {setting.upper()}. "
                    f"Promoted now: {len(changes['promoted'])}; "
                    f"demoted now: {len(changes['demoted'])}."
                )
                return
            if setting in {"", "show", "status"}:
                rows = self.revenue_ops.watch_author_rows()
                await message.reply_text(
                    f"Automatic author learning: "
                    f"{'ON' if self.revenue_ops.auto_watch_enabled else 'OFF'}\n"
                    f"Pinned/auto: {sum(row['kind'] == 'pinned' for row in rows)}/"
                    f"{sum(row['kind'] == 'auto' for row in rows)}\n"
                    f"Blocked: {len(self.revenue_ops.blocked_authors())}"
                )
                return
        await message.reply_text(
            "Usage: /watchauthor list|add @name|pin @name|remove @name|"
            "block @name|unblock @name|auto on|off|status"
        )

    async def money(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        message = update.effective_message
        raw = " ".join(context.args).strip()
        if not raw:
            await self._request_command_input(update, "money")
            return
        parts = raw.split()
        action = parts[0].lower()
        if action == "payout" and len(parts) in {3, 4}:
            try:
                amount = float(parts[2].replace(",", ""))
                row = self.revenue_ops.add_payout(
                    parts[1],
                    amount,
                    parts[3] if len(parts) == 4 else "USD",
                )
            except (RuntimeError, ValueError) as exc:
                await message.reply_text(str(exc))
                return
            await message.reply_text(
                f"Saved payout: {row['amount']:.2f} {row['currency']} on {row['date']}. "
                "The bot will compare payout periods with the reply mix; it will not "
                "pretend to attribute exact revenue to one reply."
            )
            return
        if action == "set" and len(parts) == 3:
            try:
                self.revenue_ops.set_eligibility(parts[1], parts[2])
            except RuntimeError as exc:
                await message.reply_text(str(exc))
                return
            await message.reply_text(f"Monetization setting updated: {parts[1]} = {parts[2]}.")
            return
        if action not in {"status", "show", "report"}:
            await message.reply_text(
                "Usage: /money status|report [7d|30d|90d]|"
                "payout YYYY-MM-DD amount [USD]|set premium|stripe|identity|2fa on|off|"
                "set verified_followers <number>"
            )
            return
        days = 90
        if action == "report" and len(parts) > 1:
            try:
                days = _parse_report_days(parts[1], allowed=(7, 30, 90, 180, 365))
            except RuntimeError as exc:
                await message.reply_text(str(exc))
                return
        report = self.reply_learning.report(days)
        eligibility = self.revenue_ops.eligibility()
        payouts = self.revenue_ops.payouts(days)
        totals: dict[str, float] = {}
        for row in payouts:
            currency = str(row.get("currency") or "USD")
            totals[currency] = totals.get(currency, 0.0) + float(row.get("amount") or 0.0)
        payout_text = ", ".join(
            f"{amount:.2f} {currency}" for currency, amount in sorted(totals.items())
        ) or "none recorded"
        tracked_views = int(report.get("reply_view_sum_proxy") or 0)
        efficiency_text = ", ".join(
            f"{(amount * 1_000_000 / tracked_views):.2f} {currency}/1M tracked reply views"
            for currency, amount in sorted(totals.items())
            if tracked_views > 0
        ) or "not enough matched payout/view data"
        checklist = {
            "Premium": bool(eligibility.get("premium")),
            "Stripe": bool(eligibility.get("stripe")),
            "Identity": bool(eligibility.get("identity")),
            "2FA": bool(eligibility.get("two_factor")),
            "500 verified followers": int(eligibility.get("verified_followers") or 0) >= 500,
        }
        checklist_text = "\n".join(
            f"- {'OK' if passed else 'TODO'}: {label}"
            for label, passed in checklist.items()
        )
        await message.reply_text(
            f"Monetization dashboard - {days} days\n"
            f"Public reply views tracked: {report['reply_view_sum_proxy']:,} (proxy only)\n"
            f"5M organic-impression progress cannot be verified without X private analytics.\n"
            f"Verified followers entered: {int(eligibility.get('verified_followers') or 0):,}\n"
            f"Payouts: {payout_text}\n"
            f"Period efficiency proxy: {efficiency_text}\n"
            f"Replies over 20k/50k: {report['over_20k']}/{report['over_50k']}\n"
            f"Follower lift in tracked windows: {report['follower_window_lift']}\n\n"
            f"Eligibility checklist\n{checklist_text}\n\n"
            "Enter only the occasional payout/checklist update; no Analytics CSV is required."
        )

    async def risk(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        message = update.effective_message
        raw = " ".join(context.args).strip().lower()
        if not raw:
            await message.reply_text(
                "Choose a reply-safety mode. Balanced is recommended for normal use.",
                reply_markup=_menu_keyboard("risk"),
            )
            return
        if raw in {"show", "status"}:
            guardrails = reply_farming_guardrails(self.revenue_ops.risk_mode)
            delivery_gap = self._reply_delivery_base_gap_seconds()
            farming_limits = (
                (
                    "No extra language/category caps; card delivery spacing "
                    f"{delivery_gap}-{delivery_gap * 2}s"
                )
                if guardrails.global_hourly_cap is None
                else (
                    f"Global {guardrails.global_hourly_cap}/hour; Japanese "
                    f"{guardrails.japanese_daily_cap}/day and "
                    f"{guardrails.japanese_hourly_cap}/hour; approval spacing "
                    f"{delivery_gap}-{delivery_gap * 2}s"
                )
            )
            await message.reply_text(
                f"Revenue safety mode: {self.revenue_ops.risk_mode}\n"
                "strict = exclude red and yellow candidates\n"
                "balanced = exclude red, downrank yellow, and skip Japanese tragedy/war targets\n"
                "open = allow red only outside earn, with a visible warning\n"
                f"Anti-farming pace: {farming_limits}\n\n"
                "These are operator safety heuristics, not published X rate limits. "
                "Duplicate, generic, promotional, and off-topic drafts remain blocked in every mode.",
                reply_markup=_menu_keyboard("risk"),
            )
            return
        try:
            self.revenue_ops.set_risk_mode(raw)
        except RuntimeError as exc:
            await message.reply_text(str(exc))
            return
        await message.reply_text(
            f"Reply safety mode set to {raw}. Candidate filtering and approved-reply "
            "pace guardrails apply immediately.",
            reply_markup=_menu_keyboard("risk"),
        )

    async def pace(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        message = update.effective_message
        raw = " ".join(context.args).strip().lower()
        if not raw:
            await message.reply_text(
                "Choose an adaptive pace mode or pause/resume card generation.",
                reply_markup=_menu_keyboard("pace"),
            )
            return
        if raw in {"show", "status"}:
            hourly = self.revenue_ops.hourly_ceiling(self.settings.creator_daily_reply_cap)
            effective_hourly = self._adaptive_hourly_ceiling()
            await message.reply_text(
                f"Pace: {self.revenue_ops.pace_mode}; "
                f"{'PAUSED' if self.revenue_ops.pace_paused else 'running'}\n"
                f"Base hourly card ceiling: {hourly}; effective safety ceiling: "
                f"{effective_hourly}; available now after usage/feedback: "
                f"{self._hourly_reply_capacity()}\n"
                "This is a safety ceiling, not a posting quota. Final X submission stays manual.",
                reply_markup=_menu_keyboard("pace"),
            )
            return
        if raw == "pause":
            self.revenue_ops.set_pace_paused(True)
            await message.reply_text(
                "New reply-card generation paused. Tracking remains active.",
                reply_markup=_menu_keyboard("pace"),
            )
            return
        if raw == "resume":
            self.revenue_ops.set_pace_paused(False)
            self.revenue_ops.clear_health_errors()
            await message.reply_text(
                "Reply-card generation resumed and health backoff cleared.",
                reply_markup=_menu_keyboard("pace"),
            )
            return
        try:
            self.revenue_ops.set_pace_mode(raw)
        except RuntimeError as exc:
            await message.reply_text(str(exc))
            return
        await message.reply_text(
            f"Pace set to {raw}; effective hourly safety ceiling is "
            f"{self._adaptive_hourly_ceiling()} cards under /risk "
            f"{self.revenue_ops.risk_mode}.",
            reply_markup=_menu_keyboard("pace"),
        )

    async def experiments(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        message = update.effective_message
        raw = " ".join(context.args).strip().lower()
        if not raw:
            await message.reply_text(
                "Choose whether controlled reply-format experiments are enabled.",
                reply_markup=_menu_keyboard("experiments"),
            )
            return
        if raw == "on":
            self.reply_learning.set_experiment_enabled(True)
            await message.reply_text(
                "Controlled reply-format experiments enabled.",
                reply_markup=_menu_keyboard("experiments"),
            )
            return
        if raw == "off":
            self.reply_learning.set_experiment_enabled(False)
            await message.reply_text(
                "Experiments disabled; generation uses adaptive format.",
                reply_markup=_menu_keyboard("experiments"),
            )
            return
        if raw not in {"status", "show"}:
            await message.reply_text("Usage: /experiments status|on|off")
            return
        report = self.reply_learning.report(30)
        lines = _format_performance_dimension(report.get("by_experiment") or {}, limit=8)
        await message.reply_text(
            f"Reply experiments: {'ON' if self.reply_learning.experiment_enabled else 'OFF'}\n"
            "Variants rotate across different targets, never as duplicate replies to one post.\n"
            f"Active variants: {', '.join(EXPERIMENT_VARIANTS)}\n\n"
            f"30-day results:\n{lines}",
            reply_markup=_menu_keyboard("experiments"),
        )

    async def profileaudit(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        del context
        message = update.effective_message
        owner = self.settings.x_owner_username.strip().lstrip("@")
        if not owner:
            await message.reply_text("Set the account first with /replylearn username @name.")
            return
        status = await message.reply_text(f"Auditing @{owner} profile...")
        try:
            profile = await self.x_search.user_profile(owner)
            timeline: list[XSearchResult] = []
            timeline_note = ""
            try:
                timeline = await self.x_search.user_tweets_and_replies(owner, limit=40)
            except Exception as exc:
                timeline_note = f"Recent content mix unavailable: {_friendly_error(exc)}"
            recent_replies = sum(item.is_reply for item in timeline)
            recent_originals = sum(
                not item.is_reply and not item.is_retweet for item in timeline
            )
            content_total = recent_replies + recent_originals
            original_share = (
                recent_originals / content_total if content_total else 0.0
            )
            bio = str(profile.get("description") or "").strip()
            niche_terms = {
                token.casefold()
                for token in re.findall(r"[A-Za-z0-9+#]{3,}", self.settings.creator_niche)
            }
            bio_terms = {
                token.casefold() for token in re.findall(r"[A-Za-z0-9+#]{3,}", bio)
            }
            checks = [
                (bool(profile.get("profile_image")), "profile image"),
                (bool(profile.get("profile_banner")), "header image"),
                (len(bio) >= 40, "clear bio of at least 40 characters"),
                (bool(niche_terms & bio_terms), "bio mentions the creator niche/value"),
                (bool(profile.get("pinned_ids")), "pinned post"),
                (bool(profile.get("verified")), "verified/Premium marker visible"),
            ]
            if content_total:
                checks.append(
                    (
                        recent_originals >= 3 and original_share >= 0.15,
                        "recent timeline includes original posts, not replies only",
                    )
                )
            lines = "\n".join(
                f"- {'OK' if passed else 'FIX'}: {label}" for passed, label in checks
            )
            await status.edit_text(
                f"Profile conversion audit for @{owner}\n"
                f"Followers: {int(profile.get('followers') or 0):,}\n"
                f"Bio: {bio or '(empty)'}\n"
                + (
                    f"Recent original/reply mix: {recent_originals}/{recent_replies} "
                    f"({original_share:.0%} original)\n"
                    if content_total
                    else ""
                )
                + (f"{timeline_note}\n" if timeline_note else "")
                + f"\n{lines}\n\n"
                "The profile should answer in seconds: who this is for, what recurring value "
                "they get, and why they should follow after seeing one reply. Keep original "
                "posts active alongside replies so the account does not look reply-only."
            )
        except Exception as exc:
            await status.edit_text(_friendly_error(exc))

    async def wins(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        message = update.effective_message
        raw = " ".join(context.args).strip().lower() or "30d"
        try:
            days = _parse_report_days(raw, allowed=(7, 30, 90))
        except RuntimeError as exc:
            await message.reply_text(str(exc))
            return
        rows = self.reply_learning.winning_insights(days, limit=5)
        if not rows:
            await message.reply_text("No tracked winning replies are available yet.")
            return
        blocks = []
        for index, row in enumerate(rows, start=1):
            views = int((row.get("snapshots") or [{}])[-1].get("views") or 0)
            blocks.append(
                f"{index}. {views:,} views | @{row.get('root_author') or 'unknown'} | "
                f"{row.get('strategy') or 'unknown'}\n"
                f"{_truncate_text(str(row.get('actual_text') or ''), 240)}\n"
                f"{row.get('target_url') or row.get('reply_url') or ''}"
            )
        await message.reply_text(
            f"Winning insight bank - {days} days\n\n"
            + "\n\n".join(blocks)
            + "\n\nReuse the angle or evidence pattern for original content; do not copy wording."
        )

    async def inbox(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        del context
        message = update.effective_message
        pending = [
            approval
            for approval in self.approvals.items()
            if approval.kind == "reply"
            and approval.status == "pending"
            and bool((approval.metadata or {}).get("relationship_followup"))
        ]
        if not pending:
            await message.reply_text(
                "Conversation inbox is clear. No author or verified-audience follow-up "
                "needs a decision."
            )
            return
        await message.reply_text(
            f"Conversation inbox: {len(pending)} pending author/verified-audience "
            "conversation(s). Showing up to 5."
        )
        for approval in pending[:5]:
            await self._send_approval(approval)

    async def session(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        message = update.effective_message
        raw = " ".join(context.args).strip().lower()
        if not raw:
            raw = str(self.settings.reply_session_minutes)
        active_session = _latest_reply_session_id(
            self.approvals.items(),
            chat_id=message.chat.id,
        )
        if raw == "status":
            if not active_session:
                await message.reply_text("No active reply session.")
                return
            cards = [
                approval
                for approval in self.approvals.items()
                if (approval.metadata or {}).get("reply_session_id") == active_session
            ]
            pending = sum(approval.status == "pending" for approval in cards)
            approved = sum(
                approval.status in {"mobile_approved", "published", "completed"}
                for approval in cards
            )
            await message.reply_text(
                f"Reply session {active_session}: {approved} approved, "
                f"{pending} pending, {len(cards)} total. Goal: {self.settings.creator_goal}."
            )
            return
        if raw == "stop":
            if not active_session:
                await message.reply_text("No active reply session to stop.")
                return
            cancelled = self.approvals.cancel_pending_by_metadata(
                "reply_session_id",
                active_session,
            )
            await message.reply_text(
                f"Reply session stopped. Cancelled {cancelled} remaining card(s)."
            )
            return
        try:
            minutes = int(raw)
        except ValueError:
            await message.reply_text("Usage: /session <10-120>|status|stop")
            return
        if minutes < 10 or minutes > 120:
            await message.reply_text("Session length must be from 10 to 120 minutes.")
            return
        if self.revenue_ops.pace_paused:
            await message.reply_text(
                "Reply-card generation is paused by /pace or the X health circuit breaker. "
                "Use /pace resume after checking /setupcheck."
            )
            return

        remaining_cap = max(
            0,
            self.settings.creator_daily_reply_cap
            - _reply_approvals_created_today(
                self.approvals.items(),
                timezone_name=self.settings.creator_timezone,
            ),
        )
        remaining_cap = min(remaining_cap, self._hourly_reply_capacity())
        desired = min(5, max(2, round(minutes / 4)), remaining_cap)
        if desired < 2:
            await message.reply_text(
                "Fewer than two reply-card slots remain under the daily/adaptive hourly "
                "ceiling. Hourly capacity recovers automatically; the daily cap resets in "
                f"{self.settings.creator_timezone}."
            )
            return

        await message.chat.send_action(ChatAction.TYPING)
        status = await message.reply_text(
            f"Building a {minutes}-minute session for goal `{self.settings.creator_goal}`. "
            f"Adaptive video allocation: "
            f"{self.reply_learning.recommended_video_share(self.settings.creator_goal):.0%}. "
            "Collecting posts and videos into one ranked queue..."
        )
        target_candidates: list[XSearchResult] = []
        video_candidates: list[XSearchResult] = []
        visual_attachments: list[ImageAttachment] = []
        diagnostics: list[str] = []
        try:
            try:
                _query, targets, target_note = await self._get_reply_target_context(
                    "",
                    status,
                    max_age_minutes=self.settings.reply_target_max_age_minutes,
                    languages=parse_reply_target_languages(
                        self.settings.reply_target_languages
                    ),
                )
                ready, watching = self.reply_watch.classify(targets)
                target_candidates = _combine_reply_target_results(
                    [("session-targets", "session-targets", ready + watching)]
                )[0]
                diagnostics.append(target_note.strip())
            except Exception as exc:
                LOGGER.warning("Session target lane failed", exc_info=True)
                diagnostics.append(f"post lane unavailable: {_friendly_error(exc)}")
            try:
                _label, videos, video_note = await self._get_reply_video_context("", status)
                prepared, visual_attachments, skipped = (
                    await self._prepare_reply_video_evidence(
                        videos,
                        status,
                        max_items=min(4, desired),
                    )
                )
                video_candidates = prepared
                diagnostics.append(f"{video_note}; visual skips={skipped}")
            except Exception as exc:
                LOGGER.warning("Session video lane failed", exc_info=True)
                diagnostics.append(f"video lane unavailable: {_friendly_error(exc)}")

            selected = _select_session_mix(
                target_candidates,
                video_candidates,
                max_items=desired,
                video_share=self.reply_learning.recommended_video_share(
                    self.settings.creator_goal
                ),
            )
            if len(selected) < 2:
                await status.edit_text(
                    "The session collector found fewer than two safe, distinct targets. "
                    "No Gemini job was spent. " + " | ".join(diagnostics[:2])
                )
                return
            session_id = f"{message.chat.id}-{time.time_ns()}"
            created = await self._create_reply_approvals(
                selected,
                query=f"guided {minutes}-minute session; goal={self.settings.creator_goal}",
                chat_id=message.chat.id,
                approver_user_id=(
                    update.effective_user.id
                    if update.effective_user is not None
                    else message.chat.id
                ),
                video_mode=any(result.has_video for result in selected),
                visual_attachments=visual_attachments,
                max_items=desired,
                session_id=session_id,
                sequential=True,
            )
            if not created:
                await status.edit_text(
                    "Targets were found, but the final check created no card. "
                    f"{created.diagnostic()}"
                )
                return
            await status.edit_text(
                f"Session ready: {created.created} card(s), goal {self.settings.creator_goal}. "
                "Only one card is shown at a time; approving or rejecting it opens the next."
            )
        except Exception as exc:
            await status.edit_text(_friendly_error(exc))
        finally:
            await self._notify_x_account_errors(message)

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

    async def replycap(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        chat = update.effective_chat
        message = update.effective_message
        if chat is None or message is None:
            return
        if chat.type != "private":
            await message.reply_text("Use /replycap in a private chat with this bot.")
            return
        if self.approval_chat_id is not None and int(chat.id) != self.approval_chat_id:
            await message.reply_text(
                "Only TELEGRAM_APPROVAL_CHAT_ID can change reply limits."
            )
            return
        raw = " ".join(context.args).strip().lower()
        if not raw:
            await self._request_command_input(update, "replycap")
            return
        if raw in {"show", "current", "status"}:
            approvals = self.approvals.items()
            daily_used = _reply_approvals_created_today(
                approvals,
                timezone_name=self.settings.creator_timezone,
            )
            daily_remaining = max(
                0,
                self.settings.creator_daily_reply_cap - daily_used,
            )
            hourly_used = _reply_approvals_created_since(
                approvals,
                since=datetime.now(UTC) - timedelta(hours=1),
            )
            hourly_ceiling = self._adaptive_hourly_ceiling()
            hourly_remaining = (
                0
                if self.revenue_ops.pace_paused
                else max(0, hourly_ceiling - hourly_used)
            )
            available_now = min(daily_remaining, hourly_remaining)
            pending = sum(
                approval.kind == "reply" and approval.status == "pending"
                for approval in approvals
            )
            farming_guardrails = reply_farming_guardrails(
                self.revenue_ops.risk_mode
            )
            japanese_usage = ""
            if farming_guardrails.japanese_daily_cap is not None:
                japanese_today = _language_approvals_created_today(
                    approvals,
                    language="ja",
                    timezone_name=self.settings.creator_timezone,
                )
                japanese_hour = _language_approvals_created_since(
                    approvals,
                    language="ja",
                    since=datetime.now(UTC) - timedelta(hours=1),
                )
                japanese_usage = (
                    f"- Japanese safety: {japanese_today}/"
                    f"{farming_guardrails.japanese_daily_cap} today; "
                    f"{japanese_hour}/{farming_guardrails.japanese_hourly_cap} "
                    "in the last 60 minutes\n"
                )
            try:
                creator_timezone = ZoneInfo(self.settings.creator_timezone)
            except ZoneInfoNotFoundError:
                creator_timezone = UTC
            next_creator_day = (
                datetime.now(creator_timezone).replace(
                    hour=0,
                    minute=0,
                    second=0,
                    microsecond=0,
                )
                + timedelta(days=1)
            )
            await message.reply_text(
                "Reply-card usage (approved cards only)\n"
                f"- Approved today: {daily_used}/{self.settings.creator_daily_reply_cap}\n"
                f"- Remaining today: {daily_remaining}\n"
                f"- Approved in the last 60 minutes: {hourly_used}/{hourly_ceiling}\n"
                f"- Available now: {available_now}\n"
                f"- Pending drafts not counted: {pending}\n"
                f"- Per author/day: {self.settings.reply_author_daily_cap}\n"
                f"{japanese_usage}"
                f"- Daily reset: {next_creator_day:%Y-%m-%d %H:%M} "
                f"{self.settings.creator_timezone}\n\n"
                + (
                    "Adaptive pace is PAUSED. Use /pace resume after checking /setupcheck."
                    if self.revenue_ops.pace_paused
                    else "Pending and rejected cards do not consume these ceilings."
                )
            )
            return
        scope, separator, raw_value = raw.partition(" ")
        if not separator:
            await message.reply_text("Usage: /replycap show|daily <1-2000>|author <1-25>")
            return
        try:
            value = int(raw_value.strip())
        except ValueError:
            await message.reply_text("Reply limit must be a whole number.")
            return
        if scope == "daily" and 1 <= value <= 2_000:
            update_env_value("CREATOR_DAILY_REPLY_CAP", str(value))
            self.settings = replace(self.settings, creator_daily_reply_cap=value)
            label = "Daily reply-card ceiling"
        elif scope == "author" and 1 <= value <= 25:
            update_env_value("REPLY_AUTHOR_DAILY_CAP", str(value))
            self.settings = replace(self.settings, reply_author_daily_cap=value)
            label = "Per-author daily ceiling"
        else:
            await message.reply_text("Usage: /replycap show|daily <1-2000>|author <1-25>")
            return
        await message.reply_text(f"{label} set to {value} and saved to .env.")

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
            "creator_goal": self.settings.creator_goal,
            "daily_reply_cap": self.settings.creator_daily_reply_cap,
            "author_daily_reply_cap": self.settings.reply_author_daily_cap,
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
        language_lines = _format_performance_dimension(report["by_language"])
        source_lines = _format_performance_dimension(report["by_source"])
        experiment_lines = _format_performance_dimension(report["by_experiment"])
        risk_lines = _format_performance_dimension(report["by_risk"])
        hour_lines = _format_performance_dimension(report["by_hour_local"])
        await message.reply_text(
            f"Reply performance - last {days} days\n"
            f"Posted/tracked: {report['posted']}\n"
            f"Replies/posts: {report['replies']}/{report['posts']}\n"
            f"Completed 24h measurement: {report['measured']}\n"
            f"Author replies detected: {report['author_replies']}\n"
            f"Author-response rate: {report['author_response_rate']:.0%}\n"
            f"Follower lift during tracked reply/post windows: "
            f"{report['follower_window_lift']} (account-level proxy)\n"
            f"Average outcome score: {report['average_score']:.1f}/100\n"
            f"Median reply views: {report['median_views']:,}\n"
            f"Replies over 5k/20k/50k: {report['over_5k']}/"
            f"{report['over_20k']}/{report['over_50k']}\n"
            f"Approval rate: {report['approval_rate']:.0%}\n\n"
            f"Median card-to-approval/post latency: "
            f"{_format_duration(report['median_approval_latency_seconds'])}/"
            f"{_format_duration(report['median_posting_latency_seconds'])}\n\n"
            f"Median Gemini batch latency: "
            f"{_format_duration(report['median_generation_latency_seconds'])}\n\n"
            f"By strategy:\n{strategy_lines}\n\n"
            f"Best languages:\n{language_lines}\n\n"
            f"Best sources:\n{source_lines}\n\n"
            f"Format experiments:\n{experiment_lines}\n\n"
            f"Revenue safety mix:\n{risk_lines}\n\n"
            f"Best hours ({self.settings.creator_timezone}):\n{hour_lines}\n\n"
            f"Recommendation: {_performance_recommendation(report)}"
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
        inventory = self.reply_watch.inventory()
        learning_history = bool(
            self.reply_learning.path
            and self.reply_learning.path.exists()
            and (learning.measured or learning.tracking or learning.waiting)
        )
        await message.reply_text(
            f"Creator bot health: {state}\n\n"
            f"Runtime revision: {BOT_RUNTIME_REVISION}\n"
            f"X account records/healthy: {len(accounts)}/{len(healthy_accounts)}\n"
            f"Tracking username: "
            f"{('@' + self.settings.x_owner_username) if self.settings.x_owner_username else 'missing'}\n"
            f"Learning: {'ON' if learning.enabled else 'OFF'}; "
            f"{learning.measured} measured, {learning.waiting} waiting; "
            f"history {'available' if learning_history else 'not accumulated yet'}\n"
            f"Goal: {self.settings.creator_goal}\n"
            f"Revenue safety/pace: {self.revenue_ops.risk_mode}/"
            f"{self.revenue_ops.pace_mode}"
            f"{' (PAUSED)' if self.revenue_ops.pace_paused else ''}\n"
            f"Priority authors: {len(self.revenue_ops.watch_authors())}; auto learning "
            f"{'ON' if self.revenue_ops.auto_watch_enabled else 'OFF'}\n"
            f"Candidate reservoir: {inventory.get('ready', 0)} ready, "
            f"{inventory.get('watching', 0)} watching\n"
            f"Reply schedule: {self.settings.telegram_reply_targets_minutes or 'extension default'} minutes\n"
            f"Video-reply schedule: {self.settings.telegram_reply_video_minutes or 'extension default (5)'} minutes\n"
            f"Reply cards per run: targets {self.settings.reply_target_batch_size}; "
            f"video {self.settings.reply_video_batch_size}\n"
            f"Timezone: {self.settings.creator_timezone}\n"
            f"Stale mobile approvals: {stale_mobile}\n"
            f"Startup cleanup: {self._approval_migration.get('archived_posts', 0)} "
            f"legacy posts archived; "
            f"{self._approval_migration.get('released_mobile', 0)} stale locks released\n"
            f"Mode/languages: {self.settings.reply_target_mode}; "
            f"{self.settings.reply_target_languages}\n\n"
            f"Actions:\n{detail}"
            + (f"\n\nAccount check error: {account_error}" if account_error else "")
        )

    async def _create_reply_approvals(
        self,
        results: list[XSearchResult],
        *,
        query: str,
        chat_id: int,
        approver_user_id: int,
        video_mode: bool = False,
        visual_attachments: list[ImageAttachment] | None = None,
        max_items: int | None = None,
        session_id: str = "",
        sequential: bool = False,
    ) -> _ReplyApprovalCreationResult:
        if not results:
            return _ReplyApprovalCreationResult(blocked_reason="No candidates were supplied.")
        if self.revenue_ops.pace_paused:
            return _ReplyApprovalCreationResult(blocked_reason="Reply generation is paused.")
        hourly_remaining = self._hourly_reply_capacity()
        if hourly_remaining <= 0:
            return _ReplyApprovalCreationResult(
                blocked_reason="The adaptive hourly card ceiling is full."
            )
        requested_batch_size = min(
            5,
            hourly_remaining,
            max(
                1,
                max_items
                if max_items is not None
                else (
                    self.settings.reply_video_batch_size
                    if video_mode
                    else self.settings.reply_target_batch_size
                ),
            ),
        )
        generation_size = min(5, requested_batch_size + (0 if sequential else 2))
        selected, filtered_author_limit, filtered_language_limit = (
            self._filter_reply_generation_candidates(
                _select_diverse_candidates(results, len(results)),
                max_items=generation_size,
            )
        )
        if not selected:
            return _ReplyApprovalCreationResult(
                filtered_author_limit=filtered_author_limit,
                filtered_language_limit=filtered_language_limit,
                blocked_reason=(
                    "All candidates were removed by per-author or Japanese safety limits."
                ),
            )
        strategy_by_url = {
            result.url: self.reply_learning.choose_strategy()
            for result in selected
        }
        experiment_by_url = {
            result.url: self.reply_learning.choose_experiment_variant()
            for result in selected
        }
        reply_context = (
            _summarize_mixed_reply_context(selected)
            if any(result.has_video for result in selected)
            and any(not result.has_video for result in selected)
            else (
                summarize_reply_video_context(selected, max_items=generation_size)
                if video_mode
                else summarize_reply_target_context(selected, max_items=generation_size)
            )
        )
        style_examples: list[str] = []
        for result in selected:
            source_type = "replyvideo" if result.has_video else "replytargets"
            for example in self.reply_learning.style_examples(
                language=result.language,
                source_type=source_type,
                limit=2,
            ):
                if example not in style_examples:
                    style_examples.append(example)
        generation_options: dict[str, Any] = {
            "strategy_by_url": strategy_by_url,
            "experiment_by_url": experiment_by_url,
            "style_examples": style_examples[:3],
        }
        if video_mode:
            generation_options["video_mode"] = True
            generation_options["visual_attachments"] = visual_attachments or []
        generation_started = time.monotonic()
        drafts = await self.ai.generate_reply_targets(
            query,
            reply_context,
            **generation_options,
        )
        generation_latency_seconds = round(time.monotonic() - generation_started, 1)
        created: list[AutomationApproval] = []
        filtered_active = 0
        filtered_missing_source = 0
        filtered_closed = 0
        filtered_duplicate = 0
        delivery_queue_id = session_id or f"batch-{chat_id}-{time.time_ns()}"
        recent_texts = _recent_reply_texts(self.approvals.items(), limit=120)
        for draft in drafts:
            if len(created) >= requested_batch_size:
                break
            target_url = _format_reply_target_link(draft)
            if self.approvals.has_active_target(target_url):
                filtered_active += 1
                continue
            result = _result_for_url(selected, target_url)
            if result is None:
                filtered_missing_source += 1
                continue
            if not await self._opportunity_still_open(result, video_mode=result.has_video):
                self.reply_watch.mark_expired(target_url, reason="opportunity changed")
                filtered_closed += 1
                continue
            draft_text = _format_reply_target_reply(draft)
            if _is_semantic_duplicate(draft_text, recent_texts):
                filtered_duplicate += 1
                continue
            strategy = strategy_by_url.get(target_url, "specific_observation")
            approval = self.approvals.create(
                kind="reply",
                text=draft_text,
                chat_id=chat_id,
                approver_user_id=approver_user_id,
                target_url=target_url,
                target_label=draft.target,
                metadata=_reply_tracking_metadata(
                    result,
                    strategy,
                    source_type=(
                        "replyvideo"
                        if result is not None and result.has_video
                        else "replytargets"
                    ),
                )
                | {
                    "source_summary_vi": draft.source_summary_vi,
                    "reply_translation_vi": draft.reply_translation_vi,
                    "experiment_variant": experiment_by_url.get(
                        target_url, "adaptive"
                    ),
                    "generation_latency_seconds": generation_latency_seconds,
                    "creator_goal": self.settings.creator_goal,
                    "creator_timezone": self.settings.creator_timezone,
                    "reply_safety_mode": self.revenue_ops.risk_mode,
                    "manual_final_submission": True,
                    "reply_session_id": session_id,
                    "reply_session_index": len(created),
                    "reply_session_sequential": bool(sequential),
                    "session_card_sent": False,
                    "reply_delivery_queue_id": delivery_queue_id,
                    "reply_delivery_queue_index": len(created),
                    "reply_delivery_card_sent": False,
                },
            )
            created.append(approval)
            recent_texts.append(draft_text)
            self.reply_watch.mark_drafted(target_url)
        for approval in created:
            self.approvals.update_metadata(
                approval.id,
                reply_session_size=len(created),
            )
        if created:
            await self._send_next_reply_delivery(
                delivery_queue_id,
                respect_pacing=True,
            )
        return _ReplyApprovalCreationResult(
            created=len(created),
            ai_drafts=len(drafts),
            filtered_author_limit=filtered_author_limit,
            filtered_language_limit=filtered_language_limit,
            filtered_active=filtered_active,
            filtered_missing_source=filtered_missing_source,
            filtered_closed=filtered_closed,
            filtered_duplicate=filtered_duplicate,
        )

    def _japanese_reply_slots_remaining(self) -> int | None:
        guardrails = reply_farming_guardrails(self.revenue_ops.risk_mode)
        if guardrails.japanese_daily_cap is None:
            return None
        approvals = self.approvals.items()
        used_today = _language_approvals_created_today(
            approvals,
            language="ja",
            timezone_name=self.settings.creator_timezone,
        )
        used_hour = _language_approvals_created_since(
            approvals,
            language="ja",
            since=datetime.now(UTC) - timedelta(hours=1),
        )
        return max(
            0,
            min(
                guardrails.japanese_daily_cap - used_today,
                (guardrails.japanese_hourly_cap or 0) - used_hour,
            ),
        )

    def _filter_reply_generation_candidates(
        self,
        results: list[XSearchResult],
        *,
        max_items: int | None = None,
    ) -> tuple[list[XSearchResult], int, int]:
        """Apply approval-time author/language limits while retaining backups."""
        approvals = self.approvals.items()
        japanese_slots = self._japanese_reply_slots_remaining()
        selected: list[XSearchResult] = []
        selected_by_author: dict[str, int] = {}
        selected_japanese = 0
        filtered_author_limit = 0
        filtered_language_limit = 0
        limit = len(results) if max_items is None else max(0, max_items)
        if limit == 0:
            return [], 0, 0

        for result in results:
            author = str(result.username or "").strip().lstrip("@").casefold()
            author_used = _author_approvals_created_today(
                approvals,
                username=author,
                timezone_name=self.settings.creator_timezone,
            )
            if author_used + selected_by_author.get(author, 0) >= (
                self.settings.reply_author_daily_cap
            ):
                filtered_author_limit += 1
                continue

            is_japanese = str(result.language or "").casefold().startswith("ja")
            if (
                is_japanese
                and japanese_slots is not None
                and selected_japanese >= japanese_slots
            ):
                filtered_language_limit += 1
                continue

            selected.append(result)
            if author:
                selected_by_author[author] = selected_by_author.get(author, 0) + 1
            if is_japanese:
                selected_japanese += 1
            if len(selected) >= limit:
                break

        return selected, filtered_author_limit, filtered_language_limit

    def _reply_approval_safety_block(
        self,
        approval: AutomationApproval,
        *,
        now: datetime | None = None,
    ) -> str:
        current = now or datetime.now(UTC)
        approvals = self.approvals.items()
        if _reply_approvals_created_today(
            approvals,
            timezone_name=self.settings.creator_timezone,
        ) >= self.settings.creator_daily_reply_cap:
            return "Daily approved-reply cap reached. Try again after the creator-day reset."

        author = str((approval.metadata or {}).get("root_author") or "").strip()
        if author and _author_approvals_created_today(
            approvals,
            username=author,
            timezone_name=self.settings.creator_timezone,
        ) >= self.settings.reply_author_daily_cap:
            return f"Per-author safety cap reached for @{author.lstrip('@')}."

        # A direct author/audience response is an actual conversation, not an
        # unsolicited viral-target hop. Keep hard account caps, but do not delay
        # a time-sensitive human follow-up with heuristic burst limits.
        if bool((approval.metadata or {}).get("relationship_followup")):
            return ""

        guardrails = reply_farming_guardrails(self.revenue_ops.risk_mode)
        if (
            guardrails.global_hourly_cap is not None
            and _reply_approvals_created_since(
                approvals,
                since=current - timedelta(hours=1),
            )
            >= guardrails.global_hourly_cap
        ):
            return (
                "Anti-farming hourly guardrail reached. Approved capacity returns "
                "gradually over the next 60 minutes."
            )

        language = str((approval.metadata or {}).get("language") or "").casefold()
        if language.startswith("ja") and guardrails.japanese_daily_cap is not None:
            if _language_approvals_created_today(
                approvals,
                language="ja",
                timezone_name=self.settings.creator_timezone,
            ) >= guardrails.japanese_daily_cap:
                return "Japanese reply safety cap reached for this creator day."
            if _language_approvals_created_since(
                approvals,
                language="ja",
                since=current - timedelta(hours=1),
            ) >= (guardrails.japanese_hourly_cap or 0):
                return (
                    "Japanese hourly safety cap reached. Wait for approved replies "
                    "to leave the rolling 60-minute window."
                )

        return ""

    def _reply_approval_delay_seconds(
        self,
        approval: AutomationApproval,
        *,
        now: datetime | None = None,
    ) -> int:
        if bool((approval.metadata or {}).get("relationship_followup")):
            return 0
        current = now or datetime.now(UTC)
        base_gap = self._reply_delivery_base_gap_seconds()
        approved = [
            item
            for item in self.approvals.items()
            if _is_approved_reply_card(item) and item.decided_at is not None
        ]
        if not approved:
            return 0
        last_approved_at = max(
            item.decided_at for item in approved if item.decided_at is not None
        )
        jitter = int.from_bytes(
            hashlib.sha256(
                f"{self.revenue_ops.risk_mode}:{approval.id}".encode("utf-8")
            ).digest()[:2],
            "big",
        ) % (base_gap + 1)
        required_gap = base_gap + jitter
        remaining = math.ceil(
            required_gap - max(0.0, (current - last_approved_at).total_seconds())
        )
        if remaining > 0:
            return remaining
        return 0

    def _reply_delivery_base_gap_seconds(self) -> int:
        """Keep card delivery paced even when risk filtering is fully open."""
        pace_floor = {
            "conservative": 120,
            "adaptive": 60,
            "high": 30,
        }.get(self.revenue_ops.pace_mode, 60)
        safety_floor = reply_farming_guardrails(
            self.revenue_ops.risk_mode
        ).minimum_approval_gap_seconds
        return max(pace_floor, safety_floor)

    def _adaptive_hourly_ceiling(self) -> int:
        ceiling = self.revenue_ops.hourly_ceiling(self.settings.creator_daily_reply_cap)
        farming_limit = reply_farming_guardrails(
            self.revenue_ops.risk_mode
        ).global_hourly_cap
        if farming_limit is not None:
            ceiling = min(ceiling, farming_limit)
        recent_decisions = sorted(
            [
                approval
                for approval in self.approvals.items()
                if approval.kind == "reply"
                and approval.decided_at is not None
                and approval.decided_at >= datetime.now(UTC) - timedelta(hours=24)
                and approval.status
                in {"rejected", "mobile_approved", "published", "completed"}
            ],
            key=lambda approval: approval.decided_at or approval.created_at,
            reverse=True,
        )[:20]
        if len(recent_decisions) >= 5:
            approval_rate = sum(
                approval.status in {"mobile_approved", "published", "completed"}
                for approval in recent_decisions
            ) / len(recent_decisions)
            if approval_rate < 0.20:
                ceiling = max(2, round(ceiling * 0.25))
            elif approval_rate < 0.40:
                ceiling = max(3, round(ceiling * 0.50))
        performance = self.reply_learning.report(7)
        if int(performance.get("measured") or 0) >= 5 and float(
            performance.get("average_score") or 0.0
        ) < 25.0:
            ceiling = max(2, round(ceiling * 0.75))
        return ceiling

    def _hourly_reply_capacity(self) -> int:
        if self.revenue_ops.pace_paused:
            return 0
        ceiling = self._adaptive_hourly_ceiling()
        used = _reply_approvals_created_since(
            self.approvals.items(),
            since=datetime.now(UTC) - timedelta(hours=1),
        )
        return max(
            0,
            ceiling - used,
        )

    async def _opportunity_still_open(
        self,
        original: XSearchResult,
        *,
        video_mode: bool,
    ) -> bool:
        try:
            current = await asyncio.wait_for(
                self.x_search.tweet_by_id(original.id),
                timeout=REPLY_TARGET_REFRESH_TIMEOUT_SECONDS,
            )
        except Exception:
            return True
        if not isinstance(current, XSearchResult):
            return current is not None
        if current.is_reply or current.is_retweet:
            return False
        age_limit = (
            min(90, max(45, self.settings.reply_video_max_age_minutes * 2))
            if video_mode
            else self.settings.reply_target_max_age_minutes
        )
        age_minutes = (
            (datetime.now(UTC).timestamp() - current.created_at_timestamp) / 60
            if current.created_at_timestamp
            else 0.0
        )
        if age_minutes > age_limit:
            return False
        reply_limit = max(
            80 if video_mode else 120,
            original.reply_count + 50,
            original.reply_count * 2 + 10,
        )
        if current.reply_count >= reply_limit:
            return False
        views_per_reply = (current.view_count or 0) / max(1, current.reply_count + 1)
        return current.view_count is None or views_per_reply >= (150 if video_mode else 80)

    async def _send_next_session_approval(self, session_id: str) -> bool:
        return await self._send_next_approval_queue(
            session_id,
            queue_key="reply_session_id",
            index_key="reply_session_index",
            sent_key="session_card_sent",
            respect_pacing=False,
        )

    async def _send_next_reply_delivery(
        self,
        queue_id: str,
        *,
        respect_pacing: bool,
    ) -> bool:
        # Batch IDs remain in metadata for diagnostics, but delivery is global:
        # replytargets, replyvideo, sessions, and overlapping scheduled runs
        # must never expose two undecided cards at the same time.
        del queue_id
        return await self._send_next_approval_queue(
            GLOBAL_REPLY_DELIVERY_QUEUE_ID,
            queue_key="reply_delivery_queue_id",
            index_key="reply_delivery_queue_index",
            sent_key="reply_delivery_card_sent",
            respect_pacing=respect_pacing,
            global_reply_delivery=True,
        )

    async def _send_next_approval_queue(
        self,
        queue_id: str,
        *,
        queue_key: str,
        index_key: str,
        sent_key: str,
        respect_pacing: bool,
        global_reply_delivery: bool = False,
    ) -> bool:
        if global_reply_delivery:
            pending = sorted(
                (
                    approval
                    for approval in self.approvals.items()
                    if approval.kind == "reply"
                    and approval.status == "pending"
                    and bool((approval.metadata or {}).get(queue_key))
                ),
                key=lambda approval: (
                    approval.created_at,
                    int((approval.metadata or {}).get(index_key) or 0),
                ),
            )
            # A sent-but-undecided card is already visible in Telegram. Wait
            # for its Approve/Reject callback before exposing any other batch.
            if any(
                bool((approval.metadata or {}).get(sent_key))
                for approval in pending
            ):
                return True
        else:
            pending = sorted(
                self.approvals.pending_by_metadata(queue_key, queue_id),
                key=lambda approval: int(
                    (approval.metadata or {}).get(index_key) or 0
                ),
            )
        next_card = next(
            (
                approval
                for approval in pending
                if not bool((approval.metadata or {}).get(sent_key))
            ),
            None,
        )
        if next_card is None:
            return False
        delay_seconds = (
            self._reply_approval_delay_seconds(next_card)
            if respect_pacing
            else 0
        )
        if delay_seconds > 0:
            self.approvals.update_metadata(
                next_card.id,
                reply_delivery_not_before=(
                    datetime.now(UTC) + timedelta(seconds=delay_seconds)
                ).isoformat(),
            )
            self._schedule_delayed_approval_queue(
                queue_id,
                queue_key=queue_key,
                index_key=index_key,
                sent_key=sent_key,
                delay_seconds=delay_seconds,
                global_reply_delivery=global_reply_delivery,
            )
            return True
        try:
            telegram_message = await self._send_approval(next_card)
        except Exception as exc:
            attempts = int(
                (next_card.metadata or {}).get("reply_delivery_attempts") or 0
            ) + 1
            retry_seconds = min(120, 15 * (2 ** min(attempts - 1, 3)))
            self.approvals.update_metadata(
                next_card.id,
                **{
                    sent_key: False,
                    "reply_delivery_attempts": attempts,
                    "reply_delivery_last_error": _truncate_text(
                        _exception_detail(exc),
                        500,
                    ),
                    "reply_delivery_not_before": (
                        datetime.now(UTC) + timedelta(seconds=retry_seconds)
                    ).isoformat(),
                },
            )
            self._schedule_delayed_approval_queue(
                queue_id,
                queue_key=queue_key,
                index_key=index_key,
                sent_key=sent_key,
                delay_seconds=retry_seconds,
                global_reply_delivery=global_reply_delivery,
            )
            LOGGER.warning(
                "Telegram reply-card delivery failed; retrying in %ss (attempt %s): %s",
                retry_seconds,
                attempts,
                _exception_detail(exc),
            )
            return True
        self.approvals.update_metadata(
            next_card.id,
            **{
                sent_key: True,
                "reply_delivery_not_before": "",
                "reply_delivery_attempts": 0,
                "reply_delivery_last_error": "",
                "reply_delivery_message_id": int(
                    getattr(telegram_message, "message_id", 0) or 0
                ),
                "reply_delivery_sent_at": datetime.now(UTC).isoformat(),
            },
        )
        return True

    def _schedule_delayed_approval_queue(
        self,
        queue_id: str,
        *,
        queue_key: str,
        index_key: str,
        sent_key: str,
        delay_seconds: int,
        global_reply_delivery: bool = False,
    ) -> None:
        task_key = f"{queue_key}:{queue_id}"
        active = self._delayed_approval_tasks.get(task_key)
        if active is not None and not active.done():
            return

        async def deliver() -> None:
            await asyncio.sleep(max(1, delay_seconds))
            current_task = asyncio.current_task()
            if self._delayed_approval_tasks.get(task_key) is current_task:
                self._delayed_approval_tasks.pop(task_key, None)
            await self._send_next_approval_queue(
                queue_id,
                queue_key=queue_key,
                index_key=index_key,
                sent_key=sent_key,
                respect_pacing=True,
                global_reply_delivery=global_reply_delivery,
            )

        task = asyncio.create_task(
            deliver(),
            name=f"delayed-reply-card-{queue_id}",
        )
        self._delayed_approval_tasks[task_key] = task
        self._automation_tasks.add(task)

        def cleanup(done: asyncio.Task[None]) -> None:
            self._automation_tasks.discard(done)
            if self._delayed_approval_tasks.get(task_key) is done:
                self._delayed_approval_tasks.pop(task_key, None)
            error = None if done.cancelled() else done.exception()
            if error is not None:
                LOGGER.error(
                    "Delayed reply-card delivery failed",
                    exc_info=(type(error), error, error.__traceback__),
                )

        task.add_done_callback(cleanup)

    async def _restore_reply_delivery_queues(self) -> None:
        queue_ids = {
            str((approval.metadata or {}).get("reply_delivery_queue_id") or "")
            for approval in self.approvals.items()
            if approval.kind == "reply" and approval.status == "pending"
        }
        for queue_id in sorted(queue_id for queue_id in queue_ids if queue_id):
            pending = self.approvals.pending_by_metadata(
                "reply_delivery_queue_id",
                queue_id,
            )
            # Version 0.8.8 initially marked a delayed card as sent before
            # Telegram confirmed send_message. A pending non-first card with no
            # Telegram receipt is therefore uncertain. Release it once during
            # upgrade; later deliveries always persist the real message ID.
            for approval in pending:
                metadata = approval.metadata or {}
                if (
                    bool(metadata.get("reply_delivery_card_sent"))
                    and not int(metadata.get("reply_delivery_message_id") or 0)
                    and int(metadata.get("reply_delivery_queue_index") or 0) > 0
                    and not bool(metadata.get("reply_delivery_receipt_recovered"))
                ):
                    self.approvals.update_metadata(
                        approval.id,
                        reply_delivery_card_sent=False,
                        reply_delivery_receipt_recovered=True,
                    )
            pending = self.approvals.pending_by_metadata(
                "reply_delivery_queue_id",
                queue_id,
            )
            if any(
                bool((approval.metadata or {}).get("reply_delivery_card_sent"))
                for approval in pending
            ):
                continue
            await self._send_next_reply_delivery(queue_id, respect_pacing=True)

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
                    if not record.get("followup_created"):
                        author_response = next(
                            (
                                item
                                for item in direct_replies
                                if item.in_reply_to_tweet_id == int(record["reply_id"])
                                and item.username.casefold() == root_author
                            ),
                            None,
                        )
                    if not record.get("audience_followup_created"):
                        audience_response = next(
                            (
                                item
                                for item in direct_replies
                                if item.in_reply_to_tweet_id == int(record["reply_id"])
                                and item.author_verified
                                and item.username.casefold()
                                not in {root_author, owner.casefold()}
                                and not self.reply_learning.audience_response_seen(
                                    record, item.id
                                )
                            ),
                            None,
                        )
                        if audience_response is not None:
                            await self._create_audience_followup(
                                record,
                                audience_response,
                            )
                            self.reply_learning.mark_audience_response(
                                record["approval_id"],
                                audience_response,
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
        watch_changes = self.revenue_ops.refresh_auto_authors(
            self.reply_learning.author_portfolios(),
            now=current,
        )
        if (
            self._application is not None
            and self.approval_chat_id is not None
            and (watch_changes["promoted"] or watch_changes["demoted"])
        ):
            blocks = ["Automatic author watchlist updated."]
            if watch_changes["promoted"]:
                blocks.append(
                    "Promoted: "
                    + ", ".join(f"@{name}" for name in watch_changes["promoted"])
                )
            if watch_changes["demoted"]:
                blocks.append(
                    "Demoted: "
                    + ", ".join(f"@{name}" for name in watch_changes["demoted"])
                )
            blocks.append("Use /watchauthor list to review or /watchauthor pin @name.")
            await self._application.bot.send_message(
                chat_id=self.approval_chat_id,
                text="\n".join(blocks),
            )
        await self._maybe_send_daily_digest(current)

    async def _maybe_send_daily_digest(self, current: datetime) -> None:
        if self._application is None or self.approval_chat_id is None:
            return
        try:
            timezone = ZoneInfo(self.settings.creator_timezone)
        except ZoneInfoNotFoundError:
            timezone = UTC
        local_now = current.astimezone(timezone)
        digest_date = local_now.date().isoformat()
        if (
            local_now.hour < self.settings.reply_daily_digest_hour
            or self.reply_learning.last_digest_date == digest_date
        ):
            return
        report = self.reply_learning.report(1, now=current)
        self.reply_learning.mark_digest_sent(digest_date)
        if not report["posted"]:
            return
        await self._application.bot.send_message(
            chat_id=self.approval_chat_id,
            text=(
                f"Daily reply digest ({self.settings.creator_timezone})\n"
                f"Tracked/measured: {report['posted']}/{report['measured']}\n"
                f"Median views: {report['median_views']:,}\n"
                f"Over 5k/20k/50k: {report['over_5k']}/"
                f"{report['over_20k']}/{report['over_50k']}\n"
                f"Author-response rate: {report['author_response_rate']:.0%}\n"
                f"Next allocation: {_performance_recommendation(report)}"
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
            text=generated,
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
        await self._send_approval(approval)

    async def _create_audience_followup(
        self,
        record: dict[str, Any],
        response: XSearchResult,
    ) -> None:
        if self._application is None:
            return
        original = self.approvals.get(str(record.get("approval_id") or ""))
        if original is None:
            return
        generated = await self.ai.generate_reply_from_text(response.text)
        approval = self.approvals.create(
            kind="reply",
            text=generated,
            chat_id=original.chat_id,
            approver_user_id=original.approver_user_id,
            target_url=response.url,
            target_label=f"@{response.username} high-value follow-up",
            metadata=_reply_tracking_metadata(
                response,
                "specific_observation",
                source_type="audience_followup",
            )
            | {
                "relationship_followup": True,
                "conversation_followup": True,
                "response_kind": "verified_audience",
                "relationship_parent_approval_id": str(record.get("approval_id") or ""),
                "author_response_text": response.text,
                "author_response_url": response.url,
            },
        )
        await self._send_approval(approval)

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
            "why",
        }:
            await query.answer("Unknown approval action.", show_alert=True)
            return
        answered = False
        try:
            existing = self.approvals.get(approval_id)
            if existing is None:
                raise RuntimeError("Unknown approval request.")
            reply_session_id = str(
                (existing.metadata or {}).get("reply_session_id") or ""
            )
            reply_delivery_queue_id = str(
                (existing.metadata or {}).get("reply_delivery_queue_id") or ""
            )
            if decision == "why":
                await query.answer()
                answered = True
                await query.message.reply_text(_target_explanation(existing))
                return
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
                    _approval_message_text(approval),
                    reply_markup=_approval_keyboard(approval),
                )
                return
            if (
                decision in {"approve", "mobile", "continue"}
                and existing.kind == "reply"
                and existing.status == "pending"
            ):
                safety_block = self._reply_approval_safety_block(existing)
                if safety_block:
                    await query.answer(safety_block, show_alert=True)
                    return
            approval = self.approvals.decide(
                approval_id,
                approve=decision in {"approve", "mobile", "continue"},
                chat_id=query.message.chat.id,
                user_id=query.from_user.id,
                destination="mobile",
            )
            if approval.decided_at is not None:
                self.approvals.update_metadata(
                    approval.id,
                    approval_latency_seconds=round(
                        max(
                            0.0,
                            (approval.decided_at - approval.created_at).total_seconds(),
                        ),
                        1,
                    ),
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
            if approval.status == "mobile_approved" and approval.kind == "reply":
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
            if reply_delivery_queue_id:
                await self._send_next_reply_delivery(
                    reply_delivery_queue_id,
                    respect_pacing=(
                        approval.status == "mobile_approved"
                        and not bool(
                            (approval.metadata or {}).get("relationship_followup")
                        )
                    ),
                )
            elif (
                reply_session_id
                and bool((existing.metadata or {}).get("reply_session_sequential"))
            ):
                # Backward compatibility for guided-session cards created by
                # versions before the delivery queue was introduced.
                await self._send_next_session_approval(reply_session_id)
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
            if approval.kind == "reply":
                self.reply_learning.register_approval(approval)
            text = "Reply draft opened and filled in X. Review it, then click the final X button."
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
                paused_now = self.revenue_ops.record_health_error(
                    _exception_detail(exc)
                )
                if self._application is not None and self.approval_chat_id is not None:
                    await self._application.bot.send_message(
                        chat_id=self.approval_chat_id,
                        text=(
                            f"Scheduled /{kind} failed: {_friendly_error(exc)}"
                            + (
                                "\n\nThree automation/provider errors occurred within one "
                                "hour, so new card generation is paused. Tracking stays "
                                "active. Check /setupcheck, then use /pace resume."
                                if paused_now
                                else ""
                            )
                        ),
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
        if self.revenue_ops.pace_paused:
            return
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
                # A healthy empty scan is inventory maintenance, not an alert.
                # Keep Telegram quiet; transport/auth failures still bubble to
                # the scheduled runner and are reported immediately.
                await _delete_message_safely(status)
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
            remaining_cap = min(remaining_cap, self._hourly_reply_capacity())
            confirmed_count = len(ready)
            if remaining_cap <= 0:
                await status.edit_text(
                    "Scheduled /replytargets reached the daily/adaptive hourly card ceiling. "
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
                await _delete_message_safely(status)
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
                    "Scheduled /replytargets created no new cards. Final check: "
                    f"{sent.diagnostic()}."
                )
        except Exception:
            await _delete_message_safely(status)
            raise

    async def _run_scheduled_replyvideo(self, query: str = "") -> None:
        if self._application is None or self.approval_chat_id is None:
            raise RuntimeError("Automation chat is not ready.")
        if self.revenue_ops.pace_paused:
            return
        status = await self._application.bot.send_message(
            chat_id=self.approval_chat_id,
            text=(
                "Scheduled /replyvideo started. Prioritizing fresh Japanese videos, "
                "then filling from other global lanes with low reply competition..."
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
            remaining_cap = min(remaining_cap, self._hourly_reply_capacity())
            if remaining_cap < REPLY_VIDEO_MIN_BATCH_ITEMS:
                await status.edit_text(
                    "Scheduled /replyvideo needs two slots under the daily/adaptive hourly "
                    f"ceiling. The daily cap resets in {self.settings.creator_timezone}."
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
                await _delete_message_safely(status)
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
                queued = _pending_reply_delivery_count(
                    self.approvals.items(),
                    source_type="replyvideo",
                )
                await status.edit_text(
                    "Scheduled /replyvideo created no new cards. Candidates were already "
                    "filtered during the final check. "
                    f"{sent.diagnostic()}. Pending video delivery queue: {queued}. "
                    "Queued cards retry "
                    "Telegram delivery automatically."
                )
        except Exception:
            await _delete_message_safely(status)
            raise

    async def _send_approval(
        self,
        approval: AutomationApproval,
    ) -> Any:
        if self._application is None:
            raise RuntimeError("Telegram bot is not ready.")
        body = _approval_message_text(approval)
        return await self._application.bot.send_message(
            chat_id=approval.chat_id,
            text=body[:4096],
            reply_markup=_approval_keyboard(approval),
        )

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
            remaining_cap = min(remaining_cap, self._hourly_reply_capacity())
            confirmed_count = len(ready)
            if remaining_cap <= 0:
                await status.edit_text(
                    "The daily/adaptive hourly reply-card ceiling has been reached. "
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
            if sent:
                await status.edit_text(
                    f"Queued {sent.created} reply card(s). Cards are delivered one at a time "
                    f"with automatic pacing. Watching total: {watching_total}."
                )
            else:
                await status.edit_text(
                    "No reply card passed the final check. "
                    f"{sent.diagnostic()}. Watching total: {watching_total}."
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
            "Hunting fresh Japanese videos first, then filling from other global "
            "lanes by view velocity and low reply competition..."
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
            remaining_cap = min(remaining_cap, self._hourly_reply_capacity())
            if remaining_cap < REPLY_VIDEO_MIN_BATCH_ITEMS:
                await status.edit_text(
                    "At least two slots under the daily/adaptive hourly ceiling are required "
                    f"for /replyvideo. The daily cap resets in {self.settings.creator_timezone}."
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
            if sent:
                await status.edit_text(
                    f"Queued {sent.created} viral-video reply card(s). Cards are delivered "
                    "one at a time with automatic pacing."
                )
            else:
                await status.edit_text(
                    "No viral-video card passed the final check. "
                    f"{sent.diagnostic()}."
                )
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
            self.ai = create_ai_service(self.settings)
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
                self.x_search = XSearchService(self.settings)
            await status.edit_text(
                f"X cookie saved for account: {saved_name}\n"
                "twscrape will rotate across active accounts automatically.\n\n"
                "Try:\n"
                "/replytargets AI agents\n\n"
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
                self.x_search = XSearchService(self.settings)
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
            await message.reply_text(generated)
        except Exception as exc:
            await status.edit_text(_friendly_error(exc))
        finally:
            if tweet_id is not None:
                await self._notify_x_account_errors(message)

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
        active_count = sum(
            self.approvals.has_active_target(result.url)
            for result in combined_results
            if result.url
        )
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
                    f"Selected with {fallback_level} to fill a two-reply batch. "
                    f"Skipped {active_count} already-used post(s) before ranking.\n",
                )
            note = (
                "Selected by momentum across the requested topic and languages.\n"
                if query
                else "Selected by momentum across current topics and languages.\n"
            )
            return (
                selected_query,
                results,
                note.rstrip()
                + f" Skipped {active_count} already-used post(s) before ranking.\n",
            )

        diagnostic = (
            f"Fetched {len(combined_results)} unique root posts from "
            f"{len(searched)} successful search responses; {len(search_failures)} "
            f"search lane(s) failed. Skipped {active_count} already-used post(s) before "
            "ranking. Fewer than two posts remained after the age, "
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
            "Target mix: up to two Japanese videos first, then the strongest "
            "non-Japanese global video when available."
        )
        # A typical cookie-only VPS has one usable twscrape account. Run video
        # lanes serially so Top/Latest searches cannot lease the same small
        # account pool concurrently and make every lane report unavailable.
        semaphore = asyncio.Semaphore(1)
        search_failures: list[str] = []

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
            except Exception as exc:
                search_failures.append(
                    f"{label}/{product}: "
                    f"{_truncate_text(_exception_detail(exc), 180)}"
                )
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
            samples = " | ".join(search_failures[:3])
            raise RuntimeError(
                "Every /replyvideo X search lane failed. This is an X/twscrape "
                "account or transport problem; no Gemini job was started. "
                f"First errors: {samples or 'no response detail'}"
            )
        combined, search_query_by_url = _combine_reply_target_results(searched)
        combined = self.reply_target_metrics.observe(combined)
        active_count = sum(
            self.approvals.has_active_target(result.url)
            for result in combined
            if result.url
        )
        # Remove previously-carded posts before each top-N ranking pass. If the
        # filter happens afterwards, the same hot posts occupy all 24 slots and
        # hide slightly lower-ranked unused videos forever.
        combined = [
            result
            for result in combined
            if not self.approvals.has_active_target(result.url)
        ]
        configured_floor = max(0, self.settings.reply_video_min_views)
        strict = rank_viral_video_posts(
            combined,
            max_items=max(24, len(combined)),
            max_age_minutes=strict_age,
            min_view_count=configured_floor,
            min_like_count_when_views_missing=150,
            min_view_velocity=300.0,
        )
        warm_floor = min(configured_floor, max(2_000, configured_floor // 5))
        warm = rank_viral_video_posts(
            combined,
            max_items=max(24, len(combined)),
            max_age_minutes=strict_age,
            min_view_count=warm_floor,
            min_like_count_when_views_missing=60,
            min_view_velocity=60.0,
        )
        fill_floor = min(warm_floor, max(500, configured_floor // 30))
        fill = rank_viral_video_posts(
            combined,
            max_items=max(24, len(combined)),
            max_age_minutes=fill_age,
            min_view_count=fill_floor,
            min_like_count_when_views_missing=20,
            min_view_velocity=0.0,
        )
        candidates = _merge_reply_target_search_products([strict, warm, fill])
        # Keep the second check for the small race where a card is created by
        # another run while ranking is in progress.
        candidates = [
            result
            for result in candidates
            if not self.approvals.has_active_target(result.url)
        ]
        candidates, author_limit_skips, language_limit_skips = (
            self._filter_reply_generation_candidates(candidates)
        )
        selected = _select_reply_video_mix(
            candidates,
            max_items=8,
        )
        selected = await self._enrich_reply_thread_context(selected)
        selected = self._apply_reply_target_mode(
            selected,
            self.settings.reply_target_mode,
        )
        # Goal and safety scoring can reorder the enriched pool. Restore the
        # video-specific language mix afterwards so Japanese remains a real
        # preference rather than only an earlier search hint. /replytargets
        # keeps its existing language-balanced ranking.
        selected = _select_reply_video_mix(selected, max_items=8)
        self.reply_watch.classify(selected, source_type="replyvideo")
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
        return (
            search_label,
            selected,
            f"{tier} Skipped {active_count} already-used, "
            f"{author_limit_skips} author-limited, and "
            f"{language_limit_skips} Japanese-limit video(s) before drafting.",
        )

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
            verified_replier_ratio = (
                sum(bool(item.author_verified) for item in non_author) / len(non_author)
                if non_author
                else 0.0
            )
            return replace(
                result,
                top_reply_like_count=top_reply_likes,
                root_author_has_replied=author_has_replied,
                verified_replier_ratio=verified_replier_ratio,
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
        goal = self.settings.creator_goal
        watched_authors = set(self.revenue_ops.watch_authors())
        risk_mode = self.revenue_ops.risk_mode
        for result in results:
            safety = assess_monetization_safety(result)
            if safety.level == MONETIZATION_RED and (
                goal == "earn" or risk_mode in {"strict", "balanced"}
            ):
                continue
            if safety.level == MONETIZATION_YELLOW and risk_mode == "strict":
                continue
            if (
                str(result.language or "").casefold().startswith("ja")
                and risk_mode == "balanced"
                and {
                    "disaster or tragedy",
                    "war, conflict, or graphic violence",
                }
                & set(safety.reasons)
            ):
                # These conversations are especially sensitive to opportunistic
                # viral replies. Balanced mode leaves political/current-affairs
                # discussion available but avoids tragedy/war impression chasing.
                continue
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
            top_reply_competition = min(
                100.0,
                math.log1p(max(0, result.top_reply_like_count))
                / math.log1p(10_000)
                * 100.0,
            )
            rankability = (
                result.thread_availability_score * 0.55
                + (100.0 - top_reply_competition) * 0.30
                + (100.0 - result.reply_saturation_penalty) * 0.15
            )
            premium_audience = min(
                100.0,
                (45.0 if result.author_verified else 8.0)
                + result.verified_replier_ratio * 45.0
                + affinity * 0.10,
            )
            watched_author = result.username.casefold() in watched_authors
            if goal == "earn":
                goal_score = (
                    score * 0.35
                    + premium_audience * 0.35
                    + rankability * 0.15
                    + affinity * 0.15
                )
            elif goal == "network":
                goal_score = (
                    score * 0.30
                    + relationship * 0.35
                    + rankability * 0.20
                    + affinity * 0.10
                    + (5.0 if result.root_author_has_replied else 0.0)
                )
            else:
                goal_score = (
                    score * 0.55
                    + result.viral_score * 0.20
                    + rankability * 0.20
                    + premium_audience * 0.05
                )
            if watched_author:
                goal_score += 10.0 if goal == "network" else 5.0
            if safety.level == MONETIZATION_YELLOW:
                goal_score -= 28.0 if goal == "earn" else 14.0
            elif safety.level == MONETIZATION_RED:
                goal_score -= 35.0
            source_type = "replyvideo" if result.has_video else "replytargets"
            learned_multiplier = self.reply_learning.performance_adjustment(
                language=result.language,
                source_type=source_type,
                hour_utc=datetime.now(UTC).hour,
            )
            goal_score = min(100.0, max(0.0, goal_score * learned_multiplier))
            adjusted.append(
                replace(
                    result,
                    reply_opportunity_score=goal_score,
                    audience_affinity_score=affinity,
                    relationship_score=relationship,
                    rankability_score=rankability,
                    premium_audience_score=premium_audience,
                    verified_audience_proxy=premium_audience,
                    monetization_safety_score=safety.score,
                    monetization_risk_level=safety.level,
                    monetization_risk_reasons=safety.reasons,
                    watched_author=watched_author,
                    goal_score=goal_score,
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
        # Exclude active targets before max_items truncation. Otherwise the
        # hottest five already-used posts can starve all fresh candidates.
        available_results = [
            result
            for result in recent_results
            if not self.approvals.has_active_target(result.url)
        ]
        ranked = rank_fast_growing_posts(
            available_results,
            # Rank the complete fetched pool, then apply approval-time safety
            # limits and finally take five. This lets lower-ranked languages
            # and authors replace otherwise blocked top results.
            max_items=max(5, len(available_results)),
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
        eligible, _author_skips, _language_skips = (
            self._filter_reply_generation_candidates(unseen, max_items=5)
        )
        return eligible

    async def _auto_reply_target_queries(
        self,
        languages: list[str] | tuple[str, ...] | str | None = None,
        *,
        mode: str = "balanced",
    ) -> list[str]:
        # /replytargets starts broad for reach, then adds the configured creator
        # niche in balanced/qualified modes so one command covers both lanes.
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
        # Priority-author lanes are intentionally first so the execution budget
        # cannot be consumed entirely by generic trends. They still pass the
        # same freshness, competition, safety, and deduplication gates.
        for username in reversed(self.revenue_ops.query_watch_authors(limit=4)):
            queries.insert(
                0,
                _query_for_languages(
                    f"from:{username} -filter:replies -filter:retweets",
                    selected_languages,
                ),
            )
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
            paused_now = self.revenue_ops.record_health_error(notification)
            try:
                await message.reply_text(
                    notification
                    + (
                        "\n\nNew reply-card generation was paused after three X account "
                        "errors within one hour. Tracking remains active. Run /setupcheck, "
                        "then /pace resume."
                        if paused_now
                        else ""
                    )
                )
            except Exception:
                return

def _menu_keyboard(menu_name: str = "main") -> ReplyKeyboardMarkup:
    layout = MENU_LAYOUTS.get(menu_name, MENU_LAYOUTS["main"])
    return ReplyKeyboardMarkup(
        [list(row) for row in layout],
        resize_keyboard=True,
        is_persistent=False,
        one_time_keyboard=True,
        input_field_placeholder="Choose a feature from the menu...",
    )


async def _set_bot_commands(app: Application) -> None:
    await app.bot.set_my_commands(BOT_COMMANDS)


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
            "root_author_verified": result.author_verified,
            "root_views": result.view_count,
            "root_replies": result.reply_count,
            "reply_opportunity_score": result.reply_opportunity_score,
            "viral_score": result.viral_score,
            "top_reply_like_count": result.top_reply_like_count,
            "root_author_has_replied": result.root_author_has_replied,
            "has_video": result.has_video,
            "video_context_quality": result.video_context_quality,
            "visual_frame_count": len(result.visual_frame_names or []),
            "rankability_score": result.rankability_score,
            "premium_audience_score": result.premium_audience_score,
            "verified_audience_proxy": (
                result.verified_audience_proxy or result.premium_audience_score
            ),
            "verified_replier_ratio": result.verified_replier_ratio,
            "monetization_safety_score": result.monetization_safety_score,
            "monetization_risk_level": result.monetization_risk_level,
            "monetization_risk_reasons": list(result.monetization_risk_reasons),
            "watched_author": result.watched_author,
            "candidate_age_minutes_at_card": _candidate_age_minutes(result),
            "view_velocity_score": result.view_velocity_score,
            "recent_view_velocity_score": result.recent_view_velocity_score,
            "thread_availability_score": result.thread_availability_score,
            "reply_saturation_penalty": result.reply_saturation_penalty,
            "views_per_reply": result.views_per_reply,
            "goal_score": result.goal_score,
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
        risk_level = str(metadata.get("monetization_risk_level") or "green")
        if risk_level in {"yellow", "red"}:
            reasons = ", ".join(
                str(item) for item in metadata.get("monetization_risk_reasons", [])
            )
            blocks.append(
                f"Revenue safety: {risk_level.upper()}"
                + (f" - {reasons}" if reasons else "")
            )
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
                    InlineKeyboardButton(
                        "Why?",
                        callback_data=f"automation:why:{approval.id}",
                    ),
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


def _target_explanation(approval: AutomationApproval) -> str:
    metadata = approval.metadata or {}
    risk_reasons = ", ".join(
        str(item) for item in metadata.get("monetization_risk_reasons", [])
    ) or "none detected"
    watched = "yes" if metadata.get("watched_author") else "no"
    return (
        "Why this target\n"
        f"- Goal score: {float(metadata.get('goal_score') or 0.0):.0f}/100\n"
        f"- Reply rankability: {float(metadata.get('rankability_score') or 0.0):.0f}/100\n"
        f"- Verified-audience proxy: "
        f"{float(metadata.get('verified_audience_proxy') or metadata.get('premium_audience_score') or 0.0):.0f}/100\n"
        f"- Thread availability: {float(metadata.get('thread_availability_score') or 0.0):.0f}/100\n"
        f"- Views per competing reply: {float(metadata.get('views_per_reply') or 0.0):,.0f}\n"
        f"- Post age when card was built: "
        f"{float(metadata.get('candidate_age_minutes_at_card') or 0.0):.0f} minutes\n"
        f"- Watched author: {watched}\n"
        f"- Revenue safety: {str(metadata.get('monetization_risk_level') or 'green').upper()} "
        f"({risk_reasons})\n"
        f"- Format experiment: {metadata.get('experiment_variant') or 'adaptive'}\n\n"
        "Scores are public-data proxies, not X payout or private Home-impression data."
    )


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
    """Prefer two Japanese videos, then retain global language diversity."""
    if max_items <= 0:
        return []
    japanese = [item for item in results if item.language.casefold() == "ja"]
    non_japanese = [item for item in results if item.language.casefold() != "ja"]
    selected = japanese[: min(2, max_items)]
    if non_japanese and len(selected) < max_items:
        selected.append(non_japanese[0])
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
    return sum(
        1
        for approval in approvals
        if _is_approved_reply_card(approval)
        and approval.decided_at is not None
        and approval.decided_at.astimezone(timezone).date() == today
    )


def _author_approvals_created_today(
    approvals: list[AutomationApproval],
    *,
    username: str,
    timezone_name: str,
) -> int:
    clean = str(username or "").strip().lstrip("@").casefold()
    if not clean:
        return 0
    try:
        timezone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        timezone = UTC
    today = datetime.now(timezone).date()
    return sum(
        1
        for approval in approvals
        if _is_approved_reply_card(approval)
        and approval.decided_at is not None
        and approval.decided_at.astimezone(timezone).date() == today
        and str((approval.metadata or {}).get("root_author") or "")
        .lstrip("@")
        .casefold()
        == clean
    )


def _language_approvals_created_today(
    approvals: list[AutomationApproval],
    *,
    language: str,
    timezone_name: str,
) -> int:
    clean_language = str(language or "").strip().casefold()
    try:
        timezone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        timezone = UTC
    today = datetime.now(timezone).date()
    return sum(
        1
        for approval in approvals
        if _is_approved_reply_card(approval)
        and approval.decided_at is not None
        and approval.decided_at.astimezone(timezone).date() == today
        and str((approval.metadata or {}).get("language") or "")
        .casefold()
        .startswith(clean_language)
    )


def _language_approvals_created_since(
    approvals: list[AutomationApproval],
    *,
    language: str,
    since: datetime,
) -> int:
    clean_language = str(language or "").strip().casefold()
    return sum(
        1
        for approval in approvals
        if _is_approved_reply_card(approval)
        and approval.decided_at is not None
        and approval.decided_at >= since
        and str((approval.metadata or {}).get("language") or "")
        .casefold()
        .startswith(clean_language)
    )


def _recent_reply_texts(
    approvals: list[AutomationApproval],
    *,
    limit: int = 120,
) -> list[str]:
    rows = sorted(approvals, key=lambda approval: approval.created_at, reverse=True)
    return [
        approval.text
        for approval in rows
        if approval.kind == "reply" and approval.text.strip()
    ][: max(0, limit)]


def _pending_reply_delivery_count(
    approvals: list[AutomationApproval],
    *,
    source_type: str = "",
) -> int:
    clean_source = str(source_type or "").strip().casefold()
    return sum(
        1
        for approval in approvals
        if approval.kind == "reply"
        and approval.status == "pending"
        and bool((approval.metadata or {}).get("reply_delivery_queue_id"))
        and (
            not clean_source
            or str((approval.metadata or {}).get("source_type") or "")
            .casefold()
            == clean_source
        )
    )


def _select_diverse_candidates(
    results: list[XSearchResult],
    limit: int,
) -> list[XSearchResult]:
    """Avoid one author/language consuming a batch when alternatives exist."""
    selected: list[XSearchResult] = []
    seen_authors: set[str] = set()
    language_counts: dict[str, int] = {}
    language_cap = max(2, round(max(1, limit) * 0.60))
    for result in results:
        author = result.username.casefold()
        language = (result.language or "unknown").casefold()
        if author in seen_authors or language_counts.get(language, 0) >= language_cap:
            continue
        selected.append(result)
        seen_authors.add(author)
        language_counts[language] = language_counts.get(language, 0) + 1
        if len(selected) >= limit:
            return selected
    for result in results:
        if result in selected:
            continue
        selected.append(result)
        if len(selected) >= limit:
            break
    return selected


def _reply_approvals_created_since(
    approvals: list[AutomationApproval],
    *,
    since: datetime,
) -> int:
    return sum(
        _is_approved_reply_card(approval)
        and approval.decided_at is not None
        and approval.decided_at >= since
        for approval in approvals
    )


def _is_approved_reply_card(approval: AutomationApproval) -> bool:
    """Return whether a reply card has passed an explicit approval decision."""
    return (
        approval.kind == "reply"
        and approval.decided_at is not None
        and approval.status != "rejected"
    )


def _candidate_age_minutes(result: XSearchResult) -> float:
    if result.created_at_timestamp is None:
        return 0.0
    return round(
        max(0.0, (datetime.now(UTC).timestamp() - result.created_at_timestamp) / 60),
        1,
    )


def _parse_report_days(raw: str, *, allowed: tuple[int, ...]) -> int:
    clean = str(raw or "").strip().lower()
    if clean.endswith("d"):
        clean = clean[:-1]
    try:
        days = int(clean)
    except ValueError as exc:
        raise RuntimeError(
            "Report window must be one of: " + ", ".join(f"{day}d" for day in allowed)
        ) from exc
    if days not in allowed:
        raise RuntimeError(
            "Report window must be one of: " + ", ".join(f"{day}d" for day in allowed)
        )
    return days


def _is_semantic_duplicate(text: str, existing: list[str], *, threshold: float = 0.80) -> bool:
    normalize = lambda value: re.sub(r"\W+", " ", value.casefold()).strip()
    candidate = normalize(text)
    if not candidate:
        return True
    compact_candidate = candidate.replace(" ", "")
    candidate_ngrams = {
        compact_candidate[index : index + 3]
        for index in range(max(0, len(compact_candidate) - 2))
    }
    for previous in existing:
        if not previous.strip():
            continue
        normalized_previous = normalize(previous)
        if SequenceMatcher(None, candidate, normalized_previous).ratio() >= threshold:
            return True
        compact_previous = normalized_previous.replace(" ", "")
        previous_ngrams = {
            compact_previous[index : index + 3]
            for index in range(max(0, len(compact_previous) - 2))
        }
        union = candidate_ngrams | previous_ngrams
        if union and len(candidate_ngrams & previous_ngrams) / len(union) >= 0.72:
            return True
    return False


def _summarize_mixed_reply_context(results: list[XSearchResult]) -> str:
    blocks: list[str] = []
    for index, result in enumerate(results[:5], start=1):
        context = (
            summarize_reply_video_context([result], max_items=1)
            if result.has_video
            else summarize_reply_target_context([result], max_items=1)
        )
        blocks.append(f"Mixed candidate {index}:\n{context}")
    return "\n\n".join(blocks)


def _select_session_mix(
    targets: list[XSearchResult],
    videos: list[XSearchResult],
    *,
    max_items: int,
    video_share: float = 0.60,
) -> list[XSearchResult]:
    """Use learned source allocation while retaining exploration in both lanes."""
    limit = min(5, max(2, max_items))
    share = max(0.20, min(0.80, float(video_share)))
    video_slots = min(len(videos), max(1, round(limit * share)))
    target_slots = min(len(targets), limit - video_slots)
    selected = videos[:video_slots] + targets[:target_slots]
    relationship_pool = sorted(
        [
            result
            for result in [*videos, *targets]
            if result.watched_author or result.relationship_score > 0
        ],
        key=lambda result: (
            result.watched_author,
            result.relationship_score,
            result.goal_score or result.reply_opportunity_score,
        ),
        reverse=True,
    )
    if relationship_pool and relationship_pool[0] not in selected and selected:
        # Reserve roughly one slot in a five-card session for relationship
        # building, while avoiding removal of the only item from either lane.
        counts = {
            True: sum(item.has_video for item in selected),
            False: sum(not item.has_video for item in selected),
        }
        replaceable = [
            item for item in selected if counts[item.has_video] > 1
        ] or list(selected)
        weakest = min(
            replaceable,
            key=lambda item: item.goal_score or item.reply_opportunity_score,
        )
        selected[selected.index(weakest)] = relationship_pool[0]
    pool = sorted(
        [*videos[video_slots:], *targets[target_slots:]],
        key=lambda result: (
            result.goal_score or result.reply_opportunity_score,
            result.rankability_score,
            result.viral_score,
        ),
        reverse=True,
    )
    seen = {result.url or str(result.id) for result in selected}
    for result in pool:
        key = result.url or str(result.id)
        if key in seen:
            continue
        seen.add(key)
        selected.append(result)
        if len(selected) >= limit:
            break
    return sorted(
        selected[:limit],
        key=lambda result: (
            result.goal_score or result.reply_opportunity_score,
            result.rankability_score,
        ),
        reverse=True,
    )


def _latest_reply_session_id(
    approvals: list[AutomationApproval],
    *,
    chat_id: int,
) -> str:
    candidates = [
        approval
        for approval in approvals
        if approval.chat_id == int(chat_id)
        and str((approval.metadata or {}).get("reply_session_id") or "")
        and approval.status == "pending"
    ]
    if not candidates:
        return ""
    latest = max(candidates, key=lambda approval: approval.created_at)
    return str((latest.metadata or {}).get("reply_session_id") or "")


def _format_performance_dimension(
    values: dict[str, dict[str, float | int]],
    *,
    limit: int = 4,
) -> str:
    if not values:
        return "- Not enough measured data"
    return "\n".join(
        f"- {name}: n={int(stats['count'])}, score "
        f"{float(stats['average_score']):.1f}, median {int(stats['median_views']):,} views"
        for name, stats in list(values.items())[:limit]
    )


def _format_duration(seconds: int | float) -> str:
    value = max(0, int(seconds or 0))
    if value <= 0:
        return "n/a"
    if value < 60:
        return f"{value}s"
    if value < 3600:
        return f"{round(value / 60)}m"
    return f"{value / 3600:.1f}h"


def _performance_recommendation(report: dict[str, Any]) -> str:
    measured = int(report.get("measured") or 0)
    if measured < 10:
        return "Keep collecting outcomes; at least 10 measured replies are needed for a stable split."
    languages = report.get("by_language") or {}
    sources = report.get("by_source") or {}
    best_language = next(iter(languages), "current languages")
    best_source = next(iter(sources), "current source mix")
    return (
        f"Allocate about 15% more exploration to {best_language} and {best_source}, "
        "while retaining at least 10% exploration elsewhere."
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
    if (
        "X search failed" in text
        or "reply-target search lanes failed" in text
        or "/replyvideo X search lane failed" in text
    ):
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
        detail = _strip_exception_prefix(text).removesuffix(" <- TimeoutError")
        return (
            f"{detail}\n\n"
            "Reload Chrome extension 0.8.8 or newer. It bounds a stalled Gemini "
            "attempt and retries once on a fresh managed tab instead of trusting "
            "heartbeat activity alone."
        )
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
