"""
Dashboard de Trámites — Zyra / Zeruk Brokers
============================================

Dashboard de monitoreo de trámites Comerciales y de Operaciones,
para embeber como iframe dentro de Zyra.

Rutas principales:
  /dashboard?token=...   -> el dashboard (vista única Op + Co)
  /config?token=...      -> pantalla para editar los umbrales dinámicos

Rutas de diagnóstico (relevamiento de la BD):
  /debug/ping            -> prueba la conexión a la BD
  /debug/explorar        -> lista todas las tablas de la BD
  /debug/tabla?nombre=XXX -> estructura + muestra de una tabla
  /debug/estados-comercial / /debug/estados-operacion

Todas las rutas requieren ?token=<DEBUG_TOKEN>.
"""
import os
from functools import wraps
from flask import (
    Flask, request, jsonify, render_template, redirect, url_for
)
from dotenv import load_dotenv

from db import get_connection, run_query
import config as cfg
import queries

load_dotenv()

app = Flask(__name__)

DEBUG_TOKEN = os.getenv("DEBUG_TOKEN", "")

# Crear la tabla de configuración si hace falta (al arrancar).
# Si el usuario MySQL es de solo lectura, esto fallará: lo avisamos
# pero dejamos que la app siga (el dashboard usará los defaults).
try:
    cfg.asegurar_tabla()
    CONFIG_DISPONIBLE = True
    CONFIG_ERROR = ""
except Exception as e:
    CONFIG_DISPONIBLE = False
    CONFIG_ERROR = str(e)


# --------------------------------------------------------------------------
# Protección de los endpoints /debug/
# --------------------------------------------------------------------------
def requiere_token(f):
    """Decorator: el endpoint solo responde si ?token= coincide con DEBUG_TOKEN."""
    @wraps(f)
    def wrapper(*args, **kwargs):
        token = request.args.get("token", "")
        if not DEBUG_TOKEN or token != DEBUG_TOKEN:
            return jsonify({
                "error": "No autorizado. Falta el parámetro ?token= o es incorrecto."
            }), 401
        return f(*args, **kwargs)
    return wrapper


# --------------------------------------------------------------------------
# Healthcheck
# --------------------------------------------------------------------------
@app.route("/")
def home():
    return jsonify({
        "app": "Dashboard de Trámites — Zyra",
        "estado": "operativo",
        "rutas": [
            "/dashboard?token=...",
            "/config?token=...",
            "/debug/ping?token=...",
            "/debug/explorar?token=...",
            "/debug/tabla?token=...&nombre=NOMBRE_TABLA",
        ],
    })


# --------------------------------------------------------------------------
# /dashboard  -> la vista principal del dashboard
# --------------------------------------------------------------------------
@app.route("/dashboard")
@requiere_token
def dashboard():
    try:
        configuracion = cfg.leer_config() if CONFIG_DISPONIBLE else {
            k: v[0] for k, v in cfg.DEFAULTS.items()
        }
        datos = queries.construir_dashboard(configuracion)
        return render_template(
            "dashboard.html",
            d=datos,
            token=request.args.get("token", ""),
            config_disponible=CONFIG_DISPONIBLE,
        )
    except Exception as e:
        return f"<h2>Error al construir el dashboard</h2><pre>{e}</pre>", 500


# --------------------------------------------------------------------------
# /config  -> pantalla para editar los umbrales dinámicos
# --------------------------------------------------------------------------
@app.route("/config", methods=["GET", "POST"])
@requiere_token
def configuracion():
    token = request.args.get("token", "")
    mensaje = ""
    exito = False

    if request.method == "POST":
        if not CONFIG_DISPONIBLE:
            mensaje = ("No se puede guardar: el usuario de la base de datos "
                       "no tiene permiso para crear/escribir la tabla de "
                       "configuración. " + CONFIG_ERROR)
        else:
            nuevos = {
                "op_por_vencer": request.form.get("op_por_vencer"),
                "op_vencido":    request.form.get("op_vencido"),
                "co_por_vencer": request.form.get("co_por_vencer"),
                "co_vencido":    request.form.get("co_vencido"),
            }
            exito, mensaje = cfg.guardar_config(nuevos)

    try:
        configuracion_actual = cfg.leer_config() if CONFIG_DISPONIBLE else {
            k: v[0] for k, v in cfg.DEFAULTS.items()
        }
    except Exception as e:
        configuracion_actual = {k: v[0] for k, v in cfg.DEFAULTS.items()}
        mensaje = mensaje or f"No se pudo leer la configuración: {e}"

    return render_template(
        "config.html",
        config=configuracion_actual,
        token=token,
        mensaje=mensaje,
        exito=exito,
        config_disponible=CONFIG_DISPONIBLE,
    )



