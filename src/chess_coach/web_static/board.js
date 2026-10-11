/* Chess board renderer: FEN -> SVG, with arrows, highlights and click-to-move.
   No dependencies. Square colours come from CSS variables (--sq-light, --sq-dark). */
(function () {
  "use strict";

  // Solid glyphs for both colours, painted white or black, so every piece has one shape.
  // U+FE0E asks for text rather than emoji presentation.
  const GLYPH = { k: "♚", q: "♛", r: "♜", b: "♝", n: "♞", p: "♟" };
  const FONTS = "'Segoe UI Symbol','Noto Sans Symbols 2','Apple Symbols','DejaVu Sans',sans-serif";
  const SVG_NS = "http://www.w3.org/2000/svg";
  const CELL = 48;

  function svg(tag, attrs, text) {
    const el = document.createElementNS(SVG_NS, tag);
    for (const [k, v] of Object.entries(attrs || {})) el.setAttribute(k, String(v));
    if (text !== undefined) el.textContent = text;
    return el;
  }

  /** FEN -> 8 rows (rank 8 first) of piece letters or null. */
  function parseFen(fen) {
    const rows = fen.split(" ")[0].split("/");
    if (rows.length !== 8) throw new Error("bad FEN");
    return rows.map((row) => {
      const out = [];
      for (const ch of row) {
        if (/[1-8]/.test(ch)) for (let i = 0; i < Number(ch); i++) out.push(null);
        else out.push(ch);
      }
      if (out.length !== 8) throw new Error("bad FEN");
      return out;
    });
  }

  const fileOf = (name) => name.charCodeAt(0) - 97;
  const rankOf = (name) => Number(name[1]) - 1;
  const nameOf = (file, rank) => String.fromCharCode(97 + file) + (rank + 1);

  /**
   * options: flip (view from Black), arrows [{uci, color}], highlights [square names],
   * coords (default true). Returns an <svg>; scale it with CSS.
   */
  function renderBoard(fen, options) {
    const opts = options || {};
    const flip = !!opts.flip;
    const board = parseFen(fen);
    const size = CELL * 8;
    const root = svg("svg", { viewBox: `0 0 ${size} ${size}`, role: "img", "aria-label": "Chess position" });
    root.dataset.flip = flip ? "1" : "0";

    const at = (file, rank) => ({ x: (flip ? 7 - file : file) * CELL, y: (flip ? rank : 7 - rank) * CELL });
    const colors = { light: "var(--sq-light)", dark: "var(--sq-dark)" };

    for (let rank = 0; rank < 8; rank++) {
      for (let file = 0; file < 8; file++) {
        const { x, y } = at(file, rank);
        const dark = (file + rank) % 2 === 0;
        root.append(svg("rect", { x, y, width: CELL, height: CELL, style: `fill:${dark ? colors.dark : colors.light}` }));
        if (opts.coords !== false) {
          const label = (flip ? rank === 7 : rank === 0) ? String.fromCharCode(97 + file) : null;
          const num = (flip ? file === 7 : file === 0) ? String(rank + 1) : null;
          const tone = `fill:${dark ? colors.light : colors.dark};opacity:.9`;
          if (label) root.append(svg("text", { x: x + CELL - 4, y: y + CELL - 4, "text-anchor": "end", "font-size": 9, "font-weight": 600, "font-family": "sans-serif", style: tone }, label));
          if (num) root.append(svg("text", { x: x + 4, y: y + 11, "font-size": 9, "font-weight": 600, "font-family": "sans-serif", style: tone }, num));
        }
      }
    }

    (opts.highlights || []).forEach((sq) => {
      const { x, y } = at(fileOf(sq), rankOf(sq));
      root.append(svg("rect", { x, y, width: CELL, height: CELL, fill: "#d4a72c", opacity: 0.55 }));
    });

    for (let r = 0; r < 8; r++) {            // r = 0 is rank 8
      for (let file = 0; file < 8; file++) {
        const piece = board[r][file];
        if (!piece) continue;
        const white = piece === piece.toUpperCase();
        const { x, y } = at(file, 7 - r);
        root.append(svg("text", {
          x: x + CELL / 2, y: y + CELL * 0.79, "text-anchor": "middle", "font-size": CELL * 0.86,
          fill: white ? "#fbf8ef" : "#1a1916", stroke: white ? "#1a1916" : "#fbf8ef",
          "stroke-width": 0.9, "paint-order": "stroke", "font-family": FONTS, "pointer-events": "none",
        }, GLYPH[piece.toLowerCase()] + "︎"));
      }
    }

    (opts.arrows || []).forEach((a) => {
      if (!a.uci || a.uci.length < 4) return;
      const p1 = at(fileOf(a.uci.slice(0, 2)), rankOf(a.uci.slice(0, 2)));
      const p2 = at(fileOf(a.uci.slice(2, 4)), rankOf(a.uci.slice(2, 4)));
      const [x1, y1, x2, y2] = [p1.x + CELL / 2, p1.y + CELL / 2, p2.x + CELL / 2, p2.y + CELL / 2];
      const len = Math.hypot(x2 - x1, y2 - y1) || 1;
      const ux = (x2 - x1) / len;
      const uy = (y2 - y1) / len;
      const head = 15;
      const bx = x2 - ux * head;
      const by = y2 - uy * head;
      const color = a.color || "#9a2f22";
      root.append(svg("line", { x1, y1, x2: bx, y2: by, stroke: color, "stroke-width": 7, "stroke-linecap": "round", opacity: 0.88, "pointer-events": "none" }));
      root.append(svg("polygon", {
        points: `${x2},${y2} ${bx - uy * 9.5},${by + ux * 9.5} ${bx + uy * 9.5},${by - ux * 9.5}`,
        fill: color, opacity: 0.88, "pointer-events": "none",
      }));
    });
    return root;
  }

  /**
   * Click-to-move board. `onMove(uci)` is called with a move such as "e2e4" ("e7e8q" for
   * a pawn reaching the last rank: promotion to a queen). Legality is not checked here.
   * Returns { show(arrows), lock(), unlock() }.
   */
  function interactive(host, fen, options) {
    const opts = options || {};
    const board = parseFen(fen);
    const whiteToMove = fen.split(" ")[1] !== "b";
    let selected = null;
    let locked = false;
    let arrows = [];

    const pieceAt = (sq) => board[7 - rankOf(sq)][fileOf(sq)];
    const mine = (p) => p && (p === p.toUpperCase()) === whiteToMove;

    function draw() {
      const el = renderBoard(fen, { flip: opts.flip, arrows, highlights: selected ? [selected] : [] });
      el.addEventListener("click", onClick);
      host.replaceChildren(el);
    }

    function onClick(event) {
      if (locked) return;
      const el = event.currentTarget;
      const box = el.getBoundingClientRect();
      const col = Math.min(7, Math.max(0, Math.floor(((event.clientX - box.left) / box.width) * 8)));
      const row = Math.min(7, Math.max(0, Math.floor(((event.clientY - box.top) / box.height) * 8)));
      const flip = el.dataset.flip === "1";
      const sq = nameOf(flip ? 7 - col : col, flip ? row : 7 - row);
      const piece = pieceAt(sq);
      if (!selected) {
        if (mine(piece)) { selected = sq; draw(); }
        return;
      }
      if (sq === selected) { selected = null; draw(); return; }
      if (mine(piece)) { selected = sq; draw(); return; }
      const mover = pieceAt(selected);
      const promotes = mover && mover.toLowerCase() === "p" && (sq[1] === "8" || sq[1] === "1");
      const uci = selected + sq + (promotes ? "q" : "");
      selected = null;
      draw();
      if (opts.onMove) opts.onMove(uci);
    }

    draw();
    return {
      show(next) { arrows = next || []; selected = null; draw(); },
      lock() { locked = true; selected = null; draw(); },
      unlock() { locked = false; },
    };
  }

  window.ChessBoard = { renderBoard, interactive, parseFen };
})();
