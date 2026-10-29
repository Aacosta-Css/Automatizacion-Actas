# 📑 Automatización de Actas de Proceso (Consensus S.A.S.)

Sistema inteligente de generación de actas de consultoría y reportes de actividades de implementación en formato **HTML** y correos editables **.EML** (compatibles con Microsoft Outlook), potenciado por la API de **Google Gemini Enterprise**.

---

## 🚀 Opciones de Ejecución

### 1. Ejecución Local (Windows / Mac / Linux)

#### En Windows (Fácil - 1 Clic):
Simplemente haz doble clic en el archivo:
```text
run_local.bat
```
El script activará el entorno virtual `.venv` (si existe), iniciará el servidor FastAPI y abrirá automáticamente tu navegador en `http://localhost:8000`.

#### En Consola / Terminal:
1. **Crear o activar entorno virtual:**
   ```bash
   # En Windows:
   python -m venv .venv
   .venv\Scripts\activate

   # En Linux / macOS:
   python3 -m venv .venv
   source .venv/bin/activate
   ```

2. **Instalar dependencias:**
   ```bash
   pip install -r requirements.txt
   ```

3. **Configurar archivo `.env`:**
   Copia el archivo de ejemplo y agrega tu clave de Gemini:
   ```bash
   cp .env.example .env
   ```
   Dentro de `.env` puedes configurar la clave y opcionalmente los datos del consultor:
   ```env
   GEMINI_API_KEY=tu_clave_de_gemini_aqui
   GEMINI_MODEL=gemini-3.6-flash
   CONSULTOR_NOMBRE=Alexander Acosta Beltrán
   CONSULTOR_EMAIL=aacosta@consensussa.com
   ```

4. **Iniciar el servidor:**
   ```bash
   # Opción directa:
   python main.py

   # O con recarga en vivo para desarrollo:
   uvicorn main:app --reload --port 8000
   ```
   Abre [http://localhost:8000](http://localhost:8000) en tu navegador.

---

## 🌐 Opciones de Despliegue en la Web

La aplicación está preparada para desplegarse sin problemas en múltiples plataformas en la nube:

### Opción A: Despliegue con Docker / Cloud Run / Render / Railway (Recomendado para Producción)
Dado que la aplicación genera archivos `.html`, `.eml` y descargas en `.zip`, se recomienda desplegar en entornos con sistema de archivos persistente o contenedores:
1. **Render / Railway / Heroku:** Detección automática mediante `Procfile`.
2. **Google Cloud Run / AWS ECS:** Utilizando el `Dockerfile` incluido.

### Opción B: Despliegue en Vercel (Serverless)
*Nota: Los entornos serverless de Vercel tienen almacenamiento efímero en `/tmp` y límites de ejecución de 60 segundos.*
1. Conecta tu repositorio de GitHub o GitLab a **[Vercel](https://vercel.com)**.
2. Vercel detectará la configuración automáticamente mediante `vercel.json` y `api/index.py`.
3. En la sección **Settings > Environment Variables**, agrega `GEMINI_API_KEY` y `GEMINI_MODEL=gemini-3.6-flash`.
4. Haz clic en **Deploy**.
4. Start Command (detectado automáticamente por `Procfile`):
   ```bash
   uvicorn main:app --host 0.0.0.0 --port $PORT
   ```
5. En la sección de variables de entorno añade:
   - `GEMINI_API_KEY`
6. Despliega la aplicación.

### Opción C: Despliegue con Docker / Cloud Run
Se incluye un archivo `Dockerfile` optimizado:
```bash
# Construir la imagen
docker build -t actas-consensus .

# Ejecutar el contenedor
docker run -p 8000:8000 -e GEMINI_API_KEY="tu_api_key" actas-consensus
```

---

## 📁 Estructura del Proyecto

```text
├── api/
│   └── index.py            # Entry point para Serverless en Vercel
├── templates/
│   ├── index.html          # Interfaz web responsiva con editor y descargas
│   └── Imagen1.jpg         # Firma corporativa Consensus S.A.S.
├── uploads/                # Directorio de actas, correos y audios generados
├── .dockerignore           # Exclusiones para build de Docker
├── .env.example            # Plantilla de variables de entorno
├── .gitignore              # Archivos y carpetas excluidas de Git
├── Dockerfile              # Configuración de contenedor para la nube
├── Procfile                # Comando de inicio para Render / Railway / Heroku
├── README.md               # Documentación y guía de despliegue
├── requirements.txt        # Dependencias Python optimizadas
├── run_local.bat           # Ejecutable de 1 clic para Windows
├── run_local.sh            # Script de inicio para Linux / macOS
├── vercel.json             # Configuración de despliegue en Vercel
└── main.py                 # Backend FastAPI y lógica de integración con Gemini
```

---

## ✨ Características y Mejoras Implementadas

1. **Compatibilidad Multi-Entorno:**
   - Detección automática de permisos de escritura con fallback a directorios temporales en entornos Serverless (como Vercel o AWS Lambda).
   - Enlace automático a `HOST` y `PORT` del entorno en la nube (`0.0.0.0`) y apertura de navegador en escritorio local.

2. **Panel de Descargas Directas:**
   - Visualización de actas en una nueva pestaña del navegador (`👁️ Ver HTML`).
   - Descarga directa de archivos individuales (`⬇️ HTML` y `📧 EML`).
   - Botón de descarga en lote: **📦 Descargar Todo (.ZIP)** con todas las actas y correos del período o sesión generada.

3. **Optimización de Dependencias:**
   - Dependencias estrictamente necesarias sin paquetes masivos innecesarios.
   - Soporte para lectura de documentos Word (`python-docx`), Excel (`pandas`, `openpyxl`), y audios.
