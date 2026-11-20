import sys
import ast
import argparse
from pathlib import Path
from datetime import datetime

try:
    import yaml
except ImportError:
    yaml = None

from core.workflow import WorkflowStateMachine, WorkflowError
from core.context_manager import ContextManager
from core.hooks import HarnessHooks
from adapters.antigravity import AntigravityAdapter


def _load_yaml_config(text: str) -> dict:
    if yaml is not None:
        return yaml.safe_load(text)
    result: dict = {}
    current_section = result
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        is_indented = raw_line.startswith((" ", "\t"))
        key, _, val = line.partition(":")
        key = key.strip()
        val = val.strip()
        if not val:
            result[key] = {}
            current_section = result[key]
            continue
        target = current_section if is_indented else result
        if val.lower() == "true":
            parsed_val = True
        elif val.lower() == "false":
            parsed_val = False
        else:
            try:
                parsed_val = ast.literal_eval(val)
            except (ValueError, SyntaxError):
                parsed_val = val.strip("\"'")
        target[key] = parsed_val
    return result


def load_harness(require_project_root: bool = True):
    harness_root = Path(__file__).parent.resolve()
    cfg_path = harness_root / "config.yaml"
    if not cfg_path.exists():
        raise FileNotFoundError(f"Falta archivo de configuración: {cfg_path}")

    config = _load_yaml_config(cfg_path.read_text(encoding="utf-8"))
    project_root = (harness_root / config.get("project_root", ".")).resolve()
    if not project_root.exists():
        if require_project_root:
            raise FileNotFoundError(
                f"El directorio project_root '{project_root}' configurado en config.yaml no existe."
            )
        print(
            f"[ADVERTENCIA] El directorio project_root '{project_root}' configurado en config.yaml aún no existe en disco."
        )

    fsm = WorkflowStateMachine(harness_root)
    ctx_mgr = ContextManager(
        harness_root,
        project_root,
        config.get("limits", {}).get("allowed_doc_extensions", [".md", ".sql", ".json", ".yaml"]),
    )
    hooks = HarnessHooks(harness_root, project_root, config)
    adapter = AntigravityAdapter(harness_root, project_root, config.get("adapter", {}))
    return harness_root, project_root, config, fsm, ctx_mgr, hooks, adapter


def cmd_init() -> None:
    """Ejecuta la Fase 1: Ingesta segura a vault/ + Relevamiento con Skill 01 + Validación Hook F1."""
    harness_root, _, _, fsm, ctx_mgr, hooks, adapter = load_harness()
    kb = fsm.get_kb()

    # 1. Ingerir y sellar documentos de inbox/ en .state/vault/
    vault_manifest = ctx_mgr.ingest_inbox_to_vault(kb.get("vault_documents", {}))
    kb["vault_documents"] = vault_manifest
    fsm.save_kb(kb)

    vault_docs = ctx_mgr.read_vault_documents()
    taxonomia_path = harness_root / "skills" / "references" / "taxonomia_base.md"
    extra_sections = dict(vault_docs)
    if taxonomia_path.exists():
        extra_sections["REFERENCIA_TAXONOMIA_BASE"] = taxonomia_path.read_text(encoding="utf-8")

    # 2. Ensamblar contexto limpio para Fase 1
    report_rel = "reviews/01_informe_relevamiento.md"
    ctx_file = ctx_mgr.build_phase_context(
        phase_number=1,
        skill_rel_path="01_identificacion_relevamiento/SKILL.md",
        extra_sections=extra_sections,
        target_output_rel_path=report_rel,
    )

    # 3. Invocar adaptador Antigravity
    ok_run, out_msg = adapter.execute_task(
        ctx_file,
        f"Ejecuta la SKILL 01 sobre los documentos provistos y genera '{harness_root / report_rel}'.",
    )
    print(out_msg)
    if not ok_run:
        sys.exit(1)

    # 4. Validar salida con Hook F1 si el archivo ya fue escrito
    report_path = harness_root / report_rel
    if report_path.exists():
        valid, msg, catalog = hooks.validate_phase1_output(report_path, vault_docs)
        print(f"[HOOK FASE 1] {msg}")
        if not valid:
            sys.exit(1)
        kb["artifacts"] = catalog
        fsm.save_kb(kb)
        fsm.transition_to("PHASE1_DONE", "Relevamiento inicial validado por Hook F1.")
        feedback_file = harness_root / "reviews" / "01_feedback_desarrollador.md"
        if not feedback_file.exists():
            feedback_file.write_text(
                "# FEEDBACK Y DECISIONES DEL DESARROLLADOR (FASE 1)\n\n"
                "Responde aquí a las preguntas de `01_informe_relevamiento.md` antes de ejecutar `approve-phase1`.\n",
                encoding="utf-8",
            )
        print("Siguiente paso: Responde tus decisiones en 'reviews/01_feedback_desarrollador.md' y ejecuta: python main.py approve-phase1")


