"""Read a Mermaid flowchart and lay it out as a Diagram, with the same measuring and checks as everything else.

Supported: `flowchart` or `graph` with LR, RL, TD, TB or BT; nodes with any bracket shape (`A[Text]`, `A(Text)`,
`A{Text}`, `A((Text))`, quoted text); chains (`A --> B --> C`) and `&`; edge labels (`-->|text|`, `-- text -->`);
dotted edges (`-.->`), which become planned connections; top-level `subgraph ... end`, which become groups;
`class A,B name` and `A:::name`, where a class named like a state (live, building, trial, planned, retiring)
sets that state; a `title:` in front matter; and Mermaid's `curve` setting, where `step`, `stepBefore` and
`stepAfter` (in front matter or a `%%{init: ...}%%` line) draw right-angle lines. Styling lines (`classDef`, `style`,
`linkStyle`, `click`) are ignored.

Layout: nodes go in layers by longest path, each subgraph gets its own band so groups never overlap other nodes,
and lines that skip a layer or point backwards travel in a corridor above or below the picture.
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

from .blocks import Tile
from .diagram import Diagram
from .fonts import text_width
from .theme import Theme


class MermaidError(ValueError):
    """A line of the Mermaid text that could not be read, with its line number."""


_HEADER = re.compile(r"^(?:flowchart|graph)\s*(LR|RL|TD|TB|BT)?\s*;?\s*$", re.I)
_SHAPES = [("((", "))"), ("([", "])"), ("[[", "]]"), ("[(", ")]"), ("{{", "}}"), ("[/", "/]"), ("[\\", "\\]"),
           ("[/", "\\]"), ("[\\", "/]"), ("[", "]"), ("(", ")"), ("{", "}"), (">", "]")]
_ID = re.compile(r"^([A-Za-z0-9_][\w.-]*)")
_EDGE = re.compile(r"\s*(<?(?:-\.+->|-\.+-|={2,}>|={3,}|-{2,}>|-{3,}|--[ox]))\s*(?:\|([^|]*)\|)?\s*")
_CURVE = re.compile(r"""curve['"]?\s*:\s*['"]?([A-Za-z]+)""")
_LABELED = [(re.compile(r"(?<![-.=<])--\s+([^-|>][^|]*?)\s+(-->|---)"), r"\2|\1|"),
            (re.compile(r"-\.\s+([^.|][^|]*?)\s+\.->"), r"-.->|\1|"),
            (re.compile(r"==\s+([^=|][^|]*?)\s+==>"), r"==>|\1|")]


def _clean(text: str) -> str:
    text = text.strip()
    if len(text) >= 2 and text[0] == text[-1] == '"':
        text = text[1:-1]
    return re.sub(r"\s*<br\s*/?>\s*", " ", text).strip()


def _split_outside(line: str, marker: str, limit: int = 0) -> List[str]:
    """Split a line at each `marker` that is not inside double quotes. Mermaid keeps `;` and `%%` inside a quoted
    label as text, so `a["a;b"] --> b; c` is two statements and the first node reads `a;b`."""
    parts, start, i = [], 0, 0
    while i < len(line):
        if line[i] == '"':
            end = line.find('"', i + 1)
            i = len(line) if end < 0 else end + 1      # a quote that is never closed runs to the end of the line
            continue
        if line.startswith(marker, i):
            parts.append(line[start:i])
            i, start = i + len(marker), i + len(marker)
            if limit and len(parts) == limit:
                break
            continue
        i += 1
    parts.append(line[start:])
    return parts


def _node(token: str, line_no: int) -> Tuple[str, Optional[str], Optional[str]]:
    """Parse 'A', 'A[Text]', 'A:::cls' into (id, text or None, class or None)."""
    token = token.strip()
    cls = None
    m = re.search(r":::([\w-]+)\s*$", token)
    if m:
        cls, token = m.group(1), token[:m.start()].strip()
    m = _ID.match(token)
    if not m:
        raise MermaidError(f"line {line_no}: cannot read node '{token}'")
    node_id, rest = m.group(1), token[m.end():].strip()
    if not rest:
        return node_id, None, cls
    for open_, close in _SHAPES:
        if rest.startswith(open_) and rest.endswith(close) and len(rest) >= len(open_) + len(close):
            return node_id, _clean(rest[len(open_):len(rest) - len(close)]), cls
    raise MermaidError(f"line {line_no}: cannot read the shape of node '{token}'")


