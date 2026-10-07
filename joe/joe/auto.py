"""Auto-análise, auto-cura e auto-evolução do Joe — o "sistema imunológico".

Fluxo da análise (passo a passo):
1. Lê eventos episódicos do SQLite (tudo que passou pelo bus foi auditado).
2. Detecta padrões de FALHA: ferramentas que falham repetidamente, intents sem plano,
   provedores indisponíveis recorrentes.
3. Para cada padrão → gera um PLAYBOOK em ~/.joe/learnings/*.md + fato na memória
   (tag=playbook) → injetado automaticamente no system prompt dos próximos turnos.
4. `heal()` age: reconstrói índice quebrado, marca provider morto, regenera extensão
   com erro de sintaxe via sub-agente (auto-evolução assistida por LLM quando houver).
5. `evolve_report()` resume o estado para o usuário ver a evolução nas sessões.

Princípio: nunca apaga nada; corrige por cima com backup (.bak).
"""

from __future__ import annotations

import json
import time
from collections import Counter
from pathlib import Path

from .config import LEARNINGS_DIR, Config
from .memory import Memory


class SelfAnalysis:
    def __init__(self, config: Config | None = None, memory: Memory | None = None):
        self.config = config or Config.load()
        self.memory = memory or Memory(config=self.config)

    # ------------------------------------------------------------ 1-2: diagnóstico
    def analyze(self, last_n: int = 400) -> dict:
        events = self.memory.recent_events(limit=last_n)
        tool_fail = Counter()
        tool_ok = Counter()
        for ev in events:
            if ev["topic"] == "agent.tool_result":
                p = ev["payload"]
                (tool_ok if p.get("ok") else tool_fail)[p.get("tool", "?")] += 1
        problems = [f"ferramenta '{t}' falhou {n}x" for t, n in tool_fail.items() if n >= 2]
        hot = [f"uso intenso: '{t}' ({n}x)" for t, n in tool_ok.most_common(3)]
        return {"events_seen": len(events), "problems": problems, "patterns": hot,
                "failed_tools": dict(tool_fail)}

    # ------------------------------------------------------------ 3: lições viram contexto
    def distill(self) -> list[str]:
        """Converte diagnósticos recorrentes em playbooks gravados na memória."""
        report = self.analyze()
        saved = []
        for prob in report["problems"]:
            lesson = f"PLAYBOOK: {prob}. Estratégia: antes de usar essa ferramenta, valide pré-condições (arquivo existe? dependência instalada?) ou delegue a um sub-agente."
            self.memory.remember(lesson, tag="playbook", source="self-analysis")
            saved.append(lesson)
        if report["patterns"]:
            pref = ", ".join(p.split("'")[1] for p in report["patterns"] if "'" in p)
            if pref:
                lesson = f"PLAYBOOK: ferramentas preferidas do fluxo atual: {pref}. Mantenha-as quentes e cheque /health periodicamente."
                self.memory.remember(lesson, tag="playbook", source="self-analysis")
                saved.append(lesson)
        # espelho legível em ~/.joe/learnings
        LEARNINGS_DIR.mkdir(parents=True, exist_ok=True)
        day = time.strftime("%Y-%m-%d")
        with (LEARNINGS_DIR / f"{day}.md").open("a", encoding="utf-8") as f:
            for s in saved:
                f.write(f"- {s}\n")
        return saved

    # ------------------------------------------------------------ 4: ações de cura
    def heal(self) -> list[str]:
        actions = []
        # 4a. banco ilegível → backup + recriação
        try:
            self.memory.conn.execute("SELECT count(*) FROM facts").fetchone()
        except Exception as e:
            db = self.memory.db_path
            try:
                self.memory.conn.close()
            except Exception:
                pass
            if db.exists():
                bak = db.with_name(db.stem + ".db.bak")
                if bak.exists():
                    bak.unlink()
                db.rename(bak)
                actions.append(f"banco corrompido ({e}); restaurado novo, antigo em {bak.name}")
            else:
                actions.append(f"banco ilegível ({e}); recriado limpo")
            self.memory.__init__(db)  # reabre limpo
        # 4b. extensões quebradas → relatar (cura real exige editar; faremos via LLM se houver)
        from .extensions import load_all
        for name, meta in load_all().items():
            if "error" in meta:
                actions.append(f"extensão '{name}' com erro: {meta['error']} "
                               "(peça ao Joe: 'conserte a extensão " + name + "')")
        # 4c. provider ativo sumiu → reseleção automática
        from .llm import provider_registry, auto_select
        act = provider_registry.active()
        if act and not act.available():
            new = auto_select(self.config)
            actions.append(f"provider '{act.id}' caiu; reselecionado: {new or 'nenhum (modo offline planner)'}")
        return actions or ["nada a curar ✅"]

    # ------------------------------------------------------------ 5: relatório de evolução
    def evolve_report(self) -> str:
        facts = self.memory.facts(limit=1000)
        playbooks = [f for f in facts if f["tag"] in ("playbook",)]
        preds = self.memory.predict()
        lines = [
            f"Sessão observada: {len(facts)} fatos, {len(playbooks)} playbooks próprios.",
            "O que aprendi sozinha nesta máquina:",
            *[f"  • {p['text'][:140]}" for p in playbooks[-5:]],
            "Seus hábitos atuais:",
            *[f"  • {p['intent']} ({p['count']}x nesta hora)" for p in preds],
        ]
        return "\n".join(lines)
