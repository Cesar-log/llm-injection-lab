const $ = id => document.getElementById(id);
let game = null;     // last /api/state
let current = 1;     // selected contact
let busy = false;
let pendingFile = null;  // {name, text} waiting to be sent

function setFile(f) {
  pendingFile = f;
  $('filechip').style.display = f ? 'flex' : 'none';
  $('filename').textContent = f ? '📎 ' + f.name : '';
  $('fileinp').value = '';
}
$('attach').onclick = () => $('fileinp').click();
$('unattach').onclick = () => setFile(null);
$('fileinp').onchange = () => {
  const f = $('fileinp').files[0];
  if (!f) return;
  const max = game ? game.limits.file : 4000;
  // Cheap check first, so a huge file is never loaded into memory.
  if (f.size > max * 4) { alert(`File too large (max. ${max} characters).`); setFile(null); return; }
  const r = new FileReader();
  r.onload = () => {
    if (!r.result.trim()) { alert('Empty file: write something in the .txt before attaching it.'); setFile(null); return; }
    if (r.result.length > max) { alert(`File too large: ${r.result.length} characters (max. ${max}).`); setFile(null); return; }
    setFile({ name: f.name, text: r.result });
  };
  r.readAsText(f);
};

const now = () => new Date().toTimeString().slice(0, 5);

async function api(path, body) {
  const opts = body === undefined ? {} : {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body)
  };
  try {
    const r = await fetch(path, opts);
    return { ok: r.ok, data: await r.json().catch(() => ({})) };
  } catch {
    return { ok: false, data: { error: 'The game server is down. Run "python app.py" and reload the page.' } };
  }
}

// Make a div behave like a button for mouse AND keyboard (Tab + Enter/Space).
function clickable(el, fn) {
  el.tabIndex = 0; el.setAttribute('role', 'button');
  el.onclick = fn;
  el.onkeydown = e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); fn(); } };
}

// ---------- tabs ----------
document.querySelectorAll('.tab').forEach(t => clickable(t, () => {
  document.querySelectorAll('.tab').forEach(x => x.classList.remove('active'));
  document.querySelectorAll('.screen').forEach(x => x.classList.remove('active'));
  t.classList.add('active'); $(t.dataset.s).classList.add('active');
}));

// ---------- state ----------
let firstLoad = true;
let pollTimer = null;

async function refresh() {
  const { ok, data } = await api('/api/state');
  if (!ok) {
    if (!game) { $('msgs').replaceChildren(bubble('sys', '⚠️ ' + (data.error || 'Server error.'))); return false; }
    // The server went away while a reply was pending: say so and keep trying.
    if (!busy && $('typing') && $('typing').style.display !== 'none') {
      $('secs').textContent = 'no connection, trying again…';
      clearTimeout(pollTimer); pollTimer = setTimeout(refresh, 3000);
    }
    return false;
  }
  game = data;
  $('inp').maxLength = game.limits.message;
  const open = game.contacts.filter(c => c.unlocked);
  const furthest = () => (open.find(c => !c.cleared) || open[open.length - 1]).number;
  if (firstLoad) {  // open the furthest contact the player has reached
    firstLoad = false;
    current = furthest();
    warmUp(current);
  } else if (!busy && !open.some(c => c.number === current)) {
    // Another tab pressed Restart: this contact is locked again.
    current = furthest(); setFile(null); warmUp(current);
  }
  renderProgress(); renderContacts();
  if (!busy) renderChat();  // never wipe the chat while a reply is on its way
  return true;
}

function renderProgress() {
  const p = $('progress'); p.innerHTML = '';
  let next = 1;
  while (game.cleared.includes(next) && next < game.total) next++;
  for (let n = 1; n <= game.total; n++) {
    const d = document.createElement('div');
    d.className = 'dot' + (game.cleared.includes(n) ? ' done' : n === next ? ' cur' : '');
    d.textContent = n === game.total ? '👑' : n;
    p.appendChild(d);
  }
  const b = document.createElement('button');
  b.className = 'restart'; b.textContent = 'Restart';
  b.onclick = async () => {
    if (busy) return;
    if (!confirm('Erase all progress and chats?')) return;
    const { ok, data } = await api('/api/restart', {});
    if (!ok) { alert(data.error || 'Server error. Your progress was not erased.'); return; }
    current = 1; firstLoad = false; logout(); refresh();
    store.del('acme-intro'); showIntro();
  };
  p.appendChild(b);
}

