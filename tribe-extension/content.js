/**
 * content.js – FocusOS Content Script
 *
 * Responsibilities:
 *  1. Extract readable text blocks (paragraphs, headings, list items, etc.)
 *     from the live DOM, tagging each with a unique data attribute.
 *  2. Listen for tracking-state changes and trigger / clear the heatmap
 *     overlay accordingly.
 *  3. Apply per-block color overlays (green / yellow / red) when the
 *     background script returns scored blocks.
 *  4. Track active reading time and report to the background every 30 s.
 *  5. Show / hide the brain-break popup when instructed by the background.
 *
 * All processing is local – no data leaves the machine.
 */

/** Minimum character count for a block to be considered worth analysing. */
const MIN_BLOCK_LENGTH = 20;

/** Maximum characters sent per block to keep API payloads reasonable. */
const MAX_BLOCK_TEXT_LENGTH = 600;

/** How often (ms) to report reading time to the background service worker. */
const REPORT_INTERVAL_MS = 30_000;

/** Minimum elapsed seconds required before sending a time report on stop. */
const MIN_REPORT_SECONDS = 5;

(function () {
  'use strict';

  /** IDs of elements that currently carry an overlay class */
  let overlayElements = [];

  // ─── Reading-time tracking ────────────────────────────────────────────────

  let trackingActive = false;
  let pageVisible    = !document.hidden;
  let segmentStart   = Date.now();   // start of the current visible segment
  let reportTimer    = null;
  let interactionAt  = Date.now();

  // Treat reading as engaged for 45s after user interaction; this avoids
  // counting long idle stretches (open tab, no activity) as focus time.
  const INTERACTION_ENGAGEMENT_WINDOW_MS = 45_000;
  const MAX_REPORT_SECONDS = 90;

  function markInteraction() {
    interactionAt = Date.now();
  }

  function engagedSecondsWithin(elapsedSeconds) {
    if (elapsedSeconds <= 0) return 0;
    const now = Date.now();
    const periodStartMs = now - (elapsedSeconds * 1000);
    const engagementStartMs = interactionAt;
    const engagementEndMs = interactionAt + INTERACTION_ENGAGEMENT_WINDOW_MS;
    const overlapStart = Math.max(periodStartMs, engagementStartMs);
    const overlapEnd = Math.min(now, engagementEndMs);
    const engagementMs = Math.max(0, overlapEnd - overlapStart);
    return Math.round(engagementMs / 1000);
  }

  function startTimer() {
    if (reportTimer) return;
    segmentStart = Date.now();
    reportTimer  = setInterval(reportTime, REPORT_INTERVAL_MS);
  }

  function elapsedSecondsSinceSegmentStart() {
    return Math.min(Math.round((Date.now() - segmentStart) / 1000), MAX_REPORT_SECONDS);
  }

  function stopTimer() {
    if (reportTimer) {
      clearInterval(reportTimer);
      reportTimer = null;
    }
    // Report any remaining seconds before stopping.
    const elapsed = elapsedSecondsSinceSegmentStart();
    if (elapsed >= MIN_REPORT_SECONDS) {
      const engaged = engagedSecondsWithin(elapsed);
      if (engaged >= MIN_REPORT_SECONDS) {
        chrome.runtime.sendMessage({
          type: 'READING_TIME_UPDATE',
          seconds: elapsed,
          engagedSeconds: engaged,
        });
      }
    }
    segmentStart = Date.now();
  }

  function reportTime() {
    const elapsed = elapsedSecondsSinceSegmentStart();
    segmentStart  = Date.now();
    if (elapsed > 0) {
      const engaged = engagedSecondsWithin(elapsed);
      if (engaged > 0) {
        chrome.runtime.sendMessage({
          type: 'READING_TIME_UPDATE',
          seconds: elapsed,
          engagedSeconds: engaged,
        });
      }
    }
  }

  function updateTimerState() {
    if (trackingActive && pageVisible) {
      startTimer();
    } else {
      stopTimer();
    }
  }

  document.addEventListener('visibilitychange', () => {
    pageVisible = !document.hidden;
    updateTimerState();
  });
  window.addEventListener('scroll', markInteraction, { passive: true });
  window.addEventListener('mousemove', markInteraction, { passive: true });
  window.addEventListener('keydown', markInteraction, { passive: true });
  window.addEventListener('touchstart', markInteraction, { passive: true });

  // ─── Messaging ────────────────────────────────────────────────────────────

  chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
    switch (msg.type) {
      case 'TRACKING_STATE':
        trackingActive = msg.enabled;
        if (msg.enabled) {
          runAnalysis();
        } else {
          clearOverlay();
        }
        updateTimerState();
        sendResponse({ ok: true });
        break;

      case 'APPLY_SCORES':
        applyHeatmap(msg.blocks);
        sendResponse({ ok: true });
        break;

      case 'CLEAR_OVERLAY':
        clearOverlay();
        sendResponse({ ok: true });
        break;

      case 'GET_PAGE_TEXT': {
        const blocks = extractBlocks();
        sendResponse({ blocks });
        break;
      }

      case 'SHOW_BREAK_POPUP':
        showBreakPopup();
        sendResponse({ ok: true });
        break;
    }
    // Return true keeps the channel open for async responses.
    return true;
  });

  // ─── Bootstrap ────────────────────────────────────────────────────────────

  chrome.storage.local.get(['trackingEnabled'], (result) => {
    trackingActive = result.trackingEnabled === true;
    if (trackingActive) {
      runAnalysis();
      updateTimerState();
    }
  });

  chrome.storage.onChanged.addListener((changes) => {
    if ('trackingEnabled' in changes) {
      trackingActive = changes.trackingEnabled.newValue === true;
      if (trackingActive) {
        runAnalysis();
      } else {
        clearOverlay();
      }
      updateTimerState();
    }
  });

  // ─── Block Extraction ─────────────────────────────────────────────────────

  /**
   * Walk the DOM and collect meaningful text blocks.
   * Returns an array of { id, text, domPath, position, tagName }.
   */
  function extractBlocks() {
    const blocks = [];
    let idx = 0;
    const seen = new WeakSet();

    const SELECTORS = [
      'p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
      'li', 'blockquote', 'td', 'th', 'figcaption',
    ];

    for (const sel of SELECTORS) {
      document.querySelectorAll(sel).forEach((el) => {
        if (seen.has(el)) return;
        if (isNonContent(el)) return;

        const text = (el.innerText || '').trim();
        if (text.length < MIN_BLOCK_LENGTH) return; // skip trivially short nodes

        seen.add(el);

        const id = `focusos-block-${idx}`;
        el.dataset.focusosId = id;

        blocks.push({
          id,
          text: text.slice(0, MAX_BLOCK_TEXT_LENGTH), // cap per block to keep payloads small
          domPath: buildDomPath(el),
          position: idx,
          tagName: el.tagName.toLowerCase(),
        });

        idx += 1;
      });
    }

    return blocks;
  }

  /**
   * Returns true if an element is inside a non-content region
   * (nav, header, footer, aside, hidden elements, etc.).
   */
  function isNonContent(el) {
    const SKIP_TAGS = new Set([
      'NAV', 'HEADER', 'FOOTER', 'ASIDE',
      'SCRIPT', 'STYLE', 'NOSCRIPT', 'IFRAME',
    ]);

    let node = el;
    while (node && node !== document.body) {
      if (SKIP_TAGS.has(node.tagName)) return true;
      if (node.getAttribute && node.getAttribute('aria-hidden') === 'true') return true;
      const style = window.getComputedStyle(node);
      if (style.display === 'none' || style.visibility === 'hidden') return true;
      node = node.parentElement;
    }
    return false;
  }

  /** Build a short CSS-style path to an element for debugging / DOM sync. */
  function buildDomPath(el) {
    const parts = [];
    let node = el;

    while (node && node !== document.body) {
      let seg = node.tagName.toLowerCase();

      if (node.id) {
        seg += `#${node.id}`;
        parts.unshift(seg);
        break;
      }

      const siblings = Array.from(node.parentElement?.children ?? []);
      const sameTag = siblings.filter((s) => s.tagName === node.tagName);
      if (sameTag.length > 1) {
        seg += `:nth-of-type(${sameTag.indexOf(node) + 1})`;
      }

      parts.unshift(seg);
      node = node.parentElement;
    }

    return parts.join(' > ');
  }

  // ─── Analysis Trigger ─────────────────────────────────────────────────────

  function runAnalysis() {
    const blocks = extractBlocks();
    if (blocks.length === 0) return;

    chrome.runtime.sendMessage({
      type: 'ANALYZE_PAGE',
      url: window.location.href,
      blocks,
    });
  }

  // ─── Heatmap Overlay ──────────────────────────────────────────────────────

  /**
   * Apply color overlays to blocks based on their load score.
   * @param {Array<{id: string, load: number}>} scoredBlocks
   */
  function applyHeatmap(scoredBlocks) {
    clearOverlay();

    scoredBlocks.forEach((block) => {
      const el = document.querySelector(`[data-focusos-id="${block.id}"]`);
      if (!el) return;

      el.classList.add('focusos-overlay', loadClass(block.load));
      overlayElements.push(el);
    });
  }

  /** Map a 0-1 load score to a CSS class name. */
  function loadClass(load) {
    if (load < 0.33) return 'focusos-load-low';
    if (load < 0.66) return 'focusos-load-medium';
    return 'focusos-load-high';
  }

  /** Remove all overlay classes and clean up data attributes. */
  function clearOverlay() {
    const CLASSES = ['focusos-overlay', 'focusos-load-low', 'focusos-load-medium', 'focusos-load-high'];

    overlayElements.forEach((el) => {
      el.classList.remove(...CLASSES);
      delete el.dataset.focusosId;
    });
    overlayElements = [];

    // Belt-and-suspenders: clean any stranded attributes
    document.querySelectorAll('[data-focusos-id]').forEach((el) => {
      el.classList.remove(...CLASSES);
      delete el.dataset.focusosId;
    });
  }

  // ─── Brain Break Popup ────────────────────────────────────────────────────

  function showBreakPopup() {
    hideBreakPopup(); // remove any existing popup first

    const popup = document.createElement('div');
    popup.id = 'focusos-break-popup';
    popup.setAttribute('role', 'alert');
    popup.setAttribute('aria-live', 'assertive');

    popup.innerHTML = `
      <div class="focusos-break-inner">
        <span class="focusos-break-icon" aria-hidden="true">🧠</span>
        <div class="focusos-break-body">
          <strong class="focusos-break-title">Time for a brain break!</strong>
          <p class="focusos-break-msg">Consider a short rest before your next deep read.</p>
        </div>
        <button class="focusos-break-dismiss" aria-label="Dismiss break reminder" title="Dismiss">✕</button>
      </div>
    `;

    document.body.appendChild(popup);

    popup.querySelector('.focusos-break-dismiss').addEventListener('click', () => {
      hideBreakPopup();
      chrome.runtime.sendMessage({ type: 'BREAK_DISMISSED' });
    });

    // Auto-dismiss after 8 seconds.
    setTimeout(() => hideBreakPopup(), 8000);
  }

  function hideBreakPopup() {
    const existing = document.getElementById('focusos-break-popup');
    if (existing) existing.remove();
  }
})();
