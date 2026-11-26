import os
import sys
import ast
import argparse
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, Tuple, Optional

try:
    import yaml
except ImportError:
    yaml = None

from core.workflow import WorkflowStateMachine, WorkflowError, resolve_actor
from core.context_manager import ContextManager
from core.hooks import HarnessHooks
from adapters.antigravity import UniversalAgentAdapter


FEEDBACK_TEMPLATE_HEADER = "# FEEDBACK Y DECISIONES DEL DESARROLLADOR (FASE 1)"
FEEDBACK_TEMPLATE_PROMPT = (
    "Responde aquí a las preguntas de `01_informe_relevamiento.md` antes de ejecutar `approve-phase1`."
)


def _strip_inline_yaml_comment(val: str) -> str:
    in_single = False
    in_double = False
    for idx, ch in enumerate(val):
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif ch == "#" and not in_single and not in_double:
            if idx == 0 or val[idx - 1].isspace():
                return val[:idx].strip()
    return val.strip()


def _load_yaml_config(text: str) -> dict:
    if yaml is not None:
        return yaml.safe_load(text) or {}
    result: dict = {}
    current_section = result
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        is_indented = raw_line.startswith((" ", "\t"))
        key, _, val = line.partition(":")
        key = key.strip()
        val = _strip_inline_yaml_comment(val)
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


