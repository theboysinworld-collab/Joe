"""Ferramenta de shell: execução de comandos bash com segurança básica."""

from __future__ import annotations

import subprocess

from .base import Tool, ToolResult, registry
from ..config import Config

# padrões de comandos destrutivos que exigem confirmação mesmo em modo auto
DANGEROUS = ("rm -rf /", "mkfs", "dd if=", ":(){", ">dev/sd", "shutdown", "reboot")


class ShellTool(Tool):
    name = "bash"
    description = (
        "Executa um comando bash no diretório de trabalho do projeto e retorna stdout/stderr. "
        "Use para compilar, rodar testes, inspecionar o sistema etc."
    )
    parameters = {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "Comando bash a executar"},
            "timeout": {"type": "integer", "description": "Timeout em segundos (padrão 60)"},
        },
        "required": ["command"],
    }

    def __init__(self, config: Config, confirm_fn=None):
        self.config = config
        # confirm_fn(prompt) -> bool; se None, usa input()
        self.confirm_fn = confirm_fn

    def _confirm(self, command: str) -> bool:
        if not self.config.confirm_shell:
            return True
        risky = any(d in command for d in DANGEROUS)
        prompt = f"[joe] Executar comando{ ' (PERIGOSO!)' if risky else ''}: {command}\nConfirmar? [s/N] "
        if self.confirm_fn:
            return self.confirm_fn(prompt)
        try:
            ans = input(prompt).strip().lower()
        except (EOFError, KeyboardInterrupt):
            return False
        return ans in ("s", "sim", "y", "yes")

    def run(self, command: str, timeout: int = 60) -> ToolResult:
        if not self._confirm(command):
            return ToolResult(ok=False, output="usuário cancelou a execução do comando")
        try:
            proc = subprocess.run(
                ["bash", "-lc", command],
                capture_output=True,
                text=True,
                timeout=timeout,
                errors="replace",
            )
        except subprocess.TimeoutExpired:
            return ToolResult(ok=False, output=f"timeout após {timeout}s")
        out = (proc.stdout or "") + (("\n[stderr]\n" + proc.stderr) if proc.stderr else "")
        limit = self.config.shell_output_limit
        if len(out) > limit:
            out = out[:limit] + f"\n...[saída truncada em {limit} chars]"
        return ToolResult(
            ok=proc.returncode == 0,
            output=out.strip() or f"(exit code {proc.returncode})",
            meta={"exit_code": proc.returncode},
        )


registry.register(ShellTool(Config.load()))
