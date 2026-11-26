import os
import re
import ast
import shlex
import shutil
import hashlib
import subprocess
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, List, Tuple, Set, Optional


ID_PATTERN = re.compile(r"\b(?:OBJ|BR|RF|RNF|US|UC|AT|ST|TR|ERR|ENT|API|EVT|ADR|SUP|DEC|Q)-[A-Z0-9_]+-\d+\b")
PENDING_PREFIXES = ("SUP-", "DEC-", "Q-")

# Carpetas y patrones de nombres legítimos para clasificar archivos de test (Punto 7)
TEST_DIR_NAMES = {"test", "tests", "__tests__", "spec", "specs", "e2e", "it"}
TEST_FILENAME_INFIXES = (".spec.", ".test.")
TEST_FILENAME_PREFIXES = ("test_",)
TEST_FILENAME_SUFFIXES = (
    "_test.py",
    "_test.ts",
    "_test.tsx",
    "_test.js",
    "_test.jsx",
    "_test.go",
    "_test.rs",
    "test.java",
    "tests.java",
    "test.cs",
    "tests.cs",
)

# Patrones prohibidos en código de producción según core_rules.md (Puntos 7 y 14)
FORBIDDEN_PROD_PATTERNS = [
    (re.compile(r"\bTODO\b"), "TODO"),
    (re.compile(r"\bFIXME\b"), "FIXME"),
    (re.compile(r"\bXXX\b"), "XXX"),
    (re.compile(r"\bTBD\b"), "TBD"),
    (re.compile(r"\bHACK\b"), "HACK"),
    (re.compile(r"\bNotImplementedError\b"), "NotImplementedError"),
    (re.compile(r"throw\s+new\s+Error\s*\(\s*['\"]Not\s+implemented['\"]", re.IGNORECASE), "Not implemented Error"),
    (re.compile(r"\bjest\.mock\s*\("), "jest.mock en producción"),
    (re.compile(r"\bvi\.mock\s*\("), "vi.mock en producción"),
    (re.compile(r"\bunittest\.mock\b"), "unittest.mock en producción"),
    (re.compile(r"from\s+unittest\s+import\s+mock\b"), "unittest.mock en producción"),
    (re.compile(r"\bMagicMock\s*\("), "MagicMock en producción"),
    (re.compile(r"\bsinon\.(?:stub|mock|spy)\s*\("), "sinon mock en producción"),
]

# Patrones de peligro en comandos y código generado antes de correr tests (Punto 10)
DANGEROUS_CMD_TOKENS = [
    ";",
    "&&",
    "||",
    "|",
    "`",
    "$(",
    ">",
    "<",
    "rm -rf",
    "del /f",
    "rd /s",
    "format ",
    "mkfs",
    "dd if=",
    "invoke-expression",
    "iex ",
    " -enc ",
    "curl ",
    "wget ",
]

DANGEROUS_CODE_PATTERNS = [
    (re.compile(r"\bchild_process\b.*?\b(?:exec|execSync|spawn|spawnSync)\s*\(", re.DOTALL), "child_process exec/spawn"),
    (re.compile(r"\bos\.(?:system|popen)\s*\("), "os.system / os.popen"),
    (re.compile(r"\bsubprocess\.(?:Popen|run|call|check_output)\s*\([^)]*shell\s*=\s*True", re.DOTALL), "subprocess con shell=True"),
    (re.compile(r"\bshutil\.rmtree\s*\(\s*['\"](?:/|[A-Za-z]:\\|\.\.)"), "shutil.rmtree fuera de directorio seguro"),
    (re.compile(r"\bfs\.(?:rmSync|rmdirSync)\s*\(\s*['\"](?:/|[A-Za-z]:\\|\.\.)"), "fs.rmSync fuera de directorio seguro"),
]

SENSITIVE_ENV_PATTERNS = (
    "KEY",
    "TOKEN",
    "SECRET",
    "PASSWORD",
    "PASSWD",
    "CREDENTIAL",
    "DATABASE_URL",
    "AWS_",
    "AZURE_",
    "GCP_",
    "PRIVATE",
)


