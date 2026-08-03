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
  nextReplyTargetsAt: 0,
  lastReplyTargetsTriggeredAt: 0,
  replyTargetsConfigUpdatedAt: 0,
  nextReplyVideoAt: 0,
  lastReplyVideoTriggeredAt: 0,
  replyVideoConfigUpdatedAt: 0,
  lastStatus: "Ready."
};

const els = {
  version: document.getElementById("version"),
  bridgeUrl: document.getElementById("bridgeUrl"),
  token: document.getElementById("token"),
  timeoutSeconds: document.getElementById("timeoutSeconds"),
  pollSeconds: document.getElementById("pollSeconds"),
  lowResourceMode: document.getElementById("lowResourceMode"),
  autoRun: document.getElementById("autoRun"),
  automationEnabled: document.getElementById("automationEnabled"),
  activeStart: document.getElementById("activeStart"),
  activeEnd: document.getElementById("activeEnd"),
  creatorTimezone: document.getElementById("creatorTimezone"),
  replyTargetsMinutes: document.getElementById("replyTargetsMinutes"),
  replyTargetsMaxAgeMinutes: document.getElementById("replyTargetsMaxAgeMinutes"),
  replyTargetsLanguages: document.getElementById("replyTargetsLanguages"),
  replyTargetsQuery: document.getElementById("replyTargetsQuery"),
  replyScheduleStatus: document.getElementById("replyScheduleStatus"),
  replyVideoMinutes: document.getElementById("replyVideoMinutes"),
  replyVideoQuery: document.getElementById("replyVideoQuery"),
  replyVideoWindows: document.getElementById("replyVideoWindows"),
  replyVideoScheduleStatus: document.getElementById("replyVideoScheduleStatus"),
  save: document.getElementById("save"),
  run: document.getElementById("run"),
  diagnoseGemini: document.getElementById("diagnoseGemini"),
  status: document.getElementById("status")
};

let currentConfig = { ...DEFAULTS };

document.addEventListener("DOMContentLoaded", async () => {
  els.version.textContent = `v${chrome.runtime.getManifest().version}`;
  await refreshState();
});

els.save.addEventListener("click", async () => {
  await saveFromForm();
  setStatus("Saved.");
});

els.autoRun.addEventListener("click", async () => {
  currentConfig = readConfigFromForm();
  currentConfig.autoRun = !currentConfig.autoRun;
  await saveConfig(currentConfig);
  renderConfig(currentConfig);
  setStatus(currentConfig.autoRun ? "Auto Run is ON." : "Auto Run is OFF.");
});

els.lowResourceMode.addEventListener("click", async () => {
  currentConfig = readConfigFromForm();
  currentConfig.lowResourceMode = !currentConfig.lowResourceMode;
  if (currentConfig.lowResourceMode && currentConfig.pollSeconds < 60) {
    currentConfig.pollSeconds = 60;
  }
  await saveConfig(currentConfig);
  renderConfig(currentConfig);
  setStatus(currentConfig.lowResourceMode ? "Low-resource mode is ON." : "Low-resource mode is OFF.");
});

els.automationEnabled.addEventListener("click", async () => {
  currentConfig = readConfigFromForm();
  currentConfig.automationEnabled = !currentConfig.automationEnabled;
  await saveConfig(currentConfig);
  renderConfig(currentConfig);
  setStatus(currentConfig.automationEnabled ? "Scheduled approvals are ON." : "Scheduled approvals are OFF.");
});

els.run.addEventListener("click", async () => {
  els.run.disabled = true;
  try {
    await saveFromForm();
    setStatus("Running next job...");
    const response = await sendMessage({ action: "run-now" });
    if (!response.ok) {
      throw new Error(response.error || "Run failed.");
    }
    await refreshState();
  } catch (error) {
    setStatus(`Error: ${error.message || error}`);
  } finally {
    els.run.disabled = false;
  }
});

