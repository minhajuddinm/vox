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
