/* rirdb website: small progressive enhancements. Every page works without this script (tables are complete,
   charts are SVG); it adds theme switching, sorting, filtering, copy buttons and chart tooltips.

   Conventions used by the templates:
     table.tbl th[data-sort="num"|"text"]     sortable column; a cell's data-v overrides its text
     input[data-filter="#table"]               free-text filter (row data-search, else row text)
     select[data-filter="#table"][data-key=k]  keeps rows whose data-k (comma list) contains the value;
                                               data-op="gte"|"lte" compares numbers instead
     [data-count-for="#table"]                 "N of M rows" after filtering
     button[data-copy="#el"] / [data-copy-text] copies text
     [data-tip]                                tooltip text (SVG marks and HTML)
*/
(function () {
  "use strict";
  var doc = document, root = doc.documentElement;

  // ---------- theme
  function store(k, v) { try { if (v === undefined) return localStorage.getItem(k); localStorage.setItem(k, v); } catch (e) { return null; } }
  var saved = store("rirdb-theme");
  if (saved === "light" || saved === "dark") root.setAttribute("data-theme", saved);
  doc.addEventListener("click", function (e) {
    var b = e.target.closest(".theme-btn");
    if (!b) return;
    var dark = root.getAttribute("data-theme") === "dark" ||
      (!root.getAttribute("data-theme") && window.matchMedia("(prefers-color-scheme: dark)").matches);
    var next = dark ? "light" : "dark";
    root.setAttribute("data-theme", next);
    store("rirdb-theme", next);
  });

  // ---------- sorting
  function cellVal(td, kind) {
    if (!td) return kind === "num" ? -Infinity : "";
    var v = td.getAttribute("data-v");
    if (v === null) v = td.textContent.trim();
    if (kind === "num") {
      var f = parseFloat(String(v).replace(/[,\s%]/g, "").replace("−", "-"));
      return isNaN(f) ? -Infinity : f;
    }
    return String(v).toLowerCase();
  }
  doc.addEventListener("click", function (e) {
    var th = e.target.closest("th[data-sort]");
    if (!th) return;
    var table = th.closest("table"), tbody = table.tBodies[0];
    var idx = Array.prototype.indexOf.call(th.parentNode.children, th);
    var kind = th.getAttribute("data-sort");
    var asc = th.getAttribute("aria-sort") !== "ascending";
    if (th.getAttribute("aria-sort") === null && kind === "num") asc = false;
    Array.prototype.forEach.call(th.parentNode.children, function (h) { h.removeAttribute("aria-sort"); });
    th.setAttribute("aria-sort", asc ? "ascending" : "descending");
    var rows = Array.prototype.slice.call(tbody.rows);
    rows.sort(function (a, b) {
      var x = cellVal(a.cells[idx], kind), y = cellVal(b.cells[idx], kind);
      if (x === y) return 0;
      return (x < y ? -1 : 1) * (asc ? 1 : -1);
    });
    rows.forEach(function (r) { tbody.appendChild(r); });
  });
  doc.addEventListener("keydown", function (e) {
    var th = e.target.closest && e.target.closest("th[data-sort]");
    if (th && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); th.click(); }
  });
  Array.prototype.forEach.call(doc.querySelectorAll("th[data-sort]"), function (th) { th.tabIndex = 0; });

  // ---------- filtering
  function applyFilters(sel) {
    var table = doc.querySelector(sel);
    if (!table) return;
    var ctrls = doc.querySelectorAll('[data-filter="' + sel + '"]');
    var rows = table.tBodies[0].rows, shown = 0;
    for (var i = 0; i < rows.length; i++) {
      var r = rows[i], ok = true;
      for (var j = 0; j < ctrls.length && ok; j++) {
        var c = ctrls[j], v = (c.value || "").trim();
        if (!v) continue;
        var key = c.getAttribute("data-key");
        if (!key) {
          var hay = (r.getAttribute("data-search") || r.textContent).toLowerCase();
          ok = v.toLowerCase().split(/\s+/).every(function (t) { return hay.indexOf(t) !== -1; });
        } else {
          var have = r.getAttribute("data-" + key) || "";
          var op = c.getAttribute("data-op");
          if (op === "gte" || op === "lte") {
            var num = parseFloat(have), lim = parseFloat(v);
            ok = !isNaN(num) && (op === "gte" ? num >= lim : num <= lim);
          } else {
            ok = ("," + have + ",").indexOf("," + v + ",") !== -1;
          }
        }
      }
      r.hidden = !ok;
      if (ok) shown++;
    }
    Array.prototype.forEach.call(doc.querySelectorAll('[data-count-for="' + sel + '"]'), function (el) {
      el.textContent = shown === rows.length ? rows.length.toLocaleString("en-GB") + " rows"
        : shown.toLocaleString("en-GB") + " of " + rows.length.toLocaleString("en-GB") + " rows";
    });
  }
  doc.addEventListener("input", function (e) { var s = e.target.getAttribute && e.target.getAttribute("data-filter"); if (s) applyFilters(s); });
  doc.addEventListener("change", function (e) { var s = e.target.getAttribute && e.target.getAttribute("data-filter"); if (s) applyFilters(s); });
  // pre-fill a search box from ?q= (the home page search posts here)
  var q = new URLSearchParams(location.search).get("q");
  var seen = {};
  Array.prototype.forEach.call(doc.querySelectorAll("[data-filter]"), function (c) {
    var s = c.getAttribute("data-filter");
    if (q && c.type === "search" && !c.value) c.value = q;
    if (!seen[s]) { seen[s] = 1; applyFilters(s); }
  });

  // ---------- copy
  doc.addEventListener("click", function (e) {
    var b = e.target.closest("[data-copy], [data-copy-text]");
    if (!b) return;
    var text = b.getAttribute("data-copy-text");
    if (text === null) { var el = doc.querySelector(b.getAttribute("data-copy")); text = el ? el.textContent : ""; }
    var done = function () { var t = b.textContent; b.textContent = "Copied"; setTimeout(function () { b.textContent = t; }, 1400); };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(done, function () { fallback(text); done(); });
    } else { fallback(text); done(); }
  });
  function fallback(text) {
    var ta = doc.createElement("textarea"); ta.value = text; ta.style.position = "fixed"; ta.style.opacity = "0";
    doc.body.appendChild(ta); ta.select(); try { doc.execCommand("copy"); } catch (e) {} doc.body.removeChild(ta);
  }

  // ---------- tooltip
  var tip = doc.getElementById("tip");
  if (!tip) { tip = doc.createElement("div"); tip.id = "tip"; tip.setAttribute("role", "tooltip"); tip.hidden = true; doc.body.appendChild(tip); }
  function show(el, x, y) {
    tip.textContent = el.getAttribute("data-tip");
    tip.hidden = false;
    var w = tip.offsetWidth, h = tip.offsetHeight, vw = window.innerWidth;
    var left = Math.min(Math.max(8, x + 12), vw - w - 8), top = y - h - 12;
    if (top < 8) top = y + 16;
    tip.style.left = left + "px"; tip.style.top = top + "px";
  }
  doc.addEventListener("pointermove", function (e) {
    var el = e.target.closest && e.target.closest("[data-tip]");
    if (el) show(el, e.clientX, e.clientY); else tip.hidden = true;
  });
  doc.addEventListener("focusin", function (e) {
    var el = e.target.closest && e.target.closest("[data-tip]");
    if (!el) return;
    var r = el.getBoundingClientRect(); show(el, r.left + r.width / 2, r.top);
  });
  doc.addEventListener("focusout", function () { tip.hidden = true; });
  doc.addEventListener("scroll", function () { tip.hidden = true; }, { passive: true });
})();
