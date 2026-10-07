#!/usr/bin/env python3
"""run.py — ponto de entrada único do Joe.

Inicia o sistema inteiro sem precisar ativar partes separadamente:
config → ferramentas → extensões → providers LLM (ollama/OpenRouter/OpenAI)
→ seleção automática de UM modelo ativo → capacidades sensoriais → REPL com grafo.

Uso:
    python3 run.py                 # inicia tudo e abre a interface (REPL + grafo)
    python3 run.py --once "texto"  # um turno e sai (bom para scripts)
    python3 run.py --ide [arquivo] # abre o IDE após boot completo
    python3 run.py --bench         # benchmarka modelos locais/remotos e escolhe o melhor
    python3 run.py --health        # auto-análise de saúde dos módulos/capacidades
    python3 run.py --check         # smoke test do boot completo (sem abrir REPL)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# garante que o pacote local seja encontrado mesmo sem `pip install -e .`
sys.path.insert(0, str(Path(__file__).resolve().parent))


def full_boot():
    """Inicializa TODAS as camadas na ordem da arquitetura. Retorna (cfg, mem)."""
    from joe.agent import Agent
    from joe.config import Config
    from joe.llm import bootstrap, auto_select
    from joe.memory import Memory
    from joe.tools.base import bootstrap_tools
    from joe.extensions import load_all
    from joe.capabilities import bootstrap_capabilities

    cfg = Config.load()
    bootstrap_tools(cfg)            # 1. ferramentas centrais (shell, arquivos, memória…)
    load_all()                      # 2. extensões drop-in (~/.joe/extensions)
    bootstrap(cfg)                  # 3. providers LLM (ollama local, OpenRouter, OpenAI)
    auto_select(cfg)                # 4. escolhe UM modelo ativo por vez
    bootstrap_capabilities(cfg)     # 5. sentidos/ações (voz, tela, câmera, celular, web…)
    mem = Memory(config=cfg)        # 6. memória SQLite + RAG vetorial
    return cfg, mem


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="run.py", description="Joe — inicialização completa do sistema.")
    p.add_argument("--once", metavar="TEXTO", help="envia um único pedido e encerra")
    p.add_argument("--ide", nargs="?", const=None, metavar="ARQUIVO", help="abre o IDE após o boot")
    p.add_argument("--bench", action="store_true", help="benchmark de modelos e escolha automática")
    p.add_argument("--health", action="store_true", help="auto-análise / diagnóstico geral")
    p.add_argument("--check", action="store_true", help="valida o boot completo e encerra")
    args = p.parse_args(argv)

    cfg, mem = full_boot()

    if args.check:
        from joe.tools.base import registry
        from joe.llm import provider_registry
        prov = provider_registry.active()
        print(f"[check] boot OK · {len(registry.names())} ferramentas · "
              f"provedor ativo: {prov.id if prov else 'offline-planner'} · "
              f"memória: {len(mem.facts())} fatos")
        return 0

    if args.health:
        from joe.cli import cmd_health
        return cmd_health(args)

    if args.bench:
        from joe.cli import cmd_bench
        return cmd_bench(args)

    if args.once:
        from joe.agent import Agent
        agent = Agent(cfg, mem)
        print(agent.chat(args.once))
        return 0

    if args.ide is not None:
        from joe.ide.editor import run_ide
        run_ide(args.ide)
        return 0

    # padrão: sistema inteiro ligado + interface central
    from joe.cli import repl
    return repl()


if __name__ == "__main__":
    raise SystemExit(main())
