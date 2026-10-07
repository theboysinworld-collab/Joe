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
                      "site", "portfolio", "app ", "pro jeto",
                      # variantes e sinônimos de escrita (gap "escreva X em Y.txt")
                      "escreva", "escrev", "coloque", "coloca", "bota", "poe", "adiciona",
                      "adicione", "grava", "gravar", "salve", "redija", "produza",
                      "no arquivo", "num arquivo", "em um arquivo", "com o texto",
                      "com o conteudo", "conteudo do tipo", "nesse arquivo"],
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
    "listar": ["liste", "listar", "lista de", "lista os", "quais arquivos", "quais sao os arquivos",
               "o que tem na pasta", "conteudo da pasta", "diretorios", "arquivos do"],
    "mostrar_grafo": ["grafo", "agentes", "quem esta trabalhando", "pipeline",
                      "mapa dos agentes", "visualizacao dos agentes"],
    "sub_agente": ["sub agente", "subagente", "sub-agente", "spawn", "delegue",
                   "delegate", "contrate um agente", "crie um agente", "novo agente",
                   "novo sub", "use um agente para", "chame um agente"],
}

# palavras que NUNCA devem ser tratadas como caminho de arquivo pelo planner
_NON_PATH_WORDS = {"grafo", "agentes", "sistema", "tudo", "agora", "hoje", "ai", "voce",
                   "memoria", "historico", "pipeline", "extensoes", "ferramentas"}

