import json
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_popup_reads_extension_version_from_manifest() -> None:
    manifest = json.loads(
        (PROJECT_ROOT / "browser_extension" / "manifest.json").read_text(encoding="utf-8")
    )
    popup_html = (PROJECT_ROOT / "browser_extension" / "popup.html").read_text(
        encoding="utf-8"
    )
    popup_js = (PROJECT_ROOT / "browser_extension" / "popup.js").read_text(
        encoding="utf-8"
    )

    assert manifest["version"]
    assert '<span id="version"></span>' in popup_html
    assert "chrome.runtime.getManifest().version" in popup_js
    assert f"v{manifest['version']}" not in popup_html


def test_replytargets_schedule_and_lookback_are_separate_multilingual_controls() -> None:
    background_js = (PROJECT_ROOT / "browser_extension" / "background.js").read_text(
        encoding="utf-8"
    )
    popup_js = (PROJECT_ROOT / "browser_extension" / "popup.js").read_text(
        encoding="utf-8"
    )
    popup_html = (PROJECT_ROOT / "browser_extension" / "popup.html").read_text(
        encoding="utf-8"
    )

    assert "replyTargetsMinutes: 15" in background_js
    assert "replyTargetsMaxAgeMinutes: 360" in background_js
    assert 'replyTargetsLanguages: "en,ja"' in background_js
    assert "reply_target_max_age_minutes: config.replyTargetsMaxAgeMinutes" in background_js
    assert "reply_target_languages: config.replyTargetsLanguages" in background_js
    assert "remote.reply_target_languages" in background_js
    assert "replyTargetsMaxAgeMinutes" in popup_js
    assert "replyTargetsLanguages" in popup_js
    assert "/replytargets scan interval" in popup_html
    assert "/replytargets maximum post age" in popup_html
    assert "/replytargets languages" in popup_html


def test_extension_scheduling_uses_configured_creator_timezone() -> None:
    background_js = (PROJECT_ROOT / "browser_extension" / "background.js").read_text(
        encoding="utf-8"
    )
    popup_js = (PROJECT_ROOT / "browser_extension" / "popup.js").read_text(
        encoding="utf-8"
    )
    popup_html = (PROJECT_ROOT / "browser_extension" / "popup.html").read_text(
        encoding="utf-8"
    )

    assert 'creatorTimezone: "Asia/Ho_Chi_Minh"' in background_js
    assert "saved.creatorTimezone || DEFAULTS.creatorTimezone" in background_js
    assert "function zonedDateParts" in background_js
    assert "config.creatorTimezone" in background_js
    assert "creatorTimezone" in popup_js
    assert "Creator timezone" in popup_html


def test_replyvideo_has_independent_five_minute_schedule() -> None:
    background_js = (PROJECT_ROOT / "browser_extension" / "background.js").read_text(
        encoding="utf-8"
    )
    popup_html = (PROJECT_ROOT / "browser_extension" / "popup.html").read_text(
        encoding="utf-8"
    )

    assert "replyVideoMinutes: 5" in background_js
    assert '"/automation/triggers/replyvideo"' in background_js
    assert "/replyvideo scan interval" in popup_html
    assert "/replyvideo topic" in popup_html


def test_followtargets_uses_twenty_minute_schedule_and_replyvideo_windows() -> None:
    background_js = (PROJECT_ROOT / "browser_extension" / "background.js").read_text(
        encoding="utf-8"
    )
    popup_html = (PROJECT_ROOT / "browser_extension" / "popup.html").read_text(
        encoding="utf-8"
    )

    assert "followTargetsMinutes: 20" in background_js
    assert '"/automation/triggers/followtargets"' in background_js
    assert "config.replyVideoWindows" in background_js
    assert "/followtargets scan interval" in popup_html
    assert "same active windows as /replyvideo" in popup_html


