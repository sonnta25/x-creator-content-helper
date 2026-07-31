# X Telegram Content Bot

Telegram bot for creating X/Twitter posts, replies, reply targets, multi-source trend briefs, and social images for a Vietnamese audience.

The current AI flow is:

```text
Telegram bot -> local extension bridge -> one Gemini job -> Telegram bot
```

Gemini produces each final draft in one browser job. The extension reuses one logged-in tab but starts a clean Gemini conversation for every job, preventing context from one command leaking into the next. It hard-recycles that tab after 10 successful jobs, or immediately after a provider/DOM failure, so a long-running VPS does not retain Gemini's old page heap indefinitely. Optional images use one additional Gemini job. The bot does not use Ollama, Pollinations, Playwright, or official Gemini APIs.

## Commands

- `/download <video URL>`: download one public video and send it back as a Telegram file.
- `/tweet <topic>`: create one Vietnamese long-form X post, with an optional image.
- `/tweetx <topic/search>`: search X first, then create one long-form X post from live context.
- `/tweettrend3 [auto|trending|news|sport|entertainment]`: in `auto` mode, find current topics around `CREATOR_NICHE` first, then create three Vietnamese posts with approval buttons.
- `/dailybrief [trending|news|sport|entertainment]`: create daily-ready long-form post options from multi-source trend context.
- `/retweet <X post link> | <visual description>`: remix a source X post into a fresh original tweet with an optional image.
- `/reply <tweet text or X post link>`: create one copy-ready text reply only.
- `/automationhere`: use the current Telegram chat for scheduled approval requests.
- `/replyevery <minutes>`: configure the scheduled `/replytargets` interval from Telegram (5-1440 minutes).
- `/replytargets [query]`: scan broad current conversations for emerging and late-breakout posts across configured languages, independently of `CREATOR_NICHE`; each result includes approval buttons.
- `/importcookie [account_name] <auth_token=...; ct0=...>`: import an X cookie for search.
- `/xaccounts`: list imported X cookie accounts without exposing cookies.
- `/xremove <account_name>`: remove an imported X cookie account from the twscrape pool.
- `/persona`: show or update creator niche, voice, and audience.
- `/cancel`: cancel a command that is waiting for input.

The bot registers these commands with Telegram on startup.

### Two-step command input

Commands that need input no longer run immediately when selected from Telegram's
command menu. Select `/download`, `/tweet`, `/tweetx`, `/retweet`, `/replytargets`,
`/reply`, `/persona`, `/importcookie`, `/xremove`, or `/replyevery`, and the bot
opens a reply field with a short prompt. Send the requested value as the next
message to run the command.

The direct form still works, for example `/tweet AI agents` or
`/download https://...`. Pending input is isolated per chat and Telegram user,
expires after five minutes, and can be stopped with `/cancel`. Selecting a
different command replaces the previous pending request. Use `auto` when the
`/replytargets` prompt should choose its own topic, and `show` to inspect
`/persona` or `/replyevery`.

### Video downloads

Send a public video link:

```text
/download https://www.tiktok.com/@creator/video/123
```

The bot downloads one video and uploads it to the same Telegram chat as a document,
so the file can be saved directly to a phone or computer without relying on a
short-lived platform CDN link. The downloader supports sites handled by `yt-dlp`,
including common TikTok, Douyin, Xiaohongshu, Facebook, and X public-video URLs.
Playlists are disabled.

Delivered files use a neutral name such as
`creator-video-20260727-101112-a1b2c3.mp4`; the source title and platform video ID
are not used in the filename or Telegram caption. The downloader also disables
description, info JSON, thumbnail, comment, and subtitle sidecar files. Telegram
keeps the source URL in the message caption for permission and provenance checks,
but it is not embedded in the delivered filename. These cleanup steps do not make
copied content original or bypass copyright/reused-content checks.

Downloads are processed one at a time, use a 180-second deadline, and default to a
45 MB cap to stay below the public Telegram Bot API upload limit. Configure these in
`.env`:

```env
DOWNLOAD_MAX_FILE_MB=45
DOWNLOAD_TIMEOUT_SECONDS=180
DOWNLOAD_COOKIES_FILE=data/download-cookies.txt
DOWNLOAD_COOKIES_FROM_BROWSER=chrome
DOWNLOAD_BROWSER_PROFILE=Default
```

