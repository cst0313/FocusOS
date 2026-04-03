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
 *
 * All processing is local – no data leaves the machine.
 */

/** Minimum character count for a block to be considered worth analysing. */
const MIN_BLOCK_LENGTH = 20;

/** Maximum characters sent per block to keep API payloads reasonable. */
const MAX_BLOCK_TEXT_LENGTH = 600;

(function () {
  'use strict';

  /** IDs of elements that currently carry an overlay class */
  let overlayElements = [];

  // ─── Messaging ────────────────────────────────────────────────────────────

  chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
    switch (msg.type) {
      case 'TRACKING_STATE':
        if (msg.enabled) {
          runAnalysis();
        } else {
          clearOverlay();
        }
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
    }
    // Return true keeps the channel open for async responses.
    return true;
  });

  // ─── Bootstrap ────────────────────────────────────────────────────────────

  chrome.storage.local.get(['trackingEnabled'], (result) => {
    if (result.trackingEnabled === true) {
      runAnalysis();
    }
  });

  chrome.storage.onChanged.addListener((changes) => {
    if ('trackingEnabled' in changes) {
      if (changes.trackingEnabled.newValue === true) {
        runAnalysis();
      } else {
        clearOverlay();
      }
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
})();
