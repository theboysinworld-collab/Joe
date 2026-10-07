
## Superfície Etapa 4 — proatividade visível (verificado)
- **Grafo ↔ scheduler**: eventos `suggest` viram a linha `✦ sugerido: …` no grafo central;
  sensores (`sense.<nome>`) aparecem como nós vivos 👁. Testes: `tests/test_surface.py`.
- **`/suggest`** no REPL força o ciclo de proatividade (com cooldown respeitado).
- **Auto-expansão do planner offline**: intenção sem playbook + ferramenta registrada
  (ex.: criada por extensão via `/newext`) → o executor a usa no mesmo turno, sem editar o núcleo.
- **CI**: `.github/workflows/ci.yml` roda `run.py --check`, a suíte completa e smoke E2E offline
  em Python 3.10/3.12/3.13.

### Como rodar tudo (inicialização única)
```bash
python3 run.py                 # REPL + IDE + agentes + memória + scheduler + grafo
python3 run.py --once "<intenção>"   # turno único (planner determinístico se não houver LLM)
python3 run.py --health        # diagnóstico de módulos/capacidades
python3 run.py --check         # boot de verificação (usado no CI)
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest tests/ -q -p no:libtmux
```
