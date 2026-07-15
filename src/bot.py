from __future__ import annotations

import asyncio
import logging
from dataclasses import replace
from io import BytesIO
from typing import Any
from urllib.parse import urlencode

from telegram import (
    BotCommand,
    CopyTextButton,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.constants import ChatAction
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

from src.ai_service import create_ai_service
from src.automation import AutomationApproval, AutomationApprovalStore
from src.config import Settings
from src.env_store import update_env_value
from src.models import GeneratedContent, ReplyTargetDraft, TrendPostVariant, XSearchResult
from src.trend_source_service import TrendSourceService, summarize_trend_signals
from src.x_search_service import (
    MIN_REPLY_TARGET_ENGAGEMENT_SCORE,
    MIN_REPLY_TARGET_VELOCITY_SCORE,
    MIN_REPLY_TARGET_VIEW_COUNT,
    TREND_FALLBACK_QUERIES,
    XSearchService,
    default_english_query,
    extract_tweet_id,
    format_x_results,
    rank_fast_growing_posts,
    summarize_reply_target_context,
    summarize_x_context,
)


LOGGER = logging.getLogger(__name__)

AUTO_TREND_CATEGORIES = ("trending", "news", "entertainment", "sport")
AUTO_REPLY_TARGET_FALLBACK_QUERIES = (
    "AI",
    "OpenAI",
    "crypto",
    "business",
    "technology",
    "sports",
    "entertainment",
    "internet culture",
)
REPLY_TARGET_MAX_CANDIDATES = 8
REPLY_TARGET_RESULT_LIMIT = 20
REPLY_TARGET_CONTEXT_ITEMS = 3
TREND_CONTEXT_SIGNAL_ITEMS = 3
TREND_CONTEXT_X_ITEMS = 4
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

BOT_COMMANDS = [
    BotCommand("start", "Show help and available commands"),
    BotCommand("help", "Show help and available commands"),
    BotCommand("tweet", "Generate a Vietnamese X post and image from a topic"),
    BotCommand("tweetx", "Generate an English X post using live X search context"),
    BotCommand("tweettrend3", "Auto-pick or choose a trend and generate 3 Vietnamese posts"),
    BotCommand("dailybrief", "Generate daily tweet options with images"),
    BotCommand("retweet", "Remix an X post into an original tweet and image"),
    BotCommand("replytargets", "Auto-pick or search X posts to reply to"),
    BotCommand("persona", "Show or set creator niche, voice, and audience"),
    BotCommand("importcookie", "Save X auth_token and ct0 cookie for X search"),
    BotCommand("xaccounts", "Show imported X cookie accounts"),
    BotCommand("xremove", "Remove an imported X cookie account"),
    BotCommand("reply", "Generate a witty reply from tweet text or an X post link"),
    BotCommand("automationhere", "Send scheduled approval requests to this chat"),
    BotCommand("replyevery", "Set scheduled replytargets interval in minutes"),
]


class ContentBot:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.ai = create_ai_service(settings)
        self.x_search = XSearchService(settings)
        self.trend_sources = TrendSourceService(settings, self.x_search)
        self._x_account_error_notices: dict[str, str] = {}
        self.approvals = AutomationApprovalStore(settings.automation_approvals_path)
        self.approval_chat_id = settings.telegram_approval_chat_id
        self._application: Application | None = None
        self._automation_running: set[str] = set()
        self._automation_tasks: set[asyncio.Task[None]] = set()

    def build_application(self) -> Application:
        async def post_init(app: Application) -> None:
            self._application = app
            await _set_bot_commands(app)
            bridge = getattr(self.ai, "bridge", None)
            if bridge is not None:
                bridge.set_automation_handler(self)
                await bridge.start()

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
        app.add_handler(CommandHandler("start", self.start))
        app.add_handler(CommandHandler("help", self.start))
        app.add_handler(CommandHandler("tweet", self.tweet))
        app.add_handler(CommandHandler("tweetx", self.tweetx))
        app.add_handler(CommandHandler("tweettrend3", self.tweettrend3))
        app.add_handler(CommandHandler("dailybrief", self.dailybrief))
        app.add_handler(CommandHandler("retweet", self.retweet))
        app.add_handler(CommandHandler("replytargets", self.replytargets))
        app.add_handler(CommandHandler("persona", self.persona))
        app.add_handler(CommandHandler("importcookie", self.importcookie))
        app.add_handler(CommandHandler("xaccounts", self.xaccounts))
        app.add_handler(CommandHandler("xremove", self.xremove))
        app.add_handler(CommandHandler("reply", self.reply))
        app.add_handler(CommandHandler("automationhere", self.automationhere))
        app.add_handler(CommandHandler("replyevery", self.replyevery))
        app.add_handler(
            CallbackQueryHandler(self.automation_approval, pattern=r"^automation:")
        )
        return app

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        del context
        await update.effective_message.reply_text(
            "Commands:\n"
            "/tweet <topic> - generate a Vietnamese X post and image\n"
            "/tweetx <topic/search> - generate an English X post using live X context\n"
            "/tweettrend3 [auto|trending|news|sport|entertainment] - generate 3 Vietnamese trend angles\n"
            "/dailybrief [trending|news|sport|entertainment] - generate daily tweets with images\n"
            "/retweet <X post link> - remix an X post into an original tweet and image\n"
            "/replytargets [query] - auto-pick or search posts to reply to\n"
            "/persona - show or set niche, voice, and target audience\n"
            "/importcookie <auth_token=...; ct0=...> - save X cookie for search\n"
            "/xaccounts - show imported X cookie accounts\n"
            "/xremove <account_name> - remove an imported X cookie account\n"
            "/reply <tweet text or X post link> - generate a copy-ready reply\n"
            "/automationhere - send scheduled approval requests to this chat\n"
            "/replyevery <minutes> - set the scheduled /replytargets interval\n"
            "\n"
            "AI provider: Chrome extension bridge runs Gemini for all "
            "content commands."
        )

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
            "Use /replyevery <minutes> to configure /replytargets from Telegram."
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
                "Only the chat configured with /automationhere can change this schedule."
            )
            return
        if not context.args:
            current = self.settings.telegram_reply_targets_minutes
            value = f"{current} minutes" if current is not None else "Chrome extension setting"
            await message.reply_text(
                f"Current /replytargets interval: {value}.\n"
                "Set it with /replyevery 30 (minimum 5 minutes)."
            )
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
        )
        update_env_value("TELEGRAM_REPLY_TARGETS_MINUTES", str(minutes))
        await message.reply_text(
            f"Scheduled /replytargets interval set to {minutes} minutes. "
            "Chrome will sync it within about 30 seconds."
        )

    async def get_automation_config(self) -> dict[str, Any]:
        return {
            "reply_targets_minutes": self.settings.telegram_reply_targets_minutes,
        }

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
        if decision not in {"approve", "mobile", "reject"}:
            await query.answer("Unknown approval action.", show_alert=True)
            return
        answered = False
        try:
            approval = self.approvals.decide(
                approval_id,
                approve=decision in {"approve", "mobile"},
                chat_id=query.message.chat.id,
                user_id=query.from_user.id,
                destination="mobile",
            )
            await query.answer()
            answered = True
            original = str(query.message.text or "").strip()
            mobile_note = _mobile_approval_note(approval)
            await query.edit_message_text(
                (
                    f"{original}\n\n{mobile_note}".strip()
                    if approval.status == "mobile_approved"
                    else f"{original}\n\nRejected."
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
        interval_minutes = _reply_target_interval_minutes(
            payload.get("reply_targets_minutes"),
            default=self.settings.telegram_reply_targets_minutes or 30,
        )
        return self._spawn_automation(
            "replytargets",
            lambda: self._run_scheduled_replytargets(query, interval_minutes),
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
            detail = "Reply draft" if approval.kind == "reply" else "Post draft"
            text = f"{detail} opened and filled in X. Review it, then click the final X button."
        else:
            text = f"Could not fill the approved {approval.kind} in X: {error or 'unknown error'}"
        await self._application.bot.send_message(chat_id=approval.chat_id, text=text)

    def _spawn_automation(self, kind: str, factory) -> dict[str, Any]:
        if self._application is None:
            raise RuntimeError("Telegram bot is not ready.")
        if self.approval_chat_id is None:
            raise RuntimeError("No approval chat configured. Send /automationhere in Telegram first.")
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
        interval_minutes: int,
    ) -> None:
        if self._application is None or self.approval_chat_id is None:
            raise RuntimeError("Automation chat is not ready.")
        status = _SilentStatus()
        search_query, results, _auto_note = await self._get_reply_target_context(
            query,
            status,
            interval_minutes=interval_minutes,
        )
        if not results:
            await self._application.bot.send_message(
                chat_id=self.approval_chat_id,
                text=_no_reply_targets_message(
                    search_query,
                    auto=not query,
                    interval_minutes=interval_minutes,
                ),
            )
            return
        drafts = await self.ai.generate_reply_targets(
            search_query,
            summarize_reply_target_context(results, max_items=REPLY_TARGET_CONTEXT_ITEMS),
        )
        for draft in drafts:
            target_url = _format_reply_target_link(draft)
            if self.approvals.has_active_target(target_url):
                continue
            approval = self.approvals.create(
                kind="reply",
                text=_format_reply_target_reply(draft),
                chat_id=self.approval_chat_id,
                approver_user_id=self.approval_chat_id,
                target_url=target_url,
                target_label=draft.target,
            )
            await self._send_approval(approval, reason=draft.reason)

    async def _run_scheduled_tweettrend3(self, category: str) -> None:
        if self._application is None or self.approval_chat_id is None:
            raise RuntimeError("Automation chat is not ready.")
        status = _SilentStatus()
        contexts = await self._get_trend_contexts_for_tweettrend3(category, status)
        for topic, x_context, source, selected_category in contexts:
            generated = await self.ai.generate_trend_post(
                topic,
                x_context,
                output_language="Vietnamese",
            )
            approval = self.approvals.create(
                kind="post",
                text=generated.text,
                chat_id=self.approval_chat_id,
                approver_user_id=self.approval_chat_id,
                target_label=topic,
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
            await message.reply_text("Usage: /tweet <topic>")
            return

        await message.chat.send_action(ChatAction.TYPING)
        status = await message.reply_text("Writing a Vietnamese post from your topic...")
        try:
            generated = await self.ai.generate_topic_post(topic)
            await status.delete()
            await message.reply_text(f"Topic: {generated.topic}\n\nPost:\n{generated.text}")
            await self._send_optional_image(message, generated, "topic")
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
            await status.edit_text(
                "Writing 3 Vietnamese posts from 3 different hot topics..."
            )

            await status.edit_text(
                f"Language: {output_language}\n"
                "Sending tweet options with approval buttons and images..."
            )
            approver_user_id = (
                update.effective_user.id if update.effective_user is not None else message.chat.id
            )
            for index, (topic, x_context, source, selected_category) in enumerate(
                contexts,
                start=1,
            ):
                generated = await self.ai.generate_trend_post(
                    topic,
                    x_context,
                    output_language=output_language,
                )
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
                "Three topic-based tweet drafts sent."
            )
        except Exception as exc:
            await status.edit_text(_friendly_error(exc))
        finally:
            await self._notify_x_account_errors(message)

    async def retweet(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.effective_message
        source, visual_note = _parse_retweet_args(_command_payload(message, context))
        if not source:
            await message.reply_text(
                "Usage: /retweet <X post link> | <visual description>"
            )
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
            await message.reply_text(
                f"Source: {result.url}\n\n"
                f"Remix tweet:\n{generated.text}"
            )
            await self._send_optional_image(message, generated, "remix")
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
                "Sending daily tweet options with images..."
            )
            for index, variant in enumerate(variants, start=1):
                await self._send_trend_variant(
                    message,
                    variant,
                    index,
                    label="Daily tweet",
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
            await message.reply_text("Usage: /tweetx <topic or X search query>")
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
            await message.reply_text(f"Topic: {generated.topic}\n\nTweet:\n{generated.text}")
            await self._send_optional_image(message, generated, "topic")
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

        await message.chat.send_action(ChatAction.TYPING)
        interval_minutes = self.settings.telegram_reply_targets_minutes or 30
        status = await message.reply_text(
            f"Finding fast-moving reply targets from the last {interval_minutes} minutes..."
            if query
            else (
                "Auto-picking a hot topic, then finding reply targets from the last "
                f"{interval_minutes} minutes..."
            )
        )
        try:
            search_query, results, auto_note = await self._get_reply_target_context(
                query,
                status,
                interval_minutes=interval_minutes,
            )
            if not results:
                await status.edit_text(
                    _no_reply_targets_message(
                        search_query,
                        auto=not query,
                        interval_minutes=interval_minutes,
                    )
                )
                return

            await status.edit_text("Drafting high-signal replies...")
            drafts = await self.ai.generate_reply_targets(
                search_query,
                summarize_reply_target_context(
                    results,
                    max_items=REPLY_TARGET_CONTEXT_ITEMS,
                ),
            )
            del auto_note
            await status.delete()
            approver_user_id = (
                update.effective_user.id if update.effective_user is not None else message.chat.id
            )
            for draft in drafts:
                target_url = _format_reply_target_link(draft)
                if self.approvals.has_active_target(target_url):
                    continue
                approval = self.approvals.create(
                    kind="reply",
                    text=_format_reply_target_reply(draft),
                    chat_id=message.chat.id,
                    approver_user_id=approver_user_id,
                    target_url=target_url,
                    target_label=draft.target,
                )
                await self._send_approval(approval, reason=draft.reason)
        except Exception as exc:
            await status.edit_text(_friendly_error(exc))
        finally:
            await self._notify_x_account_errors(message)

    async def persona(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.effective_message
        raw_args = " ".join(context.args).strip()
        if not raw_args:
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
            self.x_search = XSearchService(self.settings)
            self.trend_sources = TrendSourceService(self.settings, self.x_search)
            await message.reply_text(f"Persona updated.\n\n{_format_persona(self.settings)}")
        except Exception as exc:
            await message.reply_text(_friendly_error(exc))

    async def importcookie(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.effective_message
        raw_args = " ".join(context.args).strip()
        if not raw_args:
            await message.reply_text(
                "Usage:\n"
                "/importcookie auth_token=YOUR_AUTH_TOKEN; ct0=YOUR_CT0\n"
                "/importcookie account2 auth_token=YOUR_AUTH_TOKEN; ct0=YOUR_CT0\n\n"
                "Open x.com in a logged-in browser, copy the auth_token and ct0 cookies, "
                "then paste them in this command.",
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("Open X", url="https://x.com")]]
                ),
            )
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
            await message.reply_text("Usage: /xremove <account_name>\nExample: /xremove account2")
            return

        status = await message.reply_text(f"Removing X account: {account_name}...")
        try:
            removed_name = await self.x_search.remove_cookie_account(account_name)
            self._x_account_error_notices.pop(removed_name, None)
            if removed_name == self.settings.x_account_name:
                update_env_value("X_COOKIE", "")
                self.settings = replace(self.settings, x_cookie="")
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
            await message.reply_text("Usage: /reply <tweet text or X post link>")
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
            for selected_category in categories:
                await status.edit_text(
                    f"Scanning X, Google Trends, and RSS sources for {selected_category}..."
                )
                signals, errors = await self.trend_sources.collect(selected_category)
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

        contexts: list[tuple[str, str, str, str]] = []
        for signal, selected_category, signals, errors in selected:
            await status.edit_text(
                f"Enriching trend {len(contexts) + 1}/{len(selected)}: {signal.title}"
            )
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
            contexts.append(
                (
                    signal.title,
                    "\n\n".join(context_parts),
                    f"multi-source trend scan ({signal.source})",
                    selected_category,
                )
            )

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
        interval_minutes: int = 30,
    ) -> tuple[str, list[XSearchResult], str]:
        candidates = _dedupe_queries(
            ([query] if query else []) + await self._auto_reply_target_queries()
        )[:REPLY_TARGET_MAX_CANDIDATES]
        last_search_query = query or "auto hot topics"

        for relaxed in (False, True):
            for candidate in candidates:
                await status.edit_text(
                    "Finding a usable reply target...\n"
                    f"Trying: {candidate}\n"
                    f"Mode: {'wider fallback' if relaxed else 'fresh high-signal'}"
                )
                try:
                    search_query, results = await self._search_rank_reply_targets(
                        candidate,
                        relaxed=relaxed,
                        interval_minutes=interval_minutes,
                    )
                except Exception:
                    continue
                last_search_query = search_query
                if results:
                    note = (
                        "Auto-selected a wider fallback topic.\n"
                        if relaxed or (query and candidate != query)
                        else "Auto-selected topic.\n"
                    )
                    return search_query, results, note

        # Do not stop merely because a currently hot topic has weak engagement.
        # Rotate through broad topics and accept the best replyable post from the
        # last day, still skipping targets that already have an approval card.
        for candidate in candidates:
            await status.edit_text(
                "Finding a usable reply target...\n"
                f"Trying broader fallback: {candidate}"
            )
            try:
                search_query, results = await self._search_any_reply_targets(candidate)
            except Exception:
                continue
            last_search_query = search_query
            if results:
                return search_query, results, "Auto-selected a broad fallback topic.\n"

        return last_search_query, [], ""

    async def _search_any_reply_targets(
        self,
        query: str,
    ) -> tuple[str, list[XSearchResult]]:
        search_query, results = await self.x_search.search_recent(
            query,
            since_minutes=24 * 60,
            limit=min(
                REPLY_TARGET_RESULT_LIMIT,
                max(self.settings.x_search_limit, 12),
            ),
            product="Latest",
        )
        replyable = [
            result
            for result in results
            if result.url
            and result.text
            and not self.approvals.has_active_target(result.url)
        ]
        return (
            search_query,
            sorted(
                replyable,
                key=lambda result: (
                    result.like_count
                    + result.reply_count * 2
                    + result.retweet_count * 3
                    + result.quote_count * 3,
                    result.created_at_timestamp or 0,
                ),
                reverse=True,
            )[:5],
        )

    async def _search_rank_reply_targets(
        self,
        query: str,
        *,
        relaxed: bool = False,
        interval_minutes: int = 30,
    ) -> tuple[str, list[XSearchResult]]:
        base_minutes = _reply_target_interval_minutes(interval_minutes, default=30)
        since_minutes = max(base_minutes * 6, 180) if relaxed else base_minutes
        search_query, recent_results = await self.x_search.search_recent(
            query,
            since_minutes=since_minutes,
            limit=min(
                REPLY_TARGET_RESULT_LIMIT,
                max(self.settings.x_search_limit, 12),
            ),
            product="Latest",
        )
        ranked = rank_fast_growing_posts(
            recent_results,
            max_items=5,
            max_age_minutes=since_minutes,
            min_engagement_score=0 if relaxed else MIN_REPLY_TARGET_ENGAGEMENT_SCORE,
            min_velocity_score=0 if relaxed else MIN_REPLY_TARGET_VELOCITY_SCORE,
            min_view_count=0 if relaxed else MIN_REPLY_TARGET_VIEW_COUNT,
        )
        if relaxed and not ranked:
            ranked = sorted(
                (result for result in recent_results if result.url and result.text),
                key=lambda result: (
                    result.like_count
                    + result.reply_count * 2
                    + result.retweet_count * 3
                    + result.quote_count * 3
                ),
                reverse=True,
            )[:5]
        unseen = [
            result
            for result in ranked
            if not self.approvals.has_active_target(result.url)
        ]
        return search_query, unseen

    async def _auto_reply_target_queries(self) -> list[str]:
        queries: list[str] = []
        for category in AUTO_TREND_CATEGORIES:
            try:
                trends = await self.x_search.trends(category, limit=4)
            except Exception:
                trends = []
            queries.extend(trend.name for trend in trends if trend.name)

        for category in AUTO_TREND_CATEGORIES:
            queries.extend(TREND_FALLBACK_QUERIES.get(category, []))
        queries.extend(AUTO_REPLY_TARGET_FALLBACK_QUERIES)
        queries.append(self.settings.creator_niche)
        return _dedupe_queries(queries)[:16]

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
        return f"{approval.target_url}\n\n{approval.text}".strip()

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
    if include_decisions:
        rows.append(
            [
                InlineKeyboardButton(
                    "Approve on mobile",
                    callback_data=f"automation:mobile:{approval.id}",
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
                "Reject",
                callback_data=f"automation:reject:{approval.id}",
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


def _reply_target_batch_score(results: list[XSearchResult]) -> float:
    if not results:
        return 0.0
    return sum(result.velocity_score for result in results) + len(results)


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


def _no_reply_targets_message(
    search_query: str,
    auto: bool,
    interval_minutes: int = 30,
) -> str:
    intro = (
        "No reply-ready posts found after trying hot and broad fallback topics."
        if auto
        else (
            "No strong reply targets found in the last "
            f"{_reply_target_interval_minutes(interval_minutes, default=30)} minutes "
            f"for: {search_query}"
        )
    )
    return (
        f"{intro}\n\n"
        "The bot already tried fresh high-signal posts, a wider window, and broad "
        "fallback topics. Check X cookies/account limits, then try again later or use "
        "a specific topic such as `/replytargets crypto`."
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
        parts = raw_text.split(maxsplit=1)
        if len(parts) > 1:
            return parts[1].strip()
    return " ".join(context.args).strip()


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
    if text.startswith("X search failed"):
        return (
            "Could not search X. Check X_COOKIE, account rate limits, and network access. "
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
