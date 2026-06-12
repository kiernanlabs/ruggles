/* Mini Art Salon · v2 shared UI — the jury-comparison modal, the image
 * lightbox, and the mini percentile-range bar. Used by index.html,
 * gallery.html, and piece.html.
 *
 * Everything here is intentionally anonymous: pieces are never shown with a
 * title and never link out to any original source. A piece is identified by its
 * placement within a comparison group and its overall percentile in the salon —
 * never by an overall leaderboard rank (e.g. "rank 137 of 824").
 *
 * Exposes globals (classic script, no modules):
 *   apiJson(url, opts)            – fetch + JSON, throwing the backend error
 *   escapeHtml(s) / esc(s)        – HTML-escape text
 *   rangeBar(lo, hi, you, avg)    – mini 0–100 percentile-range bar markup
 *   openLightbox(src, captionHtml) / closeLightbox()
 *   openComparison(url, opts)     – fetch a comparison and render the modal
 *   closeComparison()
 *   ordinal(n)                    – 1 -> "1st", 23 -> "23rd"
 *
 * openComparison handles BOTH endpoint shapes identically:
 *   - pool comparisons   GET /pools/<pool>/comparisons/<id>
 *   - submission rounds  GET /submissions/<job>/comparisons/<round>
 * opts: { pool, currentPieceId, fetcher } — `pool` builds the per-piece links;
 * a member whose piece_id === currentPieceId is marked "you're viewing this
 * piece"; candidate members (is_candidate) get no link. `fetcher(url)` overrides
 * how the comparison is loaded (e.g. an authed fetch for private salon rounds).
 */
