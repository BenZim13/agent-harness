---
module_id: plantilla-base-especificacion
id_prefijo: PLANTILLA
version: 1.0.0
estado: Estandar Normativo Oficial
owner: Equipo de Arquitectura e Ingenieria YacaJobs
fecha: 2026-11-17
estandar: ISO/IEC/IEEE 29148:2018
dependencias: []
modulos_relacionados: [chat-contratacion, chat-cancelacion, login-registro, publicaciones-postulaciones]
---

# GUÍA MAESTRA, TAXONOMÍA Y PLANTILLA BASE DE ESPECIFICACIÓN DE DOMINIO (`PLANTILLA_BASE.md`)

## PROPÓSITO DE ESTE ESTÁNDAR

Este artefacto constituye el **estándar central de documentación de dominio** de YacaJobs basado en la norma internacional **ISO/IEC/IEEE 29148:2018**:

1. **Glosario Universal de Taxonomía:** Define con rigor técnico el significado, propósito, sintaxis y reglas de uso de cada uno de los prefijos y abreviaciones utilizados en los módulos (`BR`, `RF`, `RNF`, `US`, `UC`, `AT`, `ST`, `TR`, `ERR`, `ENT`, `API`, `EVT`, `ADR`, `SUP`, `DEC`, `Q`).
2. **Plantilla Maestra Canónica (Blueprint):** Provee la estructura oficial (Secciones 0 a 5) lista para redactar cualquier especificación de módulo.

---

## PARTE I: GLOSARIO DE TAXONOMÍA Y ABREVIACIONES

Cada requisito, regla, entidad o flujo posee un identificador unívoco e inmutable bajo el formato canónico:

$$\text{[PREFIJO]-[MODULO]-[NÚMERO]}$$

> **Regla de Inmutabilidad de Identificadores:** Un ID asignado jamás se renombra ni se reutiliza. Si un requisito o regla queda deprecado por un cambio de alcance, se marca como `[DEPRECADO]` manteniendo el número, para preservar la trazabilidad histórica en commits, tests, logs y código fuente.

### 1. Catálogo Maestro de Prefijos