def test_removed_tweettrend3_scheduler_is_not_exposed() -> None:
    background_js = (PROJECT_ROOT / "browser_extension" / "background.js").read_text(
        encoding="utf-8"
    )
    popup_html = (PROJECT_ROOT / "browser_extension" / "popup.html").read_text(
        encoding="utf-8"
    )

    assert "/automation/triggers/tweettrend3" not in background_js
    assert "/tweettrend3 fixed times" not in popup_html
    assert "/tweettrend3 category" not in popup_html


def test_extension_uploads_replyvideo_frames_before_submitting_prompt() -> None:
    background_js = (PROJECT_ROOT / "browser_extension" / "background.js").read_text(
        encoding="utf-8"
    )

    assert "async function injectedAttachImages" in background_js
    assert "func: injectedAttachImages" in background_js
    assert "Array.isArray(job.attachments)" in background_js
    assert "Gemini did not confirm the uploaded representative frames" in background_js
    assert "const clickUploadAction" in background_js
    assert "const clickNearbyAttachmentMenu" in background_js
    assert 'label.includes("upload files")' in background_js
    assert 'label.includes("mo trinh don tai tep")' in background_js
    assert "composer=${composerDebug}" in background_js
    assert "if (direct.length && !alwaysDeep) return direct" in background_js
    assert "Array.from(new Set([...direct, ...deep]))" in background_js
    assert "triggers=${triggerLog.join" in background_js


def test_gemini_recovery_does_not_leave_the_only_tab_on_about_blank() -> None:
    background_js = (PROJECT_ROOT / "browser_extension" / "background.js").read_text(
        encoding="utf-8"
    )

    assert 'url: "about:blank"' not in background_js
    assert "await waitForProviderReady(tab.id);" in background_js
    assert "function injectedProviderReady()" in background_js


def test_gemini_recycle_opens_ready_replacement_before_closing_old_tabs() -> None:
    background_js = (PROJECT_ROOT / "browser_extension" / "background.js").read_text(
        encoding="utf-8"
    )
    recycle = background_js.split("async function recycleProviderTab()", 1)[1].split(
        "async function recycleProviderAfterFailure()", 1
    )[0]

    create_at = recycle.index("replacement = await chromeTabsCreate")
    ready_at = recycle.index("await waitForProviderReady(replacement.id)")
    track_new_at = recycle.index("await setManagedProviderTab(replacement.id)")
    remove_old_at = recycle.index("await chromeTabsRemove([oldTab.id])")
    assert create_at < ready_at < remove_old_at
    assert ready_at < track_new_at < remove_old_at
    assert "oldTabs" not in recycle
    assert "await setManagedProviderTab(oldTab.id)" in recycle


def test_normal_chrome_only_manages_one_gemini_tab() -> None:
    background_js = (PROJECT_ROOT / "browser_extension" / "background.js").read_text(
        encoding="utf-8"
    )
    get_tab = background_js.split("async function getOrCreateTab", 1)[1].split(
        "async function getManagedProviderTab", 1
    )[0]

    assert 'PROVIDER_TAB_ID_KEY = "managedProviderTabId"' in background_js
    assert "tabs.length === 1 ? tabs[0] : null" in get_tab
    assert "tabs.slice" not in get_tab
    assert "chromeTabsRemove" not in get_tab


def test_gemini_job_error_is_reported_before_tab_recovery() -> None:
    background_js = (PROJECT_ROOT / "browser_extension" / "background.js").read_text(
        encoding="utf-8"
    )
    text_job_catch = background_js.index(
        "if (reportError) {\n      await reportJobError(config, job.id, error);"
    )
    recycle_after_text_error = background_js.index(
        "await recycleProviderAfterFailure();",
        text_job_catch,
    )

    assert text_job_catch < recycle_after_text_error


def test_gemini_prompt_is_inserted_atomically_with_a_fallback() -> None:
    background_js = (PROJECT_ROOT / "browser_extension" / "background.js").read_text(
        encoding="utf-8"
    )

    assert 'document.execCommand("insertText", false, value)' in background_js
    assert "el.replaceChildren(document.createTextNode(value));" in background_js
    assert "expectedLength * 0.98" in background_js
    assert "for (const chunk of chunks)" not in background_js.split(
        "async function injectedSubmitPrompt(prompt)", 1
    )[1].split("function injectedReadProviderResponse", 1)[0]


