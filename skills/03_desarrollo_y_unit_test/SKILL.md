---
name: requirement-driven-module-builder
description: Skill general de desarrollo que implementa un modulo especifico de la secuencia respetando estrictamente las reglas de negocio, arquitectura, stack, allowed_paths y maquina de estados, y construye sus pruebas unitarias.
phase: 3
---

# SKILL 03: Desarrollo Guiado por Requerimientos y Pruebas Unitarias

## 1. Propósito
Desarrollar el código fuente del módulo activo en la secuencia aprobada y validar inmediatamente su funcionamiento mediante pruebas unitarias reales, garantizando fidelidad absoluta a los requerimientos y permitiendo que el desarrollador pruebe el módulo de forma incremental.

## 2. Entradas
- Contexto limpio del módulo activo en `.state/active_context.md` (ficha del módulo en `02_plan_modular.md`, sus `assigned_ids`, su lista `allowed_paths`, definiciones normativas y `rules/core_rules.md`).
- `reviews/03_ajustes_modulos.md` (si el desarrollador probó el módulo previamente y solicitó ajustes).

## 3. Control Anti-Alucinación (Micro-Check Pre-Código y Guardia de Alcance)
1. **Micro-Check Obligatorio antes de editar archivos:** Al iniciar tu respuesta, redacta un bloque breve de 5 líneas confirmando:
   - Módulo activo y lista exacta de `allowed_paths` que tienes permitido tocar.
   - Reglas de negocio (`BR`) y transiciones permitidas (`TR`) que vas a codificar.
   - Transiciones de estado o acciones que debes bloquear explícitamente (`NO DEBE`).
2. **Prohibición de Salir de `allowed_paths`:** El hook de Python inspeccionará `git status --porcelain -uall` al terminar. Si modificas o creas cualquier archivo fuera de `allowed_paths`, la fase será rechazada. Si necesitas obligatoriamente tocar un archivo externo, detente y emite `[ESCALAR_AL_DESARROLLADOR]`.

## 4. Procedimiento Paso a Paso
1. **Revisión de Ajustes del Desarrollador:**
   - Si `reviews/03_ajustes_modulos.md` contiene correcciones para este módulo, aplícalas con prioridad.
2. **Implementación Normativa en Código de Producción:**
   - Implementa las validaciones de reglas de negocio (`BR`) y permisos por actor en el servidor/dominio.
   - Programa la máquina de estados (`ST` / `TR`) bloqueando cualquier transición no declarada en la tabla formal.
   - Devuelve los códigos y estructuras del catálogo de errores (`ERR`) y respeta las garantías transaccionales (`RNF`).
   - Agrega comentarios de trazabilidad en las funciones clave (ej. `// Implementa BR-HIRE-005, TR-HIRE-007`).
   - **Cero Atajos en Producción:** No dejes `TODO`, `FIXME` ni datos falsos hardcodeados en el código productivo.
3. **Construcción y Ejecución de Pruebas Unitarias del Módulo:**
   - Dentro de la ruta de tests autorizada en `allowed_paths`, crea o actualiza las pruebas unitarias del módulo.
   - En los archivos de test puedes utilizar mocks legítimos del framework de pruebas (`jest.mock`, `vi.mock`, `unittest.mock`), asegurándote de incluir aserciones reales (`expect(...)`, `assert ...`) para cada `BR`, `RF`, `TR` y `ERR` asignado al módulo.
   - Verifica que los tests unitarios del módulo compilen y pasen en verde.

## 5. Entregable de Salida del Módulo
- Código de producción y archivos de pruebas unitarias escritos exclusivamente dentro de `allowed_paths`.
- Resumen técnico indicando qué archivos se modificaron, qué tests unitarios cubren cada ID asignado y cómo puede el desarrollador probar el módulo antes de ejecutar `python main.py approve-module --module <ID_MODULO>`.
