const DEFAULTS = {
  bridgeUrl: "http://127.0.0.1:8765",
  token: "local-bridge-change-me",
  timeoutSeconds: 360,
  autoRun: false,
  pollSeconds: 60,
  lowResourceMode: true,
  automationEnabled: false,
  activeStart: "08:00",
  activeEnd: "22:00",
  creatorTimezone: "Asia/Ho_Chi_Minh",
  replyTargetsMinutes: 15,
  replyTargetsMaxAgeMinutes: 360,
  replyTargetsLanguages: "en,ja",
  replyTargetsQuery: "",
  replyVideoMinutes: 5,
  replyVideoQuery: "",
  replyVideoWindows: "08:00-11:00,12:00-14:00,19:00-22:00",
  followTargetsMinutes: 20,
  nextReplyTargetsAt: 0,
  lastReplyTargetsTriggeredAt: 0,
  replyTargetsConfigUpdatedAt: 0,
  nextReplyVideoAt: 0,
  lastReplyVideoTriggeredAt: 0,
  replyVideoConfigUpdatedAt: 0,
  nextFollowTargetsAt: 0,
  lastFollowTargetsTriggeredAt: 0,
  followTargetsConfigUpdatedAt: 0,
  lastStatus: "Ready."
};

const AUTO_ALARM = "x-content-bot-auto-run";
const AUTOMATION_ALARM = "x-content-bot-scheduled-approvals";
const RUNTIME_WATCHDOG_ALARM = "x-content-bot-runtime-watchdog";
const FOLLOW_UP_ALARM = "x-content-bot-follow-up";
const PROVIDER_RECOVERY_ALARM = "x-content-bot-provider-recovery";
const PROVIDER_RECOVERY_RETRY_ALARM = "x-content-bot-provider-recovery-retry";
const PROVIDER_RECOVERY_WATCHDOG_ALARM = "x-content-bot-provider-recovery-watchdog";
const ACTIVE_PROVIDER_WATCHDOG_ALARM = "x-content-bot-active-provider-watchdog";
const BRIDGE_FETCH_TIMEOUT_MS = 15000;
const TAB_SCRIPT_TIMEOUT_MS = 15000;
const ATTACHMENT_SCRIPT_TIMEOUT_MS = 45000;
const PROMPT_SUBMISSION_TIMEOUT_MS = 60000;
const PROVIDER_NO_PROGRESS_TIMEOUT_MS = 120000;
const PROVIDER_MAX_ATTEMPTS = 2;
const PROVIDER_RETRY_MIN_JOB_TIMEOUT_SECONDS = 240;
const PROVIDER_RETRY_RESERVE_SECONDS = 60;
const PROVIDER_ATTEMPT_MIN_TIMEOUT_SECONDS = 60;
const PROVIDER_ATTEMPT_WATCHDOG_GRACE_SECONDS = 30;
const FINAL_PROVIDER_URL = "https://gemini.google.com/app";
const FINAL_PROVIDER_NAME = "Gemini";
const PROVIDER_RECYCLE_AFTER_JOBS = 10;
const MIN_PROVIDER_TIMEOUT_SECONDS = 120;
const MAX_PROVIDER_TIMEOUT_SECONDS = 360;
const PROVIDER_JOB_COUNT_KEY = "providerSuccessfulJobs";
const PROVIDER_TAB_ID_KEY = "managedProviderTabId";
const ACTIVE_PROVIDER_JOB_ID_KEY = "activeProviderJobId";
const HEARTBEAT_INJECTION_MIN_INTERVAL_MS = 5 * 60 * 1000;
let running = false;
let lastStatusCache = "";
let lastRuntimeBootstrapAt = 0;

chrome.runtime.onInstalled.addListener(() => {
  initializeRuntime().catch((error) => setStatus(`Startup repair error: ${error.message || error}`));
});

chrome.runtime.onStartup.addListener(() => {
  initializeRuntime().catch((error) => setStatus(`Startup repair error: ${error.message || error}`));
});

// Chrome recommends checking critical alarms whenever an extension service
// worker starts because alarm persistence can be unpredictable across browser
// restarts and extension reloads. Top-level bootstrap runs on every worker wake.
initializeRuntime().catch((error) => setStatus(`Startup repair error: ${error.message || error}`));

chrome.storage.onChanged.addListener((changes, areaName) => {
  if (areaName !== "local") return;
  if (changes.autoRun || changes.pollSeconds || changes.lowResourceMode) {
    ensureAutoAlarm();
  }
  if (
    changes.automationEnabled || changes.lowResourceMode ||
    changes.activeStart || changes.activeEnd ||
    changes.creatorTimezone ||
    changes.replyTargetsMinutes || changes.replyTargetsMaxAgeMinutes ||
    changes.replyTargetsLanguages || changes.replyVideoMinutes ||
    changes.replyVideoWindows || changes.followTargetsMinutes
  ) {
    if (changes.replyTargetsMinutes) {
      const minutes = Math.max(5, Number(changes.replyTargetsMinutes.newValue || DEFAULTS.replyTargetsMinutes));
      chromeStorageSet({ nextReplyTargetsAt: Date.now() + minutes * 60 * 1000 })
        .then(() => ensureAutomationAlarm());
    } else if (changes.replyVideoMinutes) {
      const minutes = Math.max(3, Number(changes.replyVideoMinutes.newValue || DEFAULTS.replyVideoMinutes));
      chromeStorageSet({ nextReplyVideoAt: Date.now() + minutes * 60 * 1000 })
        .then(() => ensureAutomationAlarm());
    } else if (changes.followTargetsMinutes) {
      const minutes = Math.max(5, Number(
        changes.followTargetsMinutes.newValue || DEFAULTS.followTargetsMinutes
      ));
      chromeStorageSet({ nextFollowTargetsAt: Date.now() + minutes * 60 * 1000 })
        .then(() => ensureAutomationAlarm());
    } else {
      ensureAutomationAlarm();
    }
  }
});

// Gemini can replace its document while opening a fresh conversation. Any
// page-injected heartbeat disappears with that document, so reattach it after
// every completed navigation of the one tab managed by this extension.
chrome.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
  if (
    changeInfo.status !== "complete" ||
    !String(tab && tab.url || "").startsWith("https://gemini.google.com/")
  ) {
    return;
  }
  getManagedProviderTab(FINAL_PROVIDER_URL)
    .then((managed) => managed && managed.id === tabId
      ? injectGeminiHeartbeat(tabId)
      : null)
    .catch(() => null);
});

chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === AUTO_ALARM) {
    runJobs({ force: false, maxJobs: 1 })
      .catch((error) => setStatus(`Auto Run error: ${error.message || error}`));
  }
  if (alarm.name === AUTOMATION_ALARM) {
    automationTick()
      .then((result) => result && result.runJobs
        ? runJobs({ force: true, maxJobs: 1 })
        : null)
      .catch((error) => setStatus(`Automation error: ${error.message || error}`));
  }
  if (alarm.name === RUNTIME_WATCHDOG_ALARM) {
    bootstrapRuntime()
      .catch((error) => setStatus(`Runtime watchdog error: ${error.message || error}`));
  }
  if (alarm.name === FOLLOW_UP_ALARM) {
    runJobs({ force: false, maxJobs: 1 })
      .catch((error) => setStatus(`Follow-up error: ${error.message || error}`));
  }
  if (
    alarm.name === PROVIDER_RECOVERY_ALARM ||
    alarm.name === PROVIDER_RECOVERY_RETRY_ALARM ||
    alarm.name === PROVIDER_RECOVERY_WATCHDOG_ALARM
  ) {
    runJobs({ force: true, maxJobs: 1 })
      .catch((error) => setStatus(`Provider recovery error: ${error.message || error}`));
  }
  if (alarm.name === ACTIVE_PROVIDER_WATCHDOG_ALARM) {
    activeProviderWatchdogTick()
      .catch((error) => setStatus(`Active job watchdog error: ${error.message || error}`));
  }
});

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (!message || !message.action) return false;

  if (message.action === "run-now") {
    runJobs({ force: true, maxJobs: 1 })
      .then((result) => sendResponse(result))
      .catch((error) => sendResponse({ ok: false, error: error.message || String(error) }));
    return true;
  }

  if (message.action === "save-config") {
    saveConfig(message.config || {})
      .then(() => Promise.all([ensureAutoAlarm(), ensureAutomationAlarm()]))
      .then(() => sendResponse({ ok: true }))
      .catch((error) => sendResponse({ ok: false, error: error.message || String(error) }));
    return true;
  }

  if (message.action === "get-state") {
    bootstrapRuntime()
      .then(() => loadConfig())
      .then((config) => sendResponse({ ok: true, config }))
      .catch((error) => sendResponse({ ok: false, error: error.message || String(error) }));
    return true;
  }

  if (message.action === "runtime-heartbeat") {
    loadConfig()
      .then(async (config) => {
        await chromeStorageSessionSet({ lastRuntimeHeartbeatAt: Date.now() });
        if (Date.now() - lastRuntimeBootstrapAt >= 120000) {
          await bootstrapRuntime(config);
        }
        await recoverInterruptedProviderJob();
      })
      .then(() => sendResponse({ ok: true }))
      .catch((error) => sendResponse({ ok: false, error: error.message || String(error) }));
    return true;
  }

  if (message.action === "diagnose-gemini-dom") {
    diagnoseProviderDom(FINAL_PROVIDER_URL)
      .then((report) => sendResponse({ ok: true, report }))
      .catch((error) => sendResponse({ ok: false, error: error.message || String(error) }));
    return true;
  }

  return false;
});

async function bootstrapRuntime(config = null) {
  const runtimeConfig = config || await loadConfig();
  await Promise.all([
    ensureWatchdogAlarm(runtimeConfig),
    ensureAutoAlarm(runtimeConfig),
    ensureAutomationAlarm(runtimeConfig)
  ]);
  lastRuntimeBootstrapAt = Date.now();
}

async function initializeRuntime() {
  await ensureGeminiHeartbeatInjected();
  const config = await loadConfig();
  await bootstrapRuntime(config);
  await recoverInterruptedProviderJob();
}

async function getActiveProviderJobId() {
  const [local, session] = await Promise.all([
    chromeStorageGet({ [ACTIVE_PROVIDER_JOB_ID_KEY]: "" }),
    chromeStorageSessionGet({ [ACTIVE_PROVIDER_JOB_ID_KEY]: "" })
  ]);
  return String(
    session[ACTIVE_PROVIDER_JOB_ID_KEY] ||
    local[ACTIVE_PROVIDER_JOB_ID_KEY] ||
    ""
  );
}

async function setActiveProviderJobId(jobId) {
  const value = String(jobId || "");
  await Promise.all([
    chromeStorageSet({ [ACTIVE_PROVIDER_JOB_ID_KEY]: value }),
    chromeStorageSessionSet({ [ACTIVE_PROVIDER_JOB_ID_KEY]: value })
  ]);
}

async function recoverInterruptedProviderJob() {
  if (running) return false;
  const interruptedJobId = await getActiveProviderJobId();
  if (!interruptedJobId) return false;
  await ensureGeminiHeartbeatInjected(true);
  await ensureActiveProviderWatchdog();
  // Keep the durable job ID until completion or an explicit expired response.
  // Quick one-shot attempts reduce latency; the repeating watchdog survives a
  // delayed/lost MV3 alarm and keeps trying across the server lease boundary.
  if (!await chromeAlarmsGet(PROVIDER_RECOVERY_ALARM)) {
    chrome.alarms.create(PROVIDER_RECOVERY_ALARM, { delayInMinutes: 0.1 });
  }
  if (!await chromeAlarmsGet(PROVIDER_RECOVERY_RETRY_ALARM)) {
    chrome.alarms.create(PROVIDER_RECOVERY_RETRY_ALARM, { delayInMinutes: 1.5 });
  }
  if (!await chromeAlarmsGet(PROVIDER_RECOVERY_WATCHDOG_ALARM)) {
    chrome.alarms.create(PROVIDER_RECOVERY_WATCHDOG_ALARM, {
      delayInMinutes: 0.5,
      periodInMinutes: 0.5
    });
  }
  await setStatus(
    `Detected interrupted Gemini job ${interruptedJobId}. ` +
    "Recovery will retry until the bridge lease is reclaimed or expires."
  );
  return true;
}

async function clearProviderRecoveryState({ clearJobId = false } = {}) {
  await Promise.all([
    chromeAlarmsClear(ACTIVE_PROVIDER_WATCHDOG_ALARM),
    chromeAlarmsClear(PROVIDER_RECOVERY_ALARM),
    chromeAlarmsClear(PROVIDER_RECOVERY_RETRY_ALARM),
    chromeAlarmsClear(PROVIDER_RECOVERY_WATCHDOG_ALARM)
  ]);
  if (clearJobId) {
    await setActiveProviderJobId("");
  }
}