class HarnessHooks:
    """
    Controles determinísticos ejecutados en Python fuera del LLM.
    Verifica salidas de cada fase, deltas de Git en project_root, seguridad estática y ejecución multiplataforma de tests.
    """

    def __init__(
        self,
        harness_root: Path,
        project_root: Path,
        config: Dict[str, Any],
        state_dir: Optional[Path] = None,
        actor: str = "system",
    ):
        self.harness_root = harness_root.resolve()
        self.project_root = project_root.resolve()
        self.config = config
        self.state_dir = (state_dir or (self.harness_root / ".state")).resolve()
        self.audit_log = self.state_dir / "audit.log"
        self.rollback_dir = self.state_dir / "rollback_backups"
        self.actor = actor

    def log_event(self, phase: str, status: str, detail: str, actor: Optional[str] = None) -> None:
        self.audit_log.parent.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().isoformat()
        who = actor or self.actor
        line = f"[{timestamp}] [{who}] [{phase}] [{status}] {detail}\n"
        with open(self.audit_log, "a", encoding="utf-8") as f:
            f.write(line)

    @staticmethod
    def _compute_sha256(file_path: Path) -> Optional[str]:
        if not file_path.exists() or not file_path.is_file():
            return None
        hasher = hashlib.sha256()
        try:
            with open(file_path, "rb") as f:
                for chunk in iter(lambda: f.read(65536), b""):
                    hasher.update(chunk)
            return hasher.hexdigest()
        except OSError:
            return None

    @staticmethod
    def normalize_rel_path(rel_path: str) -> str:
        return rel_path.strip().strip("\"'").replace("\\", "/")

    # =========================================================================
    # Clasificación estricta Test vs. Producción y Análisis de Aserciones (Puntos 7 y 14)
    # =========================================================================
    @classmethod
    def is_test_file(cls, rel_path: str) -> bool:
        """
        Determina si una ruta corresponde a un archivo de test por carpeta o nombre de archivo real,
        sin confundirse con carpetas como 'latest/', 'contest/' o 'testimonial/'.
        """
        norm = cls.normalize_rel_path(rel_path)
        p = Path(norm)
        dir_parts = {part.lower() for part in p.parts[:-1]}
        if dir_parts & TEST_DIR_NAMES:
            return True

        name_lower = p.name.lower()
        if any(name_lower.startswith(pref) for pref in TEST_FILENAME_PREFIXES):
            return True
        if any(name_lower.endswith(suff) for suff in TEST_FILENAME_SUFFIXES):
            return True
        if any(inf in name_lower for inf in TEST_FILENAME_INFIXES):
            return True
        return False

    @staticmethod
    def _strip_code_comments(content: str) -> str:
        """Elimina comentarios //, /* */ y # para evitar falsos positivos al buscar aserciones en comentarios."""
        no_block = re.sub(r"/\*.*?\*/", "", content, flags=re.DOTALL)
        cleaned_lines: List[str] = []
        for line in no_block.splitlines():
            stripped = line.strip()
            if stripped.startswith("//") or stripped.startswith("#"):
                continue
            line_no_inline = re.sub(r"//.*$", "", line)
            line_no_inline = re.sub(r"\s+#.*$", "", line_no_inline)
            cleaned_lines.append(line_no_inline)
        return "\n".join(cleaned_lines)

    @classmethod
    def analyze_test_file(cls, file_path: Path) -> Dict[str, Any]:
        """
        Verifica aserciones reales (ignorando comentarios y 'assert True' triviales)
        y detecta desactivación tramposa de tests (.skip, .only, @unittest.skip).
        """
        content = file_path.read_text(encoding="utf-8", errors="ignore")
        suffix = file_path.suffix.lower()
        skip_reasons: List[str] = []
        assertion_count = 0

        if suffix == ".py":
            try:
                tree = ast.parse(content, filename=str(file_path))
                for node in ast.walk(tree):
                    # Detectar decoradores skip
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                        for dec in node.decorator_list:
                            dec_src = ast.unparse(dec) if hasattr(ast, "unparse") else ""
                            if "skip" in dec_src.lower():
                                skip_reasons.append(f"Decorador skip en '{node.name}' ({dec_src})")
                    # Detectar llamadas pytest.skip / self.skipTest
                    if isinstance(node, ast.Call):
                        func_src = ast.unparse(node.func) if hasattr(ast, "unparse") else ""
                        if func_src in ("pytest.skip", "unittest.skip", "self.skipTest", "skipTest"):
                            skip_reasons.append(f"Llamada a '{func_src}()'")
                        if (
                            ".assert" in func_src
                            or func_src.startswith("assert")
                            or func_src in ("pytest.raises", "pytest.warns", "expect")
                            or "assert_" in func_src
                        ):
                            assertion_count += 1
                    elif isinstance(node, ast.Assert):
                        # Rechazar assert True / assert 1 triviales
                        if isinstance(node.test, ast.Constant) and bool(node.test.value) is True:
                            continue
                        assertion_count += 1
                return {
                    "assertion_count": assertion_count,
                    "has_skipped_or_only": len(skip_reasons) > 0,
                    "skip_reasons": skip_reasons,
                }
            except SyntaxError:
                pass

        uncommented = cls._strip_code_comments(content)

        # Detectar .skip / .only / .todo / xit / xtest en JS/TS
        js_skip_patterns = [
            (re.compile(r"\b(?:it|test|describe)\.(?:skip|only|todo)\s*\("), "it/test/describe .skip/.only/.todo"),
            (re.compile(r"\b(?:xit|xtest|xdescribe|fit|ftest|fdescribe)\s*\("), "xit/xtest/fit/ftest"),
        ]
        for pat, label in js_skip_patterns:
            if pat.search(uncommented):
                skip_reasons.append(label)

        # Eliminar aserciones triviales expect(true).toBe(true) o assert(true)
        uncommented_nontrivial = re.sub(
            r"\bexpect\s*\(\s*true\s*\)\s*\.\s*toBe\s*\(\s*true\s*\)",
            "",
            uncommented,
            flags=re.IGNORECASE,
        )
        uncommented_nontrivial = re.sub(
            r"\bassert\s*\(\s*true\s*\)",
            "",
            uncommented_nontrivial,
            flags=re.IGNORECASE,
        )

        assertion_patterns = [
            re.compile(r"\bexpect\s*\([^)]+\)\s*\.\s*(?:not\s*\.\s*)?[A-Za-z_][A-Za-z0-9_]*\s*\("),
            re.compile(r"\bassert(?:\.[A-Za-z_][A-Za-z0-9_]*)?\s*\("),
            re.compile(r"^\s*assert\s+[^=\n]+", re.MULTILINE),
        ]
        for pat in assertion_patterns:
            assertion_count += len(pat.findall(uncommented_nontrivial))

        return {
            "assertion_count": assertion_count,
            "has_skipped_or_only": len(skip_reasons) > 0,
            "skip_reasons": skip_reasons,
        }

    @classmethod
    def scan_production_file_quality(cls, prod_file: Path) -> List[str]:
        """
        Verifica por código el cumplimiento de core_rules.md en archivos de producción (Punto 14):
        - Prohibición de TODO, FIXME, XXX, TBD, HACK, NotImplementedError.
        - Prohibición de mocks (jest.mock, vi.mock, unittest.mock, MagicMock, sinon) en producción.
        - Prohibición de funciones/métodos vacíos con solo 'pass' o '...' en Python, y 'except: pass' silenciosos.
        """
        violations: List[str] = []
        content = prod_file.read_text(encoding="utf-8", errors="ignore")

        for pattern, label in FORBIDDEN_PROD_PATTERNS:
            if pattern.search(content):
                violations.append(f"contiene '{label}'")

        if prod_file.suffix.lower() == ".py":
            try:
                tree = ast.parse(content, filename=str(prod_file))
                for node in ast.walk(tree):
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        # Ignorar métodos con @abstractmethod
                        is_abstract = any(
                            "abstractmethod" in (ast.unparse(d) if hasattr(ast, "unparse") else "")
                            for d in node.decorator_list
                        )
                        if is_abstract:
                            continue
                        body = list(node.body)
                        # Descartar docstring inicial si existe
                        if (
                            body
                            and isinstance(body[0], ast.Expr)
                            and isinstance(body[0].value, ast.Constant)
                            and isinstance(body[0].value.value, str)
                        ):
                            body = body[1:]
                        if len(body) == 1:
                            stmt = body[0]
                            if isinstance(stmt, ast.Pass):
                                violations.append(f"función '{node.name}' vacía con 'pass'")
                            elif (
                                isinstance(stmt, ast.Expr)
                                and isinstance(stmt.value, ast.Constant)
                                and stmt.value.value is Ellipsis
                            ):
                                violations.append(f"función '{node.name}' vacía con '...' (Ellipsis)")
                    elif isinstance(node, ast.ExceptHandler):
                        if len(node.body) == 1 and isinstance(node.body[0], ast.Pass):
                            violations.append("bloque 'except: pass' silencioso")
            except SyntaxError as err:
                violations.append(f"error de sintaxis Python ({err.msg} en línea {err.lineno})")

        return violations

    # =========================================================================
    # FASE 1: Validador Anti-Alucinación de Relevamiento (Punto 8)
    # =========================================================================
    def validate_phase1_output(
        self,
        report_path: Path,
        vault_docs: Dict[str, str],
    ) -> Tuple[bool, str, Dict[str, Any]]:
        if not report_path.exists():
            return False, f"No se encontró el informe requerido en '{report_path}'.", {}

        report_text = report_path.read_text(encoding="utf-8")
        missing_headers: List[str] = []
        for sec_num in range(1, 7):
            sec_pat = re.compile(
                rf"^#{{2,3}}\s*\**(?:Secci[óo]n\s*)?{sec_num}[\.\)\s:-]",
                re.IGNORECASE | re.MULTILINE,
            )
            if not sec_pat.search(report_text):
                missing_headers.append(f"## {sec_num}.")
        if missing_headers:
            return (
                False,
                f"El informe de Fase 1 no respeta la estructura de 6 secciones. Faltan: {missing_headers}",
                {},
            )

        combined_source = "\n".join(vault_docs.values())
        source_ids: Set[str] = set(ID_PATTERN.findall(combined_source))

        split_match = re.split(
            r"^#{2,3}\s*\**(?:Secci[óo]n\s*)?5[\.\)\s:-]",
            report_text,
            maxsplit=1,
            flags=re.IGNORECASE | re.MULTILINE,
        )
        normative_section = split_match[0]
        findings_section = split_match[1] if len(split_match) > 1 else ""

        cited_normative_ids: Set[str] = set(ID_PATTERN.findall(normative_section))
        cited_findings_ids: Set[str] = set(ID_PATTERN.findall(findings_section))

        # SIEMPRE verificar IDs alucinados en secciones 1 a 4 (incluso si source_ids está vacío)
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

        require_formal_ids = bool(self.config.get("limits", {}).get("require_formal_ids", True))
        formal_ids_used = len(source_ids) > 0

        if not source_ids:
            if require_formal_ids:
                self.log_event(
                    "PHASE_1",
                    "REJECTED",
                    "Documentos en inbox/ sin códigos formales (RF-*, BR-*, etc.) con require_formal_ids=True.",
                )
                return (
                    False,
                    "Control Anti-Alucinación Fase 1 bloqueado: Los documentos fuente en 'inbox/' no contienen "
                    "ningún código de requerimiento formal (ej. RF-MOD-001, BR-MOD-001, TR-MOD-001). "
                    "El harness no dará por válido un control de trazabilidad vacío.\n"
                    "Soluciones:\n"
                    "  1. Agrega identificadores formales a tus documentos de requerimientos en 'inbox/'.\n"
                    "  2. O si deseas trabajar con documentos libres sin códigos, configura 'require_formal_ids: false' "
                    "en 'config.local.yaml' para activar el control por anclas de encabezados con advertencia explícita.",
                    {},
                )

            # Modo explícito sin IDs formales: extraer encabezados normativos del documento fuente
            heading_anchors = [
                h.strip()
                for h in re.findall(r"^#{1,3}\s+(.+)$", combined_source, flags=re.MULTILINE)
                if h.strip()
            ]
            self.log_event(
                "PHASE_1",
                "WARNING",
                f"Documentos sin IDs formales (require_formal_ids=False). Se extrajeron {len(heading_anchors)} encabezados.",
            )
            extracted_catalog: Dict[str, Any] = {
                "approved_ids": [],
                "pending_ids": [],
                "referenced_unknown_ids": sorted(cited_findings_ids),
                "formal_ids_used": False,
                "heading_anchors": heading_anchors[:50],
            }
            return (
                True,
                "[ADVERTENCIA: DOCUMENTO SIN CÓDIGOS FORMALES] El informe de Fase 1 respetó las 6 secciones, "
                "pero el documento fuente no contiene IDs tipo RF-X-01. Se desactivó la validación por IDs "
                "por configuración explícita ('require_formal_ids: false').",
                extracted_catalog,
            )

        approved_ids = sorted([i for i in source_ids if not i.startswith(PENDING_PREFIXES)])
        pending_ids = sorted([i for i in source_ids if i.startswith(PENDING_PREFIXES)])
        referenced_unknown = sorted((cited_findings_ids - source_ids))

        extracted_catalog = {
            "approved_ids": approved_ids,
            "pending_ids": pending_ids,
            "referenced_unknown_ids": referenced_unknown,
            "formal_ids_used": formal_ids_used,
        }
        self.log_event(
            "PHASE_1",
            "PASSED",
            f"IDs aprobados: {len(approved_ids)}, Pendientes (SUP/DEC/Q): {len(pending_ids)}",
        )
        return True, f"Informe de Fase 1 validado correctamente ({len(approved_ids)} IDs normativos).", extracted_catalog

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

        require_formal_ids = bool(self.config.get("limits", {}).get("require_formal_ids", True))
        if require_formal_ids and not approved_ids:
            return (
                False,
                "Control de Cobertura Fase 2 bloqueado: No hay IDs aprobados indexados en Fase 1 y "
                "'require_formal_ids' está activo.",
                {},
            )

        plan_text = plan_path.read_text(encoding="utf-8")

        module_pattern = re.compile(
            r"^###\s+\**M[óo]dulo\**\s+[`\[]?([A-Za-z0-9_-]+)[`\]]?\**\s*[:\-](.*?)(?=^###\s+\**M[óo]dulo\b|^##\s+\**4[\.\)\s:-]|\Z)",
            re.DOTALL | re.MULTILINE | re.IGNORECASE,
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

            # Soportar distintos formatos Markdown de distintos modelos de IA:
            # - **allowed_paths:** [a, b]  |  - **allowed_paths**: [a, b]  |  - `allowed_paths`: [a, b]
            # o lista de viñetas debajo de allowed_paths:
            raw_paths: List[str] = []
            bracket_match = re.search(
                r"allowed_paths[`*]*\s*:\s*\**\[(.*?)\]",
                mod_body,
                re.DOTALL | re.IGNORECASE,
            )
            if bracket_match:
                raw_paths = [
                    self.normalize_rel_path(p.strip().strip("`'\"*"))
                    for p in bracket_match.group(1).split(",")
                    if self.normalize_rel_path(p.strip().strip("`'\"*"))
                ]
            else:
                bullet_match = re.search(
                    r"allowed_paths[`*]*\s*:\s*\**\s*\n((?:\s*[-*]\s+[^\n]+\n?)+)",
                    mod_body,
                    re.IGNORECASE,
                )
                if bullet_match:
                    for line in bullet_match.group(1).splitlines():
                        item = re.sub(r"^\s*[-*]\s+", "", line).strip().strip("`'\"*,")
                        norm_item = self.normalize_rel_path(item)
                        if norm_item:
                            raw_paths.append(norm_item)
                else:
                    return (
                        False,
                        f"El módulo '{mod_id}' no declara el campo obligatorio 'allowed_paths: [...]'.",
                        {},
                    )

            if not raw_paths:
                return (
                    False,
                    f"El módulo '{mod_id}' tiene la lista 'allowed_paths' vacía.",
                    {},
                )

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
    # FASE 3: Snapshots Delta, Control de Alcance, Commits, Ignorados y Rollback Real (Puntos 5, 6, 12)
    # =========================================================================
    def _run_git(self, args: List[str]) -> subprocess.CompletedProcess:
        git_bin = shutil.which("git") or "git"
        return subprocess.run(
            [git_bin] + args,
            cwd=self.project_root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
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
            changed_files.append(self.normalize_rel_path(path_part))
        return changed_files

    def _scan_sensitive_files_hashes(self) -> Dict[str, str]:
        """
        Calcula hashes SHA-256 de archivos sensibles que Git suele ignorar (.env*, .git/hooks/*, .git/config)
        y archivos críticos de ejecución (package.json, pyproject.toml) para detectar modificaciones furtivas (Punto 6).
        """
        hashes: Dict[str, str] = {}
        candidates: List[Path] = []

        for env_file in self.project_root.glob(".env*"):
            if env_file.is_file():
                candidates.append(env_file)

        git_dir = self.project_root / ".git"
        if git_dir.is_dir():
            git_cfg = git_dir / "config"
            if git_cfg.is_file():
                candidates.append(git_cfg)
            hooks_dir = git_dir / "hooks"
            if hooks_dir.is_dir():
                for hf in hooks_dir.iterdir():
                    if hf.is_file() and not hf.name.endswith(".sample"):
                        candidates.append(hf)

        for root_cfg in ("package.json", "pyproject.toml", "pytest.ini", "tsconfig.json"):
            p = self.project_root / root_cfg
            if p.is_file():
                candidates.append(p)

        for c in candidates:
            rel = c.relative_to(self.project_root).as_posix()
            sha = self._compute_sha256(c)
            if sha:
                hashes[rel] = sha
        return hashes

    def _scan_existing_tests_snapshot(self) -> Dict[str, Dict[str, Any]]:
        """
        Registra los archivos de test existentes y su cantidad de aserciones antes de que la IA edite,
        para impedir que la IA borre tests o reduzca aserciones para que den verde (Punto 6).
        """
        snapshot: Dict[str, Dict[str, Any]] = {}
        res = self._run_git(["ls-files"])
        tracked = [self.normalize_rel_path(line) for line in res.stdout.splitlines() if line.strip()] if res.returncode == 0 else []

        for rel_file in tracked:
            if self.is_test_file(rel_file):
                full_p = self.project_root / rel_file
                if full_p.is_file():
                    analysis = self.analyze_test_file(full_p)
                    snapshot[rel_file] = {
                        "sha256": self._compute_sha256(full_p),
                        "assertion_count": analysis["assertion_count"],
                    }
        return snapshot

    def _is_path_in_allowed(self, rel_file: str, allowed_paths: List[str]) -> bool:
        if not allowed_paths:
            return False
        resolved_allowed = [(self.project_root / p).resolve() for p in allowed_paths]
        candidate_path = (self.project_root / rel_file).resolve()
        if not candidate_path.is_relative_to(self.project_root):
            return False
        return any(
            candidate_path == allowed_p or candidate_path.is_relative_to(allowed_p)
            for allowed_p in resolved_allowed
        )

    def prepare_pre_module_snapshot(
        self,
        module_id: str,
        allowed_paths: Optional[List[str]] = None,
        require_clean: bool = False,
    ) -> Dict[str, Any]:
        """
        Toma una foto base (baseline) antes de arrancar el módulo (Puntos 5 y 6):
        - Permite trabajar en paralelo: si hay archivos modificados fuera de allowed_paths (ej. package-lock.json),
          guarda su hash en 'baseline_dirty_files' y los ignora al validar el módulo salvo que la IA los vuelva a tocar.
        - Si 'require_clean_allowed_paths' está activo y hay cambios sin commit DENTRO de allowed_paths del módulo,
          avisa y guarda respaldo antes de iniciar.
        """
        dirty_files = self.get_git_modified_and_untracked()
        if dirty_files and require_clean:
            raise RuntimeError(
                f"Protección de código activada (require_clean_git_before_build=True): El repositorio en "
                f"'{self.project_root}' tiene cambios sin confirmar ({dirty_files[:5]})."
            )

        require_clean_allowed = bool(self.config.get("limits", {}).get("require_clean_allowed_paths", True))
        if dirty_files and allowed_paths and require_clean_allowed:
            dirty_in_scope = [f for f in dirty_files if self._is_path_in_allowed(f, allowed_paths)]
            if dirty_in_scope:
                raise RuntimeError(
                    f"Protección de alcance del módulo '{module_id}': Hay cambios previos sin confirmar "
                    f"DENTRO de las rutas del módulo ({dirty_in_scope}). Haz commit, stash o ejecuta "
                    f"'python main.py rollback --module {module_id}' antes de construir este módulo. "
                    "(Los cambios fuera de este módulo sí están permitidos)."
                )

        baseline_dirty: Dict[str, str] = {}
        for rel_f in dirty_files:
            full_f = self.project_root / rel_f
            baseline_dirty[rel_f] = self._compute_sha256(full_f) or "DELETED"

        head_res = self._run_git(["rev-parse", "HEAD"])
        head_sha = head_res.stdout.strip() if head_res.returncode == 0 else None

        snapshot = {
            "snapshot_head": head_sha,
            "captured_at": datetime.now().isoformat(),
            "baseline_dirty_files": baseline_dirty,
            "baseline_sensitive_files": self._scan_sensitive_files_hashes(),
            "baseline_tests": self._scan_existing_tests_snapshot(),
        }
        self.log_event(
            "PHASE_3",
            "SNAPSHOT",
            f"Módulo {module_id} inició sobre HEAD={head_sha} (archivos sucios preexistentes ignorados: {len(baseline_dirty)})",
        )
        return snapshot

    def compute_module_delta(self, snapshot_data: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Calcula únicamente lo que cambió desde que arrancó el módulo (Puntos 5 y 6):
        1. Archivos modificados/creados en working tree cuyo hash difiere del baseline pre-módulo.
        2. Archivos commiteados por la IA si HEAD cambió respecto de snapshot_head.
        3. Archivos sensibles ignorados por Git (.env*, .git/hooks/*) que hayan cambiado de hash.
        """
        snap = snapshot_data or {}
        snapshot_head = snap.get("snapshot_head")
        baseline_dirty: Dict[str, str] = snap.get("baseline_dirty_files", {})
        baseline_sensitive: Dict[str, str] = snap.get("baseline_sensitive_files", {})

        head_res = self._run_git(["rev-parse", "HEAD"])
        current_head = head_res.stdout.strip() if head_res.returncode == 0 else None
        agent_made_commits = bool(snapshot_head and current_head and snapshot_head != current_head)

        committed_since_start: List[str] = []
        if agent_made_commits and snapshot_head:
            diff_res = self._run_git(["diff", "--name-only", f"{snapshot_head}..HEAD"])
            if diff_res.returncode == 0:
                committed_since_start = [
                    self.normalize_rel_path(line)
                    for line in diff_res.stdout.splitlines()
                    if line.strip()
                ]

        current_dirty = self.get_git_modified_and_untracked()
        working_delta: List[str] = []
        for rel_f in current_dirty:
            full_f = self.project_root / rel_f
            curr_hash = self._compute_sha256(full_f) or "DELETED"
            if rel_f in baseline_dirty and baseline_dirty[rel_f] == curr_hash:
                # El archivo ya estaba sucio antes de arrancar el módulo y la IA no lo tocó (Punto 5)
                continue
            working_delta.append(rel_f)

        # Verificar archivos sensibles o ignorados por Git (.env*, .git/hooks/*, etc.) (Punto 6)
        current_sensitive = self._scan_sensitive_files_hashes()
        sensitive_touched: List[str] = []
        all_sens_keys = set(baseline_sensitive.keys()) | set(current_sensitive.keys())
        for s_key in sorted(all_sens_keys):
            if baseline_sensitive.get(s_key) != current_sensitive.get(s_key):
                sensitive_touched.append(s_key)

        all_changed = sorted(set(working_delta) | set(committed_since_start))
        return {
            "changed_files": all_changed,
            "working_delta": working_delta,
            "committed_files": committed_since_start,
            "agent_made_commits": agent_made_commits,
            "sensitive_touched": sensitive_touched,
            "current_head": current_head,
        }

    def scan_security_before_tests(
        self,
        changed_files: List[Path],
        allowed_paths: List[str],
    ) -> List[str]:
        """
        Escanea estáticamente los archivos generados/modificados por la IA ANTES de ejecutar npm test / pytest
        en la máquina del desarrollador (Punto 10).
        """
        if not bool(self.config.get("testing", {}).get("pre_test_security_scan", True)):
            return []

        alerts: List[str] = []
        for f_path in changed_files:
            if not f_path.is_file():
                continue
            rel_name = f_path.relative_to(self.project_root).as_posix()
            raw_content = f_path.read_text(encoding="utf-8", errors="ignore")
            uncommented = self._strip_code_comments(raw_content)

            for pattern, risk_label in DANGEROUS_CODE_PATTERNS:
                if pattern.search(uncommented):
                    alerts.append(f"{rel_name} (patrón de riesgo detectado: {risk_label})")

        return alerts

    def validate_phase3_module(
        self,
        module_id: str,
        allowed_paths: List[str],
        agent_output_text: str,
        snapshot_data: Optional[Dict[str, Any]] = None,
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

        # 2. Calcular delta real desde el snapshot de inicio del módulo (Puntos 5 y 6)
        delta = self.compute_module_delta(snapshot_data)
        changed_rel_paths = delta["changed_files"]

        # Bloquear si la IA hizo commits por su cuenta para ocultar cambios a git status (Punto 6)
        block_commits = bool(self.config.get("limits", {}).get("block_agent_commits", True))
        if delta["agent_made_commits"] and block_commits:
            self.log_event(
                "PHASE_3",
                "UNAUTHORIZED_COMMIT",
                f"{module_id} ejecutó git commit por su cuenta (archivos: {delta['committed_files']})",
            )
            return (
                False,
                f"Violación de seguridad en '{module_id}': El agente ejecutó 'git commit' por su cuenta "
                f"(HEAD cambió de {snapshot_data.get('snapshot_head') if snapshot_data else '?'} a {delta['current_head']}). "
                f"Archivos commiteados: {delta['committed_files']}. Los commits deben ser realizados por el desarrollador.",
                {"unauthorized_commits": delta["committed_files"]},
            )

        # Bloquear si la IA modificó archivos sensibles ignorados por Git (.env, .git/hooks)fuera de alcance (Punto 6)
        unauthorized_files: List[str] = []
        for sens_rel in delta["sensitive_touched"]:
            if not self._is_path_in_allowed(sens_rel, allowed_paths):
                unauthorized_files.append(f"{sens_rel} (Archivo sensible/ignorado por Git modificado)")

        if not changed_rel_paths and not unauthorized_files:
            return False, f"El agente no realizó ninguna modificación de código para el módulo '{module_id}'.", {}

        resolved_allowed = [(self.project_root / p).resolve() for p in allowed_paths]
        prod_files_changed: List[Path] = []
        test_files_changed: List[Path] = []

        for rel_file in changed_rel_paths:
            candidate_path = self.project_root / rel_file
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
                if self.is_test_file(rel_file):
                    test_files_changed.append(resolved_candidate)
                else:
                    prod_files_changed.append(resolved_candidate)

        if unauthorized_files:
            self.log_event("PHASE_3", "SCOPE_VIOLATION", f"{module_id} tocó: {unauthorized_files}")
            return (
                False,
                f"Violación de alcance en '{module_id}': Se modificaron o crearon archivos fuera de "
                f"allowed_paths: {unauthorized_files}. Usa 'python main.py rollback --module "
                f"{module_id}' para revertir automáticamente los cambios del módulo.",
                {"unauthorized_files": unauthorized_files},
            )

        # 3. Vigilar integridad de tests preexistentes (Punto 6)
        protect_tests = bool(self.config.get("limits", {}).get("protect_existing_tests", True))
        baseline_tests: Dict[str, Dict[str, Any]] = (snapshot_data or {}).get("baseline_tests", {})
        if protect_tests and baseline_tests:
            for base_test_rel, base_info in baseline_tests.items():
                base_test_path = self.project_root / base_test_rel
                if not base_test_path.exists():
                    self.log_event("PHASE_3", "TEST_TAMPERING", f"{module_id} eliminó test existente: {base_test_rel}")
                    return (
                        False,
                        f"Manipulación de tests detectada en '{module_id}': El agente eliminó el archivo de test "
                        f"preexistente '{base_test_rel}'.",
                        {},
                    )
                if base_test_rel in changed_rel_paths:
                    curr_analysis = self.analyze_test_file(base_test_path)
                    old_count = int(base_info.get("assertion_count", 0))
                    if curr_analysis["assertion_count"] < old_count:
                        self.log_event(
                            "PHASE_3",
                            "TEST_TAMPERING",
                            f"{module_id} redujo aserciones en {base_test_rel} ({old_count} -> {curr_analysis['assertion_count']})",
                        )
                        return (
                            False,
                            f"Manipulación de tests detectada en '{module_id}': El archivo de test '{base_test_rel}' "
                            f"tenía {old_count} aserciones antes del módulo y ahora tiene {curr_analysis['assertion_count']}. "
                            "No está permitido borrar aserciones para forzar que los tests pasen.",
                            {},
                        )

        # 4. Escanear reglas de core_rules.md en archivos de producción (Puntos 7 y 14)
        shortcut_violations: List[str] = []
        for prod_file in prod_files_changed:
            file_violations = self.scan_production_file_quality(prod_file)
            for v in file_violations:
                shortcut_violations.append(f"{prod_file.relative_to(self.project_root).as_posix()} ({v})")

        if shortcut_violations:
            self.log_event("PHASE_3", "SHORTCUT_DETECTED", f"{module_id}: {shortcut_violations}")
            return (
                False,
                f"Incumplimiento de reglas de calidad en código de producción: {shortcut_violations}.",
                {"shortcut_violations": shortcut_violations},
            )

        # 5. Verificar que existan pruebas unitarias con aserciones reales y sin .skip/.only (Puntos 6 y 7)
        if not test_files_changed:
            return (
                False,
                f"El módulo '{module_id}' no creó ni actualizó ningún archivo de pruebas unitarias.",
                {},
            )

        total_real_assertions = 0
        skipped_test_Issues: List[str] = []
        for t_file in test_files_changed:
            analysis = self.analyze_test_file(t_file)
            total_real_assertions += analysis["assertion_count"]
            if analysis["has_skipped_or_only"]:
                rel_t = t_file.relative_to(self.project_root).as_posix()
                skipped_test_Issues.append(f"{rel_t}: {analysis['skip_reasons']}")

        if skipped_test_Issues:
            self.log_event("PHASE_3", "SKIPPED_TESTS_DETECTED", f"{module_id}: {skipped_test_Issues}")
            return (
                False,
                f"Tests desactivados o filtrados (.skip / .only / @skip) detectados en '{module_id}': {skipped_test_Issues}.",
                {},
            )

        if total_real_assertions == 0:
            return (
                False,
                f"Los archivos de test del módulo '{module_id}' no contienen aserciones verificables reales "
                "(los comentarios o 'assert True' triviales no cuentan).",
                {},
            )

        # 6. Escaneo estático de seguridad sobre los archivos generados antes de ejecutar el test runner (Punto 10)
        security_alerts = self.scan_security_before_tests(
            prod_files_changed + test_files_changed, allowed_paths
        )
        if security_alerts:
            self.log_event("PHASE_3", "SECURITY_BLOCKED", f"{module_id}: {security_alerts}")
            return (
                False,
                f"Bloqueo de seguridad pre-ejecución en '{module_id}': Se detectaron llamadas potencialmente "
                f"peligrosas en el código generado antes de correr los tests: {security_alerts}.",
                {"security_alerts": security_alerts},
            )

        # 7. Ejecutar comando de pruebas unitarias de forma segura y multiplataforma (Puntos 2 y 10)
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

        self.log_event("PHASE_3", "PASSED", f"Módulo {module_id} validado ({total_real_assertions} aserciones) y unit tests en PASS.")
        return (
            True,
            f"Módulo '{module_id}' validado con éxito (alcance delta, código limpio, {total_real_assertions} aserciones reales y unit tests en verde).",
            {
                "prod_files": [p.relative_to(self.project_root).as_posix() for p in prod_files_changed],
                "test_files": [t.relative_to(self.project_root).as_posix() for t in test_files_changed],
                "assertion_count": total_real_assertions,
                "test_output_summary": test_output[-500:],
            },
        )

    def rollback_module(
        self,
        module_id: str,
        allowed_paths: List[str],
        snapshot_data: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Deshace de forma real y segura los cambios introducidos por un módulo fallido (Punto 12):
        1. Guarda una copia de respaldo de los archivos del intento fallido en .state/rollback_backups/<module_id>_<ts>/.
        2. Revierte los archivos rastreados por Git a su estado en snapshot_head (o HEAD) y elimina los archivos
           nuevos (untracked) creados durante el módulo.
        3. Preserva intactos los archivos fuera del módulo que el desarrollador ya tenía modificados antes de arrancar.
        """
        delta = self.compute_module_delta(snapshot_data)
        baseline_dirty: Dict[str, str] = (snapshot_data or {}).get("baseline_dirty_files", {})
        snapshot_head = (snapshot_data or {}).get("snapshot_head") or "HEAD"

        # Identificar archivos a revertir: el delta del módulo + cualquier archivo sucio dentro de allowed_paths
        # que no estuviera exactamente igual en baseline_dirty
        candidates = set(delta["working_delta"])
        for rel_f in self.get_git_modified_and_untracked():
            if self._is_path_in_allowed(rel_f, allowed_paths):
                full_f = self.project_root / rel_f
                curr_hash = self._compute_sha256(full_f) or "DELETED"
                if baseline_dirty.get(rel_f) != curr_hash:
                    candidates.add(rel_f)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_folder = self.rollback_dir / f"{module_id}_{timestamp}"
        backed_up: List[str] = []
        reverted_tracked: List[str] = []
        removed_untracked: List[str] = []

        # Consultar archivos rastreados en snapshot_head / HEAD
        tracked_set: Set[str] = set()
        ls_res = self._run_git(["ls-tree", "-r", "--name-only", snapshot_head])
        if ls_res.returncode == 0:
            tracked_set = {self.normalize_rel_path(line) for line in ls_res.stdout.splitlines() if line.strip()}
        else:
            ls_idx = self._run_git(["ls-files"])
            if ls_idx.returncode == 0:
                tracked_set = {self.normalize_rel_path(line) for line in ls_idx.stdout.splitlines() if line.strip()}

        for rel_f in sorted(candidates):
            full_p = (self.project_root / rel_f).resolve()
            if not full_p.is_relative_to(self.project_root):
                continue

            if full_p.exists() and full_p.is_file():
                dest_bak = backup_folder / rel_f
                dest_bak.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(full_p, dest_bak)
                backed_up.append(rel_f)

            if rel_f in tracked_set:
                self._run_git(["checkout", snapshot_head, "--", rel_f])
                reverted_tracked.append(rel_f)
            else:
                if full_p.exists() and full_p.is_file():
                    try:
                        full_p.unlink()
                        removed_untracked.append(rel_f)
                    except OSError:
                        pass

        self.log_event(
            "PHASE_3",
            "ROLLBACK_EXECUTED",
            f"Módulo {module_id}: revertidos={reverted_tracked}, eliminados_untracked={removed_untracked}, backup={backup_folder}",
        )
        return {
            "backed_up": backed_up,
            "backup_folder": str(backup_folder) if backed_up else None,
            "reverted_tracked": reverted_tracked,
            "removed_untracked": removed_untracked,
        }

    # =========================================================================
    # FASE 4: Validador de Matriz de Cumplimiento Real, Trazabilidad y Regresión (Punto 9)
    # =========================================================================
    def validate_phase4_output(
        self,
        report_path: Path,
        approved_ids: List[str],
    ) -> Tuple[bool, str]:
        if not report_path.exists():
            return False, f"No se encontró el reporte final en '{report_path}'."

        report_text = report_path.read_text(encoding="utf-8")

        # 1. Verificar que todos los IDs aprobados figuren en el reporte
        missing_ids = [i for i in approved_ids if i not in report_text]
        if missing_ids:
            return (
                False,
                f"El reporte final de Fase 4 omitió contrastar los siguientes IDs aprobados: {missing_ids}",
            )

        # 2. Parsear filas de la Matriz de Contraste para comprobar que cada requisito aprobado:
        #    - Esté en estado CUMPLIDO (no en DESVIACIÓN / PENDIENTE / NO CUMPLIDO)
        #    - Cite un archivo de código de producción real y un archivo de test real con aserciones
        table_lines = [
            line.strip()
            for line in report_text.splitlines()
            if line.strip().startswith("|") and "---" not in line
        ]

        deviations_found: List[str] = []
        unverified_rows: List[str] = []
        cited_prod_files: Set[Path] = set()
        cited_test_files: Set[Path] = set()

        mandatory_table_prefixes = ("BR-", "RF-", "RNF-", "TR-", "AT-")

        for req_id in approved_ids:
            matching_rows = [row for row in table_lines if re.search(rf"\b{re.escape(req_id)}\b", row)]
            if not matching_rows:
                if req_id.startswith(mandatory_table_prefixes):
                    unverified_rows.append(f"{req_id} (no figura en ninguna fila de la tabla de contraste)")
                continue

            row_text = matching_rows[0]
            if re.search(r"\b(?:DESVIACI[ÓO]N|INCUMPLIDO|NO\s+CUMPLIDO|PENDIENTE|FAIL|FALTANTE)\b", row_text, re.IGNORECASE):
                deviations_found.append(req_id)
                continue

            if not re.search(r"\b(?:CUMPLIDO|PASSED|APROBADO|OK)\b", row_text, re.IGNORECASE):
                unverified_rows.append(f"{req_id} (la fila no declara estado CUMPLIDO)")
                continue

            row_paths = [
                self.normalize_rel_path(p)
                for p in re.findall(r"`([A-Za-z0-9_./\\-]+\.[A-Za-z0-9]+)`", row_text)
            ]
            row_prod = [p for p in row_paths if not self.is_test_file(p)]
            row_test = [p for p in row_paths if self.is_test_file(p)]

            if not row_prod or not row_test:
                unverified_rows.append(
                    f"{req_id} (debe citar entre backticks al menos un archivo de código y un archivo de test)"
                )
                continue

            for rp in row_prod:
                full_p = (self.project_root / rp).resolve()
                if not full_p.is_file() or not full_p.is_relative_to(self.project_root):
                    unverified_rows.append(f"{req_id} (archivo de código inexistente: '{rp}')")
                else:
                    cited_prod_files.add(full_p)

            for rt in row_test:
                full_t = (self.project_root / rt).resolve()
                if not full_t.is_file() or not full_t.is_relative_to(self.project_root):
                    unverified_rows.append(f"{req_id} (archivo de test inexistente: '{rt}')")
                else:
                    cited_test_files.add(full_t)

        if deviations_found:
            self.log_event("PHASE_4", "DEVIATIONS_REJECTED", f"IDs con desviación: {deviations_found}")
            return (
                False,
                f"Fase 4 rechazada: Los siguientes requerimientos fueron reportados con DESVIACIÓN o INCUMPLIMIENTO: {deviations_found}.",
            )

        if unverified_rows:
            self.log_event("PHASE_4", "INVALID_MATRIX_ROWS", f"Filas inválidas: {unverified_rows}")
            return (
                False,
                f"Control de Evidencia Física Fase 4 falló en la Matriz de Contraste: {unverified_rows}",
            )

        # 3. También verificar cualquier otra ruta con barra citada en el reporte si approved_ids estaba vacío
        path_candidates = [
            self.normalize_rel_path(p)
            for p in re.findall(r"`([A-Za-z0-9_./\\-]+\.[A-Za-z0-9]+)`", report_text)
        ]
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
            if not full_p.exists() or not full_p.is_relative_to(self.project_root):
                non_existent_paths.append(rel_p)

        if non_existent_paths:
            self.log_event("PHASE_4", "HALLUCINATED_PATHS", f"Rutas inexistentes: {non_existent_paths}")
            return (
                False,
                f"Control Anti-Alucinación Fase 4 falló: El reporte cita archivos "
                f"que NO existen físicamente en '{self.project_root}': {non_existent_paths}",
            )

        # 4. Auditar calidad real de los archivos de producción y test citados en Fase 4
        prod_issues: List[str] = []
        for pf in sorted(cited_prod_files):
            v_list = self.scan_production_file_quality(pf)
            for v in v_list:
                prod_issues.append(f"{pf.relative_to(self.project_root).as_posix()} ({v})")
        if prod_issues:
            return (
                False,
                f"Control de Calidad Fase 4 falló: Archivos de producción citados contienen código incompleto: {prod_issues}",
            )

        test_issues: List[str] = []
        for tf in sorted(cited_test_files):
            t_analysis = self.analyze_test_file(tf)
            rel_tf = tf.relative_to(self.project_root).as_posix()
            if t_analysis["has_skipped_or_only"]:
                test_issues.append(f"{rel_tf} (contiene tests saltados/filtrados: {t_analysis['skip_reasons']})")
            if t_analysis["assertion_count"] == 0:
                test_issues.append(f"{rel_tf} (0 aserciones reales)")
        if test_issues:
            return (
                False,
                f"Control de Pruebas Fase 4 falló: Archivos de test citados no son válidos: {test_issues}",
            )

        # 5. Si strict_code_traceability está activo, verificar que cada ID aprobado aparezca en el código/tests citados
        if bool(self.config.get("limits", {}).get("strict_code_traceability", False)) and approved_ids:
            combined_code = "\n".join(
                f.read_text(encoding="utf-8", errors="ignore")
                for f in (cited_prod_files | cited_test_files)
            )
            untraced_in_code = [req_id for req_id in approved_ids if req_id not in combined_code]
            if untraced_in_code:
                return (
                    False,
                    f"Control de Trazabilidad en Código (strict_code_traceability=True) falló: "
                    f"Los siguientes IDs no están referenciados en los comentarios ni tests de los archivos citados: {untraced_in_code}",
                )

        # 6. Escaneo de seguridad y ejecución de la suite completa de regresión
        sec_alerts = self.scan_security_before_tests(list(cited_prod_files | cited_test_files), [])
        if sec_alerts:
            return False, f"Bloqueo de seguridad antes de correr regresión global: {sec_alerts}"

        reg_cmd = self.config.get("testing", {}).get("regression_test_cmd", "")
        timeout_sec = int(self.config.get("testing", {}).get("timeout_seconds", 300))
        ok, reg_output = self.run_safe_command(reg_cmd, timeout_sec)
        if not ok:
            self.log_event("PHASE_4", "REGRESSION_FAILED", reg_output[:300])
            return False, f"La suite global de pruebas de regresión falló:\n{reg_output}"

        self.log_event("PHASE_4", "PASSED", "Contraste final, evidencia de código/tests y suite de regresión aprobados.")
        return True, "Fase 4 completada: 100% de requerimientos en CUMPLIDO, archivos verificados y regresión en PASS."

    # =========================================================================
    # Ejecutor Seguro y Multiplataforma de Subprocesos (Puntos 2 y 10)
    # =========================================================================
    @staticmethod
    def _split_command_cross_platform(command_str: str) -> List[str]:
        """
        Divide el comando en argumentos preservando las barras invertidas '\\' de rutas en Windows (Punto 2).
        """
        if os.name == "nt":
            raw_tokens = shlex.split(command_str, posix=False)
            cleaned: List[str] = []
            for tok in raw_tokens:
                if len(tok) >= 2 and ((tok[0] == '"' and tok[-1] == '"') or (tok[0] == "'" and tok[-1] == "'")):
                    tok = tok[1:-1]
                cleaned.append(tok)
            return cleaned
        return shlex.split(command_str, posix=True)

    def _resolve_executable(self, binary_name: str) -> Optional[str]:
        """
        Resuelve el ejecutable real según el sistema operativo (ej. npm -> npm.cmd, pytest -> pytest.exe en Windows)
        buscando primero en node_modules/.bin y .venv del proyecto y luego en el PATH del sistema (Punto 2).
        """
        candidate = Path(binary_name)
        if candidate.is_Absolute() if hasattr(candidate, "is_Absolute") else candidate.is_absolute():
            if candidate.is_file():
                return str(candidate)
            resolved = shutil.which(binary_name)
            return resolved

        if "/" in binary_name or "\\" in binary_name:
            rel_candidate = (self.project_root / binary_name).resolve()
            if rel_candidate.is_file():
                return str(rel_candidate)
            resolved = shutil.which(str(rel_candidate))
            if resolved:
                return resolved

        local_bin_dirs = [
            self.project_root / "node_modules" / ".bin",
            self.project_root / ".venv" / ("Scripts" if os.name == "nt" else "bin"),
            self.project_root / "venv" / ("Scripts" if os.name == "nt" else "bin"),
        ]
        for bin_dir in local_bin_dirs:
            if bin_dir.is_dir():
                found_local = shutil.which(binary_name, path=str(bin_dir))
                if found_local:
                    return found_local

        return shutil.which(binary_name)

    def _build_sanitized_env(self) -> Dict[str, str]:
        """
        Construye un entorno de ejecución filtrado que no expone llaves ni secretos del desarrollador
        al código generado por la IA durante la corrida de tests (Punto 10).
        """
        passthrough = set(self.config.get("testing", {}).get("passthrough_env_vars", []) or [])
        clean_env: Dict[str, str] = {}
        for k, v in os.environ.items():
            k_upper = k.upper()
            if k in passthrough:
                clean_env[k] = v
                continue
            if any(pat in k_upper for pat in SENSITIVE_ENV_PATTERNS):
                continue
            clean_env[k] = v
        clean_env["CI"] = "true"
        clean_env.setdefault("NODE_ENV", "test")
        return clean_env

    def run_safe_command(self, command_str: str, timeout_seconds: int = 300) -> Tuple[bool, str]:
        if not command_str or not command_str.strip():
            return False, "El comando de pruebas en config.yaml está vacío."

        cmd_lower = command_str.lower()
        for token in DANGEROUS_CMD_TOKENS:
            if token in cmd_lower:
                return (
                    False,
                    f"Seguridad: El comando '{command_str}' contiene el operador o patrón prohibido '{token}'.",
                )

        cmd_args = self._split_command_cross_platform(command_str)
        if not cmd_args:
            return False, "El comando de pruebas no contiene argumentos válidos."

        resolved_exe = self._resolve_executable(cmd_args[0])
        if not resolved_exe:
            return (
                False,
                f"No se encontró el ejecutable '{cmd_args[0]}' en '{self.project_root}' ni en el PATH del sistema.",
            )
        cmd_args[0] = resolved_exe

        try:
            proc = subprocess.run(
                cmd_args,
                cwd=self.project_root,
                env=self._build_sanitized_env(),
                shell=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_seconds,
                check=False,
            )
            combined_out = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()
            return proc.returncode == 0, combined_out
        except subprocess.TimeoutExpired:
            return False, f"El comando '{command_str}' excedió el tiempo límite de {timeout_seconds}s."
        except FileNotFoundError as err:
            return False, f"No se encontró el ejecutable al correr '{command_str}': {err}"