def cmd_approve_phase1() -> None:
    """Registra la aprobación humana del relevamiento y del feedback de la Fase 1."""
    harness_root, _, _, fsm, _, hooks, _ = load_harness()
    report_path = harness_root / "reviews" / "01_informe_relevamiento.md"
    feedback_path = harness_root / "reviews" / "01_feedback_desarrollador.md"

    if not report_path.exists() or not feedback_path.exists():
        raise WorkflowError("Faltan '01_informe_relevamiento.md' o '01_feedback_desarrollador.md' en reviews/.")

    state = fsm.get_state()
    state["approvals"]["phase1_approved_at"] = datetime.now().isoformat()
    fsm.save_state(state)
    fsm.transition_to("PHASE1_APPROVED", "Desarrollador aprobó el relevamiento y cargó su feedback.")
    hooks.log_event("PHASE_1", "HUMAN_APPROVED", "Feedback de Fase 1 confirmado por el desarrollador.")
    print("Fase 1 aprobada. Ahora puedes generar el plan modular con: python main.py plan")


def cmd_plan() -> None:
    """Ejecuta la Fase 2: Planificación Modular con Skill 02 + Validación de Cobertura Hook F2."""
    harness_root, _, _, fsm, ctx_mgr, hooks, adapter = load_harness()
    fsm.assert_can_run_phase2()

    report_text = (harness_root / "reviews" / "01_informe_relevamiento.md").read_text(encoding="utf-8")
    feedback_text = (harness_root / "reviews" / "01_feedback_desarrollador.md").read_text(encoding="utf-8")
    kb = fsm.get_kb()

    plan_rel = "reviews/02_plan_modular.md"
    ctx_file = ctx_mgr.build_phase_context(
        phase_number=2,
        skill_rel_path="02_planificacion_modular/SKILL.md",
        extra_sections={
            "01_INFORME_RELEVAMIENTO": report_text,
            "01_FEEDBACK_DESARROLLADOR_APROBADO": feedback_text,
            "IDS_NORMATIVOS_OBLIGATORIOS": "\n".join(kb["artifacts"].get("approved_ids", [])),
        },
        target_output_rel_path=plan_rel,
    )

    ok_run, out_msg = adapter.execute_task(
        ctx_file,
        f"Ejecuta la SKILL 02 integrando el feedback del desarrollador y genera '{harness_root / plan_rel}'.",
    )
    print(out_msg)
    if not ok_run:
        sys.exit(1)

    plan_path = harness_root / plan_rel
    if plan_path.exists():
        valid, msg, modules_plan = hooks.validate_phase2_output(
            plan_path, kb["artifacts"].get("approved_ids", [])
        )
        print(f"[HOOK FASE 2] {msg}")
        if not valid:
            sys.exit(1)
        kb["modules_plan"] = modules_plan
        fsm.save_kb(kb)
        fsm.transition_to("PHASE2_DONE", f"Plan modular validado ({len(modules_plan)} módulos).")
        print("Siguiente paso: Revisa 'reviews/02_plan_modular.md' y aprueba con: python main.py approve-plan")


