# UI de gestión del corpus

Documento de planeación. Interfaz de administración para **gestionar los documentos ingestados**, no para consultar el RAG.

---

## 1. Distinción clave

**No es una UI de consulta** — Claude Code y clientes MCP ya consultan el RAG. Es un **panel de administración**: ver, buscar, curar, re-ingestar, aprobar. Gestión de datos, no chat.

La UI es un **cliente** del sistema, no parte del servidor MCP.

---

## 2. Alcance

### 2.1 CRUD de documentos
- Listar, filtrar y buscar artículos ingestados
- Ver detalle: fuente, procedencia, capa destino, género, fecha, hash
- Re-ingestar (curación + upsert)
- Eliminar

### 2.2 Estado de curación y errores
- Estado: crudo / curado-borrador / curado-validado / ingestado / fallido
- Bandeja de errores de ingesta
- Progreso del batch (gate 6a y 6b de Fase 1)
- Documentos dudosos para revisión por excepción

### 2.3 Bandeja de aprobación + métricas
- Aprobación de fuentes nuevas (crecimiento controlado)
- Aprobación de entradas al catálogo
- Métricas: consultas por día, tasa VÁLIDO/INVÁLIDO/NO_VERIFICABLE, cobertura del catálogo

---

## 3. Stack sugerido

- UI web ligera (Vue 3 o React) sobre la API del servidor de retrieval
- Sin framework pesado
- La API la expone el servidor MCP o un servicio aparte (conecta con decisión monolito vs. dos servidores)

---

## 4. Qué NO es

- No una interfaz de chat / consulta del RAG
- No parte del servidor MCP
- No un panel con features infinitas
- No Fase 1: se construye después del corpus y la ingesta batch
