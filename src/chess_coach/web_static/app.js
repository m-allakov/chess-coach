/* The Coach's Notebook. Plain JavaScript, no build step.
   All text goes in through textContent / text nodes, never as HTML, so game data cannot
   inject markup. */
(function () {
  "use strict";

  const state = { username: "", timeClass: "", tab: "overview", cache: {} };
  const view = document.getElementById("view");
  const statusEl = document.getElementById("status");

  // ---- DOM helpers ------------------------------------------------------------------
  function h(tag, attrs, ...children) {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v === false || v == null) continue;
      if (k === "class") el.className = v;
      else if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2), v);
      else el.setAttribute(k, v === true ? "" : String(v));
    }
    for (const child of children.flat(Infinity)) {
      if (child == null || child === false) continue;
      el.append(child.nodeType ? child : document.createTextNode(String(child)));
    }
    return el;
  }
  const SVG_NS = "http://www.w3.org/2000/svg";
  function s(tag, attrs, ...children) {
    const el = document.createElementNS(SVG_NS, tag);
    for (const [k, v] of Object.entries(attrs || {})) if (v != null && v !== false) el.setAttribute(k, String(v));
    for (const child of children.flat(Infinity)) {
      if (child == null || child === false) continue;
      el.append(child.nodeType ? child : document.createTextNode(String(child)));
    }
    return el;
  }
  const fmt = (v, suffix) => (v == null ? "–" : `${v}${suffix || ""}`);
  const pct = (v) => fmt(v, "%");
  const num = (v) => h("span", { class: "mono" }, v);

  // ---- data -------------------------------------------------------------------------
  function query(extra) {
    const q = new URLSearchParams({ username: state.username });
    if (state.timeClass) q.set("time_class", state.timeClass);
    for (const [k, v] of Object.entries(extra || {})) q.set(k, v);
    return q.toString();
  }
  async function api(path, opts) {
    const o = opts || {};
    const key = `${path}?${query(o.query)}`;
    if (!o.fresh && state.cache[key]) return state.cache[key];
    const res = await fetch(`/api/${path}?${query(o.query)}`);
    const body = await res.json();
    if (!res.ok) throw new Error(body.error || `Request failed (${res.status})`);
    state.cache[key] = body;
    return body;
  }

  // ---- shared vocabulary ------------------------------------------------------------
  const CAUSE = {
    thrown_win: { name: "Winning position not converted", color: "var(--oxblood)" },
    blunder: { name: "One costly mistake", color: "var(--ink)" },
    opening: { name: "Worse after the opening", color: "var(--brass)" },
    time: { name: "Lost on the clock", color: "var(--slate)" },
    gradual: { name: "Slow decline", color: "var(--stone)" },
  };
  const HEADLINE = {
    thrown_win: ["Finish the games you are already ", "winning", "."],
    blunder: ["Check the ", "one move", " that decides the game."],
    opening: ["Leave the opening in ", "better shape", "."],
    time: ["Stop losing good games on the ", "clock", "."],
    gradual: ["Build the position more ", "patiently", "."],
  };
  const METRIC = {
    score_pct: { name: "Score", note: "share of points won", suffix: "%" },
    blunders_per_100_moves: { name: "Blunders", note: "per 100 moves", suffix: "" },
    lost_on_time_pct_of_games: { name: "Lost on time", note: "share of games", suffix: "%" },
    winning_positions_not_won_pct: { name: "Winning positions not won", note: "share of those reached", suffix: "%" },
  };
  const ARROW = { played: "#c0392b", best: "#3a8f66", answer: "#3b6fa5" };

  function empty(text) { return h("p", { class: "empty" }, text || "Nothing to show yet."); }
  function section(title, ...body) {
    return h("section", { class: "section" }, title ? h("h3", null, title) : null, body);
  }

  function figures(items) {
    return h("div", { class: "figures" }, items.map(([label, value]) => h("div", null, h("b", null, value), h("span", null, label))));
  }

  function table(headers, rows) {
    return h("div", { class: "table-wrap" }, h("table", null,
      h("thead", null, h("tr", null, headers.map((t, i) => h("th", { class: i ? "num" : "" }, t)))),
      h("tbody", null, rows.map((r) => h("tr", null, r.map((c, i) => h("td", { class: i ? "num" : "" }, c)))))));
  }

  function tag(verdict) {
    const kind = verdict === "better" ? "better" : verdict === "worse" ? "worse" : "flat";
    const label = verdict === "no clear change" || verdict === "no clear difference" ? "no clear difference" : verdict;
    return h("span", { class: `tag ${kind}` }, label);
  }

  function bars(rows, max) {
    const top = max || Math.max(1, ...rows.map((r) => r.value || 0));
    return h("div", { class: "bars" }, rows.map((r) => h("div", { class: "bar" },
      h("div", { class: "name" }, r.label),
      h("div", { class: "track" }, h("div", { class: "fill", style: `width:${Math.max(0, Math.min(100, (100 * (r.value || 0)) / top))}%` })),
      h("div", { class: "num" }, r.text != null ? r.text : fmt(r.value)))));
  }


  // ---- peers: you against players of other ratings ------------------------------------
  const bandLabel = (band) => {
    const [lo, hi] = band.split("-").map(Number);
    const mid = (lo + hi) / 2;
    return mid >= 1000 ? `${(mid / 1000).toFixed(1).replace(".0", "")}k` : String(mid);
  };

  function peerRow(m, near) {
    const bands = Object.entries(m.bands).filter(([, b]) => b.median != null);
    const all = [m.you, ...bands.flatMap(([, b]) => [b.median, b.p25, b.p75]).filter((v) => v != null)];
    let lo = Math.min(...all), hi = Math.max(...all);
    const pad = (hi - lo) * 0.12 || 1;
    lo -= pad; hi += pad;
    const W = 260, H = 46, y0 = 24, x = (v) => 6 + ((v - lo) / (hi - lo)) * (W - 12);
    const pos = m.against_closest_band || "unknown";
    const worse = pos === (m.lower_is_better ? "above" : "below");
    const better = pos === (m.lower_is_better ? "below" : "above");
    const tone = pos === "unknown" ? "var(--faint)" : worse ? "var(--oxblood)" : better ? "var(--pine)" : "var(--ink)";
    const nearStats = near ? m.bands[near] : null;
    // Band labels sit under their ticks; when ticks are close, push labels apart so they stay legible.
    const labelSlots = bands.map(([band, b]) => ({ band, text: bandLabel(band), x: x(b.median) })).sort((p, q) => p.x - q.x);
    for (let i = 1; i < labelSlots.length; i++) labelSlots[i].x = Math.max(labelSlots[i].x, labelSlots[i - 1].x + 22);
    const overshoot = Math.max(0, labelSlots.length ? labelSlots[labelSlots.length - 1].x - (W - 8) : 0);
    labelSlots.forEach((l) => { l.x -= overshoot; });
    const svg = s("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": `${m.label}: you ${m.you}` },
      s("line", { x1: 6, x2: W - 6, y1: y0, y2: y0, style: "stroke:var(--rule)" }),
      nearStats && nearStats.p25 != null && pos !== "unknown"
        ? s("rect", { x: x(nearStats.p25), y: y0 - 9, width: Math.max(2, x(nearStats.p75) - x(nearStats.p25)), height: 18, style: "fill:var(--paper-2);stroke:var(--rule)" }) : null,
      bands.map(([band, b]) => s("line", { x1: x(b.median), x2: x(b.median), y1: y0 - 7, y2: y0 + 7,
        style: `stroke:${band === near ? "var(--ink)" : "var(--stone)"}`, "stroke-width": band === near ? 2 : 1.2 })),
      labelSlots.map((l) => s("text", { x: l.x, y: H - 2, "text-anchor": "middle", "font-size": 9, "font-family": "Consolas,monospace",
        style: `fill:${l.band === near ? "var(--ink)" : "var(--faint)"}` }, l.text)),
      s("circle", { cx: x(m.you), cy: y0, r: 6.5, style: `fill:${tone};stroke:var(--paper)`, "stroke-width": 1.6 }),
      s("text", { x: x(m.you), y: 9, "text-anchor": "middle", "font-size": 10.5, "font-weight": 700, "font-family": "Consolas,monospace", style: `fill:${tone}` }, m.you));
    const nearLabel = near ? bandLabel(near) : "";
    const words = pos === "unknown" ? "not enough peers to judge"
      : pos === "within" ? `typical for ~${nearLabel} players`
        : pos === "above" ? `${m.lower_is_better ? "higher" : "better"} than most ~${nearLabel} players`
          : `${m.lower_is_better ? "lower" : "below"} than most ~${nearLabel} players`;
    return h("div", { class: "dumbbell" },
      h("div", { class: "name" }, m.label),
      svg,
      h("div", { class: `verdict ${worse ? "worse" : better ? "better" : "flat"}` }, words));
  }

  function peerSection(p) {
    if (!p || !p.available) {
      return section("Beside players of your level", h("p", { class: "note" }, (p && p.note) || "No comparison available."));
    }
    const players = Object.entries(p.metrics[0] ? p.metrics[0].bands : {}).map(([b, d]) => `${bandLabel(b)}: ${d.players}`).join(" \u00b7 ");
    const tentative = p.tentative_bands.length ? ` Bands built from fewer than 8 players (${p.tentative_bands.map(bandLabel).join(", ")}) are tentative.` : "";
    return section("Beside players of your level",
      h("p", { class: "lede", style: "font-size:18px" }, `Your ${p.your_games} blitz games at 3+0, rated about ${p.your_rating}. Ticks mark the typical player at each rating band; the shaded box is the middle half of your band (${bandLabel(p.closest_band)}); the dot is you.`),
      h("div", null, p.metrics.map((m) => peerRow(m, p.closest_band))),
      h("p", { class: "note", style: "margin-top:12px" }, `Players per band \u2013 ${players}.${tentative} ${p.note}`));
  }

  // ---- chapter I: overview ----------------------------------------------------------
  function ribbon(plan) {
    const rows = Object.entries(plan.why_games_were_not_won)
      .map(([k, v]) => ({ key: k, ...v })).filter((r) => r.games > 0).sort((a, b) => b.games - a.games);
    return h("div", null,
      h("div", { class: "ribbon", role: "img", "aria-label": "Why recent games were not won" },
        rows.map((r) => h("div", { style: `flex:${r.games} 1 0;background:${CAUSE[r.key].color}`, title: `${CAUSE[r.key].name}: ${r.games}` }))),
      h("div", { class: "ribbon-key" }, rows.map((r) => h("div", { style: `--c:${CAUSE[r.key].color}` },
        h("b", null, `${Math.round(100 * r.share_of_non_wins)}%`),
        h("span", null, CAUSE[r.key].name),
        h("small", null, `${r.games} game${r.games === 1 ? "" : "s"}`)))));
  }

  function dumbbell(key, t) {
    const meta = METRIC[key] || { name: key, note: "", suffix: "" };
    const margin = t.noise_margin == null ? 0 : t.noise_margin;
    const vals = [t.before, t.recent, t.before - margin, t.before + margin];
    let lo = Math.min(...vals);
    let hi = Math.max(...vals);
    const pad = (hi - lo) * 0.18 || 1;
    lo -= pad; hi += pad;
    const W = 240, H = 40, y0 = 22, x = (v) => 8 + ((v - lo) / (hi - lo)) * (W - 16);
    const tone = t.verdict === "better" ? "var(--pine)" : t.verdict === "worse" ? "var(--oxblood)" : "var(--ink-2)";
    const svg = s("svg", { viewBox: `0 0 ${W} ${H}`, role: "img",
      "aria-label": `${meta.name}: ${t.before} earlier, ${t.recent} recently` },
      s("line", { x1: 8, x2: W - 8, y1: y0, y2: y0, style: "stroke:var(--rule)", "stroke-width": 1 }),
      s("rect", { x: x(t.before - margin), y: y0 - 10, width: Math.max(2, x(t.before + margin) - x(t.before - margin)), height: 20, style: "fill:var(--paper-2);stroke:var(--rule)" }),
      s("line", { x1: x(t.before), x2: x(t.recent), y1: y0, y2: y0, style: `stroke:${tone}`, "stroke-width": 2.4 }),
      s("circle", { cx: x(t.before), cy: y0, r: 5, style: "fill:var(--paper);stroke:var(--ink-2)", "stroke-width": 1.8 }),
      s("circle", { cx: x(t.recent), cy: y0, r: 6.5, style: `fill:${tone}` }),
      s("text", { x: x(t.before), y: H - 1, "text-anchor": "middle", "font-size": 10.5, style: "fill:var(--faint)", "font-family": "Consolas,monospace" }, t.before),
      s("text", { x: x(t.recent), y: 9, "text-anchor": "middle", "font-size": 10.5, style: `fill:${tone}`, "font-family": "Consolas,monospace", "font-weight": 700 }, t.recent));
    const sign = t.change > 0 ? "+" : "";
    return h("div", { class: "dumbbell" },
      h("div", { class: "name" }, meta.name, h("small", null, meta.note)),
      svg,
      h("div", { class: `verdict ${t.verdict === "better" ? "better" : t.verdict === "worse" ? "worse" : "flat"}` },
        t.verdict === "no clear change" ? "no clear change" : t.verdict,
        h("small", null, `${sign}${t.change}  (noise ±${fmt(t.noise_margin)})`)));
  }

  async function overview() {
    const [plan, status, peerData] = await Promise.all([api("coach"), api("status"), api("peers").catch(() => null)]);
    if (!plan.games_analysed) return empty("No analysed games yet. Sync and analyse some games first.");
    const focus = plan.weekly_focus;
    const [a, b, c] = HEADLINE[focus.area] || ["", focus.area, ""];
    const cause = plan.why_games_were_not_won[focus.area];
    const win = plan.recent_window;
    const trend = Object.entries(plan.trend_vs_earlier_games || {});
    return h("div", null,
      h("div", { class: "hero" },
        h("div", null,
          h("div", { class: "label" }, "The one thing for this week"),
          h("h2", null, a, h("em", null, b), c),
          h("p", { class: "because" }, focus.because)),
        h("div", { class: "figure", "aria-hidden": "true" }, h("b", null, cause.games), h("span", null, `of ${plan.games_not_won} games not won`))),
      figures([
        [`Score, last ${win.games} games`, pct(win.score_pct)],
        ["Blunders per 100 moves", fmt(win.blunders_per_100_moves)],
        ["Games lost on time", pct(win.lost_on_time_pct_of_games)],
        ["Winning positions not won", pct(win.winning_positions_not_won_pct)],
      ]),
      section("Why the last " + plan.games_not_won + " games were not won", ribbon(plan),
        h("p", { class: "note", style: "margin-top:14px" }, "Each game gets one cause: the first rule that applies. A game lost on time only counts for the clock if the position was not already lost.")),
      section("Is it moving?", trend.length
        ? h("div", null, trend.map(([k, t]) => dumbbell(k, t)),
          h("p", { class: "note", style: "margin-top:12px" }, "The shaded band is how far a result can drift by chance alone. Open dot: earlier games. Solid dot: recent games. A change counts only when the solid dot leaves the band."))
        : h("p", { class: "note" }, "Not enough earlier games to compare.")),
      peerSection(peerData),
      section("What to do about it",
        h("ol", { class: "advice" }, focus.general_advice.map((t) => h("li", null, t))),
        h("p", { class: "note", style: "margin-top:16px" }, "Standard training guidance, not derived from your games.")),
      h("p", { class: "note", style: "margin-top:28px" }, `${status.games_analyzed} of ${status.games_total} games analysed.`));
  }

  // ---- chapter II: losses -----------------------------------------------------------
  async function weakness() {
    const w = await api("weakness");
    if (!w.games) return empty(w.note);
    const head = ["", "Moves", "Win-% lost", "Blunders /100", "Mistakes /100"];
    const rows = (g) => Object.entries(g).map(([k, v]) => [k, num(v.moves), num(fmt(v.avg_wp_loss)), num(fmt(v.blunders_per_100_moves)), num(fmt(v.mistakes_per_100_moves))]);
    const wp = w.winning_positions;
    return h("div", null,
      section(null, h("h2", null, "Where the ", h("em", null, "points"), " go"),
        h("p", { class: "lede" }, `${w.games} games, ${w.overall.moves.toLocaleString()} of your moves. A blunder here is a move that gives away at least 15 points of winning chance.`)),
      section("Blunders per 100 moves, by phase",
        bars(Object.entries(w.by_phase).map(([k, v]) => ({ label: k, value: v.blunders_per_100_moves, text: fmt(v.blunders_per_100_moves) })))),
      section("Winning positions",
        h("p", { class: "statement" }, `Of ${wp.games_reaching_winning_position} games in which you stood clearly better, `, h("b", null, `${wp.of_which_not_won} slipped away`),
          `. ${Math.round(100 * wp.share_of_all_blunders)}% of all your blunders were made while you were winning.`)),
      section("By phase", table(head, rows(w.by_phase))),
      section("By colour", table(head, rows(w.by_color))),
      section("By time control", table(head, rows(w.by_time_class))));
  }

  // ---- chapter III: openings --------------------------------------------------------
  async function openings() {
    const o = await api("openings");
    if (!o.openings.length) return empty("Not enough games per opening yet.");
    return h("div", null,
      section(null, h("h2", null, "Openings, in order of ", h("em", null, "trouble")),
        h("p", { class: "lede" }, "Results first, then how good the position was when the opening ended (50 is level). Playing White, the name is the opening your opponent chose.")),
      section(null, table(["Opening", "Colour", "Games", "Score", "Level on leaving", "Blunders"],
        o.openings.map((r) => [r.opening, r.color, num(r.games), num(pct(r.score_pct)), num(pct(r.avg_win_chance_leaving_opening)), num(r.opening_blunders)]))));
  }

  // ---- chapter IV: clock ------------------------------------------------------------
  async function clock() {
    const c = await api("clock");
    if (!c.games) return empty(c.note);
    const b = c.blunders;
    const left = c.avg_share_of_time_left_after_move_10;
    return h("div", null,
      section(null, h("h2", null, "The ", h("em", null, "clock")),
        h("p", { class: "lede" }, `${c.lost_on_time.games} of ${c.games} games ended on the clock; in ${c.lost_on_time.while_position_was_good} of them the position was still fine.`)),
      figures([
        ["Games lost on time", String(c.lost_on_time.games)],
        ["Position still fine", String(c.lost_on_time.while_position_was_good)],
        ["Time left after move 10", left == null ? "–" : `${Math.round(100 * left)}%`],
      ]),
      section("Blunders made with time to spare", bars([
        { label: "Hasty (2 s or less)", value: b.of_which_hasty },
        { label: "Considered (5 s or more)", value: b.of_which_considered },
        { label: "Short of time", value: b.made_when_short_of_time },
      ])),
      section("Errors by time pressure",
        table(["Zone", "Share of moves", "Blunders /100", "Mistakes /100"], Object.entries(c.by_time_zone).map(([k, v]) => [k, num(`${Math.round(100 * v.share_of_moves)}%`), num(fmt(v.blunders_per_100_moves)), num(fmt(v.mistakes_per_100_moves))])),
        h("p", { class: "note" }, "Low-time positions are often already decided, which hides errors. Judge the clock by games lost on time, not by this rate alone.")),
      section("Seconds per move", table(["Phase", "Seconds"], Object.entries(c.avg_seconds_per_move).map(([k, v]) => [k, num(fmt(v))]))));
  }

  // ---- chapter V: patterns ----------------------------------------------------------
  async function patterns() {
    const p = await api("patterns");
    if (!p.tilt.games) return empty("No analysed games yet.");
    const t = p.tilt, tod = p.time_of_day, res = p.resilience;
    const vs = (g, key) => (g.vs_games_not_after_a_loss && g.vs_games_not_after_a_loss[key] ? tag(g.vs_games_not_after_a_loss[key].verdict) : h("span", { class: "tag flat" }, "baseline"));
    const clear = Array.isArray(t.clear_signs_of_tilt) ? t.clear_signs_of_tilt : [];
    return h("div", null,
      section(null, h("h2", null, "How you ", h("em", null, "behave")),
        h("p", { class: "lede" }, clear.length ? "After a loss you play measurably worse than after a win." : "Nothing here is larger than chance would explain.")),
      section("After losses",
        table(["Game played after", "Games", "Score", "Blunders /100", "Score vs. baseline", "Blunders vs. baseline"],
          Object.entries(t.after_consecutive_losses).map(([k, g]) => [
            k === "0" ? "a win, a draw, or the first game" : `${k} loss${k === "1" ? "" : "es"} in a row`,
            num(g.games), num(pct(g.score_pct)), num(fmt(g.blunders_per_100_moves)), vs(g, "score_pct"), vs(g, "blunders_per_100_moves")])),
        h("p", { class: "note" }, `${t.sessions} sessions; a pause longer than ${t.session_gap_minutes} minutes starts a new one. Several comparisons are made at once, so a lone "worse" can be luck; a pattern that repeats across time controls is more credible.`)),
      section("Time of day (local clock)",
        table(["Period", "Games", "Score", "Blunders /100", "Score vs. other times"],
          Object.entries(tod.by_time_of_day).map(([k, g]) => [k, num(g.games), num(pct(g.score_pct)), num(fmt(g.blunders_per_100_moves)), g.vs_other_times.score_pct ? tag(g.vs_other_times.score_pct.verdict) : "–"])),
        h("p", { class: "note" }, `Weekdays ${pct(tod.weekday_vs_weekend.weekday.score_pct)} over ${tod.weekday_vs_weekend.weekday.games} games; weekends ${pct(tod.weekday_vs_weekend.weekend.score_pct)} over ${tod.weekday_vs_weekend.weekend.games}.`)),
      section("Saving bad positions",
        h("p", { class: "statement" }, `${res.games_clearly_worse_with_play_left} of ${res.games} games reached a clearly worse position with play left. `,
          h("b", null, `${fmt(res.saved_pct, "%")} of them were saved`), ` (${res.won} won, ${res.drawn} drawn, ${res.lost} lost).`),
        h("p", { class: "note" }, res.definition)));
  }

  // ---- chapter VI: season -----------------------------------------------------------
  let tileSeq = 0;
  function tile(g) {
    const W = 168, H = 64, n = g.curve.length;
    const x = (i) => (n === 1 ? 0 : (i / (n - 1)) * W);
    const y = (v) => ((100 - v) / 100) * H;
    const id = `clip${tileSeq++}`;
    const line = g.curve.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
    const area = `${line} L${x(n - 1).toFixed(1)},${H / 2} L0,${H / 2} Z`;
    const color = g.outcome === "W" ? "var(--pine)" : g.outcome === "L" ? "var(--oxblood)" : "var(--brass)";
    const when = new Date(g.end_time * 1000).toLocaleDateString(undefined, { month: "short", day: "numeric" });
    const svg = s("svg", { viewBox: `0 0 ${W} ${H}`, preserveAspectRatio: "none", role: "img", "aria-label": `Win chance over the game, ${g.outcome === "W" ? "won" : g.outcome === "L" ? "lost" : "drawn"}` },
      s("defs", null,
        s("clipPath", { id: `${id}a` }, s("rect", { x: 0, y: 0, width: W, height: H / 2 })),
        s("clipPath", { id: `${id}b` }, s("rect", { x: 0, y: H / 2, width: W, height: H / 2 }))),
      s("path", { d: area, "clip-path": `url(#${id}a)`, style: "fill:var(--pine);opacity:.28" }),
      s("path", { d: area, "clip-path": `url(#${id}b)`, style: "fill:var(--oxblood);opacity:.28" }),
      s("line", { x1: 0, x2: W, y1: H / 2, y2: H / 2, style: "stroke:var(--rule)", "stroke-width": 1, "stroke-dasharray": "3 3" }),
      s("path", { d: line, fill: "none", style: "stroke:var(--ink)", "stroke-width": 1.4, "stroke-linejoin": "round" }),
      g.blunders.map((i) => s("circle", { cx: x(i), cy: y(g.curve[i]), r: 2.8, style: "fill:var(--oxblood);stroke:var(--paper)", "stroke-width": 1 })));
    return h("a", { class: "tile", style: `--c:${color}`, href: g.url, target: "_blank", rel: "noopener noreferrer",
      title: `${g.opening}, ${g.time_class}, ${g.my_rating} vs ${g.opp_rating}. Click to replay.`,
      onclick: (e) => { if (e.metaKey || e.ctrlKey || e.shiftKey) return; e.preventDefault(); openGame(g.id); } },
      svg,
      h("div", { class: "meta" }, h("b", null, g.outcome === "W" ? "Won" : g.outcome === "L" ? "Lost" : "Drawn"), h("span", null, `${g.time_class} · ${when}`)),
      h("div", { class: "op" }, g.opening));
  }

  function calendar(days) {
    if (!days.length) return h("p", { class: "note" }, "No games.");
    const parse = (iso) => { const [y, m, d] = iso.split("-").map(Number); return new Date(y, m - 1, d); };
    const first = parse(days[0].date);
    const last = parse(days[days.length - 1].date);
    const monday = new Date(first);
    monday.setDate(first.getDate() - ((first.getDay() + 6) % 7));
    const byDate = new Map(days.map((d) => [d.date, d]));
    const weeks = Math.ceil(((last - monday) / 86400000 + 1) / 7);
    const C = 15, G = 4, left = 26, top = 16;
    const svg = s("svg", { width: left + weeks * (C + G), height: top + 7 * (C + G), role: "img", "aria-label": "Games per day" });
    ["Mon", "Wed", "Fri"].forEach((d, i) => svg.append(s("text", { x: 0, y: top + i * 2 * (C + G) + C - 3, "font-size": 9, "font-family": "sans-serif", style: "fill:var(--faint)" }, d)));
    let lastMonth = -1;
    for (let w = 0; w < weeks; w++) {
      for (let d = 0; d < 7; d++) {
        const date = new Date(monday); date.setDate(monday.getDate() + w * 7 + d);
        if (date > last) continue;
        const iso = `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
        const rec = byDate.get(iso);
        const cx = left + w * (C + G), cy = top + d * (C + G);
        if (d === 0 && date.getMonth() !== lastMonth && date >= first) {
          lastMonth = date.getMonth();
          svg.append(s("text", { x: cx, y: 10, "font-size": 10, "font-family": "sans-serif", "font-weight": 600, style: "fill:var(--faint)" }, date.toLocaleDateString(undefined, { month: "short" })));
        }
        if (!rec) { svg.append(s("rect", { x: cx, y: cy, width: C, height: C, rx: 2, style: "fill:var(--paper-2)" })); continue; }
        const score = (rec.W + 0.5 * rec.D) / rec.games;
        const colour = score > 0.5 ? "var(--pine)" : score < 0.5 ? "var(--oxblood)" : "var(--brass)";
        const strength = 0.3 + 0.7 * Math.min(1, rec.games / 12);
        svg.append(s("rect", { x: cx, y: cy, width: C, height: C, rx: 2, style: `fill:${colour};opacity:${strength.toFixed(2)}` },
          s("title", null, `${iso}: ${rec.W} won, ${rec.D} drawn, ${rec.L} lost`)));
      }
    }
    return h("div", { class: "calendar" }, svg);
  }

  async function season() {
    const d = await api("season");
    if (!d.games.length) return empty("No analysed games yet.");
    const wins = d.games.filter((g) => g.outcome === "W").length;
    return h("div", null,
      section(null, h("h2", null, "The ", h("em", null, "season"), " at a glance"),
        h("p", { class: "lede" }, `Your last ${d.games.length} games, newest first. Each line is your chance of winning, move by move; green above the middle is you better, red below is you worse, and a dot is a blunder. Click any game to replay it.`),
        h("div", { class: "key" },
          h("span", null, h("i", { style: "background:var(--pine)" }), "won"),
          h("span", null, h("i", { style: "background:var(--oxblood)" }), "lost"),
          h("span", null, h("i", { style: "background:var(--brass)" }), "drawn"),
          h("span", null, `${wins} of ${d.games.length} won`)),
        h("div", { class: "strip" }, d.games.map(tile))),
      section("Rhythm", h("p", { class: "note" }, "One square per day. Green: you won more than you lost. Red: the other way. Deeper colour: more games that day."), calendar(d.days)));
  }


  // ---- game viewer ------------------------------------------------------------------
  let viewer = null;
  function ensureViewer() {
    if (viewer) return viewer;
    const dialog = h("dialog", { class: "viewer", "aria-label": "Game viewer" });
    document.body.append(dialog);
    dialog.addEventListener("click", (e) => { if (e.target === dialog) dialog.close(); });   // backdrop click
    viewer = dialog;
    return viewer;
  }

  const KIND_WORD = { blunder: "a blunder", mistake: "a mistake", inaccuracy: "an inaccuracy" };

  async function openGame(id) {
    const dialog = ensureViewer();
    dialog.replaceChildren(h("div", { class: "v-head" }, h("h2", null, "Loading\u2026")));
    if (!dialog.open) dialog.showModal();
    let g;
    try {
      const res = await fetch(`/api/game?${query({ id })}`);
      g = await res.json();
      if (!res.ok) throw new Error(g.error || "Could not load the game");
    } catch (err) {
      dialog.replaceChildren(h("div", { class: "v-head" }, h("h2", null, err.message),
        h("button", { class: "v-close", onclick: () => dialog.close(), "aria-label": "Close" }, "\u00d7")));
      return;
    }
    renderViewer(dialog, g);
  }

  function renderViewer(dialog, g) {
    const plies = g.plies;
    const last = plies.length - 1;
    const flip = g.color === "black";
    const first = plies.findIndex((p) => p.mine && p.kind === "blunder");
    let cursor = first >= 0 ? first : 0;

    const boardHost = h("div", { class: "v-board" });
    const caption = h("div", { class: "v-caption", "aria-live": "polite" });
    const movesBox = h("div", { class: "moves" });
    const curveBox = h("div", { class: "v-curve" });

    // moves as chips
    const chips = [];
    plies.slice(0, last).forEach((p, i) => {
      if (p.mover === "white") movesBox.append(h("span", { class: "no" }, `${Math.floor(p.ply / 2) + 1}.`));
      const chip = h("button", {
        class: `mv ${p.mine ? "mine" : "theirs"} k-${p.kind || "ok"}`, onclick: () => go(i),
        title: p.kind && p.kind !== "ok" ? `${p.kind}, ${p.loss} points` : "",
      }, p.san);
      chips.push(chip);
      movesBox.append(chip, " ");
    });

    // evaluation curve with a playhead
    const W = 420, H = 100, n = plies.length;
    const x = (i) => (n === 1 ? 0 : (i / (n - 1)) * W);
    const y = (v) => ((100 - v) / 100) * H;
    const line = plies.map((p, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(p.wp).toFixed(1)}`).join(" ");
    const area = `${line} L${x(n - 1).toFixed(1)},${H / 2} L0,${H / 2} Z`;
    const playhead = s("line", { y1: 0, y2: H, style: "stroke:var(--ink)", "stroke-width": 1.5 });
    const marker = s("circle", { r: 4.5, style: "fill:var(--ink);stroke:var(--paper)", "stroke-width": 1.5 });
    const curve = s("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": "Your winning chance through the game" },
      s("defs", null,
        s("clipPath", { id: "vclipA" }, s("rect", { x: 0, y: 0, width: W, height: H / 2 })),
        s("clipPath", { id: "vclipB" }, s("rect", { x: 0, y: H / 2, width: W, height: H / 2 }))),
      s("path", { d: area, "clip-path": "url(#vclipA)", style: "fill:var(--pine);opacity:.28" }),
      s("path", { d: area, "clip-path": "url(#vclipB)", style: "fill:var(--oxblood);opacity:.28" }),
      s("line", { x1: 0, x2: W, y1: H / 2, y2: H / 2, style: "stroke:var(--rule)", "stroke-dasharray": "3 3" }),
      s("path", { d: line, fill: "none", style: "stroke:var(--ink)", "stroke-width": 1.5, "stroke-linejoin": "round" }),
      plies.slice(0, last).map((p, i) => (p.kind === "blunder"
        ? s("circle", { cx: x(i + 1), cy: y(plies[i + 1].wp), r: 3.2,
          style: `fill:${p.mine ? "var(--oxblood)" : "var(--pine)"};stroke:var(--paper)`, "stroke-width": 1 }) : null)),
      playhead, marker,
      s("rect", { x: 0, y: 0, width: W, height: H, fill: "transparent", style: "cursor:crosshair" }));
    curve.addEventListener("click", (e) => {
      const box = curve.getBoundingClientRect();
      go(Math.max(0, Math.min(last, Math.round(((e.clientX - box.left) / box.width) * (n - 1)))));
    });
    curveBox.append(curve);

    function go(i) {
      cursor = Math.max(0, Math.min(last, i));
      const p = plies[cursor];
      const arrows = [];
      if (p.uci) arrows.push({ uci: p.uci, color: p.kind === "blunder" || p.kind === "mistake" ? ARROW.played : ARROW.answer });
      if (p.best_uci && p.best_uci !== p.uci) arrows.push({ uci: p.best_uci, color: ARROW.best });
      boardHost.replaceChildren(ChessBoard.renderBoard(p.fen, { flip, arrows }));

      const bits = [];
      if (cursor === last) {
        bits.push(h("b", null, "Final position. "), `${g.outcome === "W" ? "You won" : g.outcome === "L" ? "You lost" : "Drawn"} (${g.result_code}).`);
      } else {
        const who = p.mine ? "You" : "Your opponent";
        bits.push(h("b", null, `${Math.floor(p.ply / 2) + 1}${p.mover === "white" ? "." : "..."} `), `${who} played ${p.san}`);
        if (p.kind && KIND_WORD[p.kind]) bits.push(", ", h("span", { class: `kind-${p.kind}` }, KIND_WORD[p.kind]), ` (${p.loss} points of winning chance)`);
        bits.push(". ");
        if (p.best_san && p.best_uci !== p.uci && KIND_WORD[p.kind]) bits.push(`${p.best_san} was better. `);
        bits.push(h("span", { class: "muted" }, `Your winning chance: ${p.wp}% \u2192 ${plies[cursor + 1].wp}%.`));
      }
      caption.replaceChildren(...bits);

      chips.forEach((c, k) => c.classList.toggle("active", k === cursor));
      const active = chips[cursor];
      if (active) active.scrollIntoView({ block: "nearest", inline: "nearest" });
      playhead.setAttribute("x1", x(cursor)); playhead.setAttribute("x2", x(cursor));
      marker.setAttribute("cx", x(cursor)); marker.setAttribute("cy", y(p.wp));
    }

    const nextError = (dir) => {
      for (let k = cursor + dir; k >= 0 && k < last; k += dir) {
        if (plies[k].mine && (plies[k].kind === "blunder" || plies[k].kind === "mistake")) return k;
      }
      return cursor;
    };
    const btn = (label, title, fn) => h("button", { class: "v-btn", title, "aria-label": title, onclick: fn }, label);
    const when = new Date(g.end_time * 1000).toLocaleDateString(undefined, { year: "numeric", month: "long", day: "numeric" });

    dialog.replaceChildren(
      h("div", { class: "v-head" },
        h("div", null,
          h("h2", null, g.opening, " ", h("em", null, g.outcome === "W" ? "won" : g.outcome === "L" ? "lost" : "drawn")),
          h("div", { class: "sub" }, `${g.color === "white" ? "White" : "Black"} \u00b7 ${g.my_rating} against ${g.opp_rating} \u00b7 ${g.time_class} \u00b7 ${when}`)),
        h("div", { class: "v-tools" },
          h("a", { href: g.url, target: "_blank", rel: "noopener noreferrer" }, "Open on chess.com"),
          h("button", { class: "v-close", onclick: () => dialog.close(), "aria-label": "Close" }, "\u00d7"))),
      h("div", { class: "v-body" },
        h("div", null, boardHost,
          h("div", { class: "v-controls" },
            btn("\u21e4", "Start (Home)", () => go(0)), btn("\u2190", "Previous move (Left arrow)", () => go(cursor - 1)),
            btn("\u2192", "Next move (Right arrow)", () => go(cursor + 1)), btn("\u21e5", "End (End)", () => go(last)),
            h("span", { class: "v-gap" }),
            btn("\u2039 error", "Previous mistake of yours", () => go(nextError(-1))),
            btn("error \u203a", "Next mistake of yours", () => go(nextError(1))))),
        h("div", null, caption, curveBox,
          h("div", { class: "legend" },
            h("span", null, h("i", { style: `background:${ARROW.played}` }), "played, if it cost you"),
            h("span", null, h("i", { style: `background:${ARROW.answer}` }), "played"),
            h("span", null, h("i", { style: `background:${ARROW.best}` }), "engine preferred")),
          h("p", { class: "note", style: "margin:6px 0 0" }, "Red dots: your blunders. Green dots: your opponent\u2019s."),
          movesBox)));

    dialog.onkeydown = (e) => {
      const keys = { ArrowLeft: () => go(cursor - 1), ArrowRight: () => go(cursor + 1), Home: () => go(0), End: () => go(last) };
      if (keys[e.key]) { e.preventDefault(); keys[e.key](); }
    };
    go(cursor);
  }

  // ---- chapter VII: moments ---------------------------------------------------------
  function plate(e, number, lines) {
    const flip = e.color === "black";
    const arrows = [{ uci: e.played_uci, color: ARROW.played }, { uci: e.best_uci, color: ARROW.best }];
    return h("figure", null, ChessBoard.renderBoard(e.fen, { flip, arrows }),
      h("figcaption", null, h("span", { class: "n" }, number), lines));
  }

  async function moments() {
    const m = await api("moments");
    const legend = h("p", { class: "legend" },
      h("span", null, h("i", { style: `background:${ARROW.played}` }), "what you played"),
      h("span", null, h("i", { style: `background:${ARROW.best}` }), "what the engine preferred"));
    const blunders = m.blunders.blunders.map((e, i) => plate(e, `Plate ${i + 1}`, [
      h("b", null, `Move ${e.move_number}, ${e.color}. `), `You played ${e.played}; ${e.best || "another move"} was better. `,
      `Your winning chance went from ${e.win_chance_before}% to ${e.win_chance_after}%${e.was_winning ? ", having been ahead" : ""}.`]));
    const missed = m.missed.worst_missed.map((e, i) => plate(e, `Chance ${i + 1}`, [
      h("b", null, `Move ${e.move_number}. `), `Your opponent had just blundered. You played ${e.played}; ${e.best || "another move"} kept the advantage. `,
      `Winning chance ${e.win_chance_before_your_move}% to ${e.win_chance_after_your_move}%.`]));
    return h("div", null,
      section(null, h("h2", null, "Moments that ", h("em", null, "mattered")), legend,
        h("p", { class: "lede" }, "The costliest blunders the engine confirmed at depth, and the best chances you let go."),
        blunders.length ? h("div", { class: "plates" }, blunders) : empty("No confirmed blunders.")),
      section(`Chances missed (you used ${Math.round(100 * (m.missed.punish_rate || 0))}% of ${m.missed.opponent_blunders_you_could_use} opponent blunders)`,
        missed.length ? h("div", { class: "plates" }, missed) : empty("None recorded.")));
  }

  // ---- chapter VIII: train ----------------------------------------------------------
  const STORE = "chess-coach-train";
  const INTERVALS_DAYS = [0, 1, 3, 7, 21];   // spacing after 0..4 correct answers in a row
  function loadProgress() { try { return JSON.parse(localStorage.getItem(STORE) || "{}"); } catch (e) { return {}; } }
  function saveProgress(p) { try { localStorage.setItem(STORE, JSON.stringify(p)); } catch (e) { /* storage unavailable */ } }

  const VERDICT_TEXT = { best: "Best.", good: "Good.", inaccurate: "Inaccurate.", mistake: "A mistake.", blunder: "A blunder." };
  const session = { tried: 0, solved: 0, streak: 0 };

  async function train() {
    const progress = loadProgress();
    const now = Date.now();
    const resting = Object.entries(progress).filter(([, p]) => p.due > now).map(([id]) => id);
    const data = await api("trainer", { fresh: true, query: { n: 15, exclude: resting.join(",") } });
    if (!data.puzzles.length) return empty("Nothing due right now. Positions you solved come back after a few days; new ones appear as you play and analyse more games.");

    const host = h("div");
    let index = 0;
    const stage = h("div", { class: "studio" });
    host.append(section(null, h("h2", null, "Find the ", h("em", null, "move")),
      h("p", { class: "lede" }, "These are positions from your own games where a move cost you dearly. Click a piece, then its destination. Any move that keeps the position counts, not only the engine's first choice."), stage));

    const scoreBox = h("div", { class: "score" });
    function paintScore() {
      scoreBox.replaceChildren(
        h("div", null, h("b", null, `${session.solved}/${session.tried}`), h("span", null, "solved")),
        h("div", null, h("b", null, session.streak), h("span", null, "in a row")),
        h("div", null, h("b", null, data.available), h("span", null, "positions available")));
    }

    function show() {
      if (index >= data.puzzles.length) {
        stage.replaceChildren(h("div", { class: "prompt" }, h("div", { class: "verdict-word ok" }, "That's the set."),
          h("p", { class: "lede" }, "Fetch another set whenever you like."),
          h("div", { class: "row" }, h("button", { class: "btn", onclick: () => show_tab("train") }, "New set"))), scoreBox);
        paintScore();
        return;
      }
      const p = data.puzzles[index];
      const boardHost = h("div", { class: "board" });
      const feedback = h("div");
      const skip = h("button", { class: "btn quiet", onclick: () => { index += 1; show(); } }, "Skip");
      const prompt = h("div", { class: "prompt" },
        h("div", { class: "to-move" }, `${p.color === "white" ? "White" : "Black"} to move`),
        h("h2", null, "What would you play?"),
        h("p", { class: "note" }, `Move ${p.move_number}, ${p.phase}. Here you stood at ${p.win_chance}%.`),
        feedback, h("div", { class: "row" }, skip), scoreBox);
      stage.replaceChildren(boardHost, prompt);
      paintScore();

      let busy = false;
      const controller = ChessBoard.interactive(boardHost, p.fen, {
        flip: p.color === "black",
        async onMove(uci) {
          if (busy) return;
          busy = true;
          feedback.replaceChildren(h("p", { class: "note" }, "Checking with the engine…"));
          try {
            const res = await fetch(`/api/trainer/check?${query()}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ id: p.id, uci }) });
            const r = await res.json();
            if (!res.ok) { feedback.replaceChildren(h("p", { class: "error" }, `${r.error}. Try another move.`)); busy = false; return; }
            controller.lock();
            controller.show([{ uci: r.played_uci, color: ARROW.played }, { uci: r.best_uci, color: ARROW.best }, { uci, color: ARROW.answer }]);
            session.tried += 1;
            if (r.solved) { session.solved += 1; session.streak += 1; } else session.streak = 0;
            const prog = loadProgress();
            const box = r.solved ? Math.min(4, ((prog[p.id] || {}).box || 0) + 1) : 0;
            prog[p.id] = { box, due: Date.now() + (r.solved ? INTERVALS_DAYS[box] * 86400000 : 10 * 60000) };
            saveProgress(prog);
            const lost = r.win_chance_lost;
            feedback.replaceChildren(
              h("div", { class: `verdict-word ${r.solved ? "ok" : "no"}` }, VERDICT_TEXT[r.verdict] || r.verdict),
              h("p", null, r.verdict === "best" ? `${r.answer} is exactly what the engine wanted.`
                : r.verdict === "good" ? `${r.answer} holds the position too${lost ? ` (it gives up about ${lost} points)` : ""}. The engine\u2019s first choice was ${r.best}.`
                  : `You chose ${r.answer}${lost ? `, which costs about ${lost} points of winning chance` : ""}. The engine preferred ${r.best}.`),
              h("p", { class: "note" }, `In the game you played ${r.played_in_game}.`),
              h("div", { class: "legend" },
                h("span", null, h("i", { style: `background:${ARROW.answer}` }), "your answer"),
                h("span", null, h("i", { style: `background:${ARROW.best}` }), "engine"),
                h("span", null, h("i", { style: `background:${ARROW.played}` }), "in the game")));
            skip.replaceWith(h("button", { class: "btn", onclick: () => { index += 1; show(); } }, "Next position"));
            paintScore();
          } catch (err) {
            feedback.replaceChildren(h("p", { class: "error" }, err.message));
            busy = false;
          }
        },
      });
    }
    show();
    return host;
  }

  // ---- wiring -----------------------------------------------------------------------
  const TABS = { overview, weakness, openings, clock, patterns, season, moments, train };

  let renderToken = 0;
  async function render() {
    const token = ++renderToken;     // a slow answer for an older tab must not replace a newer one
    statusEl.textContent = "";
    view.replaceChildren(h("p", { class: "note", style: "padding:40px 0" }, "Reading the games…"));
    let node;
    try { node = await TABS[state.tab](); }
    catch (err) { node = h("p", { class: "error" }, err.message); }
    if (token !== renderToken) return;
    view.replaceChildren(node);
    view.style.animation = "none"; void view.offsetWidth; view.style.animation = "";
    if (token === renderToken) warm(token);
  }

  // After the visible chapter has drawn, fetch the others quietly so switching is instant.
  async function warm(token) {
    for (const path of ["coach", "peers", "weakness", "openings", "clock", "patterns", "season", "moments"]) {
      if (token !== renderToken) return;
      try { await api(path); } catch (e) { /* the chapter will report it when opened */ }
    }
  }

  function show_tab(name) {
    state.tab = name;
    document.querySelectorAll("#tabs [role=tab]").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.tab === name)));
    render();
  }

  function remember(key, value) { try { localStorage.setItem(key, value); } catch (e) { /* storage unavailable */ } }
  function recall(key) { try { return localStorage.getItem(key) || ""; } catch (e) { return ""; } }

  document.getElementById("tabs").addEventListener("click", (e) => {
    const b = e.target.closest("[role=tab]");
    if (b) show_tab(b.dataset.tab);
  });
  document.getElementById("filters").addEventListener("submit", (e) => {
    e.preventDefault();
    state.username = document.getElementById("username").value.trim();
    state.timeClass = document.getElementById("time-class").value;
    remember("chess-coach-username", state.username);
    state.cache = {};
    render();
  });


  // ---- theme ------------------------------------------------------------------------
  function applyTheme(choice) {
    if (choice === "light" || choice === "dark") document.documentElement.dataset.theme = choice;
    else delete document.documentElement.dataset.theme;
    document.querySelectorAll("[data-theme-choice]").forEach((b) =>
      b.setAttribute("aria-pressed", String(b.dataset.themeChoice === (choice === "light" || choice === "dark" ? choice : "auto"))));
  }
  document.querySelector(".theme").addEventListener("click", (e) => {
    const b = e.target.closest("[data-theme-choice]");
    if (!b) return;
    applyTheme(b.dataset.themeChoice);
    try { localStorage.setItem("chess-coach-theme", b.dataset.themeChoice); } catch (err) { /* storage unavailable */ }
  });
  applyTheme((() => { try { return localStorage.getItem("chess-coach-theme") || "auto"; } catch (err) { return "auto"; } })());

  (async function init() {
    let configured = "";
    try { configured = (await (await fetch("/api/config")).json()).default_username || ""; } catch (e) { /* offline */ }
    state.username = configured || recall("chess-coach-username");
    document.getElementById("username").value = state.username;
    const start = location.hash.replace("#", "");
    if (TABS[start]) { state.tab = start; document.querySelectorAll("#tabs [role=tab]").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.tab === start))); }
    if (state.username) render();
    else view.replaceChildren(empty("Enter your chess.com name above and press Open."));
  })();
})();
