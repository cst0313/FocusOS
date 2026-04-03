/**
 * background.js – FocusOS Service Worker (Manifest V3)
 *
 * Responsibilities:
 *  • Open the side panel when the toolbar icon is clicked.
 *  • Relay ANALYZE_PAGE requests from content scripts to the local
 *    Python/FastAPI inference server (http://localhost:8787/predict).
 *  • Propagate scored blocks back to the originating tab's content script,
 *    including the page score so the content script can track reading time.
 *  • Re-trigger analysis on every tab switch and whenever a new link loads.
 *  • Accumulate brain budget using time-based formula: score × reading_minutes.
 *  • Send break-suggestion popups when consecutive focus-minutes pass threshold.
 *  • Serve GET_STATE / SET_TRACKING / RESET_BUDGET / SET_BREAK_THRESHOLD /
 *    RESET_CONSECUTIVE / READING_SESSION_END messages.
 */

const API_URL = 'http://localhost:8787/predict';
const LOG_URL = 'http://localhost:8787/log_session';

/** Daily budget ceiling in focus-minutes (~100 focus-minutes models 9am–8pm). */
const BUDGET_CEILING = 100;

/** Cap any single reading session at 60 minutes to avoid inflating idle tabs. */
const MAX_SESSION_MINUTES = 60;

/** Cap per-page focus-minute contribution to prevent extreme outliers. */
const MAX_PAGE_FOCUS_MINUTES = 15;

/** Default break threshold in consecutive focus-minutes. */
const DEFAULT_BREAK_THRESHOLD = 20;

/**
 * If the gap between sessions exceeds this many minutes, reset the
 * consecutive focus-minute counter (user naturally took a break).
 */
const BREAK_GAP_MINUTES = 30;

// ─── Side panel ──────────────────────────────────────────────────────────────

chrome.action.onClicked.addListener((tab) => {
  chrome.sidePanel.open({ tabId: tab.id });
});

// ─── Tab switch: re-analyse when the user makes a tab active ─────────────────

chrome.tabs.onActivated.addListener(async ({ tabId }) => {
  const { trackingEnabled } = await chrome.storage.local.get('trackingEnabled');
  if (!trackingEnabled) return;
  // Small delay so the content script is ready (tab may still be restoring).
  setTimeout(() => triggerAnalysisOnTab(tabId), 300);
});

// ─── URL change: re-analyse when a new link is opened in the active tab ──────

chrome.tabs.onUpdated.addListener(async (tabId, changeInfo, tab) => {
  if (changeInfo.status !== 'complete') return;
  if (!tab.url || tab.url.startsWith('chrome://') || tab.url.startsWith('chrome-extension://')) return;
  if (!tab.active) return;

  const { trackingEnabled } = await chrome.storage.local.get('trackingEnabled');
  if (!trackingEnabled) return;

  triggerAnalysisOnTab(tabId);
});

// ─── Message router ──────────────────────────────────────────────────────────

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  const tabId = sender.tab?.id ?? null;

  switch (msg.type) {
    case 'ANALYZE_PAGE':
      analyzePage(msg, tabId).then(sendResponse);
      return true; // keep channel open for async reply

    case 'GET_STATE':
      getState().then(sendResponse);
      return true;

    case 'SET_TRACKING':
      setTracking(msg.enabled, tabId).then(() => sendResponse({ ok: true }));
      return true;

    case 'RESET_BUDGET':
      resetBudget().then(() => sendResponse({ ok: true }));
      return true;

    case 'READING_SESSION_END':
      handleReadingSessionEnd(msg, tabId).then(() => sendResponse({ ok: true }));
      return true;

    case 'SET_BREAK_THRESHOLD':
      chrome.storage.local.set({ breakThreshold: msg.threshold })
        .then(() => sendResponse({ ok: true }));
      return true;

    case 'RESET_CONSECUTIVE':
      chrome.storage.local.set({
        consecutiveFocusMinutes: 0,
        lastSessionEndTime: Date.now(),
      }).then(() => sendResponse({ ok: true }));
      return true;
  }
});

// ─── Core: page analysis ─────────────────────────────────────────────────────

/**
 * POST the extracted blocks to the local inference server and handle the
 * response (overlay, sidebar notification).
 *
 * NOTE: budget is no longer accumulated here – it is accumulated when the
 * content script reports how long the user actually read the page.
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

  // 2. Persist the latest page result for the sidebar.
  await chrome.storage.local.set({
    lastPageResult: {
      url,
      score: data.page_score ?? 0,
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

/**
 * Persist the tracking preference and broadcast it to all eligible tabs.
 * When enabling, immediately trigger analysis on the currently active tab.
 */
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
    await triggerAnalysisOnTab(activeTabId);
  }
}

