import os
import shutil
import subprocess
from pathlib import Path
from typing import Dict, Any, Tuple, List, Optional


PROVIDER_PRESETS: Dict[str, Dict[str, Any]] = {
    "antigravity": {
        "binary": "agy",
        "args_template": [],
        "use_stdin": True,
    },
    "antigravity_cli": {
        "binary": "agy",
        "args_template": [],
        "use_stdin": True,
    },
    "claude": {
        "binary": "claude",
        "args_template": ["-p"],
        "use_stdin": True,
    },
    "codex": {
        "binary": "codex",
        "args_template": ["exec"],
        "use_stdin": True,
    },
    "gemini": {
        "binary": "gemini",
        "args_template": ["-p", "{instruction}"],
        "use_stdin": True,
    },
    "aider": {
        "binary": "aider",
        "args_template": ["--message-file", "{context_file}", "--yes-always"],
        "use_stdin": False,
    },
    "custom_cli": {
        "binary": "",
        "args_template": [],
        "use_stdin": True,
    },
    "manual": {
        "binary": "",
        "args_template": [],
        "use_stdin": False,
    },
}


class UniversalAgentAdapter:
    """
    Adaptador universal multi-IA (Antigravity, Claude Code, Codex, Gemini CLI, Aider, CLI personalizado)
    y modo manual explícito para IDEs (Zed ACP, Cursor, Windsurf, GitHub Copilot, Web Chat).

    Garantías:
    - Resuelve ejecutables multiplataforma (incluyendo .cmd/.bat/.exe en Windows con shell=False).
    - NUNCA da falso positivo si falta el binario de la IA en modo CLI: frena y avisa claramente.
    - Soporta modo manual explícito (--manual / mode: "manual") sin asumir flags inexistentes.
    """

    def __init__(self, harness_root: Path, project_root: Path, adapter_cfg: Dict[str, Any]):
        self.harness_root = harness_root.resolve()
        self.project_root = project_root.resolve()

        raw_provider = str(
            os.environ.get("HARNESS_PROVIDER")
            or adapter_cfg.get("provider")
            or adapter_cfg.get("type")
            or "antigravity"
        ).strip().lower()
        self.provider = raw_provider if raw_provider in PROVIDER_PRESETS else "custom_cli"
        preset = PROVIDER_PRESETS.get(self.provider, PROVIDER_PRESETS["custom_cli"])

        raw_mode = str(
            os.environ.get("HARNESS_MODE")
            or adapter_cfg.get("mode")
            or ("manual" if self.provider == "manual" else "cli")
        ).strip().lower()
        self.mode = "manual" if (raw_mode == "manual" or self.provider == "manual") else "cli"

        cfg_binary = adapter_cfg.get("binary")
        self.binary = str(cfg_binary).strip() if cfg_binary else str(preset.get("binary", "agy"))

        if "args_template" in adapter_cfg and adapter_cfg["args_template"] is not None:
            self.args_template: List[str] = list(adapter_cfg["args_template"])
        else:
            self.args_template = list(preset.get("args_template", []))

        if "use_stdin" in adapter_cfg and adapter_cfg["use_stdin"] is not None:
            self.use_stdin = bool(adapter_cfg["use_stdin"])
        else:
            self.use_stdin = bool(preset.get("use_stdin", True))

        self.timeout_seconds = int(adapter_cfg.get("timeout_seconds", 900))

    def is_manual_mode(self) -> bool:
        return self.mode == "manual" or self.provider == "manual"

    def resolve_binary(self) -> Optional[str]:
        if not self.binary:
            return None
        direct = Path(self.binary)
        if direct.is_file():
            return str(direct.resolve())
        return shutil.which(self.binary)

    def is_binary_available(self) -> bool:
        return self.resolve_binary() is not None

    def format_manual_instructions(self, context_file: Path, task_instruction: str, command_hint: str = "") -> str:
        validate_hint = (
            f"python main.py {command_hint} --validate"
            if command_hint
            else "python main.py <comando> --validate"
        )
        return (
            f"[MODO MANUAL / IDE ASISTIDO ACTIVO (provider='{self.provider}')]\n"
            f"1. El paquete de contexto limpio fue generado en:\n"
            f"   -> {context_file}\n"
            f"2. Pásale ese archivo a tu IA o agente de IDE (Zed ACP, Cursor, Claude, Windsurf, Copilot) con esta instrucción:\n"
            f"   \"{task_instruction}\"\n"
            f"3. Cuando la IA termine de escribir los archivos, ejecuta la validación determinística con:\n"
            f"   -> {validate_hint}"
        )

    def execute_task(
        self,
        context_file: Path,
        task_instruction: str,
        command_hint: str = "",
    ) -> Tuple[bool, str]:
        """
        Ejecuta la tarea mediante el CLI de la IA configurada o frena con error claro si el binario no existe.
        En modo manual explícito, informa cómo continuar con --validate.
        """
        if self.is_manual_mode():
            return True, self.format_manual_instructions(context_file, task_instruction, command_hint)

        resolved_bin = self.resolve_binary()
        if not resolved_bin:
            err_msg = (
                f"[ERROR: IA NO ENCONTRADA] No se encontró el ejecutable '{self.binary}' "
                f"(proveedor configurado: '{self.provider}') en el PATH del sistema.\n"
                "El harness se detuvo para no dar el paso por válido sin haber ejecutado la IA.\n"
                "Opciones para continuar:\n"
                "  1. Configura tu IA instalada en 'config.local.yaml' (ej. provider: claude | codex | gemini | aider | antigravity).\n"
                "  2. O ejecuta en modo manual explícito agregando '--manual' al comando, usa el contexto generado en\n"
                f"     '{context_file}' desde tu IDE/chat, y luego valida con '--validate'."
            )
            return False, err_msg

        cmd = [resolved_bin]
        for arg in self.args_template:
            cmd.append(
                arg.format(
                    context_file=str(context_file),
                    instruction=task_instruction,
                    project_root=str(self.project_root),
                )
            )

        stdin_payload: Optional[str] = None
        if self.use_stdin or not self.args_template:
            stdin_payload = (
                context_file.read_text(encoding="utf-8")
                + f"\n\n---\n## INSTRUCCIÓN DE TAREA ACTIVA\n{task_instruction}\n"
            )

        try:
            proc = subprocess.run(
                cmd,
                cwd=self.project_root,
                input=stdin_payload,
                shell=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout_seconds,
                check=False,
            )
            output = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()
            if proc.returncode != 0:
                hint = (
                    f"\n[DIAGNÓSTICO ADAPTADOR] El comando {cmd} falló con código {proc.returncode}. "
                    "Si tu versión del CLI no soporta los flags en 'args_template', vacíalos ('args_template: []') "
                    "y activa 'use_stdin: true' en 'config.local.yaml', o usa '--manual'."
                )
                return False, (output + hint).strip()
            return True, output
        except subprocess.TimeoutExpired:
            return False, f"El agente CLI ('{self.binary}') excedió el timeout de {self.timeout_seconds} segundos."
        except FileNotFoundError as err:
            return False, f"No se pudo invocar el ejecutable '{resolved_bin}': {err}"


# Alias retrocompatible
AntigravityAdapter = UniversalAgentAdapter
