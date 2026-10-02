// Pure helpers shared by both pages. No bridge calls here: the bridges differ (async pywebview vs string + callback).
const STYLES = [["neutral", "Neutral"], ["formal", "Formal"], ["casual", "Casual"], ["very_casual", "Very casual"], ["raw", "Raw (no cleanup)"]];
const ABOUT_MAX = 8000;
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
function toast(msg) { const t = $("toast"); t.textContent = msg; t.classList.add("on"); clearTimeout(t._h); t._h = setTimeout(() => t.classList.remove("on"), 1600); }
// Dictionary lines: "word" is a term, "wrong => right" is a replacement.
function dictRepls(lines) { return (lines || []).filter(l => l.includes("=>")).map(l => l.split("=>").map(s => s.trim())); }
function dictLines(terms, repl) { return [...terms, ...repl.map(([w, r]) => `${w} => ${r}`)]; }
// Snippets ({trigger: text}): the same caps as snippets.py and Snippets.java, which clean the setting again when they use it.
const SNIP_MAX = 50, SNIP_TEXT_MAX = 2000, SNIP_TRIGGER_MAX = 100, SNIP_TOTAL_MAX = 20000;
// Bytes a text takes in the relay's profile (JSON with ASCII escapes: 6 for a letter outside ASCII, 12 for an emoji), as
// snippets.wire_size and Snippets.wireSize count them for SNIP_TOTAL_MAX.
function snipWireSize(s) {
  let n = 0;
  for (let i = 0; i < s.length; i++) { const c = s.charCodeAt(i); n += '"\\\n\r\t\b\f'.includes(s[i]) ? 2 : (c >= 32 && c <= 126 ? 1 : 6); }
  return n;
}
// {ok, map, msg}: the snippets with this one added (a trigger already there, case ignored, gets the new text, at the end).
function snippetsAdd(map, trigger, text) {
  const t = String(trigger ?? "").replace(/[ \t\r\n]+/g, " ").trim(), x = String(text ?? "").replace(/\r\n?/g, "\n");
  if (!t || !x.trim()) return { ok: false, map, msg: "Type the phrase and the text it stands for" };
  if (t.length > SNIP_TRIGGER_MAX) return { ok: false, map, msg: `The phrase can be up to ${SNIP_TRIGGER_MAX} characters` };
  if (!/[\p{L}\p{N}\p{M}]/u.test(t)) return { ok: false, map, msg: "The phrase needs at least one letter or digit" };
  const out = {};
  for (const [k, v] of Object.entries(map || {})) if (k.toLowerCase() !== t.toLowerCase()) out[k] = v;
  if (Object.keys(out).length >= SNIP_MAX) return { ok: false, map, msg: `Up to ${SNIP_MAX} snippets` };
  const cut = [...x].length > SNIP_TEXT_MAX;
  out[t] = cut ? [...x].slice(0, SNIP_TEXT_MAX).join("") : x;
  let total = 0;
  for (const [k, v] of Object.entries(out)) total += snipWireSize(k) + snipWireSize(v);
  if (total > SNIP_TOTAL_MAX) return { ok: false, map, msg: "Not added: too much snippet text to sync (letters outside A-Z count six times). Remove or shorten a snippet first" };
  return { ok: true, map: out, msg: cut ? `Added, cut to ${SNIP_TEXT_MAX.toLocaleString("en-US")} characters` : "Added" };
}
function snippetsRemove(map, trigger) { const out = {}; for (const [k, v] of Object.entries(map || {})) if (k !== trigger) out[k] = v; return out; }
// One line of a saved text for the list: line breaks shown as " / ", cut at 80 characters.
function snippetPreview(text) { const s = String(text ?? "").replace(/\n+/g, " / "); return s.length > 80 ? s.slice(0, 79) + "…" : s; }
// "Lists and paragraphs": off, auto or lists; anything else is auto (structure.py structure_mode, Structure.mode).
function structureMode(v) { const s = String(v ?? "").trim().toLowerCase(); return ["off", "auto", "lists"].includes(s) ? s : "auto"; }
function aboutCount() {
  const n = $("about").value.length, s = $("about-status");
  s.className = "status" + (n > ABOUT_MAX ? " bad" : "");
  s.textContent = n ? `${n.toLocaleString()} of ${ABOUT_MAX.toLocaleString()} characters` + (n > ABOUT_MAX ? ". The rest is ignored." : "") : "";
}
function agoText(t) { if (!t) return "never"; const s = Math.round(Date.now() / 1000 - t); return s < 10 ? "just now" : s < 90 ? s + " s ago" : s < 5400 ? Math.round(s / 60) + " min ago" : s < 129600 ? Math.round(s / 3600) + " h ago" : Math.round(s / 86400) + " days ago"; }
// One line for both Test results of the connection test: {ok, message}. A failing role is named first.
function combineTests(stt, llm) {
  if (stt.ok && llm.ok) return { ok: true, message: "Voice and cleanup both answered" };
  return { ok: false, message: [stt.ok ? "" : "Voice: " + stt.message, llm.ok ? "" : "Cleanup: " + llm.message].filter(Boolean).join(" ") };
}
// Rows of the Home status card, from local data only: st = state.status, sync = the sync status or null (not asked yet),
// test = the last connection Test of this session ({ok, message}) or null. Each row is [label, text, kind].
function statusRows(st, cfg, sync, test) {
  const [syncText, syncKind] = !cfg.relay_sync ? ["Off", "dim"] : !sync ? ["Checking…", "dim"] : sync.running ? ["Syncing…", ""]
    : sync.error ? ["Not synced: " + sync.error, "bad"] : ["Synced " + agoText(sync.last_ok), "ok"];
  const l = st.last, app = l && l.app ? " in " + String(l.app).replace(/\.exe$/i, "") : "";
  const [lastText, lastKind] = st.unsent ? ["Not sent. Retry from the notification.", "bad"]
    : l ? [`${l.words} word${l.words === 1 ? "" : "s"}, ${agoText(l.t)}${app}`, ""] : [cfg.keep_history === false ? "History is off" : "None yet", "dim"];
  return [
    ["AI provider", st.provider, ""],
    ["Voice model", st.stt_model, ""],
    ["Cleanup model", st.llm_model, ""],
    ["Connection", test ? (test.ok ? "Working. " : "Failed. ") + test.message : "Not tested yet", test ? (test.ok ? "ok" : "bad") : "dim"],
    ["Sync", syncText, syncKind],
    ["Voice notes", st.notes === 1 ? "1 note" : st.notes + " notes", ""],
    ["Last dictation", lastText, lastKind],
  ];
}
const statusHtml = (rows) => rows.map(([a, b, k]) => `<div class="srow"><span class="sl">${esc(a)}</span><span class="sv ${k}">${esc(b)}</span></div>`).join("");
// Speed card (Home). Milliseconds as text, the same rule as timing.format_ms / Timing.formatMs: one decimal from 1000 ms, halves up.
function fmtMs(ms) { ms = Math.max(0, Math.round(+ms || 0)); if (ms < 1000) return ms + " ms"; const t = Math.floor((ms + 50) / 100); return Math.floor(t / 10) + "." + (t % 10) + " s"; }
// v = what the bridge gives (timing.speed_view / Timing.speedView): {count, biggest, stages: {stage: {median, p90}}, models: [...], last: [...]}.
// Only the stages the app can work on are listed (the time spent speaking is not); the biggest one is marked. appLabel turns a stored app into text.
const SPEED_STAGES = [["start", "Waiting for the microphone"], ["stt", "Speech to text"], ["llm", "Cleanup"], ["insert", "Typing it in"], ["total", "Total, after you stop"]];
function speedHtml(v, appLabel) {
  appLabel = appLabel || ((a) => String(a || "").replace(/\.exe$/i, ""));
  if (!v || !v.count) return `<div class="srow"><span class="sl">No timed dictations yet. Dictate something and the numbers appear here.</span></div>`;
  const ms = (x) => x ? fmtMs(x) : "–";
  let html = `<div class="srow"><span class="sl">Median of your last ${v.count} dictation${v.count === 1 ? "" : "s"}</span><span class="sv dim">slowest 1 in 10</span></div>`;
  for (const [k, label] of SPEED_STAGES) {
    const s = (v.stages || {})[k] || { median: 0, p90: 0 };
    html += `<div class="srow${k === v.biggest ? " big" : ""}"><span class="sl">${esc(label)}${k === v.biggest ? " (the biggest)" : ""}</span><span class="sv">${ms(s.median)} <small>${ms(s.p90)}</small></span></div>`;
  }
  if ((v.models || []).length) {
    html += `<div class="sub-h">By model</div>`;
    for (const m of v.models) {
      html += `<div class="srow"><span class="sl">${esc(m.stt_model || "unknown")} + ${esc(m.llm_model || "unknown")}</span><span class="sv">${ms(m.total)} <small>speech ${ms(m.stt)}, cleanup ${ms(m.llm)}, ${m.count} dictation${m.count === 1 ? "" : "s"}</small></span></div>`;
    }
  }
  if ((v.last || []).length) {
    html += `<div class="sub-h">Last ${v.last.length}</div>`;
    for (const d of v.last) {
      const st = d.stages || {}, app = d.app ? " in " + appLabel(d.app) : "";
      html += `<div class="srow"><span class="sl">${esc(agoText(d.t))}${esc(app)}</span><span class="sv">${ms(st.total)} <small>speech ${ms(st.stt)}, cleanup ${ms(st.llm)}</small></span></div>`;
    }
  }
  return html;
}
// The Devices card's list, from the bridge's answer {ok, error, devices: [{name, this, state, ago}]} (windows/sync.py
// devices_for_ui; Android: MainActivity.getDevices). A failure shows its reason; nothing but those four fields is drawn.
function devicesHtml(res) {
  if (!res || !res.ok) return `<div class="status bad">${esc((res && res.error) || "The device list could not be read.")}</div>`;
  const rows = res.devices || [];
  if (!rows.length) return '<div class="dempty">No device has synced yet</div>';
  return rows.map(d => `<div class="drow ${d.state === "active" || d.state === "recent" ? d.state : "old"}"><span class="dot"></span><span class="dn">${esc(d.name)}${d.this ? '<span class="badge">this device</span>' : ""}</span><span class="da">${esc(d.ago)}</span></div>`).join("");
}
// Rows under the relay's Test connection button, from the bridge's answer {ok, reachable, token_ok, device_name,
// relay_version, notes, message} (windows/sync.py relay_check; Android: RelayClient.check through MainActivity.syncTest).
// Each row is [label, text, kind], drawn with statusHtml. An answer without the fields (an old one) adds no rows; the
// answer's message is shown by the page as before.
function relayCheckRows(r) {
  if (!r || typeof r.reachable !== "boolean") return [];
  const rows = [["Relay", r.reachable ? "Reachable" + (r.relay_version ? ", version " + r.relay_version : "") : "Not reachable", r.reachable ? "ok" : "bad"],
    ["Token", r.token_ok ? "Accepted" : r.reachable ? "Refused" : "Not checked", r.token_ok ? "ok" : r.reachable ? "bad" : "dim"]];
  if (r.ok && r.device_name) rows.push(["This device", r.device_name, ""]);
  return rows;
}
// Learn from my corrections (setting auto_learn). One text for the switch on both pages.
const AUTO_LEARN_TEXT = "On by default. For up to 3 minutes after Vox types, or until you send it, Vox reads the text of the focused field in that app (up to 20,000 characters; it can be another field there) to notice when you fix a word, and adds the fix to your dictionary. Only the changed words are kept; numbers and swaps of everyday words are not learned. Your dictionary goes to your speech and cleanup servers as spelling hints and, with sync on, to your relay. Password fields are never read.";
// The "Recently learned" list of the Dictionary page from learned_log ({t, wrong, right, word}, oldest first): newest first,
// at most 20. Each Remove button carries data-unlearn = the entry's t; the page asks its bridge to remove that entry and the
// dictionary lines it added.
function learnedHtml(log) {
  const rows = (Array.isArray(log) ? log : []).filter(e => e && typeof e.wrong === "string" && typeof e.right === "string").slice(-20).reverse();
  if (!rows.length) return '<div class="dempty">Nothing learned yet. Fix a word in text Vox just typed and the fix shows up here.</div>';
  return rows.map(e => `<div class="lrow"><span class="lw">${esc(e.wrong)}<i>→</i><b>${esc(e.right)}</b></span><span class="la">${esc(agoText(e.t))}</span><button class="btn ghost" data-unlearn="${esc(String(e.t))}">Remove</button></div>`).join("");
}
