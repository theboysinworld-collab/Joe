# Joe 👤🛠️ — IDE · Assistente Pessoal · Agente Geral

**Joe** é um agente autônomo de terminal escrito em Python puro (zero dependências obrigatórias)
que combina três papéis:

| Papel | O que faz |
|---|---|
| 🤖 **Agente geral** | Loop LLM + ferramentas: executa shell, lê/edita/cria arquivos, busca no projeto, memoriza fatos — e age de verdade, não só descreve. |
| 🧑‍💻 **IDE** | Mini-IDE no terminal com buffers, edição por linha, `:grep` no projeto e `:run` de comandos. |
| 🗂️ **Assistente pessoal** | Memória persistente entre sessões (`~/.joe/memory.json`), histórico de conversas, configuração simples. |

## Instalação

```bash
cd joe
pip install -e .        # cria o comando `joe`
```

Requisito: Python ≥ 3.10. Nenhuma dependência externa é obrigatória.

## Uso

### Agente (modo interativo)
```bash
export OPENAI_API_KEY=sk-...   # opcional: habilita o modo LLM completo
joe                            # REPL: converse naturalmente ou use /comandos
joe "crie um script python que baixa cotações"   # turno único (scripts)
```

Sem API key o Joe entra em **modo offline**: os comandos slash (`/bash`, `/read`,
`/grep`, `/remember`...) continuam funcionando — ótimo para usar como assistente local.

Compatível com qualquer endpoint OpenAI-like (Ollama, OpenRouter, etc.):
```bash
joe config base_url http://localhost:11434/v1
joe config model llama3.1
```

### Comandos do REPL
```
/bash <cmd>     /read <arq>     /write <arq>    /grep <padrão>  /ls [dir]
/remember <fato>  /recall [busca]  /forget <palavra>
/ide [arquivo]  /model [nome]   /tools          /reset          /help  /exit
```

### Mini-IDE
```bash
joe ide main.py
```
```
:open/:new/:save/:close/:bufs/:lines      gestão de buffers
:edit <n> <txt> / :insert / :delete       edição por linha
:find <p> (buffer)  :grep <p> (projeto)   busca
:cd <dir>   :run <cmd>   :help   :q       navegação/execução
```

### Config e memória via CLI
```bash
joe config                          # ver tudo
joe config confirm_shell false      # modo auto p/ shell (use com cuidado)
joe memory list / add / search / forget
```

Arquivos: `~/.joe/config.json`, `~/.joe/memory.json`, `~/.joe/history/*.jsonl`
(sobrescreva o diretório com a variável `JOE_HOME`).

## Ferramentas do agente (function-calling)

`bash` (com confirmação interativa e bloqueio de padrões destrutivos), `read_file`,
`write_file`, `edit_file`, `list_dir`, `grep`, `remember`, `recall`, `forget`.
Registre as suas em `joe/tools/` — basta herdar de `Tool` e chamar `registry.register()`.

## Arquitetura

```
joe/
├── cli.py            # REPL + subcomandos (argparse)
├── agent.py          # loop conversa↔ferramentas (tool calling estilo OpenAI)
├── llm.py            # cliente HTTP stdlib + fallback offline
├── config.py         # ~/.joe/config.json + env vars
├── memory.py         # memória persistente
├── tools/            # registry, base, shell, files, search, memory_tools
└── ide/              # editor.py: MiniIDE com buffers e comandos ":"
tests/test_smoke.py   # testes de fumaça (pytest)
```

## Testes

```bash
pip install pytest && pytest joe/tests -q
```

## Roadmap
- [ ] Streaming de tokens e syntax highlight (opcional via `rich`)
- [ ] Autocomplete de caminhos no REPL
- [ ] Tarefas agendadas ("lembre-me de…") e integração com calendário
- [ ] Modo multi-agente (subagentes para tarefas longas)
