/**
 * sidebar.js – FocusOS Sidebar Logic
 *
 * Responsibilities:
 *  • Render tracking toggle, daily brain budget (focus-minutes), current-page
 *    score, top high-load block list, reading strategy recommendations.
 *  • Show consecutive focus-minute progress and a user-tunable break threshold.
 *  • Communicate with the background service worker via
 *    chrome.runtime.sendMessage().
 *  • Listen for live PAGE_ANALYZED / STATE_UPDATED updates and refresh the UI.
 */

'use strict';

// ─── DOM refs ─────────────────────────────────────────────────────────────────

const toggleBtn            = document.getElementById('tracking-toggle');
const trackingStatus       = document.getElementById('tracking-status-text');
const trackingHint         = document.getElementById('tracking-hint');
const budgetBar            = document.getElementById('budget-bar');
const budgetBarWrap        = document.querySelector('.budget-bar-wrap');
const budgetPct            = document.getElementById('budget-percent');
const budgetMinutes        = document.getElementById('budget-minutes');
const resetBudgetBtn       = document.getElementById('reset-budget-btn');
const scoreBadge           = document.getElementById('page-score-badge');
const scoreNumber          = document.getElementById('page-score-number');
const pageSummary          = document.getElementById('page-summary');
const topBlocksList        = document.getElementById('top-blocks-list');
const strategyText         = document.getElementById('strategy-text');
const consecutiveMinEl     = document.getElementById('consecutive-minutes');
const thresholdInlineEl    = document.getElementById('threshold-inline');
const sessionBar           = document.getElementById('session-bar');
const resetSessionBtn      = document.getElementById('reset-session-btn');
const thresholdInput       = document.getElementById('break-threshold-input');
const thresholdValueDisplay= document.getElementById('threshold-value-display');

// ─── Bootstrap ────────────────────────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', () => {
  loadAndRender();

  toggleBtn.addEventListener('click', handleToggle);
  resetBudgetBtn.addEventListener('click', handleResetBudget);
  resetSessionBtn.addEventListener('click', handleResetSession);

  // Threshold slider: live display + save on change.
  thresholdInput.addEventListener('input', () => {
    const val = Number(thresholdInput.value);
    thresholdValueDisplay.textContent = val + ' min';
    thresholdInlineEl.textContent = val;
  });
  thresholdInput.addEventListener('change', () => {
    bg('SET_BREAK_THRESHOLD', { threshold: Number(thresholdInput.value) });
  });

  // Listen for live updates pushed from the background script.
  chrome.runtime.onMessage.addListener((msg) => {
    if (msg.type === 'PAGE_ANALYZED' || msg.type === 'STATE_UPDATED') {
      loadAndRender();
    }
  });
});

// ─── State loading ────────────────────────────────────────────────────────────

async function loadAndRender() {
  const state = await bg('GET_STATE');
  if (!state) return;
  renderTracking(state.trackingEnabled);
  renderBudget(state.budgetPercent, state.dailyFocusMinutes ?? 0);
  renderSession(state.consecutiveFocusMinutes ?? 0, state.breakThreshold ?? 20);
  if (state.lastPageResult) {
    renderPageResult(state.lastPageResult);
  }
}

// ─── Tracking toggle ──────────────────────────────────────────────────────────

async function handleToggle() {
  const current = toggleBtn.getAttribute('aria-checked') === 'true';
  const next = !current;

  // Optimistic UI update.
  renderTracking(next);

  await bg('SET_TRACKING', { enabled: next });

  // Re-sync from source of truth.
  loadAndRender();
}

function renderTracking(enabled) {
  toggleBtn.setAttribute('aria-checked', String(enabled));
  trackingStatus.textContent = enabled ? 'ON' : 'OFF';
  trackingStatus.className = 'tracking-status ' + (enabled ? 'on' : 'off');
  trackingHint.textContent = enabled
    ? 'Overlay active – colored blocks show predicted load.'
    : 'Turn tracking on to analyze this page.';
}

// ─── Budget ───────────────────────────────────────────────────────────────────

function renderBudget(percent, rawMinutes) {
  const capped = Math.min(percent, 100);
  budgetBar.style.width = capped + '%';
  budgetBarWrap.setAttribute('aria-valuenow', capped);
  budgetPct.textContent = percent + '%';
  budgetPct.style.color = budgetColour(percent);
  if (budgetMinutes) {
    budgetMinutes.textContent = rawMinutes.toFixed(1) + ' / 100 focus-min';
  }
}

