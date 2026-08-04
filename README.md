# X Creator Reply Bot

Telegram bot for finding reply opportunities on X, drafting source-grounded replies
with Gemini in normal Chrome, tracking public outcomes, and downloading social-post
media. It does not require the official X API or periodic X Analytics CSV imports.

Final posting always remains manual: the bot prepares and tracks replies, but the
user reviews the X composer and presses **Reply**.

## How the bot works

```text
X discovery with twscrape
        ↓
Candidate ranking and watch reservoir
        ↓
Gemini job through the local Chrome extension
        ↓
Telegram approval card
        ↓
User reviews and posts manually on X
        ↓
Bot discovers the posted reply and tracks public results
```

The normal daily workflow is:

1. Run `/setupcheck` after deployment or cookie changes.
2. Choose a goal with `/replygoal qualify`, `earn`, or `network`.
3. Run `/session` to build a guided queue of posts and videos.
4. Review one Telegram card at a time. Use **Alternative** or **Shorter** only when
   needed.
5. Tap **Approve on mobile**, then **Open X on phone**, review the filled composer,
   and submit manually.
6. Use `/inbox` when an original author responds and `/replyreport 7d` to review
   performance.

`/replytargets` and `/replyvideo` remain available when a specific discovery lane is
needed. Scheduled scans can run both lanes automatically.

## Telegram menu

Use `/start`, `/menu`, or `/help` to open the English menu. The main menu provides:

- **Start reply session**
- **Author replies**
- **Performance**
- **Settings**
- **Help** and **Cancel**

The keyboard is not permanent. It hides after one selection so it does not cover the
Telegram composer while reply cards are being reviewed. Use `/menu` to open it again.

**Settings** contains the less-frequent controls for viral discovery, schedules,
tracking, X accounts, downloads, persona, languages, batch sizes, limits, and creator
goals. Buttons such as **Alternative**, **Shorter**, **Copy draft**, and
**Approve on mobile** are attached directly to an approval card and are independent
of the menu keyboard.

When a menu action requires a value, the bot opens a private reply prompt. The prompt
expires after five minutes and can be closed with `/cancel`. Direct command forms,
such as `/replytargets AI agents`, continue to work.

## Commands

### Daily workflow

- `/session [10-120|status|stop]` — build, inspect, or stop a guided reply queue.
- `/inbox` — reopen pending follow-ups after original-author responses.
- `/replytargets [topic|auto]` — find viral posts and create reply cards.
- `/replyvideo [topic]` — find fresh viral videos with low reply competition.
- `/reply <text or X URL>` — write one standalone reply.
- `/replyreport [7d|30d]` — show tracked reply performance.
- `/download <post URL>` — download images, carousels, videos, or Reels.

### Strategy and automation

- `/replygoal show|qualify|earn|network` — change the ranking objective.
- `/replyevery <5-1440>` — set the scheduled post-reply interval in minutes.
- `/videoevery <3-1440>` — set the scheduled video-reply interval.
- `/replybatch show|targets <2-5>|video <2-5>` — set cards requested per run.
- `/replycap show|daily <1-2000>|author <1-25>` — set daily and per-author ceilings.
- `/replylangs show|add|remove|set` — manage up to six X language codes.
- `/replylearn status|on|off|rollback|username @name` — control tracking and bounded
  learning.
- `/persona` — show or update niche, voice, and audience.

### X account and health

- `/importcookie [name] <auth_token=...; ct0=...>` — add an authenticated X session.
- `/xaccounts` — list cookie accounts without exposing cookie values.
- `/xremove <name>` — remove an account from the twscrape pool.
- `/setupcheck` — inspect X access, owner username, schedules, reservoir, learning,
  timezone, approval chat, and stale approvals.
- `/cancel` — cancel a command waiting for input.

Removed post-generation commands such as `/tweet`, `/xtweet`, `/today`,
`/dailybrief`, `/tweettrend3`, `/retweet`, and `/automationhere` are not part of the
current bot.

## Discovery and reply generation

### `/replytargets`

The bot searches authenticated X `Top` and `Latest` results, current trends, broad
language lanes, and an optional creator-niche lane. It excludes replies and reposts;
original and quote posts can remain eligible.

`REPLY_TARGET_LANGUAGES` controls language, not country. No country filter is applied
because reliable location metadata is absent from most posts. Japanese, English,
Korean, Vietnamese, and other supported X language codes can therefore be mixed.

The first observation of a candidate is normally stored in
`data/reply_watchlist.json`. Later scans re-fetch watched tweet IDs and measure recent
view and engagement movement, allowing the bot to catch both early breakouts and
posts that accelerate several hours later. Reply competition is scored separately:
crowded threads and dominant existing replies reduce opportunity even when the root
post is viral.

Scheduled scans with no ready opportunity are silent and do not spend a Gemini job.
Authentication, rate-limit, bridge, or provider failures are still reported.

