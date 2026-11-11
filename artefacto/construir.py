"""Arma la página que se publica como artefacto a partir de fuente.html.

Incrusta el logo y la imagen de firma, porque un artefacto no puede cargar imágenes desde fuera.

Uso (desde esta carpeta):
  python construir.py            -> generador_actas.html          (artefacto definitivo)
  python construir.py pruebas    -> generador_actas_pruebas.html  (artefacto de pruebas)
"""
import base64
import sys
from pathlib import Path

AQUI = Path(__file__).resolve().parent
RECURSOS = AQUI / "recursos"

fuente = (AQUI / "fuente.html").read_text(encoding="utf-8")
logo = "data:image/png;base64," + base64.b64encode((RECURSOS / "logo_consensus.png").read_bytes()).decode()
firma = base64.b64encode((RECURSOS / "firma_consensus.jpg").read_bytes()).decode()

for marcador in ("__LOGO__", "__FIRMA_B64__"):
    assert marcador in fuente, f"Falta el marcador {marcador} en fuente.html"

pagina = fuente.replace("__LOGO__", logo).replace("__FIRMA_B64__", firma)
if len(sys.argv) > 1 and sys.argv[1] == "pruebas":
    # Título distinto para no confundirlo con el definitivo en la galería
    pagina = pagina.replace("<title>Generador de Actas Consensus</title>", "<title>Generador de Actas Consensus (pruebas)</title>", 1)
    salida = AQUI / "generador_actas_pruebas.html"
else:
    salida = AQUI / "generador_actas.html"
salida.write_text(pagina, encoding="utf-8")
print(f"{salida.name}: {len(pagina) // 1024} KB")