# --------------------------------------------------------------------------
# /debug/ping  -> verifica que la conexión a la BD funcione
# --------------------------------------------------------------------------
@app.route("/debug/ping")
@requiere_token
def debug_ping():
    try:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT VERSION() AS version, DATABASE() AS base, NOW() AS ahora_servidor")
                info = cur.fetchone()
        finally:
            conn.close()
        return jsonify({
            "conexion": "OK",
            "version_mysql": info["version"],
            "base_de_datos": info["base"],
            "hora_servidor_mysql": str(info["ahora_servidor"]),
            "nota": "La 'hora_servidor_mysql' nos dice la zona horaria de la BD. "
                    "Compararla con la hora real ayuda a confirmar si Zyra guarda en UTC.",
        })
    except Exception as e:
        return jsonify({"conexion": "ERROR", "detalle": str(e)}), 500


# --------------------------------------------------------------------------
# /debug/explorar  -> lista todas las tablas de la BD
# --------------------------------------------------------------------------
@app.route("/debug/explorar")
@requiere_token
def debug_explorar():
    """
    Lista todas las tablas de la base de datos actual, con su cantidad
    aproximada de filas. Acepta ?buscar=texto para filtrar por nombre.
    """
    try:
        filtro = request.args.get("buscar", "").strip()

        # information_schema.TABLES: catálogo estándar de MySQL con todas las tablas
        sql = """
            SELECT
                TABLE_NAME      AS tabla,
                TABLE_ROWS      AS filas_aprox,
                TABLE_COMMENT   AS comentario
            FROM information_schema.TABLES
            WHERE TABLE_SCHEMA = DATABASE()
        """
        params = []
        if filtro:
            # LOWER() en ambos lados -> el filtro no distingue mayúsculas
            sql += " AND LOWER(TABLE_NAME) LIKE %s"
            params.append(f"%{filtro.lower()}%")
        sql += " ORDER BY TABLE_NAME"

        tablas = run_query(sql, params)

        return jsonify({
            "base_de_datos": os.getenv("DB_NAME"),
            "filtro_aplicado": filtro or "(ninguno — todas las tablas)",
            "cantidad_tablas": len(tablas),
            "tablas": tablas,
            "nota": "filas_aprox es una ESTIMACIÓN de MySQL, no un conteo exacto. "
                    "Usá /debug/tabla?nombre=XXX para ver la estructura y datos reales.",
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# --------------------------------------------------------------------------
# /debug/tabla  -> estructura + muestra de filas de una tabla concreta
# --------------------------------------------------------------------------
@app.route("/debug/tabla")
@requiere_token
def debug_tabla():
    """
    Muestra la estructura (columnas, tipos, claves) de una tabla y
    unas pocas filas de muestra. ?nombre=NOMBRE_TABLA es obligatorio.
    """
    nombre = request.args.get("nombre", "").strip()
    if not nombre:
        return jsonify({"error": "Falta el parámetro ?nombre="}), 400

    # Validación: solo permitir nombres de tabla razonables (evita inyección).
    if not all(c.isalnum() or c in "_$" for c in nombre):
        return jsonify({"error": "Nombre de tabla inválido."}), 400

    try:
        # 1) Verificar que la tabla exista en esta base
        existe = run_query(
            """
            SELECT TABLE_NAME
            FROM information_schema.TABLES
            WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s
            """,
            [nombre],
        )
        if not existe:
            return jsonify({"error": f"La tabla '{nombre}' no existe en esta base."}), 404

        # 2) Estructura de columnas
        columnas = run_query(
            """
            SELECT
                COLUMN_NAME    AS columna,
                COLUMN_TYPE    AS tipo,
                IS_NULLABLE    AS acepta_nulos,
                COLUMN_KEY     AS clave,
                COLUMN_DEFAULT AS valor_por_defecto,
                COLUMN_COMMENT AS comentario
            FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s
            ORDER BY ORDINAL_POSITION
            """,
            [nombre],
        )

        # 3) Conteo real de filas
        total = run_query(f"SELECT COUNT(*) AS total FROM `{nombre}`")[0]["total"]

        # 4) Muestra de hasta 5 filas
        muestra = run_query(f"SELECT * FROM `{nombre}` LIMIT 5")

        return jsonify({
            "tabla": nombre,
            "total_filas_real": total,
            "cantidad_columnas": len(columnas),
            "columnas": columnas,
            "muestra_filas": muestra,
            "nota": "muestra_filas puede contener datos sensibles de clientes. "
                    "Si compartís una captura de esto, tapá nombres, RUC/DNI y montos.",
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# --------------------------------------------------------------------------
# /debug/estados-comercial
# Diagnostica la inconsistencia de los DOS campos de estado de TramiteComercial:
#   - EstadoTramiteId       (FK al catálogo EstadoTramite)
#   - TramiteComercialEstado (texto suelto varchar(40))
# --------------------------------------------------------------------------
@app.route("/debug/estados-comercial")
@requiere_token
def debug_estados_comercial():
    try:
        total = run_query("SELECT COUNT(*) AS n FROM TramiteComercial")[0]["n"]

        # Cuántas filas usan cada campo (o ambos, o ninguno)
        cobertura = run_query("""
            SELECT
                SUM(EstadoTramiteId IS NOT NULL
                    AND TramiteComercialEstado <> '')              AS ambos,
                SUM(EstadoTramiteId IS NOT NULL
                    AND TramiteComercialEstado = '')               AS solo_fk,
                SUM(EstadoTramiteId IS NULL
                    AND TramiteComercialEstado <> '')              AS solo_texto,
                SUM(EstadoTramiteId IS NULL
                    AND TramiteComercialEstado = '')               AS ninguno
            FROM TramiteComercial
        """)[0]

        # Qué valores distintos hay en el campo de texto
        valores_texto = run_query("""
            SELECT TramiteComercialEstado AS valor, COUNT(*) AS cantidad
            FROM TramiteComercial
            GROUP BY TramiteComercialEstado
            ORDER BY cantidad DESC
        """)

        # Qué estados del catálogo se usan vía el FK
        valores_fk = run_query("""
            SELECT e.EstadoTramiteDesc AS estado_catalogo, COUNT(*) AS cantidad
            FROM TramiteComercial tc
            LEFT JOIN EstadoTramite e ON e.EstadoTramiteId = tc.EstadoTramiteId
            WHERE tc.EstadoTramiteId IS NOT NULL
            GROUP BY e.EstadoTramiteDesc
            ORDER BY cantidad DESC
        """)

        return jsonify({
            "tabla": "TramiteComercial",
            "total_filas": total,
            "cobertura": {
                "usan_ambos_campos": cobertura["ambos"],
                "solo_FK_EstadoTramiteId": cobertura["solo_fk"],
                "solo_texto_TramiteComercialEstado": cobertura["solo_texto"],
                "sin_estado_en_ningun_campo": cobertura["ninguno"],
            },
            "valores_en_campo_texto": valores_texto,
            "valores_via_FK_catalogo": valores_fk,
            "nota": "Este endpoint NO decide cuál campo es el correcto. "
                    "Sirve para llevarle datos concretos al equipo del EMI "
                    "y que ellos confirmen cuál es el campo de estado oficial.",
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# --------------------------------------------------------------------------
# /debug/estados-operacion
# Lista los valores distintos del campo de texto Tramite.TramiteEstado
# --------------------------------------------------------------------------
@app.route("/debug/estados-operacion")
@requiere_token
def debug_estados_operacion():
    try:
        valores = run_query("""
            SELECT TramiteEstado AS valor, COUNT(*) AS cantidad
            FROM Tramite
            GROUP BY TramiteEstado
            ORDER BY cantidad DESC
        """)
        total = run_query("SELECT COUNT(*) AS n FROM Tramite")[0]["n"]
        return jsonify({
            "tabla": "Tramite",
            "total_filas": total,
            "valores_en_TramiteEstado": valores,
            "nota": "Estos son los valores reales y exactos del estado de Operaciones.",
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    # En local: python app.py  -> http://127.0.0.1:5000
    # En Render: lo levanta gunicorn (ver Procfile / start command)
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)), debug=True)