def test_auto_run_self_heals_alarms_and_heartbeats_claimed_jobs() -> None:
    background_js = (PROJECT_ROOT / "browser_extension" / "background.js").read_text(
        encoding="utf-8"
    )

    assert "initializeRuntime().catch" in background_js
    assert "RUNTIME_WATCHDOG_ALARM" in background_js
    assert "chromeAlarmsGet(AUTO_ALARM)" in background_js
    assert "startJobHeartbeat(config, job.id)" in background_js
    assert "/heartbeat`" in background_js
    assert "BRIDGE_FETCH_TIMEOUT_MS = 15000" in background_js
    assert "remote.extension_bridge_timeout_seconds" in background_js
    assert "timeoutSeconds: bridgeTimeoutSeconds" in background_js
    assert "ACTIVE_PROVIDER_WATCHDOG_ALARM" in background_js
    assert "activeProviderWatchdogTick()" in background_js
    assert "ensureActiveProviderWatchdog()" in background_js
    assert "delayInMinutes: 0.5" in background_js
    assert "periodInMinutes: 0.5" in background_js
    assert "MAX_PROVIDER_TIMEOUT_SECONDS = 360" in background_js


def test_gemini_tab_wakes_the_extension_runtime_when_alarms_disappear() -> None:
    manifest = json.loads(
        (PROJECT_ROOT / "browser_extension" / "manifest.json").read_text(encoding="utf-8")
    )
    background_js = (PROJECT_ROOT / "browser_extension" / "background.js").read_text(
        encoding="utf-8"
    )
    heartbeat_js = (
        PROJECT_ROOT / "browser_extension" / "gemini_heartbeat.js"
    ).read_text(encoding="utf-8")

    assert "content_scripts" not in manifest
    assert 'message.action === "runtime-heartbeat"' in background_js
    assert 'chrome.runtime.sendMessage({ action: "runtime-heartbeat" }' in heartbeat_js
    assert "HEARTBEAT_INTERVAL_MS = 20000" in heartbeat_js
    assert "ensureGeminiHeartbeatInjected()" in background_js
    assert 'files: ["gemini_heartbeat.js"]' in background_js
    assert "await injectGeminiHeartbeat(tab.id)" in background_js
    assert "__xContentBotRuntimeHeartbeat" in heartbeat_js


def test_low_resource_mode_avoids_bootstrap_work_and_reduces_dom_polling() -> None:
    background_js = (PROJECT_ROOT / "browser_extension" / "background.js").read_text(
        encoding="utf-8"
    )
    popup_html = (PROJECT_ROOT / "browser_extension" / "popup.html").read_text(
        encoding="utf-8"
    )

    assert "lowResourceMode: true" in background_js
    assert "pollSeconds: 60" in background_js
    assert 'id="lowResourceMode"' in popup_html
    assert "runtimeConfig.lowResourceMode ? 2 : 1" in background_js
    assert "await delay(best ? 2500 : 4000)" in background_js
    assert "setInterval(tick, 20000)" in background_js
    assert "HEARTBEAT_INJECTION_MIN_INTERVAL_MS = 5 * 60 * 1000" in background_js
    assert "lastHeartbeatInjectionAt" in background_js
    assert "TAB_SCRIPT_TIMEOUT_MS = 15000" in background_js
    assert "ATTACHMENT_SCRIPT_TIMEOUT_MS = 45000" in background_js
    assert "PROMPT_SUBMISSION_TIMEOUT_MS = 60000" in background_js
    assert "PROVIDER_NO_PROGRESS_TIMEOUT_MS = 120000" in background_js
    assert "PROVIDER_MAX_ATTEMPTS = 2" in background_js
    assert "PROVIDER_RETRY_MIN_JOB_TIMEOUT_SECONDS = 240" in background_js
    assert "async function withDeadline" in background_js
    assert "function isRetryableProviderError" in background_js
    assert "async function executeTabScript" in background_js
    assert "pollCount % 4 === 0" in background_js
    assert "allowDeepScan" in background_js