def cmd_approve_plan() -> None:
    """Compuerta humana que aprueba el Plan Modular e inicializa el seguimiento de cada módulo."""
    harness_root, _, _, fsm, _, hooks, _ = load_harness()
    plan_path = harness_root / "reviews" / "02_plan_modular.md"
    kb = fsm.get_kb()

    valid, msg, modules_plan = hooks.validate_phase2_output(
        plan_path, kb["artifacts"].get("approved_ids", [])
    )
    if not valid:
        raise WorkflowError(f"No se puede aprobar el plan porque tiene errores de validación: {msg}")

    kb["modules_plan"] = modules_plan
    fsm.save_kb(kb)

    state = fsm.get_state()
    state["approvals"]["plan_approved_at"] = datetime.now().isoformat()
    for mod_id in modules_plan:
        if mod_id not in state["modules"]:
            state["modules"][mod_id] = {
                "status": "PENDING",
                "retries": 0,
                "snapshot_head": None,
            }
    fsm.save_state(state)
    fsm.transition_to("PLAN_APPROVED", f"Plan aprobado por el desarrollador ({list(modules_plan.keys())}).")
    hooks.log_event("PHASE_2", "HUMAN_APPROVED", f"Módulos habilitados: {list(modules_plan.keys())}")
    print(f"Plan aprobado. Módulos listos para construir en secuencia: {list(modules_plan.keys())}")


def cmd_build(module_id: str) -> None:
    """Ejecuta la Fase 3 para un módulo puntual con snapshot seguro, Skill 03 y circuit breaker de 3 intentos."""
    harness_root, _, config, fsm, ctx_mgr, hooks, adapter = load_harness()
    mod_spec = fsm.assert_can_build_module(module_id)

    state = fsm.get_state()
    mod_state = state["modules"][module_id]
    max_retries = int(config.get("limits", {}).get("max_module_retries", 3))

    # 1. Crear snapshot seguro antes de tocar el repositorio
    require_clean = bool(config.get("limits", {}).get("require_clean_git_before_build", True))
    if mod_state.get("retries", 0) == 0:
        snap = hooks.prepare_pre_module_snapshot(module_id, require_clean=require_clean)
        mod_state["snapshot_head"] = snap["snapshot_head"]

    mod_state["status"] = "IN_PROGRESS"
    fsm.save_state(state)
    fsm.transition_to("BUILDING_MODULES", f"Construyendo módulo {module_id}")

    # 2. Construir contexto aislado exclusivamente para este módulo
    plan_text = (harness_root / "reviews" / "02_plan_modular.md").read_text(encoding="utf-8")
    feedback_text = (harness_root / "reviews" / "01_feedback_desarrollador.md").read_text(encoding="utf-8")
    ajustes_path = harness_root / "reviews" / "03_ajustes_modulos.md"
    ajustes_text = ajustes_path.read_text(encoding="utf-8") if ajustes_path.exists() else "Sin ajustes pendientes."

    ctx_file = ctx_mgr.build_phase_context(
        phase_number=3,
        skill_rel_path="03_desarrollo_y_unit_test/SKILL.md",
        extra_sections={
            f"ESPECIFICACION_MODULO_{module_id}": str(mod_spec),
            "PLAN_MODULAR_APROBADO": plan_text,
            "DECISIONES_FEEDBACK_FASE_1": feedback_text,
            "AJUSTES_DEL_DESARROLLADOR_FASE_3": ajustes_text,
        },
        target_output_rel_path=f"Modulo {module_id} en allowed_paths: {mod_spec['allowed_paths']}",
    )

    ok_run, out_msg = adapter.execute_task(
        ctx_file,
        f"Ejecuta la SKILL 03 para implementar y crear los unit tests exclusivamente del módulo '{module_id}' "
        f"dentro de las rutas autorizadas: {mod_spec['allowed_paths']}.",
    )
    print(out_msg)

    # 3. Validar alcance Git, código limpio y ejecución de pruebas unitarias
    valid, hook_msg, details = hooks.validate_phase3_module(
        module_id, mod_spec["allowed_paths"], out_msg
    )
    print(f"[HOOK FASE 3 - {module_id}] {hook_msg}")

    state = fsm.get_state()
    mod_state = state["modules"][module_id]

    if valid:
        mod_state["status"] = "TESTED"
        mod_state["last_validation"] = details
        fsm.save_state(state)
        kb = fsm.get_kb()
        kb["test_executions"].append({
            "module_id": module_id,
            "timestamp": datetime.now().isoformat(),
            "details": details,
        })
        fsm.save_kb(kb)
        print(
            f"Módulo '{module_id}' superó las pruebas unitarias. Pruébalo en tu entorno y luego "
            f"confirma con: python main.py approve-module --module {module_id}"
        )
    else:
        mod_state["retries"] = mod_state.get("retries", 0) + 1
        if details.get("escalated") or mod_state["retries"] >= max_retries:
            mod_state["status"] = "ESCALATED"
            fsm.save_state(state)
            fsm.transition_to(
                "ESCALATED_TO_HUMAN",
                f"Módulo {module_id} escalado tras {mod_state['retries']} intentos.",
            )
            print(
                f"[CIRCUIT BREAKER / ESCALAMIENTO] El módulo '{module_id}' alcanzó {mod_state['retries']} "
                "fallos o solicitó intervención humana. Revisa los archivos o restaura con: "
                f"python main.py rollback --module {module_id}"
            )
        else:
            fsm.save_state(state)
            print(f"Intento {mod_state['retries']}/{max_retries} fallido para '{module_id}'.")
        sys.exit(1)


