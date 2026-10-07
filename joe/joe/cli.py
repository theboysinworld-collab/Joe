"""Interface de linha de comando do Joe.

Modos:
  joe                  chat agente (loop REPL com comandos slash)
  joe "pergunta"       turno único (para scripts)
  joe ide [arquivo]    mini-IDE no terminal
  joe ask "..."        alias de turno único
  joe config ...       ver/definir configuração
  joe memory ...       listar/buscar/esquecer fatos
"""

from __future__ import annotations

import argparse
import shlex
import sys

from . import __version__
from .agent import Agent
from .config import Config
from .ide import run_ide
from .memory import Memory
from .tools.base import registry

BANNER = f"""\
╔══════════════════════════════════════════════╗
║  Joe v{__version__} — IDE · assistente · agente     ║
║  /help para comandos · Ctrl+D ou /exit sai   ║
╚══════════════════════════════════════════════╝"""

HELP = """\
Comandos do REPL:
  <texto livre>          fala com o agente (LLM + ferramentas)
  /bash <cmd>            roda comando shell direto
  /read <arquivo>        mostra arquivo
  /write <arquivo>       lê linhas até linha vazia e salva
  /grep <padrão> [dir]   busca no projeto
  /ls [dir]              lista diretório
  /remember <fato>       salva na memória persistente
  /recall [busca]        consulta a memória
  /forget <palavra>      apaga fatos da memória
  /ide [arquivo]         abre o mini-IDE (:help dentro dele)
  /model [nome]          mostra/define o modelo LLM
  /tools                 lista ferramentas do agente
  /reset                 limpa a conversa
  /help                  esta ajuda
  /exit                  sai
"""


def _split(line: str) -> tuple[str, str]:
    parts = line.split(maxsplit=1)
    return parts[0].lower(), (parts[1] if len(parts) > 1 else "")


def handle_slash(agent: Agent, line: str) -> bool:
    """Retorna True se a linha foi consumida por um comando."""
    cmd, rest = _split(line)
    args = shlex.split(rest) if rest else []
    try:
        if cmd == "/exit" or cmd == "/quit":
            print("até logo! 👋")
            sys.exit(0)
        if cmd == "/help":
            print(HELP)
        elif cmd == "/tools":
            print("\n".join(f"- {t.name}: {t.description.split('.')[0]}" for t in registry.all()))
        elif cmd == "/bash":
            res = registry.execute("bash", {"command": rest})
            print(res.as_text())
        elif cmd == "/read":
            res = registry.execute("read_file", {"path": args[0] if args else ""})
            print(res.as_text())
        elif cmd == "/write":
            if not args:
                print("uso: /write <arquivo>  (depois digite o conteúdo; linha vazia termina)")
            else:
                print("(digite o conteúdo; linha vazia termina)")
                content = sys.stdin.read()
                res = registry.execute("write_file", {"path": args[0], "content": content})
                print(res.as_text())
        elif cmd == "/grep":
            res = registry.execute("grep", {"pattern": args[0], "path": args[1] if len(args) > 1 else "."})
            print(res.as_text())
        elif cmd == "/ls":
            res = registry.execute("list_dir", {"path": args[0] if args else "."})
            print(res.as_text())
        elif cmd == "/remember":
            tag = "geral"
            if rest.startswith("#"):
                tag, _, r2 = rest.partition(" ")
                tag = tag.lstrip("#")
                agent.memory.remember(r2 or rest, tag)
            else:
                agent.memory.remember(rest, tag)
            print(f"memorizado ✔ ({len(agent.memory.facts)} fatos no total)")
            agent.refresh_context()
        elif cmd == "/recall":
            facts = agent.memory.search(args[0]) if args else agent.memory.facts
            print("\n".join(f"- [{f['tag']}] {f['text']}" for f in facts) or "(memória vazia)")
        elif cmd == "/forget":
            n = agent.memory.forget(args[0] if args else "")
            print(f"{n} fato(s) removido(s)")
            agent.refresh_context()
        elif cmd == "/ide":
            run_ide(args[0] if args else None)
            agent.refresh_context()
        elif cmd == "/model":
            if args:
                agent.config.model = args[0]
                agent.config.save()
                print(f"modelo agora: {args[0]}")
            else:
                print(f"modelo atual: {agent.config.model} (base_url={agent.config.base_url}, key={'sim' if agent.config.api_key else 'NÃO'})")
        elif cmd == "/reset":
            agent.reset()
            print("conversa zerada.")
        else:
            print(f"comando desconhecido: {cmd} (use /help)")
    except SystemExit:
        raise
    except Exception as e:
        print(f"[erro no comando] {type(e).__name__}: {e}")
    return True


