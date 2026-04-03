/**
 * background.js – FocusOS Service Worker (Manifest V3)
 *
 * Responsibilities:
 *  • Open the side panel when the toolbar icon is clicked.
 *  • Relay ANALYZE_PAGE requests from content scripts to the local
 *    Python/FastAPI inference server (http://localhost:8787/predict).
 *  • Propagate scored blocks back to the originating tab's content script.
 *  • Maintain daily brain-budget state in chrome.storage.local.
 *  • Serve GET_STATE / SET_TRACKING / RESET_BUDGET / GET_SETTINGS /
 *    SET_SETTINGS / READING_TIME_UPDATE / BREAK_DISMISSED messages.
 *  • Re-trigger analysis on tab switches and navigation events.
 */

const API_URL      = 'http://localhost:8787/predict';
const SESSION_URL  = 'http://127.0.0.1:8787/session';

/**
 * Daily budget ceiling in weighted focus-minutes.
 * Weighted focus-minutes = actual_minutes × (page_score / 100).
 * At ceiling 100, you'd need 100 minutes of 100-score content to fill it.
 */
const BUDGET_CEILING = 100;

/**
 * Allow accumulation up to 2× the ceiling so the bar can show "overload"
 * without capping the true value; percentages above 100 signal excess.
 */
const BUDGET_OVERLOAD_MULTIPLIER = 2;

/** Default break-suggestion threshold (focus-minutes of active reading). */
const DEFAULT_BREAK_THRESHOLD = 20;

/**
 * Delay (ms) before requesting page text after a navigation event.
 * Content scripts need a short window to initialise after the DOM is ready.
 */
const CONTENT_SCRIPT_INIT_DELAY_MS = 800;

// ─── Side panel ──────────────────────────────────────────────────────────────

chrome.action.onClicked.addListener((tab) => {
  chrome.sidePanel.open({ tabId: tab.id });
});

// ─── Tab switch re-analysis ───────────────────────────────────────────────────

chrome.tabs.onActivated.addListener(async ({ tabId }) => {
  const stored = await chrome.storage.local.get(['trackingEnabled']);
  if (!stored.trackingEnabled) return;
  triggerAnalysisOnTab(tabId);
});

// ─── Navigation re-analysis ──────────────────────────────────────────────────

chrome.webNavigation.onCompleted.addListener(async ({ tabId, frameId }) => {
  if (frameId !== 0) return; // main frame only
  const stored = await chrome.storage.local.get(['trackingEnabled']);
  if (!stored.trackingEnabled) return;
  // Small delay to let the content script initialise after navigation.
  setTimeout(() => triggerAnalysisOnTab(tabId), CONTENT_SCRIPT_INIT_DELAY_MS);
});

/** Ask a tab's content script for its text blocks and run analysis. */
async function triggerAnalysisOnTab(tabId) {
  try {
    const textResult = await chrome.tabs.sendMessage(tabId, { type: 'GET_PAGE_TEXT' });
    if (textResult?.blocks?.length > 0) {
      const tab = await chrome.tabs.get(tabId);
      await analyzePage({ url: tab.url, blocks: textResult.blocks }, tabId);
    }
  } catch (_) { /* content script not ready – ignore */ }
}

// ─── Message router ──────────────────────────────────────────────────────────

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  const tabId = sender.tab?.id ?? null;

  switch (msg.type) {
    case 'ANALYZE_PAGE':
      analyzePage(msg, tabId).then(sendResponse);
      return true;

    case 'GET_STATE':
      getState().then(sendResponse);
      return true;

    case 'SET_TRACKING':
      setTracking(msg.enabled, tabId).then(() => sendResponse({ ok: true }));
      return true;

    case 'RESET_BUDGET':
      resetBudget().then(() => sendResponse({ ok: true }));
      return true;

    case 'GET_SETTINGS':
      getSettings().then(sendResponse);
      return true;

    case 'SET_SETTINGS':
      saveSettings(msg.settings).then(() => sendResponse({ ok: true }));
      return true;

    case 'READING_TIME_UPDATE':
      handleReadingTimeUpdate(msg.seconds, tabId).then(() => sendResponse({ ok: true }));
      return true;

    case 'BREAK_DISMISSED':
      resetBreakTimer().then(() => sendResponse({ ok: true }));
      return true;
  }
});

// ─── Core: page analysis ─────────────────────────────────────────────────────

/**
 * POST extracted blocks to the local inference server and handle the
 * response (overlay, budget update, sidebar notification, session recording).
 */
