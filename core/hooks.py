import re
import shlex
import subprocess
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, List, Tuple, Set, Optional


ID_PATTERN = re.compile(r"\b(?:OBJ|BR|RF|RNF|US|UC|AT|ST|TR|ERR|ENT|API|EVT|ADR|SUP|DEC|Q)-[A-Z0-9_]+-\d+\b")
PENDING_PREFIXES = ("SUP-", "DEC-", "Q-")
TEST_FILE_PATTERNS = (".spec.", ".test.", "test_", "_test.")
FORBIDDEN_PROD_PATTERNS = [
    re.compile(r"\bTODO\b"),
    re.compile(r"\bFIXME\b"),
    re.compile(r"\bXXX\b"),
    re.compile(r"NotImplementedError"),
]
DANGEROUS_CMD_TOKENS = [";", "&&", "||", "|", "`", "$(", "rm -rf", "mkfs", "dd if="]


class HarnessHooks:
    """
    Controles determinísticos ejecutados en Python fuera del LLM.
    Verifica salidas de cada fase, estado de Git en project_root y ejecución real de tests.
    """

    def __init__(self, harness_root: Path, project_root: Path, config: Dict[str, Any]):
        self.harness_root = harness_root.resolve()
        self.project_root = project_root.resolve()
        self.config = config
        self.audit_log = self.harness_root / ".state" / "audit.log"

    def log_event(self, phase: str, status: str, detail: str) -> None:
        self.audit_log.parent.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().isoformat()
        line = f"[{timestamp}] [{phase}] [{status}] {detail}\n"
        with open(self.audit_log, "a", encoding="utf-8") as f:
            f.write(line)

    # =========================================================================
    # FASE 1: Validador Anti-Alucinación de Relevamiento
    # =========================================================================
    def validate_phase1_output(
        self,
        report_path: Path,
        vault_docs: Dict[str, str],
    ) -> Tuple[bool, str, Dict[str, List[str]]]:
        if not report_path.exists():
            return False, f"No se encontró el informe requerido en '{report_path}'.", {}

        report_text = report_path.read_text(encoding="utf-8")
        required_headers = [
            "## 1.",
            "## 2.",
            "## 3.",
            "## 4.",
            "## 5.",
            "## 6.",
        ]
        missing_headers = [h for h in required_headers if h not in report_text]
        if missing_headers:
            return (
                False,
                f"El informe de Fase 1 no respeta la estructura de 6 secciones. Faltan: {missing_headers}",
                {},
            )

        # Extraer todos los IDs presentes en los documentos fuente de vault/
        combined_source = "\n".join(vault_docs.values())
        source_ids: Set[str] = set(ID_PATTERN.findall(combined_source))

        # Separar las secciones normativas (1 a 4) de las secciones de hallazgos/preguntas (5 y 6)
        split_match = re.split(r"^##\s+5\.", report_text, maxsplit=1, flags=re.MULTILINE)
        normative_section = split_match[0]
        findings_section = split_match[1] if len(split_match) > 1 else ""

        cited_normative_ids: Set[str] = set(ID_PATTERN.findall(normative_section))
        cited_findings_ids: Set[str] = set(ID_PATTERN.findall(findings_section))

        if source_ids:
            # Si el documento fuente usa IDs formales, prohibir cualquier ID inventado en secciones 1 a 4
            hallucinated_ids = sorted(cited_normative_ids - source_ids)
            if hallucinated_ids:
                self.log_event("PHASE_1", "REJECTED", f"IDs alucinados en secciones 1-4: {hallucinated_ids}")
                return (
                    False,
                    f"Control Anti-Alucinación Fase 1 falló: La IA citó en las secciones normativas (1 a 4) "
                    f"identificadores que NO existen en los documentos fuente: {hallucinated_ids}. "
                    "Si son referencias huérfanas detectadas, deben listarse únicamente en las secciones 5 o 6.",
                    {},
                )

        approved_ids = sorted([i for i in source_ids if not i.startswith(PENDING_PREFIXES)])
        pending_ids = sorted([i for i in source_ids if i.startswith(PENDING_PREFIXES)])
        referenced_unknown = sorted((cited_findings_ids - source_ids))

        extracted_catalog = {
            "approved_ids": approved_ids,
            "pending_ids": pending_ids,
            "referenced_unknown_ids": referenced_unknown,
        }
        self.log_event(
            "PHASE_1",
            "PASSED",
            f"IDs aprobados: {len(approved_ids)}, Pendientes (SUP/DEC/Q): {len(pending_ids)}",
        )
        return True, "Informe de Fase 1 validado correctamente.", extracted_catalog

    # =========================================================================
    # FASE 2: Validador de Cobertura 1 a 1 y Bloques de Módulos
    # =========================================================================
    def validate_phase2_output(
        self,
        plan_path: Path,
        approved_ids: List[str],
    ) -> Tuple[bool, str, Dict[str, Any]]:
        if not plan_path.exists():
            return False, f"No se encontró el plan modular en '{plan_path}'.", {}

        plan_text = plan_path.read_text(encoding="utf-8")

        # Parsear bloques ### Módulo <ID>: <Nombre>
        module_pattern = re.compile(
            r"^###\s+M[óo]dulo\s+([A-Za-z0-9_-]+)\s*:(.*?)(?=^###\s+M[óo]dulo\s+|^##\s+4\.|\Z)",
            re.DOTALL | re.MULTILINE,
        )
        matches = list(module_pattern.finditer(plan_text))
        if not matches:
            return (
                False,
                "El plan no contiene bloques con formato '### Módulo <ID>: <Nombre>'.",
                {},
            )

        modules_plan: Dict[str, Any] = {}
        all_assigned_ids: Set[str] = set()

        for idx, match in enumerate(matches, start=1):
            mod_id = match.group(1).strip()
            mod_body = match.group(2)

            # Extraer allowed_paths: [...]
            paths_match = re.search(r"allowed_paths\s*:\s*\[(.*?)\]", mod_body, re.DOTALL)
            if not paths_match:
                return (
                    False,
                    f"El módulo '{mod_id}' no declara el campo obligatorio 'allowed_paths: [...]'.",
                    {},
                )
            raw_paths = [
                p.strip().strip("`'\"")
                for p in paths_match.group(1).split(",")
                if p.strip().strip("`'\"")
            ]
            if not raw_paths:
                return (
                    False,
                    f"El módulo '{mod_id}' tiene la lista 'allowed_paths' vacía.",
                    {},
                )

            # Verificar que ninguna ruta en allowed_paths intente salir de project_root
            for rel_p in raw_paths:
                resolved_p = (self.project_root / rel_p).resolve()
                if not resolved_p.is_relative_to(self.project_root):
                    return (
                        False,
                        f"Ruta ilegal en allowed_paths de '{mod_id}': '{rel_p}' apunta fuera de project_root.",
                        {},
                    )

            mod_ids = sorted(set(ID_PATTERN.findall(mod_body)))
            all_assigned_ids.update(mod_ids)

            modules_plan[mod_id] = {
                "order": idx,
                "assigned_ids": mod_ids,
                "allowed_paths": raw_paths,
            }

        # Verificar que todos los requisitos/reglas obligatorios (BR, RF, RNF, TR) estén cubiertos
        mandatory_prefixes = ("BR-", "RF-", "RNF-", "TR-")
        mandatory_approved = [i for i in approved_ids if i.startswith(mandatory_prefixes)]
        orphan_ids = sorted(set(mandatory_approved) - all_assigned_ids)
        if orphan_ids:
            self.log_event("PHASE_2", "REJECTED", f"Requisitos huérfanos sin módulo: {orphan_ids}")
            return (
                False,
                f"Control de Cobertura Fase 2 falló: Los siguientes requisitos/reglas aprobados no fueron "
                f"asignados a ningún bloque de módulo: {orphan_ids}",
                {},
            )

        self.log_event("PHASE_2", "PASSED", f"Plan modular validado con {len(modules_plan)} módulos.")
        return True, f"Plan modular validado ({len(modules_plan)} módulos).", modules_plan

    # =========================================================================
    # FASE 3: Snapshots Seguros, Control de Alcance Git y Ejecución de Unit Tests
    # =========================================================================
    def _run_git(self, args: List[str]) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git"] + args,
            cwd=self.project_root,
            capture_output=True,
            text=True,
            check=False,
        )

    def get_git_modified_and_untracked(self) -> List[str]:
        """
        Usa 'git status --porcelain -uall' para detectar archivos modificados,
        staged, renombrados y archivos nuevos no rastreados (untracked).
        """
        res = self._run_git(["status", "--porcelain", "-uall"])
        if res.returncode != 0:
            raise RuntimeError(f"Error al consultar git status en '{self.project_root}': {res.stderr}")

        changed_files: List[str] = []
        for line in res.stdout.splitlines():
            if not line.strip():
                continue
            path_part = line[3:].strip()
            if " -> " in path_part:
                path_part = path_part.split(" -> ")[-1].strip()
            changed_files.append(path_part.strip('"'))
        return changed_files

    def prepare_pre_module_snapshot(self, module_id: str, require_clean: bool = True) -> Dict[str, Optional[str]]:
        """
        Verifica el estado del árbol Git en project_root antes de que el agente edite código.
        Nunca sobrescribe ni destruye trabajo previo del desarrollador.
        """
        dirty_files = self.get_git_modified_and_untracked()
        if dirty_files and require_clean:
            raise RuntimeError(
                f"Protección de código activada: El repositorio en '{self.project_root}' tiene cambios "
                f"sin confirmar ({dirty_files[:5]}). Haz un commit o stash manual en Zed antes de "
                f"ejecutar el módulo '{module_id}' para garantizar un punto de restauración limpio."
            )

        head_res = self._run_git(["rev-parse", "HEAD"])
        head_sha = head_res.stdout.strip() if head_res.returncode == 0 else None
        self.log_event("PHASE_3", "SNAPSHOT", f"Módulo {module_id} inició sobre HEAD={head_sha}")
        return {"snapshot_head": head_sha}

    def validate_phase3_module(
        self,
        module_id: str,
        allowed_paths: List[str],
        agent_output_text: str,
    ) -> Tuple[bool, str, Dict[str, Any]]:
        # 1. Verificar si el agente activó la válvula de escalamiento
        if "[ESCALAR_AL_DESARROLLADOR]" in agent_output_text:
            self.log_event("PHASE_3", "ESCALATED", f"Agente solicitó escalamiento en {module_id}")
            return (
                False,
                f"El agente detuvo el módulo '{module_id}' y solicitó '[ESCALAR_AL_DESARROLLADOR]'. "
                "Revisa la salida para autorizar cambios de alcance o ajustar el plan.",
                {"escalated": True},
            )

        if not allowed_paths:
            return False, f"El módulo '{module_id}' no tiene rutas autorizadas en allowed_paths.", {}

        # 2. Verificar archivos tocados o creados mediante git status --porcelain -uall
        changed_rel_paths = self.get_git_modified_and_untracked()
        if not changed_rel_paths:
            return False, f"El agente no realizó ninguna modificación de código para el módulo '{module_id}'.", {}

        resolved_allowed = [(self.project_root / p).resolve() for p in allowed_paths]
        unauthorized_files: List[str] = []
        prod_files_changed: List[Path] = []
        test_files_changed: List[Path] = []

        for rel_file in changed_rel_paths:
            candidate_path = self.project_root / rel_file
            # Bloquear enlaces simbólicos que apunten fuera de project_root
            resolved_candidate = candidate_path.resolve()
            if not resolved_candidate.is_relative_to(self.project_root):
                unauthorized_files.append(f"{rel_file} (Symlink fuera del proyecto)")
                continue

            is_allowed = any(
                resolved_candidate == allowed_p or resolved_candidate.is_relative_to(allowed_p)
                for allowed_p in resolved_allowed
            )
            if not is_allowed:
                unauthorized_files.append(rel_file)
                continue

            if resolved_candidate.is_file():
                name_lower = resolved_candidate.name.lower()
                if any(pat in name_lower for pat in TEST_FILE_PATTERNS) or "test" in rel_file.lower():
                    test_files_changed.append(resolved_candidate)
                else:
                    prod_files_changed.append(resolved_candidate)

        if unauthorized_files:
            # NO se ejecuta git checkout -- automático para no destruir archivos sin revisión humana
            self.log_event("PHASE_3", "SCOPE_VIOLATION", f"{module_id} tocó: {unauthorized_files}")
            return (
                False,
                f"Violación de alcance en '{module_id}': Se modificaron o crearon archivos fuera de "
                f"allowed_paths: {unauthorized_files}. El harness bloqueó el avance sin borrar los archivos "
                "para que puedas inspeccionarlos en Zed o usar 'python main.py rollback --module "
                f"{module_id}'.",
                {"unauthorized_files": unauthorized_files},
            )

        # 3. Escanear prohibición de TODO/FIXME exclusivamente en archivos de producción
        shortcut_violations: List[str] = []
        for prod_file in prod_files_changed:
            content = prod_file.read_text(encoding="utf-8", errors="ignore")
            for pattern in FORBIDDEN_PROD_PATTERNS:
                if pattern.search(content):
                    shortcut_violations.append(
                        f"{prod_file.relative_to(self.project_root)} (contiene '{pattern.pattern}')"
                    )

        if shortcut_violations:
            self.log_event("PHASE_3", "SHORTCUT_DETECTED", f"{module_id}: {shortcut_violations}")
            return (
                False,
                f"Código incompleto detectado en archivos de producción: {shortcut_violations}.",
                {},
            )

        # 4. Verificar que existan pruebas unitarias con aserciones reales
        if not test_files_changed:
            return (
                False,
                f"El módulo '{module_id}' no creó ni actualizó ningún archivo de pruebas unitarias.",
                {},
            )

        has_real_assertions = False
        for t_file in test_files_changed:
            t_content = t_file.read_text(encoding="utf-8", errors="ignore")
            if "expect(" in t_content or "assert" in t_content:
                has_real_assertions = True
                break

        if not has_real_assertions:
            return (
                False,
                f"Los archivos de test del módulo '{module_id}' no contienen aserciones verificables (expect/assert).",
                {},
            )

        # 5. Ejecutar comando de pruebas unitarias de forma segura (shell=False + timeout)
        test_cmd = self.config.get("testing", {}).get("unit_test_cmd", "")
        timeout_sec = int(self.config.get("testing", {}).get("timeout_seconds", 300))
        ok, test_output = self.run_safe_command(test_cmd, timeout_sec)
        if not ok:
            self.log_event("PHASE_3", "UNIT_TESTS_FAILED", f"{module_id}: {test_output[:300]}")
            return (
                False,
                f"Las pruebas unitarias del módulo '{module_id}' fallaron:\n{test_output}",
                {"test_output": test_output},
            )

        self.log_event("PHASE_3", "PASSED", f"Módulo {module_id} validado y unit tests en PASS.")
        return (
            True,
            f"Módulo '{module_id}' validado con éxito (alcance, código limpio y unit tests en verde).",
            {
                "prod_files": [p.relative_to(self.project_root).as_posix() for p in prod_files_changed],
                "test_files": [t.relative_to(self.project_root).as_posix() for t in test_files_changed],
                "test_output_summary": test_output[-500:],
            },
        )

    # =========================================================================
    # FASE 4: Validador de Evidencia Física y Regresión Global
    # =========================================================================
    def validate_phase4_output(
        self,
        report_path: Path,
        approved_ids: List[str],
    ) -> Tuple[bool, str]:
        if not report_path.exists():
            return False, f"No se encontró el reporte final en '{report_path}'."

        report_text = report_path.read_text(encoding="utf-8")

        # 1. Verificar que todos los IDs aprobados del proyecto figuren en el contraste final
        missing_ids = [i for i in approved_ids if i not in report_text]
        if missing_ids:
            return (
                False,
                f"El reporte final de Fase 4 omitió contrastar los siguientes IDs aprobados: {missing_ids}",
            )

        # 2. Extraer rutas de archivos citadas entre backticks y comprobar que existan en project_root
        path_candidates = re.findall(r"`([A-Za-z0-9_./-]+\.[A-Za-z0-9]+)`", report_text)
        if not path_candidates:
            return (
                False,
                "El reporte de Fase 4 no cita rutas de archivos reales entre backticks (`ruta/archivo.ext`).",
            )

        non_existent_paths: List[str] = []
        for rel_p in set(path_candidates):
            if "/" not in rel_p:
                continue
            full_p = (self.project_root / rel_p).resolve()
            if not full_p.exists():
                non_existent_paths.append(rel_p)

        if non_existent_paths:
            self.log_event("PHASE_4", "HALLUCINATED_PATHS", f"Rutas inexistentes: {non_existent_paths}")
            return (
                False,
                f"Control Anti-Alucinación Fase 4 falló: El reporte cita archivos de código o test "
                f"que NO existen físicamente en '{self.project_root}': {non_existent_paths}",
            )

        # 3. Ejecutar suite completa de regresión
        reg_cmd = self.config.get("testing", {}).get("regression_test_cmd", "")
        timeout_sec = int(self.config.get("testing", {}).get("timeout_seconds", 300))
        ok, reg_output = self.run_safe_command(reg_cmd, timeout_sec)
        if not ok:
            self.log_event("PHASE_4", "REGRESSION_FAILED", reg_output[:300])
            return False, f"La suite global de pruebas de regresión falló:\n{reg_output}"

        self.log_event("PHASE_4", "PASSED", "Contraste final y suite de regresión aprobados.")
        return True, "Fase 4 completada: 100% de trazabilidad física y pruebas de regresión en PASS."

    # =========================================================================
    # Ejecutor Seguro de Subprocesos (Sin shell=True)
    # =========================================================================
    def run_safe_command(self, command_str: str, timeout_seconds: int = 300) -> Tuple[bool, str]:
        if not command_str.strip():
            return False, "El comando de pruebas en config.yaml está vacío."

        for token in DANGEROUS_CMD_TOKENS:
            if token in command_str:
                return (
                    False,
                    f"Seguridad: El comando '{command_str}' contiene el operador prohibido '{token}'.",
                )

        cmd_args = shlex.split(command_str)
        try:
            proc = subprocess.run(
                cmd_args,
                cwd=self.project_root,
                shell=False,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
            )
            combined_out = (proc.stdout or "") + "\n" + (proc.stderr or "")
            return proc.returncode == 0, combined_out.strip()
        except subprocess.TimeoutExpired:
            return False, f"El comando '{command_str}' excedió el tiempo límite de {timeout_seconds}s."
        except FileNotFoundError as err:
            return False, f"No se encontró el ejecutable al correr '{command_str}': {err}"
