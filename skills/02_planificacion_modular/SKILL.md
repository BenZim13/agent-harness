---
name: modular-implementation-planner
description: Procesa el relevamiento de la Fase 1 junto con el feedback del desarrollador para construir el plan de implementacion dividido por modulos, sus rutas permitidas y su secuencia exacta de desarrollo.
phase: 2
---

# SKILL 02: Planificación de Implementación por Módulos

## 1. Propósito
Transformar los requerimientos ya clarificados y aprobados por el desarrollador en un plan de ingeniería ejecutable, dividido en módulos acotados con un orden de construcción determinístico y límites de archivos estrictos.

## 2. Entradas
- `reviews/01_informe_relevamiento.md` (Relevamiento inicial).
- `reviews/01_feedback_desarrollador.md` (Respuestas y decisiones del desarrollador — **máxima autoridad normativa**).
- Índice de artefactos en `.state/kb_index.json`.

## 3. Control Anti-Alucinación (Cobertura 1 a 1 y `allowed_paths` Obligatorio)
- **Ningún requisito aprobado puede quedar huérfano:** Todo ID normativo (`BR`, `RF`, `RNF`, `TR`, `API`, `ENT`) relevado en la Fase 1 debe estar asignado explícitamente dentro de al menos un bloque de módulo.
- **Ningún módulo puede existir sin justificación ni alcance:** Cada módulo debe declararse con el encabezado `### Módulo <ID_MODULO>: <Nombre>` e incluir obligatoriamente los campos `assigned_ids:` y `allowed_paths:` en formato de lista para que `hooks.py` pueda auditarlos.

## 4. Procedimiento Paso a Paso
1. **Consolidación de Decisiones:**
   - Integra las respuestas de `01_feedback_desarrollador.md` para cerrar las preguntas abiertas (`Q`), confirmar o descartar supuestos (`SUP`) y fijar las decisiones técnicas (`DEC`).
2. **Particionamiento Modular y Secuencia Topológica:**
   - Divide el trabajo en módulos cohesivos ordenados según sus dependencias técnicas (ej. `MOD-01` Persistencia/Migraciones -> `MOD-02` Dominio y Máquina de Estados -> `MOD-03` Endpoints API -> `MOD-04` Componentes UI).
3. **Delimitación de Rutas Permitidas (`allowed_paths`):**
   - Para cada módulo, define las rutas relativas dentro de `project_root` (archivos o carpetas específicas, incluyendo la ruta de sus tests unitarios) que el agente tendrá permiso de crear o modificar durante la Fase 3.

## 5. Formato de Salida Obligatorio (`reviews/02_plan_modular.md`)
Escribe el archivo `reviews/02_plan_modular.md` respetando exactamente esta estructura:

## 1. Decisiones Consolidadas del Feedback
## 2. Matriz de Cobertura 1 a 1 (Requisito/Regla -> Módulo Asignado)
## 3. Secuencia y Fichas Técnicas por Módulo

### Módulo MOD-01: [Nombre del Módulo 1]
- **Orden de Ejecución:** 1
- **Objetivo y Responsabilidad:** [Descripción técnica]
- **Dependencias Previas:** Ninguna
- **assigned_ids:** [`ENT-XXX`, `BR-XXX`, `RF-XXX`]
- **allowed_paths:** [`backend/src/modules/ejemplo/`, `backend/test/ejemplo/`]
- **Plan de Pruebas Unitarias:** [Qué casos y reglas se probarán unitariamente al cerrar este módulo]

*(Repetir el bloque `### Módulo MOD-0X:` para cada módulo de la secuencia)*

## 4. Solicitud de Aprobación
*(Indicar al desarrollador que revise el plan y ejecute `python main.py approve-plan` para habilitar la Fase 3).*
