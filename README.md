# Agent Harness Determinístico de 4 Fases (Zed + ACP + Antigravity CLI)

Infraestructura determinística en Python (`3.12+`) para gobernar el desarrollo de software asistido por IA mediante **Antigravity CLI (`agy`)** desde **Zed**, con controles anti-alucinación, validación de alcance Git (`allowed_paths`), ejecución segura de pruebas y compuertas de aprobación humana.

## Estructura del Proyecto

```text
agent-harness/
├── config.yaml                          # Configuración general (project_root, agy CLI, comandos de test)
├── main.py                              # Punto de entrada CLI del harness
├── core/                                # Motor determinístico (FSM, ingesta/contexto y hooks validadores)
├── adapters/                            # Adaptador seguro para Antigravity CLI / Zed ACP
├── rules/                               # Reglas permanentes inmutables (core_rules.md)
├── skills/                              # Las 4 Skills oficiales por fase + referencia taxonómica ISO 29148
├── inbox/                               # Bandeja de entrada de documentos de requerimientos
├── reviews/                             # Bandeja de interacción y aprobaciones del desarrollador
├── docs/                                # Documentación maestra del arquitectura y estándar de requerimientos
└── .state/                              # Estado persistente (FSM, kb_index.json, vault/, active_context.md, audit.log)
```

## Configuración Inicial

1. Edita `config.yaml` indicando la ruta al repositorio destino en `project_root` y los comandos de pruebas (`unit_test_cmd` y `regression_test_cmd`).
2. Coloca la documentación de requerimientos (`.md`, `.sql`, `.json`, `.yaml`, `.txt`) dentro de `inbox/`.

## Comandos del CLI (`main.py`)

| Comando | Fase | Descripción |
| :--- | :---: | :--- |
| `python main.py status` | Todas | Muestra el estado actual de la máquina de estados (FSM), documentos en `vault/` y progreso por módulo. |
| `python main.py init` | **Fase 1** | Sella documentos de `inbox/` en `.state/vault/`, ejecuta la Skill 01 de relevamiento y valida el informe con Hook F1 (`reviews/01_informe_relevamiento.md`). |
| `python main.py approve-phase1` | **Fase 1** | Registra la aprobación humana tras completar las respuestas en `reviews/01_feedback_desarrollador.md`. |
| `python main.py plan` | **Fase 2** | Ejecuta la Skill 02 para generar `reviews/02_plan_modular.md` y verifica cobertura 1 a 1 y `allowed_paths` con Hook F2. |
| `python main.py approve-plan` | **Fase 2** | Compuerta humana que aprueba el plan modular e inicializa el seguimiento de módulos para la Fase 3. |
| `python main.py build --module <ID>` | **Fase 3** | Verifica snapshot Git limpio, ejecuta la Skill 03 sobre el módulo indicado y valida `allowed_paths`, ausencia de `TODO`/`FIXME` en producción y pruebas unitarias con Hook F3. |
| `python main.py approve-module --module <ID>` | **Fase 3** | Marca un módulo ya testeado (`TESTED`) como aprobado (`APPROVED`) por el desarrollador. |
| `python main.py rollback --module <ID>` | **Fase 3** | Reinicia de forma segura el contador de intentos de un módulo escalado una vez verificado que el árbol Git está limpio. |
| `python main.py verify` | **Fase 4** | Ejecuta la Skill 04 en sesión limpia de auditoría, valida existencia física de archivos citados y corre la suite global de regresión (`reviews/04_reporte_contraste_final.md`). |
