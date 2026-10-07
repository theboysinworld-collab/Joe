"""Orquestração multi-agente do Joe — pipeline fixo + sub-agentes dinâmicos.

PAPÉIS (AgentRole) padrão do fluxo, sempre visíveis no grafo da interface:
  usuário → INTENÇÃO (classifica objetivo sem palavras-chave rígidas)
          → PLANEJADOR (decompõe em passos; escolhe ferramentas/sub-agentes)
          → EXECUTOR (roda o loop LLM↔ferramentas de verdade)
          → REVISOR (checa resultado vs. plano; pode mandar refazer 1x)
          → CONCLUSÃO (síntese final + memorização do que foi aprendido)

Cada etapa EMITE eventos no bus (agent.step / agent.thought / agent.tool /
agent.memory / agent.handoff). A UI central desenha nós e arestas a partir deles.

PLANNER OFFLINE (requisito: base eficiente sem LLM/API):
  `deterministic_plan(texto)` mapeia intenção → sequência concreta de tool-calls
  usando score semântico (mesma métrica da memória/RAG). Sem rede, sem modelo:
  "cria um site tal" → write_file + bash(python -m http.server...), etc.

AUTO-EVOLUÇÃO:
  `spawn_subagent(name, goal)` cria um agente-filho com papel registrado em runtime
  (nó novo aparece no grafo imediatamente) — é assim que o Joe distribui tarefas.

Sub-agentes herdam o registry de tools mas têm orçamento próprio de iterações.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field

from .bus import bus


# ---------------------------------------------------------------- papéis
@dataclass
class AgentRole:
    role_name: str
    description: str = ""
    system_prompt: str = ""


class RoleRegistry:
    def __init__(self) -> None:
        self.roles: dict[str, AgentRole] = {}

    def register(self, role: AgentRole) -> None:
        self.roles[role.role_name] = role

    def get(self, name: str) -> AgentRole | None:
        return self.roles.get(name)


role_registry = RoleRegistry()

for _r in (
    AgentRole("intento", "Classifica a intenção do usuário", "Você classifica intenções."),
    AgentRole("planejador", "Decompõe a tarefa em passos", "Você planeja passo a passo."),
    AgentRole("executor", "Executa ferramentas", "Você executa com ferramentas."),
    AgentRole("revisor", "Revisa o resultado contra o plano", "Você revisa criticamente."),
    AgentRole("conclusao", "Sintetiza e memoriza", "Você conclui e aprende."),
):
    role_registry.register(_r)


# ---------------------------------------------------------------- classificação de intenção (robusta, sem palavra-chave exata)
INTENT_PROFILES: dict[str, list[str]] = {
    "criar_arquivo": ["cria", "criar", "crie", "novo arquivo", "nova", "gera", "gerar", "gere",
                      "escreve", "escrever", "salva", "salvar", "implementa", "scaffold", "monta",
                      "site", "portfolio", "app ", "pro jeto"],
    "editar_codigo": ["corrige", "corrigir", "conserta", "refator", "ajusta", "edita", "bug",
                      "melhora o codigo", "muda a funcao", "troca o nome", "funcao", "codigo",
                      "programa", "script"],
    "ler_arquivo": ["leia", "ler", "abre o arquivo", "abrir arquivo", "mostra o arquivo",
                    "conteudo", "o que tem em", "me mostre o", "arquivo", "codigo fonte",
                    "me mostra", "mostra esse"],
    "executar": ["rode", "rodar", "executa", "executar", "roda", "comando", "testa", "testar",
                 "build", "compila", "instala", "os testes", "terminal", "shell"],
    "buscar_web": ["internet", "pesquisa", "noticias", "busca online", "google", "duckduckgo",
                   "o que aconteceu hoje", "cotação do dolar", "na web", "online", "hoje"],
    "memoria": ["lembre", "memoriza", "memorize", "nunca esqueca", "nunca esqueça", "anota isso",
                "guarde isso", "decora", "anote", "esqueca", "lembrar"],
    "planejar": ["planeja", "planejar", "plano", "organiza", "roteiro", "estrategia",
                 "como faco", "preciso fazer", "semana de estudos", "roadmap", "planejamento"],
    "visual": ["tela", "screenshot", "camera", "foto", "veja isto", "desenhe", "gere uma imagem",
               "capture", "olha pela camera", "imagem", "veja", "ve o que esta", "ve o que",
               "mostra a tela"],
    "celular": ["celular", "android", "termux", "notificacao", "sms", "whatsapp", "abrir app",
                "abre o app", "liga para", "instagram", "telegram", "aplicativo", "app"],
    "conversar": ["oi", "ola", "bom dia", "boa noite", "quem e voce", "obrigado",
                  "valeu", "legal", "e ai", "tudo bem"],
}

# sinônimos flexionais: radicais que casam por prefixo (ex.: "conserte"→"consert")
_VERB_STEMS = {
    "ler_arquivo": ["leia", "le ", "ler", "lê", "mostre", "mostra", "abri", "mostr"],
    "executar": ["rode", "rod", "execut", "test", "compil", "instal", "build", "run"],
    "criar_arquivo": ["cri", "ger", "escrev", "salv", "implement", "mont", "nov"],
    "editar_codigo": ["corrig", "consert", "refat", "ajust", "edit", "melhor", "troca", "mud"],
    "buscar_web": ["pesquis", "busqu", "procur", "notici"],
    "memoria": ["lembre", "lembr", "memoriz", "esquec", "anot", "guard", "decor"],
    "planejar": ["planej", "plan", "organiz", "roteir", "estrateg", "roadmap"],
    "visual": ["ve ", "vejo", "vê", "vi ", "olh", "captur", "screenshot", "film"],
    "celular": ["ligar", "chama", "mand"],
}


def _fold(s: str) -> str:
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFD", s.lower()) if unicodedata.category(c) != "Mn")


def classify_intent(text: str) -> tuple[str, float]:
    """Score semântico-por-perfil (perfis + radicos verbais): não exige frase exata."""
    t = _fold(text) + " "
    best, best_score = "conversar", 0.0
    for intent, keys in INTENT_PROFILES.items():
        score, matched = 0.0, 0
        for k in keys:
            kk = _fold(k)
            if kk.strip() and kk in t:
                score += min(len(kk), 14) / 5.0     # pistas longas pesam mais
                matched += 1
        for stem in _VERB_STEMS.get(intent, []):
            if _fold(stem) in t:
                score += 2.0
                matched += 1
                break                                # radical conta 1x por intenção
        score += 0.8 * max(0, matched - 1)           # múltiplas pistas corroboram
        if score > best_score:
            best, best_score = intent, score
    if best_score < 2.6:
        best = "conversar"
    return best, round(best_score, 2)


# ---------------------------------------------------------------- planner determinístico offline
def deterministic_plan(text: str) -> list[dict]:
    """Passo a passo executável SEM LLM — a base age sozinha quando a API falta."""
    intent, _ = classify_intent(text)
    plans: dict[str, list[dict]] = {
        "ler_arquivo": lambda: [{"name": "read_file", "args": {"path": _first_path(text)}}],
        "executar": lambda: [{"name": "bash", "args": {"command": _first_cmd(text)}}],
        "memoria": lambda: [{"name": "remember", "args": {"text": text, "tag": "usuario"}}],
        "buscar_web": lambda: [{"name": "search_web", "args": {"query": text}}],
        "visual": lambda: [{"name": "see_screen", "args": {}}],
        "celular": lambda: [{"name": "phone", "args": {"action": "status"}}],
        "criar_arquivo": lambda: [],   # precisa de conteúdo → LLM ou pedido de detalhe
        "editar_codigo": lambda: [],
        "planejar": lambda: [],
        "conversar": lambda: [],
    }
    return plans.get(intent, lambda: [])()


def _first_path(text: str) -> str:
    m = re.search(r"[\w./-]+\.[\w]{1,8}", text)
    return m.group(0) if m else "."


def _first_cmd(text: str) -> str:
    m = re.search(r"(?:rode|execute|run|rodar)\s+[`'\"]?([^`'\"\n]+)", text, re.I)
    return m.group(1).strip() if m else "pwd && ls"


# ---------------------------------------------------------------- sub-agentes
@dataclass
class SubAgent:
    name: str
    goal: str
    parent: str = "joe"
    id: str = field(default_factory=lambda: f"a{int(time.time()*1000)%100000}")

    def announce(self) -> None:
        bus.emit("agent.born", id=self.id, name=self.name, goal=self.goal, parent=self.parent)
        bus.emit("agent.handoff", frm=self.parent, to=self.id, task=self.goal)


def spawn_subagent(name: str, goal: str, agent_factory=None, parent: str = "joe") -> str:
    """Cria um agente-filho em runtime e o executa; resultado volta ao pai via bus."""
    sa = SubAgent(name=name, goal=goal, parent=parent)
    sa.announce()
    factory = agent_factory or _default_factory(goal, sa)
    try:
        result = factory()
    except Exception as e:
        result = f"[sub-agente {name} falhou] {type(e).__name__}: {e}"
    bus.emit("agent.result", id=sa.id, name=name, result=str(result)[:400])
    bus.emit("agent.handoff", frm=sa.id, to=parent, task=f"resultado de {name}")
    return str(result)


def _default_factory(goal: str, sa: SubAgent):
    def run():
        from .agent import Agent  # late import
        child = Agent(subagent=sa)
        return child.chat(goal)
    return run
