const DEFAULTS = {
  bridgeUrl: "http://127.0.0.1:8765",
  token: "local-bridge-change-me",
  timeoutSeconds: 300,
  autoRun: false,
  pollSeconds: 30,
  lastStatus: "Ready."
};

const els = {
  bridgeUrl: document.getElementById("bridgeUrl"),
  token: document.getElementById("token"),
  timeoutSeconds: document.getElementById("timeoutSeconds"),
  pollSeconds: document.getElementById("pollSeconds"),
  autoRun: document.getElementById("autoRun"),
  save: document.getElementById("save"),
  run: document.getElementById("run"),
  diagnoseGemini: document.getElementById("diagnoseGemini"),
  status: document.getElementById("status")
};

let currentConfig = { ...DEFAULTS };

document.addEventListener("DOMContentLoaded", async () => {
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
  if (areaName !== "local") return;
  if (changes.lastStatus) {
    setStatus(changes.lastStatus.newValue || "");
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
  els.autoRun.textContent = config.autoRun ? "ON" : "OFF";
  els.autoRun.classList.toggle("is-on", config.autoRun);
  els.autoRun.setAttribute("aria-pressed", config.autoRun ? "true" : "false");
}

function readConfigFromForm() {
  return {
    bridgeUrl: els.bridgeUrl.value.trim().replace(/\/$/, "") || DEFAULTS.bridgeUrl,
    token: els.token.value.trim() || DEFAULTS.token,
    timeoutSeconds: Math.max(30, Number(els.timeoutSeconds.value || DEFAULTS.timeoutSeconds)),
    pollSeconds: Math.max(30, Number(els.pollSeconds.value || DEFAULTS.pollSeconds)),
    autoRun: Boolean(currentConfig.autoRun)
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