async function analyzePage({ url, blocks }, tabId) {
  if (!blocks || blocks.length === 0) {
    return { error: 'No text blocks found on this page.' };
  }

  let data;
  try {
    const res = await fetch(API_URL, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        page_url: url,
        timestamp: new Date().toISOString(),
        blocks,
      }),
    });

    if (!res.ok) {
      throw new Error(`Server returned HTTP ${res.status}`);
    }

    data = await res.json();
  } catch (err) {
    console.warn('[FocusOS] Inference server unavailable:', err.message);
    return { error: err.message };
  }

  // 1. Push scored overlay AND page score to the content script so it can
  //    attribute reading time to the correct load level.
  if (tabId !== null) {
    try {
      await chrome.tabs.sendMessage(tabId, {
        type: 'APPLY_SCORES',
        blocks: data.blocks,
        pageScore: data.page_score ?? 0,
      });
    } catch (_) {
      // Content script may not be ready yet – not fatal.
    }
  }

  // 2. Store the latest page score so time-based budget accumulation can use it.
  const pageScore = data.page_score ?? 0;
  await chrome.storage.local.set({ lastPageScore: pageScore });

  // 3. Persist the latest page result for the sidebar.
  await chrome.storage.local.set({
    lastPageResult: {
      url,
      score: pageScore,
      blocks: data.blocks ?? [],
      timestamp: Date.now(),
    },
  });

  // 3. Ping the sidebar (may not be open – ignore failure).
  try {
    chrome.runtime.sendMessage({ type: 'PAGE_ANALYZED', data });
  } catch (_) { /* sidebar closed */ }

  return { ok: true, data };
}

/** Send GET_PAGE_TEXT to a tab and run analysis if blocks are available. */
async function triggerAnalysisOnTab(tabId) {
  try {
    const textResult = await chrome.tabs.sendMessage(tabId, { type: 'GET_PAGE_TEXT' });
    if (textResult?.blocks?.length > 0) {
      const tab = await chrome.tabs.get(tabId);
      await analyzePage({ url: tab.url, blocks: textResult.blocks }, tabId);
    }
  } catch (_) { /* content script not ready or tab unavailable */ }
}

// ─── Reading session end ──────────────────────────────────────────────────────

/**
 * Called when the content script reports the tab becoming hidden or the page
 * unloading. Computes focus-minutes = (pageScore / 100) × elapsedMinutes and
 * accumulates into the daily budget and consecutive-session counter.
 */
async function handleReadingSessionEnd({ elapsedSeconds, pageScore, url }, senderTabId) {
  const elapsedMinutes = Math.min((elapsedSeconds ?? 0) / 60, MAX_SESSION_MINUTES);
  if (elapsedMinutes < 0.1 || !pageScore || pageScore <= 0) return;

  // focus-minutes = normalised load × time, capped per page
  const focusContribution = Math.min(
    (pageScore / 100) * elapsedMinutes,
    MAX_PAGE_FOCUS_MINUTES
  );
  if (focusContribution < 0.01) return;

  await accumulateBudget(focusContribution);

  // Forward session data to the local server for the dashboard timeline.
  logSessionToServer({
    url,
    page_score: pageScore,
    elapsed_minutes: elapsedMinutes,
    focus_contribution: focusContribution,
  }).catch(() => { /* server may be offline */ });

  // Show break popup on whichever tab is currently active.
  const [activeTab] = await chrome.tabs.query({ active: true, currentWindow: true });
  const targetTabId = activeTab?.id ?? senderTabId;
  await checkBreakSuggestion(focusContribution, targetTabId);
}

// ─── Tracking state ──────────────────────────────────────────────────────────

async function setTracking(enabled, activeTabId) {
  await chrome.storage.local.set({ trackingEnabled: enabled });

  // Broadcast new state to every tab that has the content script loaded.
  const tabs = await chrome.tabs.query({});
  await Promise.allSettled(
    tabs.map((tab) =>
      chrome.tabs.sendMessage(tab.id, { type: 'TRACKING_STATE', enabled }).catch(() => {})
    )
  );

  // When turning tracking ON, kick off analysis on the active tab right away.
  if (enabled && activeTabId !== null) {
    triggerAnalysisOnTab(activeTabId);
  }
}

// ─── Reading-time budget ──────────────────────────────────────────────────────

/**
 * Called every ~30 s by the content script while the user is actively reading.
 * Accumulates time-weighted brain budget and checks the break threshold.
 *
 * Budget contribution = page_score × (seconds / 60) / 100
 *   → fills the 100-point daily ceiling proportional to both load and time.
 *
 * Break threshold is measured in raw focus-minutes (unweighted), so the
 * break suggestion depends only on how long the user has been reading,
 * not on the cognitive weight of each page.
 */
