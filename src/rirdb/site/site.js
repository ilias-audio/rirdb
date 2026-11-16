// The website's only script. Every page works without it; it adds:
//   1. tooltips on the charts (from each mark's <title>), on hover and keyboard focus;
//   2. Copy buttons, and the Filters button on a phone;
//   3. the search of the datasets and rooms pages, over window.RIRDB (search-data.js, written by data.py).
//      Its filters, the "What you get" options and the datasets picked on the cards are the options of
//      `rirdb search` and `rirdb get`, so the page writes the command (and the Python) that selects the same
//      IRs. They live in the address, so a link reopens the same search.
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

  // ------------------------------------------------------------------ 3. the search, and the command it makes
  const form = $("#filters");
  const D = window.RIRDB;
  if (!form || !D) return;
  const roomsPage = !!$("#rooms");
  const col = {};
  D.columns.forEach(function (c, i) { col[c] = i; });
  const EDGES = D.t30_edges;
  // The reverberation-time ranges of the filter (words.REVERB): [low, high), high null = no limit.
  const REVERB = { dry: [0, 0.315], room: [0.315, 0.63], class: [0.63, 1.25], hall: [1.25, 2.5], live: [2.5, null] };
  const MULTI = ["provides", "type", "t30", "kind"];
  const IR_LEVEL = ["format", "use", "grade", "t30", "type", "kind", "archive"];   // filters read on each IR
  const byId = Object.create(null);           // no inherited keys: ?dataset=constructor is not a dataset
  D.datasets.forEach(function (d, i) { byId[d.id] = i; });
  // Datasets picked on the cards (Pick: only these) or left out, by id; in the address as dataset= and exclude=.
  const picks = { dataset: [], exclude: [] };

  // The rows of counts of each room, and the rooms of each dataset.
  const bySpace = D.spaces.map(function () { return []; });
  D.counts.forEach(function (r) { bySpace[r[col.space]].push(r); });
  const spacesOf = D.datasets.map(function () { return []; });
  D.spaces.forEach(function (s, i) { spacesOf[s.d].push(i); });
  const roomText = roomsPage ? $$("#rooms .roomrow").map(function (li) { return li.textContent.toLowerCase(); }) : [];

  const whole = function (v) { const n = Math.floor(Number(v)); return n >= 1 ? n : 0; };
  function read() {
    const st = { text: "", sort: "" };
    MULTI.forEach(function (k) { st[k] = []; });
    new FormData(form).forEach(function (v, k) {
      v = String(v).trim();
      if (MULTI.includes(k)) { if (v) st[k].push(v); } else st[k] = v;
    });
    st.text = (st.text || "").toLowerCase();
    st.n = whole(st.n);
    st.per = whole(st.per);
    return st;
  }
  function write(params) {                      // set the inputs and the picks from the address
    params.forEach(function (v, k) {
      $$('[name="' + k + '"]').forEach(function (el) {
        if (el.type === "radio" || el.type === "checkbox") { if (el.value === v) el.checked = true; }
        else el.value = v;
      });
    });
    ["dataset", "exclude"].forEach(function (k) {
      picks[k] = params.getAll(k).filter(function (id) { return id in byId; });
    });
  }

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
  // One row of counts, under the filters read on each IR (as `rirdb search`). `limit`: the index of the largest
  // download allowed in D.archive_limits (-1: any); `big` counts the limits a row's download is above.
  function rowKept(r, st, bins, limit) {
    if (!st.format) { if (!r[col.primary]) return false; }
    else if (st.format === "mono") { if (r[col.format] !== "mono" && !r[col.mono_w]) return false; }
    else if (r[col.format] !== st.format) return false;
    if (st.grade) { const g = D.grades.indexOf(r[col.grade]); if (g < 0 || g > D.grades.indexOf(st.grade)) return false; }
    if (st.use && !r[col[st.use]]) return false;
    if (bins && !bins.has(r[col.t30])) return false;
    if (limit >= 0 && r[col.big] > limit) return false;
    return true;
  }
  function spaceKept(s, st) {
    if (st.type.length && !st.type.includes(s.type)) return false;
    if (st.kind.length && !st.kind.includes(s.kind)) return false;
    return true;
  }
  function datasetKept(d, st) {                 // the filters on the dataset itself
    if (st.provides.some(function (p) { return !d.provides.includes(p); })) return false;
    if (st.status === "measured" && d.status !== "measured") return false;
    if (st.status === "not" && d.status === "measured") return false;
    return true;
  }
  function chosen(d) {                          // the picks on the cards
    if (picks.dataset.length && !picks.dataset.includes(d.id)) return false;
    return !picks.exclude.includes(d.id);
  }
  function textOk(d, st) {
    return !st.text || st.text.split(/\s+/).every(function (w) { return d.text.indexOf(w) >= 0; });
  }
  // IRs kept in each room (`per`, 0: left out, at most st.per of each room), the downloads they come in, and how
  // many have a reverberation time (`timed`: all a draw balanced over T30 can return). `keep(d)`, when given,
  // says which datasets count: the picks, or the datasets the command names.
  function irsPerSpace(st, keep) {
    const bins = st.t30.length ? binsOf(st.t30) : null;
    const limit = st.archive ? D.archive_limits.indexOf(st.archive) : -1;
    const dls = new Set();
    let timed = 0;
    const per = D.spaces.map(function (s, i) {
      const d = D.datasets[s.d];
      if (!datasetKept(d, st) || !spaceKept(s, st) || (keep && !keep(d))) return 0;
      if (roomsPage ? (st.text && !st.text.split(/\s+/).every(function (w) { return roomText[i].indexOf(w) >= 0; }))
                    : !textOk(d, st)) return 0;
      let n = 0, t = 0;
      bySpace[i].forEach(function (r) {
        if (rowKept(r, st, bins, limit)) { n += r[col.n]; if (r[col.t30] >= 0) t += r[col.n]; dls.add(r[col.dl]); }
      });
      // An entry of a dataset of thousands of generated rooms stands for s.rooms rooms (data.shown_space).
      const cap = st.per ? st.per * s.rooms : Infinity;
      timed += Math.min(t, cap);
      return Math.min(n, cap);
    });
    return { per: per, dls: dls, timed: timed };
  }
  // The rooms behind entry i that keep n IRs: 1, or for an entry standing for many generated rooms (one IR each)
  // at most n of them.
  const roomsIn = function (i, n) { return Math.min(n, D.spaces[i].rooms); };
  // The datasets kept: measured ones by their IRs, the others by what their pages state.
  function datasetsKept(st, per) {
    const measuredOnly = st.grade || st.t30.length || st.type.length || st.archive;
    return D.datasets.map(function (d, i) {
      if (d.status === "measured") return spacesOf[i].reduce(function (a, s) { return a + per[s]; }, 0);
      if (!datasetKept(d, st) || !textOk(d, st) || measuredOnly) return 0;
      if (st.format && !d.formats.includes(st.format)) return 0;
      if (st.use && d.uses[st.use] !== "yes") return 0;
      // Not measured yet: a simulated or device dataset has its kind; the rooms of the others are not sorted yet.
      const kind = d.origin === "simulated" || d.origin === "device" ? d.origin : "";
      if (st.kind.length && !st.kind.includes(kind)) return 0;
      return -1;                                 // kept, no IRs counted yet
    });
  }

  // The options of `rirdb search` and `rirdb get`, as [flag, value] pairs, the datasets they name, and what the
  // command cannot say. The text search is the page's own: on the datasets page a search that keeps up to 12
  // datasets names them; otherwise the command takes what the other filters keep.
  function options(st, keptIds) {
    const o = [], notes = [];
    if (st.format) o.push(["format", st.format]);
    if (st.use) o.push(["use", st.use]);
    st.kind.forEach(function (k) { o.push(["kind", k]); });
    if (st.grade) o.push(["grade", st.grade]);
    st.type.forEach(function (t) { o.push(["space-type", t]); });
    if (st.t30.length) {
      const order = Object.keys(REVERB).filter(function (k) { return st.t30.includes(k); });
      const contiguous = order.every(function (k, i) { return !i || REVERB[order[i - 1]][1] === REVERB[k][0]; });
      if (contiguous) {
        const lo = REVERB[order[0]][0], hi = REVERB[order[order.length - 1]][1];
        o.push(["t30", lo + "-" + (hi === null ? "" : hi)]);
      } else notes.push("The command line takes one reverberation-time range: run it once per range.");
    }
    st.provides.forEach(function (p) { o.push(["provides", p]); });
    let ids = [];
    if (picks.dataset.length) {
      ids = picks.dataset;
      if (st.text) notes.push("The text search is this page's own: the command takes the picked datasets.");
    } else if (st.text) {
      if (!roomsPage && keptIds.length && keptIds.length <= 12) ids = keptIds;
      else notes.push("The text search is this page's own: the command takes every " + (roomsPage ? "room" : "dataset")
                      + " the other filters keep." + (roomsPage ? "" : " Pick datasets on their cards to name them."));
    }
    ids.forEach(function (i) { o.push(["dataset", i]); });
    picks.exclude.forEach(function (i) { o.push(["exclude-dataset", i]); });
    if (st.per) o.push(["per-space", String(st.per)]);
    if (st.archive) o.push(["max-archive", st.archive]);
    if (st.n) {
      o.push(["n", String(st.n)]);
      if (st.balance) o.push(["balance", st.balance]);
    } else if (st.balance) notes.push("Spread the draw over shapes a random draw: set How many IRs to add it.");
    const whole = o.length === 1 && o[0][0] === "dataset";      // `rirdb get --dataset X` alone (cli.get)
    if (whole) notes.push("rirdb get --dataset " + o[0][1] + " alone gives every file of the dataset as published ("
      + (D.datasets[byId[o[0][1]]].size || "size unknown") + "), not the IRs counted above; the Python line gives "
      + "these. Add any option, such as --format, to get them with rirdb get.");
    return { o: o, ids: ids, notes: notes };
  }
  // What the command selects: the filters without the text search, in the datasets it names, without those left out.
  function selection(st, c) {
    return irsPerSpace(Object.assign({}, st, { text: "" }), function (d) {
      return (!c.ids.length || c.ids.includes(d.id)) && !picks.exclude.includes(d.id);
    });
  }
  const line = function (o) { return o.map(function (x) { return " --" + x[0] + " " + x[1]; }).join(""); };
  function python(o) {                          // the same, as rirdb.select.select(...)
    const LISTS = ["kind", "space-type", "provides", "dataset", "exclude-dataset"], NUMBERS = ["n", "per-space"];
    const args = [], lists = {};
    o.forEach(function (x) {
      const k = x[0].replace(/-/g, "_");
      if (LISTS.includes(x[0])) {
        if (!lists[k]) { lists[k] = []; args.push([k, lists[k]]); }
        lists[k].push(JSON.stringify(x[1]));
      } else args.push([k, NUMBERS.includes(x[0]) ? x[1] : JSON.stringify(x[1])]);
    });
    const parts = args.map(function (a) { return a[0] + "=" + (Array.isArray(a[1]) ? "[" + a[1].join(", ") + "]" : a[1]); });
    const call = parts.length > 2 ? "select(\n    " + parts.join(",\n    ") + ",\n)" : "select(" + parts.join(", ") + ")";
    return "from rirdb.select import select\n\nsel = " + call + "\nprint(len(sel.table), \"IRs\", sel.sizes())\n"
      + "sel.download(\"rirdb-out\")";
  }
  function human(b) {                           // powers of 1000, as the command line prints sizes
    const units = ["B", "kB", "MB", "GB", "TB"];
    let i = 0;
    while (b >= 1000 && i < units.length - 1) { b /= 1000; i++; }
    return (i ? b.toFixed(1) : String(b)) + " " + units[i];
  }
  // What the command selects: IRs, rooms, datasets, and the downloads it needs.
  function summary(st, sel) {
    const irs = sel.per.reduce(function (a, n) { return a + n; }, 0);
    if (!irs) return "No measured IR matches. Remove a filter, or a pick.";
    const ds = new Set();
    let rooms = 0;
    sel.per.forEach(function (n, i) { if (n) { ds.add(D.spaces[i].d); rooms += roomsIn(i, n); } });
    let bytes = 0, unknown = 0, mirror = false;
    sel.dls.forEach(function (i) {
      const x = D.downloads[i];
      if (x[0] === null) unknown++; else bytes += x[0];
      if (x[1]) mirror = true;
    });
    const pool = st.n && st.balance === "t30" ? sel.timed : irs;   // a T30 draw never returns an IR without rt_mid
    const got = st.n ? Math.min(st.n, pool) : irs;
    const most = st.n || st.per || mirror;       // a draw, a cap or a web folder may need less than this
    const where = fmt(rooms) + (rooms === 1 ? " room" : " rooms") + " of " + ds.size + (ds.size === 1 ? " dataset" : " datasets");
    return "<b>" + fmt(got) + "</b> IRs" + (got < irs ? ", drawn from " + fmt(irs) + " in " : " in ") + where
      + ". Download: " + (most ? "at most " : "") + "<b>" + human(bytes) + "</b>"
      + (unknown ? " and " + unknown + " of unknown size" : "") + " in " + fmt(sel.dls.size)
      + (sel.dls.size === 1 ? " download" : " downloads") + ", each taken whole; the command prints the exact size first.";
  }

  // Each option's count: what the results would be with that option chosen.
  function optionCounts(st) {
    $$(".opt input", form).forEach(function (input) {
      const c = input.parentElement.querySelector(".c");
      if (!c) return;
      const k = input.name, v = input.value, alt = JSON.parse(JSON.stringify(st));
      if (MULTI.includes(k)) { if (!alt[k].includes(v)) alt[k].push(v); } else alt[k] = v;
      const per = irsPerSpace(alt, roomsPage ? chosen : null).per;
      const n = roomsPage ? per.filter(Boolean).length : datasetsKept(alt, per).filter(Boolean).length;
      c.textContent = fmt(n);
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
    if (st.per) add("At most " + st.per + " per room", function () { form.elements.per.value = ""; });
    if (st.n) add("A draw of " + fmt(st.n), function () { form.elements.n.value = ""; });
    picks.dataset.forEach(function (id) {
      add("Only " + D.datasets[byId[id]].name, function () { picks.dataset = picks.dataset.filter(function (x) { return x !== id; }); });
    });
    picks.exclude.forEach(function (id) {
      add("Not " + D.datasets[byId[id]].name, function () { picks.exclude = picks.exclude.filter(function (x) { return x !== id; }); });
    });
  }

  let shownCount = 0;
  const PER = roomsPage ? 50 : 30;
  function show(items, keep) {                    // items in order; keep(i) tells whether item i is shown
    const kept = items.filter(keep);
    items.forEach(function (el) { el.hidden = true; });
    kept.slice(0, shownCount).forEach(function (el) { el.hidden = false; });
    const more = $("#more"), left = kept.length - shownCount;
    more.hidden = left <= 0;
    more.textContent = "Show " + Math.min(PER, left) + " more (" + fmt(left) + " left)";
    return kept.length;
  }

  function run(resetPage) {
    const st = read();
    if (resetPage !== false) shownCount = PER;
    const params = new URLSearchParams();
    Object.keys(st).forEach(function (k) {
      (MULTI.includes(k) ? st[k] : [st[k]]).forEach(function (v) { if (v) params.append(k, v); });
    });
    ["dataset", "exclude"].forEach(function (k) { picks[k].forEach(function (id) { params.append(k, id); }); });
    try { history.replaceState(null, "", params.toString() ? "?" + params : location.pathname); } catch (err) { /* file: */ }
    const per = irsPerSpace(st, roomsPage ? chosen : null).per;   // what the page lists
    const result = $("#result");
    let keptIds = [];
    if (roomsPage) {
      const rows = $$("#rooms .roomrow");
      const n = show(rows, function (li) { return per[+li.dataset.i] > 0; });
      rows.forEach(function (li) { const c = li.querySelector(".count"); if (c) c.textContent = fmt(per[+li.dataset.i]) + " IRs"; });
      $$("svg .pt").forEach(function (pt) { pt.classList.toggle("dim", !per[+pt.dataset.i]); });
      const ds = new Set(), irs = per.reduce(function (a, n, i) { if (n) ds.add(D.spaces[i].d); return a + n; }, 0);
      const rooms = per.reduce(function (a, n, i) { return a + (n ? roomsIn(i, n) : 0); }, 0);
      result.innerHTML = "<b>" + fmt(rooms) + "</b> rooms in <b>" + ds.size + "</b> datasets · <b>" + fmt(irs) + "</b> IRs"
        + (rooms !== n ? " (" + fmt(n) + " rows: a dataset of thousands of generated rooms has one row per room type)" : "");
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
      const irFilter = IR_LEVEL.some(function (k) { return MULTI.includes(k) ? st[k].length : st[k]; }) || st.per;
      cards.forEach(function (c) {
        const d = D.datasets[+c.dataset.d], k = kept[+c.dataset.d], el = c.querySelector(".count");
        if (el && k > 0) el.textContent = fmt(irFilter ? k : d.n) + (irFilter ? " IRs match" : " IRs");
        const picked = picks.dataset.includes(d.id), out = picks.exclude.includes(d.id);
        c.classList.toggle("picked", picked);
        c.classList.toggle("leftout", out || (picks.dataset.length > 0 && !picked));
        $$(".pick", c).forEach(function (b) { b.setAttribute("aria-pressed", String(b.dataset.pick === "dataset" ? picked : out)); });
      });
      const measured = kept.filter(function (k) { return k > 0; });
      const irs = measured.reduce(function (a, k) { return a + k; }, 0);
      result.innerHTML = "<b>" + n + "</b> datasets" + (measured.length ? " · <b>" + measured.length + "</b> measured, <b>"
        + fmt(irs) + "</b> IRs" : "");
      const left = $("#leftout");
      const notMeasured = D.datasets.filter(function (d) { return d.status !== "measured" && datasetKept(d, st) && textOk(d, st); }).length;
      const hiddenNot = (st.grade || st.t30.length || st.type.length || st.kind.length || st.archive) && st.status !== "measured" ? notMeasured : 0;
      left.hidden = !hiddenNot;
      left.textContent = "Datasets not measured yet are left out where a filter needs measured rooms (kind of space, "
        + "reverberation time, room type, decay quality, largest download): their rooms are not sorted yet.";
      keptIds = D.datasets.filter(function (d, i) { return kept[i] > 0; }).map(function (d) { return d.id; });
      if (!n) {
        left.hidden = false;
        left.textContent = "No dataset matches all of these filters. Remove one, or clear them all.";
      }
    }
    // the command
    const c = options(st, keptIds);
    const sel = selection(st, c);                                 // what the command selects
    // no command when the text search keeps no measured dataset (it would select everything)
    $("#cmd").hidden = st.status === "not" || (!roomsPage && st.text && !picks.dataset.length && !keptIds.length);
    $("#cmd-search").textContent = "rirdb search" + line(c.o);
    $("#cmd-get").textContent = "rirdb get" + line(c.o) + " --out rirdb-out";
    $("#cmd-py").textContent = python(c.o);
    $("#cmdsum").innerHTML = summary(st, sel);
    const note = $("#cmdnote");
    note.hidden = !c.notes.length;
    note.textContent = c.notes.join(" ");
    chips(st);
    optionCounts(st);
  }

  write(new URLSearchParams(location.search));
  $$("details.more-opts").forEach(function (d) { if (d.querySelector("input:checked")) d.open = true; });
  form.addEventListener("change", function () { run(); });
  $$('input[type="number"]', form).forEach(function (el) { el.addEventListener("input", function () { run(); }); });
  $("#text").addEventListener("input", function () { run(); });
  if ($("#sort")) $("#sort").addEventListener("change", function () { run(); });
  form.addEventListener("submit", function (ev) { ev.preventDefault(); });
  form.addEventListener("reset", function () {
    setTimeout(function () { $("#text").value = ""; picks.dataset = []; picks.exclude = []; run(); }, 0);
  });
  $("#more").addEventListener("click", function () { shownCount += PER; run(false); });
  document.addEventListener("click", function (ev) {     // Pick / Leave out on a card
    const b = ev.target.closest("[data-pick]");
    if (!b) return;
    const kind = b.dataset.pick, other = kind === "dataset" ? "exclude" : "dataset", id = b.dataset.id;
    const on = picks[kind].includes(id);
    picks[kind] = picks[kind].filter(function (x) { return x !== id; });
    picks[other] = picks[other].filter(function (x) { return x !== id; });
    if (!on) picks[kind].push(id);
    run(false);
  });
  run();
})();
