"""CLI do Joe — REPL com grafo + subcomandos. Essencial por padrão, tudo a um `/`.

Comandos (interface enxuta; /help lista):
  /graph /tools /ext /health /bench /model <id> /provider <nome> /config openrouter <chave>
  /rag <busca> /remember <fato> /forget <trecho> /predict /analyze /heal /evolve
  /speak <txt> /see [tela|camera] /phone <ação> /web <busca> /image <prompt>
  /sub <nome> <tarefa>   → spawna sub-agente e mostra no grafo
  /newext <nome>         → cria extensão plug-in ao vivo
  /ide [arquivo] /reset /exit
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .agent import Agent
from .bus import bus
from .config import Config
from .agents import spawn_subagent
from .llm import provider_registry, bootstrap, auto_select, benchmark, apply_benchmark_choice, LLMError
from .memory import Memory
from .tools.base import registry
from .ui import UI


def _boot(config: Config) -> None:
    """Inicialização padrão na ordem da arquitetura (nunca pule etapas)."""
    from .tools.base import bootstrap_tools   # 1. ferramentas centrais
    bootstrap_tools(config)
    from .extensions import load_all          # 2. extensões registram tools/providers extras
    load_all()
    bootstrap(config)                          # 3. providers base
    auto_select(config)                        # 4. seleção de UM provedor ativo
    from .capabilities import bootstrap_capabilities
    bootstrap_capabilities(config)             # 5. sentidos/ações


def cmd_ask(args) -> int:
    cfg = Config.load()
    _boot(cfg)
    agent = Agent(cfg, Memory(config=cfg))
    print(agent.chat(" ".join(args.text)))
    return 0


def cmd_ide(args) -> int:
    from .ide.editor import run_ide
    cfg = Config.load()
    _boot(cfg)
    return run_ide(args.file)


def cmd_config(args) -> int:
    cfg = Config.load()
    if args.key in ("openrouter", "openrouter_api_key"):
        cfg.openrouter_api_key = args.value
    elif args.key == "model":
        cfg.model = args.value
    elif args.key == "provider":
        pass
    else:
        cfg.extra[args.key] = args.value
    cfg.save()
    print(f"config salvo: {args.key}")
    return 0


def cmd_memory(args) -> int:
    m = Memory()
    if args.op == "add":
        mid = m.remember(" ".join(args.text), tag="geral")
        print(f"fato #{mid} memorizado (SQLite+vetor)")
    elif args.op == "list":
        for f in m.facts():
            print(f"[{f['tag']}] {f['text']}")
    elif args.op == "search":
        for h in m.search(" ".join(args.text)):
            print(f"{h['score']:.2f} [{h['kind']}] {h['text'][:160]}")
    return 0


def cmd_bench(args) -> int:
    cfg = Config.load()
    _boot(cfg)
    res = benchmark(cfg)
    if not res:
        print("nenhum modelo encontrado para testar (sem ollama local e sem chaves).")
        return 1
    for r in res:
        mark = "✅" if r["ok"] else "❌"
        print(f"{mark} {r['provider']:>10} · {r['model']:<38} {r['latency_s']:>6}s  score {r['score']} {r['error']}")
    choice = apply_benchmark_choice(cfg, res)
    if choice:
        print(f"\n→ escolhido automaticamente (um modelo por vez): {choice}")
    return 0


def cmd_health(args) -> int:
    cfg = Config.load()
    _boot(cfg)
    r = registry.execute("health_check", {})
    print(r.output)
    return 0


# ---------------------------------------------------------------- REPL
HELP = """\
/joe ❯ essencial: fale naturalmente (eu entendo a intenção).
/comandos: /graph /tools /ext /health /bench /provider <n> /model <m>
/config openrouter <chave> | /rag <busca> | /remember <fato> | /forget <x>
/predict /analyze /heal /evolve | /speak /see tela|camera /phone <ação> /web <busca>
/image <prompt> | /sub <nome> <tarefa> | /newext <nome> | /ide [arq] | /reset | /exit"""


def repl() -> int:
    cfg = Config.load()
    _boot(cfg)
    mem = Memory(config=cfg)
    # coração proativo: sensores + tarefas periódicas em daemon (nunca bloqueia o REPL)
    from .scheduler import start_background
    start_background(memory=mem)
    agent = Agent(cfg, mem)
    ui = UI()
    prov = provider_registry.active()
    mode = "agente-LLM" if prov else "offline-planner (base autônoma sem API)"
    ui.header(prov.id if prov else "", cfg.model, len(registry.names()), mode)
    ui.graph()
    print(HELP)

    while True:
        try:
            line = input("\njoe ❯ ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not line:
            continue
        if line.startswith("/"):
            if not _slash(line, agent, cfg, ui, mem):
                break
            continue
        # linha natural: passa pelo PIPELINE DE AGENTES (intenção→planejador→executor→revisor→conclusão)
        reply = agent.chat(line, echo=ui.tool_line)
        ui.answer(reply)
        ui.graph()  # o grafo se atualiza com o que aconteceu de verdade
    print("até logo 👋")
    return 0


def _slash(line: str, agent: Agent, cfg: Config, ui: UI, mem: Memory) -> bool:
    parts = line.split(maxsplit=1)
    cmd, rest = parts[0].lower(), (parts[1] if len(parts) > 1 else "")
    if cmd in ("/exit", "/quit"):
        return False
    if cmd in ("/help", "/comandos"):
        print(HELP)
    elif cmd == "/graph" and rest.strip().lower() in ("live", "ao vivo", "vivo"):
        # loop ao vivo: redesenha o grafo continuamente até o usuário teclar q
        from .ui import live_graph
        ui.plain("grafo ao vivo — tecle q para sair (enter apenas repinta)")
        frames = live_graph(console=ui.console)
        ui.plain(f"(loop encerrado após {frames} frames)")
    elif cmd == "/graph":
        ui.graph()
    elif cmd == "/tools":
        print(", ".join(registry.names()))
    elif cmd == "/ext":
        from .extensions import loaded
        for name, meta in loaded().items():
            err = f"  ❌ {meta['error']}" if "error" in meta else ""
            print(f"• {name} v{meta.get('version','?')} — {meta.get('description','')}{err}")
        if not loaded():
            print("(nenhuma extensão; crie com /newext <nome>)")
    elif cmd == "/health":
        print(registry.execute("health_check", {}).output)
    elif cmd == "/bench":
        res = benchmark(cfg)
        for r in res[:10]:
            print(f"{'✅' if r['ok'] else '❌'} {r['provider']}:{r['model']} {r['latency_s']}s")
        choice = apply_benchmark_choice(cfg, res)
        print(f"→ ativo agora: {choice or 'nenhum disponível'}")
    elif cmd == "/provider":
        try:
            provider_registry.select(rest)
            cfg.save()
            print(f"provedor ativo: {rest} (troca limpa, um por vez)")
        except LLMError as e:
            print(e)
    elif cmd == "/model":
        cfg.model = rest
        cfg.save()
        print(f"modelo preferido: {rest}")
    elif cmd == "/config":
        k, _, v = rest.partition(" ")
        if k in ("openrouter", "openrouter_api_key"):
            cfg.openrouter_api_key = v.strip()
            cfg.save()
            # re-registra providers com a nova chave (registry aceita sobrescrita por id)
            provider_registry.build_default(cfg)
            auto_select(cfg)
            print("chave OpenRouter salva; provedores disponíveis:",
                  [p.id for p in provider_registry.available()])
        else:
            setattr(cfg, k, v)
            cfg.save()
            print(f"config {k} atual")
    elif cmd == "/rag":
        for h in mem.search(rest, k=6):
            print(f"{h['score']:.2f} [{h['kind']}:{h['tag']}] {h['text'][:200]}")
    elif cmd == "/remember":
        print(f"fato #{mem.remember(rest)} salvo")
    elif cmd == "/forget":
        print(f"{mem.forget(rest)} removidos")
    elif cmd == "/predict":
        preds = mem.predict()
        print("\n".join(f"- {p['intent']} ({p['count']}x nesta hora)" for p in preds)
              or "(ainda sem padrões suficientes)")
    elif cmd == "/suggest":
        # dispara imediatamente o ciclo de proatividade do scheduler (sem esperar o tick)
        from .scheduler import scheduler
        from .ui import STATE
        n = len(STATE.suggestions)
        scheduler._maybe_suggest()          # emite "suggest" no bus → UI/grafo atualizam
        novo = STATE.suggestions[n:]
        print("✦ " + (", ".join(novo) if novo else "nenhuma sugestão nova agora"))
    elif cmd == "/analyze":
        from .auto import SelfAnalysis
        sa = SelfAnalysis(cfg, mem)
        rep = sa.analyze()
        print(f"eventos: {rep['events_seen']} | problemas: {rep['problems'] or 'nenhum'}")
        saved = sa.distill()
        print(f"playbooks gerados: {len(saved)}")
    elif cmd == "/heal":
        from .auto import SelfAnalysis
        for a in SelfAnalysis(cfg, mem).heal():
            print("•", a)
    elif cmd == "/evolve":
        from .auto import SelfAnalysis
        print(SelfAnalysis(cfg, mem).evolve_report())
    elif cmd == "/sub":
        # Formas aceitas:
        #   /sub <nome> para <tarefa>  → nome explícito (2ª palavra 'para/pra')
        #   /sub <nome> <tarefa>       → posicional, ex.: "pesquisador resumir logs"
        #                                (usa o MESMO extrator do planner natural)
        #   /sub <tarefa>              → só tarefa (1ª palavra é verbo) → 'filho'
        from .agents import _sub_name, _sub_goal
        w = rest.split(maxsplit=2)
        if len(w) >= 3 and w[1].lower() in ("para", "pra"):
            name, goal = w[0], " ".join(w[2:])
        else:
            name = _sub_name(rest)
            goal = rest[len(name):].strip() if rest.strip().startswith(name) and name != "filho" else _sub_goal(rest)
            goal = goal or rest or "explorar o workspace"
        if not goal.strip():
            goal = rest or "explorar o workspace"
        out = spawn_subagent(name, goal)
        print(out if (out or "").strip() else f"[{name}] concluiu sem saída textual.")
        ui.graph()   # nó do filho já aparece no grafo
    elif cmd == "/newext":
        from .extensions import create_extension_stub
        p = create_extension_stub(rest or "minha_extensao")
        print(f"extensão criada/carregada em {p} — veja /tools e /ext")
    elif cmd in ("/speak", "/see", "/phone", "/web", "/image"):
        mapping = {"/speak": ("speak", {"text": rest}),
                   "/see": ("see_screen" if "camera" not in rest else "camera_view", {}),
                   "/phone": ("phone", {"action": rest or "status"}),
                   "/web": ("search_web", {"query": rest}),
                   "/image": ("make_image", {"prompt": rest})}
        tool, args = mapping[cmd]
        print(registry.execute(tool, args).as_text())
    elif cmd == "/ide":
        from .ide.editor import run_ide
        run_ide(rest or None)
    elif cmd == "/reset":
        agent.reset()
        print("contexto limpo (memória persiste).")
    else:
        print("comando desconhecido — /help")
    return True


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    # compat: `joe "texto"` vira `joe ask texto`
    if argv and not argv[0].startswith("-") and argv[0] not in (
            "ask", "ide", "config", "memory", "bench", "health"):
        argv = ["ask", *argv]
    p = argparse.ArgumentParser(prog="joe", description="Joe — IDE, assistente pessoal e agente geral.")
    sub = p.add_subparsers(dest="cmd")
    a = sub.add_parser("ask"); a.add_argument("text", nargs="+")
    i = sub.add_parser("ide"); i.add_argument("file", nargs="?")
    c = sub.add_parser("config"); c.add_argument("key"); c.add_argument("value")
    m = sub.add_parser("memory"); m.add_argument("op", choices=["add", "list", "search"]); m.add_argument("text", nargs="*")
    sub.add_parser("bench")
    sub.add_parser("health")
    sub.add_parser("repl")
    args = p.parse_args(argv)
    if args.cmd == "ask":
        return cmd_ask(args)
    if args.cmd == "ide":
        return cmd_ide(args)
    if args.cmd == "config":
        return cmd_config(args)
    if args.cmd == "memory":
        return cmd_memory(args)
    if args.cmd == "bench":
        return cmd_bench(args)
    if args.cmd == "health":
        return cmd_health(args)
    return repl()


if __name__ == "__main__":
    raise SystemExit(main())
