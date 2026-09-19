"""Stylesheet and client scripts for the generated site.

Kept apart from build_site.py so the templates stay readable. The palette and
typography match docs/PLAN.md's published version, so the project reads as one
thing.
"""

STYLE = """
/* Planning Citation Metrics. Light palette in :root, redefined for dark twice
   so an explicit choice wins over the OS in both directions. */
:root{
  --bg:#F6F7F5; --surface:#FFFFFF; --sunk:#EEF1F0; --ink:#1B232E; --muted:#5C6673;
  --rule:#D7DDE2; --rule-soft:#E6EBEE;
  --accent:#0E6B71; --accent-soft:#DDEEEE; --accent-ink:#0A4F54;
  --amber:#A96F12; --amber-soft:#F6EBD3;
  --warn:#9C3B2E; --bar:#BFD4D5;
}
@media (prefers-color-scheme: dark){
  :root:not([data-theme="light"]){
    --bg:#11161C; --surface:#181F27; --sunk:#1E262F; --ink:#E5EAEE; --muted:#97A2AD;
    --rule:#2B3540; --rule-soft:#222B34;
    --accent:#56B9BE; --accent-soft:#16333A; --accent-ink:#8FD6D9;
    --amber:#E2A83E; --amber-soft:#3A2E14;
    --warn:#E07A6A; --bar:#2F4A4D;
  }
}
:root[data-theme="dark"]{
  --bg:#11161C; --surface:#181F27; --sunk:#1E262F; --ink:#E5EAEE; --muted:#97A2AD;
  --rule:#2B3540; --rule-soft:#222B34;
  --accent:#56B9BE; --accent-soft:#16333A; --accent-ink:#8FD6D9;
  --amber:#E2A83E; --amber-soft:#3A2E14;
  --warn:#E07A6A; --bar:#2F4A4D;
}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--ink);
  font:16px/1.55 "Source Sans 3",system-ui,-apple-system,"Segoe UI",sans-serif;
  -webkit-font-smoothing:antialiased}
a{color:var(--accent);text-decoration-thickness:1px;text-underline-offset:2px}
a:focus-visible,button:focus-visible,input:focus-visible,select:focus-visible,
th[role=button]:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
img{max-width:100%}

/* ---------------------------------------------------------------- chrome */
.bar{border-bottom:1px solid var(--rule);background:var(--surface)}
.bar-in{max-width:1100px;margin:0 auto;padding:12px 24px;display:flex;
  align-items:center;gap:10px 28px;flex-wrap:wrap}
.wordmark{font:600 15px/1 "Source Serif 4",Georgia,serif;letter-spacing:-.01em;
  color:var(--ink);text-decoration:none;white-space:nowrap}
.wordmark span{color:var(--accent)}
.bar nav{display:flex;gap:18px;font-size:14px;margin-left:auto;flex-wrap:wrap}
.bar nav a{color:var(--muted);text-decoration:none}
.bar nav a:hover,.bar nav a[aria-current=page]{color:var(--ink)}
main{max-width:1100px;margin:0 auto;padding:36px 24px 72px}
/* The narrow column IS the reading measure: paragraphs inside it carry no
   second limit of their own, because a ch-based cap lands short of the column
   and reads as a broken right edge. Lines run long at this width, so prose
   here gets extra leading, which is what keeps a long line trackable. */
main.narrow{max-width:720px}
main.narrow p,main.narrow .lede,main.narrow ul,main.narrow ol{max-width:none}
main.narrow p,main.narrow li{line-height:1.68}
footer{border-top:1px solid var(--rule);margin-top:48px}
footer .bar-in{display:block;color:var(--muted);font-size:13.5px;padding:20px 24px 40px}
footer p{margin:0 0 6px;max-width:75ch}

/* ------------------------------------------------------------- typography */
h1{font:600 clamp(26px,3.4vw,36px)/1.15 "Source Serif 4",Georgia,serif;
  margin:0 0 8px;letter-spacing:-.01em;text-wrap:balance}
h2{font:600 21px/1.25 "Source Serif 4",Georgia,serif;margin:36px 0 12px;text-wrap:balance}
h3{font:600 15px/1.3 "Source Sans 3",sans-serif;margin:24px 0 8px}
p{margin:0 0 14px;max-width:72ch}
.lede{font-size:17.5px;color:var(--muted);max-width:66ch}
.eyebrow{font:600 11.5px/1 "Source Sans 3",sans-serif;letter-spacing:.12em;
  text-transform:uppercase;color:var(--accent);margin:0 0 10px}
.sub{color:var(--muted);font-size:15px;margin:0 0 4px}
.mono{font-family:"IBM Plex Mono",ui-monospace,Menlo,monospace}
code{font-family:"IBM Plex Mono",ui-monospace,Menlo,monospace;font-size:.88em;
  background:var(--sunk);padding:1px 5px;border-radius:3px}
.nums{font-variant-numeric:tabular-nums}

/* ----------------------------------------------------------------- search */
.search{position:relative;flex:1 1 260px;max-width:420px}
.search input{width:100%;padding:8px 12px;font:15px/1.3 "Source Sans 3",sans-serif;
  color:var(--ink);background:var(--bg);border:1px solid var(--rule);border-radius:5px}
.search input::placeholder{color:var(--muted)}
.hero-search{max-width:none;flex:none;margin:0 0 8px}
.hero-search input{padding:13px 16px;font-size:17px;background:var(--surface)}
.results{position:absolute;z-index:20;left:0;right:0;top:calc(100% + 5px);
  background:var(--surface);border:1px solid var(--rule);border-radius:6px;
  box-shadow:0 10px 28px rgba(16,24,32,.14);max-height:min(60vh,420px);overflow-y:auto}
.results:empty,.results[hidden]{display:none}
.results a{display:flex;justify-content:space-between;gap:14px;padding:9px 13px;
  text-decoration:none;color:var(--ink);border-bottom:1px solid var(--rule-soft);font-size:14.5px}
.results a:last-child{border-bottom:0}
.results a:hover,.results a.on{background:var(--accent-soft)}
.results .where{color:var(--muted);font-size:13px;text-align:right;flex:0 1 auto}
.results .none{padding:10px 13px;color:var(--muted);font-size:14px}

/* ------------------------------------------------------------------ tiles */
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(142px,1fr));
  gap:1px;background:var(--rule);border:1px solid var(--rule);border-radius:5px;
  overflow:hidden;margin:0 0 26px}
.tile{background:var(--surface);padding:13px 15px}
.tile .n{font:500 26px/1.05 "Source Serif 4",Georgia,serif;font-variant-numeric:tabular-nums;
  display:block;margin-bottom:5px}
.tile .n small{font-size:14px;color:var(--muted);font-family:"Source Sans 3",sans-serif}
.tile .l{font-size:12.5px;color:var(--muted);line-height:1.35}

/* ------------------------------------------------------------------ table */
.tw{overflow-x:auto;border:1px solid var(--rule);border-radius:5px;background:var(--surface)}
table{border-collapse:collapse;width:100%;font-size:14.5px;font-variant-numeric:tabular-nums}
th,td{text-align:left;padding:9px 13px;border-bottom:1px solid var(--rule-soft);
  vertical-align:baseline;white-space:nowrap}
th{font:600 11.5px/1.3 "Source Sans 3",sans-serif;letter-spacing:.07em;text-transform:uppercase;
  color:var(--muted);border-bottom:1px solid var(--rule);position:sticky;top:0;
  background:var(--surface);z-index:1}
th[role=button]{cursor:pointer;user-select:none}
th[role=button]::after{content:"";opacity:.45;margin-left:5px}
th[aria-sort=ascending]::after{content:"\\2191";opacity:1}
th[aria-sort=descending]::after{content:"\\2193";opacity:1}
tbody tr:last-child td{border-bottom:0}
tbody tr:hover td{background:var(--sunk)}
td.n,th.n{text-align:right}
td.name{white-space:normal;min-width:11rem}
.rank{color:var(--muted);font-size:13px}

/* -------------------------------------------------------------------- beta */
.beta{background:var(--amber-soft);border:1px solid var(--amber);border-left-width:4px;
  border-radius:4px;padding:15px 18px;margin:0 0 30px;max-width:none}
.beta h2{font:600 12px/1 "Source Sans 3",sans-serif;letter-spacing:.14em;text-transform:uppercase;
  color:var(--amber);margin:0 0 9px}
.beta p{margin:0 0 8px;font-size:15px;max-width:none}
.beta p:last-child{margin:0}
.beta a{color:var(--amber);font-weight:600}
.tag{display:inline-block;font:600 9.5px/1 "Source Sans 3",sans-serif;letter-spacing:.1em;
  text-transform:uppercase;padding:3px 5px;border-radius:2px;background:var(--amber-soft);
  color:var(--amber);vertical-align:2px;margin-left:7px}

/* ------------------------------------------------------------------ badges */
.pill{display:inline-block;font:600 10.5px/1 "Source Sans 3",sans-serif;letter-spacing:.07em;
  text-transform:uppercase;padding:4px 7px;border-radius:3px;vertical-align:2px;white-space:nowrap}
.pill-gs{background:var(--accent-soft);color:var(--accent-ink)}
.pill-oa{background:var(--amber-soft);color:var(--amber)}
.note{border-left:3px solid var(--amber);background:var(--amber-soft);padding:11px 15px;
  margin:0 0 20px;font-size:14.5px;max-width:72ch;border-radius:0 3px 3px 0}
.note p{margin:0}
.note a{color:var(--amber)}

/* ----------------------------------------------------------- person sheet */
.sheet{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,300px);gap:40px;align-items:start}
@media (max-width:820px){.sheet{grid-template-columns:minmax(0,1fr);gap:28px}}
.metric{display:flex;flex-wrap:wrap;gap:8px 32px;align-items:flex-end;margin:0 0 6px}
.metric .big{font:500 44px/1 "Source Serif 4",Georgia,serif;font-variant-numeric:tabular-nums}
.metric .side{font-size:14px;color:var(--muted);padding-bottom:5px}
.metric .side b{color:var(--ink);font-weight:600;font-size:17px}
.pct{margin:18px 0 0;max-width:430px}
.pct-row{display:grid;grid-template-columns:8.5rem 1fr 3.2rem;gap:10px;align-items:center;
  margin-bottom:7px;font-size:13.5px}
.pct-row .lab{color:var(--muted)}
.pct-track{display:block;height:7px;background:var(--sunk);border-radius:4px;overflow:hidden}
.pct-fill{display:block;height:7px;background:var(--bar);border-radius:4px}
.pct-fill.hi{background:var(--accent)}
.pct-row .v{text-align:right;font-variant-numeric:tabular-nums}
dl.facts{margin:0;display:grid;grid-template-columns:auto 1fr;gap:5px 14px;font-size:14.5px}
dl.facts dt{color:var(--muted)}
dl.facts dd{margin:0}
.links{list-style:none;padding:0;margin:14px 0 0;font-size:14.5px}
.links li{margin-bottom:5px}
.card{background:var(--surface);border:1px solid var(--rule);border-radius:5px;padding:16px 18px}
.card h3{margin-top:0}
.spark{margin:22px 0 0;max-width:330px}
.spark svg{display:block;width:100%;height:auto;color:var(--ink)}
.spark figcaption{font-size:13px;color:var(--muted);margin-top:7px}

/* ------------------------------------------------------------------- misc */
.controls{display:flex;flex-wrap:wrap;gap:10px 16px;align-items:center;margin:0 0 16px;font-size:14px}
.controls label{color:var(--muted);display:flex;align-items:center;gap:6px}
.controls select{font:14px "Source Sans 3",sans-serif;color:var(--ink);background:var(--surface);
  border:1px solid var(--rule);border-radius:4px;padding:5px 8px}
.cols{columns:2 16rem;column-gap:32px;font-size:14.5px}
.cols a{display:block;margin-bottom:5px;break-inside:avoid}
.count{color:var(--muted);font-size:14px;margin:0 0 14px}
@media (prefers-reduced-motion:reduce){*{transition:none!important;animation:none!important}}

/* homepage: status strip, source mix, two/three-column lists, folds */
.status{margin:26px 0 6px}
.status .mix{display:flex;height:8px;border-radius:4px;overflow:hidden;margin:14px 0 8px;background:var(--sunk)}
.seg.gs,.k.gs{background:var(--accent)}.seg.oa,.k.oa{background:var(--amber)}.seg.pop,.k.pop{background:var(--muted);opacity:.55}
.mixkey{margin:0!important;font-size:13px}.k{display:inline-block;width:9px;height:9px;border-radius:2px;margin:0 5px 0 0;vertical-align:baseline}
.cols2{display:grid;grid-template-columns:1fr 1fr;gap:22px}
.col h3{font-size:15px;margin:0 0 8px;font-weight:600;color:var(--ink)}
.count{display:inline-block;margin-left:6px;padding:0 7px;border-radius:9px;background:var(--sunk);color:var(--muted);font-size:12.5px;font-weight:600;vertical-align:2px}
ul.plain{list-style:none;margin:0;padding:0}ul.plain li{padding:7px 0;border-top:1px solid var(--rule-soft);line-height:1.35}
ul.plain li:first-child{border-top:0}
.d{display:block;font-size:13px;color:var(--muted)}.d b{color:var(--ink);font-weight:600}
.when{float:right;font-size:12.5px;color:var(--muted)}
ul.plain.two{columns:2;column-gap:26px}ul.plain.two li{break-inside:avoid}
details.fold{border:1px solid var(--rule);border-radius:8px;padding:0 16px;margin:10px 0;background:var(--surface)}
details.fold summary{cursor:pointer;padding:11px 0;font-weight:600;list-style:none;color:var(--ink)}details.fold summary::-webkit-details-marker{display:none}
details.fold summary::before{content:"\\25B8";display:inline-block;width:16px;color:var(--muted)}details.fold[open] summary::before{content:"\\25BE"}
details.fold[open]{padding-bottom:12px}
@media(max-width:720px){.cols2{grid-template-columns:1fr}ul.plain.two{columns:1}}
"""