| Prefijo | Nombre Completo | Significado y Definición Formal | Ejemplo Real en YacaJobs |
| :--- | :--- | :--- | :--- |
| **`OBJ`** | **Business / Module Objective** *(Objetivo del Módulo)* | Meta fundamental de negocio o valor para el usuario que el módulo busca resolver. Orienta el propósito estratégico antes del detalle técnico. | `OBJ-HIRE-001`: Habilitar contacto directo y formalización de visita técnica diagnóstica. |
| **`BR`** | **Business Rule** *(Regla de Negocio)* | Directriz normativa o política de negocio obligatoria que define qué está permitido, qué está prohibido o cómo debe comportarse el dominio ante determinadas condiciones operativas. Son agnósticas a la UI e inviolables por backend y DB. | `BR-HIRE-005`: Bloqueo estricto de confirmación si no existe domicilio validado. |
| **`RF`** | **Functional Requirement** *(Requisito Funcional)* | Función o capacidad de software específica que el sistema debe ejecutar para dar cumplimiento a una o más Reglas de Negocio (`BR`) y satisfacer las Historias de Usuario (`US`). | `RF-HIRE-002`: Acceso directo al chat al pulsar "Acordar Visita". |
| **`RNF`** | **Non-Functional Requirement** *(Requisito No Funcional)* | Criterio de calidad, rendimiento, seguridad, concurrencia, disponibilidad o usabilidad técnica que califica el modo de operar del sistema. Debe ser medible y verificable. | `RNF-HIRE-001`: Integridad transaccional ACID con 0% de estados parciales corruptos. |
| **`US`** | **User Story** *(Historia de Usuario)* | Declaración narrativa en lenguaje de usuario bajo el esquema: *Como [Rol], quiero [Acción/Capacidad] para [Beneficio/Valor]*. | `US-HIRE-001`: Como cliente, quiero presionar "Acordar Visita" e ingresar directo al chat para coordinar la visita. |
| **`UC`** | **Use Case** *(Caso de Uso)* | Descripción formal y procedimental de la interacción paso a paso entre un Actor externo y el Sistema para alcanzar un objetivo concreto. | `UC-HIRE-002`: Inicialización o Reapertura de Canal de Negociación. |
| **`AT`** | **Acceptance Test / Criteria** *(Criterio de Aceptación BDD)* | Escenario determinístico de verificación redactado en formato estructurado Gherkin (`Dado / Cuando / Entonces / Y`). Constituye el contrato de QA para pruebas automatizadas. | `AT-HIRE-004`: Bloqueo de confirmación por falta de domicilio. |
| **`ST`** | **State** *(Estado de la Máquina de Estados)* | Condición discreta y finita en la que puede encontrarse una entidad transaccional en un instante dado de su ciclo de vida. | `ST-HIRE-001`: `EN_COORDINACION`<br/>`ST-HIRE-002`: `CONTRATACION_CONFIRMADA` |
| **`TR`** | **Transition** *(Transición de Estado)* | Paso formal y atómico de un estado origen a un estado destino, disparado por un evento específico, ejecutado por un actor autorizado y condicionado por guardas lógicas estrictas. | `TR-HIRE-007`: De `PROPUESTA_TRABAJADOR` hacia `CONTRATACION_CONFIRMADA` al presionar Aceptar Turno con Dirección OK. |
| **`ERR`** | **Error Scenario** *(Catálogo de Errores y Casos Borde)* | Identificación de fallas de validación, colisiones de concurrencia, excepciones o condiciones límite, junto a su código HTTP, mensaje de error y estrategia de recuperación. | `ERR-HIRE-002`: Conflicto de concurrencia en negociación horaria (`HTTP 409 Conflict`). |
| **`ENT`** | **Entity** *(Entidad de Modelo de Datos)* | Tabla o conjunto de datos persistente en la base de datos (PostgreSQL/Supabase) que modela el dominio del módulo. | `ENT-HIRE-001`: `acuerdos` (canal perenne)<br/>`ENT-HIRE-002`: `contrataciones` (instancia de visita) |
| **`API`** | **API Endpoint Contract** *(Contrato de Servicio / Interfaz)* | Firma formal de un punto de acceso HTTP/REST, RPC o WebSocket (método, ruta, DTOs de entrada y salida, permisos). Se subdivide en `API-ASIS` y `API-[MOD]`. | `API-ASIS-001`: `POST /api/jobs/agreements/open`<br/>`API-HIRE-001`: `POST /api/hirings` |
| **`EVT`** | **Domain Event** *(Evento de Dominio y Auditoría)* | Suceso significativo acontecido en el dominio que es persistido de forma inmutable (append-only) en el log de eventos o despachado asíncronamente. | `EVT-HIRE-005`: `CONTRATACION_CONFIRMADA` |
| **`ADR`** | **Architecture Decision Record** *(Registro de Decisión Arquitectónica)* | Documento técnico formal que justifica una decisión arquitectónica estructural, evaluando contexto, alternativas descartadas y consecuencias. | `ADR-001`: Orquestador Híbrido Express + NestJS + Vite SPA. |
| **`SUP`** | **Assumption / Supuesto** *(Supuesto Documental)* | Hipótesis operativa asumida en el diseño que requiere validación de negocio o que rige temporalmente para el MVP actual. | `SUP-HIRE-002`: Inactividad en chat por más de 72 horas expira la solicitud. |
| **`DEC`** | **Pending Design Decision** *(Decisión Técnica Pendiente)* | Opción técnica o funcional identificada que se encuentra abierta a debate arquitectónico antes de pasar a congelada. | `DEC-HIRE-001`: Migración de claves primarias serial a UUIDv4. |
| **`Q`** | **Open Question / Issue** *(Pregunta Abierta de Negocio o Alcance)* | Duda o discrepancia funcional pendiente de resolución por los stakeholders del producto. | `Q-HIRE-001`: Disonancia de nomenclatura entre tablas físicas (`acuerdos`) y modelo formal (`chats`). |

### 2. Códigos de Módulos (`[MODULO]`)