function renderContacts() {
  const box = $('contacts');
  box.querySelectorAll('.contact').forEach(c => c.remove());
  for (const c of game.contacts) {
    const el = document.createElement('div');
    el.className = 'contact' + (c.unlocked ? '' : ' locked') + (c.number === current ? ' sel' : '');
    const last = c.log && c.log.length ? c.log[c.log.length - 1].text : c.greeting;
    const tag = c.cleared ? '✅' : c.unlocked ? '' : '🔒';
    const sub = c.unlocked ? last : 'Locked';
    el.innerHTML = `<div class="avatar"></div><div class="cinfo"><div class="cname"><span></span><small></small></div><div class="clast"></div></div>`;
    el.querySelector('.avatar').textContent = c.avatar;
    el.querySelector('.cname span').textContent = c.name;
    el.querySelector('.cname small').textContent = tag;
    el.querySelector('.clast').textContent = sub;
    if (c.unlocked) clickable(el, () => { if (!busy) { current = c.number; setFile(null); renderContacts(); renderChat(); warmUp(current); } });
    box.appendChild(el);
  }
}

// Everything goes in as text, never as HTML: a bot tricked into writing
// <img onerror=...> must show up as text, not run.
function bubble(cls, text, time, ticks) {
  const b = document.createElement('div');
  b.className = 'bubble ' + cls;
  b.textContent = text;
  if (time) {
    const m = document.createElement('span'); m.className = 'meta'; m.textContent = time;
    if (ticks) { const k = document.createElement('span'); k.className = 'ticks'; k.textContent = '✓✓'; m.append(' ', k); }
    b.appendChild(m);
  }
  return b;
}

function typingEl() {
  const t = document.createElement('div'); t.className = 'typing'; t.id = 'typing';
  t.innerHTML = '<i></i><i></i><i></i><span class="secs" id="secs"></span>'; return t;
}

function renderChat() {
  const c = game.contacts.find(x => x.number === current);
  $('headAvatar').textContent = c.avatar; $('headName').textContent = c.name;
  $('headDefense').textContent = `· Level ${c.number} (${c.level_name}) — 🛡️ ${c.defense}`;
  $('attach').classList.toggle('show', !!c.accepts_files);
  if (!c.accepts_files) setFile(null);
  const m = $('msgs'); m.innerHTML = '<div class="day">TODAY</div>';
  m.appendChild(bubble('bot', c.greeting));
  for (const e of c.log) {
    m.appendChild(bubble(e.from, e.text, e.from === 'sys' ? '' : e.time, e.from === 'me'));
  }
  m.appendChild(typingEl());
  // A reply started before a page reload is still being written: show it
  // and check again in a few seconds.
  const waiting = c.pending && !busy;
  $('typing').style.display = waiting ? 'flex' : 'none';
  $('send').disabled = waiting;
  // Files-only contacts read the attachment and nothing else.
  $('inp').disabled = waiting || !!c.files_only;
  if (c.files_only) $('inp').value = '';
  $('inp').placeholder = c.files_only ? 'This contact only reads files: use the 📎' : 'Type a message';
  clearTimeout(pollTimer);
  if (waiting) pollTimer = setTimeout(refresh, 3000);
  m.scrollTop = m.scrollHeight;
}

// ---------- chat ----------
// Ask the server to have the model read this bot's instructions now, while
// the player is still typing: on a slow CPU that reading is most of the wait.
function warmUp(level) { api('/api/warm', { level }); }

async function send() {
  const text = $('inp').value.trim();
  const file = pendingFile;
  if ((!text && !file) || busy) return;
  // The page opened while the server was down: load the game first, keep the text.
  if (!game || !$('typing')) { refresh(); return; }
  setBusy(true); $('inp').value = ''; $('inp').disabled = true; $('send').disabled = true;
  setFile(null);
  let failed = false;
  const m = $('msgs'), typing = $('typing');
  const shown = file ? '📎 ' + file.name + (text ? '\n' + text : '') : text;
  m.insertBefore(bubble('me', shown, now(), true), typing);
  typing.style.display = 'flex'; m.scrollTop = m.scrollHeight;
  // Small CPUs take a while: show how long the bot has been "typing".
  const started = Date.now();
  const timer = setInterval(() => { $('secs').textContent = Math.round((Date.now() - started) / 1000) + 's'; }, 1000);

  try {
    const { ok, data } = await api('/api/chat', { level: current, message: text, file });
    if (ok) {
      m.insertBefore(bubble(data.blocked ? 'sys' : 'bot', data.answer, data.blocked ? '' : now()), typing);
    } else {
      failed = '⚠️ ' + (data.error || 'Server error.');
      m.insertBefore(bubble('sys', failed), typing);
    }
  } finally {
    clearInterval(timer); $('secs').textContent = ''; typing.style.display = 'none';
    m.scrollTop = m.scrollHeight;
    setBusy(false);
  }
  // Re-render from the server: times, input state (files-only), contacts.
  const fresh = await refresh();
  const c = game && game.contacts.find(x => x.number === current);
  if (!fresh) {  // server gone: nothing was re-rendered, so unlock the composer here
    $('inp').disabled = !!(c && c.files_only); $('send').disabled = false;
  }
  if (failed) {  // give the player their attack back instead of making them redo it
    // Refusals like "too long" never reach the server log, so the re-render
    // above dropped them: show the reason again unless the log already has it.
    // Without a re-render, the bubble from above is still on screen.
    const last = c && c.log.length ? c.log[c.log.length - 1] : null;
    if (fresh && !busy && !(last && last.from === 'sys' && last.text === failed)) {
      const t = $('typing'); t.parentNode.insertBefore(bubble('sys', failed), t);
      t.parentNode.scrollTop = t.parentNode.scrollHeight;
    }
    // Even while the input waits for another tab's reply: it is re-enabled later.
    if (!(c && c.files_only)) $('inp').value = text;
    if (file) setFile(file);
  }
  if (!$('inp').disabled) $('inp').focus();
}