(function () {
  "use strict";

  function apiJson(url, opts) {
    return fetch(url, opts).then(async (r) => {
      let data = {};
      try { data = await r.json(); } catch (_) { /* empty/non-JSON */ }
      if (!r.ok || data.error) throw new Error(data.error || (r.status + " " + r.statusText));
      return data;
    });
  }

  function escapeHtml(s) {
    return String(s == null ? "" : s).replace(/[&<>"]/g, (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  }

  function ordinal(n) {
    if (n == null) return "—";
    const s = ["th", "st", "nd", "rd"], v = n % 100;
    return n + (s[(v - 20) % 10] || s[v] || s[0]);
  }

  function rangeBar(lo, hi, you, avg) {
    if (lo == null || hi == null) {
      return '<span class="sb-range-txt">' + (avg != null ? "avg " + avg : "—") + "</span>";
    }
    const clamp = (v) => Math.min(100, Math.max(0, v));
    const band = "left:" + clamp(lo) + "%;width:" + Math.max(clamp(hi) - clamp(lo), 2) + "%";
    const mark = you != null ? '<span class="you" style="left:' + clamp(you) + '%"></span>' : "";
    const tip = "pieces span " + lo + "–" + hi + "th percentile" + (you != null ? "; this piece " + you + "th" : "");
    return '<span class="sb-rng" title="' + tip + '"><span class="sb-range-txt">' + lo + "–" + hi + "</span>"
      + '<span class="sb-pbar"><span class="band" style="' + band + '"></span>' + mark + "</span></span>";
  }

  // ── image lightbox ─────────────────────────────────────────────────────────
  let lb = null;
  function ensureLb() {
    if (lb) return;
    lb = document.createElement("div");
    lb.className = "sb-lightbox";
    lb.innerHTML = '<span class="close" title="Close (Esc)">&times;</span><img alt=""><div class="cap"></div>';
    document.body.appendChild(lb);
    lb.querySelector(".close").addEventListener("click", closeLightbox);
    lb.addEventListener("click", (e) => { if (e.target === lb) closeLightbox(); });
  }
  function openLightbox(src, captionHtml) {
    if (!src) return;
    ensureLb();
    lb.querySelector("img").src = src;
    lb.querySelector(".cap").innerHTML = captionHtml || "";
    lb.classList.add("open");
  }
  function closeLightbox() {
    if (lb) { lb.classList.remove("open"); lb.querySelector("img").src = ""; }
  }

  // ── comparison modal ───────────────────────────────────────────────────────
  let ov = null, box = null;
  function ensureOv() {
    if (ov) return;
    ov = document.createElement("div");
    ov.className = "sb-overlay";
    box = document.createElement("div");
    box.className = "sb-box";
    ov.appendChild(box);
    document.body.appendChild(ov);
    ov.addEventListener("click", (e) => { if (e.target === ov) closeComparison(); });
  }
  function closeComparison() { if (ov) ov.classList.remove("open"); }

  // A single piece inside a comparison group. No title, no source link — a piece
  // It shows the piece's placement WITHIN this group of five and its overall
  // percentile in the salon — but never an overall leaderboard rank.
  function memberHtml(m, pool, cur) {
    const here = cur && m.piece_id === cur;
    let cls = "sb-mem";
    if (m.is_candidate) cls += " candidate";
    if (here) cls += " current";
    if (m.rank_in_set === 1) cls += " winner";
    const standing = m.is_candidate
      ? ' · <span class="sb-tag">your piece</span>'
      : (m.overall_percentile != null
          ? " · " + ordinal(m.overall_percentile) + " percentile in the salon"
          : "");
    let link = "";
    if (m.is_candidate) link = "";
    else if (here) link = '<span class="sb-tag">you’re viewing this piece</span>';
    else if (pool && m.piece_id)
      link = '<a href="piece.html?id=' + encodeURIComponent(m.piece_id)
           + '">see where it landed ›</a>';
    return '<div class="' + cls + '">'
      + '<img src="' + (m.image_url || "") + '" alt="" loading="lazy">'
      + "<div>"
      + '<div class="rk"><b>' + (m.rank_in_set ? "#" + m.rank_in_set + " in this group" : "set aside this round") + "</b>" + standing + "</div>"
      + (m.rationale ? '<div class="rat">"' + escapeHtml(m.rationale) + '"</div>' : "")
      + link
      + "</div></div>";
  }

  function memberCaption(m) {
    const who = m.is_candidate
      ? '<b>your piece</b>'
      : '<b>a salon piece</b>';
    return who
      + (m.rank_in_set ? " · #" + m.rank_in_set + " in group" : " · set aside this round")
      + (!m.is_candidate && m.overall_percentile != null
          ? ' · <span class="pct">' + ordinal(m.overall_percentile) + "</span> percentile in the salon" : "")
      + (m.rationale ? '<div class="rat">"' + escapeHtml(m.rationale) + '"</div>' : "");
  }

  function comparisonHtml(c, opts) {
    const id = c.comparison_id;
    const heading = c.phase
      ? "Round " + id + ' <span class="sb-muted">· ' + escapeHtml(c.phase) + " phase</span>"
      : "Comparison #" + id;
    const mp = c.members.map((m) => m.overall_percentile).filter((v) => v != null);
    const span = mp.length
      ? "group spans " + Math.min.apply(null, mp) + "–" + Math.max.apply(null, mp) + "th percentile"
      : "percentiles unavailable";
    return '<button class="close sb-x">×</button>'
      + '<h2 class="sb-h2">' + heading + "</h2>"
      + '<div class="sb-muted">' + c.members.length + " pieces · " + span + "</div>"
      + (c.overall_rationale ? '<div class="sb-overall"><b>What the jury said:</b><br>' + escapeHtml(c.overall_rationale) + "</div>" : "")
      + c.members.map((m) => memberHtml(m, opts.pool, opts.currentPieceId)).join("");
  }

  // Cache comparison detail by URL so pages can prefetch a piece's own rationale
  // for the summary list and the "details" modal then opens instantly.
  const _cmpCache = {};
  function fetchComparison(url) {
    if (!_cmpCache[url]) _cmpCache[url] = apiJson(url);
    return _cmpCache[url];
  }

  async function openComparison(url, opts) {
    opts = opts || {};
    ensureOv();
    box.innerHTML = '<button class="close sb-x">×</button><p class="sb-muted">Loading round…</p>';
    ov.classList.add("open");
    box.querySelector(".sb-x").addEventListener("click", closeComparison);
    try {
      const c = await (opts.fetcher ? opts.fetcher(url) : fetchComparison(url));
      box.innerHTML = comparisonHtml(c, opts);
      box.querySelector(".sb-x").addEventListener("click", closeComparison);
      box.querySelectorAll(".sb-mem img").forEach((img, i) => {
        const m = c.members[i];
        img.addEventListener("click", () => openLightbox(m.image_url, memberCaption(m)));
      });
    } catch (e) {
      box.innerHTML = '<button class="close sb-x">×</button><p class="sb-err">' + escapeHtml(e.message) + "</p>";
      box.querySelector(".sb-x").addEventListener("click", closeComparison);
    }
  }

  // Esc closes the lightbox first (top layer), then the comparison modal.
  document.addEventListener("keydown", (e) => {
    if (e.key !== "Escape") return;
    if (lb && lb.classList.contains("open")) closeLightbox();
    else if (ov && ov.classList.contains("open")) closeComparison();
  });

  window.apiJson = apiJson;
  window.fetchComparison = fetchComparison;
  window.escapeHtml = escapeHtml;
  window.esc = escapeHtml;
  window.ordinal = ordinal;
  window.rangeBar = rangeBar;
  window.openLightbox = openLightbox;
  window.closeLightbox = closeLightbox;
  window.openComparison = openComparison;
  window.closeComparison = closeComparison;
})();
