(() => {
  if (globalThis.__xContentBotRuntimeHeartbeat) return;
  globalThis.__xContentBotRuntimeHeartbeat = true;

  // The extension alarms are the primary scheduler. This low-frequency page
  // heartbeat is only a recovery path when Chrome drops an MV3 alarm.
  const HEARTBEAT_INTERVAL_MS = 60000;

  function wakeExtensionRuntime() {
    try {
      chrome.runtime.sendMessage({ action: "runtime-heartbeat" }, () => {
        // Reading lastError prevents a noisy console warning while the extension
        // is being reloaded or its service worker is starting.
        void chrome.runtime.lastError;
      });
    } catch (_error) {
      // The next interval retries after an extension reload or transient shutdown.
    }
  }

  wakeExtensionRuntime();
  setInterval(wakeExtensionRuntime, HEARTBEAT_INTERVAL_MS);
})();