async function ensureActiveProviderWatchdog() {
  const existing = await chromeAlarmsGet(ACTIVE_PROVIDER_WATCHDOG_ALARM);
  if (!existing) {
    chrome.alarms.create(ACTIVE_PROVIDER_WATCHDOG_ALARM, {
      delayInMinutes: 0.5,
      periodInMinutes: 0.5
    });
  }
}

async function activeProviderWatchdogTick() {
  const jobId = await getActiveProviderJobId();
  if (!jobId) {
    await chromeAlarmsClear(ACTIVE_PROVIDER_WATCHDOG_ALARM);
    return false;
  }
  if (!running) {
    return recoverInterruptedProviderJob();
  }
  const config = await loadConfig();
  try {
    await bridgeFetch(config, `/jobs/${jobId}/heartbeat`, {
      method: "POST",
      body: {},
      timeoutMs: 5000
    });
  } catch (error) {
    if (String(error && (error.message || error)).includes("Unknown or expired job")) {
      await clearProviderRecoveryState({ clearJobId: true });
      await setStatus(`Interrupted Gemini job ${jobId} has expired on the bridge.`);
      return false;
    }
    // The regular 20-second heartbeat remains primary. A stale server lease is
    // reclaimed by recoverInterruptedProviderJob if this worker is restarted.
  }
  return true;
}

async function ensureGeminiHeartbeatInjected(force = false) {
  const now = Date.now();
  const session = await chromeStorageSessionGet({ lastHeartbeatInjectionAt: 0 });
  const activeJobId = await getActiveProviderJobId();
  if (
    !force &&
    !activeJobId &&
    now - Number(session.lastHeartbeatInjectionAt || 0) < HEARTBEAT_INJECTION_MIN_INTERVAL_MS
  ) {
    return;
  }
  let tab = await getManagedProviderTab(FINAL_PROVIDER_URL);
  if (!tab) {
    const tabs = await chromeTabsQuery({ url: "https://gemini.google.com/*" });
    // A sole tab is safe to adopt. With several normal-Chrome Gemini tabs,
    // wait for the first real job to create a dedicated automation tab.
    tab = tabs.length === 1 ? tabs[0] : null;
    if (tab && tab.id) {
      await setManagedProviderTab(tab.id);
    }
  }
  if (tab && tab.id) {
    await injectGeminiHeartbeat(tab.id);
  }
  await chromeStorageSessionSet({ lastHeartbeatInjectionAt: now });
}

async function injectGeminiHeartbeat(tabId) {
  try {
    await executeTabScript(
      {
        target: { tabId },
        files: ["gemini_heartbeat.js"]
      },
      { timeoutMs: TAB_SCRIPT_TIMEOUT_MS, label: "Gemini heartbeat injection" }
    );
  } catch (_error) {
    // The managed tab can navigate or close between lookup and injection. The
    // next provider job or recovery pass will select one tab and retry.
  }
}

async function ensureWatchdogAlarm(config = null) {
  const runtimeConfig = config || await loadConfig();
  const periodInMinutes = runtimeConfig.lowResourceMode ? 2 : 1;
  const existing = await chromeAlarmsGet(RUNTIME_WATCHDOG_ALARM);
  if (!existing || Number(existing.periodInMinutes || 0) !== periodInMinutes) {
    chrome.alarms.create(RUNTIME_WATCHDOG_ALARM, { periodInMinutes });
  }
}

async function ensureAutoAlarm(config = null) {
  const runtimeConfig = config || await loadConfig();
  if (!runtimeConfig.autoRun) {
    await chromeAlarmsClear(AUTO_ALARM);
    return;
  }
  const minimumMinutes = runtimeConfig.lowResourceMode ? 1 : 0.5;
  const periodInMinutes = Math.max(minimumMinutes, runtimeConfig.pollSeconds / 60);
  const existing = await chromeAlarmsGet(AUTO_ALARM);
  if (!existing || Number(existing.periodInMinutes || 0) !== periodInMinutes) {
    chrome.alarms.create(AUTO_ALARM, { periodInMinutes });
  }
}

async function ensureAutomationAlarm(config = null) {
  const runtimeConfig = config || await loadConfig();
  if (!runtimeConfig.automationEnabled) {
    await chromeAlarmsClear(AUTOMATION_ALARM);
    return;
  }
  if (!runtimeConfig.nextReplyTargetsAt) {
    await chromeStorageSet({
      nextReplyTargetsAt: Date.now() + runtimeConfig.replyTargetsMinutes * 60 * 1000
    });
  }
  if (!runtimeConfig.nextReplyVideoAt) {
    await chromeStorageSet({
      nextReplyVideoAt: Date.now() + runtimeConfig.replyVideoMinutes * 60 * 1000
    });
  }
  if (!runtimeConfig.nextFollowTargetsAt) {
    await chromeStorageSet({
      nextFollowTargetsAt: Date.now() + runtimeConfig.followTargetsMinutes * 60 * 1000
    });
  }
  const periodInMinutes = runtimeConfig.lowResourceMode ? 1 : 0.5;
  const existing = await chromeAlarmsGet(AUTOMATION_ALARM);
  if (!existing || Number(existing.periodInMinutes || 0) !== periodInMinutes) {
    chrome.alarms.create(AUTOMATION_ALARM, { periodInMinutes });
  }
}

async function automationTick() {
  let config = await loadConfig();
  if (!config.automationEnabled) {
    return { runJobs: false };
  }
  config = await syncTelegramAutomationConfig(config);
  if (!isInsideActiveWindow(
    new Date(),
    config.activeStart,
    config.activeEnd,
    config.creatorTimezone
  )) {
    return { runJobs: Boolean(config.automationRunning) };
  }

  const now = new Date();
  const nowMs = now.getTime();
  let shouldRunJobs = Boolean(config.automationRunning);
  if (!config.nextReplyTargetsAt) {
    await chromeStorageSet({
      nextReplyTargetsAt: nowMs + config.replyTargetsMinutes * 60 * 1000
    });
  } else if (nowMs >= config.nextReplyTargetsAt) {
    await setStatus("Starting scheduled /replytargets...");
    const trigger = await bridgeFetch(config, "/automation/triggers/replytargets", {
      method: "POST",
      body: {
        query: config.replyTargetsQuery,
        reply_targets_minutes: config.replyTargetsMinutes,
        reply_target_max_age_minutes: config.replyTargetsMaxAgeMinutes,
        reply_target_languages: config.replyTargetsLanguages
      }
    });
    if (trigger.status === "accepted") {
      await chromeStorageSet({
        nextReplyTargetsAt: nowMs + config.replyTargetsMinutes * 60 * 1000,
        lastReplyTargetsTriggeredAt: nowMs
      });
      return { runJobs: true };
    }
  }

  if (!config.nextReplyVideoAt) {
    await chromeStorageSet({
      nextReplyVideoAt: nowMs + config.replyVideoMinutes * 60 * 1000
    });
  } else if (
    nowMs >= config.nextReplyVideoAt &&
    isInsideScheduleWindows(now, config.replyVideoWindows, config.creatorTimezone)
  ) {
    await setStatus("Starting scheduled /replyvideo...");
    const trigger = await bridgeFetch(config, "/automation/triggers/replyvideo", {
      method: "POST",
      body: {
        query: config.replyVideoQuery,
        reply_video_minutes: config.replyVideoMinutes
      }
    });
    if (trigger.status === "accepted") {
      await chromeStorageSet({
        nextReplyVideoAt: nowMs + config.replyVideoMinutes * 60 * 1000,
        lastReplyVideoTriggeredAt: nowMs
      });
      return { runJobs: true };
    }
  }

  if (!config.nextFollowTargetsAt) {
    await chromeStorageSet({
      nextFollowTargetsAt: nowMs + config.followTargetsMinutes * 60 * 1000
    });
  } else if (
    nowMs >= config.nextFollowTargetsAt &&
    isInsideScheduleWindows(now, config.replyVideoWindows, config.creatorTimezone)
  ) {
    await setStatus("Starting scheduled /followtargets...");
    const trigger = await bridgeFetch(config, "/automation/triggers/followtargets", {
      method: "POST",
      body: { follow_targets_minutes: config.followTargetsMinutes }
    });
    if (trigger.status === "accepted") {
      await chromeStorageSet({
        nextFollowTargetsAt: nowMs + config.followTargetsMinutes * 60 * 1000,
        lastFollowTargetsTriggeredAt: nowMs
      });
    }
  }

  return { runJobs: shouldRunJobs };
}

async function syncTelegramAutomationConfig(config) {
  const remote = await bridgeFetch(config, "/automation/config");
  const minutes = Number(remote.reply_targets_minutes || 0);
  const updatedAt = Number(remote.reply_targets_updated_at || 0);
  const videoMinutes = Number(remote.reply_video_minutes || 0);
  const videoUpdatedAt = Number(remote.reply_video_updated_at || 0);
  const followMinutes = Number(remote.follow_targets_minutes || 0);
  const followUpdatedAt = Number(remote.follow_targets_updated_at || 0);
  const bridgeTimeoutSeconds = boundedProviderTimeout(
    remote.extension_bridge_timeout_seconds
  );
  const automationRunning = Boolean(remote.automation_running);
  if (
    bridgeTimeoutSeconds !== config.timeoutSeconds
  ) {
    await chromeStorageSet({ timeoutSeconds: bridgeTimeoutSeconds });
    config = { ...config, timeoutSeconds: bridgeTimeoutSeconds };
  }
  const creatorTimezone = String(
    remote.creator_timezone || config.creatorTimezone || DEFAULTS.creatorTimezone
  );
  if (creatorTimezone !== config.creatorTimezone) {
    await chromeStorageSet({ creatorTimezone });
    config = { ...config, creatorTimezone };
  }
  const replyTargetsLanguages = String(
    remote.reply_target_languages || config.replyTargetsLanguages || DEFAULTS.replyTargetsLanguages
  ).trim();
  if (replyTargetsLanguages && replyTargetsLanguages !== config.replyTargetsLanguages) {
    await chromeStorageSet({ replyTargetsLanguages });
    config = { ...config, replyTargetsLanguages };
  }
  if (Number.isFinite(videoMinutes) && videoMinutes >= 3) {
    const videoIntervalChanged = videoMinutes !== config.replyVideoMinutes;
    const videoRevisionChanged = (
      videoUpdatedAt > 0 && videoUpdatedAt !== config.replyVideoConfigUpdatedAt
    );
    if (videoIntervalChanged || videoRevisionChanged) {
      const nextReplyVideoAt = Date.now() + videoMinutes * 60 * 1000;
      await chromeStorageSet({
        replyVideoMinutes: videoMinutes,
        replyVideoConfigUpdatedAt: videoUpdatedAt,
        nextReplyVideoAt
      });
      config = {
        ...config,
        replyVideoMinutes: videoMinutes,
        replyVideoConfigUpdatedAt: videoUpdatedAt,
        nextReplyVideoAt
      };
    }
  }
  if (Number.isFinite(followMinutes) && followMinutes >= 5) {
    const followIntervalChanged = followMinutes !== config.followTargetsMinutes;
    const followRevisionChanged = (
      followUpdatedAt > 0 && followUpdatedAt !== config.followTargetsConfigUpdatedAt
    );
    if (followIntervalChanged || followRevisionChanged) {
      const nextFollowTargetsAt = Date.now() + followMinutes * 60 * 1000;
      await chromeStorageSet({
        followTargetsMinutes: followMinutes,
        followTargetsConfigUpdatedAt: followUpdatedAt,
        nextFollowTargetsAt
      });
      config = {
        ...config,
        followTargetsMinutes: followMinutes,
        followTargetsConfigUpdatedAt: followUpdatedAt,
        nextFollowTargetsAt
      };
    }
  }
  if (!Number.isFinite(minutes) || minutes < 5) {
    return { ...config, automationRunning };
  }
  const intervalChanged = minutes !== config.replyTargetsMinutes;
  const revisionChanged = updatedAt > 0 && updatedAt !== config.replyTargetsConfigUpdatedAt;
  if (!intervalChanged && !revisionChanged) {
    return { ...config, automationRunning };
  }
  const nextReplyTargetsAt = Date.now() + minutes * 60 * 1000;
  await chromeStorageSet({
    replyTargetsMinutes: minutes,
    replyTargetsConfigUpdatedAt: updatedAt,
    nextReplyTargetsAt
  });
  return {
    ...config,
    replyTargetsMinutes: minutes,
    replyTargetsConfigUpdatedAt: updatedAt,
    nextReplyTargetsAt,
    automationRunning
  };
}

