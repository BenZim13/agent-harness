import json
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, List, Optional


class WorkflowError(Exception):
    """Excepción lanzada ante transiciones de fase ilegales o bloqueos de gobernanza."""
    pass


class WorkflowStateMachine:
    """
    Gobierna de forma determinística el estado del harness en .state/workflow_state.json
    y el índice de conocimiento en .state/kb_index.json.
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

    def __init__(self, harness_root: Path):
        self.harness_root = harness_root.resolve()
        self.state_dir = self.harness_root / ".state"
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.state_file = self.state_dir / "workflow_state.json"
        self.kb_file = self.state_dir / "kb_index.json"
        self._ensure_files()

    def _ensure_files(self) -> None:
        if not self.state_file.exists():
            initial_state = {
                "current_state": "IDLE",
                "updated_at": datetime.now().isoformat(),
                "approvals": {
                    "phase1_approved_at": None,
                    "plan_approved_at": None,
                    "final_verified_at": None,
                },
                "modules": {},  # { "MOD-01": {"status": "PENDING|IN_PROGRESS|TESTED|APPROVED|ESCALATED", "retries": 0, "snapshot_head": None, "stash_ref": None} }
                "history": [],
            }
            self._write_json(self.state_file, initial_state)

        if not self.kb_file.exists():
            initial_kb = {
                "vault_documents": {},  # { "rel_path": {"sha256": "...", "ingested_at": "..."} }
                "artifacts": {
                    "approved_ids": [],
                    "pending_ids": [],
                    "referenced_unknown_ids": [],
                },
                "modules_plan": {},  # { "MOD-01": {"order": 1, "assigned_ids": [], "allowed_paths": []} }
                "test_executions": [],
            }
            self._write_json(self.kb_file, initial_kb)

    @staticmethod
    def _read_json(path: Path) -> Dict[str, Any]:
        return json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def _write_json(path: Path, data: Dict[str, Any]) -> None:
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    def get_state(self) -> Dict[str, Any]:
        return self._read_json(self.state_file)

    def save_state(self, state: Dict[str, Any]) -> None:
        state["updated_at"] = datetime.now().isoformat()
        self._write_json(self.state_file, state)

    def get_kb(self) -> Dict[str, Any]:
        return self._read_json(self.kb_file)

    def save_kb(self, kb: Dict[str, Any]) -> None:
        self._write_json(self.kb_file, kb)

    def transition_to(self, new_state: str, reason: str) -> None:
        if new_state not in self.VALID_STATES:
            raise WorkflowError(f"Estado inválido: {new_state}")
        state = self.get_state()
        old_state = state["current_state"]
        state["current_state"] = new_state
        state["history"].append({
            "timestamp": datetime.now().isoformat(),
            "from": old_state,
            "to": new_state,
            "reason": reason,
        })
        self.save_state(state)

    # --- Guardas de Transición entre Fases ---

    def assert_can_run_phase2(self) -> None:
        state = self.get_state()
        if state["current_state"] not in ["PHASE1_APPROVED", "PHASE2_DONE", "PLAN_APPROVED"]:
            raise WorkflowError(
                f"Bloqueo de flujo: No puedes ejecutar la Fase 2 desde el estado '{state['current_state']}'. "
                "Primero debes completar la Fase 1, responder en 'reviews/01_feedback_desarrollador.md' "
                "y ejecutar: python main.py approve-phase1"
            )

    def assert_can_build_module(self, module_id: str) -> Dict[str, Any]:
        state = self.get_state()
        if state["current_state"] not in ["PLAN_APPROVED", "BUILDING_MODULES"]:
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