class _Graph:
    def __init__(self):
        self.order: List[str] = []
        self.text: Dict[str, str] = {}
        self.cls: Dict[str, str] = {}
        self.edges: List[Tuple[str, str, str, Optional[str]]] = []
        self.groups: List[Tuple[str, Optional[str]]] = []   # (title, None)
        self.group_of: Dict[str, int] = {}
        self.direction = "LR"
        self.title: Optional[str] = None
        self.curve: Optional[str] = None

    def touch(self, node_id, text, cls, group):
        if node_id not in self.text:
            self.order.append(node_id)
            self.text[node_id] = node_id
        if text:
            self.text[node_id] = text
        if cls:
            self.cls[node_id] = cls
        if group is not None and node_id not in self.group_of:
            self.group_of[node_id] = group


def parse(source: str) -> _Graph:
    g = _Graph()
    lines = source.splitlines()
    i = 0
    if lines and lines[0].strip() == "---":
        for j in range(1, len(lines)):
            if lines[j].strip() == "---":
                for meta in lines[1:j]:
                    k, _, v = meta.partition(":")
                    if k.strip().lower() == "title":
                        g.title = _clean(v)
                    elif k.strip().lower() == "curve":
                        g.curve = v.strip().strip("'\"")
                i = j + 1
                break
    header_seen = False
    stack: List[Optional[int]] = []
    for line_no in range(i, len(lines)):
        if lines[line_no].lstrip().startswith("%%{"):
            m = _CURVE.search(lines[line_no])
            if m:
                g.curve = m.group(1)
        raw = _split_outside(lines[line_no], "%%", 1)[0].strip()
        for stmt in (s.strip() for s in _split_outside(raw, ";")):
            if not stmt:
                continue
            n = line_no + 1
            if not header_seen:
                m = _HEADER.match(stmt)
                if not m:
                    raise MermaidError(f"line {n}: expected 'flowchart LR' or 'graph TD' first, got '{stmt}'")
                g.direction = (m.group(1) or "TD").upper()
                header_seen = True
                continue
            word = stmt.split()[0].lower()
            has_edge = _EDGE.search(stmt) is not None
            if word in ("classdef", "style", "linkstyle", "click", "direction") and not has_edge:
                continue
            if word == "subgraph":
                rest = stmt[len("subgraph"):].strip()
                m = re.match(r'^([\w.-]+)\s*\[\s*(.*?)\s*\]\s*$', rest)
                title = _clean(m.group(2)) if m else _clean(rest) or f"Group {len(g.groups) + 1}"
                if stack and stack[0] is not None:
                    stack.append(stack[0])          # nested: nodes join the top-level group
                else:
                    g.groups.append((title, None))
                    stack.append(len(g.groups) - 1)
                continue
            if word == "end":
                if not stack:
                    raise MermaidError(f"line {n}: 'end' without a matching 'subgraph'")
                stack.pop()
                continue
            if word == "class" and not has_edge:
                parts = stmt.split()
                if len(parts) >= 3:
                    for node_id in parts[1].split(","):
                        g.cls[node_id.strip()] = parts[2]
                continue
            group = stack[0] if stack else None
            for pattern, repl in _LABELED:
                stmt = pattern.sub(repl, stmt)
            pieces = _EDGE.split(stmt)
            # pieces: node, op, label, node, op, label, node ...
            groups_of_nodes = []
            for k in range(0, len(pieces), 3):
                ids = []
                for token in pieces[k].split("&"):
                    node_id, text, cls = _node(token, n)
                    g.touch(node_id, text, cls, group)
                    ids.append(node_id)
                groups_of_nodes.append(ids)
            for k in range(1, len(pieces), 3):
                op, label = pieces[k], pieces[k + 1]
                kind = "planned" if "." in op else "flow"
                for a in groups_of_nodes[(k - 1) // 3]:
                    for b in groups_of_nodes[(k + 2) // 3]:
                        if a != b:
                            g.edges.append((a, b, kind, _clean(label) if label else None))
    if not header_seen:
        raise MermaidError("no 'flowchart' or 'graph' line found")
    if stack:
        raise MermaidError("a 'subgraph' is missing its 'end'")
    return g


def from_mermaid(source: str, title: Optional[str] = None, theme: Optional[Theme] = None,
                 routing: Optional[str] = None) -> Diagram:
    """Lay out a Mermaid flowchart as a Diagram. Call .save() or .render() on the result as usual.

    routing: 'curved' or 'orthogonal' (right-angle lines). None follows the flowchart's own curve setting: step,
    stepBefore and stepAfter mean orthogonal; anything else, or no setting, means curved."""
    g = parse(source)
    t = theme or Theme()
    if routing is None:
        routing = "orthogonal" if (g.curve or "").lower().startswith("step") else "curved"
    d = Diagram(title or g.title or "Flowchart", theme=t, routing=routing)
    if not g.order:
        raise MermaidError("the flowchart has no nodes")
    across = g.direction in ("LR", "RL")
    flip = g.direction in ("RL", "BT")              # the mirror image of LR or TD: the first step is drawn last

    # Break cycles: an edge back to a node still on the depth-first path points backwards.
    out_edges: Dict[str, List[int]] = {n: [] for n in g.order}
    for idx, (a, b, _, _) in enumerate(g.edges):
        out_edges[a].append(idx)
    state: Dict[str, int] = {}
    back = set()
    for root in g.order:
        if root in state:
            continue
        state[root] = 1
        walk = [(root, iter(out_edges[root]))]
        while walk:
            node, it = walk[-1]
            nxt = next(it, None)
            if nxt is None:
                state[node] = 2
                walk.pop()
                continue
            child = g.edges[nxt][1]
            if state.get(child) == 1:
                back.add(nxt)
            elif child not in state:
                state[child] = 1
                walk.append((child, iter(out_edges[child])))
    forward = [i for i in range(len(g.edges)) if i not in back]

    # Layers by longest path along forward edges.
    layer = {n: 0 for n in g.order}
    for _ in range(len(g.order)):
        changed = False
        for i in forward:
            a, b = g.edges[i][0], g.edges[i][1]
            if layer[b] < layer[a] + 1:
                layer[b] = layer[a] + 1
                changed = True
        if not changed:
            break
    n_layers = max(layer.values()) + 1

    def slot(L: int) -> int:
        """Where step L is drawn, counted from the left (LR, RL) or the top (TD, BT)."""
        return n_layers - 1 - L if flip else L

    # Bands: each subgraph is one band; ungrouped nodes share one band. Bands keep first-appearance order.
    band_keys: List[Optional[int]] = []
    for node in g.order:
        key = g.group_of.get(node)
        if key not in band_keys:
            band_keys.append(key)

    # Order inside each (layer, band) cell by the average position of neighbours, a few sweeps each way.
    pos = {node: k for k, node in enumerate(g.order)}
    cells: Dict[Tuple[int, int], List[str]] = {}
    for node in g.order:
        cells.setdefault((layer[node], band_keys.index(g.group_of.get(node))), []).append(node)
    preds: Dict[str, List[str]] = {n: [] for n in g.order}
    succs: Dict[str, List[str]] = {n: [] for n in g.order}
    for i in forward:
        a, b = g.edges[i][0], g.edges[i][1]
        succs[a].append(b)
        preds[b].append(a)
    for sweep in range(6):
        neighbours = preds if sweep % 2 == 0 else succs
        layers = range(n_layers) if sweep % 2 == 0 else range(n_layers - 1, -1, -1)
        for L in layers:
            for bi in range(len(band_keys)):
                cell = cells.get((L, bi))
                if not cell:
                    continue
                cell.sort(key=lambda node: (sum(pos[x] for x in neighbours[node]) / len(neighbours[node])
                                            if neighbours[node] else pos[node], pos[node]))
                for k, node in enumerate(cell):
                    pos[node] = bi * 1000 + k

    # Measure, then place.
    tiles = {}
    for node in g.order:
        status = g.cls.get(node)
        tile = Tile(g.text[node], status=status if status in t.statuses and status != "live" else None)
        tile.name = node
        tiles[node] = tile
    size = {node: tiles[node].measure(t) for node in g.order}
    label_w = {}
    for i in forward:
        a, b, _, label = g.edges[i]
        if label and layer[b] == layer[a] + 1:
            w = text_width(label, t.small, "medium") + 14
            gap = min(slot(layer[a]), slot(layer[b]))
            label_w[gap] = max(label_w.get(gap, 0), w)
    grouped = [key is not None for key in band_keys]
    group_head = t.pad + 8 + t.line_h + 6
    if across:
        step_gap = [max(t.col_gap, label_w.get(L, 0) + 56) for L in range(n_layers)]
    else:
        starts = {min(slot(layer[n]) for n in g.order if g.group_of.get(n) == key)
                  for key in band_keys if key is not None}
        step_gap = [64.0 + (group_head + 24 if (L + 1) in starts else 0) for L in range(n_layers)]
    item_gap = 24.0 if across else 40.0

    if across:
        widths = [max(size[n][0] for n in g.order if slot(layer[n]) == L) for L in range(n_layers)]
        band_h = []
        for bi in range(len(band_keys)):
            hs = [sum(size[n][1] for n in cells.get((L, bi), [])) + item_gap * max(0, len(cells.get((L, bi), [])) - 1)
                  for L in range(n_layers)]
            band_h.append(max(hs))
        xs, x = [], 0.0
        for L in range(n_layers):
            xs.append(x)
            x += widths[L] + step_gap[L]
        placed = {}
        y = 0.0
        for bi in range(len(band_keys)):
            if bi:
                y += (2 * (t.pad + 8) + group_head + 8) if (grouped[bi] or grouped[bi - 1]) else t.row_gap + 16
            for L in range(n_layers):
                cell = cells.get((L, bi), [])
                stack_h = sum(size[n][1] for n in cell) + item_gap * max(0, len(cell) - 1)
                cy = y + (band_h[bi] - stack_h) / 2
                for node in cell:
                    placed[node] = d.add(tiles[node], xs[slot(L)], cy, width=widths[slot(L)], name=g.text[node])
                    cy += size[node][1] + item_gap
            y += band_h[bi]
    else:
        heights = [max(size[n][1] for n in g.order if slot(layer[n]) == L) for L in range(n_layers)]
        band_w = []
        for bi in range(len(band_keys)):
            ws = [sum(size[n][0] for n in cells.get((L, bi), [])) + item_gap * max(0, len(cells.get((L, bi), [])) - 1)
                  for L in range(n_layers)]
            band_w.append(max(ws))
        ys, y = [], 0.0
        for L in range(n_layers):
            ys.append(y)
            y += heights[L] + step_gap[L]
        placed = {}
        x = 0.0
        for bi in range(len(band_keys)):
            if bi:
                x += (2 * (t.pad + 8) + 16) if (grouped[bi] or grouped[bi - 1]) else t.col_gap
            for L in range(n_layers):
                cell = cells.get((L, bi), [])
                row_w = sum(size[n][0] for n in cell) + item_gap * max(0, len(cell) - 1)
                cx = x + (band_w[bi] - row_w) / 2
                for node in cell:
                    placed[node] = d.add(tiles[node], cx, ys[slot(L)] + (heights[slot(L)] - size[node][1]) / 2,
                                         name=g.text[node])
                    cx += size[node][0] + item_gap
            x += band_w[bi]

    for bi, key in enumerate(band_keys):
        if key is not None:
            members = [placed[n] for n in g.order if g.group_of.get(n) == key]
            d.group(g.groups[key][0], members)

    # Lines: neighbours in the next layer join directly; the rest travel in corridors outside the picture.
    margin = t.pad + 8 + (group_head if any(grouped) else 0)
    lo = min((p.y if across else p.x) for p in placed.values()) - margin
    hi = max((p.bottom if across else p.right) for p in placed.values()) + t.pad + 8
    mid = (lo + hi) / 2
    corridors = {"low": 0, "high": 0}
    if across:
        start, end = ("left", "right") if flip else ("right", "left")
    else:
        start, end = ("top", "bottom") if flip else ("bottom", "top")
    for i, (a, b, kind, label) in enumerate(g.edges):
        pa, pb = placed[a], placed[b]
        if i not in back and layer[b] == layer[a] + 1:
            d.link(pa, pb, kind=kind, label=label, start=start, end=end)
            continue
        centre = ((pa.cy + pb.cy) / 2) if across else ((pa.cx + pb.cx) / 2)
        side = "high" if centre > mid else "low"
        k = corridors[side]
        corridors[side] += 1
        at = (lo - 30 - 28 * k) if side == "low" else (hi + 30 + 28 * k)
        if across:
            d.link(pa, pb, kind=kind, label=label, start=start, end=end, via_y=at)
        else:
            d.link(pa, pb, kind=kind, label=label, start=start, end=end, via_x=at)
    return d


def load_mermaid(path: str, title: Optional[str] = None) -> Diagram:
    with open(path, encoding="utf-8") as fh:
        return from_mermaid(fh.read(), title=title)
