// The website's only script. Every page works without it; it adds:
//   1. tooltips on the charts (from each mark's <title>), on hover and keyboard focus;
//   2. Copy buttons, and the Filters button on a phone;
//   3. the search of the datasets and rooms pages, over window.RIRDB (search-data.js, written by data.py).
//      Its filters are the options of `rirdb search`, so the page prints the command that selects the same IRs,
//      and they live in the address, so a link reopens the same search.
(function () {
  "use strict";
  const $ = (s, el) => (el || document).querySelector(s);
  const $$ = (s, el) => Array.from((el || document).querySelectorAll(s));
  const fmt = (n) => Number(n).toLocaleString("en");

  // ------------------------------------------------------------------ 1. tooltips
  const tip = document.createElement("div");
  tip.className = "tip"; tip.hidden = true; document.body.appendChild(tip);
  function place(x, y) {
    tip.style.left = Math.max(8, Math.min(x + 12, innerWidth - tip.offsetWidth - 8)) + "px";
    const above = y - tip.offsetHeight - 10;
    tip.style.top = (above > 8 ? above : y + 18) + "px";
  }
  $$("svg .hit, svg .pt").forEach(function (mark) {
    const title = mark.querySelector("title");
    if (!title) return;
    const text = title.textContent;
    title.remove();                                  // else the browser shows its own tooltip as well
    mark.setAttribute("aria-label", text);
    const show = function (x, y) { tip.textContent = text; tip.hidden = false; place(x, y); };
    mark.addEventListener("pointermove", function (ev) { show(ev.clientX, ev.clientY); });
    mark.addEventListener("pointerleave", function () { tip.hidden = true; });
    mark.addEventListener("focus", function () { const r = mark.getBoundingClientRect(); show(r.left + r.width / 2, r.top); });
    mark.addEventListener("blur", function () { tip.hidden = true; });
  });

  // ------------------------------------------------------------------ 2. copy buttons, the filters button
  document.addEventListener("click", function (ev) {
    const b = ev.target.closest("[data-copy]");
    if (!b) return;
    const pre = b.parentElement.querySelector("pre");
    const done = function () { b.textContent = "Copied"; setTimeout(function () { b.textContent = "Copy"; }, 1500); };
    const select = function () { const r = document.createRange(); r.selectNodeContents(pre);
      const s = getSelection(); s.removeAllRanges(); s.addRange(r); };
    if (navigator.clipboard) navigator.clipboard.writeText(pre.textContent).then(done, select); else select();
  });
  $$(".filterbtn").forEach(function (b) {
    b.addEventListener("click", function () {
      const open = $("#" + b.getAttribute("aria-controls")).classList.toggle("open");
      b.setAttribute("aria-expanded", open);
    });
  });

  // ------------------------------------------------------------------ 3. the search
  const form = $("#filters");
  const D = window.RIRDB;
  if (!form || !D) return;
  const roomsPage = !!$("#rooms");
  const col = {};
  D.columns.forEach(function (c, i) { col[c] = i; });
  const EDGES = D.t30_edges;
  // The reverberation-time ranges of the filter (words.REVERB): [low, high), high null = no limit.
  const REVERB = { dry: [0, 0.315], room: [0.315, 0.63], class: [0.63, 1.25], hall: [1.25, 2.5], live: [2.5, null] };
  const MULTI = ["provides", "type", "t30"];
  const IR_LEVEL = ["format", "use", "grade", "t30", "type", "origin"];   // filters read on each IR

  // The rows of counts of each room, and the rooms of each dataset.
  const bySpace = D.spaces.map(function () { return []; });
  D.counts.forEach(function (r) { bySpace[r[col.space]].push(r); });
  const spacesOf = D.datasets.map(function () { return []; });
  D.spaces.forEach(function (s, i) { spacesOf[s.d].push(i); });
  const roomText = roomsPage ? $$("#rooms .roomrow").map(function (li) { return li.textContent.toLowerCase(); }) : [];

  function read() {
    const st = { text: "", sort: "" };
    MULTI.forEach(function (k) { st[k] = []; });
    new FormData(form).forEach(function (v, k) {
      v = String(v).trim();
      if (MULTI.includes(k)) { if (v) st[k].push(v); } else st[k] = v;
    });
    st.text = (st.text || "").toLowerCase();
    return st;
  }
  function write(params) {                      // set the inputs from the address
    params.forEach(function (v, k) {
      $$('[name="' + k + '"]').forEach(function (el) {
        if (el.type === "radio" || el.type === "checkbox") { if (el.value === v) el.checked = true; }
        else el.value = v;
      });
    });
  }
  const isSet = function (st) {
    return IR_LEVEL.concat(["provides", "status"]).some(function (k) { return MULTI.includes(k) ? st[k].length : st[k]; });
  };

  function binsOf(keys) {                       // the T30 bins inside the chosen ranges
    const bins = new Set();
    keys.forEach(function (k) {
      const r = REVERB[k];
      for (let b = 0; b < EDGES.length - 1; b++) {
        if (EDGES[b] >= r[0] - 1e-9 && (r[1] === null || EDGES[b + 1] <= r[1] + 1e-9)) bins.add(b);
      }
    });
    return bins;
  }
  // One row of counts, under the filters read on each IR (as `rirdb search`).
  function rowKept(r, st, bins) {
    if (!st.format) { if (!r[col.primary]) return false; }
    else if (st.format === "mono") { if (r[col.format] !== "mono" && !r[col.mono_w]) return false; }
    else if (r[col.format] !== st.format) return false;
    if (st.grade) { const g = D.grades.indexOf(r[col.grade]); if (g < 0 || g > D.grades.indexOf(st.grade)) return false; }
    if (st.use && !r[col[st.use]]) return false;
    if (bins && !bins.has(r[col.t30])) return false;
    return true;
  }
  function spaceKept(s, st) {
    if (st.type.length && !st.type.includes(s.type)) return false;
    if (st.origin && s.origin !== st.origin) return false;
    return true;
  }
  function datasetKept(d, st) {                 // the filters on the dataset itself
    if (st.provides.some(function (p) { return !d.provides.includes(p); })) return false;
    if (st.status === "measured" && d.status !== "measured") return false;
    if (st.status === "not" && d.status === "measured") return false;
    return true;
  }
  function textOk(d, st) {
    return !st.text || st.text.split(/\s+/).every(function (w) { return d.text.indexOf(w) >= 0; });
  }
  // IRs kept in each room: an array of counts, one per room (0: left out).
  function irsPerSpace(st) {
    const bins = st.t30.length ? binsOf(st.t30) : null;
    return D.spaces.map(function (s, i) {
      const d = D.datasets[s.d];
      if (!datasetKept(d, st) || !spaceKept(s, st)) return 0;
      if (roomsPage ? (st.text && !st.text.split(/\s+/).every(function (w) { return roomText[i].indexOf(w) >= 0; }))
                    : !textOk(d, st)) return 0;
      let n = 0;
      bySpace[i].forEach(function (r) { if (rowKept(r, st, bins)) n += r[col.n]; });
      return n;
    });
  }
  // The datasets kept: measured ones by their IRs, the others by what their pages state.
  function datasetsKept(st, per) {
    const measuredOnly = st.grade || st.t30.length || st.type.length;
    return D.datasets.map(function (d, i) {
      if (d.status === "measured") return spacesOf[i].reduce(function (a, s) { return a + per[s]; }, 0);
      if (!datasetKept(d, st) || !textOk(d, st) || measuredOnly) return 0;
      if (st.format && !d.formats.includes(st.format)) return 0;
      if (st.use && d.uses[st.use] !== "yes") return 0;
      if (st.origin && d.origin !== st.origin) return 0;
      return -1;                                 // kept, no IRs counted yet
    });
  }

  function command(st, keptIds) {
    const parts = ["rirdb search"], notes = [];
    if (st.format) parts.push("--format " + st.format);
    if (st.use) parts.push("--use " + st.use);
    if (st.origin) parts.push("--origin " + st.origin);
    if (st.grade) parts.push("--grade " + st.grade);
    st.type.forEach(function (t) { parts.push("--space-type " + t); });
    if (st.t30.length) {
      const order = Object.keys(REVERB).filter(function (k) { return st.t30.includes(k); });
      const contiguous = order.every(function (k, i) { return !i || REVERB[order[i - 1]][1] === REVERB[k][0]; });
      if (contiguous) {
        const lo = REVERB[order[0]][0], hi = REVERB[order[order.length - 1]][1];
        parts.push("--t30 " + lo + "-" + (hi === null ? "" : hi));
      } else notes.push("The command line takes one reverberation-time range: run it once per range.");
    }
    st.provides.forEach(function (p) { parts.push("--provides " + p); });
    if (st.text) {
      if (keptIds.length && keptIds.length <= 12) keptIds.forEach(function (i) { parts.push("--dataset " + i); });
      else notes.push("The text search is this page's own; the command lists the IRs of every dataset its other filters keep.");
    }
    return { line: parts.join(" "), notes: notes };
  }

  // Each option's count: what the results would be with that option chosen.
  function optionCounts(st) {
    $$(".opt input", form).forEach(function (input) {
      const k = input.name, v = input.value, alt = JSON.parse(JSON.stringify(st));
      if (MULTI.includes(k)) { if (!alt[k].includes(v)) alt[k].push(v); } else alt[k] = v;
      const per = irsPerSpace(alt);
      const n = roomsPage ? per.filter(Boolean).length : datasetsKept(alt, per).filter(Boolean).length;
      const c = input.parentElement.querySelector(".c");
      if (c) c.textContent = fmt(n);
      input.parentElement.classList.toggle("zero", !n && !input.checked);
    });
  }

  function chips(st) {
    const box = $("#chips");
    box.innerHTML = "";
    const add = function (label, clear) {
      const b = document.createElement("button");
      b.type = "button"; b.className = "chip"; b.textContent = label + " ×";
      b.setAttribute("aria-label", "Remove " + label);
      b.addEventListener("click", function () { clear(); run(); });
      box.appendChild(b);
    };
    if (st.text) add("“" + st.text + "”", function () { $("#text").value = ""; });
    $$(".opt input:checked", form).forEach(function (input) {
      if (!input.value) return;
      const legend = input.closest("fieldset").querySelector("legend").firstChild.textContent.trim();
      const label = input.parentElement.querySelector("span").textContent;
      add(MULTI.includes(input.name) && input.name !== "provides" ? label : legend + ": " + label, function () {
        if (input.type === "checkbox") input.checked = false;
        else form.querySelector('[name="' + input.name + '"][value=""]').checked = true;
      });
    });
  }

  let shown = 0;
  const PER = roomsPage ? 50 : 30;
  function show(items, keep) {                    // items in order; keep(i) tells whether item i is shown
    const kept = items.filter(keep);
    items.forEach(function (el) { el.hidden = true; });
    kept.slice(0, shown).forEach(function (el) { el.hidden = false; });
    const more = $("#more"), left = kept.length - shown;
    more.hidden = left <= 0;
    more.textContent = "Show " + Math.min(PER, left) + " more (" + fmt(left) + " left)";
    return kept.length;
  }

  function run(resetPage) {
    const st = read();
    if (resetPage !== false) shown = PER;
    const params = new URLSearchParams();
    Object.keys(st).forEach(function (k) {
      (MULTI.includes(k) ? st[k] : [st[k]]).forEach(function (v) { if (v) params.append(k, v); });
    });
    try { history.replaceState(null, "", params.toString() ? "?" + params : location.pathname); } catch (err) { /* file: */ }
    const per = irsPerSpace(st);
    const result = $("#result");
    let keptIds = [];
    if (roomsPage) {
      const rows = $$("#rooms .roomrow");
      const n = show(rows, function (li) { return per[+li.dataset.i] > 0; });
      rows.forEach(function (li) { const c = li.querySelector(".count"); if (c) c.textContent = fmt(per[+li.dataset.i]) + " IRs"; });
      $$("svg .pt").forEach(function (pt) { pt.classList.toggle("dim", !per[+pt.dataset.i]); });
      const ds = new Set(), irs = per.reduce(function (a, n, i) { if (n) ds.add(D.spaces[i].d); return a + n; }, 0);
      result.innerHTML = "<b>" + fmt(n) + "</b> rooms in <b>" + ds.size + "</b> datasets · <b>" + fmt(irs) + "</b> IRs";
      keptIds = Array.from(ds).map(function (i) { return D.datasets[i].id; });
    } else {
      const kept = datasetsKept(st, per);
      const cards = $$("#cards .card");
      const by = {
        "": function (a, b) { return (+b.dataset.measured - +a.dataset.measured) || (+a.dataset.d - +b.dataset.d); },
        n: function (a, b) { return (+b.dataset.n - +a.dataset.n) || (+a.dataset.d - +b.dataset.d); },
        year: function (a, b) { return (+b.dataset.year - +a.dataset.year) || (+a.dataset.d - +b.dataset.d); },
        name: function (a, b) { return +a.dataset.d - +b.dataset.d; },
      }[st.sort || ""];
      const box = $("#cards");
      cards.sort(by).forEach(function (c) { box.appendChild(c); });
      const n = show(cards, function (c) { return kept[+c.dataset.d] !== 0; });
      const irFilter = IR_LEVEL.some(function (k) { return MULTI.includes(k) ? st[k].length : st[k]; });
      cards.forEach(function (c) {
        const k = kept[+c.dataset.d], el = c.querySelector(".count");
        if (el && k > 0) el.textContent = fmt(irFilter ? k : D.datasets[+c.dataset.d].n) + (irFilter ? " IRs match" : " IRs");
      });
      const measured = kept.filter(function (k) { return k > 0; });
      const irs = measured.reduce(function (a, k) { return a + k; }, 0);
      result.innerHTML = "<b>" + n + "</b> datasets" + (measured.length ? " · <b>" + measured.length + "</b> measured, <b>"
        + fmt(irs) + "</b> IRs" : "");
      const left = $("#leftout");
      const notMeasured = D.datasets.filter(function (d) { return d.status !== "measured" && datasetKept(d, st) && textOk(d, st); }).length;
      const hiddenNot = (st.grade || st.t30.length || st.type.length) && st.status !== "measured" ? notMeasured : 0;
      left.hidden = !hiddenNot;
      left.textContent = hiddenNot + " datasets not measured yet are left out: reverberation time, room type and decay "
        + "quality are known only for measured datasets.";
      keptIds = D.datasets.filter(function (d, i) { return kept[i] > 0; }).map(function (d) { return d.id; });
      if (!n) {
        left.hidden = false;
        left.textContent = "No dataset matches all of these filters. Remove one, or clear them all.";
      }
    }
    // the command line
    const cmd = $("#cmd"), c = command(st, keptIds);
    cmd.hidden = !(isSet(st) || st.text) || st.status === "not";
    cmd.querySelector("pre").textContent = c.line;
    const note = $("#cmdnote");
    note.hidden = !c.notes.length;
    note.textContent = c.notes.join(" ");
    chips(st);
    optionCounts(st);
  }

  write(new URLSearchParams(location.search));
  $$("details.more-opts").forEach(function (d) { if (d.querySelector("input:checked")) d.open = true; });
  form.addEventListener("change", function () { run(); });
  $("#text").addEventListener("input", function () { run(); });
  if ($("#sort")) $("#sort").addEventListener("change", function () { run(); });
  form.addEventListener("submit", function (ev) { ev.preventDefault(); });
  form.addEventListener("reset", function () { setTimeout(function () { $("#text").value = ""; run(); }, 0); });
  $("#more").addEventListener("click", function () { shown += PER; run(false); });
  run();
})();
