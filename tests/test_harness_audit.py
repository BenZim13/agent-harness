import os
import sys
import json
import shutil
import tempfile
import unittest
import subprocess
from pathlib import Path

HARNESS_ROOT = Path(__file__).parent.parent.resolve()
if str(HARNESS_ROOT) not in sys.path:
    sys.path.insert(0, str(HARNESS_ROOT))

from core.workflow import WorkflowStateMachine, WorkflowError, resolve_actor
from core.hooks import HarnessHooks
from adapters.antigravity import UniversalAgentAdapter
from main import _deep_merge_dict


class TestAgentHarnessAuditFixes(unittest.TestCase):
    """
    Verifica determinísticamente en Windows que los 14 puntos débiles relevados
    hayan sido corregidos en el código del agent-harness.
    """

    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="harness_test_"))
        self.proj_dir = self.temp_dir / "repo"
        self.state_dir = self.temp_dir / ".state"
        self.proj_dir.mkdir(parents=True, exist_ok=True)
        self.state_dir.mkdir(parents=True, exist_ok=True)

        # Inicializar repo Git real para probar snapshots delta, commits y rollback
        subprocess.run(["git", "init"], cwd=self.proj_dir, capture_output=True, check=True)
        subprocess.run(["git", "config", "user.name", "Dev Uno"], cwd=self.proj_dir, capture_output=True, check=True)
        subprocess.run(["git", "config", "user.email", "dev1@team.com"], cwd=self.proj_dir, capture_output=True, check=True)

        (self.proj_dir / ".gitignore").write_text(".env\nnode_modules/\n", encoding="utf-8")
        (self.proj_dir / "package-lock.json").write_text('{"lockfileVersion": 3}\n', encoding="utf-8")
        (self.proj_dir / ".env").write_text("SECRET_KEY=initial\n", encoding="utf-8")
        src_mod = self.proj_dir / "src" / "mod1"
        src_mod.mkdir(parents=True, exist_ok=True)
        (src_mod / "existing.py").write_text("def base():\n    return 1\n", encoding="utf-8")

        subprocess.run(["git", "add", "."], cwd=self.proj_dir, capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "initial"], cwd=self.proj_dir, capture_output=True, check=True)

        self.config = {
            "testing": {
                "unit_test_cmd": f'"{sys.executable}" -c "print(\'TESTS_OK\')"',
                "regression_test_cmd": f'"{sys.executable}" -c "print(\'REGRESSION_OK\')"',
                "timeout_seconds": 30,
                "pre_test_security_scan": True,
            },
            "limits": {
                "require_formal_ids": True,
                "require_clean_git_before_build": False,
                "require_clean_allowed_paths": True,
                "block_agent_commits": True,
                "protect_existing_tests": True,
            },
        }
        self.hooks = HarnessHooks(
            HARNESS_ROOT,
            self.proj_dir,
            self.config,
            state_dir=self.state_dir,
            actor="Dev Uno <dev1@team.com>",
        )

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    # -------------------------------------------------------------------------
    # Punto 1 y 11: Autoría, Escritura Atómica, Recuperación .bak y Concurrencia
    # -------------------------------------------------------------------------
    def test_01_and_11_actor_attribution_atomic_write_and_locking(self) -> None:
        actor = resolve_actor(self.proj_dir, {})
        self.assertIn("Dev Uno", actor)
        self.assertIn("dev1@team.com", actor)

        fsm = WorkflowStateMachine(HARNESS_ROOT, state_dir=self.state_dir, actor=actor)
        fsm.transition_to("PHASE1_DONE", "Prueba de transición")
        state = fsm.get_state()
        self.assertEqual(state["updated_by"], actor)
        self.assertEqual(state["history"][-1]["actor"], actor)
        self.assertTrue((self.state_dir / "workflow_state.json.bak").exists())

        # Simular corte de luz / archivo corrupto a mitad de escritura y verificar auto-recuperación desde .bak
        (self.state_dir / "workflow_state.json").write_text("{corrupted_json", encoding="utf-8")
        recovered = fsm.get_state()
        self.assertIn("current_state", recovered)

        # Verificar bloqueo de concurrencia cuando otro proceso vivo sostiene el lock
        with fsm.lock("build:MOD-01"):
            self.assertTrue(fsm.lock_file.exists())
            # Simular otro host activo en el lock para probar rechazo de concurrencia
            fsm.lock_file.write_text(
                json.dumps({
                    "pid": 999999,
                    "actor": "Otro Dev <dev2@team.com>",
                    "host": "other-machine",
                    "command": "build:MOD-01",
                    "acquired_at": "2099-01-01T00:00:00",
                }),
                encoding="utf-8",
            )
            with self.assertRaises(WorkflowError):
                with fsm.lock("plan"):
                    pass

    # -------------------------------------------------------------------------
    # Punto 2: Compatibilidad Windows (.cmd / rutas con '\' / shell=False)
    # -------------------------------------------------------------------------
    def test_02_windows_executable_resolution_and_backslash_paths(self) -> None:
        # Probar ruta con barras invertidas de Windows en el comando de test
        win_script = self.proj_dir / "src" / "mod1" / "existing.py"
        cmd_with_backslashes = f'"{sys.executable}" "{win_script}"'
        ok, _ = self.hooks.run_safe_command(cmd_with_backslashes)
        self.assertTrue(ok)

        # Si estamos en Windows y existe un .cmd en node_modules/.bin, debe resolverlo con shell=False
        bin_dir = self.proj_dir / "node_modules" / ".bin"
        bin_dir.mkdir(parents=True, exist_ok=True)
        if os.name == "nt":
            fake_cmd = bin_dir / "myrunner.cmd"
            fake_cmd.write_text("@echo off\r\necho RUNNER_CMD_OK\r\n", encoding="utf-8")
            ok_cmd, out_cmd = self.hooks.run_safe_command("myrunner --flag")
            self.assertTrue(ok_cmd, f"Falló ejecución de .cmd en Windows: {out_cmd}")
            self.assertIn("RUNNER_CMD_OK", out_cmd)

    # -------------------------------------------------------------------------
    # Punto 3 y 13: Multi-IA y freno explícito si falta el binario (sin falso positivo)
    # -------------------------------------------------------------------------
    def test_03_and_13_missing_ai_binary_stops_and_multi_ai_presets(self) -> None:
        ctx_file = self.state_dir / "active_context.md"
        ctx_file.write_text("# Contexto\n", encoding="utf-8")

        # En modo CLI, si el binario no existe DEBE devolver False y frenar
        adapter_missing = UniversalAgentAdapter(
            HARNESS_ROOT,
            self.proj_dir,
            {"provider": "antigravity", "mode": "cli", "binary": "binario_inexistente_xyz_999"},
        )
        ok, msg = adapter_missing.execute_task(ctx_file, "Tarea de prueba")
        self.assertFalse(ok, "El adaptador dio por válido un paso cuando faltaba el binario de la IA")
        self.assertIn("ERROR: IA NO ENCONTRADA", msg)

        # En modo manual explícito, informa las instrucciones y se identifica como manual
        adapter_manual = UniversalAgentAdapter(
            HARNESS_ROOT,
            self.proj_dir,
            {"provider": "claude", "mode": "manual"},
        )
        self.assertTrue(adapter_manual.is_manual_mode())
        ok_man, msg_man = adapter_manual.execute_task(ctx_file, "Tarea manual", command_hint="init")
        self.assertTrue(ok_man)
        self.assertIn("--validate", msg_man)

    # -------------------------------------------------------------------------
    # Punto 4: Configuración en capas (base + proyecto + local personal)
    # -------------------------------------------------------------------------
    def test_04_layered_config_merge(self) -> None:
        base = {"project_root": ".", "adapter": {"provider": "antigravity", "mode": "cli"}}
        local = {"project_root": "C:/mi_ruta/yacajobs", "adapter": {"provider": "claude"}}
        merged = _deep_merge_dict(base, local)
        self.assertEqual(merged["project_root"], "C:/mi_ruta/yacajobs")
        self.assertEqual(merged["adapter"]["provider"], "claude")
        self.assertEqual(merged["adapter"]["mode"], "cli")

    # -------------------------------------------------------------------------
    # Puntos 5, 6 y 12: Snapshot Delta (repo sucio fuera de alcance permitido),
    # detección de commits/ignorados y Rollback real con backup
    # -------------------------------------------------------------------------
    def test_05_06_12_delta_snapshot_ignored_env_and_real_rollback(self) -> None:
        # Ensuciar package-lock.json ANTES de que arranque el módulo (simulando trabajo paralelo del dev)
        (self.proj_dir / "package-lock.json").write_text('{"lockfileVersion": 3, "dirty": true}\n', encoding="utf-8")

        # Tomar snapshot para MOD-01 (allowed_paths: ["src/mod1/"])
        snap = self.hooks.prepare_pre_module_snapshot("MOD-01", allowed_paths=["src/mod1/"], require_clean=False)
        self.assertIn("package-lock.json", snap["baseline_dirty_files"])

        # La IA crea código limpio y test con aserciones reales dentro de src/mod1/
        (self.proj_dir / "src" / "mod1" / "service.py").write_text(
            "def add(a: int, b: int) -> int:\n    return a + b\n",
            encoding="utf-8",
        )
        (self.proj_dir / "src" / "mod1" / "test_service.py").write_text(
            "from service import add\ndef test_add():\n    assert add(2, 3) == 5\n",
            encoding="utf-8",
        )

        # Validar Fase 3: NO debe asustarse por package-lock.json porque no cambió desde que arrancó el módulo
        valid, msg, details = self.hooks.validate_phase3_module(
            "MOD-01", ["src/mod1/"], "Implementado MOD-01", snapshot_data=snap
        )
        self.assertTrue(valid, f"Falló validación delta con archivo sucio externo: {msg}")
        self.assertEqual(details["assertion_count"], 1)

        # Punto 6: Si la IA modifica .env (ignorado por Git), debe detectarlo y bloquear
        (self.proj_dir / ".env").write_text("SECRET_KEY=hacked_by_ai\n", encoding="utf-8")
        valid_env, msg_env, _ = self.hooks.validate_phase3_module(
            "MOD-01", ["src/mod1/"], "Toqué .env", snapshot_data=snap
        )
        self.assertFalse(valid_env)
        self.assertIn(".env", msg_env)
        # Restaurar .env
        (self.proj_dir / ".env").write_text("SECRET_KEY=initial\n", encoding="utf-8")

        # Punto 12: Probar que rollback_module deshace realmente los archivos del módulo y preserva package-lock.json
        (self.proj_dir / "src" / "mod1" / "existing.py").write_text("def base():\n    return 999\n", encoding="utf-8")
        rb = self.hooks.rollback_module("MOD-01", ["src/mod1/"], snap)
        self.assertIn("src/mod1/service.py", rb["removed_untracked"])
        self.assertIn("src/mod1/existing.py", rb["reverted_tracked"])
        self.assertFalse((self.proj_dir / "src" / "mod1" / "service.py").exists())
        self.assertIn("return 1", (self.proj_dir / "src" / "mod1" / "existing.py").read_text(encoding="utf-8"))
        # package-lock.json del desarrollador sigue intacto
        self.assertIn('"dirty": true', (self.proj_dir / "package-lock.json").read_text(encoding="utf-8"))

    # -------------------------------------------------------------------------
    # Puntos 7 y 14: Clasificación Test vs Código, Aserciones Reales y Reglas en Producción
    # -------------------------------------------------------------------------
    def test_07_and_14_test_classification_real_assertions_and_prod_rules(self) -> None:
        # 'latest/service.py' o 'contest/app.ts' NO son archivos de test
        self.assertFalse(HarnessHooks.is_test_file("src/latest/service.py"))
        self.assertFalse(HarnessHooks.is_test_file("src/contest/controller.ts"))
        self.assertFalse(HarnessHooks.is_test_file("src/testimonial/card.tsx"))
        # Carpetas y nombres reales de test SÍ son detectados
        self.assertTrue(HarnessHooks.is_test_file("backend/test/hire.spec.ts"))
        self.assertTrue(HarnessHooks.is_test_file("src/mod1/test_service.py"))
        self.assertTrue(HarnessHooks.is_test_file("src/__tests__/app.js"))

        # Test con 'assert' solo en un comentario o 'assert True' tiene 0 aserciones reales
        fake_test = self.temp_dir / "test_fake.py"
        fake_test.write_text("# assert something\ndef test_noop():\n    assert True\n", encoding="utf-8")
        analysis_fake = HarnessHooks.analyze_test_file(fake_test)
        self.assertEqual(analysis_fake["assertion_count"], 0)

        # Test JS con '// assert' en comentario y it.skip debe detectar 0 aserciones y skip
        fake_js_test = self.temp_dir / "fake.spec.ts"
        fake_js_test.write_text("// expect(1).toBe(1)\nit.skip('test', () => {});\n", encoding="utf-8")
        analysis_js = HarnessHooks.analyze_test_file(fake_js_test)
        self.assertEqual(analysis_js["assertion_count"], 0)
        self.assertTrue(analysis_js["has_skipped_or_only"])

        # Punto 14: Función con 'pass' vacío o mock en archivo de producción es rechazada
        bad_prod = self.temp_dir / "prod_service.py"
        bad_prod.write_text(
            "from unittest.mock import MagicMock\ndef process_payment():\n    pass\n",
            encoding="utf-8",
        )
        violations = HarnessHooks.scan_production_file_quality(bad_prod)
        self.assertTrue(any("pass" in v for v in violations))
        self.assertTrue(any("mock" in v.lower() for v in violations))

    # -------------------------------------------------------------------------
    # Puntos 8, 9 y 10: Fase 1 sin códigos, Fase 4 matriz real y Seguridad pre-test
    # -------------------------------------------------------------------------
    def test_08_09_10_phase1_no_codes_phase4_matrix_and_security_scan(self) -> None:
        # Punto 8: Documento en vault sin códigos RF-X-01 debe ser bloqueado/avisado, nunca pasar en silencio
        report_p1 = self.temp_dir / "01_informe.md"
        report_p1.write_text(
            "## 1. A\n## 2. B\n## 3. C\n## 4. D\n## 5. E\n## 6. F\n",
            encoding="utf-8",
        )
        ok_p1, msg_p1, _ = self.hooks.validate_phase1_output(
            report_p1, {"doc.md": "Documento libre sin ningún código formal."}
        )
        self.assertFalse(ok_p1)
        self.assertIn("no contienen ningún código de requerimiento formal", msg_p1)

        # Punto 9: Fase 4 debe rechazar si un requisito dice DESVIACIÓN o si no cita archivo de código y test reales
        prod_f = self.proj_dir / "src" / "mod1" / "real_code.py"
        test_f = self.proj_dir / "src" / "mod1" / "test_real.py"
        prod_f.write_text("def run():\n    return 42\n", encoding="utf-8")
        test_f.write_text("from real_code import run\ndef test_run():\n    assert run() == 42\n", encoding="utf-8")

        report_p4_bad = self.temp_dir / "04_bad.md"
        report_p4_bad.write_text(
            "| ID | Desc | Código | Test | Estado |\n"
            "| --- | --- | --- | --- | --- |\n"
            "| RF-HIRE-001 | Algo | `src/mod1/real_code.py` | `src/mod1/test_real.py` | DESVIACIÓN |\n",
            encoding="utf-8",
        )
        ok_p4_bad, msg_p4_bad = self.hooks.validate_phase4_output(report_p4_bad, ["RF-HIRE-001"])
        self.assertFalse(ok_p4_bad)
        self.assertIn("DESVIACIÓN", msg_p4_bad)

        report_p4_good = self.temp_dir / "04_good.md"
        report_p4_good.write_text(
            "| ID | Desc | Código | Test | Estado |\n"
            "| --- | --- | --- | --- | --- |\n"
            "| RF-HIRE-001 | Algo | `src/mod1/real_code.py` | `src/mod1/test_real.py` | CUMPLIDO |\n",
            encoding="utf-8",
        )
        ok_p4_good, msg_p4_good = self.hooks.validate_phase4_output(report_p4_good, ["RF-HIRE-001"])
        self.assertTrue(ok_p4_good, f"Falló Fase 4 válida: {msg_p4_good}")

        # Punto 10: Escáner de seguridad pre-test bloquea archivos con os.system / child_process.exec
        malicious_test = self.proj_dir / "src" / "mod1" / "test_evil.py"
        malicious_test.write_text("import os\ndef test_evil():\n    os.system('calc.exe')\n    assert 1 == 1\n", encoding="utf-8")
        alerts = self.hooks.scan_security_before_tests([malicious_test], ["src/mod1/"])
        self.assertTrue(len(alerts) > 0)

    # -------------------------------------------------------------------------
    # Verificación Cruzada: Variaciones de Markdown entre distintos modelos de IA
    # (Claude, GPT/Codex, Gemini, Llama) y flujo completo Fase 1 -> 2 -> 3 -> 4
    # -------------------------------------------------------------------------
    def test_15_multi_llm_markdown_variations_and_full_lifecycle(self) -> None:
        vault_docs = {
            "spec.md": "Objetivo: OBJ-APP-001. Regla: BR-APP-001. Requisito: RF-APP-001. Supuesto: SUP-APP-001."
        }

        # Fase 1 generada con variaciones de encabezados típicas de GPT/Claude/Llama (### Sección 1:, ## **2. ...**)
        p1_file = self.temp_dir / "01_informe_relevamiento.md"
        p1_file.write_text(
            "## **1. Resumen**\nCita [OBJ-APP-001]\n"
            "### Sección 2: Arquitectura\n"
            "## 3 - Catálogo\nCita [BR-APP-001] y [RF-APP-001]\n"
            "## 4: Trabajo DELTA\n"
            "## **5. Inconsistencias**\nReferencia huérfana UC-APP-999 y supuesto SUP-APP-001\n"
            "## 6) Cuestionario\n1. ¿Confirmar SUP-APP-001?\n",
            encoding="utf-8",
        )
        ok1, msg1, catalog = self.hooks.validate_phase1_output(p1_file, vault_docs)
        self.assertTrue(ok1, f"Fase 1 rechazó variaciones legítimas de encabezados Markdown: {msg1}")
        self.assertEqual(catalog["approved_ids"], ["BR-APP-001", "OBJ-APP-001", "RF-APP-001"])
        self.assertEqual(catalog["pending_ids"], ["SUP-APP-001"])

        # Fase 2 generada con variaciones de Claude/GPT:
        # - MOD-01 con '**allowed_paths**: [...]' (dos puntos fuera de negrita)
        # - MOD-02 con lista de viñetas debajo de '`allowed_paths`:'
        p2_file = self.temp_dir / "02_plan_modular.md"
        p2_file.write_text(
            "## 1. Decisiones\n## 2. Matriz\n## 3. Módulos\n\n"
            "### **Módulo MOD-01**: Core\n"
            "- **assigned_ids**: [`BR-APP-001`]\n"
            "- **allowed_paths**: [`src/mod1/`]\n\n"
            "### Módulo [MOD-02] - API\n"
            "- `assigned_ids`: [`RF-APP-001`]\n"
            "- `allowed_paths`:\n"
            "  - `src/mod1/`\n\n"
            "## 4. Aprobación\n",
            encoding="utf-8",
        )
        ok2, msg2, modules_plan = self.hooks.validate_phase2_output(p2_file, catalog["approved_ids"])
        self.assertTrue(ok2, f"Fase 2 rechazó formato Markdown de otros modelos de IA: {msg2}")
        self.assertIn("MOD-01", modules_plan)
        self.assertIn("MOD-02", modules_plan)
        self.assertEqual(modules_plan["MOD-02"]["allowed_paths"], ["src/mod1/"])


if __name__ == "__main__":
    unittest.main()
