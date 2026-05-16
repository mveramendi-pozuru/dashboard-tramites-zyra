"""
Conexión a la base de datos MySQL de Zyra.

Las credenciales se leen de variables de entorno (.env en local,
panel de Render en producción). Nunca se hardcodean.
"""
import os
import pymysql
from dotenv import load_dotenv

load_dotenv()


def get_connection():
    """
    Devuelve una nueva conexión a MySQL.
    El llamador es responsable de cerrarla (idealmente con 'with' o try/finally).
    cursorclass=DictCursor -> cada fila se devuelve como diccionario.
    """
    return pymysql.connect(
        host=os.getenv("DB_HOST"),
        port=int(os.getenv("DB_PORT", 3306)),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
        database=os.getenv("DB_NAME"),
        cursorclass=pymysql.cursors.DictCursor,
        connect_timeout=10,
        read_timeout=30,
        charset="utf8mb4",
    )


def run_query(sql, params=None):
    """
    Ejecuta un SELECT y devuelve todas las filas como lista de diccionarios.
    SOLO para lectura. Este dashboard nunca hace INSERT/UPDATE/DELETE.
    """
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params or ())
            return cur.fetchall()
    finally:
        conn.close()