# sinônimos flexionais: radicais que casam por prefixo (ex.: "conserte"→"consert")
_VERB_STEMS = {
    "ler_arquivo": ["leia", "le ", "ler", "lê", "abri"],
    "listar": ["liste", "list"],
    "executar": ["rode", "rod", "execut", "test", "compil", "instal", "build", "run"],
    "criar_arquivo": ["cri", "ger", "escrev", "salv", "implement", "mont", "nov",
                      "coloque", "coloc", "bot", "grav", "redij", "adicion"],
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


# frases de UI/meta (não são leitura de arquivo nem conversa vazia)
_META_PATTERNS = [
    ("mostrar_grafo", ["grafo", "os agentes", "quem esta trabalhando", "pipeline"]),
    ("sub_agente",  ["sub agente", "subagente", "sub-agente", "delegue", "delegate",
                     "crie um agente", "novo agente", "chame um agente", "contrate"]),
]


def classify_intent(text: str) -> tuple[str, float]:
    """Score semântico-por-perfil (perfis + radicos verbais): não exige frase exata."""
    t = _fold(text) + " "
    # 1) meta-padrões têm prioridade (ex.: "me mostre o grafo" ≠ ler arquivo)
    for intent, pats in _META_PATTERNS:
        for p in pats:
            if _fold(p) in t:
                return intent, 5.0
    # 2) pontuação por perfis + radicais
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
    # 3) desempate determinístico: se a frase tem par (arquivo+conteúdo) extraível,
    #    é uma ordem de escrita real — não conversa nem listagem ("lista de compras")
    if best != "criar_arquivo" and _split_write(text):
        return "criar_arquivo", round(best_score, 2)
    if best_score < 2.6:
        # limiar baixo só vale p/ conversa; verbo de escrita + arquivo nunca é vazio
        if not _has_write_target(text):
            best = "conversar"
    return best, round(best_score, 2)


# ---------------------------------------------------------------- planner determinístico offline
def deterministic_plan(text: str) -> list[dict]:
    """Passo a passo executável SEM LLM — a base age sozinha quando a API falta.

    PLAYBOOK DE AÇÕES (passo a passo embutido no código, requisito de instruções
    pré-definidas por intenção):
      ler_arquivo  → extrair alvo → read_file(alvo) | sem alvo: pedir detalhe
      executar     → extrair comando entre aspas/backticks → bash(cmd)
      memoria      → destripar verbos ('lembre-se que ...') → remember(fato limpo)
      buscar_web   → destripar 'pesquise na internet por X' → search_web(X)
      visual       → 'camera/foto' → camera_view | senão see_screen
      celular      → 'notifica X' → phone(notificar) | 'abre app X' → abrir_app
      mostrar_grafo→ show_graph (UI pura, sem LLM)
      listar       → extrair pasta-alvo se houver → list_dir(pasta) | padrão '.'
      sub_agente   → nome+alvo extraídos → spawn_subagent(name, goal)
      criar/editar/planejar/conversar → sem plano fixo: exigem conteúdo/LLM;
            o Agent responde com fallback instruído (nunca falha silenciosa).
    """
    intent, _ = classify_intent(text)
    plans: dict[str, list[dict]] = {
        "ler_arquivo": lambda: _plan_read(text),
        "executar": lambda: [{"name": "bash", "args": {"command": _first_cmd(text)}}],
        "memoria": lambda: [{"name": "remember", "args": {"text": _strip_fact(text), "tag": "usuario"}}],
        "buscar_web": lambda: [{"name": "search_web", "args": {"query": _strip_query(text)}}],
        "visual": lambda: ([{"name": "camera_view", "args": {}}]
                           if _fold(text).__contains__("camera") or "foto" in _fold(text)
                           else [{"name": "see_screen", "args": {}}]),
        "celular": lambda: _plan_phone(text),
        # intenções de UI/orquestração → ferramenta interna (não pedem arquivo nem LLM)
        "mostrar_grafo": lambda: [{"name": "show_graph", "args": {}}],
        "listar": lambda: _plan_list(text),
        "sub_agente": lambda: [{"name": "spawn_subagent",
                                "args": {"name": _sub_name(text), "goal": _sub_goal(text)}}],
        "criar_arquivo": lambda: _plan_create(text),  # escrita offline quando há par arquivo+conteúdo
        "editar_codigo": lambda: [],
        "planejar": lambda: [],
        "conversar": lambda: [],
    }
    return plans.get(intent, lambda: [])()


def _plan_read(text: str) -> list[dict]:
    target = _first_path(text)
    if target.startswith("("):        # sem alvo extraível → não chamar read_file à toa
        return [{"name": "ask_detail", "args": {"question":
                "qual arquivo devo ler? (ex.: leia joe/config.py)"}}]
    return [{"name": "read_file", "args": {"path": target}}]


# ---------------------------------------------------------- escrita offline (playbook criar_arquivo)
_EXT_RE = r"(?:\.(?:txt|md|py|js|ts|json|yaml|yml|toml|csv|html|css|log|ini|cfg|sh|rs|go|c|h))?"
_DIR_PREP = ("em", "no", "na", "no arquivo", "pra", "para")   # preposições que anunciam o alvo

def _split_write(text: str) -> tuple[str, str] | None:
    """Extrai (arquivo, conteúdo) de frases literais de escrita, SEM LLM.

    Formas reconhecidas (todas determinísticas):
      'escreva OLA em x.txt'            → separador 'em' (sufixo)
      'crie notas.txt com lista...'     → separador 'com'/'que'
      'salve em projetos/ideias.md: ...'→ caminho + dois-pontos
      'adicione na agenda.txt reuniao..'→ preposição + extensão
      'grave isso no diario.txt: ...'   → pronome + preposição composta
    Retorna None quando não há par (arquivo+conteúdo) confiável — nesse caso
    o Agent cai no fallback instruído (nunca falha silenciosa).
    """
    if _READ_LEAD_RE.search(text):     # 'leia config.py pra mim' ≠ escrever
        return None
    t = text.strip()
    # 0) sufixo forte: <conteúdo> em|no|na|para|pra <alvo-com-extensão>
    m = re.search(r"^(?:\S+\s+)?(?:escrev\w*|col\w*|grav\w*|salv\w*|adicion\w*"
                  r"|bot\w*|redij\w*|anot\w*)(?:\s+(?:isto|isso|aquilo))?\s+"
                  r"(.+?)\s+(?:em|no|na|pra|para)\s+"
                  r"((?:[\w.-]+[/\\])*[\w.-]+\.[a-zA-Z]{1,8})\s*[.:!?]*\s*$",
                  t, re.I)
    if m and len(m.group(1)) >= 2:
        return m.group(2), m.group(1).strip(" .:!\"'")
    # 1) padrão forte: <verbo> [prep] <alvo> <:|' com '|' que '|> <conteúdo>
    m = re.search(
        r"(?:escrev\w*|col\w*|grav\w*|salv\w*|adicion\w*|bot\w*|anot\w*|redij\w*)"
        r"(?:\s+(?:isto|isso|aquilo|o seguinte|o texto))?[\s,:]+"
        r"(?:(?:no arquivo|na pasta|em|no|na|pra|para)\s+)?"
        r"((?:[\w.-]+[/\\])+[\w.-]+|[\w.-]+\.[a-zA-Z]{1,8}|[\w-]+)"
        r"(?:\s*(?:com|que|contendo|de|:)[:,]?\s*)?(.+)$",
        t, re.I)
    if m:
        path, content = m.group(1), m.group(2).strip(" .:!\"'")
        # heurística anti-falso-positivo: se o 'conteúdo' começa com outro
        # caminho e a 1ª captura era só um verbo solto, deslocamos o alvo
        alt = re.match(r"^([\w.-]+\.[a-zA-Z]{1,8})\s+(?:com|que|:)?\s*(.+)$",
                       content, re.I)
        if alt and not re.search(r"[.]", path) and alt.group(1) != path:
            path, content = alt.group(1), alt.group(2)
        if len(content) >= 1 and path.lower() not in ("isso", "isto", "ele", "ela"):
            return path, content
    # 2) fraco: primeiro token com extensão conhecida + resto como conteúdo
    m = re.search(r"\b([\w./-]+\.(?:txt|md|py|js|json|yaml|yml|log|csv|html|ini))\b\s+(.+)",
                  t, re.I)
    if m and len(m.group(2)) >= 2:
        return m.group(1), m.group(2).strip(" .:!\"'")
    return None


# verbos de escrita usados no desempate determinístico do classificador
_WRITE_VERB_RE = re.compile(
    r"\b(?:escrev\w*|col\w*|grav\w*|salv\w*|adicion\w*|bot\w*|redij\w*|anot\w*|"
    r"cri\w*|ger\w*|implement\w*|mont\w*)\b", re.I)
# verbos de LEITURA: se abrem a frase, o arquivo é alvo de leitura, nunca de escrita
_READ_LEAD_RE = re.compile(r"^\s*(?:me\s+)?(?:leia|ler|le\b|l[êe]\b|abri\w*|abre|mostra\w*"
                           r"|mostre|confira|checa\w*|ve\s+o?\s*(?:arquivo|c[óo]digo))",
                           re.I)
_FILE_TOKEN_RE = re.compile(
    r"(?:[\w.-]+[/\\])*[\w.-]+\.(?:txt|md|py|js|ts|json|yaml|yml|toml|csv|html|css|"
    r"log|ini|cfg|sh|rs|go|c|h)\b", re.I)


def _has_write_target(text: str) -> bool:
    """True se a frase contém (verbo de escrita + token de arquivo) — sinal forte
    de ordem de escrita, mesmo quando o par exato não é extraível.
    Frases que começam com verbo de leitura ('leia x.py') são excluídas."""
    if _READ_LEAD_RE.search(text):
        return False
    return bool(_WRITE_VERB_RE.search(text)) and bool(_FILE_TOKEN_RE.search(text))


_READ_TOKEN_RE = re.compile(r"[\w./-]+\.[a-zA-Z]{1,8}\b")


def _has_read_target(text: str) -> bool:
    """Verbo de leitura no início + token de arquivo → intenção ler_arquivo,
    mesmo sem palavra-chave do perfil ('abre o readme.md')."""
    return bool(_READ_LEAD_RE.search(text)) and bool(_READ_TOKEN_RE.search(text))


def _plan_create(text: str) -> list[dict]:
    """Playbook de criação: escreve se houver par (arquivo+conteúdo); caso contrário
    pede o detalhe faltante em vez de plano vazio (nunca falha silenciosa)."""
    pair = _split_write(text)
    if not pair and not _has_write_target(text):
        return []                      # criação aberta ("crie um site") → rota LLM
    return _plan_write(text)


def _plan_write(text: str) -> list[dict]:
    pair = _split_write(text)
    if not pair:
        return [{"name": "ask_detail", "args": {"question":
                "em qual arquivo devo escrever e com que conteúdo? "
                "(ex.: escreva ola.txt com: olá mundo)"}}]
    path, content = pair
    return [{"name": "write_file", "args": {"path": path, "content": content}}]


# palavras de frase que nunca são nomes de pasta (evitar 'list_dir("pasta")')
_STOP_DIR_WORDS = {"pasta", "diretorio", "diretorios", "atual", "raiz", "home",
                   "projeto", "arquivos", "arquivo", "conteudo", "itens", "tudo"}


def _plan_list(text: str) -> list[dict]:
    """'liste os arquivos [da pasta X]' → list_dir(X); sem pasta → '.' (raiz atual)."""
    t = _fold(text)
    # 1º) preposição + 'pasta X' (o nome depois de 'pasta' é o alvo real)
    m = re.search(r"(?:na|da|em)\s+pasta\s+([~\w][\w./_-]*)", t)
    if not m:
        # 2º) caminho explícito com barra ou ponto (/tmp, ./src, ~/projetos)
        m = re.search(r"(?<![\w.-])([~.]/[\w./-]+|/[\w./-]+)", t)
    if not m:
        # 3º) preposição + nome de diretório (joe, tests, src…)
        m = re.search(r"(?:na|da|em)\s+(?:pasta\s+)?([~\w][\w._-]*)", t)
    path = m.group(1) if m else "."
    if _fold(path) in _STOP_DIR_WORDS:      # 'da pasta' genérico → raiz atual
        path = "."
    return [{"name": "list_dir", "args": {"path": path}}]


def _plan_phone(text: str) -> list[dict]:
    t = _fold(text)
    # ordem importa: "abrir app X no celular" é abrir_app, não notificar.
    if any(w in t for w in ("abre", "abrir", "abra")):
        app = re.search(r"(?:app|aplicativo)\s+([\w.-]+)", t)
        return [{"name": "phone", "args": {"action": "abrir_app",
                                           "app": app.group(1) if app else text}}]
    if any(w in t for w in ("notific", "avisa", "avisar", "avise", "alerta")) or \
            re.search(r"\bno\s+cel(ular)?\b", t):
        return [{"name": "phone", "args": {"action": "notificar",
                                           "message": text}}]
    return [{"name": "phone", "args": {"action": "status"}}]


_FACT_STRIP = re.compile(
    r"^(?:por\s+favor[, ]+|me\s+)?(?:lembre[-\s]?se?\s+(?:que|de)?|nunca\s+esque(?:ça|ca)\s+(?:que|de)?"
    r"|anote\s+(?:que|isso)?|memorize?\s+(?:que|isto)?|guarde\s+(?:que|isto)?|decor(?:e|a)\s+(?:que|de)?)[,: ]*",
    re.I)


def _strip_fact(text: str) -> str:
    """'lembre-se que meu nome é Carlos' → 'meu nome é Carlos' (fato limpo na memória)."""
    out = _FACT_STRIP.sub("", text.strip(), count=1)
    return out.strip() or text.strip()


_QUERY_STRIP = re.compile(
    r"^(?:pesquis(?:e|ar|a)?|busqu(?:e|ar|a)?|procur(?:e|ar|a)?|procure)[^\"]*?(?:por|sobre|na\s+web|online|internet)?[ :]",
    re.I)


def _strip_query(text: str) -> str:
    """'pesquise na internet por clima em Recife' → 'clima em Recife'."""
    m = re.search(r"(?:por|sobre)\s+(.+)$", text, re.I)
    if m:
        return m.group(1).strip(" ?.!")
    out = _QUERY_STRIP.sub("", text.strip(), count=1)
    return out.strip(" ?.!") or text.strip()


_SUB_PREFIX_STRIP = re.compile(
    r"^(?:(?:crie|gere|use|chame|contrate|spawn)\s+)?"
    r"(?:um|uma|o|a)?\s*(?:novo\s+|nova\s+)?"
    r"(?:sub[-\s]?agente|agente|filho)(?:\s+[a-z][\w-]*)?\s+"
    r"(?:para|pra)\s+",
    re.I)


def _sub_goal(text: str) -> str:
    """Isola a meta real do filho (playbook: 'um agente X PARA <meta>').

    'crie um sub-agente revisor para revisar o codigo' → 'revisar o codigo'.
    Sem marcador 'para', mantém o texto todo (nunca devolve vazio)."""
    m = _SUB_PREFIX_STRIP.search(text.strip())
    if m:
        goal = text.strip()[m.end():].strip()
        if goal:
            return goal
    mm = re.search(r"\b(?:para|pra)\s+(.+)$", text, re.I | re.S)
    goal = mm.group(1).strip() if mm else text.strip()
    return goal or text.strip()


def _first_path(text: str) -> str:
    """Extrai caminho/arg do texto; nunca devolve '.' — sem alvo, pede detalhe."""
    m = re.search(r"[~\w][\w./-]*\.[\w]{1,8}|(?<![\w./-])[\w][\w./-]*(?:/[\w][\w./-]*)+", text)
    if m and _fold(m.group(0)) not in _NON_PATH_WORDS:
        return m.group(0)
    return "(no arquivo indicado — especifique o caminho)"


_SUB_STOP = ("para", "pra", "que", "de", "o", "a", "um", "uma",
             "chamado", "chamada", "nome", "nomeado", "apelidado")

# verbos/alvos de tarefa: se a 1ª palavra for um destes, o usuário deu só a
# tarefa (sem nome) no formato posicional "/sub <nome> <tarefa>"
_TASK_VERBS = {"resumir", "resuma", "liste", "buscar", "busque", "achar",
               "ache", "procurar", "procure", "analisar", "analise", "criar", "crie",
               "revisar", "revise", "corrigir", "corrija", "explicar", "explique",
               "traduzir", "traduza", "salvar", "salve", "ler", "leia", "executar",
               "execute", "rodar", "rode", "escrever", "escreva", "planejar", "planeje",
               "organizar", "organize", "encontrar", "encontre", "pesquisar", "pesquise",
               # imperativos/2ª pessoa de comando — se abrissem a frase, o usuário
               # deu só a tarefa (ex. "/sub use um agente para listar…")
               "use", "utilize", "empregue", "ative", "invoque", "delegue", "destaque",
               # formas infinitivas correspondentes
               "listar", "delegar", "checar"}

# palavras que NUNCA são nome de sub-agente no formato posicional
_SUB_NAME_BLACKLIST = _TASK_VERBS | set(_SUB_STOP)


def _is_task_word(w: str) -> bool:
    """'resumir' e 'analise' contam como tarefa; substantivos-agentes não.

    Regra: imperativos pt terminam em vogal tônica ('revise', 'pesquisador'→não);
    desinências de ação ('-ção', '-mento') e infinitivos ('-r') são tarefa,
    mas NÃO '-dor/-tor/-sor' (agentivos: pesquisador, executor, revisor…).
    """
    if w in _SUB_NAME_BLACKLIST:
        return True
    if w.endswith(("ção", "cao", "mento")):
        return True
    if w.endswith("r") and len(w) > 3 and not w.endswith(("dor", "tor", "sor")):
        return True
    return False


def _sub_name(text: str) -> str:
    t = _fold(text)
    # forma explicita: "chamado X", "nomeado X", "com nome X" (mais preciso que posicao)
    m = re.search(r"(?:chamad[oa]|nomead[oa]|apelidad[oa]|com nome|nome)\s+([a-z][\w-]+)", t)
    if m and m.group(1) not in ("de", "do", "da", "o", "a"):
        return m.group(1)
    m = re.search(r"(?:sub[- ]?agente|agente)\s+([a-z][\w-]+)", t)
    if m and m.group(1) not in _SUB_STOP:
        return m.group(1)
    # forma posicional do REPL: "/sub <nome> <tarefa>" — 1ª palavra é o nome,
    # exceto quando for só um verbo de tarefa (então cai no default 'filho')
    words = [w for w in re.split(r"\s+", t) if w]
    if len(words) >= 2 and not _is_task_word(words[0]):
        return words[0]
    return "filho"


def _first_cmd(text: str) -> str:
    m = re.search(r"(?:rode|execute|executar|run|rodar)\s+[`'\"]?([^`'\"\n]+)", text, re.I)
    if m:
        return m.group(1).strip()
    # sem comando delimitado: extrai a frase depois do verbo e remove pontuação final
    m = re.search(r"(?:rode|execute|executar|run|rodar)\s+(.+?)\s*[.!?]*$", text, re.I)
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
    """Filho usa o MELHOR motor disponível, com orçamento limitado.

    Ordem de decisão (camadas plugáveis — sem tocar no núcleo):
      1. LLM ativo → filho é um Agent(subagent=sa) real: herda pipeline
         intenção→plano→execução→revisão→conclusão e o loop de ferramentas.
      2. sem LLM → planner determinístico direto no registry de tools
         (não instamos Agent completo para evitar recursão sub_agente→spawn).
    Em ambos os casos o filho emite eventos no bus → nó novo no grafo.
    """
    def run():
        from .tools.base import registry          # late import (evita ciclo)
        from .llm import provider_registry
        if provider_registry.active() is not None:
            from .agent import Agent              # late import: evita ciclo agents↔agent
            child = Agent(subagent=sa)
            try:
                return child.chat(goal)
            except Exception as e:
                return f"[{sa.name}] com LLM falhou ({e}); caindo para plano offline]\n" + \
                       _offline_child(goal, sa, registry)
        return _offline_child(goal, sa, registry)
    return run


def _offline_child(goal: str, sa: SubAgent, registry) -> str:
    steps = [s for s in deterministic_plan(goal) if s["name"] != "spawn_subagent"]
    if not steps:
        return (f"[{sa.name}] tarefa entendida como '{classify_intent(goal)[0]}' — "
                "sem ação direta offline (precisa de conteúdo/LLM; eu, Joe, explico no resultado).")
    outs = []
    bus.emit("agent.thought", id=sa.id, agent=sa.id, thought=f"planejei {len(steps)} passo(s): "
             + ", ".join(s["name"] for s in steps))
    for st in steps[:4]:                          # orçamento do filho
        r = registry.execute(st["name"], st["args"])
        bus.emit("agent.tool", id=sa.id, agent=sa.id, tool=st["name"], ok=r.ok)
        outs.append(f"{st['name']}: {r.as_text()[:400]}")
    return "\n".join(outs)
