(() => {
  if (globalThis.__xContentBotRuntimeHeartbeat) return;
  globalThis.__xContentBotRuntimeHeartbeat = true;

  const HEARTBEAT_INTERVAL_MS = 25000;

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