function isInsideActiveWindow(date, start, end, timezone = DEFAULTS.creatorTimezone) {
  const parts = zonedDateParts(date, timezone);
  const current = parts.hour * 60 + parts.minute;
  const startMinutes = clockMinutes(start, 0);
  const endMinutes = clockMinutes(end, 24 * 60 - 1);
  if (startMinutes === endMinutes) return true;
  if (startMinutes < endMinutes) {
    return current >= startMinutes && current < endMinutes;
  }
  return current >= startMinutes || current < endMinutes;
}

function isInsideScheduleWindows(date, value, timezone = DEFAULTS.creatorTimezone) {
  const windows = String(value || "").split(",").map((item) => item.trim()).filter(Boolean);
  if (!windows.length) return true;
  return windows.some((windowValue) => {
    const parts = windowValue.split("-").map((item) => item.trim());
    return parts.length === 2 && isInsideActiveWindow(date, parts[0], parts[1], timezone);
  });
}

function zonedDateParts(date, timezone) {
  let formatter;
  try {
    formatter = new Intl.DateTimeFormat("en-CA", {
      timeZone: String(timezone || DEFAULTS.creatorTimezone),
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      hourCycle: "h23"
    });
  } catch (_error) {
    formatter = new Intl.DateTimeFormat("en-CA", {
      timeZone: DEFAULTS.creatorTimezone,
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      hourCycle: "h23"
    });
  }
  const values = {};
  for (const part of formatter.formatToParts(date)) {
    if (part.type !== "literal") values[part.type] = part.value;
  }
  return {
    year: Number(values.year),
    month: Number(values.month),
    day: Number(values.day),
    hour: Number(values.hour),
    minute: Number(values.minute),
    dateKey: `${values.year}-${values.month}-${values.day}`
  };
}

function clockMinutes(value, fallback) {
  const match = /^(\d{1,2}):(\d{2})$/.exec(String(value || "").trim());
  if (!match) return fallback;
  const hours = Number(match[1]);
  const minutes = Number(match[2]);
  if (hours > 23 || minutes > 59) return fallback;
  return hours * 60 + minutes;
}

function scheduledTimeToday(now, value) {
  const minutes = clockMinutes(value, 0);
  const result = new Date(now);
  result.setHours(Math.floor(minutes / 60), minutes % 60, 0, 0);
  return result;
}

