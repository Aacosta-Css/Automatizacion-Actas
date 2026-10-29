import os
import io
import uuid
import re
import base64
import unicodedata
import sqlite3
import hashlib
import hmac
import secrets
import json
import html
from email.utils import formataddr
from datetime import datetime
from pathlib import Path
from typing import List, Optional

# Novedad: Librerías estándar de Python para crear correos EML funcionales
from email.message import EmailMessage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.image import MIMEImage

from fastapi import FastAPI, Request, File, UploadFile, Form
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse, Response
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
import pandas as pd
from google import genai
from google.genai import types
import tempfile
import zipfile
import time

# --- 1. CONFIGURACIÓN INICIAL ---
import sys

# Cuando se ejecuta como .exe compilado con PyInstaller, los archivos de datos
# (templates, uploads) se copian junto al ejecutable. BASE_DIR apunta a esa carpeta.
if getattr(sys, 'frozen', False):
    # Directorio donde vive el .exe
    BASE_DIR = Path(sys.executable).resolve().parent
else:
    BASE_DIR = Path(__file__).resolve().parent

# Cargar .env desde la misma carpeta que el exe (o el script en desarrollo)
load_dotenv(dotenv_path=BASE_DIR / ".env")

TEMPLATES_DIR = BASE_DIR / "templates"


def guardar_texto_con_reintentos(ruta: Path, texto: str, encoding: str = "utf-8", reintentos: int = 4, pausa: float = 0.35) -> None:
    """Escribe texto en un archivo tolerando micro-bloqueos de sincronización de OneDrive."""
    for intento in range(reintentos):
        try:
            ruta.write_text(texto, encoding=encoding)
            return
        except PermissionError:
            if intento == reintentos - 1:
                raise
            time.sleep(pausa * (intento + 1))


def guardar_bytes_con_reintentos(ruta: Path, datos: bytes, reintentos: int = 4, pausa: float = 0.35) -> None:
    """Escribe bytes en un archivo tolerando micro-bloqueos de sincronización de OneDrive."""
    for intento in range(reintentos):
        try:
            ruta.write_bytes(datos)
            return
        except PermissionError:
            if intento == reintentos - 1:
                raise
            time.sleep(pausa * (intento + 1))


def obtener_directorio_uploads() -> Path:
    candidato = BASE_DIR / "uploads"
    try:
        candidato.mkdir(parents=True, exist_ok=True)
        # Test de permisos con archivo aleatorio no bloqueante
        prueba = candidato / f".permiso_test_{uuid.uuid4().hex[:6]}"
        prueba.write_text("ok", encoding="utf-8")
        try:
            prueba.unlink()
        except OSError:
            pass  # Si OneDrive retiene el handle temporalmente, la prueba de escritura ya fue exitosa.
        return candidato
    except (OSError, PermissionError):
        # En entornos serverless de solo lectura (como Vercel/AWS Lambda)
        dir_temp = Path(tempfile.gettempdir()) / "consensus_uploads"
        dir_temp.mkdir(parents=True, exist_ok=True)
        return dir_temp

UPLOADS_DIR = obtener_directorio_uploads()
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

app = FastAPI(title="Generador de Actas Consensus")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

client = None

def obtener_cliente_gemini():
    global client
    if client is not None:
        return client
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if api_key:
        try:
            client = genai.Client(api_key=api_key)
            print("✅ Cliente Gemini inicializado con éxito.")
            return client
        except Exception as err:
            print(f"⚠️ Error al inicializar cliente Gemini: {err}")
    else:
        print("⚠️ Advertencia: GEMINI_API_KEY no encontrada.")
    return None

# Inicialización temprana si la variable ya existe
obtener_cliente_gemini()

# --- AUTENTICACIÓN LOCAL DE CONSULTORES (SQLite + hashlib, sin dependencias externas) ---
CARGO_DEFAULT = "Consultor SAP Business One"
TELEFONO_FIJO_DEFAULT = "+57 601 743 16 24"
DOMINIO_CORPORATIVO = "@consensussa.com"
COOKIE_SESION = "user_session"
DURACION_SESION_SEG = 30 * 24 * 3600
ITERACIONES_HASH = 200_000

DB_PATH = Path("/tmp/users.db") if os.environ.get("VERCEL") else BASE_DIR / "users.db"


def conectar_db() -> sqlite3.Connection:
    conexion = sqlite3.connect(str(DB_PATH), timeout=10)
    conexion.row_factory = sqlite3.Row
    return conexion


def inicializar_db() -> None:
    try:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        with conectar_db() as conexion:
            conexion.execute(f"""
                CREATE TABLE IF NOT EXISTS users (
                    email TEXT PRIMARY KEY,
                    password_hash TEXT NOT NULL,
                    salt TEXT NOT NULL,
                    nombre_completo TEXT NOT NULL,
                    cargo TEXT DEFAULT '{CARGO_DEFAULT}',
                    celular TEXT,
                    telefono_fijo TEXT DEFAULT '{TELEFONO_FIJO_DEFAULT}'
                )
            """)
        print(f"✅ Base de datos de usuarios lista en {DB_PATH}")
    except Exception as err:
        print(f"⚠️ No se pudo inicializar la base de datos de usuarios ({DB_PATH}): {err}")


inicializar_db()


def obtener_secreto_sesion() -> bytes:
    """Clave para firmar la cookie. En Vercel debe definirse SESSION_SECRET para que la sesión
    sobreviva a reinicios de /tmp y funcione entre instancias."""
    secreto = os.getenv("SESSION_SECRET", "").strip()
    if secreto:
        return secreto.encode()
    ruta_secreto = DB_PATH.parent / ".session_secret"
    try:
        if ruta_secreto.exists():
            return ruta_secreto.read_text(encoding="utf-8").strip().encode()
        nuevo = secrets.token_hex(32)
        ruta_secreto.write_text(nuevo, encoding="utf-8")
        return nuevo.encode()
    except OSError:
        print("⚠️ SESSION_SECRET no definido y no se pudo persistir; las sesiones expirarán al reiniciar.")
        return secrets.token_hex(32).encode()


SECRETO_SESION = obtener_secreto_sesion()


def hash_password(password: str, salt: str) -> str:
    # PBKDF2-HMAC-SHA256 (librería estándar): mucho más resistente a fuerza bruta que un SHA-256 simple.
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), ITERACIONES_HASH).hex()


def verify_password(password: str, salt: str, hashed: str) -> bool:
    return hmac.compare_digest(hash_password(password, salt), hashed)


def limpiar_campo(valor: Optional[str], max_len: int = 120) -> str:
    # Elimina saltos de línea (evita inyección de encabezados en el .eml) y limita la longitud.
    return re.sub(r"[\r\n\t]+", " ", str(valor or "")).strip()[:max_len]


def perfil_publico(fila) -> dict:
    return {
        "email": fila["email"],
        "nombre_completo": fila["nombre_completo"],
        "cargo": fila["cargo"] or CARGO_DEFAULT,
        "celular": fila["celular"] or "",
        "telefono_fijo": fila["telefono_fijo"] or TELEFONO_FIJO_DEFAULT,
    }


def crear_token_sesion(perfil: dict) -> str:
    """Token firmado con HMAC que transporta el perfil de firma. Así la firma sigue disponible
    aunque Vercel limpie /tmp/users.db entre ejecuciones."""
    datos = dict(perfil, exp=int(time.time()) + DURACION_SESION_SEG)
    cuerpo = base64.urlsafe_b64encode(json.dumps(datos, ensure_ascii=False).encode()).decode().rstrip("=")
    firma = hmac.new(SECRETO_SESION, cuerpo.encode(), hashlib.sha256).hexdigest()
    return f"{cuerpo}.{firma}"


