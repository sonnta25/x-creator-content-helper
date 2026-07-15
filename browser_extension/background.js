const DEFAULTS = {
  bridgeUrl: "http://127.0.0.1:8765",
  token: "local-bridge-change-me",
  timeoutSeconds: 300,
  autoRun: false,
  pollSeconds: 30,
  automationEnabled: false,
  activeStart: "08:00",
  activeEnd: "22:00",
  replyTargetsMinutes: 30,
  replyTargetsQuery: "",
  trendTimes: "09:00,18:00",
  trendCategory: "auto",
  nextReplyTargetsAt: 0,
  trendRunKeys: [],
  lastStatus: "Ready."
};

const AUTO_ALARM = "x-content-bot-auto-run";
const AUTOMATION_ALARM = "x-content-bot-scheduled-approvals";
const FINAL_PROVIDER_URL = "https://gemini.google.com/app";
const FINAL_PROVIDER_ORIGIN = "https://gemini.google.com";
const FINAL_PROVIDER_NAME = "Gemini";
let running = false;

chrome.runtime.onInstalled.addListener(() => {
  ensureAutoAlarm();
  ensureAutomationAlarm();
});

chrome.runtime.onStartup.addListener(() => {
  ensureAutoAlarm();
  ensureAutomationAlarm();
});

chrome.storage.onChanged.addListener((changes, areaName) => {
  if (areaName !== "local") return;
  if (changes.autoRun || changes.pollSeconds) {
    ensureAutoAlarm();
  }
  if (
    changes.automationEnabled || changes.activeStart || changes.activeEnd ||
    changes.replyTargetsMinutes || changes.trendTimes
  ) {
    if (changes.replyTargetsMinutes) {
      const minutes = Math.max(5, Number(changes.replyTargetsMinutes.newValue || DEFAULTS.replyTargetsMinutes));
      chromeStorageSet({ nextReplyTargetsAt: Date.now() + minutes * 60 * 1000 })
        .then(() => ensureAutomationAlarm());
    } else {
      ensureAutomationAlarm();
    }
  }
});

chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === AUTO_ALARM) {
    runJobs({ force: false, maxJobs: 1 });
  }
  if (alarm.name === AUTOMATION_ALARM) {
    automationTick()
      .then((result) => result && !result.opened && result.runJobs
        ? runJobs({ force: true, maxJobs: 1 })
        : null)
      .catch((error) => setStatus(`Automation error: ${error.message || error}`));
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
    loadConfig()
      .then((config) => sendResponse({ ok: true, config }))
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

async function ensureAutoAlarm() {
  const config = await loadConfig();
  await chromeAlarmsClear(AUTO_ALARM);
  if (!config.autoRun) {
    return;
  }
  chrome.alarms.create(AUTO_ALARM, {
    periodInMinutes: Math.max(0.5, config.pollSeconds / 60)
  });
  runJobs({ force: false, maxJobs: 1 });
}

async function ensureAutomationAlarm() {
  const config = await loadConfig();
  await chromeAlarmsClear(AUTOMATION_ALARM);
  if (config.automationEnabled && !config.nextReplyTargetsAt) {
    await chromeStorageSet({
      nextReplyTargetsAt: Date.now() + config.replyTargetsMinutes * 60 * 1000
    });
  }
  chrome.alarms.create(AUTOMATION_ALARM, { periodInMinutes: 0.5 });
  if (config.automationEnabled) {
    automationTick()
      .then((result) => result && !result.opened && result.runJobs
        ? runJobs({ force: true, maxJobs: 1 })
        : null)
      .catch((error) => setStatus(`Automation error: ${error.message || error}`));
  }
}

async function automationTick() {
  let config = await loadConfig();
  config = await syncTelegramAutomationConfig(config);
  const opened = await openNextApprovedAction(config);
  if (opened) return { opened: true, runJobs: false };
  if (!config.automationEnabled || !isInsideActiveWindow(new Date(), config.activeStart, config.activeEnd)) {
    return { opened: false, runJobs: false };
  }

  const now = new Date();
  const nowMs = now.getTime();
  if (!config.nextReplyTargetsAt) {
    await chromeStorageSet({
      nextReplyTargetsAt: nowMs + config.replyTargetsMinutes * 60 * 1000
    });
  } else if (nowMs >= config.nextReplyTargetsAt) {
    await chromeStorageSet({
      nextReplyTargetsAt: nowMs + config.replyTargetsMinutes * 60 * 1000
    });
    await setStatus("Starting scheduled /replytargets...");
    await bridgeFetch(config, "/automation/triggers/replytargets", {
      method: "POST",
      body: {
        query: config.replyTargetsQuery,
        reply_targets_minutes: config.replyTargetsMinutes
      }
    });
  }

  const runKeys = new Set(config.trendRunKeys);
  for (const time of parseTrendTimes(config.trendTimes)) {
    const scheduled = scheduledTimeToday(now, time);
    const ageMs = nowMs - scheduled.getTime();
    const key = `${localDateKey(now)}|${time}`;
    if (ageMs >= 0 && ageMs < 10 * 60 * 1000 && !runKeys.has(key)) {
      runKeys.add(key);
      await chromeStorageSet({ trendRunKeys: Array.from(runKeys).slice(-14) });
      await setStatus(`Starting scheduled /tweettrend3 ${config.trendCategory}...`);
      await bridgeFetch(config, "/automation/triggers/tweettrend3", {
        method: "POST",
        body: { category: config.trendCategory }
      });
    }
  }
  return { opened: false, runJobs: true };
}

async function syncTelegramAutomationConfig(config) {
  const remote = await bridgeFetch(config, "/automation/config");
  const minutes = Number(remote.reply_targets_minutes || 0);
  if (!Number.isFinite(minutes) || minutes < 5 || minutes === config.replyTargetsMinutes) {
    return config;
  }
  const nextReplyTargetsAt = Date.now() + minutes * 60 * 1000;
  await chromeStorageSet({ replyTargetsMinutes: minutes, nextReplyTargetsAt });
  return { ...config, replyTargetsMinutes: minutes, nextReplyTargetsAt };
}

function isInsideActiveWindow(date, start, end) {
  const current = date.getHours() * 60 + date.getMinutes();
  const startMinutes = clockMinutes(start, 0);
  const endMinutes = clockMinutes(end, 24 * 60 - 1);
  if (startMinutes === endMinutes) return true;
  if (startMinutes < endMinutes) {
    return current >= startMinutes && current < endMinutes;
  }
  return current >= startMinutes || current < endMinutes;
}

function clockMinutes(value, fallback) {
  const match = /^(\d{1,2}):(\d{2})$/.exec(String(value || "").trim());
  if (!match) return fallback;
  const hours = Number(match[1]);
  const minutes = Number(match[2]);
  if (hours > 23 || minutes > 59) return fallback;
  return hours * 60 + minutes;
}

function parseTrendTimes(value) {
  return Array.from(new Set(String(value || "")
    .split(",")
    .map((item) => item.trim())
    .filter((item) => clockMinutes(item, -1) >= 0)))
    .sort();
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

async function openNextApprovedAction(config) {
  if (running) return false;
  const payload = await bridgeFetch(config, "/automation/approvals/next");
  if (!payload.action) return false;

  const action = payload.action;
  try {
    const targetUrl = action.kind === "reply" ? String(action.target_url || "") : "https://x.com/compose/post";
    if (!/^https:\/\/(?:www\.)?x\.com\//i.test(targetUrl)) {
      throw new Error("Approved action has an invalid X URL.");
    }
    await setStatus(`Opening approved ${action.kind} in X...`);
    const tab = await chromeTabsCreate({ url: targetUrl, active: true });
    await waitForTabComplete(tab.id);
    const [injection] = await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      func: injectedPrepareXDraft,
      args: [action.kind, action.text, action.target_url]
    });
    const result = injection ? injection.result : null;
    if (!result || !result.ok) {
      throw new Error(result && result.error ? result.error : "Could not prepare the X draft.");
    }
    await bridgeFetch(config, `/automation/approvals/${action.id}/complete`, {
      method: "POST",
      body: {}
    });
    await setStatus(`Approved ${action.kind} is ready in X. Review it and click the final X button.`);
    return true;
  } catch (error) {
    await bridgeFetch(config, `/automation/approvals/${action.id}/error`, {
      method: "POST",
      body: { error: error.message || String(error) }
    });
    throw error;
  }
}

