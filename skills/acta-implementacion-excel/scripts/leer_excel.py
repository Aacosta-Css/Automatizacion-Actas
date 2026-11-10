"""Convierte el Excel de actividades en tablas TSV por hoja, filtrando por mes/año de inicio.

Misma lógica que preparar_excel_para_prompt() de main.py: no modifica el archivo original,
solo quita filas/columnas totalmente vacías y, si hay UNA columna de fecha de inicio
identificable, deja las filas de ese mes y año.

Uso:  python leer_excel.py <archivo.xlsx> <Mes> <Año>      p. ej.  python leer_excel.py horas.xlsx Octubre 2026
"""
import io
import sys
import unicodedata

import pandas as pd

MESES = {
    "enero": 1, "ene": 1, "febrero": 2, "feb": 2, "marzo": 3, "mar": 3,
    "abril": 4, "abr": 4, "mayo": 5, "may": 5, "junio": 6, "jun": 6,
    "julio": 7, "jul": 7, "agosto": 8, "ago": 8, "septiembre": 9,
    "setiembre": 9, "sep": 9, "set": 9, "octubre": 10, "oct": 10,
    "noviembre": 11, "nov": 11, "diciembre": 12, "dic": 12,
}


def normalizar(texto) -> str:
    texto = unicodedata.normalize("NFKD", str(texto or "").casefold())
    return "".join(c for c in texto if not unicodedata.combining(c)).strip()


def preparar_excel(contenido_bytes: bytes, mes=None, anio=None) -> str:
    hojas = pd.read_excel(io.BytesIO(contenido_bytes), sheet_name=None)
    mes_n = normalizar(mes)
    numero_mes = int(mes_n) if mes_n.isdigit() and 1 <= int(mes_n) <= 12 else MESES.get(mes_n)
    try:
        anio_numero = int(anio)
    except (TypeError, ValueError):
        anio_numero = None

    tablas = []
    for nombre_hoja, df in hojas.items():
        df = df.dropna(axis=0, how="all").dropna(axis=1, how="all")
        if df.empty:
            continue
        if numero_mes and anio_numero:
            candidatos = []
            for columna in df.columns:
                nombre = normalizar(columna)
                es_inicio = ("inicio" in nombre or "start" in nombre) and not any(
                    t in nombre for t in ("fin", "final", "end", "creacion", "registro"))
                if es_inicio and any(t in nombre for t in ("fecha", "date", "inicio", "start")):
                    candidatos.append(columna)
            if len(candidatos) == 1:
                fechas = pd.to_datetime(df[candidatos[0]], errors="coerce", dayfirst=True)
                mascara = (fechas.dt.month == numero_mes) & (fechas.dt.year == anio_numero)
                if mascara.any():
                    df = df.loc[mascara.fillna(False)]
        if df.empty:
            continue
        tsv = df.fillna("").to_csv(sep="\t", index=False, lineterminator="\n")
        tablas.append(f"=== HOJA: {nombre_hoja} ===\n{tsv.rstrip()}")
    return "\n\n".join(tablas)


if __name__ == "__main__":
    if len(sys.argv) != 4:
        sys.exit(__doc__)
    sys.stdout.reconfigure(encoding="utf-8")
    with open(sys.argv[1], "rb") as f:
        print(preparar_excel(f.read(), sys.argv[2], sys.argv[3]))