def leer_token_sesion(token: Optional[str]) -> Optional[dict]:
    if not token or "." not in token:
        return None
    cuerpo, firma = token.rsplit(".", 1)
    esperada = hmac.new(SECRETO_SESION, cuerpo.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(firma, esperada):
        return None
    try:
        datos = json.loads(base64.urlsafe_b64decode(cuerpo + "=" * (-len(cuerpo) % 4)))
    except Exception:
        return None
    if not isinstance(datos, dict) or datos.get("exp", 0) < time.time() or not datos.get("email"):
        return None
    datos.pop("exp", None)
    return datos


def usuario_actual(request: Request) -> Optional[dict]:
    return leer_token_sesion(request.cookies.get(COOKIE_SESION))


def establecer_sesion(respuesta: Response, perfil: dict) -> None:
    respuesta.set_cookie(
        COOKIE_SESION,
        crear_token_sesion(perfil),
        max_age=DURACION_SESION_SEG,
        httponly=True,
        samesite="lax",
        secure=bool(os.environ.get("VERCEL")),
    )


def respuesta_con_sesion(perfil: dict, status_code: int = 200) -> JSONResponse:
    respuesta = JSONResponse(status_code=status_code, content={"success": True, "usuario": perfil})
    establecer_sesion(respuesta, perfil)
    return respuesta


@app.post("/api/auth/register")
async def auth_register(
    email: str = Form(...),
    password: str = Form(...),
    nombre_completo: str = Form(...),
    cargo: str = Form(default=CARGO_DEFAULT),
    celular: str = Form(default=""),
    telefono_fijo: str = Form(default=TELEFONO_FIJO_DEFAULT),
):
    email = limpiar_campo(email, 120).lower()
    nombre_completo = limpiar_campo(nombre_completo)
    if not re.fullmatch(r"[a-z0-9._%+-]+" + re.escape(DOMINIO_CORPORATIVO), email):
        return JSONResponse(status_code=400, content={"success": False, "error": f"Usa tu correo corporativo ({DOMINIO_CORPORATIVO})."})
    if len(password) < 8:
        return JSONResponse(status_code=400, content={"success": False, "error": "La contraseña debe tener al menos 8 caracteres."})
    if not nombre_completo:
        return JSONResponse(status_code=400, content={"success": False, "error": "El nombre completo es obligatorio."})

    salt = secrets.token_hex(16)
    perfil = {
        "email": email,
        "nombre_completo": nombre_completo,
        "cargo": limpiar_campo(cargo) or CARGO_DEFAULT,
        "celular": limpiar_campo(celular, 40),
        "telefono_fijo": limpiar_campo(telefono_fijo, 40) or TELEFONO_FIJO_DEFAULT,
    }
    try:
        with conectar_db() as conexion:
            conexion.execute(
                "INSERT INTO users (email, password_hash, salt, nombre_completo, cargo, celular, telefono_fijo) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (email, hash_password(password, salt), salt, perfil["nombre_completo"], perfil["cargo"], perfil["celular"], perfil["telefono_fijo"]),
            )
    except sqlite3.IntegrityError:
        return JSONResponse(status_code=409, content={"success": False, "error": "Ya existe una cuenta con ese correo. Inicia sesión."})
    return respuesta_con_sesion(perfil, status_code=201)


@app.post("/api/auth/login")
async def auth_login(email: str = Form(...), password: str = Form(...)):
    email = limpiar_campo(email, 120).lower()
    with conectar_db() as conexion:
        fila = conexion.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    if not fila or not verify_password(password, fila["salt"], fila["password_hash"]):
        return JSONResponse(status_code=401, content={"success": False, "error": "Correo o contraseña incorrectos."})
    return respuesta_con_sesion(perfil_publico(fila))


@app.get("/api/auth/me")
async def auth_me(request: Request):
    usuario = usuario_actual(request)
    if not usuario:
        return JSONResponse(status_code=401, content={"success": False, "error": "No hay sesión activa."})
    return {"success": True, "usuario": usuario}


@app.put("/api/auth/profile")
async def auth_profile(
    request: Request,
    nombre_completo: str = Form(default=""),
    cargo: str = Form(default=""),
    celular: str = Form(default=""),
    telefono_fijo: str = Form(default=""),
):
    usuario = usuario_actual(request)
    if not usuario:
        return JSONResponse(status_code=401, content={"success": False, "error": "No hay sesión activa."})
    perfil = {
        "email": usuario["email"],
        "nombre_completo": limpiar_campo(nombre_completo) or usuario.get("nombre_completo", ""),
        "cargo": limpiar_campo(cargo) or CARGO_DEFAULT,
        "celular": limpiar_campo(celular, 40),
        "telefono_fijo": limpiar_campo(telefono_fijo, 40) or TELEFONO_FIJO_DEFAULT,
    }
    with conectar_db() as conexion:
        conexion.execute(
            "UPDATE users SET nombre_completo = ?, cargo = ?, celular = ?, telefono_fijo = ? WHERE email = ?",
            (perfil["nombre_completo"], perfil["cargo"], perfil["celular"], perfil["telefono_fijo"], perfil["email"]),
        )
    return respuesta_con_sesion(perfil)


@app.post("/api/auth/logout")
async def auth_logout():
    respuesta = JSONResponse(content={"success": True})
    respuesta.delete_cookie(COOKIE_SESION, httponly=True, samesite="lax", secure=bool(os.environ.get("VERCEL")))
    return respuesta

RUTA_IMAGEN_FIRMA = TEMPLATES_DIR / "Imagen1.jpg"
_IMAGEN_FIRMA_BYTES: Optional[bytes] = None

def obtener_bytes_imagen_firma() -> Optional[bytes]:
    global _IMAGEN_FIRMA_BYTES
    if _IMAGEN_FIRMA_BYTES is None and RUTA_IMAGEN_FIRMA.exists():
        try:
            _IMAGEN_FIRMA_BYTES = RUTA_IMAGEN_FIRMA.read_bytes()
        except Exception as e:
            print(f"Advertencia al leer imagen de firma: {e}")
    return _IMAGEN_FIRMA_BYTES

AUDIO_UPLOADS_DIR = UPLOADS_DIR / "Audios_Consultoria"
AUDIO_EXTENSIONS = {".webm", ".wav", ".mp3", ".m4a", ".ogg", ".mp4"}
try:
    AUDIO_UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
except Exception:
    pass


def nombre_archivo_seguro(texto, valor_default):
    nombre = "".join(c if c.isalnum() or c in "-_" else "_" for c in str(texto or ""))
    nombre = re.sub(r"_+", "_", nombre).strip("_-")[:80]
    return nombre or valor_default


def fecha_audio(fecha_texto):
    fecha = pd.to_datetime(fecha_texto, dayfirst=True, errors="coerce")
    return fecha.to_pydatetime() if pd.notna(fecha) else datetime.now()


@app.post("/guardar-audio/")
async def guardar_audio(
    audio: UploadFile = File(...),
    nombre_cliente: str = Form(default="Cliente"),
    fecha_ejecucion: str = Form(default=""),
):
    if not audio.filename:
        return JSONResponse(status_code=400, content={"success": False, "error": "No se recibió un archivo de audio."})

    sufijo = Path(audio.filename).suffix.lower()
    if sufijo not in AUDIO_EXTENSIONS:
        return JSONResponse(status_code=400, content={"success": False, "error": "Formato de audio no compatible."})

    contenido = await audio.read()
    if not contenido:
        return JSONResponse(status_code=400, content={"success": False, "error": "El archivo de audio está vacío."})

    fecha = fecha_audio(fecha_ejecucion)
    meses = ["Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio", "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"]
    carpeta_mes = AUDIO_UPLOADS_DIR / f"{meses[fecha.month - 1]}_{fecha.year}"
    carpeta_mes.mkdir(parents=True, exist_ok=True)

    cliente_seguro = nombre_archivo_seguro(nombre_cliente, "Cliente")
    fecha_nombre = fecha.strftime("%Y-%m-%d")
    hora_nombre = datetime.now().astimezone().strftime("%H-%M-%S-%f")
    nombre_audio = f"{cliente_seguro}_{fecha_nombre}_{hora_nombre}{sufijo}"
    ruta_audio = carpeta_mes / nombre_audio
    ruta_audio.write_bytes(contenido)

    ruta_relativa = ruta_audio.relative_to(UPLOADS_DIR).as_posix()
    return JSONResponse(content={"success": True, "ruta": f"uploads/{ruta_relativa}", "nombre": nombre_audio})


def preparar_excel_para_prompt(contenido_bytes, mes=None, anio=None):
    """Reduce el texto del Excel sin modificar el archivo original ni descartar datos ambiguos."""
    hojas = pd.read_excel(io.BytesIO(contenido_bytes), sheet_name=None)
    tablas = []

    meses = {
        "enero": 1, "ene": 1, "febrero": 2, "feb": 2, "marzo": 3, "mar": 3,
        "abril": 4, "abr": 4, "mayo": 5, "may": 5, "junio": 6, "jun": 6,
        "julio": 7, "jul": 7, "agosto": 8, "ago": 8, "septiembre": 9,
        "setiembre": 9, "sep": 9, "set": 9, "octubre": 10, "oct": 10,
        "noviembre": 11, "nov": 11, "diciembre": 12, "dic": 12,
    }
    mes_normalizado = unicodedata.normalize("NFKD", str(mes or "").casefold())
    mes_normalizado = "".join(c for c in mes_normalizado if not unicodedata.combining(c)).strip()
    numero_mes = int(mes_normalizado) if mes_normalizado.isdigit() and 1 <= int(mes_normalizado) <= 12 else meses.get(mes_normalizado)
    try:
        anio_numero = int(anio)
    except (TypeError, ValueError):
        anio_numero = None

    for nombre_hoja, df in hojas.items():
        # Retira solo filas y columnas completamente vacías, sin alterar celdas con datos.
        df = df.dropna(axis=0, how="all").dropna(axis=1, how="all")
        if df.empty:
            continue

        if numero_mes and anio_numero:
            candidatos_fecha = []
            for columna in df.columns:
                nombre = unicodedata.normalize("NFKD", str(columna).casefold())
                nombre = "".join(c for c in nombre if not unicodedata.combining(c))
                es_inicio = ("inicio" in nombre or "start" in nombre) and not any(
                    termino in nombre for termino in ("fin", "final", "end", "creacion", "registro")
                )
                if es_inicio and any(termino in nombre for termino in ("fecha", "date", "inicio", "start")):
                    candidatos_fecha.append(columna)

            # Filtra únicamente con una columna de inicio identificable y fechas interpretables.
            if len(candidatos_fecha) == 1:
                valores_fecha = df[candidatos_fecha[0]]
                fechas = pd.to_datetime(valores_fecha, errors="coerce", dayfirst=True)
                # Filtra las filas que correspondan exactamente al mes y año solicitado sin descartar si hay celdas no parseables
                mascara_periodo = (fechas.dt.month == numero_mes) & (fechas.dt.year == anio_numero)
                if mascara_periodo.any():
                    df = df.loc[mascara_periodo.fillna(False)]

        if df.empty:
            continue
        tabla_tsv = df.fillna("").to_csv(sep="\t", index=False, lineterminator="\n")
        tablas.append(f"=== HOJA: {nombre_hoja} ===\n{tabla_tsv.rstrip()}")

    return "\n\n".join(tablas)

def llamar_gemini_con_fallback(client_gemini, contents, modelos_prioridad=None):
    if modelos_prioridad is None:
        modelo_config = os.getenv("GEMINI_MODEL", "").strip()
        modelos_estables = ["gemini-3.6-flash", "gemini-3.5-flash", "gemini-3.8-flash"]
        if modelo_config:
            modelos_prioridad = [modelo_config] + [m for m in modelos_estables if m != modelo_config]
        else:
            modelos_prioridad = modelos_estables
    ultimo_error = None
    for model_name in modelos_prioridad:
        try:
            return client_gemini.models.generate_content(model=model_name, contents=contents)
        except Exception as e:
            err_str = str(e)
            ultimo_error = e
            if any(term in err_str for term in ["503", "429", "UNAVAILABLE", "RESOURCE_EXHAUSTED", "404", "high demand"]):
                print(f"⚠️ Modelo '{model_name}' no disponible temporalmente ({err_str[:80]}...). Probando fallback...")
                continue
            raise e
    raise ultimo_error
    
# --- 2. RUTAS Y DESCARGAS ---
@app.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
    try:
        return templates.TemplateResponse("index.html", {"request": request})
    except Exception:
        ruta_raiz_html = TEMPLATES_DIR / "index.html"
        if ruta_raiz_html.exists():
            return HTMLResponse(content=ruta_raiz_html.read_text(encoding="utf-8"))
        return HTMLResponse(content=f"Error: No se encuentra {ruta_raiz_html}", status_code=500)


@app.get("/descargar/{carpeta}/{nombre_archivo}")
async def descargar_archivo(carpeta: str, nombre_archivo: str):
    safe_carpeta = Path(carpeta).name
    safe_archivo = Path(nombre_archivo).name
    ruta_archivo = UPLOADS_DIR / safe_carpeta / safe_archivo
    if not ruta_archivo.exists() or not ruta_archivo.is_file():
        return JSONResponse(status_code=404, content={"success": False, "error": "Archivo no encontrado."})

    media_type = "application/octet-stream"
    if safe_archivo.endswith(".html"):
        media_type = "text/html; charset=utf-8"
    elif safe_archivo.endswith(".eml"):
        media_type = "message/rfc822"
    elif safe_archivo.endswith(".webm"):
        media_type = "audio/webm"

    return FileResponse(
        path=str(ruta_archivo),
        filename=safe_archivo,
        media_type=media_type
    )


@app.get("/ver/{carpeta}/{nombre_archivo}", response_class=HTMLResponse)
async def ver_archivo_html(carpeta: str, nombre_archivo: str):
    safe_carpeta = Path(carpeta).name
    safe_archivo = Path(nombre_archivo).name
    ruta_archivo = UPLOADS_DIR / safe_carpeta / safe_archivo
    if not ruta_archivo.exists() or not ruta_archivo.is_file():
        return HTMLResponse(content="<h3>Archivo no encontrado</h3>", status_code=404)
    return HTMLResponse(content=ruta_archivo.read_text(encoding="utf-8"))


@app.get("/descargar-zip/{carpeta}")
async def descargar_zip_periodo(carpeta: str):
    safe_carpeta = Path(carpeta).name
    dir_carpeta = UPLOADS_DIR / safe_carpeta
    if not dir_carpeta.exists() or not dir_carpeta.is_dir():
        return JSONResponse(status_code=404, content={"success": False, "error": "Carpeta no encontrada."})

    zip_buffer = io.BytesIO()
    archivos_agregados = 0
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
        for root, _, files in os.walk(dir_carpeta):
            for file in files:
                file_path = Path(root) / file
                arcname = file_path.relative_to(dir_carpeta)
                zip_file.write(file_path, arcname=str(arcname))
                archivos_agregados += 1

    if archivos_agregados == 0:
        return JSONResponse(status_code=404, content={"success": False, "error": "No hay archivos para comprimir."})

    zip_buffer.seek(0)
    return Response(
        content=zip_buffer.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="Actas_{safe_carpeta}.zip"'}
    )


# =======================================================================
# 3. ENDPOINT DE PROCESAMIENTO -> AJUSTES APLICADOS EN ESTA SECCIÓN
# =======================================================================
@app.post("/procesar-acta/")
async def procesar_acta(
    request: Request,
    archivos: List[UploadFile] = File(default=None),
    audio: Optional[UploadFile] = None,
    tipo_proceso: str = Form(default="consultoria"),
    nombre_cliente: str = Form(default="[Nombre del Cliente]"),
    responsable_cliente: str = Form(default="[Responsable del Cliente]"),
    informacion_extra: str = Form(default=""),
    fecha_ejecucion: str = Form(default="[Fecha de Ejecución]"),
    horas_ejecutadas: str = Form(default="[Horas Ejecutadas]"),
    mes_ejecucion: Optional[str] = Form(default=""),
    anio_ejecucion: Optional[str] = Form(default="")
):
    usuario = usuario_actual(request)
    if not usuario:
        return JSONResponse(status_code=401, content={"success": False, "error": "Inicia sesión para que el acta salga firmada con tus datos."})

    # Datos de firma del consultor autenticado (escapados porque se insertan en HTML)
    consultor_nombre = html.escape(usuario.get("nombre_completo", ""))
    consultor_cargo = html.escape(usuario.get("cargo") or CARGO_DEFAULT)
    consultor_celular = html.escape(usuario.get("celular", ""))
    consultor_tel = html.escape(usuario.get("telefono_fijo") or TELEFONO_FIJO_DEFAULT)
    consultor_email = usuario["email"]
    remitente_eml = formataddr((usuario.get("nombre_completo", ""), consultor_email))

    cliente_gemini = obtener_cliente_gemini()
    if not cliente_gemini:
        return JSONResponse(status_code=500, content={"success": False, "error": "Cliente Gemini no configurado. Asegúrate de configurar GEMINI_API_KEY en las variables de entorno o archivo .env."})

    try:
        if tipo_proceso == "implementacion":
            if not mes_ejecucion or not anio_ejecucion:
                return JSONResponse(status_code=400, content={"success": False, "error": "El Mes y el Año son requeridos."})
            
            mes_limpio, anio_limpio = mes_ejecucion.strip(), anio_ejecucion.strip()
            periodo_solicitado = f"{mes_limpio} de {anio_limpio}"
            
            if not archivos or not any(f.filename for f in archivos if f):
                return JSONResponse(status_code=400, content={"success": False, "error": "Debes adjuntar al menos un archivo Excel (.xlsx, .xls) para continuar."})

            archivo_excel = next((f for f in archivos if f and f.filename and f.filename.lower().endswith((".xlsx", ".xls"))), None)
            if not archivo_excel:
                return JSONResponse(status_code=400, content={"success": False, "error": "Adjunta un archivo Excel válido (.xlsx, .xls)."})

            contenido_bytes = await archivo_excel.read()
            texto_tablas_excel = f"Archivo: {archivo_excel.filename}\n\n" + preparar_excel_para_prompt(
                contenido_bytes, mes=mes_limpio, anio=anio_limpio
            )
            
           
            # ** CAMBIO 1: INSTRUCCIONES MÁS ESTRICTAS EN EL PROMPT SOBRE LA ESTRUCTURA **
            prompt_implementacion = f"""
          
                ACTÚA COMO UN SISTEMA DE AUTOMATIZACIÓN DE REPORTES PARA CONSENSUS S.A.S.

                Procesa los datos proporcionados, agrúpalos por cliente y genera automáticamente el contenido para los archivos HTML y EML de cada cliente, además del resumen final. No pidas aprobación, confirmación ni autorización. No pauses el proceso para esperar respuesta.

                DATOS DE CONTEXTO:
                - Mes/Año a procesar: {mes_limpio}/{anio_limpio}
                - Período: {periodo_solicitado}
                - Directorio de exportación: uploads/
                - Datos del Excel: {texto_tablas_excel}


                INSTRUCCIONES DE PROCESAMIENTO:

                1. FILTRADO ESTRICTO
                Usa únicamente registros cuya fecha de inicio corresponda al mes '{mes_limpio}' del año '{anio_limpio}'. No uses una fecha de creación o registro como sustituto de la fecha de inicio. Descarta los registros de otros períodos y las actividades identificadas explícitamente como proyectadas o futuras.

                2. MAPEO DE COLUMNAS
                Identifica automáticamente los nombres equivalentes a:
                - Cuenta = Cliente
                - Descripción = Actividad
                - Fecha de inicio
                - Fecha de fin
                - Horas
                - Persona = Recurso
                - Correo Responsable = destinatario del correo
                - Tipo de actividad, Acción y responsable de proyecto, si existen

                3. LIMPIEZA Y VALIDACIÓN
                - Excluye registros vacíos, duplicados exactos y registros con horas menores o iguales a cero.
                - Valida que cliente, actividad, fechas, horas y recurso estén disponibles.
                - Conserva las horas registradas en el Excel. No compares las horas con la diferencia entre la hora de inicio y la hora de fin ni generes observaciones por esa diferencia: el intervalo puede incluir almuerzo, pausas o períodos de inactividad.
                - No cambies el archivo Excel original.
                - No interpretes una Acción genérica como “Realizar” como prueba de que la actividad está cerrada.

                4. REVISIÓN ORTOGRÁFICA
                Antes de generar los archivos, realiza una revisión ortográfica básica de las descripciones: corrige errores evidentes, tildes y espacios sin cambiar el sentido técnico. La primera letra de cada actividad debe ir en mayúscula. Conserva las siglas y nombres propios.

                5. OBSERVACIONES
                Las observaciones de control interno no deben aparecer en los informes para clientes. Incluye una observación en el HTML únicamente cuando la revisión de los datos identifique información clara y relevante que el cliente deba conocer. Omite observaciones genéricas, por ejemplo, que se encontró un correo responsable. No incluyas observaciones sobre horas frente a intervalos horarios. Si no existe una observación pertinente para el cliente, deja vacío el contenido del espacio de observaciones ya previsto en la plantilla; no agregues, elimines ni reordenes elementos HTML.

                6. AGRUPACIÓN Y CÁLCULOS
                Agrupa los registros válidos por cliente/cuenta. Para cada cliente calcula:
                - Cantidad de actividades
                - Total de horas, sumando el campo Horas
                - Recursos participantes
                - Hasta tres actividades principales, sin duplicarlas
                Ordena las filas de actividades cronológicamente por fecha y hora de inicio.

                7. ESTRUCTURA HTML Y EML INMUTABLE
                La estructura de la PLANTILLA HTML es obligatoria. No la rediseñes ni cambies su estructura, jerarquía, orden de secciones, etiquetas, estilos, colores, tabla, columnas, dimensiones, márgenes, tipografía, firma ni pie de página. Reemplaza únicamente los textos variables, las filas del <tbody>, el resumen y los placeholders existentes.

                Conserva el mismo diseño visual de referencia, compatible con Outlook: fondo gris claro (#f4f6f9), contenedor blanco centrado de máximo 750 px, encabezado azul (#003366) centrado, tipografía Arial, secciones con márgenes y rellenos amplios, bloque de actividades en tarjeta blanca con borde suave, resumen en tarjeta gris clara con borde izquierdo azul y pie de firma separado por una línea. Usa tablas HTML para la estructura principal y estilos en línea; no uses JavaScript, CSS externo ni recursos web externos. Mantén este diseño en el HTML y en el EML.

                El cuerpo del correo debe comenzar, inmediatamente después del encabezado, con estos dos párrafos:
                “Buen día,”
                “Comparto la relación de actividades ejecutadas durante el mes de {mes_limpio} de {anio_limpio}.”

                En la tabla usa estas columnas y en este orden: Actividad, Fecha, Hora inicio, Hora fin y Horas. No incluyas la columna Recurso en la tabla; los recursos participantes se muestran únicamente en el bloque de resumen. Presenta fechas en formato DD/MM/AAAA y horas en formato de 24 horas.

                No incluyas destinatario ni asunto como texto visible dentro del cuerpo HTML. El destinatario y el asunto van únicamente en los encabezados del EML.

                En el resumen incluye el período, el nombre del cliente, la cantidad de actividades, las horas registradas, el tipo de actividad cuando esté disponible, los recursos participantes y las actividades principales. Presenta las actividades con la revisión ortográfica básica y primera letra mayúscula.

                El orden del cuerpo es fijo: saludo e introducción, actividades realizadas, detalles de la sesión con observaciones, párrafos de confirmación y firma. Solo mejora la presentación visual; no muevas, elimines ni reordenes estas secciones ni cambies su contenido.

                Conserva al final el mismo bloque de firma de las actas de consultoría, con una línea superior, tipografía y espaciado uniformes. Mantén estos datos en este orden y sin variaciones:
                Presenta la firma visualmente así: “Cordialmente,” en una línea; en la siguiente, “{consultor_nombre}” en negrita y azul oscuro; debajo “{consultor_cargo}”; después las líneas “Tel: {consultor_tel}”, “Móvil: {consultor_celular}” y “Correo: {consultor_email}”. No conviertas estos datos en una lista ni los agrupes en una sola línea.
                {consultor_nombre}
                {consultor_cargo}
                Tel: {consultor_tel}
                Móvil: {consultor_celular}
                Correo: {consultor_email}
                CONSENSUS S.A.S.



                Debajo de CONSENSUS S.A.S., incluye la imagen de firma con ancho máximo de 400px y la etiqueta exacta:
                <img src="cid:imagen_firma" alt="Consensus S.A.S." style="display: block; max-width: 400px; height: auto; margin-top: 10px;" />

                8. DESTINATARIO Y ASUNTO
                Para cada cliente usa el correo válido de Correo Responsable. Si hay varios correos responsables válidos y distintos para el mismo cliente, inclúyelos en el encabezado To, separados por coma (,), sin duplicarlos. Si no hay correo, escribe exactamente [Correo no encontrado]. No inventes direcciones.

                El asunto debe tener exactamente este formato:
                Relación de actividades realizadas - [Nombre del Cliente] - {mes_limpio.upper()} {anio_limpio}

                9. FORMATO ESTRICTO DE LA RESPUESTA
                Para cada cliente genera tres bloques en este orden, separados por la línea literal ----DELIMITADOR_PARTE----. No añadas explicaciones, saludos ni texto fuera de la estructura indicada.

                <!-- INICIO_CLIENTE: [Nombre del Cliente] -->
                [CONTENIDO_HTML_COMPLETO, siguiendo exactamente la PLANTILLA HTML]
                ----DELIMITADOR_PARTE----
                To: [Correo del Responsable o Correo no encontrado]
                Subject: Relación de actividades realizadas - [Nombre del Cliente] - {mes_limpio.upper()} {anio_limpio}
                ----DELIMITADOR_PARTE----
                | [Nombre Cliente] | [Horas Totales] | [Correo del Responsable o Correo no encontrado] | ✅ Generado |
                <!-- FIN_CLIENTE: [Nombre del Cliente] -->

                Repite los bloques para todos los clientes procesados. No incluyas Markdown adicional ni cambies el texto de los delimitadores o comentarios de inicio y fin.

                10. GENERACIÓN DE ARCHIVOS
                El HTML completo de cada primer bloque se usará como cuerpo del EML de ese cliente. Genera el EML con los encabezados To y Subject del segundo bloque, y el HTML como cuerpo. No adjuntes el archivo HTML. Marca el EML como no enviado para que pueda revisarse antes de enviarlo. Escribe los archivos en uploads/ usando nombres seguros basados en el nombre del cliente y el período. Genera también el resumen final a partir de las líneas Markdown del tercer bloque.
            """

            respuesta_gemini = llamar_gemini_con_fallback(cliente_gemini, contents=[prompt_implementacion])

            # Crear subcarpeta por período: uploads/Mes_Año/
            nombre_carpeta_periodo = f"{mes_limpio}_{anio_limpio}"
            DIR_PERIODO = UPLOADS_DIR / nombre_carpeta_periodo
            DIR_PERIODO.mkdir(parents=True, exist_ok=True)

            resumen_final_md = [
                f"# 📊 Resumen de Procesamiento: {periodo_solicitado}",
                f"**Archivos generados en la carpeta:** `{str(DIR_PERIODO)}`", "---",
                "### 📂 Archivos Generados", "| Cliente / Cuenta | Horas Totales | Destinatario | Estado |", "| :--- | :--- | :--- | :--- |"
            ]
            errores_proceso = []
            clientes_procesados = 0
            archivos_generados = []

            bloques_cliente = re.findall(r'<!-- INICIO_CLIENTE: (.*?) -->(.*?)<!-- FIN_CLIENTE: \1 -->', respuesta_gemini.text, re.DOTALL)

            for nombre_cliente_match, contenido_bloque in bloques_cliente:
                clientes_procesados += 1
                try:
                    partes = contenido_bloque.split('----DELIMITADOR_PARTE----')
                    if len(partes) < 3: continue
                    
                    contenido_html, encabezados_eml, resumen_md = partes[0].strip(), partes[1].strip(), partes[2].strip()
                    safe_cliente = nombre_archivo_seguro(nombre_cliente_match, "Cliente")

                    # Preparar HTML para navegador incrustando Base64 de la imagen de firma
                    html_para_archivo = contenido_html
                    img_bytes = obtener_bytes_imagen_firma()
                    if img_bytes:
                        try:
                            b64_img = base64.b64encode(img_bytes).decode('utf-8')
                            uri_b64 = f"data:image/jpeg;base64,{b64_img}"
                            if 'cid:imagen_firma' in html_para_archivo:
                                html_para_archivo = html_para_archivo.replace('cid:imagen_firma', uri_b64)
                            elif 'CONSENSUS S.A.S.</p>' in html_para_archivo and uri_b64 not in html_para_archivo:
                                html_para_archivo = html_para_archivo.replace('CONSENSUS S.A.S.</p>', f'CONSENSUS S.A.S.</p><br><img src="{uri_b64}" alt="Consensus S.A.S." style="display: block; max-width: 400px; height: auto; margin-top: 10px;" />', 1)
                        except Exception as e_b64:
                            print(f"Advertencia al incrustar Base64 en HTML: {e_b64}")

                    nombre_html = f"{safe_cliente}_Reporte_{mes_limpio}_{anio_limpio}.html"
                    nombre_eml = f"{safe_cliente}_Correo_{mes_limpio}_{anio_limpio}.eml"
                    contador_archivo = 2
                    while (DIR_PERIODO / nombre_html).exists() or (DIR_PERIODO / nombre_eml).exists():
                        nombre_html = f"{safe_cliente}_Reporte_{mes_limpio}_{anio_limpio}_{contador_archivo}.html"
                        nombre_eml = f"{safe_cliente}_Correo_{mes_limpio}_{anio_limpio}_{contador_archivo}.eml"
                        contador_archivo += 1

                    guardar_texto_con_reintentos(DIR_PERIODO / nombre_html, html_para_archivo)
                    
                    # ** CAMBIO 2: LÓGICA PARA CREAR EL EML EDITABLE **
                    msg = EmailMessage()
                    to_addr = re.search(r'To:\s*(.*)', encabezados_eml, re.IGNORECASE)
                    subject = re.search(r'Subject:\s*(.*)', encabezados_eml, re.IGNORECASE)
                    msg['To'] = to_addr.group(1).strip() if to_addr and to_addr.group(1).strip() else '[Correo no especificado]'
                    msg['Subject'] = subject.group(1).strip() if subject and subject.group(1).strip() else f"Reporte de Actividades - {nombre_cliente_match}"
                    msg['From'] = remitente_eml
                    
                    # Encabezado clave para que Outlook lo abra como borrador
                    msg['X-Unsent'] = '1'
                    
                    html_para_eml = contenido_html
                    if 'cid:imagen_firma' not in html_para_eml and 'CONSENSUS S.A.S.</p>' in html_para_eml:
                        html_para_eml = html_para_eml.replace('CONSENSUS S.A.S.</p>', 'CONSENSUS S.A.S.</p><br><img src="cid:imagen_firma" alt="Consensus S.A.S." style="display: block; max-width: 250px; height: auto; margin-top: 10px;" />', 1)

                    msg.set_content("Este es un correo en formato HTML. Por favor, utiliza un cliente de correo compatible.")
                    msg.add_alternative(html_para_eml, subtype='html')

                    # Adjuntar imagen como recurso CID relacionado al HTML para Outlook
                    if img_bytes:
                        try:
                            payloads = msg.get_payload()
                            if isinstance(payloads, list) and len(payloads) > 1:
                                payloads[1].add_related(img_bytes, 'image', 'jpeg', cid='<imagen_firma>')
                        except Exception as e_cid:
                            print(f"Advertencia al adjuntar imagen CID a EML: {e_cid}")
                    
                    guardar_texto_con_reintentos(DIR_PERIODO / nombre_eml, msg.as_string())

                    archivos_generados.append({
                        "cliente": nombre_cliente_match,
                        "nombre_html": nombre_html,
                        "url_ver_html": f"/ver/{DIR_PERIODO.name}/{nombre_html}",
                        "url_descarga_html": f"/descargar/{DIR_PERIODO.name}/{nombre_html}",
                        "nombre_eml": nombre_eml,
                        "url_descarga_eml": f"/descargar/{DIR_PERIODO.name}/{nombre_eml}"
                    })

                    link_html = f"[`{nombre_html}`](/descargar/{DIR_PERIODO.name}/{nombre_html})"
                    link_eml = f"[`{nombre_eml}`](/descargar/{DIR_PERIODO.name}/{nombre_eml})"
                    resumen_modificado = resumen_md.replace('[Nombre_Archivo].html', link_html).replace('[Nombre_Archivo].eml', link_eml)
                    resumen_final_md.append(resumen_modificado)
                
                except Exception as e_gen:
                    errores_proceso.append(f"Fallo al generar archivos para '{nombre_cliente_match}': {str(e_gen)}")

            if errores_proceso:
                resumen_final_md.append("\n---\n### ⚠️ Errores\n" + "\n".join(f"- {e}" for e in errores_proceso))
            if not bloques_cliente:
                resumen_final_md.append("| *No se generaron archivos. Revisa el Excel o la respuesta de la IA.* | | | |")

            if archivos_generados:
                resumen_final_md.append("\n---")
                resumen_final_md.append(f"> 📦 **Descarga rápida:** [Descargar todas las actas y correos en un archivo .ZIP](/descargar-zip/{nombre_carpeta_periodo})\n")

            return JSONResponse(content={
                "success": True,
                "mensaje": f"Proceso finalizado. {len(archivos_generados)} reporte(s) generado(s).",
                "ruta_exportacion": f"uploads/{nombre_carpeta_periodo}/",
                "url_zip": f"/descargar-zip/{nombre_carpeta_periodo}" if archivos_generados else None,
                "archivos_generados": archivos_generados,
                "acta_borrador": "\n".join(resumen_final_md)
            })
        
        # --- Flujo de Consultoría Posventa ---
        else:
            cliente_nombre_final = nombre_cliente.strip() if nombre_cliente and nombre_cliente.strip() not in ("", "[Nombre del Cliente]") else "Cliente"
            responsable_final = responsable_cliente.strip() if responsable_cliente and responsable_cliente.strip() not in ("", "[Responsable del Cliente]") else "Responsable"
            fecha_final = fecha_ejecucion.strip() if fecha_ejecucion and fecha_ejecucion.strip() not in ("", "[Fecha de Ejecución]") else "Fecha"
            horas_final = horas_ejecutadas.strip() if horas_ejecutadas and horas_ejecutadas.strip() not in ("", "[Horas Ejecutadas]") else "0"

            partes_insumos = []
            if audio and audio.filename and audio.filename.strip():
                bytes_audio = await audio.read()
                mime = audio.content_type or 'audio/webm'
                if 'webm' in mime:
                    mime = 'audio/webm'
                partes_insumos.append(types.Part.from_bytes(data=bytes_audio, mime_type=mime))
            
            texto_documentos = ""
            if archivos:
                for arch in archivos:
                    if arch and arch.filename and arch.filename.strip():
                        bytes_arch = await arch.read()
                        nombre_lower = arch.filename.lower()
                        if nombre_lower.endswith((".txt", ".csv", ".log", ".text", ".md", ".json", ".sql", ".ini", ".rtf")):
                            texto_archivo = ""
                            for enc in ("utf-8-sig", "utf-8", "cp1252", "latin-1", "iso-8859-1", "utf-16"):
                                try:
                                    texto_archivo = bytes_arch.decode(enc)
                                    break
                                except Exception:
                                    pass
                            if not texto_archivo:
                                texto_archivo = bytes_arch.decode("utf-8", errors="ignore")
                            texto_documentos += f"\n--- Archivo Adjunto ({arch.filename}) ---\n{texto_archivo.strip()}\n"
                        elif nombre_lower.endswith((".xlsx", ".xls")):
                            try:
                                texto_excel = preparar_excel_para_prompt(bytes_arch)
                                texto_documentos += f"\n--- Archivo Excel ({arch.filename}) ---\n{texto_excel}\n"
                            except Exception as e_xl:
                                print(f"Error al leer excel en consultoría: {e_xl}")
                        elif nombre_lower.endswith(".docx"):
                            try:
                                import docx
                                doc = docx.Document(io.BytesIO(bytes_arch))
                                parrafos = [p.text for p in doc.paragraphs if p.text.strip()]
                                texto_documentos += f"\n--- Documento Word ({arch.filename}) ---\n" + "\n".join(parrafos) + "\n"
                            except Exception as e_docx:
                                print(f"Error al leer docx: {e_docx}")
                        elif nombre_lower.endswith(".pdf"):
                            partes_insumos.append(types.Part.from_bytes(data=bytes_arch, mime_type="application/pdf"))
                        elif nombre_lower.endswith((".png", ".jpg", ".jpeg", ".webp")):
                            mime_img = "image/png" if nombre_lower.endswith(".png") else "image/jpeg"
                            partes_insumos.append(types.Part.from_bytes(data=bytes_arch, mime_type=mime_img))
                        else:
                            try:
                                texto_generico = bytes_arch.decode("utf-8-sig", errors="ignore")
                                if texto_generico.strip():
                                    texto_documentos += f"\n--- Archivo ({arch.filename}) ---\n{texto_generico.strip()}\n"
                            except Exception:
                                pass

            if not partes_insumos and not texto_documentos.strip() and not informacion_extra.strip():
                return JSONResponse(status_code=400, content={"success": False, "error": "Debes adjuntar al menos un archivo de texto, audio o ingresar notas del servicio."})

            notas_limpias = informacion_extra.strip() if informacion_extra.strip() else "(Ninguna nota manual ingresada)"
            documentos_limpios = texto_documentos.strip() if texto_documentos.strip() else "(Ningún contenido de texto en archivos adjuntos)"

            prompt_consultoria = f"""ACTÚA COMO UN CONSULTOR SENIOR DE PROCESOS SAP BUSINESS ONE DE CONSENSUS S.A.S. ESPECIALIZADO EN CONSULTORÍA POSVENTA Y SOPORTE.

Tu labor fundamental es analizar detalladamente los insumos suministrados (archivos adjuntos, notas o audios), identificar y extraer TODAS las tareas, consultas, revisiones técnicas, configuraciones, capacitaciones o actividades realizadas, y generar el archivo HTML formal del correo y los datos de encabezado para exportar como correo y acta.

==================================================
DATOS GENERALES DEL SERVICIO:
==================================================
- CLIENTE: {cliente_nombre_final}
- RESPONSABLE CLIENTE: {responsable_final}
- FECHA DE EJECUCIÓN: {fecha_final}
- HORAS DE CONSULTORÍA: {horas_final} horas

NOTAS DEL CONSULTOR:
{notas_limpias}

CONTENIDO DE LOS ARCHIVOS ADJUNTOS:
{documentos_limpios}
==================================================

REGLAS CRÍTICAS DE EXTRACCIÓN Y PROCESAMIENTO:
1. EXTRACCIÓN TOTAL DE ACTIVIDADES REALIZADAS:
   - Extrae e identifica CADA UNA de las tareas, requerimientos, consultas, revisiones, desarrollos o configuraciones técnicas contenidas en el "CONTENIDO DE LOS ARCHIVOS ADJUNTOS", en las "NOTAS DEL CONSULTOR" o en el audio.
   - Redacta cada actividad con tono profesional, técnico y formal para SAP Business One (ejemplo: "Revisión y parametrización de...", "Análisis y depuración de error en...", "Capacitación al equipo de...", "Validación de informes y consultas SQL en...").
   - PROHIBIDO TERMINANTEMENTE dejar la lista vacía o escribir textos genéricos o corchetes de plantilla como "[Escribe aquí...]", "[Actividad...]", "(Enumera todas las actividades...)". Todo el contenido debe ser concreto y real.
   - Si los insumos contienen varios puntos o viñetas, incluye TODOS y cada uno de ellos como actividades separadas dentro de la etiqueta <ol>.
2. OBSERVACIONES:
   - Si en los insumos se menciona alguna observación, dificultad, acuerdo o pendiente relevante para el cliente, redáctala claramente.
   - Si no hay observaciones explícitas, coloca exactamente: Ninguna.
3. DESTINATARIO Y ASUNTO:
   - To: Correo del responsable si se encuentra en los insumos, o "{responsable_final}".
   - Subject: Confirmación de Consultoría SAP Business One - {cliente_nombre_final} - {fecha_final}

4. FORMATO ESTRICTO DE RESPUESTA:
Debes responder con los siguientes tres bloques separados por la línea delimitadora ----DELIMITADOR_PARTE----.
No incluyas explicaciones previas ni posteriores, ni bloques de código con comillas invertidas.

<!-- INICIO_CONSULTORIA: {cliente_nombre_final} -->
<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8">
  <title>Confirmación de Consultoría SAP Business One</title>
</head>
<body style="margin: 0; padding: 20px; background-color: #f4f6f9; font-family: Arial, Helvetica, sans-serif; color: #333333;">
  <table align="center" border="0" cellpadding="0" cellspacing="0" width="100%" style="max-width: 750px; background-color: #ffffff; border: 1px solid #e0e0e0; border-radius: 6px; overflow: hidden;">
    <tr>
      <td style="background-color: #003366; padding: 20px; text-align: center; color: #ffffff;">
        <h2 style="margin: 0; font-size: 20px; font-weight: normal; letter-spacing: 0.5px;">CONSENSUS S.A.S.</h2>
        <p style="margin: 5px 0 0 0; font-size: 13px; color: #d0e1fd;">Confirmación de Consultoría Posventa - SAP Business One</p>
      </td>
    </tr>
    <tr>
      <td style="padding: 25px 30px;">
        <p style="margin: 0 0 12px 0; font-size: 14px; line-height: 1.5;">Buen día, <strong>{cliente_nombre_final}</strong>.</p>
        <p style="margin: 0 0 20px 0; font-size: 14px; line-height: 1.5;">Mediante el presente, me permito confirmar la consultoría prestada, en la cual se llevaron a cabo las siguientes actividades:</p>
        
        <div style="background-color: #ffffff; border: 1px solid #e2e8f0; border-radius: 6px; padding: 18px 20px; margin-bottom: 25px;">
          <h3 style="margin: 0 0 12px 0; font-size: 15px; color: #003366; border-bottom: 1px solid #e2e8f0; padding-bottom: 8px;">Actividades Realizadas</h3>
          <ol style="margin: 0; padding-left: 20px; font-size: 13px; line-height: 1.7; color: #2d3748;">
            <!-- AQUÍ COLOCAS CADA ACTIVIDAD EXTRAÍDA COMO <li style="margin-bottom: 8px;">Descripción técnica detallada</li> -->
          </ol>
        </div>

        <div style="background-color: #f8f9fa; border-left: 4px solid #003366; padding: 15px 20px; margin-bottom: 25px; border-radius: 0 4px 4px 0;">
          <h3 style="margin: 0 0 10px 0; font-size: 14px; color: #003366; text-transform: uppercase; letter-spacing: 0.5px;">Detalles de la Sesión</h3>
          <p style="margin: 4px 0; font-size: 13px;"><strong>Responsable de la actividad:</strong> {responsable_final}</p>
          <p style="margin: 4px 0; font-size: 13px;"><strong>Fecha de ejecución:</strong> {fecha_final}</p>
          <p style="margin: 4px 0; font-size: 13px;"><strong>Tiempo total ejecutado:</strong> {horas_final} horas</p>
          <p style="margin: 4px 0; font-size: 13px;"><strong>Observaciones:</strong> [Observación concreta o Ninguna]</p>
        </div>

        <p style="margin: 0 0 12px 0; font-size: 13px; line-height: 1.5; color: #4a5568;">
          Lo anterior con la finalidad de confirmar los procesos realizados durante la sesión remota y su aprobación mediante la respuesta de este correo.
        </p>
        <p style="margin: 0 0 25px 0; font-size: 13px; line-height: 1.5; color: #4a5568;">
          Quedo atento ante su confirmación de aprobación por este medio.
        </p>

        <div style="border-top: 1px solid #e0e0e0; padding-top: 20px; font-size: 13px; color: #333333; line-height: 1.4;">
          <p style="margin: 0 0 5px 0;">Cordialmente,</p>
          <p style="margin: 0; font-weight: bold; color: #003366; font-size: 14px;">{consultor_nombre}</p>
          <p style="margin: 0;">{consultor_cargo}</p>
          <p style="margin: 0;">Tel: {consultor_tel}</p>
          <p style="margin: 0;">Móvil: {consultor_celular}</p>
          <p style="margin: 0;">Correo: {consultor_email}</p>
          <p style="margin: 5px 0 0 0; font-weight: bold; color: #003366;">CONSENSUS S.A.S.</p>
        </div>
      </td>
    </tr>
  </table>
</body>
</html>
----DELIMITADOR_PARTE----
To: [Correo o nombre del destinatario]
Subject: Confirmación de Consultoría SAP Business One - {cliente_nombre_final} - {fecha_final}
----DELIMITADOR_PARTE----
| {cliente_nombre_final} | {fecha_final} | {horas_final} hrs | [Nombre_Archivo].html | [Nombre_Archivo].eml | ✅ Generado |
<!-- FIN_CONSULTORIA: {cliente_nombre_final} -->
"""
            partes_insumos.append(prompt_consultoria)
            respuesta_gemini = llamar_gemini_con_fallback(cliente_gemini, contents=partes_insumos)

            fecha_periodo_consultoria = pd.to_datetime(fecha_final, dayfirst=True, errors="coerce")
            if pd.notna(fecha_periodo_consultoria):
                meses_consultoria = ["Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio", "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"]
                nombre_periodo_consultoria = f"{meses_consultoria[fecha_periodo_consultoria.month - 1]}_{fecha_periodo_consultoria.year}"
            else:
                nombre_periodo_consultoria = "Sin_fecha"
            DIR_PERIODO = UPLOADS_DIR / f"Consultoria_{nombre_periodo_consultoria}"
            DIR_PERIODO.mkdir(parents=True, exist_ok=True)

            resumen_final_md = [
                f"# 📊 Resumen de Consultoría Posventa",
                f"**Archivos exportados en la carpeta:** `{str(DIR_PERIODO)}`",
                "---",
                "### 📂 Archivos Generados",
                "| Cliente | Fecha | Horas | Archivo HTML | Archivo EML (Outlook) | Estado |",
                "| :--- | :--- | :--- | :--- | :--- | :--- |"
            ]
            errores_proceso = []
            archivos_generados = []
            nombre_html_primero = ""

            bloques_consultoria = re.findall(
                r'<!-- INICIO_CONSULTORIA: (.*?) -->(.*?)<!-- FIN_CONSULTORIA: \1 -->',
                respuesta_gemini.text,
                re.DOTALL
            )

            # Fallback en caso de que Gemini no use los tags de comentario
            if not bloques_consultoria:
                bloques_consultoria = [(cliente_nombre_final, respuesta_gemini.text)]

            for nombre_cliente_match, contenido_bloque in bloques_consultoria:
                try:
                    partes = contenido_bloque.split('----DELIMITADOR_PARTE----')
                    if len(partes) >= 2:
                        contenido_html = partes[0].strip()
                        encabezados_eml = partes[1].strip()
                        resumen_md = partes[2].strip() if len(partes) > 2 else ""
                    else:
                        contenido_html = contenido_bloque.strip()
                        encabezados_eml = f"To: {responsable_final}\nSubject: Confirmación de Consultoría SAP Business One - {nombre_cliente_match} - {fecha_final}"
                        resumen_md = f"| {nombre_cliente_match} | {fecha_final} | {horas_final} hrs | [Nombre_Archivo].html | [Nombre_Archivo].eml | ✅ Generado |"

                    # Limpiar delimitadores de markdown si Gemini los envolvió
                    contenido_html = re.sub(r'^```(?:html)?\s*', '', contenido_html, flags=re.IGNORECASE)
                    contenido_html = re.sub(r'\s*```$', '', contenido_html).strip()

                    # Si el contenido no tiene estructura HTML completa, envolverlo en plantilla básica
                    if "<table" not in contenido_html and "<html" not in contenido_html:
                        contenido_html = f"""<!DOCTYPE html>
<html lang="es"><head><meta charset="UTF-8"><title>Consultoría SAP Business One</title></head>
<body style="font-family: Arial, sans-serif; background-color: #f4f6f9; padding: 20px;">
  <div style="max-width: 800px; background: #fff; padding: 25px; border-radius: 6px; border: 1px solid #e0e0e0; margin: auto;">
    <div style="background-color: #003366; padding: 20px; color: #fff; text-align: center; border-radius: 4px; margin-bottom: 20px;">
      <h2 style="margin: 0; font-size: 18px;">CONSENSUS S.A.S. - Consultoría Posventa</h2>
    </div>
    <div style="line-height: 1.6; color: #333;">{contenido_html.replace(chr(10), '<br>')}</div>
     <div style="margin-top: 25px; border-top: 1px solid #e0e0e0; padding-top: 20px; font-size: 13px; color: #333333; line-height: 1.4;">
       <p style="margin: 0 0 5px 0;">Cordialmente,</p>
      <p style="margin: 0; font-weight: bold; color: #003366;">{consultor_nombre}</p>
       <p style="margin: 0;">{consultor_cargo}</p>
       <p style="margin: 0;">Tel: {consultor_tel}</p>
       <p style="margin: 0;">Móvil: {consultor_celular}</p>
       <p style="margin: 0;">Correo: {consultor_email}</p>
       <p style="margin: 5px 0 0 0; font-weight: bold; color: #003366;">CONSENSUS S.A.S.</p>
    </div>
  </div>
</body></html>"""

                    safe_cliente = nombre_archivo_seguro(nombre_cliente_match, "Cliente")
                    safe_fecha = "".join(c for c in fecha_final if c.isalnum() or c in "_-").strip().replace("-", "_")
                    if not safe_fecha:
                        safe_fecha = "Fecha"

                    nombre_html = f"Acta_Consultoria_{safe_cliente}_{safe_fecha}.html"
                    nombre_eml = f"Correo_Consultoria_{safe_cliente}_{safe_fecha}.eml"
                    contador_archivo = 2
                    while (DIR_PERIODO / nombre_html).exists() or (DIR_PERIODO / nombre_eml).exists():
                        nombre_html = f"Acta_Consultoria_{safe_cliente}_{safe_fecha}_{contador_archivo}.html"
                        nombre_eml = f"Correo_Consultoria_{safe_cliente}_{safe_fecha}_{contador_archivo}.eml"
                        contador_archivo += 1
                    if not nombre_html_primero:
                        nombre_html_primero = nombre_html

                    # Preparar HTML para navegador incrustando Base64 de la imagen de firma
                    html_para_archivo = contenido_html
                    img_bytes = obtener_bytes_imagen_firma()
                    if img_bytes:
                        try:
                            b64_img = base64.b64encode(img_bytes).decode('utf-8')
                            uri_b64 = f"data:image/jpeg;base64,{b64_img}"
                            if 'cid:imagen_firma' in html_para_archivo:
                                html_para_archivo = html_para_archivo.replace('cid:imagen_firma', uri_b64)
                            elif 'CONSENSUS S.A.S.</p>' in html_para_archivo and uri_b64 not in html_para_archivo:
                                html_para_archivo = html_para_archivo.replace('CONSENSUS S.A.S.</p>', f'CONSENSUS S.A.S.</p><br><img src="{uri_b64}" alt="Consensus S.A.S." style="display: block; max-width: 400px; height: auto; margin-top: 10px;" />', 1)
                        except Exception as e_b64:
                            print(f"Advertencia al incrustar Base64 en HTML: {e_b64}")

                    guardar_texto_con_reintentos(DIR_PERIODO / nombre_html, html_para_archivo)

                    # Crear archivo .EML funcional para Outlook en uploads/
                    msg = EmailMessage()
                    to_match = re.search(r'To:\s*(.*)', encabezados_eml, re.IGNORECASE)
                    subj_match = re.search(r'Subject:\s*(.*)', encabezados_eml, re.IGNORECASE)

                    to_addr = to_match.group(1).strip() if to_match and to_match.group(1).strip() else responsable_final
                    subject = subj_match.group(1).strip() if subj_match and subj_match.group(1).strip() else f"Confirmación de Consultoría SAP Business One - {nombre_cliente_match} - {fecha_final}"

                    msg['To'] = to_addr
                    msg['Subject'] = subject
                    msg['From'] = remitente_eml
                    msg['X-Unsent'] = '1'  # Indica a Outlook abrirlo directamente como borrador editable

                    html_para_eml = contenido_html
                    if 'cid:imagen_firma' not in html_para_eml and 'CONSENSUS S.A.S.</p>' in html_para_eml:
                        html_para_eml = html_para_eml.replace('CONSENSUS S.A.S.</p>', 'CONSENSUS S.A.S.</p><br><img src="cid:imagen_firma" alt="Consensus S.A.S." style="display: block; max-width: 250px; height: auto; margin-top: 10px;" />', 1)

                    msg.set_content("Este es un correo en formato HTML. Por favor, utiliza un cliente de correo compatible.")
                    msg.add_alternative(html_para_eml, subtype='html')

                    # Adjuntar imagen como recurso CID relacionado al HTML para Outlook
                    if img_bytes:
                        try:
                            payloads = msg.get_payload()
                            if isinstance(payloads, list) and len(payloads) > 1:
                                payloads[1].add_related(img_bytes, 'image', 'jpeg', cid='<imagen_firma>')
                        except Exception as e_cid:
                            print(f"Advertencia al adjuntar imagen CID a EML: {e_cid}")

                    guardar_texto_con_reintentos(DIR_PERIODO / nombre_eml, msg.as_string())

                    archivos_generados.append({
                        "cliente": nombre_cliente_match,
                        "nombre_html": nombre_html,
                        "url_ver_html": f"/ver/{DIR_PERIODO.name}/{nombre_html}",
                        "url_descarga_html": f"/descargar/{DIR_PERIODO.name}/{nombre_html}",
                        "nombre_eml": nombre_eml,
                        "url_descarga_eml": f"/descargar/{DIR_PERIODO.name}/{nombre_eml}"
                    })

                    link_html = f"[`{nombre_html}`](/descargar/{DIR_PERIODO.name}/{nombre_html})"
                    link_eml = f"[`{nombre_eml}`](/descargar/{DIR_PERIODO.name}/{nombre_eml})"
                    if resumen_md:
                        fila_md = resumen_md.replace('[Nombre_Archivo].html', link_html).replace('[Nombre_Archivo].eml', link_eml)
                        resumen_final_md.append(fila_md)
                    else:
                        resumen_final_md.append(f"| **{nombre_cliente_match}** | {fecha_final} | {horas_final} hrs | {link_html} | {link_eml} | ✅ Generado |")

                except Exception as e_gen:
                    errores_proceso.append(f"Fallo al generar archivos para '{nombre_cliente_match}': {str(e_gen)}")

            if errores_proceso:
                resumen_final_md.append("\n---\n### ⚠️ Errores\n" + "\n".join(f"- {e}" for e in errores_proceso))

            if archivos_generados:
                resumen_final_md.append("\n---")
                resumen_final_md.append(f"> 📦 **Descarga rápida:** [Descargar todas las actas y correos en un archivo .ZIP](/descargar-zip/{DIR_PERIODO.name})\n")
                resumen_final_md.append(f"> 💡 **Archivos listos:** Puedes descargar los archivos **.HTML** o **.EML** directamente arriba. Haz doble clic en el archivo `.eml` descargado para abrirlo en Microsoft Outlook como borrador listo para enviar.")

            return JSONResponse(content={
                "success": True,
                "mensaje": f"Acta de consultoría exportada con éxito en .HTML y .EML en uploads/{DIR_PERIODO.name}/",
                "ruta_exportacion": f"uploads/{DIR_PERIODO.name}/",
                "url_zip": f"/descargar-zip/{DIR_PERIODO.name}" if archivos_generados else None,
                "archivos_generados": archivos_generados,
                "acta_borrador": "\n".join(resumen_final_md)
            })

    except Exception as exc:
        return JSONResponse(status_code=500, content={"success": False, "error": f"Error interno: {str(exc)}"})


if __name__ == "__main__":
    import uvicorn
    import webbrowser, threading

    port = int(os.environ.get("PORT", 8000))
    # En la nube (Render, Railway, Cloud Run) o Docker, escuchar en 0.0.0.0.
    # En desarrollo local en Windows, por defecto 127.0.0.1.
    host = os.environ.get("HOST", "0.0.0.0" if os.environ.get("PORT") else "127.0.0.1")

    # Abrir navegador automáticamente solo cuando se ejecuta localmente en escritorio
    if not os.environ.get("PORT") and not os.environ.get("VERCEL"):
        def abrir_navegador():
            import time
            time.sleep(1.5)
            try:
                webbrowser.open(f"http://localhost:{port}")
            except Exception:
                pass
        threading.Thread(target=abrir_navegador, daemon=True).start()

    print(f"🚀 Iniciando servidor Consensus en http://{host}:{port}")
    uvicorn.run(app, host=host, port=port, reload=False)