Some private, age-gated, regional, or anti-bot-protected videos require cookies.
Export a Netscape-format cookie file on the bot machine and set
`DOWNLOAD_COOKIES_FILE` to its path. For automatic refresh, set
`DOWNLOAD_COOKIES_FROM_BROWSER` to `chrome` and optionally identify a profile such
as `Default` or `Profile 1` with `DOWNLOAD_BROWSER_PROFILE`.

When both sources are configured, the bot first uses the cookie file. If the
website returns an authentication, cookie, CAPTCHA, 401, or 403 error, the bot
loads current cookies from that browser profile, merges them into the cookie jar,
and retries the download once. Browser extraction requires the bot and browser to
run under the same Windows user. It cannot restore a logged-out or revoked session;
open the website, sign in or complete CAPTCHA, and retry. Cookie freshness and
upstream site changes can still make a URL fail. Only download content you are
authorized to save.

## Creator Flow

1. Run `/tweettrend3` for auto mode, or use `/tweettrend3 news`, `/tweettrend3 entertainment`, or `/tweettrend3 trending`. It always returns Vietnamese post text.
2. Pick the option with the strongest originality and follow potential.
3. Run `/replytargets` for auto mode, or `/replytargets <topic>` when you want a specific conversation lane.
4. Use `/tweetx <topic>` when you want a post grounded in current X chatter.
5. Use `/dailybrief news` when you need several ready-to-post ideas, with optional images when enabled.

General X search commands add `lang:en` automatically unless the query already includes a `lang:` filter. `/replytargets` is separate: it expands auto and plain-topic searches across `REPLY_TARGET_LANGUAGES` (default `en,ja`) and writes each reply in the source post's language.

`/tweettrend3` auto mode searches current Google News topics using `CREATOR_NICHE` first, then falls back to broader X/Google/RSS trends only if fewer than three niche topics are available. `CREATOR_NICHE` does not constrain `/replytargets`. `/tweettrend3` uses English/US trend context by default, but final post text is always written in natural Vietnamese. Image prompts remain English so Gemini image generation is more reliable.

`/tweettrend3` selects three distinct trend topics, then sends one copy-ready post for each topic plus an image only when `GENERATE_IMAGES=true`. It does not include option labels or score metadata in the copy-ready post message.

Manual `/tweettrend3` creates Gemini jobs one topic at a time. Its Telegram status shows the current topic number and explicitly says when the job is waiting for the Chrome extension. With extension **Auto Run** OFF, click **Run next job** once for each queued topic; with **Auto Run** ON, the extension picks them up automatically on its polling interval.

Extension `0.4.0` waits for Gemini's composer to be visible before inserting a prompt. It inserts the full prompt atomically, verifies at least 98% of the normalized text is present, and automatically retries with a DOM-safe fallback if Gemini replaces its editor node. If a Gemini job fails, it reports the failure to the bot before recycling the provider tab. Recycling opens a new Gemini tab, waits until its composer is ready, and only then closes the old Gemini tabs; if the replacement fails, the old tab is preserved. Auto Run alarms are verified whenever the service worker starts and by a one-minute watchdog. The open Gemini tab also wakes the worker every 25 seconds, so Auto Run keeps polling even if Chrome loses all extension alarms. Claimed jobs send a heartbeat; if Chrome or the worker dies, the bridge returns the abandoned job to the queue after 75 seconds.

Trend commands scan X trends, Google Trends RSS, built-in Google News RSS feeds, and any custom RSS feeds from `TREND_RSS_URLS`. RSS requests reuse a small connection pool instead of opening a new TLS connection for every feed. When X search is configured, each selected topic is enriched with recent X context from the last 24 hours. `/replytargets` runs on its own scan interval but uses the independent `REPLY_TARGET_MAX_AGE_MINUTES` lookback. Repeated scans persist metric snapshots so ranking can use recent deltas and acceleration rather than lifetime averages alone.

## Setup