| Código | Módulo Funcional | Descripción del Ámbito |
| :--- | :--- | :--- |
| `HIRE` | **Chat y Contratación** | Salas de mensajería, negociación de los 4 pilares, turno y formalización de visita técnica. |
| `CANC` | **Chat y Cancelación** | Política temporal de cancelación, caducidad a 1h, desistimiento y trazabilidad de cancelación. |
| `AUTH` | **Autenticación y Registro** | Login, registro segregado cliente/trabajador, DNI, hashing y emisión de JWT. |
| `PROF` | **Perfiles y Oficios** | Ficha del cliente, perfil técnico del trabajador, matriz de oficios y certificados. |
| `PUBL` | **Publicaciones (Foro / Pedidos)** | Cartelera pública de pedidos de clientes, urgencias, ubicación y fotos del problema. |
| `BID` | **Postulaciones y Presupuestos** | Cotizaciones enviadas por trabajadores a pedidos publicados en la cartelera. |
| `VIS` | **Visitas In-Situ** | Asistencia física, check-in de llegada del trabajador, verificación de diagnóstico. |
| `PAY` | **Pagos y Cobranzas** | Transacciones, billetera virtual, retención de garantía (Escrow) y liquidación. |
| `REV` | **Calificaciones y Reseñas** | Reputación bilateral, valoraciones con estrellas, comentarios públicos y métricas. |
| `NOTIF`| **Notificaciones** | Eventos push a dispositivos móviles, banners en UI y sockets en tiempo real. |
| `ADMIN`| **Backoffice y Soporte** | Panel de control, validación manual de DNI/certificados y resolución de disputas. |

### 3. Códigos de Actores y Niveles de Madurez

* **Actores:** `CLIENT` (`C` - Cliente Consumidor), `WORKER` (`W` - Trabajador Profesional), `SYSTEM` (`SYS` - Sistema Automatizado), `ADMIN` (`A` - Administrador / Soporte).
* **Niveles de Madurez:** `Borrador Inicial`, `Especificación Intermedia Consolidada`, `Especificación Consolidada`, `Deprecada`.

---

## PARTE II: PLANTILLA MAESTRA EN BLANCO (CANONICAL BLUEPRINT)

```markdown
---
module_id: [nombre-del-modulo-kebab-case]
id_prefijo: [PREFIJO_3_A_4_LETRAS]
version: 1.0.0
estado: Borrador Inicial | Especificacion Intermedia Consolidada | Especificacion Consolidada
owner: Equipo de Arquitectura e Ingenieria YacaJobs
fecha: YYYY-MM-DD
estandar: ISO/IEC/IEEE 29148:2018
dependencias: [Modulo-A, Modulo-B]
modulos_relacionados: [Modulo-C, Modulo-D]
---

# MODULE SPECIFICATION — [Nombre Completo del Módulo]

## 0. Metadata y Alcance
| Campo | Valor |
| :--- | :--- |
| **module_id** | `[nombre-del-modulo]` |
| **id_prefijo** | `[PREFIJO]` |
| **version** | `1.0.0` |
| **estado** | Especificación Consolidada |
| **dependencias** | `[Modulo-Dependencia-1]`, `[Modulo-Dependencia-2]` |

## Índice de lectura para IA
- **Propósito:** [Resumen de 1-2 líneas del objetivo central del módulo].
- **Actores:** Cliente (`CLIENT`), Trabajador (`WORKER`), Sistema (`SYSTEM`), Administrador (`ADMIN`).
- **Reglas Críticas:** [`BR-MOD-001`, `BR-MOD-002`].
- **Requisitos Críticos:** [`RF-MOD-001`, `RF-MOD-002`].
- **Estados Principales:** `[ESTADO_INICIAL]`, `[ESTADO_PROCESO]`, `[ESTADO_FINAL]`.
- **AS-IS / TO-BE / Pendientes:** [Resumen de estado actual, objetivo y `SUP`/`DEC`/`Q`].

## 1. Cómo funciona el módulo
1. **[Paso 1 - Disparador inicial]:** [Descripción]. `[OBJ-MOD-001]` `[US-MOD-001]` `[UC-MOD-001]`
2. **[Paso 2 - Validación y procesamiento]:** [Descripción]. `[BR-MOD-001]` `[RF-MOD-001]`
3. **[Paso 3 - Confirmación o Cierre]:** [Descripción]. `[BR-MOD-002]` `[ST-MOD-002]` `[AT-MOD-001]`

## 2. Reglas, Requisitos, Historias y Permisos
### 2.1 Reglas de Negocio (`BR-[PREFIJO]`)
#### `BR-[PREFIJO]-001`: [Título de la Regla 1]
- **Regla:** El sistema DEBE / NO DEBE [descripción imperativa].
- **Condición:** [Disparador o contexto].
- **Resultado:** [Consecuencia medible].
- **Relacionado:** `US-[PREFIJO]-001`, `RF-[PREFIJO]-001`, `AT-[PREFIJO]-001`.

### 2.2 Requisitos Funcionales (`RF-[PREFIJO]`)
| ID | Requisito Funcional | Prioridad | BR Asociada | US / UC Relacionadas |
| :--- | :--- | :---: | :--- | :--- |
| `RF-[PREFIJO]-001` | [Descripción] | Crítica | `BR-[PREFIJO]-001` | `US-[PREFIJO]-001` |

### 2.3 Requisitos No Funcionales (`RNF-[PREFIJO]`)
| ID | Atributo de Calidad | Métrica y Umbral Requerido | Método de Verificación |
| :--- | :--- | :--- | :--- |
| `RNF-[PREFIJO]-001` | Integridad Transaccional | 0% de estados parciales corruptos (ACID) | Pruebas de concurrencia en backend |

### 2.4 Historias de Usuario (`US-[PREFIJO]`)
- **`US-[PREFIJO]-001`:** Como [rol], quiero [acción], para [beneficio].

### 2.5 Criterios de Aceptación (Gherkin BDD - `AT-[PREFIJO]`)
Escenario: AT-[PREFIJO]-001 - [Nombre del escenario]
  Dado que [condición inicial]
  Cuando [el actor ejecuta la acción]
  Entonces [el sistema responde con cambio verificable]
  Y [persiste el evento en auditoría].

### 2.6 Matriz Compacta de Permisos
| Acción / Operación | Cliente (`CLIENT`) | Trabajador (`WORKER`) | Sistema (`SYSTEM`) | Administrador (`ADMIN`) |
| :--- | :---: | :---: | :---: | :---: |
| [Acción A] | **SÍ** | NO | NO | **SÍ** |

## 3. Estados, Flujos y Secuencias
### 3.1 Catálogo de Estados (`ST-[PREFIJO]-###`)
- `ST-[PREFIJO]-001` `[ESTADO_INICIAL]`: [Descripción].

