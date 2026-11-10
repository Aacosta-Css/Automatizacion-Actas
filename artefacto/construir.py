"""Arma generador_actas.html (la página que se publica como artefacto) a partir de fuente.html.

Incrusta el logo, la imagen de firma y la hoja de estilos de Quill, porque un artefacto
no puede cargar imágenes ni CSS desde fuera.

Uso (desde esta carpeta):  python construir.py
"""
import base64
from pathlib import Path

AQUI = Path(__file__).resolve().parent
RECURSOS = AQUI / "recursos"

fuente = (AQUI / "fuente.html").read_text(encoding="utf-8")
logo = "data:image/png;base64," + base64.b64encode((RECURSOS / "logo_consensus.png").read_bytes()).decode()
firma = base64.b64encode((RECURSOS / "firma_consensus.jpg").read_bytes()).decode()
quill_css = (RECURSOS / "quill.snow.min.css").read_text(encoding="utf-8")

for marcador in ("__LOGO__", "__FIRMA_B64__", "__QUILL_CSS__"):
    assert marcador in fuente, f"Falta el marcador {marcador} en fuente.html"

pagina = fuente.replace("__LOGO__", logo).replace("__FIRMA_B64__", firma).replace("__QUILL_CSS__", quill_css)
salida = AQUI / "generador_actas.html"
salida.write_text(pagina, encoding="utf-8")
print(f"{salida.name}: {len(pagina) // 1024} KB")