def cmd_approve_module(module_id: str) -> None:
    """Aprueba un módulo ya testeado en Fase 3 tras la revisión del desarrollador."""
    _, _, _, fsm, _, hooks, _ = load_harness()
    state = fsm.get_state()
    if module_id not in state["modules"]:
        raise WorkflowError(f"El módulo '{module_id}' no figura en el estado actual.")

    mod_state = state["modules"][module_id]
    if mod_state.get("status") not in ["TESTED", "APPROVED"]:
        raise WorkflowError(
            f"No puedes aprobar el módulo '{module_id}' porque su estado es '{mod_state.get('status')}'. "
            "Debe superar primero las validaciones de 'python main.py build --module "
            f"{module_id}'."
        )

    mod_state["status"] = "APPROVED"
    mod_state["approved_at"] = datetime.now().isoformat()
    fsm.save_state(state)
    hooks.log_event("PHASE_3", "MODULE_APPROVED", f"Desarrollador aprobó módulo {module_id}.")

    pending = [m for m, d in state["modules"].items() if d.get("status") != "APPROVED"]
    if not pending:
        fsm.transition_to("MODULES_COMPLETED", "Todos los módulos fueron aprobados en Fase 3.")
        print("Todos los módulos han sido aprobados. Ejecuta la Fase 4 con: python main.py verify")
    else:
        print(f"Módulo '{module_id}' aprobado. Módulos pendientes: {pending}")


def cmd_rollback(module_id: str) -> None:
    """Informa o asiste en el reinicio seguro del contador de un módulo escalado tras verificar el estado Git."""
    _, project_root, _, fsm, _, hooks, _ = load_harness()
    state = fsm.get_state()
    if module_id not in state["modules"]:
        raise WorkflowError(f"Módulo '{module_id}' desconocido.")

    dirty = hooks.get_git_modified_and_untracked()
    snap_head = state["modules"][module_id].get("snapshot_head")
    print(f"Estado actual de '{module_id}': snapshot_head={snap_head}")
    if dirty:
        print(
            f"Archivos modificados actualmente en '{project_root}': {dirty}\n"
            "Por seguridad no destructiva, descarta o guarda manualmente en Zed los cambios que no desees conservar "
            "y vuelve a ejecutar 'rollback' con el árbol limpio para reiniciar el módulo."
        )
        return

    state["modules"][module_id]["status"] = "PENDING"
    state["modules"][module_id]["retries"] = 0
    fsm.save_state(state)
    fsm.transition_to("BUILDING_MODULES", f"Rollback limpio completado para {module_id}")
    hooks.log_event("PHASE_3", "ROLLBACK", f"Módulo {module_id} reiniciado a PENDING.")
    print(f"Módulo '{module_id}' reiniciado a estado PENDING con 0 reintentos.")


