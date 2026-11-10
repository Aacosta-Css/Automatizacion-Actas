"""Genera el par .html + .eml de un acta, igual que main.py.

- El .html lleva la imagen de firma incrustada en base64 (se ve en el navegador).
- El .eml usa la imagen como recurso CID, lleva X-Unsent: 1 (Outlook lo abre como borrador)
  y el consultor como remitente.

Uso:
  python crear_eml.py --html acta.html --to "ana@cliente.com" --subject "Asunto" \
      --from-nombre "Nombre Consultor" --from-email "consultor@consensussa.com" \
      --carpeta uploads/Consultoria_Octubre_2026 --nombre-html Acta_X.html --nombre-eml Correo_X.eml
"""
import argparse
import base64
import time
from email.message import EmailMessage
from email.utils import formataddr
from pathlib import Path

ETIQUETA_FIRMA = 'CONSENSUS S.A.S.</p>'


def guardar(ruta: Path, texto: str, reintentos: int = 4) -> None:
    # Tolera micro-bloqueos de sincronización de OneDrive
    for intento in range(reintentos):
        try:
            ruta.write_text(texto, encoding="utf-8")
            return
        except PermissionError:
            if intento == reintentos - 1:
                raise
            time.sleep(0.35 * (intento + 1))


def nombres_libres(carpeta: Path, nombre_html: str, nombre_eml: str):
    # Igual que la app: si ya existen, agrega _2, _3... para no sobrescribir actas anteriores
    base_html, base_eml = Path(nombre_html).stem, Path(nombre_eml).stem
    contador = 2
    while (carpeta / nombre_html).exists() or (carpeta / nombre_eml).exists():
        nombre_html, nombre_eml = f"{base_html}_{contador}.html", f"{base_eml}_{contador}.eml"
        contador += 1
    return nombre_html, nombre_eml


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--html", required=True, help="HTML del acta con <img src=\"cid:imagen_firma\">")
    p.add_argument("--to", required=True)
    p.add_argument("--subject", required=True)
    p.add_argument("--from-nombre", required=True)
    p.add_argument("--from-email", required=True)
    p.add_argument("--carpeta", required=True, help="Carpeta de salida, p. ej. uploads/Consultoria_Octubre_2026")
    p.add_argument("--nombre-html", required=True)
    p.add_argument("--nombre-eml", required=True)
    p.add_argument("--firma", default=str(Path(__file__).resolve().parent.parent / "recursos" / "firma_consensus.jpg"),
                   help="Imagen de firma corporativa (por defecto, la incluida en el skill)")
    a = p.parse_args()

    contenido_html = Path(a.html).read_text(encoding="utf-8")
    ruta_firma = Path(a.firma)
    img_bytes = ruta_firma.read_bytes() if ruta_firma.exists() else None

    carpeta = Path(a.carpeta)
    carpeta.mkdir(parents=True, exist_ok=True)
    nombre_html, nombre_eml = nombres_libres(carpeta, a.nombre_html, a.nombre_eml)

    # HTML para navegador: imagen de firma en base64
    html_archivo = contenido_html
    if img_bytes:
        uri = "data:image/jpeg;base64," + base64.b64encode(img_bytes).decode()
        if "cid:imagen_firma" in html_archivo:
            html_archivo = html_archivo.replace("cid:imagen_firma", uri)
        elif ETIQUETA_FIRMA in html_archivo:
            html_archivo = html_archivo.replace(
                ETIQUETA_FIRMA,
                f'{ETIQUETA_FIRMA}<br><img src="{uri}" alt="Consensus S.A.S." width="589" style="display: block; width: 100%; max-width: 589px; height: auto; margin-top: 10px;" />', 1)
    guardar(carpeta / nombre_html, html_archivo)

    # EML editable para Outlook
    msg = EmailMessage()
    msg["To"] = a.to
    msg["Subject"] = a.subject
    msg["From"] = formataddr((a.from_nombre, a.from_email))
    msg["X-Unsent"] = "1"
    html_eml = contenido_html
    if "cid:imagen_firma" not in html_eml and ETIQUETA_FIRMA in html_eml:
        html_eml = html_eml.replace(
            ETIQUETA_FIRMA,
            f'{ETIQUETA_FIRMA}<br><img src="cid:imagen_firma" alt="Consensus S.A.S." width="589" style="display: block; width: 100%; max-width: 589px; height: auto; margin-top: 10px;" />', 1)
    msg.set_content("Este es un correo en formato HTML. Por favor, utiliza un cliente de correo compatible.")
    msg.add_alternative(html_eml, subtype="html")
    if img_bytes:
        msg.get_payload()[1].add_related(img_bytes, "image", "jpeg", cid="<imagen_firma>")
    guardar(carpeta / nombre_eml, msg.as_string())

    print(f"HTML: {carpeta / nombre_html}")
    print(f"EML:  {carpeta / nombre_eml}")


if __name__ == "__main__":
    main()
