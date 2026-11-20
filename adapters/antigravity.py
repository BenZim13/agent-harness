import shutil
import subprocess
from pathlib import Path
from typing import Dict, Any, Tuple


class AntigravityAdapter:
    """
    Adaptador para Antigravity CLI (agy) ejecutado desde la terminal integrada de Zed.
    - NO sobrescribe .antigravityrules ni ensucia la configuración del repositorio del proyecto.
    - Envía el paquete .state/active_context.md mediante los argumentos configurados o stdin.
    """

    def __init__(self, harness_root: Path, project_root: Path, adapter_cfg: Dict[str, Any]):
        self.harness_root = harness_root.resolve()
        self.project_root = project_root.resolve()
        self.binary = adapter_cfg.get("binary", "agy")
        self.args_template = adapter_cfg.get("args_template", ["--prompt-file", "{context_file}"])
        self.use_stdin = bool(adapter_cfg.get("use_stdin", False))
        self.timeout_seconds = int(adapter_cfg.get("timeout_seconds", 900))

    def is_binary_available(self) -> bool:
        return shutil.which(self.binary) is not None

    def execute_task(self, context_file: Path, task_instruction: str) -> Tuple[bool, str]:
        """
        Invoca a Antigravity CLI en el directorio project_root pasándole el contexto limpio.
        Si el usuario ejecuta el flujo en modo asistido dentro del panel ACP de Zed sin binario headless,
        imprime la ruta exacta del paquete de contexto preparado.
        """
        if not self.is_binary_available():
            msg = (
                f"[MODO ASISTIDO ZED / ACP] No se detectó el binario '{self.binary}' en el PATH.\n"
                f"El contexto limpio de la fase ya fue generado en:\n  -> {context_file}\n"
                f"Instrucción para el agente en Zed ACP:\n  \"{task_instruction}\"\n"
                "Una vez que Antigravity genere el entregable en Zed, vuelve a ejecutar el comando de validación."
            )
            return True, msg

        cmd = [self.binary]
        for arg in self.args_template:
            cmd.append(arg.format(context_file=str(context_file), instruction=task_instruction))

        stdin_payload = None
        if self.use_stdin:
            stdin_payload = context_file.read_text(encoding="utf-8") + f"\n\nTAREA:\n{task_instruction}\n"

        try:
            proc = subprocess.run(
                cmd,
                cwd=self.project_root,
                input=stdin_payload,
                shell=False,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                check=False,
            )
            output = (proc.stdout or "") + "\n" + (proc.stderr or "")
            return proc.returncode == 0, output.strip()
        except subprocess.TimeoutExpired:
            return False, f"Antigravity CLI excedió el timeout de {self.timeout_seconds} segundos."
