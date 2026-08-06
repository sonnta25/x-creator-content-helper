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
Candidate ranking, monetization safety, and watch reservoir
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

For audience building, `/followtargets` creates a separate manual-follow shortlist.
It never follows, unfollows, likes, or messages an account automatically.

For a revenue-oriented setup, run `/money status` once and keep `/pace adaptive`
plus `/risk balanced` enabled. The bot learns priority authors from tracked results;
use `/watchauthor add @name` only when an account should be permanently pinned.
Payouts can be entered once per payout period; no Analytics CSV is required.

`/replytargets` and `/replyvideo` remain available when a specific discovery lane is
needed. Scheduled scans can run both lanes automatically.

## Telegram menu

Use `/start`, `/menu`, or `/help` to open the English menu. The main menu provides:

- **Start reply session**
- **Conversation inbox**
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

Finite mode controls do not require typing: **Creator goal**, **Reply safety**,
**Adaptive pace**, and **Experiments** open dedicated button menus containing every
available mode plus status and back navigation. Numeric ranges and free-form values
still use the private reply prompt.

## Commands

### Daily workflow

- `/session [10-120|status|stop]` — build, inspect, or stop a guided reply queue.
- `/inbox` — reopen pending original-author and verified-audience follow-ups.
- `/replytargets [topic|auto]` — find viral posts and create reply cards.
- `/replyvideo [topic]` — find fresh viral videos with low reply competition.
- `/followtargets` — find Vietnamese Premium accounts you do not currently follow.
- `/reply <text or X URL>` — write one standalone reply.
- `/replyreport [7d|30d]` — show tracked reply performance.
- `/wins [7d|30d|90d]` — show the strongest real reply angles as an insight bank.
- `/download <post URL>` — download images, carousels, videos, or Reels.

### Strategy and automation

- `/replygoal show|qualify|earn|network` — change the ranking objective.
- `/replyevery <5-1440>` — set the scheduled post-reply interval in minutes.
- `/videoevery <3-1440>` — set the scheduled video-reply interval.
- `/followevery <5-1440>` — set the follow-candidate interval. Its active windows are
  always the same windows configured for `/replyvideo`.
- `/replybatch show|targets <2-5>|video <2-5>` — set cards requested per run.
- `/replycap show|daily <1-2000>|author <1-25>` — inspect approved-card usage and
  set daily or per-author ceilings. Pending and rejected drafts do not consume caps;
  pending cards only reserve generation capacity until approved, rejected, or expired.
- `/pace show|conservative|adaptive|high|pause|resume` — control adaptive hourly
  card ceilings and health backoff.
- `/replylangs show|add|remove|set` — manage up to six X language codes.
- `/replylearn status|on|off|rollback|username @name` — control tracking and bounded
  learning.
- `/experiments status|on|off` — rotate and compare grounded reply formats.
- `/persona` — show or update niche, voice, and audience.
- `/watchauthor list|add @name|pin @name|remove @name|block @name|unblock @name|auto on|off|status`
  — inspect and control pinned, automatically learned, and blocked priority authors.
- `/risk show|strict|balanced|open` — control monetization filtering and anti-farming
  pace guardrails.
- `/money status|report [90d]|payout YYYY-MM-DD amount [USD]|set ...` — maintain
  monetization readiness and the payout feedback loop without CSV imports.
- `/profileaudit` — check whether the public profile converts reply viewers.

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

For growth-stage accounts, the final target portfolio now favors authors in the
8k-50k mid-tier conversion range, retains 50k-300k authors for reach, and leaves a
slot open for the strongest breakout regardless of account size. This is a soft
portfolio preference: exceptional momentum and low reply competition can still
outrank the preferred author tiers.

Selection adds a bounded audience-window bonus using `CREATOR_TIMEZONE`: Asia/Japan
at 07:00-11:00, Europe at 14:00-17:00, and US/global English at 20:00-23:30. Language
is only a proxy because most X posts expose no reliable country metadata. Outside
those windows the bot keeps global exploration neutral.

`REPLY_TARGET_MIN_VIEWS` is the normal selection baseline, not an unconditional hard
gate. When fewer than two posts survive, the bot re-ranks the same fresh results with
bounded momentum and view-floor fallbacks. The final tier can accept any visible view
signal, but it still keeps the freshness, root-post, active-card, deduplication, and
monetization-safety checks. If fewer than two real candidates remain, no tweet or URL
is invented and no Gemini batch is spent.

Posts that already have an active card are removed before each top-N ranking pass, so
previous winners cannot hide slightly lower-ranked unused candidates. If Gemini still
produces no card, the Telegram status reports exact final-check counts for active URLs,
stale opportunities, unknown URLs, similar drafts, and safety limits.