SEARCH_JS = """
/* Client-side search over a prebuilt index. Linear scan: ~1,200 short records
   is instant and needs no library. */
(function () {
  var INDEX = null, LOADING = null;
  function load(base) {
    if (INDEX) return Promise.resolve(INDEX);
    if (!LOADING) LOADING = fetch(base + "search.json")
      .then(function (r) { return r.json(); })
      .then(function (d) { INDEX = d; return d; })
      .catch(function () { return []; });
    return LOADING;
  }
  function norm(s) {
    return (s || "").normalize("NFKD").replace(/[\\u0300-\\u036f]/g, "")
      .toLowerCase().replace(/[^a-z0-9 ]+/g, " ").replace(/\\s+/g, " ").trim();
  }
  function score(rec, q) {
    var n = rec.n;                                  // normalized name
    if (n === q) return 0;
    if (n.indexOf(q) === 0) return 1;               // starts with
    var parts = n.split(" ");
    for (var i = 0; i < parts.length; i++) if (parts[i].indexOf(q) === 0) return 2;
    if (n.indexOf(q) > -1) return 3;
    if (rec.w && rec.w.indexOf(q) > -1) return 4;   // department words
    return -1;
  }
  function wire(box) {
    var input = box.querySelector("input"), list = box.querySelector(".results");
    var base = box.getAttribute("data-base") || "";
    var rows = [], cursor = -1;
    function render(items, q) {
      list.innerHTML = "";
      if (!q) { list.hidden = true; return; }
      if (!items.length) {
        list.hidden = false;
        list.innerHTML = '<div class="none">Nothing matches that name.</div>';
        return;
      }
      items.forEach(function (rec) {
        var a = document.createElement("a");
        a.href = base + rec.u;
        a.innerHTML = '<span>' + rec.t + '</span><span class="where">' + (rec.d || "") + '</span>';
        list.appendChild(a);
      });
      rows = Array.prototype.slice.call(list.querySelectorAll("a"));
      cursor = -1;
      list.hidden = false;
    }
    function run() {
      var q = norm(input.value);
      if (!q) { render([], ""); return; }
      load(base).then(function (data) {
        var hits = [];
        for (var i = 0; i < data.length; i++) {
          var s = score(data[i], q);
          if (s > -1) hits.push([s, data[i]]);
        }
        hits.sort(function (a, b) { return a[0] - b[0] || (b[1].c || 0) - (a[1].c || 0); });
        render(hits.slice(0, 12).map(function (h) { return h[1]; }), q);
      });
    }
    input.addEventListener("input", run);
    input.addEventListener("focus", function () { if (input.value) run(); });
    input.addEventListener("keydown", function (e) {
      if (e.key === "Escape") { list.hidden = true; input.blur(); return; }
      if (!rows.length) return;
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        if (cursor > -1) rows[cursor].classList.remove("on");
        cursor = (cursor + (e.key === "ArrowDown" ? 1 : rows.length - 1)) % rows.length;
        rows[cursor].classList.add("on");
        rows[cursor].scrollIntoView({ block: "nearest" });
      } else if (e.key === "Enter" && cursor > -1) {
        e.preventDefault(); rows[cursor].click();
      }
    });
    document.addEventListener("click", function (e) {
      if (!box.contains(e.target)) list.hidden = true;
    });
  }
  document.querySelectorAll(".search").forEach(wire);
  // "/" focuses search, the way most reference sites behave.
  document.addEventListener("keydown", function (e) {
    if (e.key === "/" && !/^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement.tagName)) {
      var i = document.querySelector(".search input");
      if (i) { e.preventDefault(); i.focus(); }
    }
  });
})();
"""