def cmd_verify() -> None:
    """Ejecuta la Fase 4: Contraste Final y Suite Global de Regresión en sesión limpia."""
    harness_root, _, _, fsm, ctx_mgr, hooks, adapter = load_harness()
    fsm.assert_can_run_phase4()

    kb = fsm.get_kb()
    vault_docs = ctx_mgr.read_vault_documents()
    feedback_text = (harness_root / "reviews" / "01_feedback_desarrollador.md").read_text(encoding="utf-8")

    report_rel = "reviews/04_reporte_contraste_final.md"
    extra_sections = dict(vault_docs)
    extra_sections["FEEDBACK_APROBADO_DESARROLLADOR"] = feedback_text
    extra_sections["IDS_APROBADOS_A_CONTRASTAR"] = "\n".join(kb["artifacts"].get("approved_ids", []))

    ctx_file = ctx_mgr.build_phase_context(
        phase_number=4,
        skill_rel_path="04_contraste_y_regresion/SKILL.md",
        extra_sections=extra_sections,
        target_output_rel_path=report_rel,
    )

    ok_run, out_msg = adapter.execute_task(
        ctx_file,
        f"Ejecuta la SKILL 04 en sesión limpia de auditoría y genera '{harness_root / report_rel}'.",
    )
    print(out_msg)
    if not ok_run:
        sys.exit(1)

    report_path = harness_root / report_rel
    if report_path.exists():
        valid, msg = hooks.validate_phase4_output(
            report_path, kb["artifacts"].get("approved_ids", [])
        )
        print(f"[HOOK FASE 4] {msg}")
        if not valid:
            sys.exit(1)
        state = fsm.get_state()
        state["approvals"]["final_verified_at"] = datetime.now().isoformat()
        fsm.save_state(state)
        fsm.transition_to("VERIFIED", "Contraste final y pruebas de regresión aprobadas.")
        print("Aplicación validada al 100% y lista para su puesta en uso operativo.")


def cmd_status() -> None:
    """Muestra el estado actual de la máquina de estados, aprobaciones y avance por módulo."""
    _, project_root, _, fsm, _, _, _ = load_harness(require_project_root=False)
    state = fsm.get_state()
    kb = fsm.get_kb()
    print("=== ESTADO DEL AGENT HARNESS ===")
    print(f"Proyecto Destino (project_root): {project_root}")
    print(f"Estado Actual FSM              : {state['current_state']}")
    print(f"Documentos en Vault            : {len(kb.get('vault_documents', {}))}")
    print(f"IDs Aprobados Indexados        : {len(kb.get('artifacts', {}).get('approved_ids', []))}")
    print("Avance de Módulos:")
    for mod_id, m_data in state.get("modules", {}).items():
        print(f"  - {mod_id}: {m_data.get('status')} (Reintentos: {m_data.get('retries', 0)})")


def main() -> None:
    parser = argparse.ArgumentParser(description="Agent Harness Determinístico de 4 Fases")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("init", help="Fase 1: Ingesta documental y relevamiento inicial")
    subparsers.add_parser("approve-phase1", help="Aprueba el relevamiento y feedback de la Fase 1")
    subparsers.add_parser("plan", help="Fase 2: Genera el plan de implementación por módulos")
    subparsers.add_parser("approve-plan", help="Aprueba el plan modular para habilitar la Fase 3")

    p_build = subparsers.add_parser("build", help="Fase 3: Desarrolla y testea un módulo específico")
    p_build.add_argument("--module", required=True, help="ID del módulo (ej. MOD-01)")

    p_app_mod = subparsers.add_parser("approve-module", help="Marca un módulo testeado como aprobado")
    p_app_mod.add_argument("--module", required=True, help="ID del módulo (ej. MOD-01)")

    p_roll = subparsers.add_parser("rollback", help="Reinicia de forma segura un módulo escalado")
    p_roll.add_argument("--module", required=True, help="ID del módulo (ej. MOD-01)")

    subparsers.add_parser("verify", help="Fase 4: Ejecuta regresión global y contraste contra requerimientos")
    subparsers.add_parser("status", help="Muestra el estado actual del proyecto y de los módulos")

    args = parser.parse_args()
    try:
        if args.command == "init":
            cmd_init()
        elif args.command == "approve-phase1":
            cmd_approve_phase1()
        elif args.command == "plan":
            cmd_plan()
        elif args.command == "approve-plan":
            cmd_approve_plan()
        elif args.command == "build":
            cmd_build(args.module)
        elif args.command == "approve-module":
            cmd_approve_module(args.module)
        elif args.command == "rollback":
            cmd_rollback(args.module)
        elif args.command == "verify":
            cmd_verify()
        elif args.command == "status":
            cmd_status()
    except (WorkflowError, Exception) as err:
        print(f"[ERROR HARNESS] {err}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