def repl(agent: Agent) -> None:
    print(BANNER)
    if not agent.config.api_key:
        print("⚠ sem OPENAI_API_KEY → modo offline (comandos slash funcionam normalmente).")
    while True:
        try:
            line = input("joe> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\naaté logo! 👋")
            break
        if not line:
            continue
        if line.startswith("/"):
            handle_slash(agent, line)
            continue
        reply = agent.chat(line, echo=lambda s: print(f"  {s}", file=sys.stderr))
        print(reply)


def one_shot(agent: Agent, text: str) -> None:
    if text.startswith("/"):
        handle_slash(agent, text)
    else:
        print(agent.chat(text, echo=lambda s: print(f"  {s}", file=sys.stderr)))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="joe",
        description="Joe — IDE, assistente pessoal e agente geral no terminal.",
    )
    parser.add_argument("--version", action="version", version=f"joe {__version__}")
    sub = parser.add_subparsers(dest="command")

    p_ask = sub.add_parser("ask", help="pergunta única ao agente")
    p_ask.add_argument("text", nargs="+")

    p_ide = sub.add_parser("ide", help="abre o mini-IDE")
    p_ide.add_argument("file", nargs="?")

    p_cfg = sub.add_parser("config", help="ver/definir configuração")
    p_cfg.add_argument("key", nargs="?")
    p_cfg.add_argument("value", nargs="?")

    p_mem = sub.add_parser("memory", help="gerenciar memória persistente")
    p_mem.add_argument("action", choices=["list", "add", "search", "forget"], nargs="?", default="list")
    p_mem.add_argument("text", nargs="*")

    parser.add_argument("rest", nargs="*", help="texto livre (turno único)")
    argv = list(argv) if argv is not None else sys.argv[1:]
    known_cmds = {"ask", "ide", "config", "memory"}
    # texto livre sem subcomando: joe "oi" / joe /bash ls  → injeta 'ask'
    if argv and not argv[0].startswith("-") and argv[0] not in known_cmds:
        ns = argparse.Namespace(command=None, rest=argv)
    else:
        ns = parser.parse_args(argv)

    agent = Agent()

    if ns.command == "ask":
        one_shot(agent, " ".join(ns.text))
    elif ns.command == "ide":
        run_ide(ns.file)
    elif ns.command == "config":
        cfg = agent.config
        if not ns.key:
            from dataclasses import asdict
            import json
            print(json.dumps({k: v for k, v in asdict(cfg).items()}, indent=2, ensure_ascii=False))
        else:
            val: object = ns.value
            cur = getattr(cfg, ns.key, None)
            if isinstance(cur, bool):
                val = str(val).lower() in ("1", "true", "yes", "s")
            elif isinstance(cur, int):
                val = int(val)
            elif isinstance(cur, float):
                val = float(val)
            setattr(cfg, ns.key, val)
            path = cfg.save()
            print(f"{ns.key} = {val}  (salvo em {path})")
    elif ns.command == "memory":
        mem = agent.memory
        txt = " ".join(ns.text)
        if ns.action == "list":
            print("\n".join(f"- [{f['tag']}] {f['text']}" for f in mem.facts) or "(vazia)")
        elif ns.action == "add":
            mem.remember(txt); print("ok")
        elif ns.action == "search":
            print("\n".join(f"- [{f['tag']}] {f['text']}" for f in mem.search(txt)) or "nada")
        elif ns.action == "forget":
            print(f"{mem.forget(txt)} removido(s)")
    elif ns.rest:
        one_shot(agent, " ".join(ns.rest))
    else:
        repl(agent)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