### Windows

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
Copy-Item .env.example .env
```

`start.ps1` compares `pyproject.toml` with the dependencies installed in `.venv`.
After extracting an updated ZIP over an existing installation, starting the bot
automatically installs newly added packages such as `yt-dlp` before checking or
launching the polling process:

```powershell
.\scripts\windows\start.ps1
```

### Ubuntu

```bash
sudo apt update
sudo apt install -y python3.11 python3.11-venv python3.11-dev unzip
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
```

Edit `.env`:

```env
TELEGRAM_BOT_TOKEN=...

DOWNLOAD_MAX_FILE_MB=45
DOWNLOAD_TIMEOUT_SECONDS=180
DOWNLOAD_COOKIES_FILE=data/download-cookies.txt
DOWNLOAD_COOKIES_FROM_BROWSER=chrome
DOWNLOAD_BROWSER_PROFILE=Default

CONTENT_PROVIDER=extension_bridge
EXTENSION_BRIDGE_HOST=127.0.0.1
EXTENSION_BRIDGE_PORT=8765
EXTENSION_BRIDGE_TOKEN=choose-a-private-token
EXTENSION_BRIDGE_TIMEOUT_SECONDS=360

GENERATE_IMAGES=false
IMAGE_PROVIDER=extension_bridge
GEMINI_IMAGE_PROMPT_PREFIX=Create one square realistic image for this social post. Return the image only, with no extra text.

X_COOKIE=
X_ACCOUNT_NAME=telegram_bot
X_ACCOUNTS_DB=data/twscrape_accounts.db
X_SEARCH_LIMIT=8
X_SEARCH_PRODUCT=Top
REPLY_TARGET_MIN_AUTHOR_FOLLOWERS=50000
REPLY_TARGET_MIN_VIEWS=500
REPLY_TARGET_MAX_AGE_MINUTES=360
REPLY_TARGET_LANGUAGES=en,ja
REPLY_TARGET_METRICS_PATH=data/reply_target_metrics.json
X_POST_CHAR_LIMIT=2000

TREND_SOURCES=x,google_trends,rss
GOOGLE_TRENDS_GEO=US
TREND_RSS_URLS=

HASHTAG_MODE=auto

CREATOR_NICHE=gold markets, cryptocurrency, and practical AI tools such as ChatGPT, Claude, Grok, and emerging AI products
CREATOR_VOICE=witty, practical, dry, slightly contrarian, with a sharp creator POV
TARGET_AUDIENCE=Vietnamese retail investors, crypto users, creators, founders, and professionals seeking timely practical insights on gold, crypto, and AI tools
```

## Chrome Extension

Use this when you have Gemini web access but no API keys.

1. Open Chrome.
2. Log in to `https://gemini.google.com`.
3. Open `chrome://extensions`.
4. Enable **Developer mode**.
5. Click **Load unpacked**.
6. Select the repo folder `browser_extension`.
7. Open the extension popup and set:
   - Bridge URL: `http://127.0.0.1:8765`
   - Token: same value as `EXTENSION_BRIDGE_TOKEN`
   - Auto Run: ON if you want jobs to run automatically

When Auto Run is OFF, click **Run next job** after sending a Telegram command. When Auto Run is ON, Chrome checks for pending jobs about every 30 seconds while Chrome is open. A watchdog recreates a missing alarm and the open Gemini tab sends a lightweight runtime heartbeat every 25 seconds, so restarting Chrome Lite or its extension worker does not silently leave jobs queued.

Gemini prompts are compacted at the bot layer, then entered into one composer in 1,200-character chunks and submitted once. Each job starts a clean conversation in the same warm tab. After 10 completed Gemini jobs, the extension opens a fresh Gemini tab, waits for its composer, and then closes the old Gemini tabs to release their DOM/JavaScript heap. Provider timeouts and DOM failures trigger the same recycle immediately. Response DOM checks run every 2.5 seconds, and volatile status plus the recycle counter are kept in memory-backed session storage instead of being written repeatedly to the Chrome profile.

### 2 GB RAM VPS mode

For a 2-core / 2 GB Windows VPS, run Chrome with the included low-memory profile instead of your normal Chrome profile:

```powershell
.\scripts\windows\start-chrome-lite.ps1
```

