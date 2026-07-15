# X Telegram Content Bot

Telegram bot for creating X/Twitter posts, replies, reply targets, multi-source trend briefs, and social images for a Vietnamese audience.

The current AI flow is:

```text
Telegram bot -> local extension bridge -> Gemini draft -> Gemini final -> Telegram bot
```

Gemini creates and finalizes drafts, then generates images through the Chrome extension. The extension reuses one logged-in Gemini tab for every job to avoid repeated cold starts. The bot does not use Ollama, Pollinations, Playwright, or official Gemini APIs.

## Commands

- `/tweet <topic>`: create one Vietnamese long-form X post and image from a topic.
- `/tweetx <topic/search>`: search X first, then create one long-form X post from live context.
- `/tweettrend3 [auto|trending|news|sport|entertainment]`: scan X, Google Trends, and RSS, then create three Vietnamese posts from three different hot topics, with images and approval buttons.
- `/dailybrief [trending|news|sport|entertainment]`: create daily-ready long-form post options from multi-source trend context.
- `/retweet <X post link> | <visual description>`: remix a source X post into a fresh original tweet and image.
- `/reply <tweet text or X post link>`: create one copy-ready text reply only.
- `/automationhere`: use the current Telegram chat for scheduled approval requests.
- `/replyevery <minutes>`: configure the scheduled `/replytargets` interval from Telegram (5-1440 minutes).
- `/replytargets [query]`: auto-pick a hot topic, or use your query, then find fresh X posts worth replying to; each result includes approval buttons.
- `/importcookie [account_name] <auth_token=...; ct0=...>`: import an X cookie for search.
- `/xaccounts`: list imported X cookie accounts without exposing cookies.
- `/xremove <account_name>`: remove an imported X cookie account from the twscrape pool.
- `/persona`: show or update creator niche, voice, and audience.

The bot registers these commands with Telegram on startup.

## Creator Flow

1. Run `/tweettrend3` for auto mode, or use `/tweettrend3 news`, `/tweettrend3 entertainment`, or `/tweettrend3 trending`. It always returns Vietnamese post text.
2. Pick the option with the strongest originality and follow potential.
3. Run `/replytargets` for auto mode, or `/replytargets <topic>` when you want a specific conversation lane.
4. Use `/tweetx <topic>` when you want a post grounded in current X chatter.
5. Use `/dailybrief news` when you need several ready-to-post ideas with images.

X search commands add `lang:en` automatically unless your query already includes a `lang:` filter.

`/tweettrend3` uses English/US trend context by default, but final post text is always written in natural Vietnamese. Image prompts remain English so Gemini image generation is more reliable.

`/tweettrend3` selects three distinct trend topics, then sends one copy-ready post and generated image for each topic. It does not include option labels or score metadata in the copy-ready post message.

Trend commands scan X trends, Google Trends RSS, built-in Google News RSS feeds, and any custom RSS feeds from `TREND_RSS_URLS`. When X search is configured, each selected topic is enriched with recent X context from the last 24 hours. `/replytargets` uses the configured schedule interval as its recent-post window and filters for stronger engagement/velocity when X exposes those metrics.

## Setup

### Windows

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
Copy-Item .env.example .env
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

CONTENT_PROVIDER=extension_bridge
EXTENSION_BRIDGE_HOST=127.0.0.1
EXTENSION_BRIDGE_PORT=8765
EXTENSION_BRIDGE_TOKEN=choose-a-private-token
EXTENSION_BRIDGE_TIMEOUT_SECONDS=360

GENERATE_IMAGES=true
IMAGE_PROVIDER=extension_bridge
GEMINI_IMAGE_PROMPT_PREFIX=Create one square realistic image for this social post. Return the image only, with no extra text.

X_COOKIE=
X_ACCOUNT_NAME=telegram_bot
X_ACCOUNTS_DB=data/twscrape_accounts.db
X_SEARCH_LIMIT=8
X_SEARCH_PRODUCT=Top
X_POST_CHAR_LIMIT=2000

TREND_SOURCES=x,google_trends,rss
GOOGLE_TRENDS_GEO=US
TREND_RSS_URLS=

HASHTAG_MODE=auto

CREATOR_NICHE=AI tools, creator growth, and online business
CREATOR_VOICE=witty, practical, dry, slightly contrarian, with a sharp creator POV
TARGET_AUDIENCE=Vietnamese X users, creators, founders, and indie hackers
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

