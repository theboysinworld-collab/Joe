# Joe — IDE, assistente pessoal e agente geral

Plataforma local em Python puro (≥3.10), sem dependências obrigatórias.
Liga tudo num único boot: `python3 run.py`.

## Inicialização única (`run.py`)
Ordem do boot (arquitetura em camadas, cada uma plugável):
**config → ferramentas → extensões drop-in → provedores LLM → benchmark/seleção de UM modelo
→ capacidades sensoriais → memória SQLite + RAG → scheduler proativo (daemon) → interface**.

```bash
python3 run.py                        # REPL + grafo de agentes ao centro
python3 run.py --once "<intenção>"    # turno único (planner determinístico offline)
python3 run.py --ide [arquivo]        # mini-IDE de terminal (buffers, :edit, :grep, :run)
python3 run.py --bench                # benchmark dos modelos detectados (ollama etc.)
python3 run.py --health               # diagnóstico de módulos/capacidades/ferramentas
python3 run.py --check                # boot de verificação (usado no CI)
pip install -e .                      # opcional: comando global `joe`
```

## Pipeline de agentes (visível na UI central, estilo grafo/n8n a ~35% opacidade)
`intenção → classificador → planejador → executor → revisor → conclusão`, com sub-agentes
(`/sub <nome> <tarefa>`) que aparecem no grafo ao nascer e se comunicam via EventBus
(`joe/bus.py`, replay ring-buffer). Cada nó mostra o que pensou, criou, decidiu e salvou.

## Inteligência sem LLM (fallback determinístico)
- **Intenções**: perfis ponderados + radicais + normalização (sem exigência de palavras-chave exatas).
- **Planner offline** (`joe/agent.py::deterministic_plan`): executa ações reais sem rede/API.
- **Auto-expansão**: intenção sem playbook + ferramenta registrada (ex.: criada por extensão
  via `/newext`) → usada no mesmo turno, sem editar o núcleo.

## Modelos locais lentos (>120s) — timeout configurável
Ollama em CPU, contexto grande ou primeiro carregamento do modelo podem demorar mais de
120s. O Joe trata isso em camadas:
- `config.llm_timeout` (padrão **300s** por tentativa) — ajustável com
  `/config llm_timeout 600` no REPL (aplica na hora, sem reiniciar) ou via env `JOE_LLM_TIMEOUT`.
- **Timeout é retryável**: até 3 tentativas com backoff (o modelo pode estar só carregando).
- Se ainda assim falhar, o turno **não morre**: o agente cai no planner determinístico
  offline e avisa `[LLM indisponível: …] — alternando para plano offline`.
- Dica: mantenha o modelo quente com `OLLAMA_KEEP_ALIVE=-1` ou `/bench` para escolher
  um modelo menor/rápido (ex.: `qwen2.5-coder:1.5b`).

## Memória e RAG (`joe/memory.py`)
SQLite unificado (fatos, episódios, hábitos) + vetores; embeddings via ollama
(`nomic-embed-text`) quando disponível, com fallback offline determinístico (hashing).
Busca híbrida (vetorial + lexical). `/remember`, `/recall`, `/rag`, `/predict`.

## Proatividade e autonomia (`joe/scheduler.py`)
Sensores deduplicados, aprendizado de hábitos e sugestões contextuais com cooldown;
eventos `suggest` viram a linha `✦ sugerido: …` no grafo; `/suggest` força o ciclo.
Auto-análise (`/analyze`), auto-cura (`/heal`) e auto-evolução (`/evolve`).

## Capacidades sensoriais (`joe/capabilities.py`)
falar · ouvir · ver tela · câmera · celular (Termux/notificações) · criar imagem ·
editar vídeo · pesquisar web (DuckDuckGo com cascatas de fallback offline-first).
Cada capacidade tem degradação graciosa quando o hardware/rede falta.

## Superfície Etapa 4 — proatividade visível (verificado)
- **Grafo ↔ scheduler**: eventos `suggest` viram a linha `✦ sugerido: …` no grafo central;
  sensores (`sense.<nome>`) aparecem como nós vivos 👁. Testes: `tests/test_surface.py`.
- **`/suggest`** no REPL força o ciclo de proatividade (com cooldown respeitado).
- **Auto-expansão do planner offline**: intenção sem playbook + ferramenta registrada
  (ex.: criada por extensão via `/newext`) → o executor a usa no mesmo turno, sem editar o núcleo.
- **CI**: `.github/workflows/ci.yml` roda `run.py --check`, a suíte completa com piso de
  cobertura ≥85% e smoke E2E offline em Python 3.10/3.12/3.13.

## Extensibilidade sem tocar o núcleo
- Ferramentas: subclasses de `joe/tools/base.py` ou `FunctionTool` registrados no registry.
- Extensões drop-in: pasta `~/.joe/extensions/*.py` carregadas no boot (`/ext`, `/newext`).
- Provedores LLM: ollama local / OpenRouter / OpenAI via `/provider` e
  `/config openrouter <chave>` (chave fica em `~/.joe/config.json`).

## Comandos essenciais do REPL
| comando | função |
|---|---|
| texto livre | intenção → pipeline de agentes (LLM se houver, planner offline senão) |
| `/graph` `/tools` `/health` `/ext` `/provider` `/bench` | estado do sistema |
| `/remember` `/recall` `/rag` `/predict` | memória + RAG |
| `/bash` `/read` `/write` `/edit` `/ls` `/grep` | arquivos e shell (com confirmação p/ perigosos) |
| `/web` `/see` `/say` `/img` | capacidades sensoriais |
| `/sub <nome> <tarefa>` `/suggest` | sub-agentes, proatividade manual |
| `/analyze` `/heal` `/evolve` `/newext` | auto-manutenção e criação de módulos |
| `/config` `/memory` `/reset` `/exit` | configuração e sessão |

## Testes
```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest tests/ -q -p no:libtmux           # 167 testes
python3 -m pytest tests/ -q -p no:libtmux --cov=joe --cov-fail-under=85              # cobertura 87%
```
Suítes: smoke, unidades (agent/llm/core/config), ferramentas (shell/vision/web/memória),
capacidades sensoriais, scheduler, superfície (grafo↔scheduler), IDE, REPL E2E interativo,
boot `run.py`, provedores com HTTP mockado.

## Estrutura
```
joe/
├── run.py            # inicialização única (full_boot)
├── joe/
│   ├── cli.py        # REPL leve (essencial exposto)
│   ├── agent.py      # pipeline + planner determinístico + tool loop
│   ├── agents.py     # sub-agentes
│   ├── bus.py        # EventBus (comunicação visível no grafo)
│   ├── ui.py         # grafo central translúcido estilo n8n
│   ├── llm.py        # provedores + benchmark + um modelo ativo
│   ├── memory.py     # SQLite + vetores + hábitos/predict
│   ├── scheduler.py  # sensores + proatividade daemon
│   ├── capabilities.py  # visão/áudio/celular/imagem/vídeo/web
│   ├── config.py     # ~/.joe/config.json + env vars
│   ├── extensions.py # carregamento drop-in
│   ├── auto.py       # análise/cura/evolução
│   ├── ide/          # mini-IDE de terminal
│   └── tools/        # registry + ferramentas base
└── tests/            # 19 arquivos, 167 testes
```