Per-author and Japanese anti-farming limits are also applied before the final top-five
cut. When a leading candidate has reached one of those limits, the bot continues down
the ranked pool and fills the batch from another eligible author or language instead of
submitting a batch that is guaranteed to create zero cards.

Priority authors are searched before generic trend lanes. Their posts still pass the
same freshness, public-momentum, reply-competition, deduplication, and safety gates;
watching an author is a ranking advantage, not an automatic approval.

Automatic author learning is enabled by default and uses only the bot's own tracked
public outcomes. An author becomes eligible after at least two replies, a strong safe-
content history, and evidence such as 20,000 median reply views, an original-author
response, or a strong relationship score. The bot keeps at most 20 learned authors
and can retire one after 30 days without interaction or after a sustained weak sample.
Authors added with `/watchauthor add` or `pin` are permanent and never auto-demoted.
`remove` allows later relearning; `block` removes the author and prevents relearning.

Each discovery scan rotates through the watchlist and, when both groups exist,
reserves query capacity for both pinned and learned authors. It does not repeatedly
search only the first four usernames. Use `/watchauthor auto off` to disable automatic
promotion and demotion without removing existing entries.

Every candidate receives a conservative monetization assessment. Green means no known
restricted category was detected from the supplied text or media metadata. Yellow marks
potentially restricted topics such as disasters, conflict, controversial political or
social issues, or strong language. Red marks high-risk categories such as betting,
gambling, explicit adult material, or obvious scam promotion. `earn` always excludes red;
`balanced` excludes red and downranks yellow; `strict` excludes both. `open` can retain
red only outside `earn`, with a visible warning. This classifier is not a legal
determination or a private X monetization decision.

Scheduled scans with no ready opportunity are silent and do not spend a Gemini job.
Authentication, rate-limit, bridge, or provider failures are still reported.

### `/followtargets`

This lane is independent of Gemini and reply-card limits. By default, it runs every
20 minutes only inside the `/replyvideo` active windows and proposes up to 12 people.
Each run:

1. refreshes and caches the posting account's Following list from `twscrape`;
2. searches recent Vietnamese posts to find active community members;
3. requires the account's X Premium/blue flag and excludes protected profiles;
4. excludes your own account, accounts already followed, and suggestions shown within
   the configured cooldown;
5. ranks moderate-size accounts by following/follower balance, recent activity, and
   Vietnamese profile signals; and
6. sends one Telegram button that opens each candidate's profile.

The Telegram card contains only a compact name/username list and one profile button per
candidate. Ranking remains internal. The bot never auto-follows or performs
follow/unfollow churn. If fewer than 12 truthful matches exist, it reports the smaller
list instead of inserting an already-followed or obviously unbalanced account.

### `/replyvideo`

The video lane gives Japanese candidates a stronger preference than `/replytargets`:
when available, each ranked batch starts with up to two Japanese videos, followed by
the strongest non-Japanese global candidate. If Japanese supply is scarce or blocked
by safety limits, the remaining slots fall back to other languages without failing the
run. Freshness, momentum, reply competition, monetization safety, and learned outcome
quality still apply; language preference never bypasses those gates.

Previously carded videos are likewise removed before strict, warm, and fill ranking;
the bot therefore continues down the fresh candidate pool instead of repeatedly asking
Gemini to process the same top videos.

When caption or X media text is reliable, the reply is grounded only in that text.
When it is not reliable and `REPLY_VIDEO_FRAME_ANALYSIS=true`, the bot downloads the
video, extracts a small number of representative frames, sends them to Gemini, and
removes temporary media after processing.

Global, English, Japanese, and Vietnamese `Top`/`Latest` searches are serialized with
tracking and account checks. This deliberately trades a little scan speed for reliable
operation when the twscrape pool contains only one usable cookie account.

Frames are evidence samples, not full audio or motion analysis. The prompt forbids
inventing speech, timing, identity, location, intent, or outcomes that are not visible
or stated.

### Reply safety and batch recovery

Replies follow the source language and must add a concrete observation, implication,
comparison, caveat, or reason. Generic agreement and question-only replies are
rejected. Deterministic checks also reject emoji-only drafts, canned Japanese/English
openings, URLs, hashtags, unrelated mentions, and self-promotion before a card reaches
Telegram. Near-copy detection covers recent pending and approved drafts, including
Japanese text without spaces.

Japanese replies default to natural `です/ます` social distance with strangers unless
the source is clearly casual. Balanced mode skips Japanese disaster, death, mourning,
war, and graphic-violence targets; strict mode excludes all yellow-risk topics. Prompts
for serious contexts forbid humor and engagement questions.