async function handleReadingTimeUpdate(seconds, tabId) {
  if (!seconds || seconds <= 0) return;

  const today = new Date().toDateString();
  const stored = await chrome.storage.local.get([
    'dailyBudget',
    'budgetDate',
    'lastPageScore',
    'focusSecondsSinceBreak',
    'breakThreshold',
    'lastSessionSeconds',
    'lastSessionUrl',
    'lastPageResult',
  ]);

  // ── Time-weighted budget ──
  const pageScore  = stored.lastPageScore ?? 0;
  const baseBudget = stored.budgetDate === today ? (stored.dailyBudget ?? 0) : 0;
  const contribution = pageScore * (seconds / 60) / 100;
  const nextBudget = Math.min(baseBudget + contribution, BUDGET_CEILING * BUDGET_OVERLOAD_MULTIPLIER);

  // ── Break timer ──
  const rawSeconds = (stored.focusSecondsSinceBreak ?? 0) + seconds;
  const threshold  = (stored.breakThreshold ?? DEFAULT_BREAK_THRESHOLD) * 60;

  await chrome.storage.local.set({
    dailyBudget:            nextBudget,
    budgetDate:             today,
    focusSecondsSinceBreak: rawSeconds,
  });

  // ── Notify sidebar of budget update ──
  try {
    chrome.runtime.sendMessage({ type: 'BUDGET_UPDATED' });
  } catch (_) { /* sidebar closed */ }

  // ── Break suggestion popup ──
  if (rawSeconds >= threshold && tabId !== null) {
    try {
      await chrome.tabs.sendMessage(tabId, { type: 'SHOW_BREAK_POPUP' });
      // Reset timer only after successfully notifying the content script.
      await chrome.storage.local.set({ focusSecondsSinceBreak: 0 });
    } catch (_) { /* content script not available */ }
  }

  // ── Record session chunk to FastAPI timeline ──
  const pageUrl = stored.lastPageResult?.url ?? '';
  if (pageUrl) {
    const blocksData = stored.lastPageResult?.blocks ?? [];
    const langMean = _mean(blocksData.map((b) => b.lang ?? 0));
    const execMean = _mean(blocksData.map((b) => b.exec ?? 0));
    const visMean  = _mean(blocksData.map((b) => b.vis  ?? 0));

    fetch(SESSION_URL, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        page_url:       pageUrl,
        timestamp:      new Date().toISOString(),
        page_score:     pageScore,
        page_label:     scoreLabel(pageScore),
        active_seconds: seconds,
        lang_mean:      langMean,
        exec_mean:      execMean,
        vis_mean:       visMean,
      }),
    }).catch(() => { /* server may not be running */ });
  }
}

async function resetBreakTimer() {
  await chrome.storage.local.set({ focusSecondsSinceBreak: 0 });
}

// ─── Brain budget helpers ─────────────────────────────────────────────────────

async function resetBudget() {
  await chrome.storage.local.set({
    dailyBudget:            0,
    budgetDate:             new Date().toDateString(),
    focusSecondsSinceBreak: 0,
  });
}

// ─── Settings ─────────────────────────────────────────────────────────────────

async function getSettings() {
  const stored = await chrome.storage.local.get(['breakThreshold']);
  return {
    breakThreshold: stored.breakThreshold ?? DEFAULT_BREAK_THRESHOLD,
  };
}

async function saveSettings(settings) {
  const patch = {};
  if (typeof settings.breakThreshold === 'number') {
    patch.breakThreshold = Math.max(10, Math.min(60, Math.round(settings.breakThreshold)));
  }
  await chrome.storage.local.set(patch);
}

// ─── State snapshot for sidebar ──────────────────────────────────────────────

async function getState() {
  const today = new Date().toDateString();
  const stored = await chrome.storage.local.get([
    'trackingEnabled',
    'dailyFocusMinutes',
    'budgetDate',
    'lastPageResult',
    'focusSecondsSinceBreak',
    'breakThreshold',
  ]);

  const rawBudget  = stored.budgetDate === today ? (stored.dailyBudget ?? 0) : 0;
  const budgetPercent = Math.min(Math.round((rawBudget / BUDGET_CEILING) * 100), 200);

  const focusMinutesSinceBreak = Math.floor((stored.focusSecondsSinceBreak ?? 0) / 60);
  const breakThreshold = stored.breakThreshold ?? DEFAULT_BREAK_THRESHOLD;

  return {
    trackingEnabled:      stored.trackingEnabled === true,
    dailyBudget:          rawBudget,
    budgetPercent,
    lastPageResult:       stored.lastPageResult ?? null,
    focusMinutesSinceBreak,
    breakThreshold,
  };
}

// ─── Utilities ────────────────────────────────────────────────────────────────

function _mean(arr) {
  if (!arr.length) return 0;
  return arr.reduce((s, v) => s + v, 0) / arr.length;
}

function scoreLabel(score) {
  if (score < 30) return 'low';
  if (score < 60) return 'good';
  return 'high';
}