async function runJobs({ force, maxJobs }) {
  if (running) {
    await setStatus("Already running a job.");
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
  if (job.kind === "image") {
    await runImageJob(config, job);
    return true;
  }

  await runGeminiTextJob(config, job);
  return true;
}

async function runGeminiTextJob(config, job, { reportError = true } = {}) {
  try {
    const finalPrompt = job.final_prompt || job.grok_prompt;
    if (!finalPrompt) {
      throw new Error(`Missing ${FINAL_PROVIDER_NAME} prompt for job ${job.id}.`);
    }
    await setStatus(
      `Job ${job.id}\nUsing existing ${FINAL_PROVIDER_NAME} tab...`
    );
    const finalOutput = await runProviderPrompt(
      FINAL_PROVIDER_URL,
      finalPrompt,
      config.timeoutSeconds
    );

    await setStatus(`Job ${job.id}\nReturning final output...`);
    await bridgeFetch(config, `/jobs/${job.id}/result`, {
      method: "POST",
      body: { output: finalOutput }
    });
    await setStatus(`Done. Gemini tab remains open for the next job.\n\n${finalOutput.slice(0, 600)}`);
  } catch (error) {
    if (reportError) {
      await reportJobError(config, job.id, error);
    }
    throw error;
  }
}

async function runImageJob(config, job) {
  try {
    await setStatus(`Image job ${job.id}\nUsing existing ${FINAL_PROVIDER_NAME} tab...`);
    const dataUrl = await runProviderImage(
      FINAL_PROVIDER_URL,
      job.final_prompt || job.grok_prompt,
      config.timeoutSeconds
    );
    if (!isUsableDataUrl(dataUrl)) {
      throw new Error(`${FINAL_PROVIDER_NAME} image was visible, but the extension could not extract usable image bytes.`);
    }
    await bridgeFetch(config, `/jobs/${job.id}/result`, {
      method: "POST",
      body: { image_data_url: dataUrl }
    });
    await setStatus("Image returned to bot. Gemini tab remains open for the next job.");
  } catch (error) {
    await reportJobError(config, job.id, error);
    throw error;
  }
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

async function runProviderPrompt(url, prompt, timeoutSeconds) {
  const tab = await getOrCreateTab(url);
  await chromeTabsUpdate(tab.id, { active: true });
  await waitForTabComplete(tab.id);
  const provider = providerNameFromUrl(url);
  const submittedPrompt = provider === "Gemini" ? compactPromptForSingleInput(prompt) : prompt;
  await setStatus(`Submitting prompt to ${provider}...`);

  const [submitResult] = await chrome.scripting.executeScript({
    target: { tabId: tab.id },
    func: injectedSubmitPrompt,
    args: [submittedPrompt]
  });
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
  let best = "";
  let last = "";
  let stableSince = Date.now();
  let lastStatusAt = 0;
  let lastDebug = "";

  while (Date.now() - started < timeoutMs) {
    const [readResult] = await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      func: injectedReadProviderResponse,
      args: [submittedPrompt, submit.before || ""]
    });
    const value = readResult ? readResult.result : null;
    if (value && value.ok && String(value.text || "").trim()) {
      const current = String(value.text).trim();
      if (current && current !== submit.before) {
        best = current;
      }
      if (current !== last) {
        last = current;
        stableSince = Date.now();
      }
      if (best.length > 20 && Date.now() - stableSince > 3500) {
        return best;
      }
    }
    if (value && value.debug) {
      lastDebug = value.debug;
    }
    if (Date.now() - lastStatusAt > 5000) {
      await setStatus(
        `Waiting for ${provider} response...\n${lastDebug || "No readable candidate yet."}`
      );
      lastStatusAt = Date.now();
    }
    await delay(1000);
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
    .replace(/\n{2,}/g, " || ")
    .replace(/\n/g, " | ")
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
  const [result] = await chrome.scripting.executeScript({
    target: { tabId: tab.id },
    func: injectedDiagnoseDom
  });
  const report = result ? result.result : "";
  if (!report) {
    throw new Error(`No DOM diagnostic report from ${url}`);
  }
  await setStatus(report);
  return report;
}

async function runProviderImage(url, prompt, timeoutSeconds) {
  const tab = await getOrCreateTab(url);
  await chromeTabsUpdate(tab.id, { active: true });
  await waitForTabComplete(tab.id);
  const [result] = await chrome.scripting.executeScript({
    target: { tabId: tab.id },
    func: injectedSubmitAndFindImage,
    args: [prompt, timeoutSeconds * 1000]
  });
  if (!result || !result.result) {
    throw new Error(`No generated image detected on ${url}`);
  }
  if (typeof result.result === "string") {
    if (!isUsableDataUrl(result.result)) {
      throw new Error(`${FINAL_PROVIDER_NAME} returned an empty image data URL.`);
    }
    return result.result;
  }
  if (isUsableDataUrl(result.result.dataUrl)) {
    return result.result.dataUrl;
  }
  if (result.result.src) {
    try {
      await setStatus(`Image visible. Downloading the ${FINAL_PROVIDER_NAME} image URL...`);
      return await fetchImageUrlAsDataUrl(result.result.src);
    } catch (_error) {
      if (!result.result.rect) {
        throw _error;
      }
    }
  }
  if (result.result.rect) {
    await setStatus(`Image visible. Capturing and cropping the ${FINAL_PROVIDER_NAME} tab...`);
    return await captureVisibleImage(tab.windowId, result.result.rect);
  }
  throw new Error(`Generated image was detected on ${url}, but image bytes were empty.`);
}

function isUsableDataUrl(value) {
  return typeof value === "string" && /^data:image\/[a-z0-9.+-]+;base64,.{200,}/i.test(value);
}

async function fetchImageUrlAsDataUrl(src) {
  const response = await fetch(src, { credentials: "include" });
  if (!response.ok) {
    throw new Error(`${FINAL_PROVIDER_NAME} image URL returned HTTP ${response.status}`);
  }
  const blob = await response.blob();
  if (!blob || blob.size < 20000) {
    throw new Error(`${FINAL_PROVIDER_NAME} image URL returned an empty image.`);
  }
  if (!String(blob.type || "").startsWith("image/")) {
    throw new Error(`${FINAL_PROVIDER_NAME} image URL returned ${blob.type || "unknown content type"}.`);
  }
  return await blobToDataUrl(blob);
}

async function captureVisibleImage(windowId, rect) {
  const screenshot = await chromeCaptureVisibleTab(windowId, { format: "png" });
  const response = await fetch(screenshot);
  const blob = await response.blob();
  const bitmap = await createImageBitmap(blob);
  const scale = Number(rect.devicePixelRatio || 1);
  const x = Math.max(0, Math.floor(Number(rect.left || 0) * scale));
  const y = Math.max(0, Math.floor(Number(rect.top || 0) * scale));
  const width = Math.min(bitmap.width - x, Math.floor(Number(rect.width || 0) * scale));
  const height = Math.min(bitmap.height - y, Math.floor(Number(rect.height || 0) * scale));
  if (width <= 0 || height <= 0) {
    throw new Error("Generated image is visible, but its screen bounds could not be cropped.");
  }
  const canvas = new OffscreenCanvas(width, height);
  const context = canvas.getContext("2d");
  context.drawImage(bitmap, x, y, width, height, 0, 0, width, height);
  const cropped = await canvas.convertToBlob({ type: "image/png" });
  return await blobToDataUrl(cropped);
}

async function blobToDataUrl(blob) {
  const buffer = await blob.arrayBuffer();
  const bytes = new Uint8Array(buffer);
  let binary = "";
  const chunkSize = 0x8000;
  for (let index = 0; index < bytes.length; index += chunkSize) {
    const chunk = bytes.subarray(index, index + chunkSize);
    binary += String.fromCharCode(...chunk);
  }
  return `data:${blob.type || "image/png"};base64,${btoa(binary)}`;
}

async function getOrCreateTab(url) {
  const origin = new URL(url).origin;
  const tabs = await chromeTabsQuery({ url: `${origin}/*` });
  if (tabs.length > 0) {
    return tabs[0];
  }
  return await chromeTabsCreate({ url, active: true });
}

async function waitForTabComplete(tabId) {
  const tab = await chromeTabsGet(tabId);
  if (tab.status === "complete") {
    return;
  }
  await new Promise((resolve) => {
    const listener = (updatedTabId, info) => {
      if (updatedTabId === tabId && info.status === "complete") {
        chrome.tabs.onUpdated.removeListener(listener);
        resolve();
      }
    };
    chrome.tabs.onUpdated.addListener(listener);
  });
}

async function bridgeFetch(config, path, options = {}) {
  let response;
  try {
    response = await fetch(`${config.bridgeUrl}${path}`, {
      method: options.method || "GET",
      headers: {
        "Content-Type": "application/json",
        "X-Extension-Bridge-Token": config.token
      },
      body: options.body ? JSON.stringify(options.body) : undefined
    });
  } catch (error) {
    throw new Error(
      `Cannot reach bridge at ${config.bridgeUrl}. Start the Telegram bot with ` +
      `python -m src.main, keep CONTENT_PROVIDER=extension_bridge, then try again.`
    );
  }
  const text = await response.text();
  const payload = text ? JSON.parse(text) : {};
  if (!response.ok) {
    throw new Error(payload.error || `Bridge HTTP ${response.status}`);
  }
  return payload;
}

async function injectedSubmitPrompt(prompt) {
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

  const findInput = () => {
    const selectors = [
      "#prompt-textarea",
      ".ProseMirror",
      "[contenteditable='true']",
      "div[role='textbox']",
      "p[data-placeholder]",
      "textarea"
    ];
    for (const selector of selectors) {
      const items = Array.from(document.querySelectorAll(selector)).filter(isVisible);
      if (items.length) return items[items.length - 1];
    }
    return null;
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

  const setInput = async (el, value) => {
    el.focus();
    el.click();
    const chunks = splitInputChunks(value);
    if ("value" in el) {
      el.value = "";
      for (const chunk of chunks) {
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
    for (const chunk of chunks) {
      const inserted = document.execCommand("insertText", false, chunk);
      if (!inserted) el.textContent = `${el.textContent || ""}${chunk}`;
      el.dispatchEvent(new InputEvent("input", { bubbles: true, inputType: "insertText", data: chunk }));
      await sleep(25);
    }
    el.dispatchEvent(new Event("change", { bubbles: true }));
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
  const input = findInput();
  if (!input) {
    return { ok: false, error: "Could not find Gemini input.", debug: `url=${location.href}` };
  }

  const expectedText = compactText(prompt);
  await setInput(input, prompt);
  await sleep(750);
  let inputText = compactText(input.innerText || input.textContent || input.value || "");
  if (expectedText.length > 0 && inputText.length < Math.max(20, Math.floor(expectedText.length * 0.8))) {
    return {
      ok: false,
      error: "Prompt was not fully inserted into the provider input.",
      debug: `input=${describe(input)}; inputChars=${inputText.length}; expectedChars=${expectedText.length}; inputSample=${inputText.slice(0, 120)}`
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
    debug: `input=${describe(input)}; inputChars=${inputText.length}; send=${submitMethod}`
  };
}

function injectedReadProviderResponse(prompt, before) {
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
    || /"targets"|"variants"|"image_prompt"|"topic"/i.test(prompt);

  const isVisible = (el) => {
    if (!el) return false;
    const style = window.getComputedStyle(el);
    const rect = el.getBoundingClientRect();
    return style.visibility !== "hidden" && style.display !== "none" && rect.width > 0 && rect.height > 0;
  };

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
      "you are a twitter/x tweet qa",
      "you are a twitter/x reply engine",
      "you are an x reply qa",
      "you are an autonomous twitter/x knowledge engine",
      "original task:",
      "original reply task:",
      "original reply-target task:",
      "original tweet task:",
      "original tweet-variants task:",
      "original vietnamese-source task:",
      "chatgpt analysis/draft:",
      "chatgpt draft/analysis",
      "first-pass gemini draft:",
      "generated json:",
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
      for (const el of deepElements(document.body, selector)) {
        pushCandidate(el);
      }
    }
  }

  if (!candidates.length && isGemini) {
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

  if (!candidates.length && isGemini) {
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
      "you are a twitter/x tweet qa",
      "you are a twitter/x reply engine",
      "you are an x reply qa",
      "you are an autonomous twitter/x knowledge engine",
      "your input is one generated tweet",
      "your input is one generated x reply",
      "your job is not to create a new topic",
      "your job is not to write a new reply",
      "your purpose is not to write tweets",
      "never explain your reasoning",
      "return only one final tweet",
      "return only the final rewritten reply",
      "step 1 - silent ai detection",
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
        if (!/[{}]/.test(text) && !/"(?:text|targets|variants|reply|url|image_prompt)"\s*:/.test(text)) {
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

function injectedSubmitAndFindImage(prompt, timeoutMs) {
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

  const isVisible = (el) => {
    if (!el) return false;
    const style = window.getComputedStyle(el);
    const rect = el.getBoundingClientRect();
    return style.visibility !== "hidden" && style.display !== "none" && rect.width > 120 && rect.height > 120;
  };

  const isInputVisible = (el) => {
    if (!el) return false;
    const style = window.getComputedStyle(el);
    const rect = el.getBoundingClientRect();
    return style.visibility !== "hidden" && style.display !== "none" && rect.width > 0 && rect.height > 0;
  };

  const findInput = () => {
    const selectors = ["textarea", "[contenteditable='true']", "div[role='textbox']", "p[data-placeholder]"];
    for (const selector of selectors) {
      const items = Array.from(document.querySelectorAll(selector)).filter(isInputVisible);
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
      return;
    }
    document.execCommand("selectAll", false, null);
    for (const chunk of splitInputChunks(value)) {
      if (!document.execCommand("insertText", false, chunk)) {
        el.textContent = `${el.textContent || ""}${chunk}`;
      }
      el.dispatchEvent(new InputEvent("input", { bubbles: true, inputType: "insertText", data: chunk }));
      await sleep(25);
    }
  };

  const clickSend = () => {
    const buttons = Array.from(document.querySelectorAll("button")).filter(isInputVisible);
    const send = buttons.find((button) => {
      const label = `${button.getAttribute("aria-label") || ""} ${button.title || ""} ${button.textContent || ""}`.toLowerCase();
      return label.includes("send") || label.includes("submit") || label.includes("arrow");
    });
    if (!send) return false;
    send.click();
    return true;
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

  const imageSrcs = () => Array.from(document.images)
    .filter(isVisible)
    .map((img) => {
      const rect = img.getBoundingClientRect();
      const style = window.getComputedStyle(img);
      const src = img.currentSrc || img.src;
      return {
        src,
        complete: Boolean(img.complete),
        naturalWidth: Number(img.naturalWidth || 0),
        naturalHeight: Number(img.naturalHeight || 0),
        area: rect.width * rect.height,
        opacity: Number(style.opacity || 1),
        filter: String(style.filter || ""),
        blurAncestor: Boolean(img.closest("[class*='blur'], [style*='blur']")),
        isPreferredAsset: /(?:googleusercontent\.com|generativelanguage)/i.test(String(src || "")),
        rect
      };
    })
    .filter((item) => {
      const src = item.src || "";
      if (!src || src.includes("avatar") || src.includes("profile")) return false;
      if (item.filter && item.filter !== "none") return false;
      if (item.opacity < 0.95 || item.blurAncestor) return false;
      if (!item.complete && !src.startsWith("blob:") && !src.startsWith("data:")) return false;
      if (item.naturalWidth > 0 || item.naturalHeight > 0) {
        return item.naturalWidth >= 512 && item.naturalHeight >= 512;
      }
      return item.area > 180000;
    })
    .sort((a, b) => {
      if (a.isPreferredAsset !== b.isPreferredAsset) return a.isPreferredAsset ? 1 : -1;
      return a.area - b.area;
    });

  const imageKey = (item) => item.src;

  const imageRectPayload = (item) => ({
    left: item.rect.left,
    top: item.rect.top,
    width: item.rect.width,
    height: item.rect.height,
    devicePixelRatio: window.devicePixelRatio || 1
  });

  const isUsableDataUrl = (value) => (
    typeof value === "string" && /^data:image\/[a-z0-9.+-]+;base64,.{200,}/i.test(value)
  );

  const toDataUrl = async (src) => {
    if (isUsableDataUrl(src)) {
      return src;
    }
    const response = await fetch(src);
    if (!response.ok) {
      throw new Error(`Image fetch returned HTTP ${response.status}`);
    }
    const blob = await response.blob();
    if (!blob || blob.size < 20000) {
      throw new Error("Image fetch returned an empty blob");
    }
    return await new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(reader.result);
      reader.onerror = reject;
      reader.readAsDataURL(blob);
    });
  };

  return (async () => {
    const readyStarted = Date.now();
    let input = null;
    while (Date.now() - readyStarted < 30000) {
      input = findInput();
      if (input) break;
      await sleep(500);
    }
    if (!input) throw new Error("Could not find the Gemini chat input. Are you logged in?");

    const before = new Set(imageSrcs().map(imageKey));
    await setInput(input, prompt);
    await sleep(500);
    if (!clickSend()) pressEnter(input);

    const started = Date.now();
    let visibleCandidate = null;
    let stableCandidate = null;
    let stableSince = 0;
    while (Date.now() - started < timeoutMs) {
      const candidates = imageSrcs();
      const newest = candidates.filter((item) => !before.has(imageKey(item))).pop();
      if (newest) {
        visibleCandidate = newest;
        if (!stableCandidate || imageKey(stableCandidate) !== imageKey(newest)) {
          stableCandidate = newest;
          stableSince = Date.now();
        } else {
          stableCandidate = newest;
        }
        if (Date.now() - stableSince > 3500) {
          try {
            const dataUrl = await toDataUrl(newest.src);
            if (isUsableDataUrl(dataUrl)) {
              return { dataUrl, src: newest.src, rect: imageRectPayload(newest) };
            }
            return { src: newest.src, rect: imageRectPayload(newest) };
          } catch (_error) {
            return { src: newest.src, rect: imageRectPayload(newest) };
          }
        }
      }
      if (!visibleCandidate && candidates.length) {
        visibleCandidate = candidates[candidates.length - 1];
      }
      await sleep(1500);
    }
    if (visibleCandidate) {
      return { src: visibleCandidate.src, rect: imageRectPayload(visibleCandidate) };
    }
    throw new Error("Timed out waiting for a generated image.");
  })();
}

function injectedPrepareXDraft(kind, text, targetUrl) {
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const isVisible = (element) => {
    if (!element) return false;
    const style = window.getComputedStyle(element);
    const rect = element.getBoundingClientRect();
    return style.display !== "none" && style.visibility !== "hidden" && rect.width > 0 && rect.height > 0;
  };
  const findEditor = () => Array.from(document.querySelectorAll(
    "[data-testid='tweetTextarea_0'], div[role='textbox'][contenteditable='true']"
  )).find(isVisible);
  const setEditorText = (editor, value) => {
    editor.focus();
    document.execCommand("selectAll", false, null);
    const inserted = document.execCommand("insertText", false, value);
    if (!inserted) {
      editor.textContent = value;
    }
    editor.dispatchEvent(new InputEvent("input", {
      bubbles: true,
      inputType: "insertText",
      data: value
    }));
  };

  return (async () => {
    const cleanText = String(text || "").trim();
    if (!cleanText) return { ok: false, error: "The approved draft is empty." };

    if (kind === "reply") {
      const match = /\/status\/(\d+)/i.exec(String(targetUrl || ""));
      const statusId = match ? match[1] : "";
      let replyButton = null;
      const replyDeadline = Date.now() + 30000;
      while (Date.now() < replyDeadline && !replyButton) {
        const articles = Array.from(document.querySelectorAll("article")).filter(isVisible);
        const targetArticle = statusId
          ? articles.find((article) => article.querySelector(`a[href*='/status/${statusId}']`))
          : articles[0];
        replyButton = targetArticle && targetArticle.querySelector("[data-testid='reply']");
        if (!replyButton) await sleep(500);
      }
      if (!replyButton) {
        return { ok: false, error: "Could not find the Reply button for the target post. Check X login and page state." };
      }
      replyButton.click();
    }

    const editorDeadline = Date.now() + 30000;
    let editor = null;
    while (Date.now() < editorDeadline && !editor) {
      editor = findEditor();
      if (!editor) await sleep(500);
    }
    if (!editor) {
      return { ok: false, error: "Could not find the X composer. Check that you are logged in." };
    }
    if (String(editor.innerText || editor.textContent || "").trim()) {
      return { ok: false, error: "The X composer already contains text, so it was not overwritten." };
    }
    setEditorText(editor, cleanText);
    await sleep(500);
    const finalText = String(editor.innerText || editor.textContent || "").trim();
    if (!finalText) {
      return { ok: false, error: "X did not accept the draft text." };
    }
    editor.focus();
    return { ok: true, characters: finalText.length };
  })();
}

async function loadConfig() {
  const saved = await chromeStorageGet(DEFAULTS);
  return {
    bridgeUrl: String(saved.bridgeUrl || DEFAULTS.bridgeUrl).replace(/\/$/, ""),
    token: String(saved.token || DEFAULTS.token),
    timeoutSeconds: Math.max(30, Number(saved.timeoutSeconds || DEFAULTS.timeoutSeconds)),
    autoRun: Boolean(saved.autoRun),
    pollSeconds: Math.max(30, Number(saved.pollSeconds || DEFAULTS.pollSeconds)),
    automationEnabled: Boolean(saved.automationEnabled),
    activeStart: String(saved.activeStart || DEFAULTS.activeStart),
    activeEnd: String(saved.activeEnd || DEFAULTS.activeEnd),
    replyTargetsMinutes: Math.max(5, Number(saved.replyTargetsMinutes || DEFAULTS.replyTargetsMinutes)),
    replyTargetsQuery: String(saved.replyTargetsQuery || ""),
    trendTimes: String(saved.trendTimes || DEFAULTS.trendTimes),
    trendCategory: String(saved.trendCategory || DEFAULTS.trendCategory),
    nextReplyTargetsAt: Number(saved.nextReplyTargetsAt || 0),
    trendRunKeys: Array.isArray(saved.trendRunKeys) ? saved.trendRunKeys : [],
    lastStatus: String(saved.lastStatus || DEFAULTS.lastStatus)
  };
}

async function saveConfig(config) {
  await chromeStorageSet({
    bridgeUrl: String(config.bridgeUrl || DEFAULTS.bridgeUrl).replace(/\/$/, ""),
    token: String(config.token || DEFAULTS.token),
    timeoutSeconds: Math.max(30, Number(config.timeoutSeconds || DEFAULTS.timeoutSeconds)),
    autoRun: Boolean(config.autoRun),
    pollSeconds: Math.max(30, Number(config.pollSeconds || DEFAULTS.pollSeconds)),
    automationEnabled: Boolean(config.automationEnabled),
    activeStart: String(config.activeStart || DEFAULTS.activeStart),
    activeEnd: String(config.activeEnd || DEFAULTS.activeEnd),
    replyTargetsMinutes: Math.max(5, Number(config.replyTargetsMinutes || DEFAULTS.replyTargetsMinutes)),
    replyTargetsQuery: String(config.replyTargetsQuery || ""),
    trendTimes: String(config.trendTimes || DEFAULTS.trendTimes),
    trendCategory: String(config.trendCategory || DEFAULTS.trendCategory),
    nextReplyTargetsAt: Number(config.nextReplyTargetsAt || 0),
    trendRunKeys: Array.isArray(config.trendRunKeys) ? config.trendRunKeys : []
  });
}

async function setStatus(text) {
  await chromeStorageSet({ lastStatus: text });
}

function chromeStorageGet(defaults) {
  return new Promise((resolve) => chrome.storage.local.get(defaults, resolve));
}

function chromeStorageSet(values) {
  return new Promise((resolve) => chrome.storage.local.set(values, resolve));
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

function chromeTabsRemove(tabId) {
  return new Promise((resolve) => chrome.tabs.remove(tabId, resolve));
}

function chromeAlarmsClear(name) {
  return new Promise((resolve) => chrome.alarms.clear(name, resolve));
}

function chromeCaptureVisibleTab(windowId, options) {
  return new Promise((resolve, reject) => {
    chrome.tabs.captureVisibleTab(windowId, options, (dataUrl) => {
      if (chrome.runtime.lastError) {
        reject(new Error(chrome.runtime.lastError.message));
        return;
      }
      resolve(dataUrl);
    });
  });
}