It uses a separate profile, loads only this extension, disables GPU/background services, limits Chrome to two renderer processes, and caps disk cache at 16 MB (media cache 1 MB). Gemini stays in one warm tab, starts a clean conversation for every job, and hard-recycles the page every 10 jobs. On a 2-core VPS it also pins Chrome to one logical CPU at BelowNormal priority, so Chrome cannot saturate both cores. The bot runner no longer starts Ollama or any unused model service. A background watchdog checks every 30 seconds, reapplies the CPU limit, and relaunches this profile if Chrome exits. Sign in to Gemini once in that profile. Do not use headless mode: this bridge needs the visible Gemini web UI. Stop only this Chrome instance and its watchdog with:

```powershell
.\scripts\windows\stop-chrome-lite.ps1
```

If the VPS still kills Chrome, 2 GB is below a comfortable operating margin for Gemini's web UI; increase the Windows page file or move to 4 GB RAM for reliable scheduled runs.

If Chrome disappears without an obvious Task Manager spike, run this on the VPS after it happens:

```powershell
.\scripts\windows\diagnose-chrome-lite.ps1
```

It writes a timestamped report under `logs/` with watchdog restart times, current Chrome processes, Windows resource-exhaustion events, Chrome crash/hang reports, Defender events, and Chrome Crashpad files. Run it with `-Hours 48` to inspect a longer period. If the watchdog shows a restart but Windows has no crash, resource, or Defender event, the VPS host/provider or another external policy is the likely source.

### Scheduled Telegram approvals

The extension can schedule content generation while keeping the final X action manual:

1. Start the bot and send `/automationhere` in the Telegram chat that should receive approvals.
2. Reload `browser_extension/` from `chrome://extensions` after updating the project.
3. Open the extension popup and configure **Scheduled approvals**:
   - **Active from / Active until**: the daily activity window, using the computer's local time. Overnight windows such as `22:00` to `02:00` are supported.
   - **/replytargets scan interval**: how often Chrome triggers a scan; the minimum is 5 and the default is 15 minutes.
   - **/replytargets maximum post age**: independent lookback; the default is 360 minutes.
   - **/replytargets languages**: comma-separated X language codes; the default is `en,ja`.
   - **/replytargets query**: optional; leave blank for automatic topic selection.
   - **/tweettrend3 fixed times**: comma-separated local times, for example `09:00, 13:30, 18:00`.
   - **/tweettrend3 category**: `auto`, `trending`, `news`, `sport`, or `entertainment`.
4. Turn **Automation** ON and keep Chrome, the Telegram bot, and the Gemini/X login sessions running.

You can also change **/replytargets every** from the private Telegram approval chat with `/replyevery 30`. Every command writes a schedule revision, even when the numeric value did not change. The extension picks it up within about 30 seconds and resets the next run from that moment. The popup shows both **Last trigger** and **Next run** using the VPS local clock.

For both manual `/replytargets` and `/tweettrend3` commands and their scheduled runs, Telegram sends each draft with **Approve on mobile** and **Reject** buttons. Only the Telegram user who requested a manual draft can approve it.

`/replytargets` approval messages contain only the target X link and the copy-ready reply. A scheduled run immediately sends a short started/progress message, so source discovery cannot look like a missed schedule. Auto mode starts from the authenticated X account's current trends plus localized broad OR-queries covering news, politics, business, sports, entertainment, technology, internet culture, AI, and crypto; it does not start from `CREATOR_NICHE`. The trends timeline may reflect the logged-in account's locale/personalization and is not claimed to be a global or country-specific chart. Search itself applies no country filter. With the default six-query budget, up to four distinct X trends are searched once across every configured language; the remaining slots are assigned round-robin to localized broad discovery, normally one English and one Japanese query. If X trends are unavailable, all six slots rotate through the localized broad, breaking-news, and AI fallbacks.

Every query searches both `Top` and `Latest`, with at most eight results from each product, so auto mode can compare up to 96 raw rows before deduplication and quality filters. `Top` supplies confirmed distribution while `Latest` supplies earlier breakout candidates. `-is:reply -is:retweet` is added at search time, and the parsed results are checked again; original posts and quote posts remain eligible. An explicit `/replytargets <topic>` stays inside that topic but expands it across the configured languages unless the user supplies a `lang:` operator.

