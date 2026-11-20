---
name: spec-discovery-and-requirements-auditor
description: Analiza la documentacion de requerimientos y arquitectura en .state/vault/, releva reglas de negocio, stack, arquitectura y trabajo de ingenieria, y genera el informe de conclusiones y preguntas para el desarrollador.
phase: 1
---

# SKILL 01: Identificación, Relevamiento y Auditoría Documental

## 1. Propósito
Comprender exhaustivamente la documentación técnica provista por el desarrollador, extraer las definiciones normativas del sistema y detectar cualquier contradicción, ambigüedad o decisión pendiente antes de planificar o escribir código.

## 2. Entradas
- Documentos originales sellados en `.state/vault/`.
- Glosario de referencia taxonómica en `skills/references/taxonomia_base.md` (si el documento utiliza prefijos formales como `BR`, `RF`, `RNF`, `ST`, `TR`, `ENT`, `API`, `AT`, `SUP`, `DEC`, `Q`).
- Estructura actual del repositorio en `project_root` (solo lectura, para contrastar el estado `AS-IS` si corresponde).

## 3. Control Anti-Alucinación (Anclaje por Cita Obligatoria)
- En las Secciones 1, 2, 3 y 4 del informe, **cada regla, requisito, estado, transición, entidad o contrato listado DEBE incluir entre corchetes su ID original exacto** (ej. `[BR-HIRE-005]`, `[RF-HIRE-002]`, `[TR-HIRE-007]`) o la sección del documento fuente si el proyecto no usa prefijos formales.
- **PROHIBIDO inventar IDs:** El validador en Python comprobará que todo ID citado en las Secciones 1 a 4 exista literalmente en `.state/vault/`.
- Si detectas que el documento cita un ID que no está definido en el archivo (referencia huérfana, ej. `UC-HIRE-002`) o encuentras elementos pendientes (`SUP`, `DEC`, `Q`), repórtalos **únicamente en las Secciones 5 y 6**.

## 4. Procedimiento Paso a Paso
1. **Reconocimiento de Estructura y Madurez:**
   - Identifica el nivel de madurez del documento (`Borrador Inicial`, `Especificación Intermedia Consolidada` o `Especificación Consolidada`) y sus dependencias.
2. **Relevamiento de los 4 Ejes Obligatorios:**
   - **Eje A — Reglas de Negocio y Permisos:** Extrae todas las reglas de negocio (`BR`), condiciones de bloqueo, matriz de permisos por actor (`CLIENT`, `WORKER`, `SYSTEM`, `ADMIN`) y catálogo de errores (`ERR`).
   - **Eje B — Arquitectura Especificada:** Releva la ubicación en el sistema, el modelo de datos (`ENT`), contratos de interfaz (`API`), eventos de dominio (`EVT`), máquina de estados (`ST` y `TR`) y decisiones arquitectónicas (`ADR`).
   - **Eje C — Stack Tecnológico y Restricciones (`RNF`):** Identifica lenguajes, frameworks, base de datos, seguridad (JWT, RLS, privacidad) y umbrales de rendimiento o transacciones ACID.
   - **Eje D — Trabajo de Ingeniería Requerido (`DELTA`):** Contrasta el estado actual (`AS-IS`) frente al estado objetivo (`TO-BE`) para determinar qué componentes deben crearse o modificarse.
3. **Auditoría de Consistencia y Detección de Dudas:**
   - Recopila todos los puntos marcados como Supuestos (`SUP`), Decisiones Pendientes (`DEC`) o Preguntas Abiertas (`Q`).
   - Cruza las reglas de negocio (`BR`) y privacidad contra los diagramas de secuencia, contratos `API` y tabla de transiciones (`TR`) buscando contradicciones lógicas, estados terminales sin transición de cancelación o validaciones ambiguas.

## 5. Formato de Salida Obligatorio (`reviews/01_informe_relevamiento.md`)
Escribe el archivo `reviews/01_informe_relevamiento.md` respetando exactamente estos encabezados `##`:

## 1. Resumen Ejecutivo y Stack Tecnológico Relevado
## 2. Arquitectura y Modelo de Datos (ENT, API, EVT, ADR)
## 3. Catálogo Consolidado de Reglas de Negocio (BR), Estados (ST/TR) y Requisitos (RF/RNF)
## 4. Trabajo de Ingeniería Identificado (Análisis AS-IS vs TO-BE / DELTA)
## 5. Inconsistencias, Referencias Huérfanas o Riesgos Técnicos Detectados
## 6. Cuestionario para el Desarrollador y Alternativas Propuestas
*(Lista numerada de preguntas claras sobre las dudas halladas, ofreciendo para cada una 2 alternativas técnicas con sus pros y contras para que el desarrollador responda en `reviews/01_feedback_desarrollador.md`).*