These controls support X's [Authenticity policy](https://help.x.com/en/rules-and-policies/authenticity)
and [Automation rules](https://help.x.com/en/rules-and-policies/x-automation?lang=browser):
the bot proposes a contextual draft, but the operator must read it and perform the
final Reply action on X. It does not auto-submit replies or treat viral keywords as
permission to contact people automatically.

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
Tap **Why?** to inspect rankability, post age, verified-audience proxy, experiment
bucket, author-watch status, and revenue-safety reasons only when needed.

After **Approve on mobile**, Telegram shows **Open X on phone**. This two-step design
is required because one Telegram button cannot both record a callback and open X.
Long replies that do not fit safely in a Web Intent remain available through
**Copy draft** or normal copy/paste.

Reply batches use one global delivery queue across `/replytargets`, `/replyvideo`,
guided sessions, and overlapping scheduled runs. Only one undecided reply card is
visible at a time. After an unrelated reply is approved, the bot keeps every later card
queued until the current reply-safety spacing has elapsed, then sends the oldest one
automatically; the operator no longer receives a "wait and tap again" alert. Rejecting
a card releases the next one immediately because no reply was posted. Author and
verified-audience conversation follow-ups remain immediate. The timer starts from
Telegram approval because the bot deliberately cannot observe the final manual X Reply
click.

The delivery window is 120-240 seconds in conservative pace, 60-120 seconds in
adaptive pace, and 30-60 seconds in high pace. Strict risk mode always keeps at least
the 120-240 second window. Open risk mode removes its language/category caps but no
longer disables card-delivery spacing.

The queue is bounded by `REPLY_PENDING_QUEUE_CAP` (default 5). Discovery pauses before
Gemini when fewer than two slots remain, preventing scheduled runs from creating drafts
faster than the operator can review them. A queued card older than two minutes is
re-fetched from X immediately before delivery; stale or newly crowded targets are
expired, while temporary X lookup failures retry without sending an unverified card.
Creation and Telegram delivery both use process-wide asynchronous locks, so overlapping
commands cannot create duplicate approvals or send the same card twice.
Target and video discovery also share one scan slot; their per-lane X timeouts therefore
start without competing against another full scan for the same twscrape cookie pool.

A queued card is marked as delivered only after Telegram returns a message receipt.
Transient Telegram delivery failures keep the card unsent and retry after 15, 30, 60,
then at most 120 seconds while its approval remains fresh. On the first restart after
upgrading, the bot also releases uncertain delayed cards created by the earlier
pre-receipt queue implementation.

Set the real posting username with:

```text
/replylearn username @your_x_username
```

The bot then looks for the manually posted reply on that public timeline; users do
not need to send the reply URL back. It samples public metrics around 15 minutes,
1 hour, 6 hours, and 24 hours. Original-author responses are checked more frequently
and immediately create a Telegram follow-up card with **Continue conversation** and
**Stop here**.

Tracking is work-budgeted with `REPLY_TRACKING_CHECKS_PER_CYCLE` (default 8). Recent
author responses are checked first, then due metric checkpoints, then older response
checks. This keeps a single twscrape cookie pool responsive even when the account has
hundreds of tracked replies.

One verified direct response to the user's reply may also enter `/inbox`. This broadens
the conversation workflow without drafting for every low-value notification. Follower
count is sampled across reply as well as legacy post windows, so follower lift no longer
depends on removed post-generation commands.

Learning is bounded. It adjusts strategy allocation using real approvals, edits,
public outcomes, language, source type, and posting hour. `/replyreport` also compares
author tiers, selection-age buckets, root-view stages, and audience windows, so the
8k-50k-author and 5k-50k-view "sweet spots" are verified against this account's own
public outcomes rather than accepted as universal rules. Strong real posted replies
may supply short style examples, but the prompt forbids copying their wording or
claims. The bot does not rewrite its source code or base prompt automatically.

Format experiments rotate concise statements, insight-then-question, confident
implications, and natural humor across different target posts. The bot never sends
multiple experimental replies to the same post. `/session` learns a bounded video/text
allocation and retains exploration plus relationship opportunities instead of using a
permanent 70% video mix.

## Revenue operations and account safety

`/money` stores only values deliberately entered by the user: checklist state,
verified-follower count, and occasional payout totals. It combines those values with
public tracked reply views to show a period-level efficiency proxy. It cannot see
verified Home Timeline impressions, subscriber tier, private organic-impression
eligibility, profile visits, or exact revenue attributable to one reply.

```text
/money set premium on
/money set stripe on
/money set identity on
/money set 2fa on
/money set verified_followers 540
/money payout 2026-08-01 125.40 USD
```

`/pace adaptive` converts the daily cap into a rolling hourly ceiling. `/risk` adds an
anti-reply-farming layer based on approved cards:

- `strict`: at most 12 approved replies/hour; Japanese 20/day and 2/hour; unrelated
  approvals are spaced by a stable 120-240 second safety window;
- `balanced` (recommended): at most 20/hour; Japanese 30/day and 6/hour; unrelated
  approvals are spaced by 60-120 seconds;
- `open`: removes these extra numeric heuristics but keeps quality, duplication,
  relevance, sensitive-topic, per-author, and manual-submission protections.

These values are conservative operator heuristics, not published X rate limits. X
prohibits bulk, duplicative, irrelevant, unsolicited, and aggressively automated
engagement rather than publishing a guaranteed safe reply count. A genuine author or
verified-audience follow-up is treated as a real conversation and is not delayed by the
heuristic spacing window. The daily and per-author hard ceilings still apply.

Three new
X-account errors inside one hour pause new card generation while tracking continues.
After checking `/setupcheck`, use `/pace resume`. `high` raises capacity but never
auto-posts; under `strict` or `balanced`, the anti-farming hourly ceiling still wins.

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
- `earn` adds a public verified-audience proxy and monetization safety; it cannot see X private
  monetization or subscriber-ranking data.
- `network` emphasizes author responses and repeat relationships.

The daily value counts only cards the user approves, not pending or rejected drafts,
and is not a guaranteed posting quota. Pending cards reserve available daily, hourly,
per-author, and Japanese generation slots so overlapping scans cannot produce a card
that is already impossible to approve. `/replycap show` displays global and Japanese
daily/hourly usage. `/profileaudit` also checks whether the recent timeline contains
original posts instead of looking reply-only.
Available candidates, deduplication, quality checks, batch size, X limits, and manual
submission still determine actual volume.

The legacy `premium_audience_score` field remains for data compatibility, but new UI
calls it a **verified-audience proxy**. It must not be interpreted as a payout score.

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
```

`setup.ps1` creates `.venv`, installs dependencies, asks for the Telegram token, and
creates or updates `.env` while preserving supported operator settings. It then starts
one hidden bot process, so a separate `start.ps1` call is not needed after setup.
Dependencies are constrained by `requirements.lock`, the exact versions validated by
the test suite. `start.ps1` refuses to modify the venv while a current or older bot copy
is running.
The locked `tzdata` package is required on Windows so Vietnam daily resets, audience
windows, and local-hour reports do not silently fall back to UTC.

Operational commands:

```powershell
.\scripts\windows\status.ps1
.\scripts\windows\stop.ps1
.\scripts\windows\start.ps1
.\scripts\windows\restart.ps1
```

After extracting an update, use `restart.ps1`. It stops bot processes from both the
current folder and recognized older `x-content-bot` folders, verifies they exited,
synchronizes locked dependencies, and starts exactly one updated instance.

Logs are written to `logs/bot.out.log` and `logs/bot.err.log`.

## Chrome extension setup

1. Open `chrome://extensions`.
2. Enable **Developer mode**.
3. Choose **Load unpacked** and select `browser_extension/`.
4. Log in to Gemini and X in the same normal Chrome profile.
5. Open the extension popup and make its bridge URL and token match `.env`.
6. Enable **Auto Run** for queued Gemini work and **Automation** for schedules.
7. Keep low-resource mode enabled on a small VPS.

The required extension version is `0.9.0`. After replacing project files, press
**Reload** on the extension card whenever `browser_extension/` changed.

Version `0.9.0` allows up to 60 seconds for Gemini's editor to accept and submit a
large reply-target prompt on a low-spec VPS, while still bounding frame upload and
DOM-read operations. It also reduces expensive shadow-DOM scans; fails an attempt with
no readable Gemini progress after at most two minutes; and wraps the complete attempt
in a separate deadline so a stuck Chrome API promise cannot look healthy merely by
sending heartbeats. With the normal 360-second bridge budget, one stalled attempt is
retried once on a fresh managed Gemini tab. Login, quota, rate-limit, and invalid-frame
errors fail immediately instead of wasting the second attempt. It also identifies the
real Gemini composer instead of sidebar search controls, recognizes localized and
shadow-DOM attachment menus, retries a missing upload control once on a fresh tab, and
falls back to caption/media-grounded video candidates when at least two remain.

The extension uses a lightweight watchdog only while a provider job is active and
requeues an interrupted job as soon as its heartbeat lease expires.
The active job ID remains persisted until completion or confirmed expiry. Heartbeat
injection is restored after every managed Gemini navigation, while a 30-second reclaim
watchdog keeps retrying across delayed MV3 alarms instead of relying on two one-shot
attempts. Video evidence frames are capped at 512 pixels and 350 KB to reduce Chrome
memory use. The extension cannot restart the entire Chrome process if Chrome itself is
closed or remains frozen.

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
REVENUE_OPS_PATH=data/revenue_ops.json

REPLY_TARGET_LANGUAGES=en,ja
REPLY_TARGET_MODE=balanced
CREATOR_GOAL=qualify
CREATOR_TIMEZONE=Asia/Ho_Chi_Minh

CREATOR_DAILY_REPLY_CAP=500
REPLY_AUTHOR_DAILY_CAP=5
REPLY_TARGET_BATCH_SIZE=3
REPLY_VIDEO_BATCH_SIZE=3
REPLY_PENDING_QUEUE_CAP=5

TELEGRAM_FOLLOW_TARGETS_MINUTES=20
FOLLOW_TARGET_BATCH_SIZE=12
FOLLOW_TARGET_COOLDOWN_HOURS=24
FOLLOW_TARGET_MIN_FOLLOWERS=100
FOLLOW_TARGET_MAX_FOLLOWERS=50000

REPLY_VIDEO_FRAME_ANALYSIS=true
REPLY_VIDEO_FRAME_COUNT=2
REPLY_LEARNING_ENABLED=true
REPLY_TRACKING_POLL_MINUTES=5
REPLY_TRACKING_CHECKS_PER_CYCLE=8
REPLY_DAILY_DIGEST_HOUR=22
```

Prefer `/importcookie`, `/replylangs`, `/replygoal`, `/replycap`, `/replybatch`,
`/replyevery`, `/videoevery`, and `/followevery` for settings supported from Telegram. Those commands
update the running bot immediately and persist supported values to `.env`.

Do not increase the bridge timeout simply to hide a stuck provider tab. Values are
clamped to 120-360 seconds, and extension `0.9.0` should recover or recycle a stalled
tab earlier.

The bridge serializes scheduled, manual, revision, and tracking Gemini requests. A
request waiting behind another job does not start its provider timeout until it reaches
the front of the queue, preventing false timeouts caused only by local job contention.

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

The package excludes `.env` variants except `.env.example`, cookie files,
`auth_token`/`ct0` exports, private key files, `data/`, logs, tests, virtual
environments, and Git metadata.

Run validation locally with:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
node --check browser_extension\background.js
```

## Troubleshooting

- **No reply-ready posts:** run `/setupcheck` and `/xaccounts`; refresh X cookies if
  the pool reports authentication, challenge, or rate-limit errors.
- **Chrome never claimed the job:** reload extension `0.9.0`, verify the bridge URL
  and token, and enable Auto Run or click **Run next job**.
- **Heartbeat stopped:** keep Chrome open. Version `0.9.0` reattaches the page heartbeat
  after Gemini navigation, preserves interrupted job state, and retries lease reclaim
  every 30 seconds. It still cannot recover a terminated or completely frozen Chrome
  process.
- **Gemini made no readable progress:** inspect the managed Gemini tab for login,
  verification, quota, or usage-limit messages.
- **Video frame upload failed:** confirm Gemini is logged in and reload the extension;
  set `REPLY_VIDEO_FRAME_ANALYSIS=false` if the VPS cannot handle frame analysis.
- **Dependency import traceback:** run `setup.ps1` again if `.venv` is missing or
  damaged. After normal code updates, use `restart.ps1`.
- **Telegram `Conflict: terminated by other getUpdates request`:** run `restart.ps1`
  from the updated folder; it removes recognized older bot copies before starting.
- **Old menu remains visible:** restart the updated bot, send `/menu` once, then select
  a command; the one-time keyboard will collapse.

## Data and security

- `.env`, X cookies, download cookies, `data/`, and Chrome profiles are private.
- Never commit or share live Telegram tokens, `auth_token`, or `ct0` values.
- The deployment ZIP intentionally excludes runtime credentials and history.
- Public metrics from twscrape are useful signals, not guaranteed X Analytics data.
- `data/revenue_ops.json` may contain payout totals and is excluded from the ZIP/repository.
- Manual final posting reduces automation risk but does not remove the policy or account
  risk of non-official X collection. Do not treat a configured volume ceiling as a safe quota.
- X and Gemini web interfaces can change; keep the extension and dependencies current.

## BUY ME A COFFEE

If this project saves time or helps grow your X account, you can support continued
development here:

[☕ Buy me a coffee](https://buymeacoffee.com/sonnta)
