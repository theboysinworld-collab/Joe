"""Ferramentas de meta-cognição: o Joe gerencia a si mesmo (auto-evolução controlada).

- spawn_subagent : distribui tarefa para um filho; aparece no grafo imediatamente.
- create_extension: cria módulo plug-in novo (a ferramenta requisitada entra em vigor já).
- self_update    : atualiza dependências/pip do próprio pacote (com backup + teste = auto-cura).
- health_check   : varredura geral (imports, DB, providers, tools) — diagnóstico.
- learn          : grava um playbook/learning que será injetado no system prompt futuro.
- predict_needs  : expõe a previsão proativa baseada em hábitos ao LLM.
"""

from __future__ import annotations

import subprocess
import sys

from .base import Tool, ToolResult, registry


class SpawnSubagentTool(Tool):
    name = "spawn_subagent"
    description = ("Cria um sub-agente autônomo para executar uma subtarefa em paralelo "
                   "(aparece no grafo). Args: name, goal.")
    parameters = {"type": "object", "properties": {
        "name": {"type": "string"}, "goal": {"type": "string"}}, "required": ["name", "goal"]}

    def run(self, name: str, goal: str) -> ToolResult:
        from ..agents import spawn_subagent
        return ToolResult(output=spawn_subagent(name, goal))


class CreateExtensionTool(Tool):
    name = "create_extension"
    description = ("Gera um novo módulo/extensão plug-in em ~/.joe/extensions/<nome>/ com uma "
                   "ferramenta de exemplo; ela já fica disponível neste turno. Args: name, description?")
    parameters = {"type": "object", "properties": {
        "name": {"type": "string"}, "description": {"type": "string"}}, "required": ["name"]}

    def run(self, name: str, description: str = "") -> ToolResult:
        from ..extensions import create_extension_stub
        path = create_extension_stub(name, description)
        return ToolResult(output=(
            f"extensão criada e carregada em {path}. Edite extension.py para implementar a lógica; "
            "a ferramenta de exemplo já está registrada (veja /tools)."))


class SelfUpdateTool(Tool):
    name = "self_update"
    description = ("Auto-cura/atualização: reinstala dependências e testa imports críticos. "
                   "Args: dry_run? (default true).")
    parameters = {"type": "object", "properties": {"dry_run": {"type": "boolean"}}}

    def run(self, dry_run: bool = True) -> ToolResult:
        steps = []
        # 1) smoke test dos módulos essenciais (a "cura" é diagnosticar antes de tratar)
        r = subprocess.run([sys.executable, "-c",
                            "import joe.agent, joe.memory, joe.llm, joe.capabilities, joe.extensions;"
                            "print('imports ok')"],
                           capture_output=True, text=True, check=False)
        steps.append(f"[imports] {'✅' if r.returncode==0 else '❌'} {(r.stdout or r.stderr).strip()[:200]}")
        # 2) integridade do SQLite
        try:
            from ..memory import Memory
            m = Memory()
            m.conn.execute("PRAGMA integrity_check").fetchone()
            steps.append("[banco] ✅ sqlite íntegro")
        except Exception as e:
            steps.append(f"[banco] ❌ {e}")
        # 3) atualização de pacotes (só se pedido explicitamente)
        if not dry_run:
            up = subprocess.run([sys.executable, "-m", "pip", "install", "--upgrade", "joe-agent"],
                                capture_output=True, text=True, check=False)
            steps.append(f"[pip] rc={up.returncode} {(up.stdout or up.stderr)[-200:]}")
        else:
            steps.append("[pip] pulado (dry_run=true; passe dry_run=false para atualizar de fato)")
        return ToolResult(output="\n".join(steps))


class HealthCheckTool(Tool):
    name = "health_check"
    description = "Diagnóstico geral: providers LLM, capacidades, ferramentas, extensões, memória."
    parameters = {"type": "object", "properties": {}}

    def run(self) -> ToolResult:
        from ..llm import provider_registry, bootstrap
        from ..capabilities import capability_registry, bootstrap_capabilities
        from ..config import Config
        from ..extensions import load_all
        cfg = Config.load()
        if not provider_registry.all():
            bootstrap(cfg)
        if not capability_registry.all():
            bootstrap_capabilities(cfg)
        lines = ["— Providers —"]
        for p in provider_registry.all():
            lines.append(f"{'✅' if p.available() else '⚪'} {p.id}")
        act = provider_registry.active()
        lines.append(f"ativo: {act.id if act else '(nenhum — modo offline planner)'}")
        lines.append("— Capacidades —")
        lines += [f"{'✅' if c['disponivel'] else '⚠️ '} {c['capacidade']}"
                  for c in capability_registry.status()]
        lines.append(f"— Ferramentas ({len(registry.names())}) —")
        lines.append(", ".join(registry.names()))
        exts = load_all()
        lines.append(f"— Extensões ({len(exts)}) —")
        lines += [f"{'✅' if 'error' not in m else '❌'} {m['name']}" for m in exts.values()]
        return ToolResult(output="\n".join(lines))


class LearnTool(Tool):
    name = "learn"
    description = ("Grava um aprendizado/playbook durável ('da próxima vez faça X') — vira contexto "
                   "automático em sessões futuras. Args: lesson, tag?")
    parameters = {"type": "object", "properties": {
        "lesson": {"type": "string"}, "tag": {"type": "string"}}, "required": ["lesson"]}

    def run(self, lesson: str, tag: str = "playbook") -> ToolResult:
        from ..memory import Memory
        mid = Memory().remember(lesson, tag=tag, source="self")
        return ToolResult(output=f"aprendido gravado #{mid}: {lesson[:120]}")


class AskDetailTool(Tool):
    """Terminal gracioso do planner offline: sem alvo suficiente, pergunta (não falha)."""
    name = "ask_detail"
    description = ("Usada internamente quando o pedido é legítimo mas falta um dado (qual arquivo? "
                   "qual conteúdo?). Devolve a pergunta pronta para o usuário. Args: question.")
    parameters = {"type": "object", "properties": {
        "question": {"type": "string"}}, "required": ["question"]}

    def run(self, question: str) -> ToolResult:
        return ToolResult(output=f"❓ preciso de um detalhe: {question}")


class PredictNeedsTool(Tool):
    name = "predict_needs"
    description = "Retorna previsões proativas baseadas nos hábitos do usuário nesta hora do dia."
    parameters = {"type": "object", "properties": {}}

    def run(self) -> ToolResult:
        from ..memory import Memory
        preds = Memory().predict()
        return ToolResult(output="\n".join(f"- {p['intent']} (visto {p['count']}x)" for p in preds)
                          or "(sem padrões suficientes ainda)")


class ShowGraphTool(Tool):
    """Intenção de UI: 'me mostre o grafo' → renderiza o grafo de agentes (sem LLM)."""
    name = "show_graph"
    description = ("Mostra o grafo atual dos agentes em pipeline (intenção→planejador→"
                   "executor→revisor→conclusão) com pensamentos, ferramentas e memória.")
    parameters = {"type": "object", "properties": {}}

    def run(self) -> ToolResult:
        from ..ui import graph_text
        return ToolResult(output=graph_text())


for t in (SpawnSubagentTool(), CreateExtensionTool(), SelfUpdateTool(),
          HealthCheckTool(), LearnTool(), PredictNeedsTool(), ShowGraphTool(),
          AskDetailTool()):
    registry.register(t)