def test_stalled_gemini_job_is_bounded_and_retried_once_on_a_fresh_tab() -> None:
    background_js = (PROJECT_ROOT / "browser_extension" / "background.js").read_text(
        encoding="utf-8"
    )
    text_job = background_js.split("async function runGeminiTextJob", 1)[1].split(
        "function startJobHeartbeat", 1
    )[0]

    assert "for (let attempt = 1; attempt <= maxAttempts; attempt += 1)" in text_job
    assert "finalOutput = await withDeadline(" in text_job
    assert "await recycleProviderAfterFailure();" in text_job
    assert "!isRetryableProviderError(error)" in text_job
    assert '"usage limit"' in text_job
    assert '"rate limit"' in text_job
    retry_classifier = background_js.split(
        "function isRetryableProviderError", 1
    )[1].split("function startJobHeartbeat", 1)[0]
    assert '"image file input was not found"' not in retry_classifier

    auto_alarm = background_js.split("async function ensureAutoAlarm", 1)[1].split(
        "async function ensureAutomationAlarm", 1
    )[0]
    automation_alarm = background_js.split(
        "async function ensureAutomationAlarm", 1
    )[1].split("async function automationTick", 1)[0]
    assert "runJobs(" not in auto_alarm
    assert "automationTick(" not in automation_alarm


def test_interrupted_provider_job_is_reclaimed_after_its_lease() -> None:
    background_js = (PROJECT_ROOT / "browser_extension" / "background.js").read_text(
        encoding="utf-8"
    )

    assert 'const PROVIDER_RECOVERY_ALARM = "x-content-bot-provider-recovery"' in background_js
    assert 'const PROVIDER_RECOVERY_RETRY_ALARM = "x-content-bot-provider-recovery-retry"' in background_js
    assert 'const PROVIDER_RECOVERY_WATCHDOG_ALARM = "x-content-bot-provider-recovery-watchdog"' in background_js
    assert 'const ACTIVE_PROVIDER_JOB_ID_KEY = "activeProviderJobId"' in background_js
    assert "await recoverInterruptedProviderJob();" in background_js
    assert "delayInMinutes: 0.1" in background_js
    assert "delayInMinutes: 1.5" in background_js
    assert "periodInMinutes: 0.5" in background_js
    assert "runJobs({ force: true, maxJobs: 1 })" in background_js
    assert "await setActiveProviderJobId(job.id)" in background_js
    assert "async function getActiveProviderJobId()" in background_js
    assert "chromeStorageGet({ [ACTIVE_PROVIDER_JOB_ID_KEY]: \"\" })" in background_js
    assert "chromeStorageSessionGet({ [ACTIVE_PROVIDER_JOB_ID_KEY]: \"\" })" in background_js
    recovery = background_js.split(
        "async function recoverInterruptedProviderJob()", 1
    )[1].split("async function clearProviderRecoveryState", 1)[0]
    assert 'setActiveProviderJobId("")' not in recovery
    assert "await ensureGeminiHeartbeatInjected(true)" in recovery


def test_gemini_navigation_reattaches_the_page_heartbeat() -> None:
    background_js = (PROJECT_ROOT / "browser_extension" / "background.js").read_text(
        encoding="utf-8"
    )
    prepare = background_js.split("async function prepareProviderTab", 1)[1].split(
        "async function recordProviderJobSuccess", 1
    )[0]

    assert "chrome.tabs.onUpdated.addListener" in background_js
    assert prepare.count("await injectGeminiHeartbeat(tab.id)") >= 4
    assert "await waitForProviderReady(tab.id);\n    await injectGeminiHeartbeat(tab.id);" in prepare