els.diagnoseGemini.addEventListener("click", async () => {
  els.diagnoseGemini.disabled = true;
  try {
    setStatus("Diagnosing Gemini DOM...");
    const response = await sendMessage({ action: "diagnose-gemini-dom" });
    if (!response.ok) {
      throw new Error(response.error || "Diagnose failed.");
    }
    setStatus(response.report || "No diagnostic report returned.");
  } catch (error) {
    setStatus(`Error: ${error.message || error}`);
  } finally {
    els.diagnoseGemini.disabled = false;
  }
});

chrome.storage.onChanged.addListener((changes, areaName) => {
  if (areaName !== "local" && areaName !== "session") return;
  if (changes.lastStatus) {
    setStatus(changes.lastStatus.newValue || "");
  }
  if (areaName === "local" && (
    changes.nextReplyTargetsAt || changes.lastReplyTargetsTriggeredAt ||
    changes.nextReplyVideoAt || changes.lastReplyVideoTriggeredAt
  )) {
    if (changes.nextReplyTargetsAt) {
      currentConfig.nextReplyTargetsAt = Number(changes.nextReplyTargetsAt.newValue || 0);
    }
    if (changes.lastReplyTargetsTriggeredAt) {
      currentConfig.lastReplyTargetsTriggeredAt = Number(changes.lastReplyTargetsTriggeredAt.newValue || 0);
    }
    if (changes.nextReplyVideoAt) {
      currentConfig.nextReplyVideoAt = Number(changes.nextReplyVideoAt.newValue || 0);
    }
    if (changes.lastReplyVideoTriggeredAt) {
      currentConfig.lastReplyVideoTriggeredAt = Number(changes.lastReplyVideoTriggeredAt.newValue || 0);
    }
    renderReplyScheduleStatus(currentConfig);
  }
});

async function refreshState() {
  const response = await sendMessage({ action: "get-state" });
  if (!response.ok) {
    setStatus(`Error: ${response.error || "Could not load state."}`);
    return;
  }
  currentConfig = { ...DEFAULTS, ...response.config };
  renderConfig(currentConfig);
  setStatus(currentConfig.lastStatus || "Ready.");
}

async function saveFromForm() {
  currentConfig = readConfigFromForm();
  await saveConfig(currentConfig);
}

async function saveConfig(config) {
  const response = await sendMessage({
    action: "save-config",
    config
  });
  if (!response.ok) {
    throw new Error(response.error || "Could not save config.");
  }
}

function renderConfig(config) {
  els.bridgeUrl.value = config.bridgeUrl;
  els.token.value = config.token;
  els.timeoutSeconds.value = String(config.timeoutSeconds);
  els.pollSeconds.value = String(config.pollSeconds);
  els.lowResourceMode.textContent = config.lowResourceMode ? "ON" : "OFF";
  els.lowResourceMode.classList.toggle("is-on", config.lowResourceMode);
  els.lowResourceMode.setAttribute("aria-pressed", config.lowResourceMode ? "true" : "false");
  els.activeStart.value = config.activeStart;
  els.activeEnd.value = config.activeEnd;
  els.creatorTimezone.value = config.creatorTimezone;
  els.replyTargetsMinutes.value = String(config.replyTargetsMinutes);
  els.replyTargetsMaxAgeMinutes.value = String(config.replyTargetsMaxAgeMinutes);
  els.replyTargetsLanguages.value = config.replyTargetsLanguages;
  els.replyTargetsQuery.value = config.replyTargetsQuery;
  els.replyVideoMinutes.value = String(config.replyVideoMinutes);
  els.replyVideoQuery.value = config.replyVideoQuery;
  els.replyVideoWindows.value = config.replyVideoWindows;
  els.autoRun.textContent = config.autoRun ? "ON" : "OFF";
  els.autoRun.classList.toggle("is-on", config.autoRun);
  els.autoRun.setAttribute("aria-pressed", config.autoRun ? "true" : "false");
  els.automationEnabled.textContent = config.automationEnabled ? "ON" : "OFF";
  els.automationEnabled.classList.toggle("is-on", config.automationEnabled);
  els.automationEnabled.setAttribute("aria-pressed", config.automationEnabled ? "true" : "false");
  renderReplyScheduleStatus(config);
}

