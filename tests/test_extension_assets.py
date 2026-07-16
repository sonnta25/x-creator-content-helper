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


def test_gemini_recovery_does_not_leave_the_only_tab_on_about_blank() -> None:
    background_js = (PROJECT_ROOT / "browser_extension" / "background.js").read_text(
        encoding="utf-8"
    )

    assert 'url: "about:blank"' not in background_js
    assert "await waitForProviderReady(tab.id);" in background_js
    assert "function injectedProviderReady()" in background_js


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

    scripts = manifest.get("content_scripts", [])
    assert any(
        "https://gemini.google.com/*" in item.get("matches", [])
        and "gemini_heartbeat.js" in item.get("js", [])
        for item in scripts
    )
    assert 'message.action === "runtime-heartbeat"' in background_js
    assert 'chrome.runtime.sendMessage({ action: "runtime-heartbeat" }' in heartbeat_js
    assert "HEARTBEAT_INTERVAL_MS = 25000" in heartbeat_js
    assert "ensureGeminiHeartbeatInjected()" in background_js
    assert 'files: ["gemini_heartbeat.js"]' in background_js
    assert "__xContentBotRuntimeHeartbeat" in heartbeat_js
