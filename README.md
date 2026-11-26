# Agent Harness Determinístico Multi-IA de 4 Fases

Infraestructura determinística en Python (`3.10+`) para gobernar el desarrollo de software asistido por IA en equipo, compatible con **Windows, Linux y macOS** y con **cualquier IA o IDE** (**Antigravity CLI**, **Claude Code**, **Codex**, **Gemini CLI**, **Aider**, **Cursor**, **Windsurf**, **Zed ACP**, **GitHub Copilot** o modo manual).

## Características Principales

- **Configuración en Capas Sin Pisar al Equipo:**
  - `config.yaml`: Configuración base compartida en Git.
  - `<project_root>/.harness/config.yaml`: Configuración opcional propia del repositorio destino.
  - `config.local.yaml` *(en `.gitignore`)*: Ruta local de tu máquina (`project_root`), tu IA preferida (`adapter.provider`) y tu identidad.
- **Estado Compartido, Atómico y con Autoría:**
  - `workflow_state.json`, `kb_index.json`, `audit.log` y `reviews/` se versionan en Git (en el harness o dentro de `<project_root>/.harness/` si configuras `state.location: "project"`).
  - Escritura atómica (`temp` + `fsync` + `os.replace` + respaldo `.bak`) y bloqueo de concurrencia (`.state/harness.lock`).
  - Cada aprobación, transición y ejecución registra el nombre y correo del desarrollador (`git config user.name` / `user.email`).
- **Snapshots Delta y Rollback Real:**
  - Permite trabajar en paralelo: ignora archivos que ya estaban modificados fuera del módulo antes de `build` (ej. `package-lock.json`) comparando hashes SHA-256 contra la foto inicial del módulo.
  - Detecta si la IA hizo `git commit` por su cuenta, si tocó archivos ignorados por Git (`.env*`, `.git/hooks/*`) o si intentó borrar/desactivar tests (`.skip`, reducción de aserciones).
  - `python main.py rollback --module <ID>` respalda en `.state/rollback_backups/` y revierte automáticamente los archivos del módulo fallido.
- **Compatibilidad Multiplataforma Real (Windows / Linux / macOS):**
  - Resuelve automáticamente ejecutables `.cmd`, `.bat` y `.exe` en Windows (`npm.cmd`, `npx.cmd`, `pytest.exe`) tanto en `node_modules/.bin` y `.venv` como en el `PATH` global, preservando `shell=False` y las rutas con `\`.

## Configuración Rápida para cada Desarrollador

1. Copia la plantilla personal:
   ```bash
   cp config.local.yaml.example config.local.yaml
   ```
2. Edita `config.local.yaml` con tu ruta a `project_root` y la IA que uses (`antigravity`, `claude`, `codex`, `gemini`, `aider` o `manual`).
3. Coloca la documentación de requerimientos en `inbox/`.

## Comandos del CLI (`main.py`)

| Comando | Fase | Descripción |
| :--- | :---: | :--- |
| `python main.py status` | Todas | Muestra estado de la FSM, desarrollador activo, IA configurada y progreso por módulo. |
| `python main.py init [--manual] [--validate]` | **Fase 1** | Sella documentos de `inbox/` en `.state/vault/`, ejecuta Skill 01 y valida con Hook F1 (`reviews/01_informe_relevamiento.md`). |
| `python main.py approve-phase1` | **Fase 1** | Registra la aprobación humana con autoría tras responder en `reviews/01_feedback_desarrollador.md`. |
| `python main.py plan [--manual] [--validate]` | **Fase 2** | Ejecuta Skill 02 para generar `reviews/02_plan_modular.md` y verifica cobertura 1 a 1 y `allowed_paths` con Hook F2. |
| `python main.py approve-plan` | **Fase 2** | Compuerta humana con autoría que aprueba el plan modular e inicializa los módulos para Fase 3. |
| `python main.py build --module <ID> [--manual] [--validate]` | **Fase 3** | Toma snapshot delta, ejecuta Skill 03 y valida `allowed_paths`, calidad de producción (`core_rules.md`), integridad de tests, seguridad estática y pruebas unitarias con Hook F3. |
| `python main.py approve-module --module <ID>` | **Fase 3** | Marca un módulo testeado (`TESTED`) como aprobado (`APPROVED`) registrando quién lo aprobó. |
| `python main.py rollback --module <ID>` | **Fase 3** | Respalda en `.state/rollback_backups/`, revierte automáticamente los archivos modificados/creados por el módulo y reinicia el estado a `PENDING`. |
| `python main.py verify [--manual] [--validate]` | **Fase 4** | Ejecuta Skill 04, valida fila por fila la Matriz de Contraste (estado `CUMPLIDO`, código + test con aserciones reales) y corre la suite de regresión. |
| `python main.py check --phase <1..4> [--module <ID>]` | **Integración** | Ejecuta de forma desacoplada únicamente el Hook determinístico de la fase indicada (ideal para integrar con `agent-workflow` o CI/CD). |
