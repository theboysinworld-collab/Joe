"""Interface do Joe — leve, minimalista, com o GRAFO de agentes ao centro.

Layout (rich):
┌ topo: status compacto (provider ativo, modelo, #tools, humor) ────────────┐
│            ░░ ANEL DE AGENTES (nós em círculo, 35% opacidade) ░░           │
│         usuário → intenção → planejador ⇄ executor → revisor → conclusão   │
│      sub-agentes nascem como novos nós; arestas = handoffs reais do bus    │
│  log abaixo: pensamentos/decisões/ferramentas/memória de cada agente       │
└ prompt essencial: "joe ❯" ─────────────────────────────────────────────────┘

A UI NÃO conhece o agente: ela só assina o EventBus e desenha o replay.
`render_graph()` é pura (string) → testável sem terminal.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from .bus import bus

PIPELINE = ["usuário", "intenção", "planejador", "executor", "revisor", "conclusão"]


@dataclass
class GraphState:
    nodes: dict[str, dict] = field(default_factory=dict)     # id -> {label, last_note, ts}
    edges: list[tuple[str, str]] = field(default_factory=list)

    def touch(self, nid: str, note: str = "") -> None:
        self.nodes[nid] = {"label": nid, "note": note[:60], "ts": time.time()}


STATE = GraphState()
for n in PIPELINE:
    STATE.touch(n)


# ---------------------------------------------------------------- UI grafo (nós = papéis do pipeline)
def _node_id(role: str, agent_name: str) -> str:
    """Nó canônico por papel do pipeline; sub-agentes ganham nó próprio."""
    return _ROLE_MAP.get(role, agent_name.lower())


def _listener(ev: dict) -> None:
    t, p = ev["topic"], ev
    if t == "agent.step":
        role = p.get("role", "?")
        nid = _node_id(role, p.get("name", "Joe"))
        STATE.touch(nid, f"{role}: {p.get('intent') or p.get('verdict') or p.get('mode') or ''}")
        # aresta real do pipeline: papel anterior → papel atual (nunca crasha)
        seq = [_ROLE_MAP.get(r, r) for r in _PIPELINE_ROLES]
        cur = _ROLE_MAP.get(role, role)
        if cur in seq and seq.index(cur) > 0:
            STATE.edges.append((seq[seq.index(cur) - 1], cur))
    elif t == "agent.thought":
        STATE.touch(p.get("agent", "joe"), f"💭 {p['thought'][:50]}")
    elif t == "agent.tool":
        STATE.touch(p.get("agent", "joe"), f"⚙ {p['tool']}")
    elif t == "agent.memory":
        STATE.touch(p.get("agent", "joe"), f"🧠 {p['saved'][:40]}")
    elif t == "agent.born":
        STATE.touch(p["name"].lower(), f"sub-agente: {p['goal'][:40]}")
    elif t == "agent.handoff":
        STATE.edges.append((p["frm"], p["to"]))
    elif t == "ext.tool_added":
        STATE.touch("extensões", f"+ {p['tool']}")


_ROLE_MAP = {"intencao": "intenção", "planejador": "planejador", "executor": "executor",
             "revisor": "revisor", "conclusao": "conclusão"}
# ordem canônica dos papéis emitidos pelo Agent._step (sem acentos)
_PIPELINE_ROLES = ("intencao", "planejador", "executor", "revisor", "conclusao")
bus.subscribe("agent", _listener)
bus.subscribe("ext", _listener)


def render_graph(width: int = 72, opacity_pct: int = 35) -> Text:
    """Desenha os nós num anel central translúcido (vibe n8n) + arestas pontilhadas."""
    ids = list(STATE.nodes)
    canvas = [[" "] * width for _ in range(9)]
    cx, cy = width // 2, 4
    rx, ry = min(width // 2 - 8, 26), 3

    def put(x: int, y: int, s: str):
        x = max(1, min(width - len(s) - 1, x))
        y = max(0, min(8, y))
        canvas[y][x:x + len(s)] = list(s)

    positions = {}
    n = max(len(ids), 1)
    # rótulo curto do nó (evita sobreposição em anéis apertados)
    labels = {nid: (nid[:10] + "…" if len(nid) > 10 else nid) for nid in ids}
    for i, nid in enumerate(ids):
        ang = 2 * math.pi * i / n - math.pi / 2
        x = int(cx + rx * math.cos(ang))
        y = int(cy + ry * round(math.sin(ang), 2) * 2)
        positions[nid] = (x, y)
    # arestas: linha pontilhada entre nós conectados (últimas 12, sem duplicatas)
    seen_edges = set()
    for a, b in STATE.edges[-12:]:
        if (a, b) in seen_edges or a not in positions or b not in positions:
            continue
        seen_edges.add((a, b))
        (x1, y1), (x2, y2) = positions[a], positions[b]
        steps = max(abs(x2 - x1), abs(y2 - y1), 1)
        for k in range(1, steps):
            px = max(0, min(width - 1, int(x1 + (x2 - x1) * k / steps)))
            py = max(0, min(len(canvas) - 1, int(y1 + (y2 - y1) * k / steps)))
            if canvas[py][px] == " ":
                canvas[py][px] = "·"
    for nid, (x, y) in positions.items():
        put(x - len(labels[nid]) // 2, y, f"⟨{labels[nid]}⟩")
    out = Text()
    alpha = 0.25 + 0.75 * (opacity_pct / 100)
    dim = alpha < 0.8
    for row in canvas:
        line = "".join(row).rstrip()
        style = "dim italic" if dim else ""
        out.append(line + "\n", style=style)
    legend = ", ".join(f"{nid}[{STATE.nodes[nid]['note'][:28]}]" if STATE.nodes[nid]["note"] else nid
                       for nid in ids[-8:])
    out.append(f"\n  · rede: {legend}", style="cyan dim")
    return out


def graph_text(width: int = 72) -> str:
    """Versão texto puro do grafo (para ferramentas/CLI sem rich)."""
    return render_graph(width).plain


def graph_panel() -> Panel:
    return Panel(render_graph(), title="☉ agentes — pensamento · criação · decisão · memória",
                 border_style="cyan", padding=(0, 1))


class UI:
    def __init__(self) -> None:
        self.console = Console()

    def header(self, provider: str, model: str, tools: int, mode: str) -> None:
        # NOTE: kwargs de layout pertencem ao Panel, não a Console.print()
        self.console.print(Panel(
            Text.assemble(("joe ", "bold green"), (f"· {provider or 'offline-planner'}", "cyan"),
                          (f" · {model}" if model else "", ""), (f" · {tools} ferramentas", "dim"),
                          (f" · {mode}", "magenta")),
            border_style="blue", padding=(0, 1)))

    def graph(self) -> None:
        self.console.print(graph_panel())

    def tool_line(self, s: str) -> None:
        self.console.print(Text(s, style="dim"))

    def answer(self, s: str) -> None:
        self.console.print(Panel(s, border_style="green", title="✔ conclusão"))

    def error(self, s: str) -> None:
        self.console.print(Text(f"✗ {s}", style="red"))

    def plain(self, s: str) -> None:
        self.console.print(s)