TABLE_JS = """
/* Column sorting and filtering for the ranking tables. */
(function () {
  function val(td) {
    var v = td.getAttribute("data-v");
    if (v !== null) return parseFloat(v);
    var t = td.textContent.replace(/[,%]/g, "").trim();
    var n = parseFloat(t);
    return isNaN(n) ? t.toLowerCase() : n;
  }
  document.querySelectorAll("table[data-sortable]").forEach(function (table) {
    var tbody = table.tBodies[0];
    table.querySelectorAll("th").forEach(function (th, i) {
      if (th.hasAttribute("data-nosort")) return;
      th.setAttribute("role", "button");
      th.tabIndex = 0;
      function sort() {
        var desc = th.getAttribute("aria-sort") !== "descending";
        if (th.hasAttribute("data-text") && !th.getAttribute("aria-sort")) desc = false;
        table.querySelectorAll("th").forEach(function (o) { o.removeAttribute("aria-sort"); });
        th.setAttribute("aria-sort", desc ? "descending" : "ascending");
        var rows = Array.prototype.slice.call(tbody.rows);
        rows.sort(function (a, b) {
          var x = val(a.cells[i]), y = val(b.cells[i]);
          if (x === y) return 0;
          if (x === "" || x === null) return 1;
          if (y === "" || y === null) return -1;
          return (x > y ? 1 : -1) * (desc ? -1 : 1);
        });
        rows.forEach(function (r) { tbody.appendChild(r); });
        renumber(table);
      }
      th.addEventListener("click", sort);
      th.addEventListener("keydown", function (e) {
        if (e.key === "Enter" || e.key === " ") { e.preventDefault(); sort(); }
      });
    });
  });
  function renumber(table) {
    var i = 0;
    Array.prototype.forEach.call(table.tBodies[0].rows, function (r) {
      if (r.hidden) return;
      var c = r.querySelector(".rank");
      if (c) c.textContent = ++i;
    });
  }
  /* Filters. Every select pointing at a table is applied together, so Rank and
     Country combine instead of overwriting each other, and the whole set is
     applied on load as well as on change: coming back to the page restores the
     select values but not the filtered rows. */
  var byTable = {};
  document.querySelectorAll("[data-filter-for]").forEach(function (sel) {
    var id = sel.getAttribute("data-filter-for");
    (byTable[id] = byTable[id] || []).push(sel);
  });
  Object.keys(byTable).forEach(function (id) {
    var table = document.getElementById(id);
    if (!table) return;
    var sels = byTable[id];
    function apply() {
      var shown = 0;
      Array.prototype.forEach.call(table.tBodies[0].rows, function (r) {
        var ok = sels.every(function (sel) {
          var want = sel.value;
          return !want || r.getAttribute("data-" + sel.getAttribute("data-filter-key")) === want;
        });
        r.hidden = !ok;
        if (ok) shown++;
      });
      var out = document.getElementById(id + "-count");
      if (out) out.textContent = shown.toLocaleString();
      renumber(table);
    }
    sels.forEach(function (sel) { sel.addEventListener("change", apply); });
    apply();
    // Back/forward can restore the select values after this script has run.
    window.addEventListener("pageshow", apply);
  });
})();
"""