When Auto Run is OFF, click **Run next job** after sending a Telegram command. When Auto Run is ON, Chrome checks for pending jobs about every 30 seconds while Chrome is open.

Long Gemini prompts are entered into the same composer in small 1,200-character chunks with a short pause between chunks, then submitted once. This reduces peak Chrome disk/CPU activity without changing the prompt or splitting it into separate Gemini messages.

### 2 GB RAM VPS mode

For a 2-core / 2 GB Windows VPS, run Chrome with the included low-memory profile instead of your normal Chrome profile:

```powershell
.\scripts\windows\start-chrome-lite.ps1
```

It uses a separate profile, loads only this extension, disables GPU/background services and nonessential background telemetry, limits Chrome to one renderer process, and caps disk cache at 16 MB (media cache 1 MB). Gemini stays open after every job, including image generation, so the next job can reuse the logged-in, warm tab instead of triggering another cold start. On a 2-core VPS it also pins Chrome to one logical CPU at BelowNormal priority, so Chrome cannot saturate both cores. It starts a background watchdog that checks every 30 seconds, reapplies the CPU limit to Chrome child processes, and relaunches this Chrome profile if Windows kills it. Gemini jobs will be slower, but the bot and Windows remain responsive. Sign in to Gemini once in that profile. Do not use headless mode: this bridge needs the visible Gemini web UI. Stop only this Chrome instance and its watchdog with:

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
   - **/replytargets every**: interval in minutes; the minimum is 5 and the default is 30.
   - **/replytargets query**: optional; leave blank for automatic topic selection.
   - **/tweettrend3 fixed times**: comma-separated local times, for example `09:00, 13:30, 18:00`.
   - **/tweettrend3 category**: `auto`, `trending`, `news`, `sport`, or `entertainment`.
4. Turn **Automation** ON and keep Chrome, the Telegram bot, and the Gemini/X login sessions running.

You can also change **/replytargets every** from the private Telegram approval chat with `/replyevery 30`. The extension picks up the new interval within about 30 seconds and resets the next run using that interval.

For both manual `/replytargets` and `/tweettrend3` commands and their scheduled runs, Telegram sends each draft with **Approve on mobile** and **Reject** buttons. Only the Telegram user who requested a manual draft can approve it.

`/replytargets` approval messages contain only the target X link and the copy-ready reply. To keep small VPS CPU use predictable, each run checks up to 8 topics, processes up to 20 X results per topic, and sends the best 3 candidates to Gemini. If a selected topic returns no usable posts, the bot automatically tries other topics, widens the search window and relaxes engagement thresholds, then rotates through broad fallback topics using a 24-hour search window.

Mobile approval is deliberately two-step because one Telegram button cannot both record a callback and open another app:

1. Tap **Approve on mobile**. The bot records the approval and removes the decision buttons.
2. Tap **Open X on phone**. The official mobile-friendly X Web Intent opens the matching reply or post composer and pre-populates the draft, so no manual paste is normally needed.

Once a reply target is pending or approved, the bot skips the same target to avoid duplicate approval cards. This history is persisted in `data/automation_approvals.json`, so deduplication survives bot restarts.

- **Open X on phone** uses the official mobile-friendly X Web Intent.
- **Copy draft** uses Telegram's native clipboard button for drafts up to 256 characters. Telegram limits native copy buttons to 256 characters; longer post drafts still use the pre-filled X mobile intent.

Review or edit the filled draft in X, then submit it yourself. A pending approval expires after 30 minutes. `Auto Run` still controls whether Gemini jobs from manually entered Telegram commands run automatically; when it is OFF, use **Run next job** before approving the returned draft.

The first scheduled run may wait for the configured interval. Scheduled automation processes one Gemini job per 30-second check, which keeps CPU use steadier on small VPS plans. Fixed `/tweettrend3` times are considered due for ten minutes, which allows for Chrome alarm delays or a briefly sleeping computer. Missed runs are not replayed in bulk.

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
- `Extension bridge timed out`: open the extension popup, click **Run next job**, or turn Auto Run ON.
- `Missing image data`: reload the extension, keep the Gemini tab visible, and confirm Gemini generated an image in an `<img>` tag.
- `No Google/RSS/X trend context found`: check internet access, RSS feed URLs, X cookies, or try a specific category like `/tweettrend3 news`.