// While a reply is on its way, other chat actions wait; show it instead of
// silently ignoring clicks.
function setBusy(on) {
  busy = on;
  document.body.classList.toggle('busy', on);
}
$('send').onclick = send;
$('inp').onkeydown = e => { if (e.key === 'Enter' && !e.isComposing) send(); };
$('clear').onclick = async () => {
  if (busy) return;
  const { ok, data } = await api('/api/reset', { level: current });
  if (!ok) { alert(data.error || 'Server error. The chat was not cleared.'); return; }
  refresh();
};

// ---------- portal ----------
async function doLogin() {
  if (!$('user').value.trim() || !$('pass').value) { $('err').textContent = 'Fill in username and password.'; return; }
  const { ok, data } = await api('/api/login', { username: $('user').value, password: $('pass').value });
  if (!ok) {
    // Wrong logins always come with a message; no message means the server failed.
    $('err').textContent = data.error || 'Server error. Try again.';
    const l = $('login'); l.classList.remove('shake'); void l.offsetWidth; l.classList.add('shake');
    return;
  }
  const d = data.dashboard;
  $('granted').textContent = data.final ? 'SYSTEM COMPROMISED' : 'ACCESS GRANTED';
  $('granted').classList.toggle('final', !!data.final);
  $('dTitle').textContent = d.title; $('dRole').textContent = data.username;
  $('dCards').innerHTML = '';
  for (const c of d.cards) {
    const el = document.createElement('div'); el.className = 'card' + (c.locked ? ' lock' : '');
    el.innerHTML = '<small></small><b></b>';
    el.querySelector('small').textContent = (c.locked ? '🔒 ' : '') + c.label;
    el.querySelector('b').textContent = c.value;
    $('dCards').appendChild(el);
  }
  $('dClue').textContent = d.clue;
  renderDebrief(data.level, data.debrief);
  renderReport(data.report);
  $('login').style.display = 'none'; $('dash').style.display = 'block'; $('err').textContent = '';
  // Open the contact this login just unlocked, ready for when the player goes back.
  const next = game && game.contacts.find(c => c.number === data.level + 1);
  if (next && !next.unlocked && !busy) { current = next.number; setFile(null); warmUp(current); }
  refresh();
}
// Small helper: an element with text (never HTML) and optional children.
function el(tag, text, cls) {
  const e = document.createElement(tag);
  if (text !== undefined) e.textContent = text;
  if (cls) e.className = cls;
  return e;
}

// What the player just exploited, and how a real company would defend it.
function renderDebrief(level, db) {
  const box = $('debrief');
  box.hidden = !db || !db.flaw;
  if (box.hidden) return;
  const p = (label, text) => { const x = el('p'); x.append(el('b', label + ' '), text); return x; };
  box.replaceChildren(
    el('h4', `🧠 Level ${level} debrief`), el('span', db.owasp, 'tag'),
    p('What you exploited:', db.flaw), p('How to really defend against it:', db.fix));
}

function duration(s) {
  if (s === null || s === undefined) return '—';
  const h = Math.floor(s / 3600), m = Math.floor(s % 3600 / 60);
  return h ? `${h}h ${m}min` : m ? `${m}min ${s % 60}s` : `${s}s`;
}

