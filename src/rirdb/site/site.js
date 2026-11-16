// The website's only script. Every page works without it; it adds:
//   1. tooltips on the charts (from each mark's <title>, or its data-tip), on hover and keyboard focus;
//   2. Copy buttons, and the Filters button on a phone;
//   3. the datasets page, over window.RIRDB (search-data.js, written by data.py): the search, the list of datasets
//      (cards or a table), and What you get, the charts of what the command selects. Its filters, the options of
//      What you get and the datasets picked on the cards are the options of `rirdb search` and `rirdb get`, so the
//      page writes the command (and the Python) that selects the same IRs. They live in the address, so a link
//      reopens the same search.
(function () {
  "use strict";
  const $ = (s, el) => (el || document).querySelector(s);
  const $$ = (s, el) => Array.from((el || document).querySelectorAll(s));
  const fmt = (n) => Number(Math.round(n)).toLocaleString("en");
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);

  // ------------------------------------------------------------------ 1. tooltips
  const tip = document.createElement("div");
  tip.className = "tip"; tip.hidden = true; document.body.appendChild(tip);
  function place(x, y) {
    tip.style.left = Math.max(8, Math.min(x + 12, innerWidth - tip.offsetWidth - 8)) + "px";
    const above = y - tip.offsetHeight - 10;
    tip.style.top = (above > 8 ? above : y + 18) + "px";
  }
  const show = function (text, x, y) { tip.textContent = text; tip.hidden = false; place(x, y); };
  $$("svg .hit, svg .pt").forEach(function (mark) {   // the charts written by charts.py
    const title = mark.querySelector("title");
    if (!title) return;
    mark.dataset.tip = title.textContent;
    title.remove();                                  // else the browser shows its own tooltip as well
    mark.setAttribute("aria-label", mark.dataset.tip);
  });
  document.addEventListener("pointermove", function (ev) {    // every mark with a data-tip, also those drawn later
    const m = ev.target.closest && ev.target.closest("[data-tip]");
    if (m) show(m.dataset.tip, ev.clientX, ev.clientY); else tip.hidden = true;
  });
  document.addEventListener("focusin", function (ev) {
    const m = ev.target.closest && ev.target.closest("[data-tip]");
    if (!m) return;
    const r = m.getBoundingClientRect();
    show(m.dataset.tip, r.left + r.width / 2, r.top);
  });
  document.addEventListener("focusout", function () { tip.hidden = true; });

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

  // ------------------------------------------------------------------ 3. the datasets page
  const form = $("#filters");
  const D = window.RIRDB;
  if (!form || !D) return;
  $$(".tabs").forEach(function (t) { t.hidden = false; });   // without this script the page lists the datasets only
  const col = {};
  D.columns.forEach(function (c, i) { col[c] = i; });
  const EDGES = D.t30_edges, NB = EDGES.length - 1;            // T30 bins; bin NB: no reverberation time
  const LC = EDGES.slice(0, NB).map(function (lo, b) { return (Math.log(lo) + Math.log(EDGES[b + 1])) / 2; });
  // --balance t30 draws over 20 bins of rt_mid equal in log time from 0.1 to 10 s (select.T30_BINS), the end
  // bins holding everything beyond: the draw bin of each T30 bin of the page.
  const DRAW_BIN = LC.map(function (c) { return Math.max(0, Math.min(19, Math.floor(10 * Math.log10(Math.exp(c) / 0.1)))); });
  // The reverberation-time ranges of the filter (words.REVERB): [low, high), high null = no limit.
  const REVERB = { dry: [0, 0.315], room: [0.315, 0.63], class: [0.63, 1.25], hall: [1.25, 2.5], live: [2.5, null] };
  const MULTI = ["provides", "type", "t30", "kind"];
  const IR_LEVEL = ["format", "use", "grade", "t30", "type", "kind", "archive"];   // filters read on each IR
  const byId = Object.create(null);           // no inherited keys: ?dataset=constructor is not a dataset
  D.datasets.forEach(function (d, i) { byId[d.id] = i; });
  // Datasets picked on the cards (Pick: only these) or left out, by id; in the address as dataset= and exclude=.
  const picks = { dataset: [], exclude: [] };

  // The rows of counts of each room, the rooms of each dataset, and the words of each room (the rooms list).
  const bySpace = D.spaces.map(function () { return []; });
  D.counts.forEach(function (r) { bySpace[r[col.space]].push(r); });
  const spacesOf = D.datasets.map(function () { return []; });
  D.spaces.forEach(function (s, i) { spacesOf[s.d].push(i); });
  const roomText = D.spaces.map(function (s) {
    return (s.label + " " + (D.room_types[s.type] || s.type) + " " + D.datasets[s.d].name + " " + D.datasets[s.d].id).toLowerCase();
  });

  const whole = function (v) { const n = Math.floor(Number(v)); return n >= 1 ? n : 0; };
  function read() {
    const st = { text: "", sort: "", list: "", tab: "" };
    MULTI.forEach(function (k) { st[k] = []; });
    new FormData(form).forEach(function (v, k) {
      v = String(v).trim();
      if (MULTI.includes(k)) { if (v) st[k].push(v); } else st[k] = v;
    });
    st.text = (st.text || "").toLowerCase();
    st.n = whole(st.n);
    st.per = whole(st.per);
    st.since = whole(st.since); st.until = whole(st.until);
    if (st.since && st.until && st.since > st.until) {        // From after To: the range the two years make
      const s = st.since; st.since = st.until; st.until = s;
      form.elements.since.value = st.since; form.elements.until.value = st.until;
    }
    return st;
  }
  function write(params) {                      // set the inputs and the picks from the address
    params.forEach(function (v, k) {
      $$('[name="' + k + '"]').forEach(function (el) {
        if (el.type === "radio" || el.type === "checkbox") { if (el.value === v) el.checked = true; }
        else if (el.tagName === "SELECT" && !Array.from(el.options).some(function (o) { return o.value === v; })) {
          if (/^\d{4}$/.test(v)) { el.add(new Option(v, v)); el.value = v; }   // a year no dataset has: kept
        } else el.value = v;
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
      for (let b = 0; b < NB; b++) {
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
    if ((st.since || st.until) && !d.year) return false;          // `--year`: a dataset of unknown year is left out
    if (st.since && d.year < st.since) return false;
    if (st.until && d.year > st.until) return false;
    return true;
  }
  function chosen(d) {                          // the picks on the cards
    if (picks.dataset.length && !picks.dataset.includes(d.id)) return false;
    return !picks.exclude.includes(d.id);
  }
  function textOk(d, st) {
    return !st.text || st.text.split(/\s+/).every(function (w) { return d.text.indexOf(w) >= 0; });
  }

  // The IRs each room keeps. For every room: `n` the IRs the filters keep, `m` those left after the per-room cap
  // (at most st.per of each room; an entry of a dataset of thousands of generated rooms stands for s.rooms rooms,
  // data.shown_space). With `full`, also per T30 bin: `raw` before the cap,
  // `bins` after it, and `kd`, the IRs with a known defect. `keep(d)`, when given, says which datasets count: the
  // picks, or the datasets the command names. Also: the downloads the IRs come in.
  function gather(st, keep, full) {
    const bins = st.t30.length ? binsOf(st.t30) : null;
    const limit = st.archive ? D.archive_limits.indexOf(st.archive) : -1;
    const dls = new Set();
    const sp = D.spaces.map(function (s) {
      const d = D.datasets[s.d];
      if (!datasetKept(d, st) || !spaceKept(s, st) || (keep && !keep(d)) || (!keep && !textOk(d, st))) return null;
      return { s: s, raw: full ? new Float64Array(NB + 1) : null, n: 0, kd: 0 };
    });
    sp.forEach(function (x, i) {
      if (!x) return;
      bySpace[i].forEach(function (r) {
        if (!rowKept(r, st, bins, limit)) return;
        const k = r[col.n], b = r[col.t30];
        x.n += k;
        if (col.kd !== undefined && r[col.kd]) x.kd += k;
        if (full) x.raw[b >= 0 ? b : NB] += k;
        dls.add(r[col.dl]);
      });
      if (!x.n) { sp[i] = null; return; }
      x.r = Math.min(x.n, x.s.rooms);        // the rooms it keeps: generated rooms hold about one IR each
      const cap = st.per ? st.per * x.r : Infinity;
      x.m = Math.min(x.n, cap);
      if (full) x.bins = x.m < x.n ? capBins(x.raw, x.n, x.m, x.r) : x.raw;
    });
    return { sp: sp, dls: dls };
  }
  // `select.spread_per_space` at the resolution of the T30 bins: `cap` targets evenly spaced in log time from the
  // room's first bin to its last, each taking an IR from the nearest bin with one left (one IR: the median one);
  // IRs without a reverberation time only when the timed ones are fewer than the cap.
  function capBins(h, n, cap, nRooms) {
    if (nRooms > 1) return h.map(function (v) { return v * cap / n; });   // many rooms, each capped alike
    const timed = n - h[NB], out = new Float64Array(NB + 1);
    if (timed <= cap) { for (let b = 0; b < NB; b++) out[b] = h[b]; out[NB] = cap - timed; return out; }
    const free = Array.from(h.slice(0, NB));
    let lo = 0, hi = NB - 1;
    while (!free[lo]) lo++;
    while (!free[hi]) hi--;
    let targets;
    if (cap > 1) targets = Array.from({ length: cap }, function (_, k) { return LC[lo] + (LC[hi] - LC[lo]) * k / (cap - 1); });
    else { let c = 0, b = lo; while ((c += free[b]) < timed / 2) b++; targets = [LC[b]]; }
    targets.forEach(function (t) {
      let best = -1, gap = Infinity;
      for (let b = lo; b <= hi; b++) if (free[b] > 0 && Math.abs(LC[b] - t) < gap) { gap = Math.abs(LC[b] - t); best = b; }
      free[best]--; out[best]++;
    });
    return out;
  }
  // A draw of n IRs (`select.draw`: weighted, without replacement, each IR as likely as `select.weights` makes it)
  // keeps each IR as if every IR had a random clock that runs at its weight, and the n first to ring were taken.
  // On average, then, a group of c IRs of weight w gives c·(1 − e^(−w·τ)), with τ such that the groups add up to
  // n; a room is drawn from with the chance 1 − e^(−τ·W), W the summed weight of its IRs. Returns, per room: `e`
  // its IRs per T30 bin, `hit` how many of its rooms the draw takes from; and `pool`, the IRs the draw can take.
  function expected(st, g) {
    const sp = g.sp;
    const pool0 = sp.reduce(function (a, x) { return a + (x ? x.m : 0); }, 0);
    if (!st.n) return { e: sp.map(function (x) { return x && x.bins; }),
                        hit: sp.map(function (x) { return x ? x.r : 0; }), pool: pool0 };
    const bal = st.balance || "none";
    const size = {};                             // the IRs of each dataset, or of each draw bin of T30
    sp.forEach(function (x) {
      if (!x) return;
      if (bal === "dataset") size[x.s.d] = (size[x.s.d] || 0) + x.m;
      if (bal === "t30") for (let b = 0; b < NB; b++) size[DRAW_BIN[b]] = (size[DRAW_BIN[b]] || 0) + x.bins[b];
    });
    const weight = function (x, b) {             // the weight of each IR of room x in T30 bin b (b = NB: no T30)
      if (bal === "space") return x.r / x.m;        // a generated-rooms entry: x.r rooms of about m/r IRs
      if (bal === "dataset") return 1 / size[x.s.d];
      if (bal === "t30") return b < NB ? 1 / size[DRAW_BIN[b]] : 0;
      return 1;
    };
    const cells = [];                            // [room, bin, IRs, weight]
    sp.forEach(function (x, i) {
      if (x) for (let b = 0; b <= NB; b++) if (x.bins[b] > 0 && weight(x, b) > 0) cells.push([i, b, x.bins[b], weight(x, b)]);
    });
    const pool = cells.reduce(function (a, c) { return a + c[2]; }, 0);
    const n = Math.min(st.n, pool);
    const taken = function (tau) { return cells.reduce(function (a, c) { return a + c[2] * (1 - Math.exp(-c[3] * tau)); }, 0); };
    let lo = 0, hi = 1;
    while (n >= pool ? false : taken(hi) < n) hi *= 2;
    for (let k = 0; k < 60 && n < pool; k++) { const mid = (lo + hi) / 2; if (taken(mid) < n) lo = mid; else hi = mid; }
    const tau = n >= pool ? Infinity : hi;
    const e = sp.map(function (x) { return x && new Float64Array(NB + 1); });
    const w = sp.map(function () { return 0; }), can = w.slice();     // summed weight, and IRs the draw can take
    cells.forEach(function (c) {
      e[c[0]][c[1]] = c[2] * (1 - Math.exp(-c[3] * tau)); w[c[0]] += c[2] * c[3]; can[c[0]] += c[2];
    });
    const hit = sp.map(function (x, i) {        // generated rooms of about one IR each: those holding one it can take
      if (!x || !can[i]) return 0;
      const k = x.r > 1 ? x.r * can[i] / x.m : 1;
      return k * (1 - Math.exp(-tau * w[i] / k));
    });
    return { e: e, hit: hit, w: w, tau: tau, pool: pool };
  }
  // The datasets kept: measured ones by their IRs, the others by what their pages state.
  function datasetsKept(st, sp) {
    const measuredOnly = st.grade || st.t30.length || st.type.length || st.archive;
    return D.datasets.map(function (d, i) {
      if (d.status === "measured") return spacesOf[i].reduce(function (a, s) { return a + (sp[s] ? sp[s].m : 0); }, 0);
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
  // command cannot say. The text search is the page's own: a search that keeps up to 12 datasets names them;
  // otherwise the command takes what the other filters keep.
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
    if (st.since || st.until) {
      o.push(["year", st.since && st.since === st.until ? String(st.since) : (st.since || "") + "-" + (st.until || "")]);
    }
    let ids = [];
    if (picks.dataset.length) {
      ids = picks.dataset;
      if (st.text) notes.push("The text search is this page's own: the command takes the picked datasets.");
    } else if (st.text) {
      if (keptIds.length && keptIds.length <= 12) {
        ids = keptIds;
        notes.push("Your search names " + ids.length + (ids.length === 1 ? " dataset" : " datasets") + ": the command, and "
                   + "What you get, take all " + (ids.length === 1 ? "its" : "their") + " IRs; the rooms that match are listed "
                   + "under the command.");
      }
      else notes.push("The text search is this page's own: the command takes every dataset the other filters keep. "
                      + "Pick datasets on their cards to name them.");
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
      + "these. Add any option, such as --format, to get them with rirdb get."
      + (D.datasets[byId[o[0][1]]].defect ? " Its files include a known defect: its page says which." : ""));
    return { o: o, ids: ids, notes: notes };
  }
  // What the command selects: the filters without the text search, in the datasets it names, without those left out.
  function selection(st, c, full) {
    return gather(Object.assign({}, st, { text: "" }), function (d) {
      return (!c.ids.length || c.ids.includes(d.id)) && !picks.exclude.includes(d.id);
    }, full);
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
  const sum = function (a) { let t = 0; for (const v of a) t += v; return t; };
  // What the command selects, in numbers: IRs, rooms, datasets, the downloads it needs. A draw's rooms and
  // datasets are those it takes IRs from, on average (expected).
  function totals(st, sel, ex) {
    const ds = new Map(), dw = new Map();
    let rooms = 0, drawnRooms = 0;
    sel.sp.forEach(function (x, i) {
      if (!x) return;
      rooms += x.r;
      drawnRooms += ex.hit[i];
      ds.set(x.s.d, (ds.get(x.s.d) || 0) + (st.n ? sum(ex.e[i]) : x.m));
      if (st.n) dw.set(x.s.d, (dw.get(x.s.d) || 0) + ex.w[i]);
    });
    let datasets = 0;
    ds.forEach(function (_, d) { datasets += !st.n ? 1 : dw.get(d) > 0 ? 1 - Math.exp(-ex.tau * dw.get(d)) : 0; });
    const irs = sel.sp.reduce(function (a, x) { return a + (x ? x.m : 0); }, 0);
    const pool = st.n ? ex.pool : irs;           // a T30 draw never takes an IR without a reverberation time
    const got = st.n ? Math.min(st.n, pool) : irs;
    // the downloads: every one the IRs come in; a draw of n needs n of them at most, so it counts the n largest
    const sizes = [];
    let unknown = 0, mirror = false;
    sel.dls.forEach(function (i) {
      const x = D.downloads[i];
      if (x[0] === null) unknown++; else sizes.push(x[0]);
      if (x[1]) mirror = true;
    });
    const ndl = st.n ? Math.min(st.n, sel.dls.size) : sel.dls.size;
    sizes.sort(function (a, b) { return b - a; });
    const bytes = sum(sizes.slice(0, st.n ? ndl : sizes.length));
    if (st.n) unknown = Math.min(unknown, ndl);
    return { irs: irs, got: got, pool: pool, rooms: rooms, drawnRooms: Math.min(drawnRooms, got),
             ds: ds, nds: ds.size, datasets: datasets, bytes: bytes, unknown: unknown, ndl: ndl,
             most: !!(st.n || st.per || mirror) };     // a draw, a cap or a web folder may need less than this
  }
  function summary(st, t) {
    if (!t.irs) return "No measured IR matches. Remove a filter, or a pick.";
    const where = fmt(t.rooms) + (t.rooms === 1 ? " room" : " rooms") + " of " + t.nds + (t.nds === 1 ? " dataset" : " datasets");
    return "<b>" + fmt(t.got) + "</b> IRs" + (t.got < t.pool ? ", drawn from " + fmt(t.pool) + " in " : " in ") + where
      + ". Download: " + (t.most ? "at most " : "") + "<b>" + human(t.bytes) + "</b>"
      + (t.unknown ? " and " + t.unknown + " of unknown size" : "") + " in " + (st.n ? "up to " : "") + fmt(t.ndl)
      + (t.ndl === 1 ? " download" : " downloads") + ", each taken whole; the command prints the exact size first.";
  }

  // Each option's count: the datasets the page would list with that option chosen.
  function optionCounts(st) {
    $$(".opt input", form).forEach(function (input) {
      const c = input.parentElement.querySelector(".c");
      if (!c) return;
      const k = input.name, v = input.value, alt = JSON.parse(JSON.stringify(st));
      if (MULTI.includes(k)) { if (!alt[k].includes(v)) alt[k].push(v); } else alt[k] = v;
      const n = datasetsKept(alt, gather(alt, null, false).sp).filter(Boolean).length;
      c.textContent = fmt(n);
      input.parentElement.classList.toggle("zero", !n && !input.checked);
    });
  }
  // The datasets of each year the other filters keep, over the years chosen (the small chart of Published).
  function yearBars(st) {
    const box = $("#yearbars");
    if (!box) return;
    const alt = Object.assign({}, st, { since: 0, until: 0 });
    const kept = datasetsKept(alt, gather(alt, null, false).sp);
    const years = D.datasets.map(function (d) { return d.year; }).filter(Boolean);
    const lo = Math.min.apply(null, years), hi = Math.max.apply(null, years), count = {};
    D.datasets.forEach(function (d, i) { if (kept[i] && d.year) count[d.year] = (count[d.year] || 0) + 1; });
    const top = Math.max(1, Math.max.apply(null, Object.values(count).concat([1])));
    let html = "";
    for (let y = lo; y <= hi; y++) {
      const n = count[y] || 0, inside = (!st.since || y >= st.since) && (!st.until || y <= st.until);
      html += '<i class="' + (inside ? "in" : "out") + (n ? "" : " zero") + '" style="height:' + (n ? Math.max(8, 100 * n / top) : 0)
        + '%" data-tip="' + y + ": " + n + (n === 1 ? " dataset" : " datasets") + '"></i>';
    }
    box.innerHTML = html;
    $("#ylo").textContent = lo; $("#yhi").textContent = hi;          // the first and last year under the bars
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
    $$('.opt input:checked', document).forEach(function (input) {
      if (!input.value || input.form !== form) return;
      const legend = input.closest("fieldset").querySelector("legend").firstChild.textContent.trim();
      const label = input.parentElement.querySelector("span").textContent;
      add(MULTI.includes(input.name) && input.name !== "provides" ? label : legend + ": " + label, function () {
        if (input.type === "checkbox") input.checked = false;
        else document.querySelector('[name="' + input.name + '"][value=""]').checked = true;
      });
    });
    if (st.since || st.until) {
      add("Published " + (st.since && st.until ? (st.since === st.until ? "in " + st.since : st.since + " to " + st.until)
        : st.since ? "since " + st.since : "until " + st.until),
        function () { form.elements.since.value = ""; form.elements.until.value = ""; });
    }
    if (st.per) add("At most " + st.per + " per room", function () { form.elements.per.value = ""; });
    if (st.n) add("A draw of " + fmt(st.n), function () { form.elements.n.value = ""; });
    picks.dataset.forEach(function (id) {
      add("Only " + D.datasets[byId[id]].name, function () { picks.dataset = picks.dataset.filter(function (x) { return x !== id; }); });
    });
    picks.exclude.forEach(function (id) {
      add("Not " + D.datasets[byId[id]].name, function () { picks.exclude = picks.exclude.filter(function (x) { return x !== id; }); });
    });
  }

  // ---------------------------------------------------------------- the charts of What you get (SVG, no library)
  const svg = function (w, h, body, label) {
    return '<svg class="chart" viewBox="0 0 ' + w + " " + h + '" role="img" aria-label="' + esc(label) + '">' + body + "</svg>";
  };
  // The reverberation time of the IRs you get (columns, as shares) over everything the filters match (outline).
  function t30Chart(got, all, untimed) {
    const W = 420, H = 190, L = 40, R = 10, T = 16, B = 34, pw = W - L - R, base = H - B, step = pw / NB;
    const tg = sum(got) || 1, ta = sum(all) || 1;
    const sg = got.map(function (v) { return v / tg; }), sa = all.map(function (v) { return v / ta; });
    const top = Math.max(Math.max.apply(null, sg), Math.max.apply(null, sa)) * 1.1 || 1;
    const y = function (v) { return base - (base - T) * v / top; };
    const x = function (v) { return L + pw * Math.log(v / EDGES[0]) / Math.log(EDGES[NB] / EDGES[0]); };
    let out = "";
    const grid = Math.max(5, Math.floor(top * 100 / 2 / 5) * 5);
    [grid, 2 * grid].forEach(function (p) {
      if (p / 100 < top) out += '<line class="grid" x1="' + L + '" x2="' + (W - R) + '" y1="' + y(p / 100) + '" y2="' + y(p / 100)
        + '"/><text x="' + (L - 6) + '" y="' + (y(p / 100) + 4) + '" text-anchor="end">' + p + " %</text>";
    });
    [0.1, 0.3, 1, 3, 10].forEach(function (t) {
      out += '<text x="' + x(t) + '" y="' + (base + 15) + '" text-anchor="middle">' + t + "</text>";
    });
    out += '<text x="' + (W - R) + '" y="' + (H - 3) + '" text-anchor="end">T30, s</text>';
    const bw = Math.min(24, step - 2);
    for (let b = 0; b < NB; b++) {
      const lo = EDGES[b], hi = EDGES[b + 1];
      const span = b === 0 ? "below " + hi : b === NB - 1 ? lo + " s or more" : lo + " to " + hi + " s";
      const tipText = (got[b] < 10 && got[b] % 1 ? got[b].toFixed(1) : fmt(got[b])) + " IRs (" + Math.round(100 * sg[b])
        + " %) " + span + "; the filters alone: " + fmt(all[b]) + " (" + Math.round(100 * sa[b]) + " %)";
      out += '<g class="hit" data-tip="' + esc(tipText) + '"><rect class="target" x="' + (L + b * step) + '" y="' + T
        + '" width="' + step + '" height="' + (base - T) + '"/>'
        + (got[b] > 0 ? '<rect class="bar" x="' + (L + b * step + (step - bw) / 2) + '" y="' + y(sg[b]) + '" width="' + bw
          + '" height="' + (base - y(sg[b])) + '" rx="2"/>' : "") + "</g>";
    }
    let path = "M" + L + "," + base;
    sa.forEach(function (v, b) { path += "V" + y(v) + "H" + (L + (b + 1) * step); });
    out += '<path class="outline" d="' + path + "V" + base + '"/>';
    return svg(W, H, out, "Reverberation time of the IRs you get") + (untimed >= 0.5 ? '<p class="small faint">'
      + fmt(untimed) + " IRs you get have no reverberation time (anechoic rooms, devices, decays too short to measure)"
      + ".</p>" : "");
  }
  // Horizontal bars: [label, value, note, href]; the longest is `top`.
  function hbars(items, unit) {
    if (!items.length) return '<p class="small faint">None.</p>';
    const top = Math.max.apply(null, items.map(function (r) { return r[1]; })) || 1;
    return '<ul class="hbars">' + items.map(function (r) {
      const name = r[3] ? '<a href="' + esc(r[3]) + '">' + esc(r[0]) + "</a>" : esc(r[0]);
      return '<li data-tip="' + esc(r[0] + ": " + fmt(r[1]) + " " + unit + (r[2] ? " " + r[2] : "")) + '"><span class="hl">' + name
        + '</span><span class="hb"><i style="width:' + (100 * r[1] / top) + '%"></i></span><span class="hv num">' + fmt(r[1])
        + (r[2] ? ' <span class="faint">' + esc(r[2]) + "</span>" : "") + "</span></li>";
    }).join("") + "</ul>";
  }
  // Rooms by how many IRs each gives, with the per-room cap (columns) and without it (outline).
  const PER_EDGES = [1, 2, 6, 21, 101, 1001, Infinity];
  const PER_LABELS = ["1", "2–5", "6–20", "21–100", "101–1,000", "over 1,000"];
  function perChart(sel) {
    const got = PER_LABELS.map(function () { return 0; }), all = got.slice();
    const put = function (h, v, k) {
      for (let j = 0; j < PER_LABELS.length; j++) if (v >= PER_EDGES[j] - 0.5 && v < PER_EDGES[j + 1] - 0.5) { h[j] += k; return; }
      h[0] += k;                                  // a share of a generated room: under one IR on average
    };
    sel.sp.forEach(function (x) { if (x) { put(got, x.m / x.r, x.r); put(all, x.n / x.r, x.r); } });
    const W = 420, H = 170, L = 44, R = 8, T = 12, B = 34, pw = W - L - R, base = H - B, step = pw / got.length;
    const top = Math.max.apply(null, got.concat(all)) * 1.1 || 1;
    const y = function (v) { return base - (base - T) * v / top; };
    let out = '<line class="grid" x1="' + L + '" x2="' + (W - R) + '" y1="' + y(top / 1.1) + '" y2="' + y(top / 1.1) + '"/>';
    got.forEach(function (v, j) {
      const x0 = L + j * step, bw = Math.min(40, step - 8);
      out += '<g class="hit" data-tip="' + esc(fmt(v) + " rooms give " + PER_LABELS[j] + " IRs; without the cap "
        + fmt(all[j])) + '"><rect class="target" x="' + x0 + '" y="' + T + '" width="' + step + '" height="' + (base - T) + '"/>'
        + (v ? '<rect class="bar" x="' + (x0 + (step - bw) / 2) + '" y="' + Math.min(y(v), base - 2) + '" width="' + bw + '" height="' + Math.max(2, base - y(v))
        + '" rx="2"/>' : "") + '<rect class="ghost" x="' + (x0 + (step - bw) / 2) + '" y="' + y(all[j]) + '" width="' + bw
        + '" height="' + (base - y(all[j])) + '"/></g><text x="' + (x0 + step / 2) + '" y="' + (base + 15)
        + '" text-anchor="middle">' + PER_LABELS[j] + "</text>";
      if (v) out += '<text class="label" x="' + (x0 + step / 2) + '" y="' + (Math.min(y(v), base - 2) - 4) + '" text-anchor="middle">'
        + fmt(v) + "</text>";          // the count on each column: one bucket can dwarf the others
    });
    out += '<text x="' + (W - R) + '" y="' + (H - 3) + '" text-anchor="end">IRs from the room</text>';
    return svg(W, H, out, "Rooms by how many IRs they give");
  }
  // Every room at its median T30 and DRR, coloured by kind; the rooms not in the selection faded.
  function roomsChart(sel) {
    const W = 960, H = 400, L = 40, R = 10, T = 16, B = 34, pw = W - L - R, base = H - B;
    const x = function (v) { return L + pw * Math.log(Math.min(Math.max(v, 0.05), 25) / 0.05) / Math.log(500); };
    const y = function (v) { return base - (base - T) * (Math.min(Math.max(v, -40), 40) + 40) / 80; };
    let out = "";
    for (let t = -40; t <= 40; t += 10) {
      out += '<line class="' + (t ? "grid" : "axis") + '" x1="' + L + '" x2="' + (W - R) + '" y1="' + y(t) + '" y2="' + y(t)
        + '"/><text x="' + (L - 6) + '" y="' + (y(t) + 4) + '" text-anchor="end">' + (t > 0 ? "+" + t : t) + "</text>";
    }
    [0.1, 0.3, 1, 3, 10].forEach(function (t) {
      out += '<line class="grid" x1="' + x(t) + '" x2="' + x(t) + '" y1="' + T + '" y2="' + base + '"/><text x="' + x(t) + '" y="'
        + (base + 15) + '" text-anchor="middle">' + t + " s</text>";
    });
    out += '<text x="' + (W - R) + '" y="' + (H - 3) + '" text-anchor="end">reverberation time T30, median of the room</text>'
      + '<text x="' + L + '" y="' + (T - 5) + '">DRR, dB</text>';
    const dots = ["", ""];                       // the faded rooms first, under the others
    D.spaces.forEach(function (s, i) {
      if (s.t30 === null || s.drr === null) return;
      const d = D.datasets[s.d], on = !!sel.sp[i];
      dots[on ? 1 : 0] += '<a href="d/' + esc(d.id) + ".html#room-" + esc(s.id.split("/").slice(1).join("/")) + '" tabindex="-1" class="pt k-'
        + s.kind + (on ? "" : " dim") + '" data-tip="' + esc(s.label + " (" + d.name + "): T30 " + s.t30.toFixed(2) + " s, DRR "
        + (s.drr >= 0 ? "+" : "") + s.drr.toFixed(1) + " dB") + '"><circle class="target" cx="' + x(s.t30) + '" cy="' + y(s.drr)
        + '" r="8"/><circle class="dot" cx="' + x(s.t30) + '" cy="' + y(s.drr) + '" r="3.4"/></a>';
    });
    return '<div class="plotbox">' + svg(W, H, out + dots[0] + dots[1], "Rooms by reverberation time and DRR").replace(
      'class="chart"', 'class="chart scatter"') + '</div><p class="legend kinds">' + Object.keys(D.kinds).map(function (k) {
      return '<span><i class="k-' + k + '"></i>' + esc(D.kinds[k]) + "</span>"; }).join("") + "</p>";
  }

  // The median T30 of IRs counted per T30 bin: the middle of the bin that holds the median, to two digits.
  function medianT30(h) {
    const total = sum(h.slice(0, NB));
    if (!total) return "";
    let c = 0, b = 0;
    while ((c += h[b]) < total / 2) b++;
    return Number(Math.exp(LC[b]).toPrecision(2)).toString();
  }
  let roomsShown = 20;
  // What you get: the numbers, the charts and the rooms, for the selection `sel` (the command's).
  function getView(st, sel, t, ex) {
    const draw = !!st.n, about = draw ? "about " : "";
    const nRooms = Math.round(draw ? t.drawnRooms : t.rooms), nSets = Math.round(draw ? t.datasets : t.nds);
    const tiles = [
      [fmt(t.got), "IRs", draw ? "drawn from " + fmt(t.pool) : st.per ? "at most " + st.per + " of each room" : ""],
      [about + fmt(nRooms), nRooms === 1 ? "room" : "rooms", draw ? "of " + fmt(t.rooms) : ""],
      [about + fmt(nSets), nSets === 1 ? "dataset" : "datasets", draw ? "of " + fmt(t.nds) : ""],
      [(t.most ? "≤ " : "") + human(t.bytes), "to download", (draw ? "up to " : "") + fmt(t.ndl) + (t.ndl === 1 ? " download" : " downloads")
        + (t.unknown ? ", " + t.unknown + " of unknown size" : "")]];
    $("#stats").innerHTML = t.irs ? tiles.map(function (x) {
      return '<div class="stat"><b class="num">' + x[0] + "</b><span>" + x[1] + "</span>" + (x[2] ? '<span class="faint small">'
        + x[2] + "</span>" : "") + "</div>"; }).join("") : '<p class="note">No measured IR matches. Remove a filter, or a pick.</p>';
    $(".getcharts").hidden = !t.irs;
    $(".getrooms").hidden = !t.irs;
    // a known defect among them (registry `defects`): the datasets, and how many of their IRs have one (after the
    // per-room cap, about: the cap keeps a share of each room)
    const kd = new Map();
    sel.sp.forEach(function (x) { if (x && x.kd) kd.set(x.s.d, (kd.get(x.s.d) || 0) + x.kd * x.m / x.n); });
    const warn = $("#defects");
    warn.hidden = !kd.size;
    warn.innerHTML = Array.from(kd).map(function (k) {
      const d = D.datasets[k[0]], n = Math.max(1, Math.round(k[1]));
      const name = '<a href="d/' + esc(d.id) + '.html#defect">' + esc(d.name) + "</a>";
      const many = (st.per ? "about " : "") + fmt(n) + (n === 1 ? " IR" : " IRs");
      return "<b>Known defect.</b> " + (draw ? "The draw can take IRs of " + name + " whose files have a defect as published ("
          + many + " of those it draws from, graded D): its page says which; rirdb warns when it does."
        : many.charAt(0).toUpperCase() + many.slice(1) + " of " + name + (n === 1 ? " has" : " have") + " a defect of the files as "
          + "published, so " + (n === 1 ? "it is" : "they are") + " graded D: its page says which. They stay in the selection, and rirdb "
          + "prints the same warning.");
    }).join("<br>");
    if (!t.irs) return;
    // the reverberation time
    const got = new Float64Array(NB + 1), all = new Float64Array(NB + 1);
    sel.sp.forEach(function (x, i) { if (x) for (let b = 0; b <= NB; b++) { got[b] += ex.e[i][b]; all[b] += x.raw[b]; } });
    $("#ch-t30 .chartbox").innerHTML = t30Chart(Array.from(got.slice(0, NB)), Array.from(all.slice(0, NB)), got[NB]);
    // per dataset: the 12 that give the most, then the rest
    const per = Array.from(t.ds).map(function (k) {
      const d = D.datasets[k[0]];
      const raw = spacesOf[k[0]].reduce(function (a, s) { return a + (sel.sp[s] ? sel.sp[s].n : 0); }, 0);
      return [d.name, k[1], raw > k[1] + 0.5 ? "of " + fmt(raw) : "", "d/" + d.id + ".html"];
    }).sort(function (a, b) { return b[1] - a[1]; });
    const rest = per.slice(12);
    $("#ch-ds .chartbox").innerHTML = hbars(per.slice(0, 12).concat(rest.length ? [[rest.length + " more datasets",
      sum(rest.map(function (r) { return r[1]; })), "", ""]] : []), "IRs" + (draw ? " on average" : ""));
    $("#ch-per .chartbox").innerHTML = perChart(sel);
    // rooms by type
    const types = new Map();
    sel.sp.forEach(function (x, i) { if (x) types.set(x.s.type, (types.get(x.s.type) || 0) + (draw ? ex.hit[i] : x.r)); });
    const tl = Array.from(types).sort(function (a, b) { return b[1] - a[1]; });
    const other = tl.slice(8);
    $("#ch-types .chartbox").innerHTML = hbars(tl.slice(0, 8).map(function (k) { return [D.room_types[k[0]] || k[0], k[1], "", ""]; })
      .concat(other.length ? [[other.length + " more types", sum(other.map(function (k) { return k[1]; })), "", ""]] : []), "rooms");
    $("#ch-rooms .chartbox").innerHTML = roomsChart(sel);
    // the rooms you get, the most IRs first; a text search narrows them to the rooms it names
    const words = st.text ? st.text.split(/\s+/) : [];
    let list = [];
    // in a draw, the rooms it most likely takes IRs from (half an IR or more, on average)
    sel.sp.forEach(function (x, i) {
      if (!x) return;
      const n = draw ? sum(ex.e[i]) : x.m;
      if (n >= 0.5) list.push([i, n]);
    });
    const named = list.filter(function (r) { return words.every(function (w) { return roomText[r[0]].indexOf(w) >= 0; }); });
    const rooms = function (l) {                 // "N rooms", and the rows they fill where generated rooms are grouped
      const k = Math.round(l.reduce(function (a, r) { return a + (draw ? ex.hit[r[0]] : sel.sp[r[0]].r); }, 0));
      return (draw ? "about " : "") + fmt(k) + (k === 1 ? " room" : " rooms") + (!draw && k !== l.length ? " in " + fmt(l.length) + (l.length === 1 ? " row" : " rows") + " (a dataset of "
        + "thousands of generated rooms has a row per room type)" : "");
    };
    const cap = function (s) { return s.charAt(0).toUpperCase() + s.slice(1); };
    $("#roomsnote").textContent = words.length ? (named.length ? cap(rooms(named)) + " match “" + st.text + "”."
      : "No room is named “" + st.text + "”: " + rooms(list) + ", the most IRs first.")
      : cap(rooms(list)) + ", the most IRs first" + (draw ? " (those a draw most likely takes IRs from)" : "") + ".";
    if (named.length) list = named;
    list.sort(function (a, b) { return b[1] - a[1] || a[0] - b[0]; });
    $("#roomlist").innerHTML = list.slice(0, roomsShown).map(function (r) {
      const s = D.spaces[r[0]], d = D.datasets[s.d];
      const t30 = medianT30(draw ? ex.e[r[0]] : sel.sp[r[0]].bins);      // of the IRs you get, not of the whole room
      const vals = [t30 ? "T30 " + t30 + " s" : "", s.volume ? fmt(s.volume) + " m³" : ""]
        .filter(Boolean).join(" · ");
      return '<li class="roomrow"><i class="kind k-' + s.kind + '"></i><div><a href="d/' + esc(d.id) + ".html#room-"
        + esc(s.id.split("/").slice(1).join("/")) + '">' + esc(s.label) + '</a><span class="quiet small"> · '
        + esc(D.room_types[s.type] || s.type) + '</span><div class="small quiet">' + esc(d.name) + "</div></div>"
        + '<div class="small num">' + vals + '<div class="faint">' + (draw ? "about " : "") + fmt(r[1]) + " IRs</div></div></li>";
    }).join("");
    const left = list.length - roomsShown, more = $("#moreroom");
    more.hidden = left <= 0;
    more.textContent = "Show " + Math.min(20, left) + " more rooms (" + fmt(left) + " left)";
  }

  let shownCount = 0;
  const PER = 30;
  function showItems(items, keep) {               // items in order; keep(i) tells whether item i is shown
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
    if (resetPage !== false) { shownCount = PER; roomsShown = 20; }
    const params = new URLSearchParams();
    Object.keys(st).forEach(function (k) {
      (MULTI.includes(k) ? st[k] : [st[k]]).forEach(function (v) { if (v) params.append(k, v); });
    });
    ["dataset", "exclude"].forEach(function (k) { picks[k].forEach(function (id) { params.append(k, id); }); });
    try { history.replaceState(null, "", params.toString() ? "?" + params : location.pathname); } catch (err) { /* file: */ }
    // the two views
    const onGet = st.tab === "get";
    $$('[role="tab"]').forEach(function (b) { b.setAttribute("aria-selected", String((b.dataset.tab === "get") === onGet)); });
    $("#list").hidden = onGet;
    $("#get").hidden = !onGet;
    // the list of datasets: what the page lists (the text search and the filters; the picks only mark the cards)
    const sp = gather(st, null, false).sp;
    const kept = datasetsKept(st, sp);
    const table = st.list === "table";
    $("#cards").hidden = table;
    $("#tablebox").hidden = !table;
    $$("[data-list]").forEach(function (b) { b.setAttribute("aria-pressed", String((b.dataset.list === "table") === table)); });
    const by = {
      "": function (a, b) { return (+b.dataset.measured - +a.dataset.measured) || (+a.dataset.d - +b.dataset.d); },
      n: function (a, b) { return (Math.max(0, kept[+b.dataset.d]) - Math.max(0, kept[+a.dataset.d])) || (+a.dataset.d - +b.dataset.d); },   // the IRs shown
      rooms: function (a, b) { return (+b.dataset.rooms - +a.dataset.rooms) || (+a.dataset.d - +b.dataset.d); },
      year: function (a, b) { return (+b.dataset.year - +a.dataset.year) || (+a.dataset.d - +b.dataset.d); },
      name: function (a, b) { return +a.dataset.d - +b.dataset.d; },
    }[st.sort || ""] || function (a, b) { return +a.dataset.d - +b.dataset.d; };
    $$("[data-sort]").forEach(function (b) { b.setAttribute("aria-pressed", String(b.dataset.sort === (st.sort || ""))); });
    const box = table ? $("#rows") : $("#cards");
    const items = $$(".item", box).sort(by);
    items.forEach(function (c) { box.appendChild(c); });
    const n = showItems(items, function (c) { return kept[+c.dataset.d] !== 0; });
    const irFilter = IR_LEVEL.some(function (k) { return MULTI.includes(k) ? st[k].length : st[k]; }) || st.per;
    $$(".item").forEach(function (c) {
      const d = D.datasets[+c.dataset.d], k = kept[+c.dataset.d], el = c.querySelector(".count");
      if (el && k > 0) el.textContent = fmt(irFilter ? k : d.n) + (table ? "" : irFilter ? " IRs match" : " IRs");
      const picked = picks.dataset.includes(d.id), out = picks.exclude.includes(d.id);
      c.classList.toggle("picked", picked);
      c.classList.toggle("leftout", out || (picks.dataset.length > 0 && !picked));
      $$(".pick", c).forEach(function (b) { b.setAttribute("aria-pressed", String(b.dataset.pick === "dataset" ? picked : out)); });
    });
    const measured = kept.filter(function (k) { return k > 0; });
    $("#result").innerHTML = "<b>" + n + "</b> datasets" + (measured.length ? " · <b>" + measured.length + "</b> measured, <b>"
      + fmt(measured.reduce(function (a, k) { return a + k; }, 0)) + "</b> IRs" : "");
    $("#n-list").textContent = n;
    const left = $("#leftout");
    const notMeasured = D.datasets.filter(function (d) { return d.status !== "measured" && datasetKept(d, st) && textOk(d, st); }).length;
    const hiddenNot = (st.grade || st.t30.length || st.type.length || st.kind.length || st.archive) && st.status !== "measured" ? notMeasured : 0;
    left.hidden = !hiddenNot;
    left.textContent = "Datasets not measured yet are left out where a filter needs measured rooms (kind of space, "
      + "reverberation time, room type, decay quality, largest download): their rooms are not sorted yet.";
    if (!n) {
      left.hidden = false;
      left.textContent = "No dataset matches all of these filters. Remove one, or clear them all.";
    }
    const keptIds = D.datasets.filter(function (d, i) { return kept[i] > 0; }).map(function (d) { return d.id; });
    // the command, and what it selects
    const c = options(st, keptIds);
    const sel = selection(st, c, true);
    const ex = expected(st, sel);
    const t = totals(st, sel, ex);
    const none = st.status === "not" || (st.text && !picks.dataset.length && !keptIds.length);  // it would select everything
    $("#cmd").hidden = none;
    $("#cmd-search").textContent = "rirdb search" + line(c.o);
    $("#cmd-get").textContent = "rirdb get" + line(c.o) + " --out rirdb-out";
    $("#cmd-py").textContent = python(c.o);
    $("#cmdsum").innerHTML = summary(st, t);
    const note = $("#cmdnote");
    note.hidden = !c.notes.length;
    note.textContent = c.notes.join(" ");
    $("#n-get").textContent = none || !t.irs ? "" : fmt(t.got) + " IRs";
    if (onGet) {
      if (none) { $("#stats").innerHTML = '<p class="note">No measured dataset matches this search.</p>';
        $(".getcharts").hidden = true; $(".getrooms").hidden = true; $("#defects").hidden = true; }
      else getView(st, sel, t, ex);
    }
    chips(st);
    optionCounts(st);
    yearBars(st);
  }

  write(new URLSearchParams(location.search));
  $$("details.more-opts").forEach(function (d) { if (d.querySelector("input:checked")) d.open = true; });
  document.addEventListener("change", function (ev) { if (ev.target.form === form) run(); });
  document.addEventListener("input", function (ev) {
    if (ev.target.form === form && (ev.target.type === "number" || ev.target.type === "search")) run();
  });
  form.addEventListener("submit", function (ev) { ev.preventDefault(); });
  form.addEventListener("reset", function () {        // clears the filters, keeps the view
    const keep = { tab: form.elements.tab.value, list: form.elements.list.value };
    setTimeout(function () {
      $("#text").value = ""; picks.dataset = []; picks.exclude = [];
      form.elements.tab.value = keep.tab; form.elements.list.value = keep.list;
      run();
    }, 0);
  });
  $("#more").addEventListener("click", function () { shownCount += PER; run(false); });
  $("#moreroom").addEventListener("click", function () { roomsShown += 20; run(false); });
  document.addEventListener("click", function (ev) {
    const tab = ev.target.closest('[role="tab"]');
    if (tab) { form.elements.tab.value = tab.dataset.tab; run(false); return; }
    const view = ev.target.closest("[data-list]");
    if (view) { form.elements.list.value = view.dataset.list; run(false); return; }
    const sort = ev.target.closest("[data-sort]");
    if (sort) { $("#sort").value = sort.dataset.sort; run(); return; }
    const b = ev.target.closest("[data-pick]");      // Pick / Leave out on a card or a row
    if (!b) return;
    const kind = b.dataset.pick, other = kind === "dataset" ? "exclude" : "dataset", id = b.dataset.id;
    const on = picks[kind].includes(id);
    picks[kind] = picks[kind].filter(function (x) { return x !== id; });
    picks[other] = picks[other].filter(function (x) { return x !== id; });
    if (!on) picks[kind].push(id);
    run(false);
  });
  // the tabs, with the arrow keys
  $$('[role="tab"]').forEach(function (b, i, all) {
    b.addEventListener("keydown", function (ev) {
      if (ev.key !== "ArrowRight" && ev.key !== "ArrowLeft") return;
      const next = all[(i + 1) % all.length];
      next.focus(); next.click();
    });
  });
  run();
})();
