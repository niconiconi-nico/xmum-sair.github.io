/* XMUM SAIR 生存指南 — page-wide behaviour: the header blocks and their Courses menu, the light/dark toggle,
   and the upload helper (search is in search.js, ratings in ratings.js, the rating form in rate.js). */
(function () {
  'use strict';
  var root = document.documentElement;
  var base = document.body.dataset.base || '/';

  // 1. mark the header block that covers the current URL; a programme page also lights up the Courses block
  var here = location.pathname.replace(/\/index\.html$/, '/').replace(/\/blog\/$/, '/qa/');   // the blog sits under Discussion
  var best = null;
  document.querySelectorAll('.site-header a.nb[href]').forEach(function (a) {
    var p = a.getAttribute('href');
    if (p.indexOf('http') === 0 || p === base) return;
    if (here === p || here.indexOf(p) === 0) {
      if (!best || p.length > best.getAttribute('href').length) best = a;
    }
  });
  var menuBtn = document.querySelector('.nb[data-menu]');
  var menu = menuBtn && document.getElementById(menuBtn.dataset.menu);
  if (best) {
    best.setAttribute('aria-current', 'page');
    if (menu && menu.contains(best)) menuBtn.setAttribute('aria-current', 'true');
  }

  // 1b. the Courses block opens a row of programme blocks instead of leaving the page (no script: it is a plain link)
  if (menuBtn && menu) {
    var setOpen = function (open) { menu.hidden = !open; menuBtn.setAttribute('aria-expanded', open ? 'true' : 'false'); };
    menuBtn.addEventListener('click', function (e) { e.preventDefault(); setOpen(menu.hidden); });
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && !menu.hidden) { setOpen(false); menuBtn.focus(); }
    });
    document.addEventListener('click', function (e) {
      if (!menu.hidden && !menu.contains(e.target) && !menuBtn.contains(e.target)) setOpen(false);
    });
  }

  // 2. light / dark: follow the system unless the visitor chose; the choice is kept in localStorage
  function stored() { try { return localStorage.getItem('theme'); } catch (e) { return null; } }
  function effective() {
    var t = stored();
    if (t === 'light' || t === 'dark') return t;
    return window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
  }
  var btn = document.querySelector('.theme-toggle');
  function paint() {
    var t = effective();
    if (btn) {
      btn.textContent = t === 'dark' ? '☀️' : '🌙';
      btn.setAttribute('aria-label', t === 'dark' ? '切换到浅色 | Switch to light' : '切换到深色 | Switch to dark');
      btn.title = btn.getAttribute('aria-label');
    }
  }
  if (btn) {
    btn.addEventListener('click', function () {
      var next = effective() === 'dark' ? 'light' : 'dark';
      root.setAttribute('data-theme', next);
      try { localStorage.setItem('theme', next); } catch (e) { /* private mode: the choice lasts for this page only */ }
      paint();
    });
  }
  paint();

  // 3. /upload/: suggest known courses and lecturers, take anything else as new, then open the GitHub form filled in
  var up = document.getElementById('up-form'), upData = document.getElementById('up-data');
  if (up && upData) {
    var D = JSON.parse(upData.textContent);
    var $ = function (id) { return document.getElementById(id); };
    var code = $('up-code'), name = $('up-name'), nameRow = $('up-name-row'), note = $('up-course-note'), lect = $('up-lect'),
        lectList = $('up-lects'), year = $('up-year'), term = $('up-term'), other = $('up-other'), type = $('up-type'),
        assess = $('up-assess'), assessRow = $('up-assess-row'), err = $('up-error');
    var CODE = /^[A-Z]{1,4}(?:\d{3,4}|\d\.\d)$/;
    var norm = function (s) { return s.trim().toUpperCase().replace(/\s+/g, ''); };
    var fillLecturers = function (first) {   // this course's lecturers on top, everyone else after them
      while (lectList.firstChild) lectList.removeChild(lectList.firstChild);
      var add = function (n, label) { var o = document.createElement('option'); o.value = n; if (label) o.label = label; lectList.appendChild(o); };
      first.forEach(function (n) { add(n, '教过这门课 | taught this course'); });
      D.lecturers.forEach(function (n) { if (first.indexOf(n) < 0) add(n); });
    };
    var onCode = function () {
      var c = norm(code.value), hit = D.courses[c];
      if (hit) {
        note.textContent = '✓ 已有课程 | Existing course: ' + c + ' ' + hit.name + ' · ' + hit.prog;
        name.value = hit.name; name.dataset.auto = '1'; nameRow.hidden = true;
        fillLecturers(hit.lecturers);
        return;
      }
      if (name.dataset.auto) { name.value = ''; delete name.dataset.auto; }
      nameRow.hidden = false;
      fillLecturers([]);
      if (!c) note.textContent = '';
      else if (!CODE.test(c)) note.textContent = '代码的样子：COM107、EEE212、MPU1.1、G0191 | Codes look like COM107, EEE212, MPU1.1, G0191';
      else note.textContent = '新课程：会自动建好文件夹，请填课程名' + (/^(G|MPU)/.test(c) ? '；G/MPU 开头的放进公共课' : '')
        + ' | New course: a folder is made for it; please add its name' + (/^(G|MPU)/.test(c) ? ' (G/MPU courses go to University-wide)' : '');
    };
    code.addEventListener('input', onCode);
    code.addEventListener('change', onCode);
    term.addEventListener('change', function () { other.hidden = term.value !== 'other'; if (!other.hidden) other.focus(); });
    type.addEventListener('change', function () { assessRow.hidden = type.value !== 'Past-Papers'; });
    up.addEventListener('submit', function (e) {
      e.preventDefault();
      // every field here is a hint and the GitHub form asks for the programme itself, so nothing is required: an empty
      // hint is left out of the form, and one in an odd shape (2024/04, swe 404) goes through for the bot to tidy
      var c = norm(code.value);
      var session = term.value === 'other' ? other.value.trim() : (year.value && term.value ? year.value + '-' + term.value : '');
      err.textContent = '';
      var p = new URLSearchParams();
      p.set('template', 'upload.yml');
      p.set('title', ['[上传 | Upload]', c, session, type.value].filter(Boolean).join(' '));
      if (c) p.set('code', c);
      if (name.value.trim()) p.set('name', name.value.trim());
      if (session) p.set('session', session);
      if (type.value) p.set('type', type.value);
      if (lect.value.trim()) p.set('lecturer', lect.value.trim());
      if (type.value === 'Past-Papers' && assess.value.trim()) p.set('assessment', assess.value.trim());
      location.href = up.dataset.form.split('?')[0] + '?' + p.toString().replace(/\+/g, '%20');
    });
    onCode();
  }
})();