### `/replyvideo`

The video lane prioritizes fresh global viral video and low reply competition. When
caption or X media text is reliable, the reply is grounded only in that text. When it
is not reliable and `REPLY_VIDEO_FRAME_ANALYSIS=true`, the bot downloads the video,
extracts a small number of representative frames, sends them to Gemini, and removes
temporary media after processing.

Frames are evidence samples, not full audio or motion analysis. The prompt forbids
inventing speech, timing, identity, location, intent, or outcomes that are not visible
or stated.

### Reply safety and batch recovery

Replies follow the source language and must add a concrete observation, implication,
comparison, caveat, or reason. Generic agreement and question-only replies are
rejected.

Gemini is asked for 2-5 drafts. If one draft is malformed, the bot preserves good
drafts and repairs only unresolved URLs. A blank URL is recovered only when its
`@author` uniquely matches a permitted candidate URL; URLs are never guessed by list
position. If fewer than two safe replies remain, one small rescue job requests only
the missing slot. The bot never invents a second tweet merely to fill a batch.

## Approval cards and tracking

A reply card contains only the information needed for a decision:

- target X URL;
- concise Vietnamese source summary;
- Vietnamese translation of the proposed reply;
- original copy-ready reply in the source language.

Views, competition, velocity, strategy, and opportunity scores remain internal for
ranking and learning instead of cluttering the Telegram card.

After **Approve on mobile**, Telegram shows **Open X on phone**. This two-step design
is required because one Telegram button cannot both record a callback and open X.
Long replies that do not fit safely in a Web Intent remain available through
**Copy draft** or normal copy/paste.

Set the real posting username with:

```text
/replylearn username @your_x_username
```

The bot then looks for the manually posted reply on that public timeline; users do
not need to send the reply URL back. It samples public metrics around 15 minutes,
1 hour, 6 hours, and 24 hours. Original-author responses are checked more frequently
and immediately create a Telegram follow-up card with **Continue conversation** and
**Stop here**.

Learning is bounded. It adjusts strategy allocation using real approvals, edits,
public outcomes, language, source type, and posting hour. Strong real posted replies
may supply short style examples, but the prompt forbids copying their wording or
claims. The bot does not rewrite its source code or base prompt automatically.

## Limits and goals

Defaults are designed for higher-volume reply workflows:

```env
CREATOR_GOAL=qualify
CREATOR_DAILY_REPLY_CAP=500
REPLY_AUTHOR_DAILY_CAP=5
REPLY_SESSION_MINUTES=20
REPLY_TARGET_BATCH_SIZE=3
REPLY_VIDEO_BATCH_SIZE=3
```

- `qualify` emphasizes reach and reply visibility.
- `earn` adds public verified/Premium-audience proxies; it cannot see X private
  monetization or subscriber-ranking data.
- `network` emphasizes author responses and repeat relationships.

The daily value is an approval-card ceiling, not a guaranteed posting quota.
Available candidates, deduplication, quality checks, batch size, X limits, and manual
submission still determine actual volume.

## Windows VPS setup

Requirements:

- Windows 10/11 or Windows Server with Python 3.11+;
- normal Google Chrome running under the same Windows user as the bot;
- logged-in Gemini and X sessions;
- a Telegram bot token from BotFather.

From PowerShell in the extracted project folder:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\windows\setup.ps1
.\scripts\windows\start.ps1
```

`setup.ps1` creates `.venv`, installs dependencies, asks for the Telegram token, and
creates `.env`. `start.ps1` checks dependency changes before starting one hidden bot
process.

Operational commands:

```powershell
.\scripts\windows\status.ps1
.\scripts\windows\stop.ps1
.\scripts\windows\start.ps1
```

Logs are written to `logs/bot.out.log` and `logs/bot.err.log`.

## Chrome extension setup

1. Open `chrome://extensions`.
2. Enable **Developer mode**.
3. Choose **Load unpacked** and select `browser_extension/`.
4. Log in to Gemini and X in the same normal Chrome profile.
5. Open the extension popup and make its bridge URL and token match `.env`.
6. Enable **Auto Run** for queued Gemini work and **Automation** for schedules.
7. Keep low-resource mode enabled on a small VPS.

The required extension version is `0.8.4`. After replacing project files, press
**Reload** on the extension card whenever `browser_extension/` changed.

Version `0.8.4` allows up to 60 seconds for Gemini's editor to accept and submit a
large reply-target prompt on a low-spec VPS, while still bounding frame upload and
DOM-read operations. It also
reduces expensive shadow-DOM scans; fails a no-progress Gemini response after four
minutes; and reclaims an interrupted job after the bridge lease expires. It cannot
restart the entire Chrome process if Chrome itself is closed or crashes.

## Important `.env` settings

Keep `.env` private. A practical configuration starts with:

```env
TELEGRAM_BOT_TOKEN=replace_me
TELEGRAM_APPROVAL_CHAT_ID=replace_me

CONTENT_PROVIDER=extension_bridge
EXTENSION_BRIDGE_HOST=127.0.0.1
EXTENSION_BRIDGE_PORT=8765
EXTENSION_BRIDGE_TOKEN=replace_with_a_private_local_token
EXTENSION_BRIDGE_TIMEOUT_SECONDS=360

X_OWNER_USERNAME=your_x_username
X_ACCOUNTS_DB=data/twscrape_accounts.db

REPLY_TARGET_LANGUAGES=en,ja
REPLY_TARGET_MODE=balanced
CREATOR_GOAL=qualify
CREATOR_TIMEZONE=Asia/Ho_Chi_Minh

CREATOR_DAILY_REPLY_CAP=500
REPLY_AUTHOR_DAILY_CAP=5
REPLY_TARGET_BATCH_SIZE=3
REPLY_VIDEO_BATCH_SIZE=3

REPLY_VIDEO_FRAME_ANALYSIS=true
REPLY_VIDEO_FRAME_COUNT=2
REPLY_LEARNING_ENABLED=true
REPLY_TRACKING_POLL_MINUTES=5
REPLY_DAILY_DIGEST_HOUR=22
```

Prefer `/importcookie`, `/replylangs`, `/replygoal`, `/replycap`, `/replybatch`,
`/replyevery`, and `/videoevery` for settings supported from Telegram. Those commands
update the running bot immediately and persist supported values to `.env`.

Do not increase the bridge timeout simply to hide a stuck provider tab. Extension
`0.8.4` should fail and recycle a stalled tab earlier.

## Media downloads

`/download` uses `yt-dlp` for supported video platforms and `gallery-dl` as a bounded
image/carousel fallback. Common public TikTok, Douyin, Xiaohongshu/RedNote,
Facebook/Instagram Reel, and X URLs may work, subject to platform changes,
authentication, regional restrictions, and anti-bot checks.

Optional downloader settings:

```env
DOWNLOAD_MAX_FILE_MB=45
DOWNLOAD_TIMEOUT_SECONDS=180
DOWNLOAD_COOKIES_FILE=data/download-cookies.txt
DOWNLOAD_COOKIES_FROM_BROWSER=chrome
DOWNLOAD_BROWSER_PROFILE=Default
```

Browser-cookie extraction requires Chrome and the bot to run under the same Windows
user. Cookies preserve an authorized session; they do not bypass CAPTCHA or restore
a revoked login. Only download media you are authorized to save.

## Updating and packaging

To update an existing VPS installation:

1. Stop the bot.
2. Extract the new ZIP over the project directory.
3. Preserve the existing `.env` and `data/` directory.
4. Reload the Chrome extension if its files changed.
5. Start the bot; dependency synchronization runs automatically.

Create a clean deployment ZIP with:

```powershell
.\scripts\windows\package.ps1
```

The package excludes `.env`, cookies, `data/`, logs, tests, virtual environments, and
Git metadata.

Run validation locally with:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
node --check browser_extension\background.js
```

## Troubleshooting

- **No reply-ready posts:** run `/setupcheck` and `/xaccounts`; refresh X cookies if
  the pool reports authentication, challenge, or rate-limit errors.
- **Chrome never claimed the job:** reload extension `0.8.4`, verify the bridge URL
  and token, and enable Auto Run or click **Run next job**.
- **Heartbeat stopped:** keep Chrome open. Version `0.8.4` can recover an interrupted
  MV3 worker or tab operation, but not a terminated Chrome process.
- **Gemini made no readable progress:** inspect the managed Gemini tab for login,
  verification, quota, or usage-limit messages.
- **Video frame upload failed:** confirm Gemini is logged in and reload the extension;
  set `REPLY_VIDEO_FRAME_ANALYSIS=false` if the VPS cannot handle frame analysis.
- **Dependency import traceback:** run `setup.ps1` again if `.venv` is missing or
  damaged. Normal updates only require `start.ps1`.
- **Telegram `Conflict: terminated by other getUpdates request`:** stop duplicate bot
  processes and keep exactly one polling instance.
- **Old menu remains visible:** restart the updated bot, send `/menu` once, then select
  a command; the one-time keyboard will collapse.

## Data and security

- `.env`, X cookies, download cookies, `data/`, and Chrome profiles are private.
- Never commit or share live Telegram tokens, `auth_token`, or `ct0` values.
- The deployment ZIP intentionally excludes runtime credentials and history.
- Public metrics from twscrape are useful signals, not guaranteed X Analytics data.
- X and Gemini web interfaces can change; keep the extension and dependencies current.

## BUY ME A COFFEE

If this project saves time or helps grow your X account, you can support continued
development here:

[☕ Buy me a coffee](https://buymeacoffee.com/sonnta)
