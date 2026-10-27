/* HR Attendance: single page app, vanilla JS, no build step.
   Every piece of data is inserted with textContent (see h()), never as HTML. */
(function () {
  'use strict';

  const DEMO = new URLSearchParams(window.location.search).get('demo') === '1';

  const state = { me: null, token: 0, openCount: null, week: null };

  // Which pages exist and which permission opens them. The server enforces the same
  // table (auth.PERMS) on every call; this only decides what to show.
  const NAV = [
    { route: 'overview', icon: '🏠', label: 'Overview', perm: 'overview' },
    { route: 'scan', icon: '🕒', label: 'Attendance scan', perm: 'scan' },
    { route: 'exceptions', icon: '⚠️', label: 'Exceptions', perm: 'exceptions', badge: true },
    { route: 'staff', icon: '👥', label: 'Staff', perm: 'staff_view' },
    { route: 'outlets', icon: '🏬', label: 'Outlets', perm: 'outlets_view' },
    { route: 'reports', icon: '📄', label: 'Reports', perm: 'reports_list' },
    { route: 'rules', icon: '📋', label: 'Rules', perm: 'rules_view' },
    { route: 'logs', icon: '📁', label: 'Log files', perm: 'logs' },
    { route: 'settings', icon: '⚙️', label: 'Settings', perm: 'any' },
    { route: 'health', icon: '🛡️', label: 'System health', perm: 'health', admin: true }
  ];
  const ROLE_NAME = { master: 'Master Admin', ceo: 'CEO', admin: 'Admin', hrm: 'HRM', viewer: 'Viewer' };

  function can(perm) {
    if (!state.me) return false;
    return perm === 'any' || (state.me.perms || []).indexOf(perm) >= 0;
  }

  // ---------- tiny safe DOM helper ----------
  function h(tag, props) {
    const el = document.createElement(tag);
    if (props) {
      Object.keys(props).forEach((k) => {
        const v = props[k];
        if (v === false || v == null) return;
        if (k === 'class') el.className = v;
        else if (k === 'text') el.textContent = String(v);
        else if (k.slice(0, 2) === 'on' && typeof v === 'function') el.addEventListener(k.slice(2), v);
        else el.setAttribute(k, v === true ? '' : String(v));
      });
    }
    for (let i = 2; i < arguments.length; i++) add(el, arguments[i]);
    return el;
  }
  function add(el, kid) {
    if (kid == null || kid === false) return;
    if (Array.isArray(kid)) kid.forEach((k) => add(el, k));
    else if (kid instanceof Node) el.appendChild(kid);
    else el.appendChild(document.createTextNode(String(kid)));
  }
  const $ = (id) => document.getElementById(id);

  // ---------- formatting ----------
  const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  function parseDate(s) {
    const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(s || ''));
    return m ? new Date(Date.UTC(+m[1], +m[2] - 1, +m[3])) : null;
  }
  function fmtDate(s) {
    const d = parseDate(s);
    return d ? d.getUTCDate() + ' ' + MONTHS[d.getUTCMonth()] : (s == null ? '' : String(s));
  }
  function fmtRange(a, b) {
    const x = parseDate(a), y = parseDate(b);
    if (!x || !y) return '';
    const yr = y.getUTCFullYear();
    return x.getUTCMonth() === y.getUTCMonth()
      ? x.getUTCDate() + ' to ' + y.getUTCDate() + ' ' + MONTHS[y.getUTCMonth()] + ' ' + yr
      : x.getUTCDate() + ' ' + MONTHS[x.getUTCMonth()] + ' to ' + y.getUTCDate() + ' ' + MONTHS[y.getUTCMonth()] + ' ' + yr;
  }
  function isoWeekOf(d) {
    const t = new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate()));
    const day = t.getUTCDay() || 7;
    t.setUTCDate(t.getUTCDate() + 4 - day);
    const y = t.getUTCFullYear();
    const wk = Math.ceil(((t - Date.UTC(y, 0, 1)) / 86400000 + 1) / 7);
    return y + '-W' + String(wk).padStart(2, '0');
  }
  function shiftWeek(startStr, days) {
    const d = parseDate(startStr);
    if (!d) return null;
    d.setUTCDate(d.getUTCDate() + days);
    return isoWeekOf(d);
  }
  function pct(v) {
    if (v == null || v === '') return '–';
    const n = Number(v);
    return isNaN(n) ? String(v) : (Math.round(n * 10) / 10) + '%';
  }
  function coveredText(c) {
    if (!c) return '';
    if (typeof c === 'object') return fmtRange(c.start, c.end);
    return String(c);
  }
  function coveredNote(o) {
    const t = coveredText(o.covered);
    return t && t !== fmtRange(o.start, o.end) ? 'data for ' + t : '';
  }
  function hasData(o) { return !!(o && o.kpis && o.kpis.attendance_rate != null); }
  function num(v) { return v == null ? '–' : String(v); }
  function fmtSize(n) {
    n = Number(n);
    if (isNaN(n)) return '';
    if (n < 1024) return n + ' B';
    if (n < 1048576) return Math.round(n / 1024) + ' KB';
    return (n / 1048576).toFixed(1) + ' MB';
  }
  function badgeClass(item) {
    const s = String((item && (item.severity || item.status)) || '').toLowerCase();
    const st = String((item && item.status) || '').toLowerCase();
    if (/confirm|resolved|ready|ok|green|done|active|complete/.test(st) && !/review/.test(st)) return 'ok';
    if (/high|red|review|error|open|blocker/.test(s) || /review/.test(st)) return 'red';
    if (/med|amber|pending|check|partial|warn/.test(s + ' ' + st)) return 'amb';
    if (/low|info|blue/.test(s + ' ' + st)) return 'blu';
    return 'blu';
  }
  const badge = (text, cls) => h('span', { class: 'b ' + cls, text: text });

  // ---------- API ----------
  class ApiError extends Error {
    constructor(status, message) { super(message); this.status = status; }
  }
  function friendly(status, serverMsg) {
    if (status === 0) return 'Cannot reach the Client server. Please check it is running (use the desktop icon), then try again.';
    if (status === 401) return 'Your sign-in has ended. Please sign in again.';
    if (status === 403) return serverMsg || 'You do not have access to this. Ask Admin if you think you should.';
    if (status === 404) return 'That item was not found.';
    if (status === 503) return 'This is not available yet. It will appear once the first scan has run.';
    if (status >= 400 && status < 500 && serverMsg) return serverMsg;
    return 'Something went wrong on the server. Please try again, and tell Admin if it keeps happening.';
  }
  async function api(path, opts) {
    opts = opts || {};
    const method = opts.method || 'GET';
    if (DEMO) return demoApi(method, path, opts.json);
    const headers = { Accept: 'application/json' };
    let body;
    if (opts.json !== undefined) { headers['Content-Type'] = 'application/json'; body = JSON.stringify(opts.json); }
    if (opts.form) body = opts.form;
    if (method !== 'GET' && state.me && state.me.csrf) headers['X-CSRF-Token'] = state.me.csrf;
    let res;
    try {
      res = await fetch(path, { method: method, headers: headers, body: body, credentials: 'same-origin' });
    } catch (e) {
      throw new ApiError(0, friendly(0));
    }
    let data = null;
    try { data = await res.json(); } catch (e) { data = null; }
    if (res.status === 401 && path !== '/api/login') {
      state.me = null;
      go('login');
      throw new ApiError(401, friendly(401));
    }
    if (!res.ok) {
      const sm = data && typeof (data.error || data.message) === 'string' ? (data.error || data.message) : '';
      throw new ApiError(res.status, friendly(res.status, sm));
    }
    return data;
  }
  const qs = (o) => {
    const p = new URLSearchParams();
    Object.keys(o).forEach((k) => { if (o[k]) p.set(k, o[k]); });
    const s = p.toString();
    return s ? '?' + s : '';
  };

  // ---------- demo data ----------
  const D_OUT = ['Outlet A', 'Outlet B', 'Outlet C', 'Outlet D'];
  const D_STAFF = 'ABCDEFGHIJKL'.split('').map((c, i) => ({
    id: 'S' + String(i + 1).padStart(3, '0'), name: 'Employee ' + c, outlet: D_OUT[i % 4], status: i === 11 ? 'On leave' : 'Active'
  }));
  const D_EXC = [
    ['Employee A', 'Outlet A', '2026-10-06', 'Missing clock-out', 'Needs review', 'high'],
    ['Employee B', 'Outlet B', '2026-10-07', 'Late by 22 min', 'Pending question', 'medium'],
    ['Employee C', 'Outlet C', '2026-10-08', 'Punched at another outlet', 'Info', 'low'],
    ['Employee D', 'Outlet D', '2026-10-09', 'Single punch only', 'Pending question', 'medium'],
    ['Employee E', 'Outlet A', '2026-10-10', 'Late by 4 min (within grace)', 'Confirmed rule', 'ok'],
    ['Employee F', 'Outlet B', '2026-10-05', 'Missing clock-in', 'Needs review', 'high']
  ].map((r, i) => ({ id: 'X' + (i + 1), staff: r[0], outlet: r[1], date: r[2], issue: r[3], status: r[4], severity: r[5] }));
  const D_REPORTS = [
    { name: 'Weekly W41', kind: 'Weekly', month: '2026-10', size: 48200, created: '2026-10-12 12:00', status: 'Partial', path: 'demo/w41.xlsx' },
    { name: 'Weekly W40', kind: 'Weekly', month: '2026-10', size: 51300, created: '2026-10-05 12:00', status: 'Ready', path: 'demo/w40.xlsx' },
    { name: 'Monthly September', kind: 'Monthly', month: '2026-09', size: 120400, created: '2026-10-01 12:00', status: 'Ready', path: 'demo/sep.xlsx' }
  ];
  const D_HELP = [
    { title: 'Late arrival grace period', source: 'Rules', snippet: 'Clocking in up to 15 minutes after the shift start is within grace and is not flagged as late.' },
    { title: 'Missing clock-out', source: 'Flags', snippet: 'A day with one punch only is listed for review. HR decides what to do, nothing is changed automatically.' },
    { title: 'Punched at another outlet', source: 'Flags', snippet: 'Transfers between outlets are common. They are shown for information, not as an error.' }
  ];
  const DEMO_PERMS = {
    master: ['overview', 'reports_list', 'report_review', 'report_workbook', 'exceptions', 'staff_view', 'staff_edit', 'outlets_view', 'outlets_edit', 'scan', 'health', 'logs', 'rules_view', 'rules_edit', 'users_manage', 'lan_view', 'lan_set', 'help', 'it_diagnose', 'it_repair'],
    ceo: ['overview', 'reports_list', 'report_review', 'report_workbook', 'exceptions', 'staff_view', 'outlets_view', 'rules_view', 'rules_edit', 'users_manage', 'lan_view', 'help'],
    admin: ['overview', 'reports_list', 'report_review', 'report_workbook', 'exceptions', 'staff_view', 'staff_edit', 'outlets_view', 'outlets_edit', 'scan', 'health', 'logs', 'rules_view', 'lan_view', 'help', 'it_diagnose', 'it_repair'],
    hrm: ['overview', 'reports_list', 'report_review', 'report_workbook', 'exceptions', 'rules_view', 'users_manage', 'help'],
    viewer: ['overview', 'reports_list', 'report_review', 'rules_view', 'help']
  };
  function demoMe(role) {
    return { username: 'demo-' + role, role: role, role_label: ROLE_NAME[role], display_name: 'Demo ' + ROLE_NAME[role],
      csrf: 'demo', is_master: role === 'master', must_change: false, perms: DEMO_PERMS[role] };
  }
  function demoApi(method, path, json) {
    const u = new URL(path, 'http://demo.invalid');
    const p = u.pathname, q = u.searchParams;
    const role = state.me ? state.me.role : 'hrm';
    const out = (v) => new Promise((r) => setTimeout(() => r(v), 60));
    const fail = (s) => Promise.reject(new ApiError(s, friendly(s)));
    if (p === '/api/overview') {
      return out({
        week_label: 'Week 41', start: '2026-10-05', end: '2026-10-11', covered: 'all outlets', status: 'Partial',
        kpis: { attendance_rate: 96.4, late_arrivals: 18, late_staff: 6, missing_punches: 7, open_exceptions: 12, older_than_week: 3 },
        by_outlet: [
          { outlet: 'Outlet A', on_time_pct: 84, late_pct: 11, missing_pct: 5, attendance_pct: 96 },
          { outlet: 'Outlet B', on_time_pct: 80, late_pct: 13, missing_pct: 7, attendance_pct: 93 },
          { outlet: 'Outlet C', on_time_pct: 90, late_pct: 8, missing_pct: 2, attendance_pct: 98 },
          { outlet: 'Outlet D', on_time_pct: 86, late_pct: 9, missing_pct: 5, attendance_pct: 95 }
        ]
      });
    }
    if (p === '/api/exceptions') {
      if (role === 'viewer') return fail(403);
      const st = q.get('status'), ou = q.get('outlet');
      return out(D_EXC.filter((e) => (!st || e.status === st) && (!ou || e.outlet === ou)));
    }
    if (p === '/api/staff') return DEMO_PERMS[role].indexOf('staff_view') < 0 ? fail(403) : out(D_STAFF.map((x) => Object.assign({ created_by: 'admin', created_at: '2026-10-01T09:00:00', edited_by: '', edited_at: '', source: 'master data' }, x)));
    if (p === '/api/outlets') return DEMO_PERMS[role].indexOf('outlets_view') < 0 ? fail(403) : out(D_OUT.map((o) => ({ id: o, outlet: o, name: o, note: '', staff_count: D_STAFF.filter((s) => s.outlet === o).length, created_by: 'admin', created_at: '2026-10-01T09:00:00', edited_by: '', edited_at: '', source: 'master data' })));
    if (p === '/api/reports') return out(D_REPORTS);
    if (p === '/api/help') {
      const s = (q.get('q') || '').toLowerCase();
      return out(D_HELP.filter((x) => !s || (x.title + ' ' + x.snippet).toLowerCase().indexOf(s) >= 0));
    }
    if (p === '/api/users') return out({ assignable: ['admin', 'hrm', 'viewer'], users: [
      { username: 'demo-master', display_name: 'Demo Master', role: 'master', is_master: true, password_set: true, must_change: false, can_manage: false, created_by: 'setup', created_at: '2026-10-01T09:00:00', last_login: '2026-10-18T08:00:00' },
      { username: 'demo-hrm', display_name: 'Demo HRM', role: 'hrm', is_master: false, password_set: true, must_change: true, can_manage: true, created_by: 'demo-master', created_at: '2026-10-02T09:00:00', last_login: '' }
    ] });
    if (p === '/api/rules') return out([
      { id: 'late_grace_minutes', label: 'Late-in grace period (minutes)', value: 15, status: 'CONFIRMED', question: 'C5', used_in: 'day engine', edited_by: '', edited_at: '', created_by: '', created_at: '' },
      { id: 'early_out_grace_minutes', label: 'Early-out grace (minutes)', value: 15, status: 'ASSUMED', question: 'Q5', used_in: 'not built yet', edited_by: '', edited_at: '', created_by: '', created_at: '' }
    ]);
    if (p === '/api/logs') return out([
      { name: 'scan_2026-10.log', path: 'scan_2026-10.log', size: 24000, modified: '2026-10-12 14:30' },
      { name: 'app.log', path: 'app.log', size: 8400, modified: '2026-10-11 09:00' }
    ]);
    if (p === '/api/logs/view') return out({ name: 'demo.log', content: 'Demo log content\nLine 2\nLine 3' });
    if (p === '/api/health') return DEMO_PERMS[role].indexOf('health') >= 0 ? out({ server: 'running (demo)', last_scan: 'today 12:00', disk_free: '120 GB', reports_folder: 'ok' }) : fail(403);
    if (method === 'POST') return role === 'master' || role === 'admin' ? out({ ok: true, message: 'Demo only: nothing was run or saved.' }) : fail(403);
    return fail(404);
  }

  // ---------- routing ----------
  function currentRoute() {
    return (window.location.hash.replace(/^#\/?/, '').split('?')[0]) || '';
  }
  function go(route) { window.location.hash = '#/' + route; }
  function allowed(route) {
    const item = NAV.find((n) => n.route === route);
    return !!(item && can(item.perm));
  }
  const VIEWS = {
    overview: vOverview, scan: vScan, exceptions: vExceptions,
    staff: vStaff, outlets: vOutlets, reports: vReports, rules: vRules,
    logs: vLogs, settings: vSettings, health: vHealth
  };

  function route() {
    closeDrawer();
    closeModal();
    const r = currentRoute();
    const wk = /[?&]week=(\d{4}-W\d{2})$/.exec(window.location.hash);
    if (wk) state.week = wk[1];
    if (!state.me) {
      if (r !== 'login') { go('login'); return; }
      showLogin();
      return;
    }
    if (r === 'login' || r === '') { go(state.me.must_change ? 'settings' : 'overview'); return; }
    if (state.me.must_change && r !== 'settings') { go('settings'); return; }
    showApp();
    buildNav(r);
    const view = $('view');
    if (!allowed(r)) {
      view.replaceChildren(pageHead('Not available', ''), emptyCard('This page is not part of your role', 'Use the menu on the left to open a page you can see.'));
      focusHead();
      return;
    }
    const my = ++state.token;
    view.replaceChildren(pageHead(titleFor(r), ''), h('div', { class: 'card', 'aria-busy': 'true' }, h('p', { class: 'sub', text: 'Loading…' })));
    Promise.resolve(VIEWS[r](view, () => my === state.token)).catch((e) => {
      if (my !== state.token || (e && e.status === 401)) return;
      view.replaceChildren(pageHead(titleFor(r), ''), errorCard(e, route));
    }).then(() => { if (my === state.token) focusHead(); });
  }
  function titleFor(r) {
    const n = NAV.find((x) => x.route === r);
    return r === 'overview' ? 'Attendance overview' : (n ? n.label : 'HR Attendance');
  }
  function focusHead() {
    const hd = document.querySelector('#view h1');
    if (hd) { hd.setAttribute('tabindex', '-1'); hd.focus({ preventScroll: true }); }
  }

  // ---------- shell ----------
  function showLogin() {
    $('lan-warning').hidden = ['127.0.0.1', 'localhost'].indexOf(window.location.hostname) >= 0;
    $('app').hidden = true;
    $('login-screen').hidden = false;
    $('login-user').focus();
  }
  function showApp() {
    $('login-screen').hidden = true;
    $('app').hidden = false;
    $('demo-banner').hidden = !DEMO;
  }
  function buildNav(active) {
    const nav = $('nav');
    const kids = [];
    let adminHeader = false;
    NAV.forEach((n) => {
      if (!can(n.perm)) return;
      if (n.admin && !adminHeader) { kids.push(h('small', { text: 'Admin only' })); adminHeader = true; }
      const a = h('a', { href: '#/' + n.route, 'aria-current': n.route === active ? 'page' : false },
        h('span', { 'aria-hidden': 'true', text: n.icon }), n.label);
      if (n.badge && state.openCount) a.appendChild(h('span', { class: 'tag', text: state.openCount, 'aria-label': state.openCount + ' open' }));
      kids.push(a);
    });
    kids.push(h('button', { class: 'nav-help', type: 'button', onclick: showHelp }, h('span', { 'aria-hidden': 'true', text: '❓' }), 'Help'));
    nav.replaceChildren.apply(nav, kids);
  }
  function loadBadge() {
    if (!state.me || !can('exceptions')) return;
    api('/api/overview').then((o) => {
      state.openCount = o && o.kpis ? o.kpis.open_exceptions : null;
      if (state.me) buildNav(currentRoute());
    }).catch(() => {});
  }
  function whoChip() {
    const roleLbl = state.me.role_label || ROLE_NAME[state.me.role] || state.me.role;
    const kids = [h('span', {}, 'Signed in as ', h('b', { text: state.me.display_name || state.me.username }), ' · ' + roleLbl)];
    if (!DEMO) {
      kids.push(h('button', { class: 'btn ghost small', type: 'button', onclick: logout }, 'Sign out'));
    }
    return h('div', { class: 'user' }, kids);
  }
  function demoSwitch() {
    const box = h('div', { class: 'roles', role: 'group', 'aria-label': 'Demo role' });
    ['viewer', 'hrm', 'admin', 'ceo', 'master'].forEach((r) => {
      box.appendChild(h('button', {
        type: 'button', 'aria-pressed': state.me.role === r ? 'true' : 'false',
        onclick: () => { state.me = demoMe(r); route(); }
      }, ROLE_NAME[r]));
    });
    return box;
  }
  function pageHead(title, sub, extra) {
    return h('div', { class: 'bar' },
      h('div', {}, h('h1', { text: title }), sub ? h('p', { text: sub }) : null),
      h('div', { class: 'bar-actions' }, extra, DEMO ? demoSwitch() : null, whoChip()));
  }
  function emptyCard(title, text) {
    return h('div', { class: 'card empty' }, h('b', { text: title }), h('span', { text: text }));
  }
  function errorCard(e, retry) {
    return h('div', { class: 'card' },
      h('div', { class: 'msg err', role: 'alert', text: e && e.message ? e.message : friendly(-1) }),
      h('div', { class: 'btn-row' }, h('button', { class: 'btn ghost', type: 'button', onclick: retry }, 'Try again')));
  }
  function toast(text) {
    const t = $('toast');
    t.textContent = text;
    t.hidden = false;
    clearTimeout(toast.t);
    toast.t = setTimeout(() => { t.hidden = true; }, 4000);
  }
  function table(label, heads, rows) {
    return h('div', { class: 'tablewrap' }, h('table', {},
      h('caption', { class: 'vh', text: label }),
      h('thead', {}, h('tr', {}, heads.map((x) => h('th', { scope: 'col', text: x })))),
      h('tbody', {}, rows.map((r) => h('tr', {}, r.map((c) => h('td', {}, c)))))));
  }

  // ---------- modal ----------
  function openModal(title, contentEl) {
    $('modal-title').textContent = title;
    $('modal-body').replaceChildren(contentEl);
    $('modal-backdrop').hidden = false;
    $('modal').hidden = false;
    $('modal-close').focus();
  }
  function closeModal() {
    if ($('modal').hidden) return;
    $('modal').hidden = true;
    $('modal').classList.remove('wide');
    $('modal-backdrop').hidden = true;
  }

  // ---------- drawer ----------
  let drawerOpener = null;
  function openDrawer(item, opener) {
    drawerOpener = opener || null;
    $('drawer-title').textContent = 'Review: ' + (item.issue || 'exception');
    const kv = h('dl', { class: 'kv' });
    [['Staff', item.staff], ['Outlet', item.outlet], ['Date', item.date ? fmtDate(item.date) : 'Whole month'], ['Issue', item.issue], ['Reference', item.id]].forEach((r) => {
      kv.appendChild(h('dt', { text: r[0] }));
      kv.appendChild(h('dd', { text: r[1] == null ? '–' : r[1] }));
    });
    kv.appendChild(h('dt', { text: 'Status' }));
    kv.appendChild(h('dd', {}, badge(item.status || 'Open', badgeClass(item))));
    $('drawer-body').replaceChildren(kv,
      h('p', { class: 'sub', text: 'What happens next: a person in HR decides every item. This page only shows what the check found. Nothing is approved, paid or sent from here.' }));
    $('drawer-backdrop').hidden = false;
    $('drawer').hidden = false;
    $('drawer-close').focus();
  }
  function closeDrawer() {
    if ($('drawer').hidden) return;
    $('drawer').hidden = true;
    $('drawer-backdrop').hidden = true;
    if (drawerOpener && document.contains(drawerOpener)) drawerOpener.focus();
    drawerOpener = null;
  }
  function trapKeys(e) {
    const panel = !$('modal').hidden ? $('modal') : (!$('drawer').hidden ? $('drawer') : null);
    if (!panel) return;
    if (e.key === 'Escape') { closeModal(); closeDrawer(); return; }
    if (e.key === 'Tab') {
      const f = Array.prototype.slice.call(panel.querySelectorAll('button,a[href],input,select,textarea'));
      if (!f.length) return;
      const first = f[0], last = f[f.length - 1];
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    }
  }

  // ---------- shared pieces ----------
  function excTable(list, label) {
    return table(label, ['Staff', 'Outlet', 'Date', 'Issue', 'Status', ''], list.map((e) => {
      const b = h('button', { class: 'btn ghost small', type: 'button', 'aria-label': (badgeClass(e) === 'ok' ? 'View ' : 'Review ') + e.issue + ' for ' + e.staff }, badgeClass(e) === 'ok' ? 'View' : 'Review');
      b.addEventListener('click', () => openDrawer(e, b));
      return [e.staff, e.outlet, (e.date ? fmtDate(e.date) : 'Whole month'), e.issue, badge(e.status || 'Open', badgeClass(e)), b];
    }));
  }
  function runScan(btn, out) {
    btn.disabled = true;
    out.replaceChildren();
    return api('/api/scan/run', { method: 'POST', json: {} }).then((r) => {
      out.replaceChildren(h('div', { class: 'msg good', role: 'status', text: (r && r.message) || 'The scan has started. Refresh in a minute to see the result.' }));
    }).catch((e) => {
      if (e.status !== 401) out.replaceChildren(h('div', { class: 'msg err', role: 'alert', text: e.message }));
    }).then(() => { btn.disabled = false; });
  }

  // ---------- views ----------

  async function vOverview(view, live) {
    const o = await api('/api/overview' + qs({ week: state.week }));
    if (!live()) return;
    const k = o.kpis || {};
    const isAdmin = can('scan');
    const canExc = can('exceptions');
    const [excAll, rep] = await Promise.all([
      canExc ? api('/api/exceptions' + qs({ week: state.week })).catch(() => null) : Promise.resolve(null),
      api('/api/reports').catch(() => null)
    ]);
    if (!live()) return;
    const exc = excAll ? excAll.filter((e) => e.status === 'Needs review' || e.status === 'Pending question') : null;
    state.openCount = k.open_exceptions || null;
    buildNav('overview');

    const sub = [o.week_label, fmtRange(o.start, o.end), coveredNote(o)].filter(Boolean).join(' · ');
    const weekNav = h('div', { class: 'bar-actions' },
      h('button', { class: 'btn ghost small', type: 'button', onclick: () => { state.week = shiftWeek(o.start, -7); route(); } }, '← Earlier week'),
      h('button', { class: 'btn ghost small', type: 'button', onclick: () => { state.week = shiftWeek(o.start, 7); route(); } }, 'Later week →'));

    const kpi = (label, val, cls, note, ncls) => h('div', { class: 'kpi' }, h('span', { text: label }), h('b', { class: cls, text: val }), h('em', { class: ncls, text: note }));
    const kpis = h('div', { class: 'kpis' },
      kpi('Attendance rate', pct(k.attendance_rate), 'c-ok', 'of scheduled days', 'c-ok'),
      kpi('Late arrivals', num(k.late_arrivals), 'c-amb', num(k.late_staff) + ' staff', 'c-amb'),
      kpi('Missing punches', num(k.missing_punches), 'c-red', Number(k.missing_punches) ? 'needs review' : 'none this week', 'c-red'),
      kpi('Open exceptions', num(k.open_exceptions), '', num(k.older_than_week) + ' older than a week', 'c-mute'));

    const bars = h('div', { class: 'card' }, h('h2', { text: 'Attendance by outlet' }),
      h('p', { class: 'sub', text: 'Scheduled days, split by on time, late and missing' }));
    const outs = o.by_outlet || [];
    if (!outs.length) bars.appendChild(h('div', { class: 'empty', text: 'No outlet figures for this week yet.' }));
    outs.forEach((x) => {
      const seg = (v, c) => { const i = h('i', { class: c }); i.style.width = Math.max(0, Math.min(100, Number(v) || 0)) + '%'; return i; };
      bars.appendChild(h('div', { class: 'hb' }, h('span', { text: x.outlet }),
        h('div', { class: 'track', role: 'img', 'aria-label': x.outlet + ': ' + pct(x.on_time_pct) + ' on time, ' + pct(x.late_pct) + ' late, ' + pct(x.missing_pct) + ' missing' },
          seg(x.on_time_pct, 'f-ok'), seg(x.late_pct, 'f-amb'), seg(x.missing_pct, 'f-red')),
        h('b', { text: pct(x.attendance_pct) })));
    });
    bars.appendChild(h('div', { class: 'legend' },
      h('span', {}, h('s', { class: 'f-ok' }), 'On time'), h('span', {}, h('s', { class: 'f-amb' }), 'Late'), h('span', {}, h('s', { class: 'f-red' }), 'Missing punch')));

    const open = Number(k.open_exceptions) || 0;
    const scan = h('div', { class: 'card' }, h('h2', { text: 'Attendance scan' }),
      h('p', { class: 'sub', text: o.status ? 'Status: ' + o.status : 'No scan status yet' }),
      h('div', { class: 'step ' + (hasData(o) ? 'd' : 'w') }, h('i', { 'aria-hidden': 'true', text: hasData(o) ? '✓' : '!' }), hasData(o) ? 'Clock data found for this week' : 'No clock data for this week yet'),
      h('div', { class: 'step ' + (open ? '' : 'd') }, h('i', { 'aria-hidden': 'true', text: open ? '2' : '✓' }), open ? 'Review exceptions (' + open + ' open)' : 'No exceptions to review'));
    if (isAdmin) {
      const scanOut = h('div', { 'aria-live': 'polite' });
      const run = h('button', { class: 'btn', type: 'button' }, 'Run scan now');
      run.addEventListener('click', () => runScan(run, scanOut));
      scan.appendChild(h('div', { class: 'btn-row' }, run, h('a', { class: 'btn ghost', href: '#/scan' }, 'Upload files')));
      scan.appendChild(scanOut);
    } else {
      scan.appendChild(h('p', { class: 'sub', text: 'Scans are run by Admin.' }));
    }

    const parts = [pageHead(titleFor('overview'), sub, weekNav), kpis, h('div', { class: 'grid' }, bars, scan)];
    if (canExc) {
      const c = h('div', { class: 'card' }, h('h2', { text: 'Exceptions to review' }),
        h('p', { class: 'sub', text: 'Open items a person must look at. Information-only items are on the Exceptions page.' }));
      if (!exc) c.appendChild(h('div', { class: 'empty', text: 'The exception list is not available right now.' }));
      else if (!exc.length) c.appendChild(h('div', { class: 'empty', text: 'Nothing to review. All clear.' }));
      else c.appendChild(excTable(exc.slice(0, 5), 'Exceptions to review'));
      if (exc && exc.length > 5) c.appendChild(h('div', { class: 'btn-row' }, h('a', { class: 'btn ghost small', href: '#/exceptions' }, 'See all ' + exc.length)));
      parts.push(c);
    }
    const rc = h('div', { class: 'card' }, h('h2', { text: 'Reports' }), h('p', { class: 'sub', text: 'Weekly and monthly, ready to open or download' }));
    if (!rep || !rep.length) rc.appendChild(h('div', { class: 'empty', text: 'No reports yet.' }));
    else rc.appendChild(table('Recent reports', ['Report', 'Period', 'Status'], (rep || []).slice(0, 3).map((r) => [r.name, r.month || '', badge(r.status || '', badgeClass(r))])));
    rc.appendChild(h('div', { class: 'btn-row' }, h('a', { class: 'btn ghost small', href: '#/reports' }, 'All reports')));
    parts.push(rc);
    view.replaceChildren.apply(view, parts);
  }

  async function vScan(view, live) {
    let o = null;
    try { o = await api('/api/overview'); } catch (e) { if (e.status === 401) throw e; }
    if (!live()) return;
    const files = [];
    const out = h('div', { 'aria-live': 'polite' });
    const upOut = h('div', { 'aria-live': 'polite' });
    const list = h('ul', { class: 'filelist', 'aria-label': 'Files chosen' });
    const send = h('button', { class: 'btn', type: 'button', disabled: true }, 'Upload chosen files');
    const input = h('input', { type: 'file', id: 'file-input', multiple: true });
    const drop = h('div', { class: 'drop', id: 'drop-zone' },
      h('p', {}, h('b', { text: 'Drag clock files here' })),
      h('p', { text: 'or' }),
      h('label', { class: 'btn ghost', for: 'file-input' }, 'Choose files'), input);
    function refresh() {
      list.replaceChildren.apply(list, files.map((f) => h('li', {}, h('span', { text: f.name }), h('span', { class: 'c-mute', text: fmtSize(f.size) }))));
      send.disabled = !files.length;
    }
    function addFiles(fl) { Array.prototype.forEach.call(fl, (f) => files.push(f)); refresh(); }
    input.addEventListener('change', () => { addFiles(input.files); input.value = ''; });
    ['dragenter', 'dragover'].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add('over'); }));
    ['dragleave', 'drop'].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove('over'); }));
    drop.addEventListener('drop', (e) => { if (e.dataTransfer) addFiles(e.dataTransfer.files); });
    input.addEventListener('focus', () => drop.classList.add('over'));
    input.addEventListener('blur', () => drop.classList.remove('over'));
    send.addEventListener('click', () => {
      const form = new FormData();
      files.forEach((f) => form.append('files', f, f.name));
      send.disabled = true;
      api('/api/upload', { method: 'POST', form: form }).then((r) => {
        upOut.replaceChildren(h('div', { class: 'msg good', role: 'status', text: (r && r.message) || 'Files uploaded. Now run the scan.' }));
        files.length = 0; refresh();
      }).catch((e) => {
        if (e.status !== 401) upOut.replaceChildren(h('div', { class: 'msg err', role: 'alert', text: e.message }));
        send.disabled = !files.length;
      });
    });
    const run = h('button', { class: 'btn', type: 'button' }, 'Run scan now');
    run.addEventListener('click', () => runScan(run, out));
    const open = o && o.kpis ? Number(o.kpis.open_exceptions) || 0 : 0;
    const step = (cls, mark, text) => h('div', { class: 'step ' + cls }, h('i', { 'aria-hidden': 'true', text: mark }), text);
    const steps = h('div', { class: 'card' }, h('h2', { text: 'Where things stand' }),
      h('p', { class: 'sub', text: o && o.status ? 'Status: ' + o.status : 'Status not available yet' }),
      step(hasData(o) ? 'd' : 'w', hasData(o) ? '✓' : '!', hasData(o) ? 'Clock data found for this week' : 'No clock data for this week yet'),
      step(hasData(o) ? 'd' : 'w', hasData(o) ? '✓' : '!', hasData(o) ? 'Results ready for ' + (o.week_label || 'this week') : 'No results yet'),
      step(open ? '' : 'd', open ? '3' : '✓', open ? 'Review exceptions (' + open + ' open)' : 'No exceptions to review'));
    const runCard = h('div', { class: 'card' }, h('h2', { text: 'Run the check' }),
      h('p', { class: 'sub', text: 'Reads the files already in the drop folder and builds the report.' }), h('div', { class: 'btn-row' }, run), out);
    const upCard = h('div', { class: 'card' }, h('h2', { text: 'Upload files' }),
      h('p', { class: 'sub', text: 'Add this month’s clock exports, the schedule and the HR workbook. Existing files are never replaced.' }),
      drop, list, h('div', { class: 'btn-row' }, send), upOut);
    view.replaceChildren(pageHead('Attendance scan', 'Run the check or add files', openFolderBtn('drop', 'Open drop folder')), h('div', { class: 'grid' }, steps, runCard), upCard);
  }

  async function vExceptions(view, live) {
    const f = { status: '', outlet: '' };
    const all = await api('/api/exceptions' + qs({ week: state.week }));
    if (!live()) return;
    const uniq = (key) => all.map((e) => e[key]).filter((v, i, a) => v && a.indexOf(v) === i).sort();
    const sel = (id, label, opts, key) => {
      const s = h('select', { id: id }, h('option', { value: '', text: 'All' }), opts.map((v) => h('option', { value: v, text: v })));
      s.addEventListener('change', () => { f[key] = s.value; load(); });
      return h('div', {}, h('label', { for: id, text: label }), s);
    };
    const holder = h('div', { 'aria-live': 'polite' });
    const count = h('p', { class: 'sub' });
    async function load() {
      try {
        const list = Object.keys(f).some((k) => f[k]) ? await api('/api/exceptions' + qs(Object.assign({ week: state.week }, f))) : all;
        if (!live()) return;
        count.textContent = list.length + (list.length === 1 ? ' item' : ' items');
        holder.replaceChildren(list.length ? excTable(list, 'Exceptions') : h('div', { class: 'empty' }, h('b', { text: 'Nothing matches' }), 'Try a different status or outlet.'));
      } catch (e) {
        if (e.status !== 401) holder.replaceChildren(h('div', { class: 'msg err', role: 'alert', text: e.message }));
      }
    }
    view.replaceChildren(pageHead('Exceptions', 'The things a person must look at'),
      h('div', { class: 'card' }, h('div', { class: 'filters' }, sel('f-status', 'Status', uniq('status'), 'status'), sel('f-outlet', 'Outlet', uniq('outlet'), 'outlet'))),
      h('div', { class: 'card' }, count, holder));
    if (!all.length) { holder.replaceChildren(h('div', { class: 'empty' }, h('b', { text: 'Nothing to review' }), 'The check found no exceptions.')); count.textContent = ''; }
    else load();
  }

  // ---- small form helpers ----
  function field(id, label, props) {
    const input = h('input', Object.assign({ id: id, type: 'text', autocomplete: 'off' }, props || {}));
    return h('div', { class: 'field' }, h('label', { for: id, text: label }), input);
  }
  function selectField(id, label, options, current) {
    const sel = h('select', { id: id }, options.map((o) => h('option', { value: o[0], text: o[1] })));
    if (current != null) sel.value = current;
    return h('div', { class: 'field' }, h('label', { for: id, text: label }), sel);
  }
  function showError(box, e) {
    if (e && e.status !== 401) box.replaceChildren(h('div', { class: 'msg err', role: 'alert', text: e.message }));
  }
  function stamp(who, when) {
    const t = String(when || '').replace('T', ' ').slice(0, 16);
    return who || t ? (who || '–') + (t ? ' · ' + t : '') : '–';
  }
  function iconBtn(icon, label, onclick) {
    return h('button', { class: 'btn ghost small icon-btn', type: 'button', title: label, 'aria-label': label, onclick: onclick }, icon);
  }

  function openFolderBtn(which, label) {
    if (DEMO || !can('open_folder')) return null;
    return h('button', { class: 'btn ghost', type: 'button', onclick: () => {
      api('/api/open-folder', { method: 'POST', json: { which: which } }).then((r) => toast((r && r.message) || 'Opened'))
        .catch((e) => toast(e.message));
    } }, '📂 ' + label);
  }

  // ---- Staff and Outlets (Admin screens, with + and edit) ----
  async function vStaff(view, live) {
    const list = await api('/api/staff');
    if (!live()) return;
    const editable = can('staff_edit');
    function openForm(rec) {
      const isNew = !rec;
      const out = h('div', { 'aria-live': 'polite' });
      const save = h('button', { class: 'btn', type: 'button' }, 'Save');
      const wrap = h('div', {},
        field('sf-id', 'Staff ID', { value: rec ? rec.id : '', disabled: !isNew, maxlength: 32 }),
        field('sf-name', 'Name', { value: rec ? rec.name : '', maxlength: 80 }),
        field('sf-outlet', 'Outlet', { value: rec ? rec.outlet : '', maxlength: 40 }),
        selectField('sf-status', 'Status', ['Active', 'On leave', 'Left', 'Check details'].map((x) => [x, x]), rec ? rec.status : 'Active'),
        h('p', { class: 'sub', text: 'Saved in the app directory only. The attendance check and the master data files are not changed.' }),
        h('div', { class: 'btn-row' }, save, h('button', { class: 'btn ghost', type: 'button', onclick: closeModal }, 'Cancel')), out);
      save.addEventListener('click', () => {
        save.disabled = true;
        api('/api/staff/save', { method: 'POST', json: { create: isNew, id: $('sf-id').value.trim(), name: $('sf-name').value,
          outlet: $('sf-outlet').value, status: $('sf-status').value } })
          .then(() => { closeModal(); toast('Staff record saved'); route(); })
          .catch((e) => { showError(out, e); save.disabled = false; });
      });
      openModal(isNew ? 'Add staff' : 'Edit staff ' + rec.id, wrap);
    }
    const body = list.length ? table('Staff', ['Staff ID', 'Name', 'Outlet', 'Status', 'Created by · at', 'Edited by · at', ''],
      list.map((s) => [s.id, s.name, s.outlet, badge(s.status || '', badgeClass(s)), stamp(s.created_by, s.created_at) === '–' ? s.source : stamp(s.created_by, s.created_at),
        stamp(s.edited_by, s.edited_at), editable ? iconBtn('✎', 'Edit staff ' + s.id, () => openForm(s)) : '']))
      : h('div', { class: 'empty' }, h('b', { text: 'No staff listed yet' }), 'Staff appear after the first scan, or add one with +.');
    const add = editable ? h('button', { class: 'btn', type: 'button', onclick: () => openForm(null) }, '+ Add staff') : null;
    view.replaceChildren(pageHead('Staff', list.length + ' people', add), h('div', { class: 'card' }, body));
  }

  async function vOutlets(view, live) {
    const list = await api('/api/outlets');
    if (!live()) return;
    const editable = can('outlets_edit');
    function openForm(rec) {
      const isNew = !rec;
      const out = h('div', { 'aria-live': 'polite' });
      const save = h('button', { class: 'btn', type: 'button' }, 'Save');
      const wrap = h('div', {},
        field('of-id', 'Outlet ID', { value: rec ? rec.id : '', disabled: !isNew, maxlength: 32 }),
        field('of-name', 'Name', { value: rec ? rec.name : '', maxlength: 80 }),
        field('of-note', 'Note', { value: rec ? rec.note : '', maxlength: 200 }),
        h('div', { class: 'btn-row' }, save, h('button', { class: 'btn ghost', type: 'button', onclick: closeModal }, 'Cancel')), out);
      save.addEventListener('click', () => {
        save.disabled = true;
        api('/api/outlets/save', { method: 'POST', json: { create: isNew, id: $('of-id').value.trim(), name: $('of-name').value, note: $('of-note').value } })
          .then(() => { closeModal(); toast('Outlet saved'); route(); })
          .catch((e) => { showError(out, e); save.disabled = false; });
      });
      openModal(isNew ? 'Add outlet' : 'Edit outlet ' + rec.id, wrap);
    }
    const body = list.length ? table('Outlets', ['Outlet', 'Name', 'Staff', 'Created by · at', 'Edited by · at', ''],
      list.map((o) => [o.id, o.name || '', num(o.staff_count), stamp(o.created_by, o.created_at) === '–' ? o.source : stamp(o.created_by, o.created_at),
        stamp(o.edited_by, o.edited_at), editable ? iconBtn('✎', 'Edit outlet ' + o.id, () => openForm(o)) : '']))
      : h('div', { class: 'empty' }, h('b', { text: 'No outlets listed yet' }), 'Outlets appear after the first scan, or add one with +.');
    const add = editable ? h('button', { class: 'btn', type: 'button', onclick: () => openForm(null) }, '+ Add outlet') : null;
    view.replaceChildren(pageHead('Outlets', list.length + ' outlets', add), h('div', { class: 'card' }, body));
  }

  // ---- Reports: view in the app, beside download ----
  function viewReport(r) {
    const path = '/api/reports/view?path=' + encodeURIComponent(r.path);
    if (/\.html$/i.test(r.path)) {
      // srcdoc + empty sandbox: no scripts, and no cookies are needed for the frame.
      const box = h('div', {}, h('p', { class: 'sub', text: 'Loading…' }));
      openModal(r.name, box);
      $('modal').classList.add('wide');
      fetch(path, { credentials: 'same-origin' }).then((res) => {
        if (!res.ok) throw new ApiError(res.status, friendly(res.status));
        return res.text();
      }).then((html) => {
        box.replaceChildren(h('iframe', { class: 'report-frame', srcdoc: html, sandbox: '', title: r.name }));
      }).catch((e) => showError(box, e.status ? e : new ApiError(0, friendly(0))));
      return;
    }
    const holder = h('div', {}, h('p', { class: 'sub', text: 'Loading…' }));
    openModal(r.name, holder);
    $('modal').classList.add('wide');
    api(path).then((d) => {
      const tabs = h('div', { class: 'btn-row' });
      const area = h('div', {});
      function show(i) {
        const s = d.sheets[i];
        area.replaceChildren(s.rows.length ? table(s.name, s.rows[0].map((_, k) => 'Col ' + (k + 1)), s.rows)
          : h('div', { class: 'empty', text: 'This sheet is empty.' }),
        s.truncated ? h('p', { class: 'sub', text: 'Only the first rows are shown. Download the file to see all of it.' }) : null);
        Array.prototype.forEach.call(tabs.children, (b, k) => { b.className = k === i ? 'btn small' : 'btn ghost small'; });
      }
      d.sheets.forEach((s, i) => tabs.appendChild(h('button', { class: 'btn ghost small', type: 'button', onclick: () => show(i) }, s.name)));
      holder.replaceChildren(tabs, area);
      if (d.sheets.length) show(0);
    }).catch((e) => showError(holder, e));
  }

  async function vReports(view, live) {
    const list = await api('/api/reports');
    if (!live()) return;
    const rows = list.map((r) => {
      const actions = h('div', { class: 'btn-row-inline' });
      if (DEMO) actions.appendChild(h('span', { class: 'c-mute', text: 'Not in demo' }));
      else if (r.can_open === false) actions.appendChild(h('span', { class: 'c-mute', text: 'HRM and above' }));
      else {
        actions.appendChild(iconBtn('👁', 'View ' + r.name, () => viewReport(r)));
        actions.appendChild(h('a', { class: 'btn ghost small icon-btn', href: '/api/reports/download?path=' + encodeURIComponent(r.path),
          download: '', title: 'Download', 'aria-label': 'Download ' + r.name }, '⬇'));
      }
      return [r.name, r.kind || '', r.month || '', fmtSize(r.size), (r.created || '').replace('T', ' '), badge(r.status || '', badgeClass(r)), actions];
    });
    const body = list.length ? table('Reports', ['Report', 'Type', 'Month', 'Size', 'Created', 'Status', ''], rows)
      : h('div', { class: 'empty' }, h('b', { text: 'No reports yet' }), 'Reports appear after a scan has run.');
    const parts = [pageHead('Reports', 'View in the app or download', openFolderBtn('reports', 'Open reports folder')), h('div', { class: 'card' }, body)];
    if (can('scan')) {
      const scope = h('select', { id: 'rep-scope' }, h('option', { value: 'weekly', text: 'Latest week' }), h('option', { value: 'monthly', text: 'Latest month' }));
      const genOut = h('div', { 'aria-live': 'polite' });
      const gen = h('button', { class: 'btn', type: 'button' }, 'Create report');
      gen.addEventListener('click', () => {
        gen.disabled = true;
        api('/api/reports/generate', { method: 'POST', json: { scope: scope.value } }).then((r) => {
          genOut.replaceChildren(h('div', { class: 'msg good', role: 'status', text: (r && r.message) || 'The report is being created.' }));
          toast('Report requested');
        }).catch((e) => showError(genOut, e)).then(() => { gen.disabled = false; });
      });
      parts.push(h('div', { class: 'card' }, h('h2', { text: 'Create a report' }),
        h('div', { class: 'filters' }, h('div', {}, h('label', { for: 'rep-scope', text: 'What to include' }), scope), gen), genOut));
    }
    view.replaceChildren.apply(view, parts);
  }

  // ---- Rules: all rules in a table, + and edit for Master Admin and CEO ----
  function ruleValueText(v) {
    return Array.isArray(v) ? v.join(', ') : (v == null ? '' : String(v));
  }
  async function vRules(view, live) {
    const list = await api('/api/rules');
    if (!live()) return;
    const editable = can('rules_edit');
    const holder = h('div', {});
    const filter = h('select', { id: 'rule-filter', 'aria-label': 'Filter by status' },
      ['', 'CONFIRMED', 'ASSUMED', 'OPEN'].map((s) => h('option', { value: s, text: s || 'All statuses' })));
    function openForm(rec) {
      const isNew = !rec;
      const out = h('div', { 'aria-live': 'polite' });
      const save = h('button', { class: 'btn', type: 'button' }, 'Save');
      const kind = rec ? (typeof rec.value === 'boolean' ? 'bool' : (typeof rec.value === 'number' ? 'num' : (Array.isArray(rec.value) ? 'list' : 'text'))) : 'text';
      const valueField = kind === 'bool'
        ? selectField('rf-value', 'Value', [['true', 'Yes (true)'], ['false', 'No (false)']], String(rec.value))
        : field('rf-value', 'Value' + (kind === 'list' ? ' (separate with commas)' : kind === 'num' ? ' (number)' : ''), { value: rec ? ruleValueText(rec.value) : '' });
      const wrap = h('div', {},
        field('rf-id', 'Rule id (lower-case, digits, _)', { value: rec ? rec.id : '', disabled: !isNew }),
        field('rf-label', 'What the rule says', { value: rec ? rec.label : '' }),
        valueField,
        selectField('rf-status', 'Status', [['ASSUMED', 'ASSUMED (working default)'], ['OPEN', 'OPEN (no safe default)'], ['CONFIRMED', 'CONFIRMED (Client answered)']], rec ? rec.status : 'ASSUMED'),
        field('rf-question', 'Question number', { value: rec ? rec.question : '' }),
        field('rf-source', 'Source: who confirmed it, where (needed for CONFIRMED)', { value: rec ? rec.source : '' }),
        field('rf-note', 'Note', { value: rec ? rec.note : '' }),
        h('p', { class: 'sub', text: 'Saved to rules.toml after a check. Your name and the time are recorded. Rules marked "not built yet" are not read by any check.' }),
        h('div', { class: 'btn-row' }, save, h('button', { class: 'btn ghost', type: 'button', onclick: closeModal }, 'Cancel')), out);
      save.addEventListener('click', () => {
        let value = $('rf-value').value;
        if (kind === 'bool') value = value === 'true';
        else if (kind === 'num') { value = Number(value); if (value !== value || $('rf-value').value.trim() === '') { out.replaceChildren(h('div', { class: 'msg err', role: 'alert', text: 'Please type a number.' })); return; } }
        else if (kind === 'list') value = value.split(',').map((x) => x.trim()).filter(Boolean);
        const fields = { label: $('rf-label').value, value: value, status: $('rf-status').value, question: $('rf-question').value,
          source: $('rf-source').value, note: $('rf-note').value };
        save.disabled = true;
        const req = isNew ? { id: $('rf-id').value.trim(), fields: fields } : { id: rec.id, fields: fields };
        api(isNew ? '/api/rules/add' : '/api/rules/update', { method: 'POST', json: req })
          .then(() => { closeModal(); toast('Rule saved'); route(); })
          .catch((e) => { showError(out, e); save.disabled = false; });
      });
      openModal(isNew ? 'Add rule' : 'Edit rule ' + rec.id, wrap);
    }
    function render() {
      const want = filter.value;
      const rows = list.filter((r) => !want || r.status === want).map((r) => [
        h('b', { text: r.id }), r.label, ruleValueText(r.value),
        badge(r.status, r.status === 'CONFIRMED' ? 'ok' : r.status === 'OPEN' ? 'red' : 'amb'),
        r.question || '', r.used_in || '', stamp(r.edited_by || r.created_by, r.edited_at || r.created_at),
        editable ? iconBtn('✎', 'Edit rule ' + r.id, () => openForm(r)) : '']);
      holder.replaceChildren(rows.length ? table('Rules', ['Rule', 'Meaning', 'Value', 'Status', 'Q', 'Used in', 'Last edited by · at', ''], rows)
        : h('div', { class: 'empty', text: 'No rules match.' }));
    }
    filter.addEventListener('change', render);
    const note = editable ? 'Only Master Admin and CEO can edit rules. Every change is checked and recorded.'
      : 'Rules are read-only for your role. Ask the Master Admin or the CEO to change one.';
    const add = editable ? h('button', { class: 'btn', type: 'button', onclick: () => openForm(null) }, '+ Add rule') : null;
    view.replaceChildren(pageHead('Rules', list.length + ' rules', add),
      h('div', { class: 'card' }, h('p', { class: 'sub', text: note }), h('div', { class: 'filters' }, filter), holder));
    render();
  }

  // ---- Log files ----
  async function vLogs(view, live) {
    const startInput = h('input', { type: 'date', id: 'log-start' });
    const endInput = h('input', { type: 'date', id: 'log-end' });
    const holder = h('div', { 'aria-live': 'polite' });
    const viewer = h('div', { class: 'log-viewer' });

    async function loadList() {
      holder.replaceChildren(h('p', { class: 'sub', text: 'Loading…' }));
      try {
        const files = await api('/api/logs' + qs({ start: startInput.value, end: endInput.value }));
        if (!live()) return;
        if (!files.length) {
          holder.replaceChildren(h('div', { class: 'empty' }, h('b', { text: 'No log files found' }), 'Try a different date range.'));
          return;
        }
        const rows = files.map((f) => {
          const viewBtn = h('button', { class: 'btn ghost small', type: 'button', title: 'View' }, '👁 View');
          viewBtn.addEventListener('click', () => viewLog(f.path, f.name));
          const dlLink = h('a', { class: 'btn ghost small', href: '/api/logs/download?path=' + encodeURIComponent(f.path), download: '', title: 'Download' }, '⬇');
          return [f.name, fmtSize(f.size), f.modified || '', h('div', { class: 'btn-row-inline' }, viewBtn, dlLink)];
        });
        holder.replaceChildren(table('Log files', ['File', 'Size', 'Modified', ''], rows));
      } catch (e) {
        if (e.status !== 401) holder.replaceChildren(h('div', { class: 'msg err', role: 'alert', text: e.message }));
      }
    }

    async function viewLog(path, name) {
      viewer.replaceChildren(h('p', { class: 'sub', text: 'Loading ' + name + '…' }));
      try {
        const data = await api('/api/logs/view?path=' + encodeURIComponent(path));
        viewer.replaceChildren(
          h('div', { class: 'card' },
            h('div', { class: 'bar flush' }, h('h2', { text: data.name }), h('button', { class: 'btn ghost small', type: 'button', onclick: () => viewer.replaceChildren() }, 'Close')),
            h('pre', { class: 'log-content', text: data.content })
          )
        );
      } catch (e) {
        if (e.status !== 401) viewer.replaceChildren(h('div', { class: 'msg err', role: 'alert', text: e.message }));
      }
    }

    startInput.addEventListener('change', loadList);
    endInput.addEventListener('change', loadList);

    view.replaceChildren(
      pageHead('Log files', 'View and download system logs', openFolderBtn('logs', 'Open logs folder')),
      h('div', { class: 'card' },
        h('div', { class: 'filters' },
          h('div', {}, h('label', { for: 'log-start', text: 'From' }), startInput),
          h('div', {}, h('label', { for: 'log-end', text: 'To' }), endInput),
          h('button', { class: 'btn ghost', type: 'button', onclick: loadList }, 'Filter')
        )
      ),
      h('div', { class: 'card' }, holder),
      viewer
    );
    await loadList();
  }

  // ---- Help pop-up: (a) ask the knowledge base, (b) IT diagnosis and repair ----
  function showHelp() {
    const wrapper = h('div', { class: 'help' });
    const tabs = h('div', { class: 'btn-row help-tabs' });
    const body = h('div', {});
    const names = [['ask', 'Ask the help bot']];
    if (can('it_diagnose')) names.push(['it', 'IT diagnosis and repair']);
    const buttons = {};
    names.forEach((n) => {
      buttons[n[0]] = h('button', { class: 'btn ghost small', type: 'button', onclick: () => select(n[0]) }, n[1]);
      tabs.appendChild(buttons[n[0]]);
    });
    function select(which) {
      Object.keys(buttons).forEach((k) => { buttons[k].className = k === which ? 'btn small' : 'btn ghost small'; });
      body.replaceChildren(which === 'ask' ? helpBot() : itPanel());
    }
    wrapper.appendChild(tabs);
    wrapper.appendChild(body);
    openModal('Help', wrapper);
    select('ask');
  }

  function helpBot() {
    const log = h('div', { class: 'chat', role: 'log', 'aria-live': 'polite' });
    const input = h('input', { id: 'chat-q', type: 'text', placeholder: 'Ask about a rule or flag, for example: late grace', autocomplete: 'off' });
    const send = h('button', { class: 'btn', type: 'button' }, 'Ask');
    const bubble = (who, kids) => h('div', { class: 'bubble ' + who }, kids);
    log.appendChild(bubble('bot', h('p', { text: 'Hello. Ask me about Client attendance rules and flags. I answer only from the rules and guides, and I say so when a rule is still only assumed.' })));
    async function ask() {
      const q = input.value.trim();
      if (!q) return;
      input.value = '';
      log.appendChild(bubble('me', h('p', { text: q })));
      try {
        const r = await api('/api/help' + qs({ q: q }));
        const hits = (r || []).filter((x) => !x.no_match);
        const hint = (r || []).find((x) => x.no_match);
        if (!hits.length) {
          log.appendChild(bubble('bot', h('p', { text: (hint && hint.snippet) || 'I could not find that. Try a simpler word, such as late or missing.' })));
        } else {
          log.appendChild(bubble('bot', hits.slice(0, 3).map((x) => h('div', { class: 'result' },
            h('b', { text: x.title }), h('small', { text: [x.source, x.status].filter(Boolean).join(' · ') }), h('p', { text: x.snippet || '' })))));
        }
      } catch (e) {
        if (e.status !== 401) log.appendChild(bubble('bot', h('p', { class: 'c-red', text: e.message })));
      }
      log.scrollTop = log.scrollHeight;
    }
    send.addEventListener('click', ask);
    input.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); ask(); } });
    return h('div', {}, log, h('div', { class: 'chat-input' }, h('label', { class: 'vh', for: 'chat-q', text: 'Your question' }), input, send));
  }

  function itPanel() {
    const out = h('div', { 'aria-live': 'polite' });
    const diagBtn = h('button', { class: 'btn', type: 'button' }, 'Run diagnosis');
    const propBtn = h('button', { class: 'btn ghost', type: 'button' }, 'Show proposed repairs');
    const repair = h('div', {});
    function busy(btn, p) { btn.disabled = true; return p.then((x) => { btn.disabled = false; return x; }, (e) => { btn.disabled = false; throw e; }); }
    diagBtn.addEventListener('click', () => {
      out.replaceChildren(h('p', { class: 'sub', text: 'Checking…' }));
      busy(diagBtn, api('/api/it/diagnose', { method: 'POST', json: {} })).then((r) => {
        out.replaceChildren(h('p', { class: 'sub', text: 'Overall: ' + (r.overall || r.status || 'unknown') }),
          table('Diagnosis', ['Check', 'Result', 'Detail'], (r.results || []).map((x) => [String(x.check || '').replace(/_/g, ' '),
            badge(x.status || '', /fail/i.test(x.status) ? 'red' : /warn/i.test(x.status) ? 'amb' : 'ok'), String(x.detail || '')])));
      }).catch((e) => showError(out, e));
    });
    propBtn.addEventListener('click', () => {
      out.replaceChildren(h('p', { class: 'sub', text: 'Looking for problems…' }));
      repair.replaceChildren();
      busy(propBtn, api('/api/it/propose', { method: 'POST', json: {} })).then((r) => {
        const inc = r.incidents || [];
        if (!inc.length) { out.replaceChildren(h('div', { class: 'empty' }, h('b', { text: 'Nothing to repair' }), 'No problem was found that needs a fix.')); return; }
        out.replaceChildren(h('p', { class: 'sub', text: 'Nothing has been changed yet. Only safe, listed repairs can be applied, and only after you approve.' }),
          table('Proposed repairs', ['Problem', 'Risk', 'Proposed fix', 'State'], inc.map((i) => [
            String(i['class'] || i.id || ''), String(i.fix_risk || ''), String(i.proposed_fix || ''), String(i.state || '')])));
        const pw = h('input', { id: 'it-pw', type: 'password', autocomplete: 'current-password' });
        const go = h('button', { class: 'btn', type: 'button' }, 'Approve and repair');
        go.addEventListener('click', () => {
          go.disabled = true;
          api('/api/it/repair', { method: 'POST', json: { confirm: true, password: pw.value } }).then((rr) => {
            pw.value = '';
            const list = rr.incidents || [];
            repair.replaceChildren(h('div', { class: 'msg good', role: 'status', text: 'Repair finished. ' + list.length + ' item(s) handled.' }),
              table('Result', ['Problem', 'State', 'Applied'], list.map((i) => [String(i['class'] || i.id || ''), String(i.state || ''),
                Array.isArray(i.applied) ? i.applied.length + ' change(s)' : String(i.applied || '–')])));
          }).catch((e) => showError(repair, e)).then(() => { go.disabled = false; });
        });
        repair.replaceChildren(h('div', { class: 'field' }, h('label', { for: 'it-pw', text: 'Type your password to approve' }), pw),
          h('div', { class: 'btn-row' }, go));
      }).catch((e) => showError(out, e));
    });
    return h('div', {}, h('p', { class: 'sub', text: 'Checks the app, the folders and the logs. It never edits data files and never deletes anything.' }),
      h('div', { class: 'btn-row' }, diagBtn, propBtn), out, repair);
  }

  // ---- Settings: account, password, office access, users ----
  async function vSettings(view, live) {
    const me = state.me;
    const parts = [];
    const must = !!me.must_change;
    parts.push(pageHead('Settings', must ? 'Please choose your own password before you continue' : ''));

    // account (the master admin may edit their own name; nobody else can)
    const accOut = h('div', { 'aria-live': 'polite' });
    const acc = h('div', { class: 'card' }, h('h2', { text: 'Your account' }),
      h('dl', { class: 'kv' }, h('dt', { text: 'Username' }), h('dd', { text: me.username }),
        h('dt', { text: 'Role' }), h('dd', { text: me.role_label || ROLE_NAME[me.role] || me.role }),
        h('dt', { text: 'Name' }), h('dd', { text: me.display_name || me.username })));
    if (me.is_master) {
      const nm = field('s-name', 'Your name', { value: me.display_name || '', maxlength: 80 });
      const nb = h('button', { class: 'btn', type: 'button' }, 'Save name');
      nb.addEventListener('click', () => {
        nb.disabled = true;
        api('/api/profile', { method: 'POST', json: { display_name: $('s-name').value } }).then(() => {
          me.display_name = $('s-name').value.trim();
          accOut.replaceChildren(h('div', { class: 'msg good', role: 'status', text: 'Name saved.' }));
        }).catch((e) => showError(accOut, e)).then(() => { nb.disabled = false; });
      });
      acc.appendChild(nm);
      acc.appendChild(h('div', { class: 'btn-row' }, nb));
    }
    acc.appendChild(accOut);
    parts.push(acc);

    // change password (only here)
    const pwOut = h('div', { 'aria-live': 'polite' });
    const pwF = (id, label) => [h('label', { for: id, text: label }), h('input', { id: id, type: 'password', autocomplete: id === 's-pw-old' ? 'current-password' : 'new-password' })];
    const pwBtn = h('button', { class: 'btn', type: 'button' }, 'Change password');
    pwBtn.addEventListener('click', () => {
      const o = $('s-pw-old').value, n = $('s-pw-new').value;
      if (n !== $('s-pw-new2').value) { pwOut.replaceChildren(h('div', { class: 'msg err', role: 'alert', text: 'The two new passwords do not match.' })); return; }
      pwBtn.disabled = true;
      api('/api/password', { method: 'POST', json: { old: o, new: n } }).then(() => {
        ['s-pw-old', 's-pw-new', 's-pw-new2'].forEach((i) => { $(i).value = ''; });
        pwOut.replaceChildren(h('div', { class: 'msg good', role: 'status', text: 'Password changed.' }));
        if (me.must_change) { me.must_change = false; go('overview'); loadBadge(); }
      }).catch((e) => showError(pwOut, e)).then(() => { pwBtn.disabled = false; });
    });
    parts.push(h('div', { class: 'card' }, h('h2', { text: 'Change password' }),
      pwF('s-pw-old', must ? 'Temporary password you were given' : 'Current password'),
      pwF('s-pw-new', 'New password (at least 10 characters)'), pwF('s-pw-new2', 'Repeat new password'),
      h('div', { class: 'btn-row' }, pwBtn), pwOut));

    if (!must && can('lan_view')) {
      try {
        const lan = await api('/api/lan-info');
        if (lan) parts.push(lanCard(lan));
      } catch (e) { if (e.status === 401) throw e; }
    }
    if (!must && can('users_manage')) {
      const usersCard = await buildUsersCard(live);
      if (usersCard) parts.push(usersCard);
    }
    if (!must && can('users_security')) {
      const sec = await securityCard();
      if (sec) parts.push(sec);
    }
    if (!must && can('rules_edit')) {
      parts.push(h('div', { class: 'card' }, h('h2', { text: 'Master settings' }),
        h('p', { class: 'sub', text: 'The attendance rules and office access are changed only by the Master Admin and the CEO.' }),
        h('div', { class: 'btn-row' }, h('a', { class: 'btn ghost', href: '#/rules' }, 'Open the rules'))));
    }
    if (!live()) return;
    view.replaceChildren.apply(view, parts);
  }

  async function buildUsersCard(live) {
    let data;
    try { data = await api('/api/users'); } catch (e) {
      if (e.status === 401) throw e;
      return h('div', { class: 'card' }, h('h2', { text: 'Users' }), h('div', { class: 'msg err', text: e.message }));
    }
    if (!live()) return null;
    const holder = h('div', {});
    const roleOpts = () => data.assignable.map((r) => [r, ROLE_NAME[r] || r]);

    async function reload() { data = await api('/api/users'); renderList(); }
    function renderList() {
      const rows = data.users.map((u) => {
        const actions = h('div', { class: 'btn-row-inline' });
        if (u.can_manage) {
          actions.appendChild(h('button', { class: 'btn ghost small', type: 'button', onclick: () => showEdit(u) }, 'Edit'));
          actions.appendChild(h('button', { class: 'btn ghost small danger', type: 'button', onclick: () => confirmDelete(u) }, 'Delete'));
        } else if (u.is_master) {
          actions.appendChild(h('span', { class: 'c-mute', text: 'Protected' }));
        }
        if (u.locked && (u.can_manage || state.me.is_master)) {
          actions.appendChild(h('button', { class: 'btn ghost small', type: 'button', onclick: () => {
            api('/api/users/unlock', { method: 'POST', json: { username: u.username } }).then(() => { toast('Unlocked'); return reload(); })
              .catch((e) => toast(e.message));
          } }, 'Unlock'));
        }
        const pw = !u.password_set ? badge('Not set', 'red') : (u.must_change ? badge('Set, must change', 'amb') : badge('Set', 'ok'));
        return [h('b', { text: u.display_name || u.username }), u.username, ROLE_NAME[u.role] || u.role,
          h('span', {}, pw, u.locked ? badge('Locked', 'red') : null),
          stamp(u.created_by, u.created_at), (u.last_login || '').replace('T', ' ').slice(0, 16) || 'Never', actions];
      });
      holder.replaceChildren(table('Users', ['Name', 'Username', 'Role', 'Password', 'Created by · at', 'Last sign-in', ''], rows));
    }
    function showAdd() {
      const out = h('div', { 'aria-live': 'polite' });
      const save = h('button', { class: 'btn', type: 'button' }, 'Add user');
      const wrap = h('div', {},
        field('au-name', 'Name', { maxlength: 80 }), field('au-user', 'Username (to sign in)', { maxlength: 64, autocapitalize: 'none' }),
        selectField('au-role', 'Role', roleOpts(), null),
        h('div', { class: 'field' }, h('label', { for: 'au-pw', text: 'Initial password (type it; at least 10 characters)' }),
          h('input', { id: 'au-pw', type: 'password', autocomplete: 'new-password' })),
        h('p', { class: 'sub', text: 'The person must change this password at their first sign-in. Passwords are stored scrambled and can never be shown again.' }),
        h('div', { class: 'btn-row' }, save, h('button', { class: 'btn ghost', type: 'button', onclick: closeModal }, 'Cancel')), out);
      save.addEventListener('click', () => {
        save.disabled = true;
        api('/api/users/add', { method: 'POST', json: { username: $('au-user').value.trim(), role: $('au-role').value,
          password: $('au-pw').value, display_name: $('au-name').value } })
          .then(() => { closeModal(); toast('User added'); return reload(); })
          .catch((e) => { showError(out, e); save.disabled = false; });
      });
      openModal('Add user', wrap);
    }
    function showEdit(u) {
      const out = h('div', { 'aria-live': 'polite' });
      const save = h('button', { class: 'btn', type: 'button' }, 'Save');
      const opts = roleOpts();
      if (!opts.some((o) => o[0] === u.role)) opts.push([u.role, ROLE_NAME[u.role] || u.role]);
      const wrap = h('div', {},
        field('eu-name', 'Name', { value: u.display_name, maxlength: 80 }),
        selectField('eu-role', 'Role', opts, u.role),
        h('p', { class: 'sub', text: 'Password: ' + (u.password_set ? 'set' : 'not set') + '. Passwords cannot be shown. To reset, type a new initial password below; the person must change it at next sign-in.' }),
        h('div', { class: 'field' }, h('label', { for: 'eu-pw', text: 'New initial password (leave empty to keep)' }),
          h('input', { id: 'eu-pw', type: 'password', autocomplete: 'new-password' })),
        h('div', { class: 'btn-row' }, save, h('button', { class: 'btn ghost', type: 'button', onclick: closeModal }, 'Cancel')), out);
      save.addEventListener('click', () => {
        const body = { username: u.username, display_name: $('eu-name').value };
        if ($('eu-role').value !== u.role) body.role = $('eu-role').value;
        if ($('eu-pw').value) body.password = $('eu-pw').value;
        save.disabled = true;
        api('/api/users/update', { method: 'POST', json: body })
          .then(() => { closeModal(); toast('User updated'); return reload(); })
          .catch((e) => { showError(out, e); save.disabled = false; });
      });
      openModal('Edit ' + u.username, wrap);
    }
    function confirmDelete(u) {
      const out = h('div', { 'aria-live': 'polite' });
      openModal('Delete user', h('div', {},
        h('p', { text: 'Delete the account "' + u.username + '"? They will be signed out and cannot sign in again.' }),
        h('div', { class: 'btn-row' },
          h('button', { class: 'btn danger-fill', type: 'button', onclick: () => {
            api('/api/users/delete', { method: 'POST', json: { username: u.username } })
              .then(() => { closeModal(); toast('User deleted'); return reload(); }).catch((e) => showError(out, e));
          } }, 'Delete'),
          h('button', { class: 'btn ghost', type: 'button', onclick: closeModal }, 'Cancel')), out));
    }
    renderList();
    return h('div', { class: 'card' },
      h('div', { class: 'bar flush' }, h('h2', { text: 'Users' }), h('button', { class: 'btn', type: 'button', onclick: showAdd }, '+ Add user')),
      h('p', { class: 'sub', text: 'You can add: ' + data.assignable.map((r) => ROLE_NAME[r] || r).join(', ') + '. The Master Admin account cannot be edited or deleted by anyone else.' }),
      holder);
  }

  // Master only, read-only: who can sign in and when. Hashes and salts are never sent.
  async function securityCard() {
    let d;
    try { d = await api('/api/users/security'); } catch (e) { if (e.status === 401) throw e; return null; }
    const fmt = (t) => String(t || '').replace('T', ' ').slice(0, 16) || '–';
    return h('div', { class: 'card' }, h('h2', { text: 'Sign-in security (read-only)' }),
      h('p', { class: 'sub', text: d.note + ' To open the raw file, use the app PC itself (07_LOGS\\webapp\\users.json); it cannot be downloaded here.' }),
      table('Sign-in security', ['Username', 'Role', 'Password', 'Last password change', 'Last sign-in', 'Locked out', 'Must change'],
        d.users.map((u) => [u.username, ROLE_NAME[u.role] || u.role, u.password_set ? badge('Set', 'ok') : badge('Not set', 'red'),
          fmt(u.password_changed_at), fmt(u.last_login), u.locked ? badge('Locked', 'red') : 'No', u.must_change ? 'Yes' : 'No'])));
  }

  function lanCard(lan) {
    const out = h('div', { 'aria-live': 'polite' });
    const want = !lan.setting;
    const kids = [h('h2', { text: 'Office network access' }),
      h('p', { class: 'sub', text: 'Lets other PCs on the same office network open this app. Sign-in is always required. The connection is not encrypted.' }),
      h('dl', { class: 'kv' }, h('dt', { text: 'Now' }), h('dd', { text: lan.running ? 'ON: ' + lan.url : 'OFF (this PC only)' }),
        h('dt', { text: 'Setting' }), h('dd', { text: lan.setting ? 'ON at next start' : 'OFF' }))];
    if (lan.can_change) {
      const pw = h('input', { type: 'password', id: 'lan-pw', autocomplete: 'current-password' });
      const btn = h('button', { class: 'btn', type: 'button' }, want ? 'Turn office access ON' : 'Turn office access OFF');
      btn.addEventListener('click', () => {
        btn.disabled = true;
        api('/api/lan-set', { method: 'POST', json: { enabled: want, password: pw.value } }).then(() => {
          pw.value = '';
          out.replaceChildren(h('div', { class: 'msg good', role: 'status', text: 'Saved. It takes effect the next time the app is started from the desktop icon.' }));
        }).catch((e) => showError(out, e)).then(() => { btn.disabled = false; });
      });
      kids.push(h('label', { for: 'lan-pw', text: 'Type your password to change it' }), pw, h('div', { class: 'btn-row' }, btn));
    } else {
      kids.push(h('p', { class: 'sub', text: 'Only the Master Admin can turn this on or off.' }));
    }
    kids.push(out);
    return h('div', { class: 'card' }, kids);
  }

  async function vHealth(view, live) {
    const hd = await api('/api/health');
    if (!live()) return;
    const keys = hd && typeof hd === 'object' ? Object.keys(hd).filter((k) => k !== 'results') : [];
    const body = keys.length ? h('dl', { class: 'kv' }, keys.map((k) => [
      h('dt', { text: k.replace(/_/g, ' ') }),
      h('dd', { text: typeof hd[k] === 'object' ? Object.keys(hd[k]).map((c) => c + ' ' + hd[k][c]).join(', ') : String(hd[k]) })]))
      : h('div', { class: 'empty', text: 'No health information was returned.' });
    const parts = [pageHead('System health', 'Admin only'), h('div', { class: 'card' }, body)];
    if (hd && Array.isArray(hd.results) && hd.results.length) {
      parts.push(h('div', { class: 'card' }, h('h2', { text: 'Checks' }),
        table('Health checks', ['Check', 'Result', 'Detail'], hd.results.map((r) => [
          String(r.check || '').replace(/_/g, ' '),
          badge(r.status || '', /fail/i.test(r.status) ? 'red' : /warn/i.test(r.status) ? 'amb' : 'ok'),
          String(r.detail || '')]))));
    }
    view.replaceChildren.apply(view, parts);
  }

  // ---------- login / logout / boot ----------
  async function logout() {
    try { await api('/api/logout', { method: 'POST', json: {} }); } catch (e) { /* signing out anyway */ }
    state.me = null; state.openCount = null; state.week = null;
    go('login');
    route();
  }
  function initLogin() {
    $('login-form').addEventListener('submit', async (e) => {
      e.preventDefault();
      const err = $('login-error');
      err.hidden = true;
      const user = $('login-user').value.trim(), pass = $('login-pass').value;
      if (!user || !pass) { err.textContent = 'Please type your username and password.'; err.hidden = false; return; }
      $('login-submit').disabled = true;
      try {
        await api('/api/login', { method: 'POST', json: { username: user, password: pass } });
        $('login-pass').value = '';
        state.me = await api('/api/me');
        go('overview');
        route();
        loadBadge();
      } catch (ex) {
        err.textContent = ex.status === 401 || ex.status === 403 ? 'That username or password is not right. Please try again.' : ex.message;
        err.hidden = false;
      }
      $('login-submit').disabled = false;
    });
  }

  async function boot() {
    initLogin();
    $('drawer-close').addEventListener('click', closeDrawer);
    $('drawer-backdrop').addEventListener('click', closeDrawer);
    $('modal-close').addEventListener('click', closeModal);
    $('modal-backdrop').addEventListener('click', closeModal);
    document.addEventListener('keydown', trapKeys);
    window.addEventListener('hashchange', route);
    if (DEMO) {
      state.me = demoMe('hrm');
    } else {
      try { state.me = await api('/api/me'); } catch (e) { state.me = null; }
      if ('serviceWorker' in navigator && /^https?:$/.test(window.location.protocol)) {
        navigator.serviceWorker.register('sw.js').catch(() => {});
      }
    }
    route();
    loadBadge();
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot); else boot();
})();
