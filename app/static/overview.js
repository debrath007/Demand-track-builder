/* Account overview: one ring over every position, split by stage or by business unit. Every click (a
   slice, a row beside it, a box in the workflow) narrows the page; the demands behind the numbers are
   listed once something is narrowed. Data comes from the page as window.OV (leadership_dashboard). */
(function () {
  var OV = window.OV;
  if (!OV) return;
  var C = ['#2a78d6', '#eb6834', '#1baf7a', '#eda100', '#e87ba4', '#008300', '#6250d6', '#e34948'];
  var D = OV.rows, ORDER = OV.order, MEANS = OV.means, L = OV.layout;
  var NAME = { stage: 'Stage', sub: 'Sub-stage', bu: 'Business unit', type: 'Type', practice: 'Practice', pstart: 'Timing', escd: 'Escalations', costing: 'Costing' };
  var HINT = { sub: 'where exactly inside this stage', bu: 'which business unit they belong to', stage: 'how far along they are',
    type: 'new or replacement, billable or not', practice: 'the skill area' };
  var dim = 'stage', F = {}, openRef = null, showFlow = location.hash === '#workflow';
  var $ = function (id) { return document.getElementById(id); };

  function esc(t) { return String(t == null ? '' : t).replace(/[&<>"]/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]; }); }
  function money(v) { return '$' + Math.round(v).toLocaleString('en-US'); }
  function rowsFor(skip) { return D.filter(function (x) { for (var k in F) { if (k !== skip && x[k] !== F[k]) return false; } return true; }); }
  function count(rows, key) {
    var m = {}; rows.forEach(function (x) { m[x[key]] = (m[x[key]] || 0) + 1; });
    var keys = ORDER[key] ? ORDER[key].filter(function (k) { return m[k]; }) : [];
    Object.keys(m).forEach(function (k) { if (keys.indexOf(k) < 0) keys.push(k); });
    return keys.map(function (k) { return [k, m[k]]; });
  }
  function toggle(key, val) {
    if (F[key] === val) { delete F[key]; } else { F[key] = val; }
    if (key === 'stage') delete F.sub;
    draw();
  }

  function isOpen(x) { return x.open; }

  // The ring counts open positions only; joined and abandoned ones stay out of it (and out of its legend).
  function ring() {
    var base = rowsFor(dim).filter(isOpen), total = base.length, sel = F[dim], R = 74, LEN = 2 * Math.PI * R, start = 0;
    var keys = (dim === 'stage' ? OV.ring_stages : ORDER[dim]).slice();
    base.forEach(function (x) { if (keys.indexOf(x[dim]) < 0) keys.push(x[dim]); });
    var items = keys.map(function (k, j) { return { k: k, n: base.filter(function (x) { return x[dim] === k; }).length, c: C[j] || '#8B877E' }; });
    var h = '<g transform="rotate(-90 100 100)" fill="none">';
    items.forEach(function (it) {
      if (!it.n) return;
      var len = it.n / total * LEN, vis = Math.max(len - 2, 1);
      h += '<circle class="slice" data-key="' + dim + '" data-val="' + esc(it.k) + '" cx="100" cy="100" r="' + R + '" stroke="' + it.c + '" stroke-width="' + (sel === it.k ? 34 : 26) + '" opacity="' + (sel && sel !== it.k ? .35 : 1) + '" stroke-dasharray="' + vis.toFixed(2) + ' ' + (LEN - vis).toFixed(2) + '" stroke-dashoffset="' + (-start).toFixed(2) + '"><title>' + esc(it.k) + ': ' + it.n + '</title></circle>';
      start += len;
    });
    h += '</g>' + sliceLabels(items, total, R);
    var rows = rowsFor(), shown = rows.filter(isOpen).length, allOpen = D.filter(isOpen).length;
    h += '<text x="100" y="97" text-anchor="middle" class="ov-total">' + shown + '</text>';
    h += '<text x="100" y="116" text-anchor="middle" class="ov-unit">' + (shown < allOpen ? 'of ' + allOpen + ' open' : 'open positions') + '</text>';
    $('ov-ring').innerHTML = h;
    $('ov-legend').innerHTML = items.map(function (it) {
      return '<li data-key="' + dim + '" data-val="' + esc(it.k) + '" class="' + (sel === it.k ? 'on' : '') + (it.n ? '' : ' zero') + '"><span class="sw" style="background:' + it.c + '"></span><span>' + esc(it.k) + '</span><span class="num">' + it.n + '</span><span class="pct">' + (total ? Math.round(it.n / total * 100) : 0) + '%</span>' + (MEANS[it.k] ? '<span class="d">' + esc(MEANS[it.k]) + '</span>' : '') + '</li>';
    }).join('');
    return rows;
  }

  // Each slice's name, written along the ring inside its own colour. Text on the lower half runs the other
  // way round so it never reads upside down; a name too long for its slice is left to the legend.
  function sliceLabels(items, total, R) {
    var FONT = 10, GAP = 6, a0 = 0, h = '';
    function pt(a) { return (100 + R * Math.sin(a)).toFixed(2) + ' ' + (100 - R * Math.cos(a)).toFixed(2); }
    items.forEach(function (it, j) {
      if (!it.n) return;
      var span = it.n / total * 2 * Math.PI, mid = a0 + span / 2, k = String(it.k);
      a0 += span;
      var need = k.length * FONT * (k === k.toUpperCase() ? 0.72 : 0.58) + GAP;
      if (need > R * span - GAP) return;
      var half = Math.min(span, 1.9 * Math.PI) / 2, s = mid - half, e = mid + half, big = half * 2 > Math.PI ? 1 : 0;
      var down = Math.cos(mid) < 0, id = 'ov-lbl-' + j;
      var d = down ? 'M' + pt(e) + ' A' + R + ' ' + R + ' 0 ' + big + ' 0 ' + pt(s) : 'M' + pt(s) + ' A' + R + ' ' + R + ' 0 ' + big + ' 1 ' + pt(e);
      h += '<path id="' + id + '" d="' + d + '" fill="none"/><text class="ov-slice-label" fill="' + inkOn(it.c) + '" opacity="' + (F[dim] && F[dim] !== it.k ? .5 : 1) + '">'
        + '<textPath href="#' + id + '" startOffset="50%" text-anchor="middle" dominant-baseline="central">' + esc(k) + '</textPath></text>';
    });
    return h;
  }

  // White on dark slices, near-black on light ones (the yellow, the pink).
  function inkOn(hex) {
    var n = parseInt(hex.slice(1), 16), r = n >> 16, g = (n >> 8) & 255, b = n & 255;
    return (0.299 * r + 0.587 * g + 0.114 * b) > 160 ? '#1d1b17' : '#fff';
  }

  function bars(key) {
    var base = rowsFor(key), pairs = count(base, key), max = base.length || 1;
    if (!pairs.length) return '';
    return '<div class="grp"><div class="t"><b>By ' + NAME[key].toLowerCase() + '</b> · ' + HINT[key] + '</div>' + pairs.map(function (p) {
      return '<div class="bar ' + (F[key] === p[0] ? 'on' : '') + '" data-key="' + key + '" data-val="' + esc(p[0]) + '"><span>' + esc(p[0]) + '</span><span class="track"><span class="fill" style="width:' + (p[1] / max * 100) + '%"></span></span><span class="n">' + p[1] + '</span></div>';
    }).join('') + '</div>';
  }

  function detail(rows) {
    var lost = 0, late = 0, open = 0, norate = 0, escd = 0, cost = 0, costn = 0, ks = Object.keys(F);
    rows.forEach(function (x) { lost += x.lost || 0; late += x.late ? 1 : 0; open += x.open ? 1 : 0; norate += x.norate ? 1 : 0; escd += x.esc.length ? 1 : 0; cost += x.cost || 0; costn += x.cost_active ? 1 : 0; });
    var h = '<div class="eyebrow">' + (ks.length ? 'You are looking at · click a tag to remove it' : 'You are looking at') + '</div>';
    if (ks.length) {
      h += '<div class="chips">' + ks.map(function (k) { return '<button class="chip-x" data-key="' + k + '" data-val="' + esc(F[k]) + '">' + esc(F[k]) + ' ✕</button>'; }).join('') + '<button class="chip-x clear" id="ov-clear">Clear all</button></div>';
    } else {
      h += '<h2>The whole account</h2><div class="small muted">Click any row below, or a slice, to narrow down.</div>';
    }
    h += '<div class="facts4"><div class="fact"><div class="k">Positions</div><div class="v">' + rows.length + '</div><div class="h">in this view</div></div>'
      + '<div class="fact"><div class="k">Open</div><div class="v">' + open + '</div><div class="h">nobody has joined yet</div></div>'
      + '<div class="fact pick' + (F.pstart ? ' on' : '') + '" data-key="pstart" data-val="Past start" role="button" tabindex="0"><div class="k">Past start</div><div class="v">' + late + '</div><div class="h">start date gone, still unfilled · ' + (F.pstart ? 'showing only these' : 'click to see them') + '</div></div>'
      + '<div class="fact pick' + (F.escd ? ' on' : '') + '" data-key="escd" data-val="Escalated" role="button" tabindex="0"><div class="k">Escalated</div><div class="v">' + escd + '</div><div class="h">with an open escalation · ' + (F.escd ? 'showing only these' : 'click to see them') + '</div></div>'
      + '<div class="fact"><div class="k">Revenue lost</div><div class="v">' + money(lost) + '</div><div class="h">bill rate × ' + OV.hours + ' h × working days late' + (norate ? ' · ' + norate + ' with no bill rate' : '') + '</div></div>'
      + '<div class="fact pick cost' + (F.costing ? ' on' : '') + '" data-key="costing" data-val="Non-billable cost" role="button" tabindex="0"><div class="k">Non-billable cost</div><div class="v">' + money(cost) + '</div><div class="h">' + costn + ' proactive position' + (costn === 1 ? '' : 's') + ' not billing · ' + (F.costing ? 'showing only these' : 'click to see them') + '</div></div></div>';
    h += costing(rows);
    if (F.stage) h += bars('sub');
    h += bars(dim === 'stage' ? 'bu' : 'stage');
    h += '<div class="two">' + bars('type') + bars('practice') + '</div>';
    $('ov-detail').innerHTML = h;
  }

  function costing(rows) {
    var c = rows.filter(function (x) { return x.costing; });
    if (!c.length) return '';
    var active = 0, sofar = 0, month = 0, norate = 0, bu = {};
    c.forEach(function (x) {
      active += x.cost_active ? 1 : 0; sofar += x.cost; month += x.cost_month; norate += x.cost_rate === null ? 1 : 0;
      var b = bu[x.bu] || (bu[x.bu] = { n: 0, cost: 0, month: 0 });
      b.n += x.cost_active ? 1 : 0; b.cost += x.cost; b.month += x.cost_month;
    });
    var max = Math.max.apply(null, Object.keys(bu).map(function (k) { return bu[k].cost; })) || 1;
    var h = '<div class="costing"><div class="ov-listhead"><h2>Costing</h2><span class="small muted">proactive, non-billable positions past their start date</span></div>'
      + '<div class="cost3"><div><div class="k">Positions not billing</div><div class="v">' + active + '</div><div class="h">the client isn\'t paying for them yet</div></div>'
      + '<div><div class="k">Cost so far</div><div class="v">' + money(sofar) + '</div><div class="h">cost rate × ' + OV.hours + ' h × working days since the start date</div></div>'
      + '<div><div class="k">Cost per month from here</div><div class="v">' + money(month) + '</div><div class="h">if nothing changes · ' + OV.month_days + ' working days</div></div></div>';
    if (norate) h += '<div class="small late" style="margin-top:8px">' + norate + ' position' + (norate === 1 ? ' has' : 's have') + ' no cost rate: no offer, and no rate card entry for the grade, practice and region.</div>';
    h += '<table class="ov-table"><thead><tr><th>Business unit</th><th class="r">Not billing</th><th>Agreed cap</th><th>Cost so far</th><th class="r">Per month</th></tr></thead><tbody>';
    Object.keys(bu).sort().forEach(function (k) {
      var b = bu[k], cap = OV.caps[k];
      h += '<tr><td>' + esc(k) + '</td><td class="r">' + b.n + '</td><td>' + (cap === null || cap === undefined ? '—' : cap + (b.n > cap ? ' <span class="chip esc">over cap</span>' : '')) + '</td><td><span class="cbar"><i style="width:' + (b.cost / max * 100) + '%"></i></span>' + money(b.cost) + '</td><td class="r">' + money(b.month) + '</td></tr>';
    });
    return h + '</tbody></table><div class="small muted" style="margin-top:6px">Never counted as revenue lost. Costing stops when the demand owner marks the position billable.</div></div>';
  }

  function costList(rows, title) {
    var h = '<div class="ov-listhead"><h2>' + rows.length + ' non-billable position' + (rows.length === 1 ? '' : 's') + ' <span class="small muted">· ' + esc(title) + '</span></h2><span class="small muted">What each one has cost the account since its start date.</span></div>';
    h += '<div class="ov-scroll"><table class="ov-table"><thead><tr><th>App ref</th><th>Position</th><th>Resource</th><th>Owner · BU</th><th>Practice · grade</th><th>Stage</th><th>Start date</th><th class="r">Working days</th><th class="r">Cost / h</th><th class="r">Cost so far</th></tr></thead><tbody>';
    var total = 0;
    h += rows.map(function (x) {
      if (x.cost_active) total += x.cost;
      return '<tr' + (x.cost_active ? '' : ' class="stopped"') + '><td><a class="mono" href="/demands/' + esc(x.ref) + '">' + esc(x.req || x.ref) + '</a></td><td>' + esc(x.name) + '<div class="small muted">' + (x.cost_until ? 'Billable since ' + esc(x.cost_until) : esc(x.type)) + '</div></td><td>' + esc(x.resource || 'Not named yet') + '</td><td>' + esc(x.owner) + '<div class="small muted">' + esc(x.bu) + '</div></td><td>' + esc(x.practice) + ' · ' + esc(x.grade) + '</td><td>' + esc(x.stage) + '<div class="small muted">' + esc(x.sub) + '</div></td><td>' + esc(x.start || '—') + '</td><td class="r">' + x.cost_days + '</td><td class="r">' + (x.cost_rate === null ? '<span class="late">none</span>' : '$' + x.cost_rate.toFixed(2) + '<div class="small muted">' + esc(x.cost_source) + '</div>') + '</td><td class="r"><strong>' + (x.cost_rate === null ? '—' : money(x.cost)) + '</strong>' + (x.cost_active ? '' : '<div class="small muted">costing stopped</div>') + '</td></tr>';
    }).join('');
    h += '</tbody><tfoot><tr><td colspan="9" class="r"><strong>Still costing</strong></td><td class="r"><strong>' + money(total) + '</strong></td></tr></tfoot></table></div>';
    $('ov-list').innerHTML = h;
  }

  function list(rows) {
    var ks = Object.keys(F);
    if (!ks.length) {
      $('ov-list').innerHTML = '<div class="empty">Demands are listed here once you narrow down. Click a slice of the ring, any row beside it, the Past start, Escalated or Non-billable cost number, or a box in the workflow.</div>';
      return;
    }
    var title = ks.map(function (k) { return NAME[k].toLowerCase() + ': ' + F[k]; }).join(' · ');
    if (F.costing) { costList(rows, title); return; }
    var h = '<div class="ov-listhead"><h2>' + rows.length + ' demand' + (rows.length === 1 ? '' : 's') + ' <span class="small muted">· ' + esc(title) + '</span></h2><span class="small muted">These are the demands behind the numbers above.</span></div>';
    // A table on wide screens; on phones app.css lays each row out as a card (.ov-cards), so the cell
    // classes name where each piece goes, and .m-only / .d-only hold the bits that move between the two.
    h += '<div class="ov-scroll"><table class="ov-table ov-cards"><thead><tr><th>App ref</th><th>Demand</th><th>Owner · BU</th><th>Practice</th><th>Stage</th><th>Start</th><th>Joining</th><th class="r">Revenue lost</th><th></th></tr></thead><tbody>';
    h += rows.map(function (x) {
      var on = openRef === x.ref;
      return '<tr><td class="c-ref"><a class="mono" href="/demands/' + esc(x.ref) + '">' + esc(x.req || x.ref) + '</a></td>'
        + '<td class="c-name">' + esc(x.name) + '<div class="small muted d-only">' + esc(x.type) + '</div></td>'
        + '<td class="c-owner">' + esc(x.owner) + '<div class="small muted">' + esc(x.bu) + '<span class="m-only"> · ' + esc(x.type) + '</span></div>' + callLink(x) + '</td>'
        + '<td class="c-prac">' + esc(x.practice) + '</td>'
        + '<td class="c-stage">' + esc(x.stage) + '<div class="small muted">' + esc(x.sub) + '</div>' + x.esc.map(function (e) { return '<div class="small late">⚠ ' + esc(e.t) + ' · L' + e.l + '</div>'; }).join('') + '</td>'
        + '<td class="c-start ' + (x.late ? 'late' : '') + '"><span class="m-only">Start </span>' + esc(x.start || '—') + (x.late ? '<div class="small late">' + x.days_late + ' days late</div>' : '') + '</td>'
        + '<td class="c-join"><span class="m-only">Joining </span>' + esc(x.joining || 'Not set') + '</td>'
        + '<td class="c-lost r"><span class="m-only">Lost </span>' + (x.lost ? money(x.lost) : '—') + '</td>'
        + '<td class="c-wf"><a href="#" class="wf-link" data-flow="' + esc(x.ref) + '">' + (on ? 'Hide workflow' : 'Show workflow') + '</a></td></tr>'
        + (on ? '<tr class="wfrow"><td colspan="9"><div class="small" style="margin-bottom:8px"><strong>' + esc(x.ref) + ' · ' + esc(x.name) + '</strong> is now at <strong>' + esc(x.stage) + ' · ' + esc(x.sub) + '</strong></div><div class="wf-wrap">' + wfDiagram(L, { current: { stage: x.stage, sub: x.sub }, escalations: x.esc }) + '</div></td></tr>' : '');
    }).join('');
    if (!rows.length) h += '<tr><td colspan="9" class="small muted">No demands match. Remove a tag above.</td></tr>';
    $('ov-list').innerHTML = h + '</tbody></table></div>';
  }

  // A tel: link: on a phone it opens the dialler with the owner's number filled in, ready to call.
  var PHONE_ICON = '<svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true"><path fill="currentColor" d="M6.6 10.8a15.1 15.1 0 0 0 6.6 6.6l2.2-2.2a1 1 0 0 1 1-.25 11.4 11.4 0 0 0 3.6.57 1 1 0 0 1 1 1V20a1 1 0 0 1-1 1A17 17 0 0 1 3 4a1 1 0 0 1 1-1h3.5a1 1 0 0 1 1 1c0 1.25.2 2.45.57 3.57a1 1 0 0 1-.25 1z"/></svg>';
  function shownPhone(p) {
    var m = /^\+1(\d{3})(\d{3})(\d{4})$/.exec(p);
    return m ? '(' + m[1] + ') ' + m[2] + '-' + m[3] : p;
  }
  function callLink(x) {
    if (!x.owner_phone) return '';
    return '<a class="ov-call" href="tel:' + esc(x.owner_phone) + '" aria-label="Call ' + esc(x.owner) + ' on ' + esc(shownPhone(x.owner_phone)) + '">'
      + PHONE_ICON + '<span>Call</span><span class="num">' + esc(shownPhone(x.owner_phone)) + '</span></a>';
  }

  function flow() {
    var rows = D.filter(function (x) { for (var k in F) { if (k !== 'stage' && k !== 'sub' && x[k] !== F[k]) return false; } return true; });
    var counts = {}, em = {};
    rows.forEach(function (x) { counts[x.sub] = (counts[x.sub] || 0) + 1; if (x.esc.length) em[x.sub] = (em[x.sub] || 0) + x.esc.length; });
    $('ov-flowlink').textContent = showFlow ? 'Hide workflow' : 'Show workflow';
    $('ov-flow').hidden = !showFlow;
    if (showFlow) $('ov-flow').innerHTML = '<h2>Workflow</h2><div class="hint" style="margin-bottom:10px">Every stage and sub-stage a demand can be in, with the open escalations at each step.</div><div class="wf-wrap">' + wfDiagram(L, { counts: counts, esc: em, sel: F.sub || F.stage }) + '</div>';
  }

  function draw() { var rows = ring(); flow(); detail(rows); list(rows); }

  $('ov-tabs').addEventListener('click', function (e) {
    var b = e.target.closest('button'); if (!b) return;
    dim = b.dataset.d;
    this.querySelectorAll('button').forEach(function (x) { x.classList.toggle('on', x === b); });
    draw();
  });
  $('ov').addEventListener('click', function (e) {
    if (e.target.id === 'ov-clear') { F = {}; draw(); return; }
    if (e.target.id === 'ov-flowlink') { e.preventDefault(); showFlow = !showFlow; draw(); return; }
    var w = e.target.closest('[data-flow]');
    if (w) { e.preventDefault(); openRef = openRef === w.dataset.flow ? null : w.dataset.flow; draw(); return; }
    var inFlow = e.target.closest('#ov-flow');
    var sb = inFlow && e.target.closest('.wfbox');
    if (sb) { if (F.sub === sb.dataset.sub) { delete F.sub; } else { F.stage = sb.dataset.stage; F.sub = sb.dataset.sub; } draw(); return; }
    var st = inFlow && e.target.closest('.wfstage');
    if (st) { toggle('stage', st.dataset.stage); return; }
    var t = e.target.closest('[data-key]');
    if (t) toggle(t.dataset.key, t.dataset.val);
  });
  draw();
})();