// ─── Brain budget helpers ─────────────────────────────────────────────────────

/**
 * Add focus-minutes to today's cumulative brain budget.
 * focusMinutes = (page_score / 100) × reading_minutes, capped per session.
 */
async function accumulateBudget(focusMinutes) {
  const today = new Date().toDateString();
  const stored = await chrome.storage.local.get(['dailyFocusMinutes', 'budgetDate']);

  const base = stored.budgetDate === today ? (stored.dailyFocusMinutes ?? 0) : 0;
  const next = base + focusMinutes;

  await chrome.storage.local.set({ dailyFocusMinutes: next, budgetDate: today });
}

async function resetBudget() {
  await chrome.storage.local.set({
    dailyFocusMinutes: 0,
    budgetDate: new Date().toDateString(),
    consecutiveFocusMinutes: 0,
    lastSessionEndTime: Date.now(),
  });
}

// ─── Break suggestion ─────────────────────────────────────────────────────────

/**
 * Update the consecutive focus-minute counter. When the user-defined threshold
 * is crossed, send a SHOW_BREAK_POPUP message to the active tab and reset the
 * counter so the next break triggers after another full threshold cycle.
 */
async function checkBreakSuggestion(focusContribution, activeTabId) {
  const stored = await chrome.storage.local.get([
    'consecutiveFocusMinutes',
    'lastSessionEndTime',
    'breakThreshold',
  ]);

  const threshold = stored.breakThreshold ?? DEFAULT_BREAK_THRESHOLD;
  const lastEnd = stored.lastSessionEndTime ?? 0;

  // Auto-reset if there has been a natural break (long gap between sessions).
  const gapMinutes = lastEnd > 0 ? (Date.now() - lastEnd) / 60000 : 0;
  const prevConsecutive = gapMinutes > BREAK_GAP_MINUTES
    ? 0
    : (stored.consecutiveFocusMinutes ?? 0);

  const newConsecutive = prevConsecutive + focusContribution;
  const now = Date.now();

  // Notify the sidebar to refresh its display.
  try { chrome.runtime.sendMessage({ type: 'STATE_UPDATED' }); } catch (_) {}

  if (prevConsecutive < threshold && newConsecutive >= threshold) {
    // Threshold just crossed – show break popup, then reset the consecutive
    // counter so the user gets reminded again after another threshold cycle.
    await chrome.storage.local.set({
      consecutiveFocusMinutes: 0,
      lastSessionEndTime: now,
    });

    if (activeTabId != null) {
      try {
        await chrome.tabs.sendMessage(activeTabId, { type: 'SHOW_BREAK_POPUP' });
      } catch (_) {}
    }
  } else {
    await chrome.storage.local.set({
      consecutiveFocusMinutes: newConsecutive,
      lastSessionEndTime: now,
    });
  }
}

// ─── State snapshot for sidebar ──────────────────────────────────────────────

async function getState() {
  const today = new Date().toDateString();
  const stored = await chrome.storage.local.get([
    'trackingEnabled',
    'dailyFocusMinutes',
    'budgetDate',
    'lastPageResult',
    'consecutiveFocusMinutes',
    'lastSessionEndTime',
    'breakThreshold',
  ]);

  const rawMinutes = stored.budgetDate === today ? (stored.dailyFocusMinutes ?? 0) : 0;
  const budgetPercent = Math.min(Math.round((rawMinutes / BUDGET_CEILING) * 100), 200);

  const threshold = stored.breakThreshold ?? DEFAULT_BREAK_THRESHOLD;
  const lastEnd = stored.lastSessionEndTime ?? 0;
  const gapMinutes = lastEnd > 0 ? (Date.now() - lastEnd) / 60000 : 0;
  const consecutiveMinutes = gapMinutes > BREAK_GAP_MINUTES
    ? 0
    : (stored.consecutiveFocusMinutes ?? 0);

  return {
    trackingEnabled: stored.trackingEnabled === true,
    dailyFocusMinutes: rawMinutes,
    budgetPercent,
    lastPageResult: stored.lastPageResult ?? null,
    consecutiveFocusMinutes: consecutiveMinutes,
    breakThreshold: threshold,
  };
}

// ─── Log session to local server (for dashboard timeline) ────────────────────

async function logSessionToServer(sessionData) {
  await fetch(LOG_URL, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      ...sessionData,
      timestamp: new Date().toISOString(),
    }),
  });
}