// The end screen: a pentest-style summary of the whole run.
function renderReport(r) {
  const box = $('report');
  box.hidden = !r;
  if (!r) return;
  const totals = el('div', undefined, 'rtotals');
  for (const [v, label] of [[duration(r.seconds), 'total time'], [r.sent, 'messages'], [r.blocked, 'caught by a filter']]) {
    const d = el('div'); d.append(el('b', String(v)), el('small', label)); totals.append(d);
  }
  const table = el('table');
  const head = el('tr'); head.append(el('th', 'Target'), el('th', 'Attempts', 'n'), el('th', 'Blocked', 'n'));
  table.append(head);
  for (const x of r.levels) {
    const tr = el('tr');
    tr.append(el('td', `${x.avatar} ${x.number}. ${x.name}`), el('td', String(x.sent), 'n'), el('td', String(x.blocked), 'n'));
    table.append(tr);
  }
  const badges = el('div', undefined, 'badges');
  r.badges.forEach((b, i) => {
    const d = el('div', undefined, 'badge');
    d.style.animationDelay = (0.3 + i * 0.25) + 's';
    d.append(el('b', `${b.icon} ${b.name}`), el('small', b.text));
    badges.append(d);
  });
  box.replaceChildren(el('h4', '> BREACH REPORT'), totals, table, el('h4', '> ACHIEVEMENTS'), badges);
}

function logout() {
  $('dash').style.display = 'none'; $('login').style.display = 'block';
  $('user').value = ''; $('pass').value = ''; $('err').textContent = '';
}
$('enter').onclick = doLogin;
$('pass').onkeydown = $('user').onkeydown = e => { if (e.key === 'Enter' && !e.isComposing) doLogin(); };
$('logout').onclick = logout;

// ---------- intro ----------
// The story, typed like a terminal. Shown once per browser (and again after
// "Restart"); any key or click skips it.
const INTRO = [
  ['', '$ ssh guest@acme-store.internal'],
  ['', 'connecting...', 500],
  ['ok', '[OK]  Acme Chat: guest access granted'],
  ['bad', '[X]   Acme Portal: access DENIED'],
  ['', ''],
  ['hi', 'MISSION: sign in to the portal as admin.'],
  ['', ''],
  ['', 'Every team at Acme has an AI bot that knows the team login.'],
  ['', 'Make the bot leak it, sign in, and follow the clues to the CEO.'],
  ['', ''],
  ['', 'House rule: talking only. No reading the code. ;)'],
];
const store = {
  get(k) { try { return localStorage.getItem(k); } catch { return null; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch {} },
  del(k) { try { localStorage.removeItem(k); } catch {} },
};
let introDone = null;

function showIntro() {
  const box = $('intro'), pre = $('introText');
  box.hidden = false; pre.replaceChildren(); $('introGo').classList.remove('show');
  const reduce = matchMedia('(prefers-reduced-motion: reduce)').matches;
  let line = 0, ch = 0, timer = null, span = null;
  const cursor = el('span', undefined, 'cursor');
  const finish = () => {
    clearTimeout(timer); pre.replaceChildren();
    for (const [cls, text] of INTRO) { pre.append(el('span', text, cls), '\n'); }
    pre.append(cursor); $('introGo').classList.add('show'); $('introGo').focus();
    introDone = close;
  };
  const close = () => {
    clearTimeout(timer); box.hidden = true; introDone = null; store.set('acme-intro', '1');
    if (!$('inp').disabled) $('inp').focus();
  };
  const step = () => {
    if (line >= INTRO.length) return finish();
    const [cls, text, pause] = INTRO[line];
    if (ch === 0) { span = el('span', '', cls); pre.insertBefore(span, cursor); }
    if (ch < text.length) { span.textContent += text[ch++]; timer = setTimeout(step, 22); return; }
    pre.insertBefore(document.createTextNode('\n'), cursor);
    line++; ch = 0; timer = setTimeout(step, pause || (text ? 260 : 120));
  };
  pre.append(cursor);
  introDone = finish;  // first key/click skips the typing, the second one closes
  if (reduce) finish(); else step();
}
$('introGo').onclick = () => introDone && introDone();
$('intro').onclick = e => { if (e.target !== $('introGo') && introDone) introDone(); };
addEventListener('keydown', e => {
  if ($('intro').hidden || !introDone) return;
  if (e.key === 'Enter' || e.key === ' ' || e.key === 'Escape') { e.preventDefault(); introDone(); }
});

// Another tab may have sent a message or logged in meanwhile: catch up when
// the player comes back to this one.
document.addEventListener('visibilitychange', () => { if (!document.hidden && !busy) refresh(); });

if (!store.get('acme-intro')) showIntro();
refresh();
