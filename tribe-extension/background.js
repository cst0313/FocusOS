/**
 * background.js – FocusOS Service Worker (Manifest V3)
 *
 * Responsibilities:
 *  • Open the side panel when the toolbar icon is clicked.
 *  • Relay ANALYZE_PAGE requests from content scripts to the local
 *    Python/FastAPI inference server (http://localhost:8787/predict).
 *  • Propagate scored blocks back to the originating tab's content script.
 *  • Maintain daily brain-budget state in chrome.storage.local.
 *  • Serve GET_STATE / SET_TRACKING / RESET_BUDGET messages from the sidebar.
 */

const API_URL = 'http://localhost:8787/predict';

/** Daily budget ceiling (normalised to ~100 "cognitive points"). */
const BUDGET_CEILING = 100;

// ─── Side panel ──────────────────────────────────────────────────────────────

chrome.action.onClicked.addListener((tab) => {
  chrome.sidePanel.open({ tabId: tab.id });
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
  }
});

// ─── Core: page analysis ─────────────────────────────────────────────────────

/**
 * POST the extracted blocks to the local inference server and handle the
 * response (overlay, budget update, sidebar notification).
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

  // 1. Push scored overlay to the content script of the originating tab.
  if (tabId !== null) {
    try {
      await chrome.tabs.sendMessage(tabId, {
        type: 'APPLY_SCORES',
        blocks: data.blocks,
      });
    } catch (_) {
      // Content script may not be ready yet – not fatal.
    }
  }

  // 2. Accumulate into today's brain budget.
  await accumulateBudget(data.page_score ?? 0);

  // 3. Persist the latest page result for the sidebar.
  await chrome.storage.local.set({
    lastPageResult: {
      url,
      score: data.page_score ?? 0,
      blocks: data.blocks ?? [],
      timestamp: Date.now(),
    },
  });

  // 4. Ping the sidebar (may not be open – ignore failure).
  try {
    chrome.runtime.sendMessage({ type: 'PAGE_ANALYZED', data });
  } catch (_) { /* sidebar closed */ }

  return { ok: true, data };
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
  const broadcasts = tabs.map((tab) =>
    chrome.tabs.sendMessage(tab.id, { type: 'TRACKING_STATE', enabled }).catch(() => {})
  );
  await Promise.allSettled(broadcasts);

  // When turning tracking ON, kick off analysis on the active tab right away.
  if (enabled && activeTabId !== null) {
    try {
      const textResult = await chrome.tabs.sendMessage(activeTabId, { type: 'GET_PAGE_TEXT' });
      if (textResult?.blocks?.length > 0) {
        const tab = await chrome.tabs.get(activeTabId);
        await analyzePage({ url: tab.url, blocks: textResult.blocks }, activeTabId);
      }
    } catch (_) { /* content script not ready */ }
  }
}

// ─── Brain budget helpers ─────────────────────────────────────────────────────

async function accumulateBudget(pageScore) {
  const today = new Date().toDateString();
  const stored = await chrome.storage.local.get(['dailyBudget', 'budgetDate']);

  const base = stored.budgetDate === today ? (stored.dailyBudget ?? 0) : 0;
  const next = Math.min(base + pageScore, BUDGET_CEILING * 2); // allow >100% to show overload

  await chrome.storage.local.set({ dailyBudget: next, budgetDate: today });
}

async function resetBudget() {
  await chrome.storage.local.set({
    dailyBudget: 0,
    budgetDate: new Date().toDateString(),
  });
}

// ─── State snapshot for sidebar ──────────────────────────────────────────────

async function getState() {
  const today = new Date().toDateString();
  const stored = await chrome.storage.local.get([
    'trackingEnabled',
    'dailyBudget',
    'budgetDate',
    'lastPageResult',
  ]);

  const rawBudget = stored.budgetDate === today ? (stored.dailyBudget ?? 0) : 0;
  const budgetPercent = Math.min(Math.round((rawBudget / BUDGET_CEILING) * 100), 200);

  return {
    trackingEnabled: stored.trackingEnabled === true,
    dailyBudget: rawBudget,
    budgetPercent,
    lastPageResult: stored.lastPageResult ?? null,
  };
}
