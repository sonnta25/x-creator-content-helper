# X Telegram Content Bot

Telegram bot for creating X/Twitter posts, replies, reply targets, multi-source trend briefs, and social images for a Vietnamese audience.

The current AI flow is:

```text
Telegram bot -> local extension bridge -> Gemini draft -> Gemini final -> Telegram bot
```

Gemini creates a first-pass draft using the existing analysis prompt, then Gemini humanizes/finalizes the output in a clean tab. Gemini also generates images through the Chrome extension. The bot does not use Ollama, Pollinations, Playwright, or official Gemini APIs.

## Commands

- `/tweet <topic>`: create one Vietnamese long-form X post and image from a topic.
- `/tweetx <topic/search>`: search X first, then create one long-form X post from live context.
- `/tweettrend3 [auto|trending|news|sport|entertainment]`: scan X, Google Trends, and RSS, then create three Vietnamese long-form post options with images and hashtags.
- `/dailybrief [trending|news|sport|entertainment]`: create daily-ready long-form post options from multi-source trend context.
- `/retweet <X post link> | <visual description>`: remix a source X post into a fresh original tweet and image.
- `/reply <tweet text or X post link>`: create one copy-ready text reply only.
- `/replytargets [query]`: auto-pick a hot topic, or use your query, then find fresh X posts worth replying to; sends reply text and post link as separate messages.
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

`/tweettrend3` sends each post as its own copy-ready message, then sends the generated image separately. It does not include option labels or score metadata in the copy-ready post message.

Trend commands scan X trends, Google Trends RSS, built-in Google News RSS feeds, and any custom RSS feeds from `TREND_RSS_URLS`. When X search is configured, the selected lead topic is enriched with recent X context from the last 24 hours. `/replytargets` focuses on recent posts from roughly the last 30 minutes and filters for stronger engagement/velocity when X exposes those metrics.

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
EXTENSION_BRIDGE_TIMEOUT_SECONDS=300

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