function renderReplyScheduleStatus(config) {
  const format = (timestamp) => timestamp
    ? new Date(timestamp).toLocaleString()
    : "not yet";
  els.replyScheduleStatus.textContent = config.automationEnabled
    ? `Last trigger: ${format(config.lastReplyTargetsTriggeredAt)} · Next run: ${format(config.nextReplyTargetsAt)}`
    : "Automation is OFF; /replytargets will not run on a schedule.";
  els.replyVideoScheduleStatus.textContent = config.automationEnabled
    ? `Last trigger: ${format(config.lastReplyVideoTriggeredAt)} Â· Next run: ${format(config.nextReplyVideoAt)}`
    : "Automation is OFF; /replyvideo will not run on a schedule.";
}

function readConfigFromForm() {
  return {
    bridgeUrl: els.bridgeUrl.value.trim().replace(/\/$/, "") || DEFAULTS.bridgeUrl,
    token: els.token.value.trim() || DEFAULTS.token,
    timeoutSeconds: Math.max(30, Number(els.timeoutSeconds.value || DEFAULTS.timeoutSeconds)),
    pollSeconds: Math.max(
      currentConfig.lowResourceMode ? 60 : 30,
      Number(els.pollSeconds.value || DEFAULTS.pollSeconds)
    ),
    lowResourceMode: Boolean(currentConfig.lowResourceMode),
    autoRun: Boolean(currentConfig.autoRun),
    automationEnabled: Boolean(currentConfig.automationEnabled),
    activeStart: els.activeStart.value || DEFAULTS.activeStart,
    activeEnd: els.activeEnd.value || DEFAULTS.activeEnd,
    creatorTimezone: els.creatorTimezone.value.trim() || DEFAULTS.creatorTimezone,
    replyTargetsMinutes: Math.max(5, Number(els.replyTargetsMinutes.value || DEFAULTS.replyTargetsMinutes)),
    replyTargetsMaxAgeMinutes: Math.min(1440, Math.max(
      30,
      Number(
        els.replyTargetsMaxAgeMinutes.value || DEFAULTS.replyTargetsMaxAgeMinutes
      )
    )),
    replyTargetsLanguages: els.replyTargetsLanguages.value.trim() || DEFAULTS.replyTargetsLanguages,
    replyTargetsQuery: els.replyTargetsQuery.value.trim(),
    replyVideoMinutes: Math.max(3, Number(els.replyVideoMinutes.value || DEFAULTS.replyVideoMinutes)),
    replyVideoQuery: els.replyVideoQuery.value.trim(),
    replyVideoWindows: els.replyVideoWindows.value.trim() || DEFAULTS.replyVideoWindows,
    nextReplyTargetsAt: Number(currentConfig.nextReplyTargetsAt || 0),
    lastReplyTargetsTriggeredAt: Number(currentConfig.lastReplyTargetsTriggeredAt || 0),
    replyTargetsConfigUpdatedAt: Number(currentConfig.replyTargetsConfigUpdatedAt || 0),
    nextReplyVideoAt: Number(currentConfig.nextReplyVideoAt || 0),
    lastReplyVideoTriggeredAt: Number(currentConfig.lastReplyVideoTriggeredAt || 0),
    replyVideoConfigUpdatedAt: Number(currentConfig.replyVideoConfigUpdatedAt || 0)
  };
}

function setStatus(text) {
  els.status.textContent = text;
}

function sendMessage(message) {
  return new Promise((resolve) => {
    chrome.runtime.sendMessage(message, (response) => {
      if (chrome.runtime.lastError) {
        resolve({ ok: false, error: chrome.runtime.lastError.message });
        return;
      }
      resolve(response || { ok: false, error: "No response from extension worker." });
    });
  });
}
