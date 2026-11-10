# Skills de Claude Code para actas Consensus

Copia visible de los skills que usa Claude Code. La copia que Claude Code carga en este
proyecto está en `.claude/skills/` (carpeta oculta en Windows); si cambias un skill aquí,
cópialo también allí.

| Skill | Qué hace |
|---|---|
| `acta-consultoria-posventa` | Acta de consultoría posventa (.html + .eml) desde notas, transcripción o documentos. |
| `acta-implementacion-excel` | Actas mensuales de implementación por cliente desde un Excel de actividades. |

## Instalar en otro equipo

Copia las dos carpetas a `C:\Users\<tu usuario>\.claude\skills\` para usarlas en Claude Code
desde cualquier carpeta. Requieren Python con `pandas` y `openpyxl` (para leer el Excel).
