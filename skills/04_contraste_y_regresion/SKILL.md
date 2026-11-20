---
name: spec-compliance-and-regression-validator
description: Audita en una sesion limpia todo el software desarrollado frente a la toma de requerimientos original y verifica pruebas de regresion y criterios de aceptacion Gherkin antes de la puesta en uso.
phase: 4
---

# SKILL 04: Contraste Final de Requerimientos y Pruebas de Regresión

## 1. Propósito
Actuar como auditor independiente en la etapa final del flujo para contrastar el software completo contra el documento original de requerimientos (más el feedback aprobado) y verificar mediante pruebas de regresión y aceptación (`AT`) que toda la aplicación cumpla las especificaciones antes de su puesta en uso.

## 2. Entradas
- Documentos originales en `.state/vault/` + Decisiones aprobadas en `reviews/01_feedback_desarrollador.md` y `reviews/03_ajustes_modulos.md`.
- Índice completo de IDs requeridos en `.state/kb_index.json`.
- Código fuente completo y suites de tests en `project_root`.

## 3. Control Anti-Alucinación (Evidencia Física Verificable)
- **Prohibido declarar cumplimiento sin ruta física real:** En la Matriz de Contraste, cada fila debe incluir la ruta relativa real del archivo de código y del archivo de test dentro de `project_root` escrita entre backticks (ej. `` `backend/src/hire/hire.service.ts` `` y `` `backend/test/hire.spec.ts` ``).
- El validador en Python comprobará en disco que cada archivo citado exista realmente y que el 100% de los IDs aprobados del proyecto figuren en el reporte.

## 4. Procedimiento Paso a Paso
1. **Verificación de Criterios de Aceptación (`AT` - Gherkin BDD) y Regresión:**
   - Revisa que cada escenario de aceptación (`AT`: `Dado / Cuando / Entonces / Y`) cuente con su prueba automatizada y que toda la suite de regresión del proyecto pase sin fallos.
2. **Auditoría de la Matriz de Trazabilidad Bidireccional:**
   - Contrasta cada `BR`, `RF`, `RNF`, `ST`, `TR`, `API`, `ENT`, `EVT` y `AT` aprobado contra la implementación real en `project_root`.
   - Verifica que se hayan cerrado todas las brechas de la matriz `DELTA` (`AS-IS` -> `TO-BE`).

## 5. Formato de Salida Obligatorio (`reviews/04_reporte_contraste_final.md`)
Escribe el archivo `reviews/04_reporte_contraste_final.md` respetando exactamente estos encabezados `##`:

## 1. Resultado de la Suite de Regresión Global
## 2. Matriz de Contraste Requerimientos vs. Implementación
| ID Artefacto | Descripción | Archivo de Código (`ruta/real`) | Archivo de Test (`ruta/test`) | Estado (`CUMPLIDO` / `DESVIACIÓN`) |
| :--- | :--- | :--- | :--- | :---: |

## 3. Auditoría de Criterios de Aceptación Gherkin (AT) y Brechas DELTA
## 4. Desviaciones Detectadas y Dictamen de Puesta en Uso
