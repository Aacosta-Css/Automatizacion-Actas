---
name: acta-consultoria-posventa
description: Genera el acta de consultoría posventa SAP Business One de Consensus S.A.S. (archivo .html y correo .eml editable para Outlook) a partir de notas, transcripciones, documentos o audio de una sesión con un cliente. Úsalo cuando el usuario pida un acta, confirmación o correo de consultoría posventa / soporte para un cliente, aunque no diga "skill".
---

# Acta de Consultoría Posventa (SAP Business One)

> `<carpeta del skill>` es el directorio de este skill (Claude Code lo muestra como *Base directory for this skill* al cargarlo). Los archivos se generan en `uploads/` dentro de la carpeta de trabajo actual.

Replica el flujo "Consultoría (Posventa)" de la app (`main.py`, `prompt_consultoria`), pero
ejecutado directamente por Claude: tú redactas el acta y el script genera los archivos.

## 1. Reúne los datos

| Dato | Si falta |
|---|---|
| Cliente | Pregúntalo (obligatorio). |
| Responsable del cliente (nombre o correo) | Usa "Responsable". |
| Fecha de ejecución (DD/MM/AAAA) | Usa la fecha de hoy y avísalo. |
| Horas de consultoría | Pregúntalo; si no lo sabe, "0". |
| Insumos: notas, archivos (.txt, .docx, .pdf, .xlsx, imágenes) o transcripción del audio | Debe haber al menos uno; si no hay, pídelo. |

**Firma del consultor** (nombre, cargo, teléfono fijo, celular, correo):
1. Si el usuario los da, úsalos.
2. Si no, búscalos por su correo en `users.db` (base de la app; solo existe si trabajas en la carpeta del proyecto):
   `python -c "import sqlite3; print(sqlite3.connect('users.db').execute('SELECT nombre_completo, cargo, telefono_fijo, celular, email FROM users WHERE email=?', ('CORREO',)).fetchone())"`
3. Si tampoco están, pregúntalos. Valores por defecto: cargo "Consultor SAP Business One", teléfono "+57 601 743 16 24".
Nunca firmes con datos de otro consultor.

## 2. Extrae las actividades (reglas del prompt original)

1. **Extracción total**: identifica CADA tarea, consulta, revisión, desarrollo, configuración o capacitación de los insumos. Si hay varios puntos o viñetas, cada uno es una actividad separada.
2. Redacta cada actividad en tono profesional, técnico y formal para SAP Business One (p. ej. "Revisión y parametrización de…", "Análisis y depuración de error en…", "Capacitación al equipo de…", "Validación de informes y consultas SQL en…").
3. **Prohibido** dejar la lista vacía o usar textos genéricos o de plantilla ("[Actividad…]", "Escribe aquí…"). Todo debe ser concreto y real; no inventes actividades que no estén en los insumos.
4. **Observaciones**: si los insumos mencionan una dificultad, acuerdo o pendiente relevante para el cliente, redáctala; si no, escribe exactamente `Ninguna`.

## 3. Arma el HTML

Copia [plantilla.html](plantilla.html) y reemplaza solo los `{{...}}`:
- `{{ACTIVIDADES…}}` → un `<li style="margin-bottom: 8px;">…</li>` por actividad.
- Datos del servicio y de la firma. Escapa `&`, `<`, `>` en textos que vengan del usuario.

No cambies estructura, estilos, colores ni el orden de secciones. Conserva
`<img src="cid:imagen_firma" …>` al final de la firma. Guarda el HTML en el scratchpad.

## 4. Genera .html + .eml

- **Destinatario (To)**: correo del responsable si aparece en los insumos; si no, el nombre del responsable.
- **Asunto**: `Confirmación de Consultoría SAP Business One - {Cliente} - {Fecha}`
- **Carpeta**: `uploads/Consultoria_{Mes}_{Año}/` (mes en español con mayúscula inicial, p. ej. `Consultoria_Octubre_2026`; si la fecha no es válida, `Consultoria_Sin_fecha`).
- **Nombres**: `Acta_Consultoria_{cliente_seguro}_{fecha_segura}.html` y `Correo_Consultoria_{cliente_seguro}_{fecha_segura}.eml` (solo letras, números, `-` y `_`; espacios → `_`; fecha sin `/`).

Ejecuta desde la raíz del proyecto:

```
python "<carpeta del skill>/scripts/crear_eml.py" --html <html_del_scratchpad> --to "<To>" --subject "<Asunto>" --from-nombre "<Nombre consultor>" --from-email "<correo consultor>" --carpeta "uploads/Consultoria_<Mes>_<Año>" --nombre-html "<archivo>.html" --nombre-eml "<archivo>.eml"
```

El script incrusta la imagen de firma (`recursos/firma_consensus.jpg` del skill) en el .html, la adjunta como CID
en el .eml, marca `X-Unsent: 1` y no sobrescribe actas anteriores (agrega `_2`, `_3`…).

## 5. Responde al usuario

Muestra la tabla resumen y las rutas generadas:

| Cliente | Fecha | Horas | Archivo HTML | Archivo EML (Outlook) | Estado |
|---|---|---|---|---|---|
| … | … | … hrs | `…html` | `…eml` | ✅ Generado |

Recuérdale que el `.eml` se abre en Outlook como borrador para revisarlo antes de enviarlo.