The first time a post is seen, ranking uses its lifetime-average momentum. Later scans read `data/reply_target_metrics.json` and use view, weighted-engagement, direct-reply, and reply/quote deltas from the previous observation, plus acceleration. Viral confidence and reply opportunity are separate: reply activity is retained as a capped viral signal, but no longer makes a crowded thread automatically attractive. Thread availability measures recent views per competing reply, lifetime views per reply, total reply load, and new-reply pressure. Reply opportunity uses 65% viral momentum, 25% thread availability, and 10% recency, then subtracts a soft saturation penalty; exceptional ongoing view velocity reduces—but does not erase—that penalty. Scores are normalized within each language, and the final selection keeps the strongest qualified candidate from every detected configured language before filling the remaining slots globally.

This design favors a post that is still gaining distribution while its root reply section remains open, including two-to-six-hour late breakouts. The 500-view minimum remains a hard floor whenever X exposes view count. The configured 50,000-follower value is a small capped reach bonus rather than a hard gate. Replies match the source language and must add one source-grounded observation, tension, implication, or real question; when a question fits, it targets a concrete decision, assumption, consequence, or tradeoff the original author can answer. Generic agreement, recap, unsupported context, generic engagement questions, and forced sarcasm are rejected.

### Automatic reply tracking and learning

Reply tracking is enabled by default. Set the real posting account once with
`/replylearn username @yourname` or `X_OWNER_USERNAME=yourname`. This value is
not `X_ACCOUNT_NAME`: the latter is only the local twscrape cookie-account label.
After an approval is opened and you manually submit the reply on X, the bot scans
that account's tweets and replies and matches the new item by parent tweet, posting
window, and draft similarity. You do not need to send the published reply URL back
to Telegram.

The tracker captures reply and root-post metrics at approximately 15 minutes,
1 hour, 6 hours, and 24 hours. Its 24-hour outcome score combines:

- reply views relative to new root-post views;
- reply engagement per view, with replies, reposts, and quotes weighted above likes;
- whether the original root author directly replied.

The score is a strategy-comparison heuristic, not an X impression guarantee.
Use `/replyreport 7d` or `/replyreport 30d` to inspect results. Use
`/replylearn status`, `on`, `off`, or `rollback` to control it.

The bot rotates among five fixed strategies: a specific observation, practical
implication, respectful counterpoint, author-specific question, and natural humor.
It tracks immediately, but waits for 60 completed 24-hour samples before the first
automatic adjustment. Later revisions require new samples and are limited to once
per seven days; every strategy weight can move by at most 10% relative to its prior
value. Low-sample results are shrunk toward the global average. Weight versions are
stored in `data/reply_learning.json`, and rollback restores the previous version.
The bot does not rewrite source code or overwrite its base prompts.

The project requires `twscrape>=0.19.2`. That release includes current X client-transaction-ID and GraphQL compatibility fixes; older `0.19.1` builds can fail before search and misleadingly produce an empty candidate pool.

If Gemini returns an empty target array or changes common JSON field names, `/replytargets` normalizes the response and automatically queues one repair job using the same candidate URLs. Scheduled automation remains active while that repair job is pending, so the extension picks it up on the next automation check. If both attempts fail, the Telegram error includes short response previews for diagnosis.

Mobile approval is deliberately two-step because one Telegram button cannot both record a callback and open another app:

1. Tap **Approve on mobile**. The bot records the approval and removes the decision buttons.
2. Tap **Open X on phone**. The official mobile-friendly X Web Intent opens the matching reply or post composer and pre-populates the draft when the URL is short enough. For a long post, Telegram rejects a URL containing the full encoded draft; the bot instead opens the X composer safely and keeps the draft above for you to copy and paste.

Once a reply target is pending or approved, the bot skips the same target to avoid duplicate approval cards. This history is persisted in `data/automation_approvals.json`, so deduplication survives bot restarts.

- **Open X on phone** uses the official mobile-friendly X Web Intent.
- **Copy draft** uses Telegram's native clipboard button for drafts up to 256 characters. For longer drafts that cannot safely fit in an encoded X URL, copy the message text and paste it into the composer opened by **Open X on phone**.

Review or edit the filled draft in X, then submit it yourself. A pending approval expires after 30 minutes. `Auto Run` still controls whether Gemini jobs from manually entered Telegram commands run automatically; when it is OFF, use **Run next job** before approving the returned draft.