function localDateKey(date) {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

async function runJobs({ force, maxJobs }) {
  if (running) {
    return { ok: true, status: "already-running" };
  }

  const config = await loadConfig();
  if (!force && !config.autoRun) {
    return { ok: true, status: "auto-off" };
  }

  running = true;
  try {
    let processed = 0;
    for (let i = 0; i < maxJobs; i += 1) {
      const didRun = await runOneJob(config);
      if (!didRun) break;
      processed += 1;
    }
    if (processed === 0) {
      await setStatus(`No pending job. Last checked ${new Date().toLocaleTimeString()}.`);
    } else {
      // The bot can queue a repair job immediately after receiving this result.
      // Keep one low-frequency follow-up wake so
      // it is not left waiting if the primary repeating alarm disappears.
      chrome.alarms.create(FOLLOW_UP_ALARM, { delayInMinutes: 1 });
    }
    return { ok: true, processed };
  } catch (error) {
    await setStatus(`Error: ${error.message || error}`);
    return { ok: false, error: error.message || String(error) };
  } finally {
    running = false;
  }
}

async function runOneJob(config) {
  await setStatus("Checking bridge...");
  const next = await bridgeFetch(config, "/jobs/next");
  if (!next.job) {
    return false;
  }

  const job = next.job;
  await runGeminiTextJob(config, job);
  return true;
}

async function runGeminiTextJob(config, job, { reportError = true } = {}) {
  let providerStarted = false;
  let providerCompleted = false;
  await setActiveProviderJobId(job.id);
  await ensureActiveProviderWatchdog();
  const stopHeartbeat = startJobHeartbeat(config, job.id);
  try {
    const finalPrompt = job.final_prompt;
    if (!finalPrompt) {
      throw new Error(`Missing ${FINAL_PROVIDER_NAME} prompt for job ${job.id}.`);
    }
    await setStatus(
      `Job ${job.id}\nUsing existing ${FINAL_PROVIDER_NAME} tab...`
    );
    providerStarted = true;
    const attachments = Array.isArray(job.attachments) ? job.attachments : [];
    const maxAttempts = config.timeoutSeconds >= PROVIDER_RETRY_MIN_JOB_TIMEOUT_SECONDS
      ? PROVIDER_MAX_ATTEMPTS
      : 1;
    const attemptTimeoutSeconds = maxAttempts > 1
      ? Math.max(
        PROVIDER_ATTEMPT_MIN_TIMEOUT_SECONDS,
        Math.floor((config.timeoutSeconds - PROVIDER_RETRY_RESERVE_SECONDS) / maxAttempts)
      )
      : config.timeoutSeconds;
    let finalOutput = "";
    for (let attempt = 1; attempt <= maxAttempts; attempt += 1) {
      await setStatus(
        `Job ${job.id}\n${FINAL_PROVIDER_NAME} attempt ${attempt}/${maxAttempts}...`
      );
      try {
        finalOutput = await withDeadline(
          runProviderPrompt(
            FINAL_PROVIDER_URL,
            finalPrompt,
            attemptTimeoutSeconds,
            attachments
          ),
          (attemptTimeoutSeconds + PROVIDER_ATTEMPT_WATCHDOG_GRACE_SECONDS) * 1000,
          `${FINAL_PROVIDER_NAME} attempt ${attempt}/${maxAttempts}`
        );
        break;
      } catch (error) {
        if (attempt >= maxAttempts || !isRetryableProviderError(error)) {
          throw error;
        }
        await setStatus(
          `Gemini attempt ${attempt}/${maxAttempts} stalled. ` +
          "Opening one fresh managed tab and retrying once..."
        );
        await recycleProviderAfterFailure();
      }
    }
    if (!finalOutput) {
      throw new Error("Gemini retry finished without a readable response.");
    }
    providerCompleted = true;

    await setStatus(`Job ${job.id}\nReturning final output...`);
    await bridgeFetch(config, `/jobs/${job.id}/result`, {
      method: "POST",
      body: { output: finalOutput }
    });
    const lifecycle = await recordProviderJobSuccess();
    const lifecycleText = lifecycle.recycled
      ? `Gemini tab recycled after ${PROVIDER_RECYCLE_AFTER_JOBS} successful jobs.`
      : `Gemini tab remains open (${lifecycle.count}/${PROVIDER_RECYCLE_AFTER_JOBS} jobs before recycle).`;
    await setStatus(`Done. ${lifecycleText}\n\n${finalOutput.slice(0, 600)}`);
  } catch (error) {
    if (reportError) {
      await reportJobError(config, job.id, error);
    }
    if (providerStarted && !providerCompleted) {
      await recycleProviderAfterFailure();
    }
    throw error;
  } finally {
    stopHeartbeat();
    if (await getActiveProviderJobId() === String(job.id)) {
      await clearProviderRecoveryState({ clearJobId: true });
    }
  }
}

async function withDeadline(promise, timeoutMs, label) {
  let timer = null;
  try {
    return await Promise.race([
      promise,
      new Promise((_, reject) => {
        timer = setTimeout(() => {
          reject(new Error(`${label} exceeded its bounded ${Math.round(timeoutMs / 1000)}-second watchdog.`));
        }, timeoutMs);
      })
    ]);
  } finally {
    if (timer !== null) clearTimeout(timer);
  }
}

function isRetryableProviderError(error) {
  const message = String(error && (error.message || error) || "").toLowerCase();
  const nonRetryableMarkers = [
    "usage limit",
    "quota",
    "rate limit",
    "reached your limit",
    "login",
    "sign in",
    "missing gemini prompt",
    "invalid image data"
  ];
  return !nonRetryableMarkers.some((marker) => message.includes(marker));
}

function startJobHeartbeat(config, jobId) {
  let stopped = false;
  const tick = async () => {
    if (stopped) return;
    try {
      await bridgeFetch(config, `/jobs/${jobId}/heartbeat`, {
        method: "POST",
        body: {},
        timeoutMs: 5000
      });
    } catch (_error) {
      // A temporary bridge failure is tolerated. If the worker actually dies,
      // the server-side lease expires and another worker can reclaim the job.
    }
  };
  tick();
  const timer = setInterval(tick, 20000);
  return () => {
    stopped = true;
    clearInterval(timer);
  };
}

async function reportJobError(config, jobId, error) {
  try {
    await bridgeFetch(config, `/jobs/${jobId}/error`, {
      method: "POST",
      body: { error: error.message || String(error) }
    });
  } catch (_reportError) {
    // If the bridge itself is unreachable, keep the original error visible in the popup.
  }
}

async function executeTabScript(details, { timeoutMs = TAB_SCRIPT_TIMEOUT_MS, label = "Tab script" } = {}) {
  let timer = null;
  try {
    return await Promise.race([
      chrome.scripting.executeScript(details),
      new Promise((_, reject) => {
        timer = setTimeout(() => {
          reject(new Error(`${label} did not respond within ${Math.round(timeoutMs / 1000)} seconds.`));
        }, timeoutMs);
      })
    ]);
  } finally {
    if (timer !== null) clearTimeout(timer);
  }
}

async function runProviderPrompt(url, prompt, timeoutSeconds, attachments = []) {
  const tab = await prepareProviderTab(url);
  const provider = providerNameFromUrl(url);
  const submittedPrompt = provider === "Gemini" ? compactPromptForSingleInput(prompt) : prompt;
  if (attachments.length) {
    await setStatus(`Uploading ${attachments.length} representative frame(s) to ${provider}...`);
    const [attachmentResult] = await executeTabScript(
      {
        target: { tabId: tab.id },
        func: injectedAttachImages,
        args: [attachments]
      },
      { timeoutMs: ATTACHMENT_SCRIPT_TIMEOUT_MS, label: "Gemini frame upload" }
    );
    const upload = attachmentResult ? attachmentResult.result : null;
    if (!upload || !upload.ok) {
      const detail = upload && upload.error ? upload.error : "Image attachment upload failed.";
      const debug = upload && upload.debug ? ` ${upload.debug}` : "";
      throw new Error(`${detail}${debug}`);
    }
  }
  await setStatus(`Submitting prompt to ${provider}...`);

  const [submitResult] = await executeTabScript(
    {
      target: { tabId: tab.id },
      func: injectedSubmitPrompt,
      args: [submittedPrompt]
    },
    { timeoutMs: PROMPT_SUBMISSION_TIMEOUT_MS, label: "Gemini prompt submission" }
  );
  const submit = submitResult ? submitResult.result : null;
  if (!submit || !submit.ok) {
    const error = submit && submit.error ? submit.error : `Could not submit prompt to ${provider}.`;
    const debug = submit && submit.debug ? ` ${submit.debug}` : "";
    throw new Error(`${error}${debug}`);
  }

  await setStatus(
    `${provider} prompt submitted.\n${submit.debug || ""}\nWaiting for response...`
  );

  const started = Date.now();
  const timeoutMs = timeoutSeconds * 1000;
  const noProgressTimeoutMs = Math.min(
    PROVIDER_NO_PROGRESS_TIMEOUT_MS,
    Math.max(60000, timeoutMs - 15000)
  );
  let best = "";
  let last = "";
  let stableSince = Date.now();
  let lastStatusAt = 0;
  let lastDebug = "";
  let pollCount = 0;

  while (Date.now() - started < timeoutMs) {
    pollCount += 1;
    const allowDeepScan = Boolean(best) || pollCount % 4 === 0;
    const [readResult] = await executeTabScript(
      {
        target: { tabId: tab.id },
        func: injectedReadProviderResponse,
        args: [submittedPrompt, submit.before || "", allowDeepScan]
      },
      { timeoutMs: TAB_SCRIPT_TIMEOUT_MS, label: "Gemini response DOM read" }
    );
    const value = readResult ? readResult.result : null;
    if (value && value.fatalError) {
      throw new Error(`Gemini stopped the job: ${value.fatalError}`);
    }
    if (value && value.ok && String(value.text || "").trim()) {
      const current = String(value.text).trim();
      if (current && current !== submit.before) {
        best = current;
      }
      if (current !== last) {
        last = current;
        stableSince = Date.now();
      }
      if (best.length > 20 && Date.now() - stableSince > 4500) {
        return best;
      }
    }
    if (value && value.debug) {
      lastDebug = value.debug;
    }
    if (Date.now() - lastStatusAt > 15000) {
      await setStatus(
        `Waiting for ${provider} response...\n${lastDebug || "No readable candidate yet."}`
      );
      lastStatusAt = Date.now();
    }
    if (!best && Date.now() - started >= noProgressTimeoutMs) {
      throw new Error(
        `Gemini produced no readable progress for ${Math.round(noProgressTimeoutMs / 1000)} seconds. ` +
        `${lastDebug || "The provider tab may be stuck or rate limited."}`
      );
    }
    await delay(best ? 2500 : 4000);
  }

  if (best) {
    return best;
  }
  throw new Error(`Timed out waiting for ${provider} response. ${lastDebug}`);
}

function providerNameFromUrl(url) {
  const hostname = new URL(url).hostname;
  if (hostname.includes("gemini")) return "Gemini";
  return "Provider";
}

function compactPromptForSingleInput(prompt) {
  return String(prompt || "")
    .replace(/\r\n/g, "\n")
    .replace(/[ \t]+\n/g, "\n")
    .replace(/\n[ \t]+/g, "\n")
    .replace(/\n{3,}/g, "\n\n")
    .replace(/[ \t]{2,}/g, " ")
    .trim();
}

function delay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function diagnoseProviderDom(url) {
  const tab = await getOrCreateTab(url);
  await chromeTabsUpdate(tab.id, { active: true });
  await waitForTabComplete(tab.id);
  const [result] = await executeTabScript(
    {
      target: { tabId: tab.id },
      func: injectedDiagnoseDom
    },
    { timeoutMs: TAB_SCRIPT_TIMEOUT_MS, label: "Gemini DOM diagnosis" }
  );
  const report = result ? result.result : "";
  if (!report) {
    throw new Error(`No DOM diagnostic report from ${url}`);
  }
  await setStatus(report);
  return report;
}

async function getOrCreateTab(url) {
  const managed = await getManagedProviderTab(url);
  if (managed) return managed;

  const origin = new URL(url).origin;
  const tabs = await chromeTabsQuery({ url: `${origin}/*` });
  // In normal Chrome, never close or take over several user-owned Gemini tabs.
  // Reuse a sole existing tab; otherwise create one dedicated managed tab.
  let tab = tabs.length === 1 ? tabs[0] : null;
  if (!tab) {
    tab = await chromeTabsCreate({ url, active: true });
  }
  if (!tab || !tab.id) {
    throw new Error("Chrome did not return a managed Gemini tab.");
  }
  await setManagedProviderTab(tab.id);
  return tab;
}

async function getManagedProviderTab(url = FINAL_PROVIDER_URL) {
  const session = await chromeStorageSessionGet({ [PROVIDER_TAB_ID_KEY]: 0 });
  const tabId = Number(session[PROVIDER_TAB_ID_KEY] || 0);
  if (!tabId) return null;
  try {
    const tab = await chromeTabsGet(tabId);
    const expectedOrigin = new URL(url).origin;
    if (tab && tab.id && String(tab.url || "").startsWith(expectedOrigin)) {
      return tab;
    }
  } catch (_error) {
    // The user may have closed the previously managed tab.
  }
  await chromeStorageSessionSet({ [PROVIDER_TAB_ID_KEY]: 0 });
  return null;
}

async function setManagedProviderTab(tabId) {
  await chromeStorageSessionSet({ [PROVIDER_TAB_ID_KEY]: Number(tabId || 0) });
}

async function prepareProviderTab(url) {
  const tab = await getOrCreateTab(url);
  await chromeTabsUpdate(tab.id, { active: true });
  await waitForTabComplete(tab.id);
  await injectGeminiHeartbeat(tab.id);

  try {
    await waitForProviderReady(tab.id);
  } catch (_error) {
    await chromeTabsUpdate(tab.id, { url, active: true });
    await waitForTabComplete(tab.id);
    await waitForProviderReady(tab.id);
    await injectGeminiHeartbeat(tab.id);
  }

  const [resetResult] = await executeTabScript(
    {
      target: { tabId: tab.id },
      func: injectedStartFreshChat
    },
    { timeoutMs: TAB_SCRIPT_TIMEOUT_MS, label: "Gemini fresh-chat reset" }
  );
  if (resetResult && resetResult.result) {
    await delay(1000);
    await waitForTabComplete(tab.id);
    await waitForProviderReady(tab.id);
    await injectGeminiHeartbeat(tab.id);
    return await chromeTabsGet(tab.id);
  }

  await chromeTabsUpdate(tab.id, { url, active: true });
  await waitForTabComplete(tab.id);
  await waitForProviderReady(tab.id);
  await injectGeminiHeartbeat(tab.id);
  return await chromeTabsGet(tab.id);
}

async function recordProviderJobSuccess() {
  const session = await chromeStorageSessionGet({ [PROVIDER_JOB_COUNT_KEY]: 0 });
  const count = Math.max(0, Number(session[PROVIDER_JOB_COUNT_KEY] || 0)) + 1;
  if (count < PROVIDER_RECYCLE_AFTER_JOBS) {
    await chromeStorageSessionSet({ [PROVIDER_JOB_COUNT_KEY]: count });
    return { count, recycled: false };
  }

  const recycled = await recycleProviderTab();
  const nextCount = recycled ? 0 : PROVIDER_RECYCLE_AFTER_JOBS - 1;
  await chromeStorageSessionSet({ [PROVIDER_JOB_COUNT_KEY]: nextCount });
  return { count: nextCount, recycled };
}

async function recycleProviderTab() {
  const oldTab = await getManagedProviderTab(FINAL_PROVIDER_URL);
  let replacement = null;
  try {
    // Keep the current Gemini tab alive until the replacement is fully loaded
    // and its composer is usable. This prevents a failed navigation or a slow
    // VPS from leaving automation without a working provider tab.
    replacement = await chromeTabsCreate({ url: FINAL_PROVIDER_URL, active: true });
    if (!replacement || !replacement.id) {
      throw new Error("Chrome did not return a replacement Gemini tab.");
    }
    await waitForTabComplete(replacement.id);
    await waitForProviderReady(replacement.id);
    await setManagedProviderTab(replacement.id);
    await injectGeminiHeartbeat(replacement.id);

    if (oldTab && oldTab.id && oldTab.id !== replacement.id) {
      try {
        await chromeTabsRemove([oldTab.id]);
      } catch (_cleanupError) {
        // Only the bot-managed old tab is eligible for cleanup. User-owned
        // Gemini tabs in normal Chrome are never touched.
      }
    }
    return true;
  } catch (_error) {
    if (replacement && replacement.id) {
      try {
        await chromeTabsRemove([replacement.id]);
      } catch (_cleanupError) {
        // The failed replacement may already have been closed by Chrome.
      }
    }
    if (oldTab && oldTab.id) {
      await setManagedProviderTab(oldTab.id);
      try {
        await chromeTabsUpdate(oldTab.id, { active: true });
      } catch (_activationError) {
        // Preserve the old tab whenever it still exists; the next job retries.
      }
    } else {
      await setManagedProviderTab(0);
    }
    return false;
  }
}

async function recycleProviderAfterFailure() {
  const recycled = await recycleProviderTab();
  await chromeStorageSessionSet({
    [PROVIDER_JOB_COUNT_KEY]: recycled ? 0 : PROVIDER_RECYCLE_AFTER_JOBS - 1
  });
  return recycled;
}

function injectedStartFreshChat() {
  if (location.pathname === "/app" || location.pathname === "/app/") {
    return true;
  }
  const normalize = (value) => String(value || "")
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase();
  const candidates = Array.from(document.querySelectorAll("a[href='/app'], button, [role='button']"));
  const button = candidates.find((item) => {
    const href = item.getAttribute("href") || "";
    if (href === "/app") return true;
    const label = normalize(`${item.getAttribute("aria-label") || ""} ${item.title || ""} ${item.textContent || ""}`);
    return label.includes("new chat") || label.includes("doan chat moi") || label.includes("cuoc tro chuyen moi");
  });
  if (!button) return false;
  button.click();
  return true;
}

async function waitForProviderReady(tabId, timeoutMs = 30000) {
  const started = Date.now();
  let lastUrl = "";
  while (Date.now() - started < timeoutMs) {
    try {
      const [result] = await executeTabScript(
        {
          target: { tabId },
          func: injectedProviderReady
        },
        { timeoutMs: 5000, label: "Gemini readiness check" }
      );
      const state = result ? result.result : null;
      if (state && state.ready) {
        return state;
      }
      if (state && state.url) {
        lastUrl = state.url;
      }
    } catch (_error) {
      // Navigation can briefly invalidate the execution context. Retry until
      // the provider document and its composer are both available.
    }
    await delay(500);
  }
  throw new Error(
    `Gemini page loaded but its chat input was not ready within ${Math.round(timeoutMs / 1000)} seconds.` +
    (lastUrl ? ` Last URL: ${lastUrl}` : "")
  );
}

function injectedProviderReady() {
  const isVisible = (element) => {
    if (!element) return false;
    const style = window.getComputedStyle(element);
    const rect = element.getBoundingClientRect();
    return style.visibility !== "hidden" && style.display !== "none" && rect.width > 0 && rect.height > 0;
  };
  const normalize = (value) => String(value || "")
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase();
  const queryAllDeep = (selector) => {
    const found = [];
    const seen = new Set();
    const visit = (root) => {
      if (!root || seen.has(root)) return;
      seen.add(root);
      for (const element of root.querySelectorAll ? root.querySelectorAll(selector) : []) {
        if (!found.includes(element)) found.push(element);
      }
      for (const element of root.querySelectorAll ? root.querySelectorAll("*") : []) {
        if (element.shadowRoot) visit(element.shadowRoot);
      }
    };
    visit(document);
    return found;
  };
  const labelFor = (element) => normalize(
    `${element.getAttribute("aria-label") || ""} `
    + `${element.getAttribute("data-placeholder") || ""} `
    + `${element.getAttribute("placeholder") || ""} ${element.textContent || ""}`
  ).replace(/\s+/g, " ").trim();
  const selectors = [
    "#prompt-textarea",
    ".ProseMirror",
    "[contenteditable='true']",
    "div[role='textbox']",
    "p[data-placeholder]",
    "textarea"
  ];
  const score = (element) => {
    const rect = element.getBoundingClientRect();
    const label = labelFor(element);
    let value = 0;
    if (label.includes("search") || label.includes("tim kiem")
        || label.includes("検索") || label.includes("검색")) value -= 250;
    if (element.id === "prompt-textarea") value += 150;
    if (element.classList.contains("ProseMirror")) value += 120;
    if (element.getAttribute("role") === "textbox") value += 40;
    if (element.getAttribute("contenteditable") === "true") value += 25;
    if (label.includes("prompt") || label.includes("ask gemini")
        || label.includes("nhap cau lenh") || label.includes("hoi gemini")) value += 80;
    if (rect.width >= Math.min(280, window.innerWidth * 0.35)) value += 30;
    if (rect.top >= window.innerHeight * 0.35) value += 30;
    return value;
  };
  let candidates = Array.from(new Set(
    selectors.flatMap((selector) => Array.from(document.querySelectorAll(selector)))
  )).filter(isVisible);
  let input = candidates.sort((left, right) => score(right) - score(left))[0] || null;
  if (!input || score(input) < 55) {
    candidates = Array.from(new Set(
      selectors.flatMap((selector) => queryAllDeep(selector))
    )).filter(isVisible);
    input = candidates.sort((left, right) => score(right) - score(left))[0] || null;
  }
  return {
    ready: Boolean(input && score(input) >= 55),
    url: location.href,
    title: document.title,
    composer: input ? `${input.tagName.toLowerCase()}:${labelFor(input).slice(0, 100)}` : "none"
  };
}

async function waitForTabComplete(tabId) {
  const tab = await chromeTabsGet(tabId);
  if (tab.status === "complete") {
    return;
  }
  await new Promise((resolve, reject) => {
    const cleanup = () => {
      clearTimeout(timer);
      chrome.tabs.onUpdated.removeListener(updatedListener);
      chrome.tabs.onRemoved.removeListener(removedListener);
    };
    const updatedListener = (updatedTabId, info) => {
      if (updatedTabId === tabId && info.status === "complete") {
        cleanup();
        resolve();
      }
    };
    const removedListener = (removedTabId) => {
      if (removedTabId === tabId) {
        cleanup();
        reject(new Error("Provider tab closed while it was loading."));
      }
    };
    const timer = setTimeout(() => {
      cleanup();
      reject(new Error("Provider tab did not finish loading within 30 seconds."));
    }, 30000);
    chrome.tabs.onUpdated.addListener(updatedListener);
    chrome.tabs.onRemoved.addListener(removedListener);
  });
}

async function bridgeFetch(config, path, options = {}) {
  let response;
  const controller = new AbortController();
  const timeoutMs = Math.max(1000, Number(options.timeoutMs || BRIDGE_FETCH_TIMEOUT_MS));
  const timeout = setTimeout(() => controller.abort(), timeoutMs);
  try {
    response = await fetch(`${config.bridgeUrl}${path}`, {
      method: options.method || "GET",
      headers: {
        "Content-Type": "application/json",
        "X-Extension-Bridge-Token": config.token
      },
      body: options.body ? JSON.stringify(options.body) : undefined,
      signal: controller.signal
    });
  } catch (error) {
    if (error && error.name === "AbortError") {
      throw new Error(`Bridge request timed out after ${Math.round(timeoutMs / 1000)} seconds: ${path}`);
    }
    throw new Error(
      `Cannot reach bridge at ${config.bridgeUrl}. Start the Telegram bot with ` +
      `python -m src.main, keep CONTENT_PROVIDER=extension_bridge, then try again.`
    );
  } finally {
    clearTimeout(timeout);
  }
  const text = await response.text();
  const payload = text ? JSON.parse(text) : {};
  if (!response.ok) {
    throw new Error(payload.error || `Bridge HTTP ${response.status}`);
  }
  return payload;
}

async function injectedAttachImages(attachments) {
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const isVisible = (el) => {
    if (!el) return false;
    const style = window.getComputedStyle(el);
    const rect = el.getBoundingClientRect();
    return style.visibility !== "hidden" && style.display !== "none" && rect.width > 0 && rect.height > 0;
  };
  const asciiLower = (value) => String(value || "")
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase();
  const deepElements = (root, selector = "*") => {
    const found = [];
    const visited = new Set();
    const visit = (node) => {
      if (!node || visited.has(node)) return;
      visited.add(node);
      if (node.nodeType === Node.ELEMENT_NODE) {
        if (node.matches(selector)) found.push(node);
        if (node.shadowRoot) visit(node.shadowRoot);
      }
      for (const child of node.children || []) visit(child);
    };
    visit(root);
    return found;
  };
  const queryFastThenDeep = (selector, alwaysDeep = false) => {
    const direct = Array.from(document.querySelectorAll(selector));
    if (direct.length && !alwaysDeep) return direct;
    const deep = deepElements(document.documentElement, selector);
    return Array.from(new Set([...direct, ...deep]));
  };
  const fileInputs = () => queryFastThenDeep("input[type='file']").sort((left, right) => {
    const score = (input) => {
      const accept = String(input.accept || "").toLowerCase();
      return (accept.includes("image") ? 4 : 0) + (input.multiple ? 2 : 0) + (isVisible(input) ? 1 : 0);
    };
    return score(right) - score(left);
  });
  const labelFor = (item) => asciiLower(
    `${item.getAttribute("aria-label") || ""} ${item.getAttribute("data-tooltip") || ""} `
    + `${item.getAttribute("mattooltip") || ""} ${item.getAttribute("data-testid") || ""} `
    + `${item.getAttribute("data-test-id") || ""} ${item.title || ""} ${item.textContent || ""}`
  ).replace(/\s+/g, " ").trim();
  const clickableElements = () => queryFastThenDeep(
    "button, [role='button'], [role='menuitem'], [role='option'], [aria-label]",
    true
  ).filter(isVisible);
  const blobImageCount = () => queryFastThenDeep("img").filter((img) =>
    String(img.src || "").startsWith("blob:")
  ).length;
  const clickMatching = (matcher) => {
    const item = clickableElements().find((candidate) => matcher(labelFor(candidate)));
    if (!item) return "";
    item.click();
    return labelFor(item).slice(0, 120);
  };
  const clickAddMenu = () => clickMatching((label) => (
    label.includes("open upload file menu")
      || label.includes("upload file menu")
      || label.includes("open attachment menu")
      || label.includes("add files")
      || label.includes("add file")
      || label.includes("insert files")
      || label.includes("attach files")
      || label.includes("attach file")
      || label.includes("mo trinh don tai tep")
      || label.includes("mo trinh don dinh kem")
      || label.includes("dinh kem tep")
      || label.includes("dinh kem anh")
      || label.includes("them tep")
      || label.includes("them anh")
      || label.includes("tai tep")
      || label.includes("tai anh")
      || label.includes("ファイルを追加")
      || label.includes("ファイルを添付")
      || label.includes("파일 추가")
      || label.includes("파일 첨부")
      || label.includes("ファイルを追加")
      || label.includes("파일 추가")
  ));
  const clickUploadAction = () => clickMatching((label) => (
    !label.includes("menu") && (
      label.includes("upload files")
      || label.includes("upload file")
      || label.includes("upload from computer")
      || label.includes("upload from device")
      || label.includes("choose files")
      || label.includes("select files")
      || label.includes("tai tep len")
      || label.includes("tai len tu may tinh")
      || label.includes("tai len tu thiet bi")
      || label.includes("chon tep")
      || label.includes("chon anh")
      || label.includes("ファイルをアップロード")
      || label.includes("파일 업로드")
      || label.includes("ファイルをアップロード")
      || label.includes("파일 업로드")
    )
  ));
  const findComposer = () => {
    const candidates = queryFastThenDeep(
      "#prompt-textarea, .ProseMirror, [contenteditable='true'], "
      + "div[role='textbox'], p[data-placeholder], textarea",
      true
    ).filter(isVisible);
    const score = (item) => {
      const rect = item.getBoundingClientRect();
      const label = labelFor(item);
      const searchLike = label.includes("search") || label.includes("tim kiem")
        || label.includes("検索") || label.includes("검색");
      let value = searchLike ? -200 : 0;
      if (item.id === "prompt-textarea") value += 120;
      if (item.classList.contains("ProseMirror")) value += 100;
      if (item.getAttribute("role") === "textbox") value += 35;
      if (item.getAttribute("contenteditable") === "true") value += 25;
      if (label.includes("prompt") || label.includes("ask gemini")
          || label.includes("nhap cau lenh") || label.includes("hoi gemini")) value += 70;
      if (rect.width >= Math.min(280, window.innerWidth * 0.35)) value += 25;
      if (rect.top >= window.innerHeight * 0.35) value += 25;
      return value;
    };
    return candidates.sort((left, right) => score(right) - score(left))[0] || null;
  };
  const clickNearbyAttachmentMenu = () => {
    const composer = findComposer();
    if (!composer) return "";
    const composerRect = composer.getBoundingClientRect();
    const excluded = [
      "send", "gui", "microphone", "mic", "voice", "giong noi",
      "model", "spark", "main action", "thao tac chinh"
    ];
    const ranked = clickableElements().map((item) => {
      const rect = item.getBoundingClientRect();
      const label = labelFor(item);
      const verticalDistance = Math.abs(
        (rect.top + rect.height / 2) - (composerRect.top + composerRect.height / 2)
      );
      const horizontallyRelevant = rect.right >= composerRect.left - 220
        && rect.left <= composerRect.right + 80;
      if (verticalDistance > 160 || !horizontallyRelevant
          || excluded.some((term) => label.includes(term))) {
        return { item, label, score: -1 };
      }
      let score = 0;
      if (item.getAttribute("aria-haspopup") === "menu") score += 8;
      if (/upload|attach|add.file|them.tep|tai.tep|dinh.kem/.test(label)) score += 12;
      if (["add", "plus", "them", "dinh kem"].includes(label)) score += 10;
      if (/upload|attach|add/.test(String(
        item.getAttribute("data-testid") || item.getAttribute("data-test-id") || ""
      ).toLowerCase())) score += 12;
      if (item.querySelector("svg, mat-icon")) score += 1;
      if (rect.left <= composerRect.left + 180) score += 2;
      return { item, label, score };
    }).sort((left, right) => right.score - left.score);
    if (!ranked.length || ranked[0].score < 7) return "";
    ranked[0].item.click();
    return ranked[0].label.slice(0, 120) || "near-composer-menu";
  };
  const decodeAttachment = (attachment) => {
    const match = /^data:(image\/[a-z0-9.+-]+);base64,([a-z0-9+/=]+)$/i.exec(String(attachment.data_url || ""));
    if (!match) throw new Error(`Invalid image data for ${attachment.name || "attachment"}.`);
    const binary = atob(match[2]);
    const bytes = new Uint8Array(binary.length);
    for (let index = 0; index < binary.length; index += 1) bytes[index] = binary.charCodeAt(index);
    return new File([bytes], String(attachment.name || "frame.jpg"), {
      type: String(attachment.mime_type || match[1] || "image/jpeg")
    });
  };

  if (!Array.isArray(attachments) || !attachments.length || attachments.length > 5) {
    return { ok: false, error: "Expected 1-5 image attachments." };
  }
  let inputs = fileInputs();
  const triggerLog = [];
  if (!inputs.length) {
    const addLabel = clickAddMenu() || clickNearbyAttachmentMenu();
    if (addLabel) triggerLog.push(`add=${addLabel}`);
    for (let attempt = 0; attempt < 32 && !inputs.length; attempt += 1) {
      await sleep(250);
      inputs = fileInputs();
      if (inputs.length) break;
      if ([0, 3, 8, 15, 23].includes(attempt)) {
        const uploadLabel = clickUploadAction();
        if (uploadLabel) triggerLog.push(`upload=${uploadLabel}`);
      }
    }
  }
  const input = inputs[0];
  if (!input) {
    const composer = findComposer();
    const composerDebug = composer
      ? `${composer.tagName.toLowerCase()}:${labelFor(composer).slice(0, 100) || "unlabelled"}`
      : "none";
    const composerRect = composer ? composer.getBoundingClientRect() : null;
    const nearbyControls = clickableElements().filter((item) => {
      if (!composerRect) return false;
      const rect = item.getBoundingClientRect();
      return Math.abs(rect.top - composerRect.top) < 220;
    }).slice(0, 16);
    return {
      ok: false,
      error: "Gemini image file input was not found.",
      debug: `url=${location.href}; fileInputs=0; triggers=${triggerLog.join(" | ") || "none"}; `
        + `composer=${composerDebug}; nearbyControls=${nearbyControls.map(labelFor).filter(Boolean).join(" | ") || "none"}; `
        + `visibleControls=${clickableElements().slice(-16).map(labelFor).filter(Boolean).join(" | ") || "none"}`
    };
  }
  let files;
  try {
    files = attachments.map(decodeAttachment);
  } catch (error) {
    return { ok: false, error: error.message || String(error) };
  }
  const beforeBlobImages = blobImageCount();
  try {
    const transfer = new DataTransfer();
    files.forEach((file) => transfer.items.add(file));
    const filesSetter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "files")?.set;
    if (filesSetter) filesSetter.call(input, transfer.files);
    else input.files = transfer.files;
    input.dispatchEvent(new Event("input", { bubbles: true, composed: true }));
    input.dispatchEvent(new Event("change", { bubbles: true, composed: true }));
  } catch (error) {
    return { ok: false, error: `Could not assign Gemini image files: ${error.message || error}` };
  }

  const expectedNames = files.map((file) => file.name.toLowerCase());
  for (let attempt = 0; attempt < 80; attempt += 1) {
    const pageText = String(document.body.innerText || "").toLowerCase();
    const named = expectedNames.filter((name) => pageText.includes(name)).length;
    const blobImages = blobImageCount();
    if (named === expectedNames.length || blobImages >= beforeBlobImages + files.length) {
      return {
        ok: true,
        debug: `attached=${files.length}; named=${named}; blobImages=${blobImages}`
      };
    }
    await sleep(250);
  }
  return {
    ok: false,
    error: "Gemini did not confirm the uploaded representative frames.",
    debug: `expected=${expectedNames.join(",")}; fileInputs=${inputs.length}`
  };
}

