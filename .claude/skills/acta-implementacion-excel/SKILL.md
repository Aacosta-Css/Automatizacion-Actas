---
name: acta-implementacion-excel
description: Genera las actas mensuales de implementación SAP Business One de Consensus S.A.S. a partir de un Excel de registro de actividades/horas — un .html y un correo .eml editable para Outlook por cada cliente, más un resumen. Úsalo cuando el usuario pida la relación de actividades del mes, actas de implementación o correos por cliente desde un Excel, aunque no diga "skill".
---

# Actas de Implementación desde Excel (relación mensual por cliente)

> `<carpeta del skill>` es el directorio de este skill (Claude Code lo muestra como *Base directory for this skill* al cargarlo). Los archivos se generan en `uploads/` dentro de la carpeta de trabajo actual.

Replica el flujo "Acta de Implementación (Excel)" de la app (`main.py`, `prompt_implementacion`),
ejecutado directamente por Claude: tú procesas los datos y redactas, el script genera los archivos.
No pidas aprobación entre clientes: procesa el lote completo y entrega el resumen al final.

## 1. Datos necesarios

- **Archivo Excel** (.xlsx / .xls). Nunca lo modifiques.
- **Mes y año** a procesar (obligatorios; si faltan, pregúntalos).
- **Firma del consultor** (nombre, cargo, teléfono fijo, celular, correo): del usuario, o de `users.db` (solo en la carpeta del proyecto de la app) por su correo:
  `python -c "import sqlite3; print(sqlite3.connect('users.db').execute('SELECT nombre_completo, cargo, telefono_fijo, celular, email FROM users WHERE email=?', ('CORREO',)).fetchone())"`.
  Si no están, pregúntalos. Por defecto: cargo "Consultor SAP Business One", teléfono "+57 601 743 16 24".

## 2. Lee el Excel

```
python "<carpeta del skill>/scripts/leer_excel.py" "<archivo.xlsx>" <Mes> <Año>
```

Devuelve cada hoja como TSV, ya filtrada por la columna de fecha de inicio cuando es identificable.

## 3. Procesa (reglas del prompt original)

1. **Filtrado estricto**: solo registros cuya **fecha de inicio** sea del mes y año pedidos. No uses fecha de creación o registro como sustituto. Descarta otros períodos y actividades marcadas como proyectadas o futuras.
2. **Mapeo de columnas**: Cuenta = Cliente · Descripción = Actividad · Fecha de inicio · Fecha de fin · Horas · Persona = Recurso · Correo Responsable = destinatario · Tipo de actividad, Acción y responsable de proyecto si existen.
3. **Limpieza**: excluye vacíos, duplicados exactos y horas ≤ 0. Valida cliente, actividad, fechas, horas y recurso. **Conserva las horas del Excel**: no las compares con inicio/fin (puede haber almuerzo o pausas). Una Acción genérica como "Realizar" no prueba que la actividad esté cerrada.
4. **Ortografía**: corrige errores evidentes, tildes y espacios sin cambiar el sentido técnico; primera letra en mayúscula; conserva siglas y nombres propios.
5. **Observaciones**: nada de control interno ni sobre horas vs. intervalos. Solo información clara y relevante para el cliente; si no hay, deja vacío el valor (sin quitar la línea).
6. **Por cliente**: cantidad de actividades, total de horas (suma de Horas), recursos participantes y hasta 3 actividades principales sin duplicar. Filas en orden cronológico por fecha y hora de inicio.

## 4. Arma un HTML por cliente

Copia [plantilla.html](plantilla.html) y reemplaza solo los `{{...}}`. Reglas:
- Columnas de la tabla en este orden: **Actividad, Fecha, Hora inicio, Hora fin, Horas** (sin columna Recurso; los recursos van solo en el resumen). Fechas `DD/MM/AAAA`, horas en 24 h.
- No pongas destinatario ni asunto dentro del cuerpo.
- No cambies estructura, estilos, colores ni orden de secciones (saludo → actividades → detalles con observaciones → confirmación → firma). Conserva `<img src="cid:imagen_firma" …>`.
- Escapa `&`, `<`, `>` de los textos del Excel. Guarda cada HTML en el scratchpad.

## 5. Genera .html + .eml por cliente

- **To**: correos válidos de Correo Responsable del cliente, separados por coma y sin duplicar. Si no hay: `[Correo no encontrado]`. No inventes direcciones.
- **Asunto**: `Relación de actividades realizadas - {Cliente} - {MES EN MAYÚSCULAS} {Año}`
- **Carpeta**: `uploads/{Mes}_{Año}/` (p. ej. `uploads/Octubre_2026/`).
- **Nombres**: `{cliente_seguro}_Reporte_{Mes}_{Año}.html` y `{cliente_seguro}_Correo_{Mes}_{Año}.eml` (solo letras, números, `-` y `_`).

```
python "<carpeta del skill>/scripts/crear_eml.py" --html <html_del_scratchpad> --to "<To>" --subject "<Asunto>" --from-nombre "<Nombre consultor>" --from-email "<correo consultor>" --carpeta "uploads/<Mes>_<Año>" --nombre-html "<archivo>.html" --nombre-eml "<archivo>.eml"
```

El script incrusta la firma (`recursos/firma_consensus.jpg` del skill), marca `X-Unsent: 1` (borrador en Outlook)
y no sobrescribe archivos existentes.

## 6. Resumen final

```
# 📊 Resumen de Procesamiento: {Mes} de {Año}
**Archivos generados en la carpeta:** `uploads/{Mes}_{Año}/`

| Cliente / Cuenta | Horas Totales | Destinatario | Estado |
| :--- | :--- | :--- | :--- |
| … | … | … o [Correo no encontrado] | ✅ Generado |
```

Señala aparte los clientes sin correo responsable y los registros descartados (con el motivo),
para que el consultor los revise antes de enviar.
