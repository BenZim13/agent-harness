import os
import json
import uuid
import socket
import getpass
import subprocess
from pathlib import Path
from datetime import datetime
from contextlib import contextmanager
from typing import Dict, Any, List, Optional, Iterator


class WorkflowError(Exception):
    """Excepción lanzada ante transiciones de fase ilegales, bloqueos de concurrencia o gobernanza."""
    pass


def resolve_actor(project_root: Optional[Path] = None, config: Optional[Dict[str, Any]] = None) -> str:
    """
    Determina la identidad del desarrollador que ejecuta la acción para trazabilidad en equipo.
    Prioridad:
    1. Variable de entorno HARNESS_USER
    2. Configuración personal developer.name / developer.email en config.local.yaml
    3. git config user.name y user.email en el repositorio
    4. Usuario del sistema operativo y hostname
    """
    env_user = os.environ.get("HARNESS_USER", "").strip()
    if env_user:
        return env_user

    if config:
        dev_cfg = config.get("developer", {})
        if isinstance(dev_cfg, dict):
            dev_name = str(dev_cfg.get("name", "")).strip()
            dev_email = str(dev_cfg.get("email", "")).strip()
            if dev_name and dev_email:
                return f"{dev_name} <{dev_email}>"
            if dev_name:
                return dev_name

    cwd = project_root if (project_root and project_root.exists()) else Path.cwd()
    git_name = ""
    git_email = ""
    try:
        res_name = subprocess.run(
            ["git", "config", "user.name"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if res_name.returncode == 0:
            git_name = res_name.stdout.strip()

        res_email = subprocess.run(
            ["git", "config", "user.email"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if res_email.returncode == 0:
            git_email = res_email.stdout.strip()
    except Exception:
        pass

    if git_name and git_email:
        return f"{git_name} <{git_email}>"
    if git_name:
        return git_name

    try:
        os_user = getpass.getuser()
    except Exception:
        os_user = "unknown"
    try:
        host = socket.gethostname()
    except Exception:
        host = "localhost"
    return f"{os_user}@{host}"


def _is_pid_running(pid: int) -> bool:
    """Verifica si un PID local sigue vivo tanto en Windows como en POSIX."""
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            res = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            return str(pid) in res.stdout
        except Exception:
            return False
    else:
        try:
            os.kill(pid, 0)
            return True
        except PermissionError:
            return True
        except OSError:
            return False


class WorkflowStateMachine:
    """
    Gobierna de forma determinística el estado compartido del harness en workflow_state.json
    y el índice de conocimiento en kb_index.json, con:
    - Escritura atómica (temp + fsync + os.replace) y respaldo .bak contra cortes.
    - Lock de concurrencia (.state/harness.lock) para evitar pisadas simultáneas.
    - Atribución de autoría (actor) en cada transición y aprobación.
    """

    VALID_STATES = [
        "IDLE",
        "PHASE1_DONE",
        "PHASE1_APPROVED",
        "PHASE2_DONE",
        "PLAN_APPROVED",
        "BUILDING_MODULES",
        "MODULES_COMPLETED",
        "VERIFIED",
        "ESCALATED_TO_HUMAN",
    ]

    def __init__(
        self,
        harness_root: Path,
        state_dir: Optional[Path] = None,
        actor: Optional[str] = None,
    ):
        self.harness_root = harness_root.resolve()
        self.state_dir = (state_dir or (self.harness_root / ".state")).resolve()
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.actor = actor or resolve_actor(self.harness_root)
        self.state_file = self.state_dir / "workflow_state.json"
        self.kb_file = self.state_dir / "kb_index.json"
        self.lock_file = self.state_dir / "harness.lock"
        self._ensure_files()

    def _ensure_files(self) -> None:
        if not self.state_file.exists():
            initial_state = {
                "version": 1,
                "current_state": "IDLE",
                "updated_at": datetime.now().isoformat(),
                "updated_by": self.actor,
                "approvals": {
                    "phase1_approved_at": None,
                    "phase1_approved_by": None,
                    "plan_approved_at": None,
                    "plan_approved_by": None,
                    "final_verified_at": None,
                    "final_verified_by": None,
                },
                "modules": {},
                "history": [],
            }
            self._write_json(self.state_file, initial_state)

        if not self.kb_file.exists():
            initial_kb = {
                "vault_documents": {},
                "artifacts": {
                    "approved_ids": [],
                    "pending_ids": [],
                    "referenced_unknown_ids": [],
                    "formal_ids_used": True,
                },
                "modules_plan": {},
                "test_executions": [],
            }
            self._write_json(self.kb_file, initial_kb)

    @staticmethod
    def _read_json(path: Path) -> Dict[str, Any]:
        bak_path = path.with_name(f"{path.name}.bak")
        try:
            text = path.read_text(encoding="utf-8")
            if not text.strip():
                raise ValueError("Archivo JSON vacío")
            return json.loads(text)
        except Exception as err:
            if bak_path.exists():
                try:
                    recovered = json.loads(bak_path.read_text(encoding="utf-8"))
                    # Restaurar automáticamente el archivo corrupto desde el backup válido
                    WorkflowStateMachine._write_json(path, recovered)
                    return recovered
                except Exception:
                    pass
            raise WorkflowError(
                f"El archivo de estado '{path}' está corrupto o ilegible ({err}) y no pudo recuperarse."
            ) from err

    @staticmethod
    def _write_json(path: Path, data: Dict[str, Any]) -> None:
        """
        Escritura atómica segura ante cortes de energía o interrupciones (Ctrl+C):
        1. Escribe en un archivo temporal en el mismo directorio y hace fsync.
        2. Guarda copia .bak del estado válido anterior.
        3. Reemplaza atómicamente con os.replace().
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
        tmp_path = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp")
        bak_path = path.with_name(f"{path.name}.bak")

        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                f.write(payload)
                f.flush()
                os.fsync(f.fileno())

            if path.exists():
                try:
                    existing_text = path.read_text(encoding="utf-8")
                    if existing_text.strip():
                        json.loads(existing_text)
                        bak_tmp = path.with_name(f".{path.name}.bak.tmp")
                        bak_tmp.write_text(existing_text, encoding="utf-8")
                        os.replace(bak_tmp, bak_path)
                except Exception:
                    pass

            os.replace(tmp_path, path)
        finally:
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except OSError:
                    pass

    @contextmanager
    def lock(self, command_name: str = "cli", stale_seconds: int = 1800) -> Iterator[None]:
        """
        Bloqueo de concurrencia basado en archivo exclusivo (.state/harness.lock)
        para impedir que dos ejecuciones simultáneas pisen el estado.
        """
        self.state_dir.mkdir(parents=True, exist_ok=True)
        host = socket.gethostname()
        lock_payload = {
            "pid": os.getpid(),
            "actor": self.actor,
            "host": host,
            "command": command_name,
            "acquired_at": datetime.now().isoformat(),
        }

        for _ in range(2):
            try:
                fd = os.open(str(self.lock_file), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(json.dumps(lock_payload, indent=2, ensure_ascii=False))
                    f.flush()
                break
            except FileExistsError:
                # Verificar si el lock existente es huérfano (PID muerto o expirado)
                is_stale = False
                existing_info: Dict[str, Any] = {}
                try:
                    existing_info = json.loads(self.lock_file.read_text(encoding="utf-8"))
                    lock_pid = int(existing_info.get("pid", -1))
                    lock_host = existing_info.get("host", "")
                    acquired_str = existing_info.get("acquired_at", "")
                    if lock_host == host and not _is_pid_running(lock_pid):
                        is_stale = True
                    elif acquired_str:
                        age = (datetime.now() - datetime.fromisoformat(acquired_str)).total_seconds()
                        if age > stale_seconds:
                            is_stale = True
                except Exception:
                    is_stale = True

                if is_stale:
                    try:
                        self.lock_file.unlink()
                        continue
                    except OSError:
                        pass

                holder = existing_info.get("actor", "otro proceso")
                cmd = existing_info.get("command", "desconocido")
                pid = existing_info.get("pid", "?")
                since = existing_info.get("acquired_at", "?")
                raise WorkflowError(
                    f"Bloqueo de concurrencia: El harness está siendo ejecutado por '{holder}' "
                    f"(comando '{cmd}', PID {pid}, iniciado en {since}). "
                    f"Espera a que finalice o elimina '{self.lock_file}' si el proceso murió."
                )

        try:
            yield
        finally:
            try:
                if self.lock_file.exists():
                    info = json.loads(self.lock_file.read_text(encoding="utf-8"))
                    if info.get("pid") == os.getpid():
                        self.lock_file.unlink()
            except Exception:
                pass

    def get_state(self) -> Dict[str, Any]:
        state = self._read_json(self.state_file)
        state.setdefault("version", 1)
        state.setdefault("updated_by", self.actor)
        state.setdefault("approvals", {})
        state.setdefault("modules", {})
        state.setdefault("history", [])
        return state

    def save_state(self, state: Dict[str, Any]) -> None:
        state["version"] = int(state.get("version", 0)) + 1
        state["updated_at"] = datetime.now().isoformat()
        state["updated_by"] = self.actor
        self._write_json(self.state_file, state)

    def get_kb(self) -> Dict[str, Any]:
        return self._read_json(self.kb_file)

    def save_kb(self, kb: Dict[str, Any]) -> None:
        self._write_json(self.kb_file, kb)

    def transition_to(self, new_state: str, reason: str, actor: Optional[str] = None) -> None:
        if new_state not in self.VALID_STATES:
            raise WorkflowError(f"Estado inválido: {new_state}")
        who = actor or self.actor
        state = self.get_state()
        old_state = state["current_state"]
        state["current_state"] = new_state
        state["history"].append({
            "timestamp": datetime.now().isoformat(),
            "actor": who,
            "from": old_state,
            "to": new_state,
            "reason": reason,
        })
        self.save_state(state)

    # --- Guardas de Transición entre Fases ---

    def assert_can_run_phase2(self) -> None:
        state = self.get_state()
        if state["current_state"] not in [
            "PHASE1_APPROVED",
            "PHASE2_DONE",
            "PLAN_APPROVED",
            "BUILDING_MODULES",
            "ESCALATED_TO_HUMAN",
        ]:
            raise WorkflowError(
                f"Bloqueo de flujo: No puedes ejecutar la Fase 2 desde el estado '{state['current_state']}'. "
                "Primero debes completar la Fase 1, responder en 'reviews/01_feedback_desarrollador.md' "
                "y ejecutar: python main.py approve-phase1"
            )

    def assert_can_build_module(self, module_id: str) -> Dict[str, Any]:
        state = self.get_state()
        if state["current_state"] not in [
            "PLAN_APPROVED",
            "BUILDING_MODULES",
            "ESCALATED_TO_HUMAN",
            "MODULES_COMPLETED",
        ]:
            raise WorkflowError(
                f"Bloqueo de flujo: No puedes construir módulos desde el estado '{state['current_state']}'. "
                "El plan modular debe estar aprobado mediante: python main.py approve-plan"
            )
        kb = self.get_kb()
        modules_plan = kb.get("modules_plan", {})
        if module_id not in modules_plan:
            raise WorkflowError(
                f"El módulo '{module_id}' no existe en el plan aprobado. "
                f"Módulos válidos: {list(modules_plan.keys())}"
            )
        mod_state = state["modules"].get(module_id, {})
        if mod_state.get("status") == "ESCALATED":
            raise WorkflowError(
                f"El módulo '{module_id}' se encuentra ESCALADO al desarrollador tras superar el límite de fallos "
                "o requerir cambios fuera de alcance. Ejecuta 'python main.py rollback --module "
                f"{module_id}' o ajusta 'reviews/03_ajustes_modulos.md' antes de reintentar."
            )
        return modules_plan[module_id]

    def assert_can_run_phase4(self) -> None:
        state = self.get_state()
        modules = state.get("modules", {})
        if not modules:
            raise WorkflowError("No hay módulos registrados en el plan.")
        unapproved = [m_id for m_id, data in modules.items() if data.get("status") != "APPROVED"]
        if unapproved:
            raise WorkflowError(
                "Bloqueo de flujo: No puedes ejecutar la Fase 4 (Contraste Final y Regresión) "
                f"porque los siguientes módulos aún no fueron probados y aprobados: {unapproved}. "
                "Usa 'python main.py approve-module --module <ID>' cuando termines de probar cada uno."
            )
