// The website's only script. Every page works without it; it adds:
//   1. tooltips on the charts (from each mark's <title>), on hover and on keyboard focus;
//   2. sorting: click a column's name in a table of class "sortable";
//   3. the search of the datasets and spaces pages, over the data inside the page (data.py, search_data);
//      its filters are kept in the address, so a link reopens the same search.
(function () {
  "use strict";

  // ------------------------------------------------------------------ 1. tooltips
  const tip = document.createElement("div");
  tip.className = "tip";
  tip.hidden = true;
  document.body.appendChild(tip);

  function place(x, y) {
    tip.style.left = Math.max(8, Math.min(x + 12, innerWidth - tip.offsetWidth - 8)) + "px";
    const above = y - tip.offsetHeight - 10;
    tip.style.top = (above > 8 ? above : y + 18) + "px";
  }
  document.querySelectorAll("svg.chart .hit, svg.chart .pt").forEach(function (mark) {
    const title = mark.querySelector("title");
    if (!title) return;
    const text = title.textContent;
    title.remove();                                   // else the browser shows its own tooltip as well
    mark.setAttribute("aria-label", text);
    function show(x, y) { tip.textContent = text; tip.hidden = false; place(x, y); }
    mark.addEventListener("pointermove", function (ev) { show(ev.clientX, ev.clientY); });
    mark.addEventListener("pointerleave", function () { tip.hidden = true; });
    mark.addEventListener("focus", function () {
      const r = mark.getBoundingClientRect();
      show(r.left + r.width / 2, r.top);
    });
    mark.addEventListener("blur", function () { tip.hidden = true; });
  });

  // ------------------------------------------------------------------ 2. sorting
  // A cell's data-sort holds its value; numbers sort as numbers, empty cells always last.
  function key(row, col) {
    const cell = row.cells[col];
    const raw = cell.dataset.sort !== undefined ? cell.dataset.sort : cell.textContent.trim();
    const n = Number(raw);
    return raw === "" ? null : (isNaN(n) ? raw.toLowerCase() : n);
  }
  document.querySelectorAll("table.sortable").forEach(function (table) {
    table.querySelectorAll("thead th").forEach(function (th, col) {
      th.tabIndex = 0;
      function sort() {
        const up = th.getAttribute("aria-sort") !== "ascending";
        table.querySelectorAll("thead th").forEach(function (h) { h.removeAttribute("aria-sort"); });
        th.setAttribute("aria-sort", up ? "ascending" : "descending");
        const body = table.tBodies[0];
        const rows = Array.from(body.rows);
        rows.sort(function (a, b) {
          const x = key(a, col), y = key(b, col);
          if (x === null || y === null) return (x === null) - (y === null);
          return (x < y ? -1 : x > y ? 1 : 0) * (up ? 1 : -1);
        });
        rows.forEach(function (r) { body.appendChild(r); });
      }
      th.addEventListener("click", sort);
      th.addEventListener("keydown", function (ev) {
        if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); sort(); }
      });
    });
  });

  // ------------------------------------------------------------------ 3. the search
  const holder = document.getElementById("search-data");
  const form = document.getElementById("filters");
  if (!holder || !form) return;
  const data = JSON.parse(holder.textContent);
  const col = {};
  data.columns.forEach(function (c, i) { col[c] = i; });
  const isSpacesPage = !!document.querySelector("tr[data-i]");

  function read() {
    const f = {};
    new FormData(form).forEach(function (v, k) { f[k] = String(v).trim(); });
    f.text = (f.text || "").toLowerCase();
    return f;
  }

  // Filters on the space (one value per space).
  function spaceKept(s, f) {
    if (f.origin && s.origin !== f.origin) return false;
    if (f.type && s.type !== f.type) return false;
    if (isSpacesPage && f.text &&
        (s.label + " " + s.id + " " + s.dataset).toLowerCase().indexOf(f.text) < 0) return false;
    if (f.vol_from && !(s.volume !== null && s.volume >= Number(f.vol_from))) return false;
    if (f.vol_to && !(s.volume !== null && s.volume <= Number(f.vol_to))) return false;
    return true;
  }

  // Filters on a count row (IRs that share a format, grade, T30 bin and licence uses), as `rirdb search`:
  // without a format, primary rows only; mono takes mono rows and the W channel where a space has no mono row.
  function rowKept(r, f) {
    if (!f.format) { if (!r[col.primary]) return false; }
    else if (f.format === "mono") { if (r[col.format] !== "mono" && !r[col.mono_w]) return false; }
    else if (r[col.format] !== f.format) return false;
    if (f.grade) {                                     // "A" means A or better; no grade never passes
      const g = data.grades.indexOf(r[col.grade]);
      if (g < 0 || g > data.grades.indexOf(f.grade)) return false;
    }
    if (f.use && !r[col[f.use]]) return false;
    if (f.t30_from || f.t30_to) {
      const bin = r[col.t30];
      if (bin < 0) return false;                       // no valid T30
      if (f.t30_from && bin < data.t30_edges.indexOf(Number(f.t30_from))) return false;
      if (f.t30_to && bin + 1 > data.t30_edges.indexOf(Number(f.t30_to))) return false;
    }
    return true;
  }

  function cli(f) {
    const parts = ["rirdb search"];
    if (f.use) parts.push("--use " + f.use);
    if (f.format) parts.push("--format " + f.format);
    if (f.origin) parts.push("--origin " + f.origin);
    if (f.grade) parts.push("--grade " + f.grade);
    let line = parts.join(" ");
    const only = [];
    if (f.type) only.push("space type");
    if (f.t30_from || f.t30_to) only.push("T30");
    if (f.vol_from || f.vol_to) only.push("volume");
    if (only.length) line += "\n# " + only.join(", ") + ": filters of this page only, not of the command line yet";
    return line;
  }

  const fmt = function (n) { return n.toLocaleString("en"); };
  const result = document.getElementById("result");
  const cliBox = document.getElementById("cli");

  function run() {
    const f = read();
    const irFilter = ["format", "origin", "grade", "use", "type", "t30_from", "t30_to", "vol_from", "vol_to"]
      .some(function (k) { return f[k]; });
    const perSpace = new Map();                        // space index -> IRs kept
    const spaceOk = data.spaces.map(function (s) { return spaceKept(s, f); });
    data.counts.forEach(function (r) {
      const i = r[col.space];
      if (spaceOk[i] && rowKept(r, f)) perSpace.set(i, (perSpace.get(i) || 0) + r[col.n]);
    });

    let irs = 0, spaces = 0;
    const datasets = new Set();
    if (isSpacesPage) {
      document.querySelectorAll("tr[data-i]").forEach(function (tr) {
        const i = Number(tr.dataset.i), n = perSpace.get(i) || 0;
        tr.cells[3].textContent = fmt(n);
        tr.cells[3].dataset.sort = n;
        tr.classList.toggle("off", n === 0);
        if (n) { irs += n; spaces += 1; datasets.add(data.spaces[i].dataset); }
      });
      document.querySelectorAll("svg .pt").forEach(function (pt) {
        pt.classList.toggle("off", !perSpace.get(Number(pt.dataset.i)));
      });
    } else {
      const byDataset = new Map();                     // dataset -> [IRs, spaces]
      perSpace.forEach(function (n, i) {
        const d = data.spaces[i].dataset, v = byDataset.get(d) || [0, 0];
        byDataset.set(d, [v[0] + n, v[1] + 1]);
      });
      document.querySelectorAll("tr[data-dataset]").forEach(function (tr) {
        const v = byDataset.get(tr.dataset.dataset) || [0, 0];
        const textOk = !f.text || tr.dataset.text.indexOf(f.text) >= 0;
        tr.cells[3].textContent = fmt(v[0]); tr.cells[3].dataset.sort = v[0];
        tr.cells[4].textContent = fmt(v[1]); tr.cells[4].dataset.sort = v[1];
        const on = textOk && v[0] > 0;
        tr.classList.toggle("off", !on);
        if (on) { irs += v[0]; spaces += v[1]; datasets.add(tr.dataset.dataset); }
      });
      // Catalogued datasets have no IRs yet: only the text search applies, and only without other filters.
      const section = document.getElementById("catalogued");
      if (section) {
        section.classList.toggle("off", irFilter);
        section.querySelectorAll("tr[data-text]").forEach(function (tr) {
          tr.classList.toggle("off", !!f.text && tr.dataset.text.indexOf(f.text) < 0);
        });
      }
    }
    result.innerHTML = "";
    [[irs, " IRs in "], [spaces, " spaces of "], [datasets.size, " datasets"]].forEach(function (p) {
      const b = document.createElement("b");
      b.textContent = fmt(p[0]);
      result.appendChild(b);
      result.appendChild(document.createTextNode(p[1]));
    });
    if (!irs) result.appendChild(document.createTextNode(". Nothing matches: loosen a filter."));
    cliBox.textContent = cli(f);
    cliBox.hidden = !irFilter;
    remember(f);
  }

  // The filters also live in the address (datasets.html?format=binaural&grade=A), so a search can be shared.
  new URLSearchParams(location.search).forEach(function (v, k) {
    if (form.elements[k]) form.elements[k].value = v;
  });
  function remember(f) {
    const q = new URLSearchParams();
    Object.keys(f).forEach(function (k) { if (f[k]) q.set(k, f[k]); });
    try { history.replaceState(null, "", q.toString() ? "?" + q : location.pathname); } catch (err) { /* a file: page */ }
  }

  form.addEventListener("input", run);
  form.addEventListener("change", run);
  form.addEventListener("reset", function () { setTimeout(run, 0); });
  form.addEventListener("submit", function (ev) { ev.preventDefault(); });
  run();
})();