### 3.2 Tabla Formal de Transiciones (`TR-[PREFIJO]-###`)
| ID | Estado Origen | Evento Disparador | Actor | Guardas / Condiciones | Estado Destino |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `TR-[PREFIJO]-001` | `[*]` | Iniciar | `CLIENT` | Autenticación válida | `[ESTADO_INICIAL]` |
*Cualquier transición no declarada en esta tabla está ESTRICTAMENTE PROHIBIDA.*

### 3.5 Catálogo de Errores y Casos Borde (`ERR-[PREFIJO]`)
| ID | Condición de Error | Código HTTP | Mensaje al Usuario | Estrategia de Recuperación |
| :--- | :--- | :---: | :--- | :--- |
| `ERR-[PREFIJO]-001` | Formato no válido | `422` | *"Campo obligatorio..."* | Resaltar campo en UI |

## 4. Contexto Técnico
### 4.1 Arquitectura e Integración
### 4.2 Modelo de Datos y Entidades (`ENT-[PREFIJO]`)
### 4.3 Contratos de API (`API-[PREFIJO]`)
### 4.4 Eventos de Dominio (`EVT-[PREFIJO]`)
### 4.5 Seguridad, Permisos y Privacidad (JWT, RLS, Ley 25.326, Idempotencia)

## 5. Estado Actual, Evolución y Trazabilidad
### 5.1 AS-IS (Estado Actual Implementado)
### 5.2 TO-BE (Estado Proyectado)
### 5.3 DELTA (Matriz de Brechas)
### 5.4 Supuestos (`SUP`), Decisiones Pendientes (`DEC`) y Preguntas Abiertas (`Q`)
### 5.5 Matriz de Trazabilidad Bidireccional Cruzada
| Funcionalidad | US | UC | BR | RF / RNF | ST / TR | API / ENT / EVT | AT (Gherkin) |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :--- |
| [Flujo 1] | `US-001` | `UC-001` | `BR-001` | `RF-001` | `ST-001` / `TR-001` | `API-001`, `ENT-001` | `AT-001` |
```