async function injectedSubmitPrompt(prompt) {
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const compactText = (value) => String(value || "").replace(/\s+/g, " ").trim();
  const asciiLower = (value) => String(value || "")
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase();

  const isVisible = (el) => {
    if (!el) return false;
    const style = window.getComputedStyle(el);
    const rect = el.getBoundingClientRect();
    return style.visibility !== "hidden" && style.display !== "none" && rect.width > 0 && rect.height > 0;
  };

  const describe = (el) => {
    if (!el) return "none";
    const rect = el.getBoundingClientRect();
    return [
      `tag=${el.tagName.toLowerCase()}`,
      el.id ? `id=${el.id}` : "",
      el.getAttribute("data-testid") ? `testid=${el.getAttribute("data-testid")}` : "",
      el.getAttribute("role") ? `role=${el.getAttribute("role")}` : "",
      el.getAttribute("aria-label") ? `aria=${el.getAttribute("aria-label")}` : "",
      `rect=${Math.round(rect.width)}x${Math.round(rect.height)}`
    ].filter(Boolean).join(" | ");
  };

  const queryAllDeep = (selector) => {
    const found = [];
    const visited = new Set();
    const visit = (root) => {
      if (!root || visited.has(root)) return;
      visited.add(root);
      for (const item of root.querySelectorAll ? root.querySelectorAll(selector) : []) {
        if (!found.includes(item)) found.push(item);
      }
      for (const item of root.querySelectorAll ? root.querySelectorAll("*") : []) {
        if (item.shadowRoot) visit(item.shadowRoot);
      }
    };
    visit(document);
    return found;
  };

  const findInput = () => {
    const selectors = [
      "#prompt-textarea",
      ".ProseMirror",
      "[contenteditable='true']",
      "div[role='textbox']",
      "p[data-placeholder]",
      "textarea"
    ];
    const candidates = Array.from(new Set(
      selectors.flatMap((selector) => queryAllDeep(selector))
    )).filter(isVisible);
    const score = (item) => {
      const rect = item.getBoundingClientRect();
      const label = asciiLower(
        `${item.getAttribute("aria-label") || ""} `
        + `${item.getAttribute("data-placeholder") || ""} `
        + `${item.getAttribute("placeholder") || ""}`
      );
      let value = 0;
      if (label.includes("search") || label.includes("tim kiem")
          || label.includes("検索") || label.includes("검색")) value -= 250;
      if (item.id === "prompt-textarea") value += 150;
      if (item.classList.contains("ProseMirror")) value += 120;
      if (item.getAttribute("role") === "textbox") value += 40;
      if (item.getAttribute("contenteditable") === "true") value += 25;
      if (label.includes("prompt") || label.includes("ask gemini")
          || label.includes("nhap cau lenh") || label.includes("hoi gemini")) value += 80;
      if (rect.width >= Math.min(280, window.innerWidth * 0.35)) value += 30;
      if (rect.top >= window.innerHeight * 0.35) value += 30;
      return value;
    };
    const selected = candidates.sort((left, right) => score(right) - score(left))[0];
    return selected && score(selected) >= 55 ? selected : null;
  };

  const visibleAssistantText = () => {
    const selectors = ["[data-message-author-role='assistant']", "[dir='auto']", "pre"];
    const candidates = [];
    const seen = new Set();
    for (const selector of selectors) {
      for (const el of document.querySelectorAll(selector)) {
        if (!isVisible(el) || seen.has(el)) continue;
        seen.add(el);
        const text = compactText(el.innerText || el.textContent || "");
        if (text.length < 8 || text.length > 30000) continue;
        candidates.push(text);
      }
    }
    return candidates.length ? candidates[candidates.length - 1] : "";
  };

  const inputValue = (el) => compactText(el && (el.innerText || el.textContent || el.value || ""));

  const clearInput = (el) => {
    el.focus();
    el.click();
    if ("value" in el) {
      const prototype = el instanceof HTMLTextAreaElement
        ? HTMLTextAreaElement.prototype
        : HTMLInputElement.prototype;
      const setter = Object.getOwnPropertyDescriptor(prototype, "value")?.set;
      if (setter) setter.call(el, "");
      else el.value = "";
      el.dispatchEvent(new Event("input", { bubbles: true }));
      return;
    }
    const selection = window.getSelection();
    const range = document.createRange();
    range.selectNodeContents(el);
    selection.removeAllRanges();
    selection.addRange(range);
    document.execCommand("delete", false, null);
  };

  const insertOnce = (el, value, method) => {
    if ("value" in el) {
      const prototype = el instanceof HTMLTextAreaElement
        ? HTMLTextAreaElement.prototype
        : HTMLInputElement.prototype;
      const setter = Object.getOwnPropertyDescriptor(prototype, "value")?.set;
      if (setter) setter.call(el, value);
      else el.value = value;
      el.dispatchEvent(new InputEvent("input", {
        bubbles: true,
        inputType: "insertText",
        data: value
      }));
      el.dispatchEvent(new Event("change", { bubbles: true }));
      return "native-value";
    }

    if (method === "exec-command") {
      const inserted = document.execCommand("insertText", false, value);
      if (inserted) {
        return method;
      }
    }

    // ProseMirror can replace its contenteditable node after an input event.
    // Replacing the whole value in one DOM operation avoids losing chunks to a
    // stale node, while the single input event updates Gemini's editor state.
    el.replaceChildren(document.createTextNode(value));
    el.dispatchEvent(new InputEvent("input", {
      bubbles: true,
      inputType: "insertText",
      data: value
    }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
    return "dom-replace";
  };

  const setInput = async (initialInput, value) => {
    const expectedLength = compactText(value).length;
    let input = initialInput;
    let method = "exec-command";
    for (let attempt = 0; attempt < 2; attempt += 1) {
      input = findInput() || input;
      clearInput(input);
      await sleep(100);
      method = insertOnce(input, value, attempt === 0 ? "exec-command" : "dom-replace");
      await sleep(900);
      input = findInput() || input;
      const text = inputValue(input);
      if (text.length >= Math.max(20, Math.floor(expectedLength * 0.98))) {
        return { input, text, method, attempt: attempt + 1 };
      }
    }
    return { input, text: inputValue(input), method, attempt: 2 };
  };

  const clickButton = (button) => {
    for (const type of ["pointerdown", "mousedown", "pointerup", "mouseup", "click"]) {
      button.dispatchEvent(new MouseEvent(type, {
        bubbles: true,
        cancelable: true,
        view: window
      }));
    }
    button.click();
  };

  const findSendButton = () => {
    const buttons = Array.from(document.querySelectorAll("button")).filter((button) => {
      return isVisible(button) && !button.disabled && button.getAttribute("aria-disabled") !== "true";
    });
    const explicit = buttons.find((button) => {
      const label = asciiLower(`${button.getAttribute("aria-label") || ""} ${button.title || ""} ${button.textContent || ""}`);
      return label.includes("send") || label.includes("submit") || label.includes("gui");
    });
    if (explicit) return explicit;

    const input = document.querySelector("#prompt-textarea");
    const composer = input ? input.closest("form, main, [data-testid='composer']") : null;
    const scoped = Array.from((composer || document.body).querySelectorAll("button")).filter((button) => {
      if (!isVisible(button) || button.disabled || button.getAttribute("aria-disabled") === "true") return false;
      const label = asciiLower(`${button.getAttribute("aria-label") || ""} ${button.title || ""} ${button.textContent || ""}`);
      return !label.includes("them tep")
        && !label.includes("add")
        && !label.includes("doc chinh ta")
        && !label.includes("dictation")
        && !label.includes("che do thoai")
        && !label.includes("voice")
        && !label.includes("microphone");
    });
    return scoped[scoped.length - 1] || null;
  };

  const pressEnter = (el) => {
    for (const type of ["keydown", "keypress", "keyup"]) {
      el.dispatchEvent(new KeyboardEvent(type, {
        key: "Enter",
        code: "Enter",
        bubbles: true,
        cancelable: true
      }));
    }
  };

  const submitClosestForm = (el) => {
    const form = el.closest("form");
    if (!form) return false;
    if (typeof form.requestSubmit === "function") {
      form.requestSubmit();
      return true;
    }
    form.dispatchEvent(new SubmitEvent("submit", { bubbles: true, cancelable: true }));
    return true;
  };

  const before = visibleAssistantText();
  let input = findInput();
  if (!input) {
    return { ok: false, error: "Could not find Gemini input.", debug: `url=${location.href}` };
  }

  const expectedText = compactText(prompt);
  const insertion = await setInput(input, prompt);
  input = insertion.input;
  let inputText = insertion.text;
  if (expectedText.length > 0 && inputText.length < Math.max(20, Math.floor(expectedText.length * 0.98))) {
    return {
      ok: false,
      error: "Prompt was not fully inserted into the provider input.",
      debug: `input=${describe(input)}; method=${insertion.method}; attempts=${insertion.attempt}; inputChars=${inputText.length}; expectedChars=${expectedText.length}; inputSample=${inputText.slice(0, 120)}`
    };
  }
  let button = null;
  for (let attempt = 0; attempt < 12; attempt += 1) {
    button = findSendButton();
    if (button) break;
    await sleep(500);
  }
  inputText = compactText(input.innerText || input.textContent || input.value || "");
  let submitMethod = "none";
  if (button) {
    clickButton(button);
    submitMethod = `button: ${describe(button)}`;
  } else if (submitClosestForm(input)) {
    submitMethod = "form-submit";
  } else {
    pressEnter(input);
    submitMethod = "enter-fallback";
  }

  return {
    ok: true,
    before,
    debug: `input=${describe(input)}; insert=${insertion.method}; attempts=${insertion.attempt}; inputChars=${inputText.length}; send=${submitMethod}`
  };
}

function injectedReadProviderResponse(prompt, before, allowDeepScan = false) {
  const compactText = (value) => String(value || "").replace(/\s+/g, " ").trim();
  const stripProviderAttribution = (value) => compactText(value).replace(
    /^(?:gemini|chatgpt|grok)\s+(?:said|says|đã\s+nói|đã\s+nói|nói|noi)\s*[:\-–—]?\s*/iu,
    ""
  );
  const normalizeText = (value) => compactText(value).toLowerCase();
  const isGemini = location.hostname.includes("gemini.google.com");
  const normalizedPrompt = normalizeText(prompt);
  const promptStart = normalizedPrompt.slice(0, 120);
  const expectsJson = /return\s+(?:only\s+)?(?:valid\s+)?json/i.test(prompt)
    || /"targets"/i.test(prompt);

  const isVisible = (el) => {
    if (!el) return false;
    const style = window.getComputedStyle(el);
    const rect = el.getBoundingClientRect();
    return style.visibility !== "hidden" && style.display !== "none" && rect.width > 0 && rect.height > 0;
  };

  const providerFatalError = () => {
    const selectors = [
      "[role='alert']",
      "[aria-live='assertive']",
      "[data-testid*='error']",
      "[class*='error-message']",
      "[class*='ErrorMessage']"
    ];
    const markers = [
      "you've reached your limit",
      "you have reached your limit",
      "usage limit",
      "rate limit",
      "something went wrong",
      "couldn't generate",
      "could not generate",
      "try again later",
      "network error",
      "Ä‘Ã£ Ä‘áº¡t giá»›i háº¡n",
      "thá»­ láº¡i sau"
    ];
    for (const selector of selectors) {
      for (const el of document.querySelectorAll(selector)) {
        if (!isVisible(el)) continue;
        const text = compactText(el.innerText || el.textContent || "");
        const normalized = text.toLowerCase();
        if (text.length >= 4 && markers.some((marker) => normalized.includes(marker))) {
          return text.slice(0, 240);
        }
      }
    }
    return "";
  };

  const fatalError = providerFatalError();
  if (fatalError) {
    return { ok: false, fatalError, debug: `providerError=${fatalError}` };
  }

  // Gemini's answer body is sometimes rendered inside open shadow roots. Normal
  // querySelectorAll does not cross that boundary, even though the surrounding
  // response action buttons are visible in the document DOM.
  const deepElements = (root, selector = "*") => {
    if (!root) return [];
    const found = [];
    const visited = new Set();
    const visit = (node) => {
      if (!node || visited.has(node)) return;
      visited.add(node);
      if (node.nodeType === Node.ELEMENT_NODE) {
        if (node.matches(selector)) found.push(node);
        if (node.shadowRoot) visit(node.shadowRoot);
      }
      for (const child of node.children || []) visit(child);
    };
    visit(root);
    return found;
  };

  const looksLikeSubmittedPrompt = (text) => {
    const normalized = normalizeText(text);
    if (!normalized) return false;
    if (normalizedPrompt.includes(normalized) && normalized.length > 60) return true;
    if (promptStart && normalized.includes(promptStart)) return true;
    const textStart = normalized.slice(0, 120);
    return Boolean(textStart && normalizedPrompt.includes(textStart) && textStart.length > 60);
  };

  const looksLikePromptLeak = (text) => {
    const normalized = normalizeText(text);
    return [
      "you are a twitter/x reply engine",
      "you are an x reply qa",
      "original task:",
      "original reply task:",
      "original reply-target task:",
      "generated x reply:",
      "required json shape:",
      "tham khảo nội dung sau",
      "tham khao noi dung sau"
    ].some((marker) => normalized.includes(marker));
  };

  const looksLikeUserMessage = (el) => {
    let current = el;
    for (let depth = 0; current && depth < 7; depth += 1) {
      const role = `${current.getAttribute("data-message-author-role") || ""} ${current.getAttribute("data-testid") || ""} ${current.getAttribute("aria-label") || ""} ${current.className || ""}`.toLowerCase();
      if (role.includes("assistant") || role.includes("bot")) return false;
      if (role.includes("user") || role.includes("human") || role.includes("collapsible-user-message")) return true;
      current = current.parentElement;
    }
    return false;
  };

  const jsonObjectText = (text) => {
    const start = text.indexOf("{");
    const end = text.lastIndexOf("}");
    if (start === -1 || end === -1 || end <= start) return "";
    return text.slice(start, end + 1).trim();
  };

  const normalizeCandidateText = (text) => {
    let clean = stripProviderAttribution(text);
    clean = clean.replace(/^```(?:json)?\s*/i, "").replace(/\s*```$/i, "").trim();
    if (expectsJson) {
      const json = jsonObjectText(clean);
      if (!json) return "";
      clean = json;
    }
    return clean;
  };

  const selectors = ["[data-message-author-role='assistant']", "[dir='auto']", "pre"];
  const candidates = [];
  const seen = new Set();
  const pushCandidate = (el) => {
    if (!isVisible(el) || seen.has(el) || looksLikeUserMessage(el)) return;
    seen.add(el);
    const text = normalizeCandidateText(el.innerText || el.textContent || "");
    if (text.length < 8 || text.length > 30000) return;
    if (looksLikeSubmittedPrompt(text)) return;
    if (looksLikePromptLeak(text)) return;
    const normalized = normalizeText(text);
    if (candidates.some((candidate) => candidate.normalized === normalized)) return;
    candidates.push({ text, normalized });
  };

  if (isGemini) {
    // Current Gemini UI does not mark the answer itself, but it reliably renders
    // response action buttons after each completed answer. Walk backward from
    // those buttons to their sibling content instead of scanning page navigation.
    const responseActionSelectors = [
      "button[aria-label*='Good response']",
      "button[aria-label*='Câu trả lời tốt']",
      "button[data-testid='good-response-turn-action-button']"
    ];
    const actionButtons = responseActionSelectors.flatMap((selector) =>
      Array.from(document.querySelectorAll(selector))
    );
    for (const actionButton of actionButtons.reverse()) {
      let current = actionButton;
      for (let depth = 0; current && depth < 7; depth += 1) {
        const previous = current.previousElementSibling;
        if (previous) {
          pushCandidate(previous);
          if (candidates.length) break;
        }

        // Some Gemini builds put the answer and action bar under the same
        // ancestor instead of as direct siblings. Search its deepest readable
        // descendants, which avoids selecting page-level navigation text.
        const descendants = deepElements(current, "div, p, span").reverse();
        for (const descendant of descendants) {
          if (descendant.contains(actionButton)) continue;
          pushCandidate(descendant);
          if (candidates.length) break;
        }
        if (candidates.length) break;
        current = current.parentElement;
      }
      if (candidates.length) break;
    }
  }

  if (!candidates.length) {
    for (const selector of selectors) {
      const elements = allowDeepScan
        ? deepElements(document.body, selector)
        : Array.from(document.querySelectorAll(selector));
      for (const el of elements) {
        pushCandidate(el);
      }
    }
  }

  if (!candidates.length && isGemini && allowDeepScan) {
    // Gemini does not consistently expose assistant attributes. Try
    // likely response containers first, including plain-text replies such as /reply.
    const responseSelectors = [
      "message-content",
      "model-response",
      "[data-message-id]",
      "[data-testid*='response']",
      "[data-testid*='model']",
      "[class*='model-response']",
      "[class*='response-content']",
      "[class*='message-content']",
      "[role='article']"
    ];
    for (const selector of responseSelectors) {
      for (const el of deepElements(document.body, selector)) {
        pushCandidate(el);
      }
    }
  }

  if (!candidates.length && isGemini && allowDeepScan) {
    // Last-resort DOM scan. The previous JSON-only heuristic could never read a
    // plain Gemini reply. Keep this narrow enough to avoid navigation and page shells.
    for (const el of deepElements(document.body, "div, p, span")) {
      if (el.childElementCount > 6) continue;
      const text = compactText(el.innerText || el.textContent || "");
      if (text.length < 20 || text.length > 12000) continue;
      pushCandidate(el);
    }
  }

  const last = candidates.length ? candidates[candidates.length - 1].text : "";
  const text = last && last !== before ? last : "";
  return {
    ok: Boolean(text),
    text,
    debug: `candidates=${candidates.length}; responseActions=${isGemini ? document.querySelectorAll("button[aria-label*='Good response'], button[aria-label*='Câu trả lời tốt'], button[data-testid='good-response-turn-action-button']").length : 0}; sample=${last.slice(0, 220)}`
  };
}

function injectedSubmitAndRead(prompt, timeoutMs) {
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const splitInputChunks = (value, maxLength = 1200) => {
    const text = String(value || "");
    const chunks = [];
    for (let start = 0; start < text.length;) {
      let end = Math.min(text.length, start + maxLength);
      if (end < text.length) {
        const boundary = Math.max(text.lastIndexOf("\n", end), text.lastIndexOf(" ", end));
        if (boundary > start + Math.floor(maxLength / 2)) end = boundary + 1;
      }
      chunks.push(text.slice(start, end));
      start = end;
    }
    return chunks.length ? chunks : [""];
  };
  const normalizeText = (value) => String(value || "").toLowerCase().replace(/\s+/g, " ").trim();
  const asciiLower = (value) => String(value || "")
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase();
  const compactText = (value) => String(value || "").replace(/\s+/g, " ").trim();
  const normalizedPrompt = normalizeText(prompt);
  const promptStart = normalizedPrompt.slice(0, 120);
  let sawPromptLikeCandidate = false;
  let lastCandidateCount = 0;
  let lastCandidateSample = "";

  const isVisible = (el) => {
    if (!el) return false;
    const style = window.getComputedStyle(el);
    const rect = el.getBoundingClientRect();
    return style.visibility !== "hidden" && style.display !== "none" && rect.width > 0 && rect.height > 0;
  };

  const findInput = () => {
    const selectors = [
      "#prompt-textarea",
      "[data-testid='prompt-textarea']",
      "[data-testid='composer-input']",
      "[aria-label*='Message']",
      "[aria-label*='Ask']",
      ".ProseMirror",
      "textarea",
      "[contenteditable='true']",
      "div[role='textbox']",
      "p[data-placeholder]"
    ];
    for (const selector of selectors) {
      const items = Array.from(document.querySelectorAll(selector)).filter(isVisible);
      if (items.length) return items[items.length - 1];
    }
    return null;
  };

  const setInput = async (el, value) => {
    el.focus();
    if ("value" in el) {
      el.value = "";
      for (const chunk of splitInputChunks(value)) {
        el.value += chunk;
        el.dispatchEvent(new Event("input", { bubbles: true }));
        await sleep(25);
      }
      el.dispatchEvent(new Event("change", { bubbles: true }));
      return;
    }
    const selection = window.getSelection();
    const range = document.createRange();
    range.selectNodeContents(el);
    selection.removeAllRanges();
    selection.addRange(range);
    document.execCommand("delete", false, null);
    for (const chunk of splitInputChunks(value)) {
      if (!document.execCommand("insertText", false, chunk)) {
        el.textContent = `${el.textContent || ""}${chunk}`;
      }
      el.dispatchEvent(new InputEvent("beforeinput", { bubbles: true, cancelable: true, inputType: "insertText", data: chunk }));
      el.dispatchEvent(new InputEvent("input", { bubbles: true, inputType: "insertText", data: chunk }));
      await sleep(25);
    }
    el.dispatchEvent(new Event("change", { bubbles: true }));
  };

  const clickSend = () => {
    const directSelectors = [
      "[data-testid='send-button']",
      "[data-testid='composer-send-button']",
      "[data-testid='composer-submit-button']",
      "button[aria-label='Send prompt']",
      "button[aria-label='Send message']",
      "button[aria-label*='Send']",
      "button[aria-label*='Gửi']",
      "button[aria-label*='Gui']"
    ];
    for (const selector of directSelectors) {
      const button = Array.from(document.querySelectorAll(selector)).find((item) => {
        return isVisible(item) && !item.disabled && item.getAttribute("aria-disabled") !== "true";
      });
      if (button) {
        button.click();
        return true;
      }
    }
    const buttons = Array.from(document.querySelectorAll("button")).filter((button) => {
      return isVisible(button) && !button.disabled && button.getAttribute("aria-disabled") !== "true";
    });
    const send = buttons.find((button) => {
      const label = asciiLower(`${button.getAttribute("aria-label") || ""} ${button.title || ""} ${button.textContent || ""}`);
      return label.includes("send") || label.includes("submit") || label.includes("gui");
    });
    const fallback = send || composerSubmitButton();
    if (!fallback) return false;
    fallback.click();
    return true;
  };

  const composerSubmitButton = () => {
    const root = document.querySelector("form") || document.querySelector("[data-testid='composer']") || document.body;
    const buttons = Array.from(root.querySelectorAll("button")).filter((button) => {
      if (!isVisible(button) || button.disabled || button.getAttribute("aria-disabled") === "true") return false;
      const label = asciiLower(`${button.getAttribute("aria-label") || ""} ${button.title || ""} ${button.textContent || ""}`);
      return !label.includes("them tep")
        && !label.includes("add")
        && !label.includes("doc chinh ta")
        && !label.includes("dictation")
        && !label.includes("che do thoai")
        && !label.includes("voice");
    });
    return buttons[buttons.length - 1] || null;
  };

  const pressEnter = (el) => {
    for (const type of ["keydown", "keypress", "keyup"]) {
      el.dispatchEvent(new KeyboardEvent(type, {
        key: "Enter",
        code: "Enter",
        bubbles: true,
        cancelable: true
      }));
    }
  };

  const looksLikePromptLeak = (text) => {
    const normalized = normalizeText(text);
    if (!normalized) return false;
    return [
      "you are a twitter/x reply engine",
      "you are an x reply qa",
      "your input is one generated x reply",
      "your job is not to write a new reply",
      "never explain your reasoning",
      "return only the final rewritten reply",
      "step 1 (silent)",
      "final silent qa"
    ].some((marker) => normalized.includes(marker));
  };

  const looksLikeSubmittedPrompt = (text) => {
    const normalized = normalizeText(text);
    if (!normalized) return false;
    if (normalizedPrompt.includes(normalized) && normalized.length > 60) return true;
    if (promptStart && normalized.includes(promptStart)) return true;
    const textStart = normalized.slice(0, 120);
    if (textStart && normalizedPrompt.includes(textStart) && textStart.length > 60) return true;
    return false;
  };

  const looksLikeUserMessage = (el) => {
    let current = el;
    for (let depth = 0; current && depth < 6; depth += 1) {
      const role = `${current.getAttribute("data-message-author-role") || ""} ${current.getAttribute("data-testid") || ""} ${current.getAttribute("aria-label") || ""} ${current.className || ""}`.toLowerCase();
      if (role.includes("assistant") || role.includes("bot")) return false;
      if (role.includes("user") || role.includes("human")) return true;
      current = current.parentElement;
    }
    return false;
  };

  const responseText = () => {
    const selectors = [
      "[data-message-author-role='assistant']",
      "[data-testid='markdown']",
      "[data-testid*='markdown']",
      "[data-testid*='answer']",
      "[data-testid*='response']",
      "[data-testid*='message-bubble']",
      "article",
      "main article",
      "[data-testid*='message']",
      "[class*='response']",
      "[class*='message']",
      "[class*='prose']",
      "[class*='markdown']",
      "[class*='Message']",
      "pre",
      "code",
      "[dir='auto']"
    ];
    const seen = new Set();
    const candidates = [];
    const pushCandidate = (el) => {
      if (!isVisible(el) || seen.has(el)) return;
      seen.add(el);
      if (looksLikeUserMessage(el)) return;
      const text = (el.innerText || el.textContent || "").trim();
      const compact = compactText(text);
      if (compact.length < 8) return;
      if (compact.length > 30000) return;
      if (looksLikeSubmittedPrompt(compact) || looksLikePromptLeak(compact)) {
        sawPromptLikeCandidate = true;
        return;
      }
      if (candidates.some((candidate) => candidate.normalized === normalizeText(compact))) {
        return;
      }
      candidates.push({ text: compact, normalized: normalizeText(compact) });
    };
    for (const selector of selectors) {
      for (const el of document.querySelectorAll(selector)) {
        pushCandidate(el);
      }
    }
    if (!candidates.length) {
      for (const el of document.body ? document.body.querySelectorAll("*") : []) {
        const text = compactText(el.innerText || el.textContent || "");
        if (!/[{}]/.test(text) && !/"(?:targets|reply|url)"\s*:/.test(text)) {
          continue;
        }
        pushCandidate(el);
      }
    }
    lastCandidateCount = candidates.length;
    lastCandidateSample = candidates.length ? candidates[candidates.length - 1].text.slice(0, 240) : "";
    if (!candidates.length) return "";
    return candidates[candidates.length - 1].text.trim();
  };

  return (async () => {
    const started = Date.now();
    let input = null;
    while (Date.now() - started < 30000) {
      input = findInput();
      if (input) break;
      await sleep(500);
    }
    if (!input) {
      return { ok: false, error: "Could not find the chat input. Are you logged in?" };
    }

    const before = responseText();
    await setInput(input, prompt);
    await sleep(1000);
    let submitted = false;
    for (let attempt = 0; attempt < 10; attempt += 1) {
      if (clickSend()) {
        submitted = true;
        break;
      }
      await sleep(500);
    }
    if (!submitted) {
      pressEnter(input);
      await sleep(1000);
    }

    let best = "";
    let last = "";
    let stableSince = Date.now();
    while (Date.now() - started < timeoutMs) {
      const current = responseText();
      if (current && current !== before) {
        best = current;
      }
      if (current !== last) {
        last = current;
        stableSince = Date.now();
      }
      if (best.length > 20 && Date.now() - stableSince > 3500) {
        return { ok: true, text: best };
      }
      await sleep(1000);
    }
    if (best) return { ok: true, text: best };
    if (sawPromptLikeCandidate) {
      return {
        ok: false,
        error: "Only prompt/instruction text was visible. The extension likely read the user prompt instead of the assistant response.",
        debug: `candidates=${lastCandidateCount}; sample=${lastCandidateSample}`
      };
    }
    return {
      ok: false,
      error: "Timed out waiting for a readable response.",
      debug: `candidates=${lastCandidateCount}; sample=${lastCandidateSample}`
    };
  })();
}

function injectedDiagnoseDom() {
  const compactText = (value) => String(value || "").replace(/\s+/g, " ").trim();
  const short = (value, limit = 140) => {
    const text = compactText(value);
    return text.length > limit ? `${text.slice(0, limit)}...` : text;
  };
  const isVisible = (el) => {
    if (!el) return false;
    const style = window.getComputedStyle(el);
    const rect = el.getBoundingClientRect();
    return style.visibility !== "hidden" && style.display !== "none" && rect.width > 0 && rect.height > 0;
  };
  const describe = (el) => {
    if (!el) return "";
    const rect = el.getBoundingClientRect();
    const attrs = [
      `tag=${el.tagName.toLowerCase()}`,
      el.id ? `id=${el.id}` : "",
      el.getAttribute("data-testid") ? `testid=${el.getAttribute("data-testid")}` : "",
      el.getAttribute("role") ? `role=${el.getAttribute("role")}` : "",
      el.getAttribute("aria-label") ? `aria=${el.getAttribute("aria-label")}` : "",
      el.getAttribute("placeholder") ? `placeholder=${el.getAttribute("placeholder")}` : "",
      el.getAttribute("contenteditable") ? `contenteditable=${el.getAttribute("contenteditable")}` : "",
      `rect=${Math.round(rect.width)}x${Math.round(rect.height)}`,
      `text="${short(el.innerText || el.textContent || "", 100)}"`
    ].filter(Boolean);
    return attrs.join(" | ");
  };
  const count = (selector) => {
    const items = Array.from(document.querySelectorAll(selector)).filter(isVisible);
    return { selector, count: items.length, sample: items[items.length - 1] ? describe(items[items.length - 1]) : "" };
  };

  const selectorReports = [
    "#prompt-textarea",
    "[data-testid='prompt-textarea']",
    "[data-testid='composer-input']",
    ".ProseMirror",
    "textarea",
    "[contenteditable='true']",
    "div[role='textbox']",
    "p[data-placeholder]",
    "[data-testid='send-button']",
    "[data-testid='composer-send-button']",
    "[data-testid='composer-submit-button']",
    "button[aria-label*='Send']",
    "button[aria-label*='Gửi']",
    "[data-message-author-role='assistant']",
    "[data-testid='markdown']",
    "[data-testid*='markdown']",
    "[data-testid*='message']",
    "article",
    "main article",
    "pre",
    "code",
    "[dir='auto']"
  ].map(count);

  const interesting = Array.from(
    document.querySelectorAll("[data-testid], [aria-label], [role], textarea, [contenteditable='true'], pre, code")
  )
    .filter(isVisible)
    .slice(-30)
    .map(describe);

  const visibleText = short(document.body ? document.body.innerText : "", 500);
  const responseActionContext = Array.from(
    document.querySelectorAll("button[aria-label*='Good response'], button[aria-label*='Câu trả lời tốt'], button[data-testid='good-response-turn-action-button']")
  )
    .filter(isVisible)
    .slice(-3)
    .map((button) => {
      const group = button.parentElement;
      const sibling = group ? group.previousElementSibling : null;
      return `action=${describe(button)} | group=${describe(group)} | previous=${describe(sibling)}`;
    });
  const reportLines = [
    `DOM diagnose @ ${new Date().toLocaleTimeString()}`,
    `URL: ${location.href}`,
    `Title: ${document.title}`,
    "",
    "Selector counts:",
    ...selectorReports.map((item) => {
      const sample = item.sample ? ` | ${item.sample}` : "";
      return `${item.selector}: ${item.count}${sample}`;
    }),
    "",
    "Last interesting visible elements:",
    ...interesting,
    "",
    "Response action context:",
    ...(responseActionContext.length ? responseActionContext : ["none"]),
    "",
    `Body sample: ${visibleText}`
  ];
  return reportLines.join("\n");
}

function boundedProviderTimeout(value) {
  const parsed = Number(value || DEFAULTS.timeoutSeconds);
  const finite = Number.isFinite(parsed) ? parsed : DEFAULTS.timeoutSeconds;
  return Math.min(
    MAX_PROVIDER_TIMEOUT_SECONDS,
    Math.max(MIN_PROVIDER_TIMEOUT_SECONDS, finite)
  );
}

async function loadConfig() {
  const [saved, session] = await Promise.all([
    chromeStorageGet(DEFAULTS),
    chromeStorageSessionGet({ lastStatus: DEFAULTS.lastStatus })
  ]);
  return {
    bridgeUrl: String(saved.bridgeUrl || DEFAULTS.bridgeUrl).replace(/\/$/, ""),
    token: String(saved.token || DEFAULTS.token),
    timeoutSeconds: boundedProviderTimeout(saved.timeoutSeconds),
    autoRun: Boolean(saved.autoRun),
    pollSeconds: Math.max(
      saved.lowResourceMode === false ? 30 : 60,
      Number(saved.pollSeconds || DEFAULTS.pollSeconds)
    ),
    lowResourceMode: saved.lowResourceMode !== false,
    automationEnabled: Boolean(saved.automationEnabled),
    activeStart: String(saved.activeStart || DEFAULTS.activeStart),
    activeEnd: String(saved.activeEnd || DEFAULTS.activeEnd),
    creatorTimezone: String(saved.creatorTimezone || DEFAULTS.creatorTimezone),
    replyTargetsMinutes: Math.max(5, Number(saved.replyTargetsMinutes || DEFAULTS.replyTargetsMinutes)),
    replyTargetsMaxAgeMinutes: Math.min(1440, Math.max(
      30,
      Number(saved.replyTargetsMaxAgeMinutes || DEFAULTS.replyTargetsMaxAgeMinutes)
    )),
    replyTargetsLanguages: String(
      saved.replyTargetsLanguages || DEFAULTS.replyTargetsLanguages
    ),
    replyTargetsQuery: String(saved.replyTargetsQuery || ""),
    replyVideoMinutes: Math.max(3, Number(saved.replyVideoMinutes || DEFAULTS.replyVideoMinutes)),
    replyVideoQuery: String(saved.replyVideoQuery || ""),
    replyVideoWindows: String(saved.replyVideoWindows || DEFAULTS.replyVideoWindows),
    followTargetsMinutes: Math.max(5, Number(
      saved.followTargetsMinutes || DEFAULTS.followTargetsMinutes
    )),
    nextReplyTargetsAt: Number(saved.nextReplyTargetsAt || 0),
    lastReplyTargetsTriggeredAt: Number(saved.lastReplyTargetsTriggeredAt || 0),
    replyTargetsConfigUpdatedAt: Number(saved.replyTargetsConfigUpdatedAt || 0),
    nextReplyVideoAt: Number(saved.nextReplyVideoAt || 0),
    lastReplyVideoTriggeredAt: Number(saved.lastReplyVideoTriggeredAt || 0),
    replyVideoConfigUpdatedAt: Number(saved.replyVideoConfigUpdatedAt || 0),
    nextFollowTargetsAt: Number(saved.nextFollowTargetsAt || 0),
    lastFollowTargetsTriggeredAt: Number(saved.lastFollowTargetsTriggeredAt || 0),
    followTargetsConfigUpdatedAt: Number(saved.followTargetsConfigUpdatedAt || 0),
    lastStatus: String(session.lastStatus || saved.lastStatus || DEFAULTS.lastStatus)
  };
}

async function saveConfig(config) {
  await chromeStorageSet({
    bridgeUrl: String(config.bridgeUrl || DEFAULTS.bridgeUrl).replace(/\/$/, ""),
    token: String(config.token || DEFAULTS.token),
    timeoutSeconds: boundedProviderTimeout(config.timeoutSeconds),
    autoRun: Boolean(config.autoRun),
    pollSeconds: Math.max(
      config.lowResourceMode === false ? 30 : 60,
      Number(config.pollSeconds || DEFAULTS.pollSeconds)
    ),
    lowResourceMode: config.lowResourceMode !== false,
    automationEnabled: Boolean(config.automationEnabled),
    activeStart: String(config.activeStart || DEFAULTS.activeStart),
    activeEnd: String(config.activeEnd || DEFAULTS.activeEnd),
    creatorTimezone: String(config.creatorTimezone || DEFAULTS.creatorTimezone),
    replyTargetsMinutes: Math.max(5, Number(config.replyTargetsMinutes || DEFAULTS.replyTargetsMinutes)),
    replyTargetsMaxAgeMinutes: Math.min(1440, Math.max(
      30,
      Number(config.replyTargetsMaxAgeMinutes || DEFAULTS.replyTargetsMaxAgeMinutes)
    )),
    replyTargetsLanguages: String(
      config.replyTargetsLanguages || DEFAULTS.replyTargetsLanguages
    ),
    replyTargetsQuery: String(config.replyTargetsQuery || ""),
    replyVideoMinutes: Math.max(3, Number(config.replyVideoMinutes || DEFAULTS.replyVideoMinutes)),
    replyVideoQuery: String(config.replyVideoQuery || ""),
    replyVideoWindows: String(config.replyVideoWindows || DEFAULTS.replyVideoWindows),
    followTargetsMinutes: Math.max(5, Number(
      config.followTargetsMinutes || DEFAULTS.followTargetsMinutes
    )),
    nextReplyTargetsAt: Number(config.nextReplyTargetsAt || 0),
    lastReplyTargetsTriggeredAt: Number(config.lastReplyTargetsTriggeredAt || 0),
    replyTargetsConfigUpdatedAt: Number(config.replyTargetsConfigUpdatedAt || 0),
    nextReplyVideoAt: Number(config.nextReplyVideoAt || 0),
    lastReplyVideoTriggeredAt: Number(config.lastReplyVideoTriggeredAt || 0),
    replyVideoConfigUpdatedAt: Number(config.replyVideoConfigUpdatedAt || 0),
    nextFollowTargetsAt: Number(config.nextFollowTargetsAt || 0),
    lastFollowTargetsTriggeredAt: Number(config.lastFollowTargetsTriggeredAt || 0),
    followTargetsConfigUpdatedAt: Number(config.followTargetsConfigUpdatedAt || 0)
  });
}

async function setStatus(text) {
  const value = String(text || "");
  if (value === lastStatusCache) return;
  lastStatusCache = value;
  await chromeStorageSessionSet({ lastStatus: value });
}

function chromeStorageGet(defaults) {
  return new Promise((resolve) => chrome.storage.local.get(defaults, resolve));
}

function chromeStorageSet(values) {
  return new Promise((resolve) => chrome.storage.local.set(values, resolve));
}

function chromeStorageSessionGet(defaults) {
  return new Promise((resolve) => chrome.storage.session.get(defaults, resolve));
}

function chromeStorageSessionSet(values) {
  return new Promise((resolve) => chrome.storage.session.set(values, resolve));
}

function chromeTabsQuery(query) {
  return new Promise((resolve) => chrome.tabs.query(query, resolve));
}

function chromeTabsCreate(createProperties) {
  return new Promise((resolve) => chrome.tabs.create(createProperties, resolve));
}

function chromeTabsUpdate(tabId, updateProperties) {
  return new Promise((resolve) => chrome.tabs.update(tabId, updateProperties, resolve));
}

function chromeTabsGet(tabId) {
  return new Promise((resolve) => chrome.tabs.get(tabId, resolve));
}

function chromeTabsRemove(tabIds) {
  return new Promise((resolve) => chrome.tabs.remove(tabIds, resolve));
}

function chromeAlarmsClear(name) {
  return new Promise((resolve) => chrome.alarms.clear(name, resolve));
}

function chromeAlarmsGet(name) {
  return new Promise((resolve) => chrome.alarms.get(name, resolve));
}