The first scheduled run may wait for the configured interval. While a scheduled workflow is active, automation processes one Gemini job per 30-second check, which keeps CPU use steadier on small VPS plans. When no scheduled workflow is active, it does not poll `/jobs/next`; manual commands still obey the separate `Auto Run` switch. Fixed `/tweettrend3` times are considered due for ten minutes, which allows for Chrome alarm delays or a briefly sleeping computer. Missed runs are not replayed in bulk.

If Gemini asks for human verification, solve it manually in Chrome and run the job again. This bridge depends on the web UI, so reload the extension after changing files in `browser_extension/`.

## Run

Windows:

```powershell
.\.venv\Scripts\python.exe -m src.main
```

Ubuntu:

```bash
source .venv/bin/activate
python -m src.main
```

Bridge health check:

```powershell
Invoke-RestMethod -Uri http://127.0.0.1:8765/health
```

or:

```bash
curl http://127.0.0.1:8765/health
```

Expected provider: `extension_bridge`.

## X Cookie Search

To enable `/tweetx`, `/tweettrend3`, `/dailybrief`, and `/replytargets` live X context:

1. Open `x.com` in a logged-in browser.
2. Open DevTools > Application > Cookies.
3. Copy `auth_token` and `ct0`.
4. Send this to the Telegram bot:

```text
/importcookie auth_token=YOUR_AUTH_TOKEN; ct0=YOUR_CT0
```

For multiple accounts:

```text
/importcookie account2 auth_token=ACCOUNT_2_AUTH_TOKEN; ct0=ACCOUNT_2_CT0
/importcookie account3 auth_token=ACCOUNT_3_AUTH_TOKEN; ct0=ACCOUNT_3_CT0
/xaccounts
```

If an account starts returning X/twscrape errors, the bot sends a Telegram warning
with recovery commands. Remove a bad account with:

```text
/xremove account2
```

The default cookie is written to `.env`. Named accounts are stored in `data/twscrape_accounts.db`. Keep `.env` and `data/` private.

## Multi-Source Trends

`/tweettrend3` and `/dailybrief` use these sources by default:

```env
TREND_SOURCES=x,google_trends,rss
GOOGLE_TRENDS_GEO=US
TREND_RSS_URLS=
```

`rss` includes built-in Google News feeds for `trending`, `news`, `sport`, and `entertainment`.
Add custom feeds with semicolon-separated `Label|URL` entries:

```env
TREND_RSS_URLS=TechCrunch|https://techcrunch.com/feed/;The Verge|https://www.theverge.com/rss/index.xml
```

Use only RSS and Google Trends when X cookies are not available:

```env
TREND_SOURCES=google_trends,rss
```

## Ubuntu Service

Example systemd unit:

```ini
[Unit]
Description=X Content Telegram Bot
After=network.target

[Service]
WorkingDirectory=/opt/x-content-bot
EnvironmentFile=/opt/x-content-bot/.env
ExecStart=/opt/x-content-bot/.venv/bin/python -m src.main
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Install:

```bash
sudo nano /etc/systemd/system/x-content-bot.service
sudo systemctl daemon-reload
sudo systemctl enable --now x-content-bot
sudo systemctl status x-content-bot --no-pager
```

Logs:

```bash
sudo journalctl -u x-content-bot -n 100 --no-pager
```

## Troubleshooting

- `Missing required environment variable: TELEGRAM_BOT_TOKEN`: edit `.env` in the project root.
- `Could not connect to the local Chrome extension bridge`: start the bot, open Chrome, confirm the extension Bridge URL/token match `.env`.
- `Extension bridge timed out`: verify extension `0.3.9` or newer is loaded and Auto Run is ON. The Gemini-tab heartbeat should wake Auto Run within 25 seconds, while the runtime watchdog recreates missing alarms; **Run next job** remains available for an immediate check.
- `Missing image data`: reload the extension, keep the Gemini tab visible, and confirm Gemini generated an image in an `<img>` tag.
- `No Google/RSS/X trend context found`: check internet access, RSS feed URLs, X cookies, or try a specific category like `/tweettrend3 news`.
