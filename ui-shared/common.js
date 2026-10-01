// Pure helpers shared by both pages. No bridge calls here: the bridges differ (async pywebview vs string + callback).
const STYLES = [["neutral", "Neutral"], ["formal", "Formal"], ["casual", "Casual"], ["very_casual", "Very casual"], ["raw", "Raw (no cleanup)"]];
const ABOUT_MAX = 8000;
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
function toast(msg) { const t = $("toast"); t.textContent = msg; t.classList.add("on"); clearTimeout(t._h); t._h = setTimeout(() => t.classList.remove("on"), 1600); }
// Dictionary lines: "word" is a term, "wrong => right" is a replacement.
function dictRepls(lines) { return (lines || []).filter(l => l.includes("=>")).map(l => l.split("=>").map(s => s.trim())); }
function dictLines(terms, repl) { return [...terms, ...repl.map(([w, r]) => `${w} => ${r}`)]; }
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
