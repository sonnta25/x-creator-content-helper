from __future__ import annotations

import logging
from dataclasses import replace
from io import BytesIO

from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction
from telegram.ext import Application, CommandHandler, ContextTypes

from src.ai_service import create_ai_service
from src.config import Settings
from src.env_store import update_env_value
from src.models import GeneratedContent, ReplyTargetDraft, TrendPostVariant, XSearchResult
from src.trend_source_service import TrendSourceService, summarize_trend_signals
from src.x_search_service import (
    MIN_REPLY_TARGET_ENGAGEMENT_SCORE,
    MIN_REPLY_TARGET_VELOCITY_SCORE,
    MIN_REPLY_TARGET_VIEW_COUNT,
    REPLY_TARGET_SEARCH_LIMIT,
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
]


class ContentBot:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.ai = create_ai_service(settings)
        self.x_search = XSearchService(settings)
        self.trend_sources = TrendSourceService(settings, self.x_search)
        self._x_account_error_notices: dict[str, str] = {}

    def build_application(self) -> Application:
        async def post_init(app: Application) -> None:
            await _set_bot_commands(app)
            bridge = getattr(self.ai, "bridge", None)
            if bridge is not None:
                await bridge.start()

        async def post_shutdown(app: Application) -> None:
            del app
            bridge = getattr(self.ai, "bridge", None)
            if bridge is not None:
                await bridge.stop()

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
            "\n"
            "AI provider: Chrome extension bridge runs Gemini for all "
            "content commands."
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
            "Finding the best hot X trend automatically..."
            if category in {"auto", "best"}
            else f"Finding hot X trends in {category}..."
        )
        status_text = f"{status_text}\nOutput language: Vietnamese"
        status = await message.reply_text(status_text)
        try:
            if category in {"auto", "best"}:
                topic, x_context, source, _results, category = await self._get_auto_trend_context(
                    status
                )
            else:
                topic, x_context, source, _results = await self._get_trend_context(
                    category,
                    status,
                )
            await status.edit_text(
                f"Writing 3 Vietnamese post options from: {topic}"
            )
            variants = await self.ai.generate_trend_post_variants(
                topic,
                x_context,
                output_language=output_language,
            )

            await status.edit_text(
                f"Source: {source}\n"
                f"Category: {category}\n"
                f"Language: {output_language}\n"
                f"Topic: {topic}\n\n"
                "Sending tweet options with images..."
            )
            for index, variant in enumerate(variants, start=1):
                await self._send_trend_variant(message, variant, index)
            await status.edit_text(
                f"Source: {source}\n"
                f"Category: {category}\n"
                f"Language: {output_language}\n"
                f"Topic: {topic}\n\n"
                "Tweet options sent."
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
                summarize_x_context(results),
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
        status = await message.reply_text(
            "Finding fast-moving reply targets from the last 30 minutes..."
            if query
            else "Auto-picking a hot topic, then finding reply targets from the last 30 minutes..."
        )
        try:
            search_query, results, auto_note = await self._get_reply_target_context(
                query,
                status,
            )
            if not results:
                await status.edit_text(_no_reply_targets_message(search_query, auto=not query))
                return

            await status.edit_text("Drafting high-signal replies...")
            drafts = await self.ai.generate_reply_targets(
                search_query,
                summarize_reply_target_context(results, max_items=5),
            )
            await status.edit_text(
                f"Reply targets for: {search_query}\n"
                f"{auto_note}"
                "Sending each reply and post link as separate messages..."
            )
            for draft in drafts:
                await message.reply_text(_format_reply_target_reply(draft))
                await message.reply_text(_format_reply_target_link(draft))
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
                summarize_x_context(results),
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
                limit=self.settings.x_search_limit,
                product="Latest",
            )
        except Exception as exc:
            errors.append(f"X enrichment: {exc}")

        context_parts = [f"Multi-source trend context:\n{summarize_trend_signals(signals)}"]
        if results:
            context_parts.append(
                f"Recent X context for {search_query}:\n{summarize_x_context(results)}"
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

    async def _get_reply_target_context(
        self,
        query: str,
        status,
    ) -> tuple[str, list[XSearchResult], str]:
        if query:
            search_query, results = await self._search_rank_reply_targets(query)
            return search_query, results, ""

        best_query = ""
        best_results: list[XSearchResult] = []
        tried: list[str] = []
        for candidate in await self._auto_reply_target_queries():
            if candidate in tried:
                continue
            tried.append(candidate)
            await status.edit_text(
                "Auto-picking reply targets...\n"
                f"Trying: {candidate}"
            )
            try:
                search_query, results = await self._search_rank_reply_targets(candidate)
            except Exception:
                continue
            if not best_query:
                best_query = search_query
            if _reply_target_batch_score(results) > _reply_target_batch_score(best_results):
                best_query = search_query
                best_results = results
            if len(best_results) >= 3 and _reply_target_batch_score(best_results) >= 20:
                break

        auto_note = "Auto-selected topic.\n"
        return best_query or "auto hot topics", best_results, auto_note

    async def _search_rank_reply_targets(self, query: str) -> tuple[str, list[XSearchResult]]:
        search_query, recent_results = await self.x_search.search_recent(
            query,
            since_minutes=30,
            limit=max(REPLY_TARGET_SEARCH_LIMIT, self.settings.x_search_limit),
            product="Latest",
        )
        return search_query, rank_fast_growing_posts(
            recent_results,
            max_items=5,
            max_age_minutes=30,
        )

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
    ) -> None:
        copy_text = _format_trend_variant_copy(variant)
        await message.reply_text(copy_text)

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


def _no_reply_targets_message(search_query: str, auto: bool) -> str:
    intro = (
        "No strong reply targets found while auto-scanning hot topics."
        if auto
        else f"No strong reply targets found in the last 30 minutes for: {search_query}"
    )
    return (
        f"{intro}\n\n"
        f"Minimum quality: engagement score >= {MIN_REPLY_TARGET_ENGAGEMENT_SCORE:.0f}, "
        f"velocity >= {MIN_REPLY_TARGET_VELOCITY_SCORE:.1f}/min, "
        f"and views >= {MIN_REPLY_TARGET_VIEW_COUNT} when X exposes view count.\n\n"
        "Try a specific topic if you want to loosen the search direction, for example "
        "`/replytargets crypto` or `/replytargets entertainment`."
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