function budgetColour(pct) {
  if (pct < 40) return 'var(--accent-green)';
  if (pct < 75) return 'var(--accent-amber)';
  return 'var(--accent-red)';
}

async function handleResetBudget() {
  await bg('RESET_BUDGET');
  renderBudget(0, 0);
  renderSession(0, Number(thresholdInput.value) || 20);
}

// ─── Focus Session ────────────────────────────────────────────────────────────

function renderSession(consecutiveMinutes, threshold) {
  const display = consecutiveMinutes.toFixed(1);
  consecutiveMinEl.textContent = display;
  thresholdInlineEl.textContent = threshold;

  // Sync slider (only if user isn't actively dragging it).
  if (document.activeElement !== thresholdInput) {
    thresholdInput.value = threshold;
    thresholdValueDisplay.textContent = threshold + ' min';
  }

  // Session progress bar (0–threshold → 0–100%).
  const pct = Math.min((consecutiveMinutes / threshold) * 100, 100);
  sessionBar.style.width = pct + '%';
  sessionBar.className = 'session-bar ' + sessionBarColour(pct);
}

function sessionBarColour(pct) {
  if (pct < 50) return 'low';
  if (pct < 85) return 'medium';
  return 'high';
}

async function handleResetSession() {
  await bg('RESET_CONSECUTIVE');
  renderSession(0, Number(thresholdInput.value) || 20);
}

// ─── Page result ──────────────────────────────────────────────────────────────

function renderPageResult({ score, blocks }) {
  // ── Score badge + number ──
  scoreNumber.textContent = String(Math.round(score ?? 0));

  const level = scoreLevel(score);
  scoreBadge.className = 'score-badge ' + level.cls;
  scoreBadge.textContent = level.label;

  pageSummary.textContent = level.description;

  // ── Top high-load blocks (top 5 by load) ──
  const sorted = (blocks ?? [])
    .slice()
    .sort((a, b) => (b.load ?? 0) - (a.load ?? 0))
    .slice(0, 5);

  renderTopBlocks(sorted);

  // ── Reading strategy ──
  strategyText.textContent = readingStrategy(score);
}

function scoreLevel(score) {
  if (score == null || score === 0) {
    return { cls: 'neutral', label: '—', description: 'Analyse a page to see its cognitive load.' };
  }
  if (score < 30) {
    return {
      cls: 'low',
      label: 'Low demand',
      description: 'Light neural cost. Good for any time of day.',
    };
  }
  if (score < 60) {
    return {
      cls: 'good',
      label: 'Moderate',
      description: 'Moderate cognitive demand. Suitable for normal focus periods.',
    };
  }
  return {
    cls: 'high',
    label: 'High demand',
    description: 'High predicted cortical activation. Best tackled during peak focus hours.',
  };
}

function readingStrategy(score) {
  if (score == null || score === 0) return '—';
  if (score < 30) {
    return '✅ Low demand — fine to skim or read anytime.';
  }
  if (score < 60) {
    return '☀️ Moderate — schedule for a normal focus window (e.g. mid-morning).';
  }
  return '🔴 High demand — save for peak focus hours (e.g. 9–11 am or 3–5 pm) when your working memory is fresh.';
}

// ─── Block list ───────────────────────────────────────────────────────────────

function renderTopBlocks(blocks) {
  if (!blocks || blocks.length === 0) {
    topBlocksList.innerHTML = '<li class="block-list-empty">No high-load sections detected.</li>';
    return;
  }

  topBlocksList.innerHTML = blocks.map((block) => {
    const loadVal = (block.load ?? 0).toFixed(2);
    const cls = blockClass(block.load);
    const snippet = truncate(block.text ?? '', 80);
    return `
      <li class="block-item ${cls}">
        <span class="block-item-score">${loadVal}</span>
        <span class="block-item-text">${escapeHtml(snippet)}</span>
      </li>`;
  }).join('');
}

function blockClass(load) {
  if (load < 0.33) return 'low';
  if (load < 0.66) return 'medium';
  return 'high';
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

/** Send a message to the background service worker and return the response. */
function bg(type, extra = {}) {
  return new Promise((resolve) => {
    chrome.runtime.sendMessage({ type, ...extra }, (response) => {
      if (chrome.runtime.lastError) {
        console.warn('[FocusOS]', chrome.runtime.lastError.message);
        resolve(null);
      } else {
        resolve(response);
      }
    });
  });
}

function truncate(str, maxLen) {
  if (str.length <= maxLen) return str;
  return str.slice(0, maxLen - 1) + '…';
}

function escapeHtml(str) {
  const map = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
  return str.replace(/[&<>"']/g, (c) => map[c]);
}
