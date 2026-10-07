"""Joe — IDE completo, assistente pessoal autônomo e agente geral.

Arquitetura em camadas estáveis (adicione módulos, nunca mude o núcleo):
- bus.py         EventBus: comunicação desacoplada (UI/auditoria só escutam).
- config.py      ~/.joe/config.json + env (OpenRouter key, ollama host, modelo).
- llm.py         Providers plugáveis (ollama/OpenAI/OpenRouter), benchmark, 1 ativo/vez.
- memory.py      SQLite unificado: fatos+episódios+RAG vetorial+hábitos (fallback offline).
- capabilities.py Sentidos: falar/ouvir/ver tela/câmera/celular/imagem/vídeo/web.
- agents.py      Pipeline de papéis, planner determinístico sem LLM, sub-agentes.
- agent.py       Núcleo do loop intenção→plano→execução→revisão→conclusão.
- tools/         Registry de ferramentas (contrato único p/ novos módulos).
- extensions.py  Plug-ins drop-in em ~/.joe/extensions (auto-criação pelo Joe).
- auto.py        Auto-análise/auto-cura/auto-evolução (playbooks aprendidos).
- ui.py          Interface leve com grafo de agentes translúcido ao centro.
- ide/           Editor no terminal.
"""

__version__ = "0.2.0"
__app_name__ = "joe"