def _deep_merge_dict(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(merged.get(k), dict):
            merged[k] = _deep_merge_dict(merged[k], v)
        else:
            merged[k] = v
    return merged


def _resolve_workspace_dirs(harness_root: Path, project_root: Path, config: Dict[str, Any]) -> Tuple[Path, Path, Path]:
    """
    Determina dónde viven .state/, reviews/ e inbox/ según state.location ('harness' o 'project') (Punto 1).
    """
    state_cfg = config.get("state", {}) if isinstance(config.get("state"), dict) else {}
    location = str(state_cfg.get("location", "harness")).strip().lower()
    if location == "project":
        base = project_root / ".harness"
        state_dir = base / ".state"
        reviews_dir = base / "reviews"
        inbox_dir = base / "inbox" if (base / "inbox").exists() else (harness_root / "inbox")
    else:
        state_dir = harness_root / ".state"
        reviews_dir = harness_root / "reviews"
        inbox_dir = harness_root / "inbox"

    state_dir.mkdir(parents=True, exist_ok=True)
    reviews_dir.mkdir(parents=True, exist_ok=True)
    return state_dir, reviews_dir, inbox_dir


def load_harness(
    require_project_root: bool = True,
    cli_overrides: Optional[Dict[str, Any]] = None,
):
    """
    Carga la configuración en capas (Punto 4):
    1. config.yaml (Base compartida en Git)
    2. <project_root>/.harness/config.yaml (Configuración específica del proyecto si existe)
    3. config.local.yaml (Configuración personal del desarrollador, ignorada por Git)
    4. Variables de entorno (HARNESS_PROJECT_ROOT, HARNESS_PROVIDER, HARNESS_MODE, HARNESS_USER) y flags CLI.
    """
    harness_root = Path(__file__).parent.resolve()
    cfg_path = harness_root / "config.yaml"
    if not cfg_path.exists():
        raise FileNotFoundError(f"Falta archivo de configuración base: {cfg_path}")

    config = _load_yaml_config(cfg_path.read_text(encoding="utf-8"))

    # Capa 2: config.local.yaml preliminar para resolver project_root local del dev
    local_cfg_path = harness_root / "config.local.yaml"
    local_cfg: Dict[str, Any] = {}
    if local_cfg_path.exists():
        local_cfg = _load_yaml_config(local_cfg_path.read_text(encoding="utf-8"))
        config = _deep_merge_dict(config, local_cfg)

    if cli_overrides and cli_overrides.get("project_root"):
        config["project_root"] = cli_overrides["project_root"]
    elif os.environ.get("HARNESS_PROJECT_ROOT"):
        config["project_root"] = os.environ["HARNESS_PROJECT_ROOT"]

    raw_proj = str(config.get("project_root", "."))
    proj_candidate = Path(raw_proj)
    project_root = proj_candidate.resolve() if proj_candidate.is_absolute() else (harness_root / proj_candidate).resolve()

    # Capa 3: <project_root>/.harness/config.yaml (si el proyecto define sus propios comandos de test)
    if project_root.exists():
        proj_cfg_path = project_root / ".harness" / "config.yaml"
        if proj_cfg_path.exists():
            proj_cfg = _load_yaml_config(proj_cfg_path.read_text(encoding="utf-8"))
            # project config sobrescribe base, pero config.local.yaml tiene la última palabra personal
            config = _deep_merge_dict(_deep_merge_dict(config, proj_cfg), local_cfg)

    if cli_overrides:
        if cli_overrides.get("provider"):
            config.setdefault("adapter", {})["provider"] = cli_overrides["provider"]
        if cli_overrides.get("manual"):
            config.setdefault("adapter", {})["mode"] = "manual"

    if not project_root.exists():
        if require_project_root:
            raise FileNotFoundError(
                f"El directorio project_root '{project_root}' no existe. "
                "Copia 'config.local.yaml.example' a 'config.local.yaml' y configura tu ruta local."
            )
        print(
            f"[ADVERTENCIA] El directorio project_root '{project_root}' aún no existe en disco."
        )

    actor = resolve_actor(project_root if project_root.exists() else harness_root, config)
    state_dir, reviews_dir, inbox_dir = _resolve_workspace_dirs(harness_root, project_root, config)

    fsm = WorkflowStateMachine(harness_root, state_dir=state_dir, actor=actor)
    ctx_mgr = ContextManager(
        harness_root,
        project_root,
        config.get("limits", {}).get("allowed_doc_extensions", [".md", ".sql", ".json", ".yaml", ".yml", ".txt"]),
        state_dir=state_dir,
        inbox_dir=inbox_dir,
    )
    hooks = HarnessHooks(harness_root, project_root, config, state_dir=state_dir, actor=actor)
    adapter = UniversalAgentAdapter(harness_root, project_root, config.get("adapter", {}))
    return harness_root, project_root, reviews_dir, config, fsm, ctx_mgr, hooks, adapter


def cmd_init(manual: bool = False, validate_only: bool = False) -> None:
    """Ejecuta la Fase 1: Ingesta segura a vault/ + Relevamiento con Skill 01 + Validación Hook F1."""
    harness_root, _, reviews_dir, _, fsm, ctx_mgr, hooks, adapter = load_harness(
        cli_overrides={"manual": manual}
    )
    with fsm.lock("init"):
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

        report_path = reviews_dir / "01_informe_relevamiento.md"
        report_rel = report_path.relative_to(reviews_dir.parent).as_posix()

        # 2. Ensamblar contexto limpio para Fase 1
        ctx_file = ctx_mgr.build_phase_context(
            phase_number=1,
            skill_rel_path="01_identificacion_relevamiento/SKILL.md",
            extra_sections=extra_sections,
            target_output_rel_path=report_rel,
            output_base_dir=reviews_dir.parent,
        )

        # 3. Invocar adaptador (salvo que se ejecute con --validate tras corrida manual)
        if not validate_only:
            prev_mtime = report_path.stat().st_mtime if report_path.exists() else None
            ok_run, out_msg = adapter.execute_task(
                ctx_file,
                f"Ejecuta la SKILL 01 sobre los documentos provistos y genera '{report_path}'.",
                command_hint="init",
            )
            print(out_msg)
            if not ok_run:
                sys.exit(1)
            if adapter.is_manual_mode():
                return
            # Si el CLI de IA devolvió el informe Markdown por stdout en vez de escribir el archivo en disco
            new_mtime = report_path.stat().st_mtime if report_path.exists() else None
            if (not report_path.exists() or new_mtime == prev_mtime) and "## 1" in out_msg and "## 2" in out_msg:
                report_path.parent.mkdir(parents=True, exist_ok=True)
                report_path.write_text(out_msg, encoding="utf-8")

        # 4. Validar salida con Hook F1 (si el archivo no existe, falla explícitamente)
        if not report_path.exists():
            print(
                f"[ERROR FASE 1] La IA finalizó pero no generó el archivo requerido '{report_path}'.",
                file=sys.stderr,
            )
            sys.exit(1)

        valid, msg, catalog = hooks.validate_phase1_output(report_path, vault_docs)
        print(f"[HOOK FASE 1] {msg}")
        if not valid:
            sys.exit(1)

        kb["artifacts"] = catalog
        fsm.save_kb(kb)
        fsm.transition_to("PHASE1_DONE", "Relevamiento inicial validado por Hook F1.")

        feedback_file = reviews_dir / "01_feedback_desarrollador.md"
        if not feedback_file.exists():
            feedback_file.write_text(
                f"{FEEDBACK_TEMPLATE_HEADER}\n\n{FEEDBACK_TEMPLATE_PROMPT}\n",
                encoding="utf-8",
            )
        print(
            f"Siguiente paso: Responde tus decisiones en '{feedback_file}' "
            "y ejecuta: python main.py approve-phase1"
        )


def cmd_approve_phase1() -> None:
    """Registra la aprobación humana del relevamiento y del feedback de la Fase 1 con autoría."""
    _, _, reviews_dir, _, fsm, _, hooks, _ = load_harness()
    with fsm.lock("approve-phase1"):
        report_path = reviews_dir / "01_informe_relevamiento.md"
        feedback_path = reviews_dir / "01_feedback_desarrollador.md"

        if not report_path.exists() or not feedback_path.exists():
            raise WorkflowError(f"Faltan '01_informe_relevamiento.md' o '01_feedback_desarrollador.md' en '{reviews_dir}'.")

        feedback_content = feedback_path.read_text(encoding="utf-8").strip()
        stripped_template = feedback_content.replace(FEEDBACK_TEMPLATE_HEADER, "").replace(FEEDBACK_TEMPLATE_PROMPT, "").strip()
        kb = fsm.get_kb()
        pending_ids = kb.get("artifacts", {}).get("pending_ids", [])
        if pending_ids and not stripped_template:
            raise WorkflowError(
                f"Existen elementos pendientes de decisión ({pending_ids}) y '01_feedback_desarrollador.md' "
                "aún contiene únicamente el texto de plantilla vacío. Registra tus respuestas antes de aprobar."
            )

        now_iso = datetime.now().isoformat()
        state = fsm.get_state()
        state["approvals"]["phase1_approved_at"] = now_iso
        state["approvals"]["phase1_approved_by"] = fsm.actor
        fsm.save_state(state)
        fsm.transition_to("PHASE1_APPROVED", f"Relevamiento y feedback aprobados por {fsm.actor}.")
        hooks.log_event("PHASE_1", "HUMAN_APPROVED", f"Feedback de Fase 1 confirmado por {fsm.actor}.")
        print(f"Fase 1 aprobada por {fsm.actor}. Ahora puedes generar el plan modular con: python main.py plan")


def cmd_plan(manual: bool = False, validate_only: bool = False) -> None:
    """Ejecuta la Fase 2: Planificación Modular con Skill 02 + Validación de Cobertura Hook F2."""
    _, _, reviews_dir, _, fsm, ctx_mgr, hooks, adapter = load_harness(
        cli_overrides={"manual": manual}
    )
    with fsm.lock("plan"):
        fsm.assert_can_run_phase2()

        report_text = (reviews_dir / "01_informe_relevamiento.md").read_text(encoding="utf-8")
        feedback_text = (reviews_dir / "01_feedback_desarrollador.md").read_text(encoding="utf-8")
        kb = fsm.get_kb()

        plan_path = reviews_dir / "02_plan_modular.md"
        plan_rel = plan_path.relative_to(reviews_dir.parent).as_posix()

        ctx_file = ctx_mgr.build_phase_context(
            phase_number=2,
            skill_rel_path="02_planificacion_modular/SKILL.md",
            extra_sections={
                "01_INFORME_RELEVAMIENTO": report_text,
                "01_FEEDBACK_DESARROLLADOR_APROBADO": feedback_text,
                "IDS_NORMATIVOS_OBLIGATORIOS": "\n".join(kb["artifacts"].get("approved_ids", [])),
            },
            target_output_rel_path=plan_rel,
            output_base_dir=reviews_dir.parent,
        )

        if not validate_only:
            prev_mtime = plan_path.stat().st_mtime if plan_path.exists() else None
            ok_run, out_msg = adapter.execute_task(
                ctx_file,
                f"Ejecuta la SKILL 02 integrando el feedback del desarrollador y genera '{plan_path}'.",
                command_hint="plan",
            )
            print(out_msg)
            if not ok_run:
                sys.exit(1)
            if adapter.is_manual_mode():
                return
            new_mtime = plan_path.stat().st_mtime if plan_path.exists() else None
            if (not plan_path.exists() or new_mtime == prev_mtime) and "###" in out_msg and "allowed_paths" in out_msg:
                plan_path.parent.mkdir(parents=True, exist_ok=True)
                plan_path.write_text(out_msg, encoding="utf-8")

        if not plan_path.exists():
            print(
                f"[ERROR FASE 2] La IA finalizó pero no generó el archivo requerido '{plan_path}'.",
                file=sys.stderr,
            )
            sys.exit(1)

        valid, msg, modules_plan = hooks.validate_phase2_output(
            plan_path, kb["artifacts"].get("approved_ids", [])
        )
        print(f"[HOOK FASE 2] {msg}")
        if not valid:
            sys.exit(1)

        kb["modules_plan"] = modules_plan
        fsm.save_kb(kb)
        fsm.transition_to("PHASE2_DONE", f"Plan modular validado ({len(modules_plan)} módulos).")
        print(f"Siguiente paso: Revisa '{plan_path}' y aprueba con: python main.py approve-plan")


def cmd_approve_plan() -> None:
    """Compuerta humana que aprueba el Plan Modular con autoría e inicializa el seguimiento de cada módulo."""
    _, _, reviews_dir, _, fsm, _, hooks, _ = load_harness()
    with fsm.lock("approve-plan"):
        plan_path = reviews_dir / "02_plan_modular.md"
        kb = fsm.get_kb()

        valid, msg, modules_plan = hooks.validate_phase2_output(
            plan_path, kb["artifacts"].get("approved_ids", [])
        )
        if not valid:
            raise WorkflowError(f"No se puede aprobar el plan porque tiene errores de validación: {msg}")

        kb["modules_plan"] = modules_plan
        fsm.save_kb(kb)

        now_iso = datetime.now().isoformat()
        state = fsm.get_state()
        state["approvals"]["plan_approved_at"] = now_iso
        state["approvals"]["plan_approved_by"] = fsm.actor
        for mod_id in modules_plan:
            if mod_id not in state["modules"]:
                state["modules"][mod_id] = {
                    "status": "PENDING",
                    "retries": 0,
                    "snapshot_head": None,
                    "snapshot": None,
                    "approved_by": None,
                    "built_by": None,
                }
        fsm.save_state(state)
        fsm.transition_to("PLAN_APPROVED", f"Plan aprobado por {fsm.actor} ({list(modules_plan.keys())}).")
        hooks.log_event("PHASE_2", "HUMAN_APPROVED", f"Plan aprobado por {fsm.actor}. Módulos: {list(modules_plan.keys())}")
        print(f"Plan aprobado por {fsm.actor}. Módulos listos para construir en secuencia: {list(modules_plan.keys())}")


def cmd_build(module_id: str, manual: bool = False, validate_only: bool = False) -> None:
    """Ejecuta la Fase 3 para un módulo puntual con snapshot delta, Skill 03 y circuit breaker de 3 intentos."""
    _, _, reviews_dir, config, fsm, ctx_mgr, hooks, adapter = load_harness(
        cli_overrides={"manual": manual}
    )
    with fsm.lock(f"build:{module_id}"):
        mod_spec = fsm.assert_can_build_module(module_id)

        state = fsm.get_state()
        mod_state = state["modules"][module_id]
        max_retries = int(config.get("limits", {}).get("max_module_retries", 3))

        # 1. Crear snapshot delta seguro al iniciar el módulo (si aún no tiene snapshot activo)
        require_clean = bool(config.get("limits", {}).get("require_clean_git_before_build", False))
        if not validate_only and not mod_state.get("snapshot"):
            snap = hooks.prepare_pre_module_snapshot(
                module_id,
                allowed_paths=mod_spec["allowed_paths"],
                require_clean=require_clean,
            )
            mod_state["snapshot_head"] = snap["snapshot_head"]
            mod_state["snapshot"] = snap

        mod_state["status"] = "IN_PROGRESS"
        mod_state["built_by"] = fsm.actor
        fsm.save_state(state)
        fsm.transition_to("BUILDING_MODULES", f"Construyendo módulo {module_id} ({fsm.actor})")

        # 2. Construir contexto aislado exclusivamente para este módulo
        plan_text = (reviews_dir / "02_plan_modular.md").read_text(encoding="utf-8")
        feedback_text = (reviews_dir / "01_feedback_desarrollador.md").read_text(encoding="utf-8")
        ajustes_path = reviews_dir / "03_ajustes_modulos.md"
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
            output_base_dir=reviews_dir.parent,
        )

        out_msg = ""
        if not validate_only:
            ok_run, out_msg = adapter.execute_task(
                ctx_file,
                f"Ejecuta la SKILL 03 para implementar y crear los unit tests exclusivamente del módulo '{module_id}' "
                f"dentro de las rutas autorizadas: {mod_spec['allowed_paths']}.",
                command_hint=f"build --module {module_id}",
            )
            print(out_msg)
            if not ok_run:
                sys.exit(1)
            if adapter.is_manual_mode():
                return

        # 3. Validar alcance delta Git, calidad de producción, integridad de tests y ejecución segura
        state = fsm.get_state()
        mod_state = state["modules"][module_id]
        valid, hook_msg, details = hooks.validate_phase3_module(
            module_id,
            mod_spec["allowed_paths"],
            out_msg,
            snapshot_data=mod_state.get("snapshot"),
        )
        print(f"[HOOK FASE 3 - {module_id}] {hook_msg}")

        if valid:
            mod_state["status"] = "TESTED"
            mod_state["last_validation"] = details
            fsm.save_state(state)
            kb = fsm.get_kb()
            kb["test_executions"].append({
                "module_id": module_id,
                "actor": fsm.actor,
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
                    "fallos o solicitó intervención humana. Revisa los archivos o restaura automáticamente con: "
                    f"python main.py rollback --module {module_id}"
                )
            else:
                fsm.save_state(state)
                print(f"Intento {mod_state['retries']}/{max_retries} fallido para '{module_id}'.")
            sys.exit(1)


def cmd_approve_module(module_id: str) -> None:
    """Aprueba con autoría un módulo ya testeado en Fase 3 tras la revisión del desarrollador."""
    _, _, _, _, fsm, _, hooks, _ = load_harness()
    with fsm.lock(f"approve-module:{module_id}"):
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
        mod_state["approved_by"] = fsm.actor
        fsm.save_state(state)
        hooks.log_event("PHASE_3", "MODULE_APPROVED", f"{fsm.actor} aprobó módulo {module_id}.")

        pending = [m for m, d in state["modules"].items() if d.get("status") != "APPROVED"]
        if not pending:
            fsm.transition_to("MODULES_COMPLETED", f"Todos los módulos fueron aprobados en Fase 3 (último por {fsm.actor}).")
            print("Todos los módulos han sido aprobados. Ejecuta la Fase 4 con: python main.py verify")
        else:
            print(f"Módulo '{module_id}' aprobado por {fsm.actor}. Módulos pendientes: {pending}")


def cmd_rollback(module_id: str) -> None:
    """
    Revierte de forma automática y segura los archivos modificados/creados por un módulo fallido
    guardando antes una copia en .state/rollback_backups/, y reinicia el módulo a PENDING (Punto 12).
    """
    _, _, _, _, fsm, _, hooks, _ = load_harness()
    with fsm.lock(f"rollback:{module_id}"):
        state = fsm.get_state()
        if module_id not in state["modules"]:
            raise WorkflowError(f"Módulo '{module_id}' desconocido.")

        kb = fsm.get_kb()
        mod_spec = kb.get("modules_plan", {}).get(module_id, {})
        allowed_paths = mod_spec.get("allowed_paths", [])
        mod_state = state["modules"][module_id]
        snap_data = mod_state.get("snapshot")

        rb_info = hooks.rollback_module(module_id, allowed_paths, snap_data)
        if rb_info["backed_up"]:
            print(f"[RESPALDO ROLLBACK] Se guardó copia de {len(rb_info['backed_up'])} archivo(s) en: {rb_info['backup_folder']}")
        if rb_info["reverted_tracked"]:
            print(f"[GIT CHECKOUT] Archivos rastreados restaurados: {rb_info['reverted_tracked']}")
        if rb_info["removed_untracked"]:
            print(f"[LIMPIEZA] Archivos untracked del módulo eliminados: {rb_info['removed_untracked']}")

        mod_state["status"] = "PENDING"
        mod_state["retries"] = 0
        mod_state["snapshot"] = None
        mod_state["snapshot_head"] = None
        fsm.save_state(state)
        fsm.transition_to("BUILDING_MODULES", f"Rollback completado para {module_id} por {fsm.actor}")
        hooks.log_event("PHASE_3", "ROLLBACK", f"Módulo {module_id} revertido y reiniciado a PENDING por {fsm.actor}.")
        print(f"Módulo '{module_id}' restaurado y reiniciado a estado PENDING con 0 reintentos.")


def cmd_verify(manual: bool = False, validate_only: bool = False) -> None:
    """Ejecuta la Fase 4: Contraste Final y Suite Global de Regresión en sesión limpia."""
    _, _, reviews_dir, _, fsm, ctx_mgr, hooks, adapter = load_harness(
        cli_overrides={"manual": manual}
    )
    with fsm.lock("verify"):
        fsm.assert_can_run_phase4()

        kb = fsm.get_kb()
        vault_docs = ctx_mgr.read_vault_documents()
        feedback_text = (reviews_dir / "01_feedback_desarrollador.md").read_text(encoding="utf-8")

        report_path = reviews_dir / "04_reporte_contraste_final.md"
        report_rel = report_path.relative_to(reviews_dir.parent).as_posix()

        extra_sections = dict(vault_docs)
        extra_sections["FEEDBACK_APROBADO_DESARROLLADOR"] = feedback_text
        extra_sections["IDS_APROBADOS_A_CONTRASTAR"] = "\n".join(kb["artifacts"].get("approved_ids", []))

        ctx_file = ctx_mgr.build_phase_context(
            phase_number=4,
            skill_rel_path="04_contraste_y_regresion/SKILL.md",
            extra_sections=extra_sections,
            target_output_rel_path=report_rel,
            output_base_dir=reviews_dir.parent,
        )

        if not validate_only:
            prev_mtime = report_path.stat().st_mtime if report_path.exists() else None
            ok_run, out_msg = adapter.execute_task(
                ctx_file,
                f"Ejecuta la SKILL 04 en sesión limpia de auditoría y genera '{report_path}'.",
                command_hint="verify",
            )
            print(out_msg)
            if not ok_run:
                sys.exit(1)
            if adapter.is_manual_mode():
                return
            new_mtime = report_path.stat().st_mtime if report_path.exists() else None
            if (not report_path.exists() or new_mtime == prev_mtime) and "## 1" in out_msg and "|" in out_msg:
                report_path.parent.mkdir(parents=True, exist_ok=True)
                report_path.write_text(out_msg, encoding="utf-8")

        if not report_path.exists():
            print(
                f"[ERROR FASE 4] La IA finalizó pero no generó el archivo requerido '{report_path}'.",
                file=sys.stderr,
            )
            sys.exit(1)

        valid, msg = hooks.validate_phase4_output(
            report_path, kb["artifacts"].get("approved_ids", [])
        )
        print(f"[HOOK FASE 4] {msg}")
        if not valid:
            sys.exit(1)

        now_iso = datetime.now().isoformat()
        state = fsm.get_state()
        state["approvals"]["final_verified_at"] = now_iso
        state["approvals"]["final_verified_by"] = fsm.actor
        fsm.save_state(state)
        fsm.transition_to("VERIFIED", f"Contraste final y pruebas de regresión aprobadas por {fsm.actor}.")
        print("Aplicación validada al 100% y lista para su puesta en uso operativo.")


def cmd_check(phase: int, module_id: Optional[str] = None) -> None:
    """
    Ejecuta únicamente los controles duros determinísticos (Hooks F1..F4) de forma desacoplada,
    permitiendo usar agent-harness como validador desde agent-workflow, git hooks o CI/CD.
    """
    _, _, reviews_dir, _, fsm, ctx_mgr, hooks, _ = load_harness()
    kb = fsm.get_kb()
    state = fsm.get_state()

    if phase == 1:
        vault_docs = ctx_mgr.read_vault_documents()
        if not vault_docs:
            vault_manifest = ctx_mgr.ingest_inbox_to_vault(kb.get("vault_documents", {}))
            kb["vault_documents"] = vault_manifest
            fsm.save_kb(kb)
            vault_docs = ctx_mgr.read_vault_documents()
        valid, msg, _ = hooks.validate_phase1_output(reviews_dir / "01_informe_relevamiento.md", vault_docs)
    elif phase == 2:
        valid, msg, _ = hooks.validate_phase2_output(
            reviews_dir / "02_plan_modular.md", kb.get("artifacts", {}).get("approved_ids", [])
        )
    elif phase == 3:
        if not module_id:
            raise WorkflowError("Para '--phase 3' debes especificar '--module <ID>'.")
        mod_spec = kb.get("modules_plan", {}).get(module_id)
        if not mod_spec:
            raise WorkflowError(f"Módulo '{module_id}' no encontrado en kb_index.json.")
        snap = state.get("modules", {}).get(module_id, {}).get("snapshot")
        valid, msg, _ = hooks.validate_phase3_module(
            module_id, mod_spec.get("allowed_paths", []), "", snapshot_data=snap
        )
    elif phase == 4:
        valid, msg = hooks.validate_phase4_output(
            reviews_dir / "04_reporte_contraste_final.md", kb.get("artifacts", {}).get("approved_ids", [])
        )
    else:
        raise WorkflowError(f"Fase inválida: {phase}. Usa 1, 2, 3 o 4.")

    print(f"[CHECK FASE {phase}] {msg}")
    if not valid:
        sys.exit(1)


def cmd_status() -> None:
    """Muestra el estado actual de la máquina de estados, autoría, aprobaciones y avance por módulo."""
    _, project_root, reviews_dir, config, fsm, _, _, adapter = load_harness(require_project_root=False)
    state = fsm.get_state()
    kb = fsm.get_kb()
    approvals = state.get("approvals", {})
    print("=== ESTADO DEL AGENT HARNESS ===")
    print(f"Desarrollador Activo           : {fsm.actor}")
    print(f"Proyecto Destino (project_root): {project_root}")
    print(f"Ubicación de Estado Compartido : {fsm.state_dir} (reviews: {reviews_dir})")
    print(f"Adaptador IA Configurado       : provider='{adapter.provider}', mode='{adapter.mode}', binary='{adapter.binary}'")
    print(f"Estado Actual FSM              : {state['current_state']} (v{state.get('version', 1)}, actualizado por {state.get('updated_by', '?')})")
    print(f"Documentos en Vault            : {len(kb.get('vault_documents', {}))}")
    print(f"IDs Aprobados Indexados        : {len(kb.get('artifacts', {}).get('approved_ids', []))}")
    if approvals.get("phase1_approved_at"):
        print(f"Fase 1 Aprobada                : {approvals.get('phase1_approved_at')} por {approvals.get('phase1_approved_by', '?')}")
    if approvals.get("plan_approved_at"):
        print(f"Plan Modular Aprobado          : {approvals.get('plan_approved_at')} por {approvals.get('plan_approved_by', '?')}")
    print("Avance de Módulos:")
    for mod_id, m_data in state.get("modules", {}).items():
        by_info = f", aprobado por {m_data['approved_by']}" if m_data.get("approved_by") else ""
        print(f"  - {mod_id}: {m_data.get('status')} (Reintentos: {m_data.get('retries', 0)}{by_info})")


def main() -> None:
    parser = argparse.ArgumentParser(description="Agent Harness Determinístico Multi-IA de 4 Fases")
    subparsers = parser.add_subparsers(dest="command", required=True)

    p_init = subparsers.add_parser("init", help="Fase 1: Ingesta documental y relevamiento inicial")
    p_init.add_argument("--manual", action="store_true", help="Prepara el contexto sin invocar el CLI de IA")
    p_init.add_argument("--validate", action="store_true", help="Valida el entregable generado manualmente")

    subparsers.add_parser("approve-phase1", help="Aprueba el relevamiento y feedback de la Fase 1")

    p_plan = subparsers.add_parser("plan", help="Fase 2: Genera el plan de implementación por módulos")
    p_plan.add_argument("--manual", action="store_true", help="Prepara el contexto sin invocar el CLI de IA")
    p_plan.add_argument("--validate", action="store_true", help="Valida el plan generado manualmente")

    subparsers.add_parser("approve-plan", help="Aprueba el plan modular para habilitar la Fase 3")

    p_build = subparsers.add_parser("build", help="Fase 3: Desarrolla y testea un módulo específico")
    p_build.add_argument("--module", required=True, help="ID del módulo (ej. MOD-01)")
    p_build.add_argument("--manual", action="store_true", help="oma snapshot y prepara contexto sin invocar el CLI de IA")
    p_build.add_argument("--validate", action="store_true", help="Valida el módulo codificado manualmente")

    p_app_mod = subparsers.add_parser("approve-module", help="Marca un módulo testeado como aprobado")
    p_app_mod.add_argument("--module", required=True, help="ID del módulo (ej. MOD-01)")

    p_roll = subparsers.add_parser("rollback", help="Revierte con respaldo y reinicia un módulo escalado o fallido")
    p_roll.add_argument("--module", required=True, help="ID del módulo (ej. MOD-01)")

    p_verify = subparsers.add_parser("verify", help="Fase 4: Ejecuta regresión global y contraste contra requerimientos")
    p_verify.add_argument("--manual", action="store_true", help="Prepara el contexto sin invocar el CLI de IA")
    p_verify.add_argument("--validate", action="store_true", help="Valida el reporte final generado manualmente")

    p_check = subparsers.add_parser("check", help="Ejecuta solo el Hook determinístico de una fase (compatible con agent-workflow / CI)")
    p_check.add_argument("--phase", type=int, required=True, choices=[1, 2, 3, 4], help="Número de fase (1-4)")
    p_check.add_argument("--module", help="ID del módulo (requerido para --phase 3)")

    subparsers.add_parser("status", help="Muestra el estado actual del proyecto, autoría y avance de módulos")

    args = parser.parse_args()
    try:
        if args.command == "init":
            cmd_init(manual=args.manual, validate_only=args.validate)
        elif args.command == "approve-phase1":
            cmd_approve_phase1()
        elif args.command == "plan":
            cmd_plan(manual=args.manual, validate_only=args.validate)
        elif args.command == "approve-plan":
            cmd_approve_plan()
        elif args.command == "build":
            cmd_build(args.module, manual=args.manual, validate_only=args.validate)
        elif args.command == "approve-module":
            cmd_approve_module(args.module)
        elif args.command == "rollback":
            cmd_rollback(args.module)
        elif args.command == "verify":
            cmd_verify(manual=args.manual, validate_only=args.validate)
        elif args.command == "check":
            cmd_check(args.phase, args.module)
        elif args.command == "status":
            cmd_status()
    except (WorkflowError, Exception) as err:
        print(f"[ERROR HARNESS] {err}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
