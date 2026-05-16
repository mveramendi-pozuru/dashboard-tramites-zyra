"""
Configuración del dashboard: umbrales de antigüedad (en días) para
clasificar trámites en curso como En fecha / Por vencer / Vencido.

Los umbrales son DINÁMICOS: se guardan en una tabla propia del dashboard
(DashboardTramitesConfig) y se editan desde la pantalla de configuración.

Esta tabla es la ÚNICA que el dashboard escribe. Todo lo demás es solo lectura.
"""
from db import get_connection, run_query

NOMBRE_TABLA = "DashboardTramitesConfig"

# Valores por defecto si la tabla está vacía (primera vez).
# clave -> (valor_por_defecto, descripción)
DEFAULTS = {
    "op_por_vencer":  (3,  "Operaciones: días para pasar a 'Por vencer'"),
    "op_vencido":     (7,  "Operaciones: días para pasar a 'Vencido'"),
    "co_por_vencer":  (5,  "Comercial: días para pasar a 'Por vencer'"),
    "co_vencido":     (10, "Comercial: días para pasar a 'Vencido'"),
}


def asegurar_tabla():
    """
    Crea la tabla de configuración si no existe y la rellena con los
    valores por defecto. Es seguro llamarla siempre al arrancar.
    """
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(f"""
                CREATE TABLE IF NOT EXISTS `{NOMBRE_TABLA}` (
                    clave        VARCHAR(50)  NOT NULL PRIMARY KEY,
                    valor        INT          NOT NULL,
                    descripcion  VARCHAR(200) NOT NULL
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """)
            # Insertar defaults solo si la clave no existe todavía
            for clave, (valor, desc) in DEFAULTS.items():
                cur.execute(
                    f"INSERT IGNORE INTO `{NOMBRE_TABLA}` (clave, valor, descripcion) "
                    f"VALUES (%s, %s, %s)",
                    (clave, valor, desc),
                )
        conn.commit()
    finally:
        conn.close()


def leer_config():
    """Devuelve un dict {clave: valor} con los umbrales actuales."""
    filas = run_query(f"SELECT clave, valor FROM `{NOMBRE_TABLA}`")
    config = {f["clave"]: f["valor"] for f in filas}
    # Completar con defaults cualquier clave que faltara
    for clave, (valor, _desc) in DEFAULTS.items():
        config.setdefault(clave, valor)
    return config


def guardar_config(nuevos_valores):
    """
    Actualiza los umbrales. 'nuevos_valores' es un dict {clave: valor}.
    Solo acepta las 4 claves conocidas y valores enteros positivos.
    Devuelve (ok, mensaje).
    """
    limpios = {}
    for clave in DEFAULTS:
        if clave not in nuevos_valores:
            continue
        try:
            v = int(nuevos_valores[clave])
        except (TypeError, ValueError):
            return False, f"El valor de '{clave}' no es un número entero válido."
        if v < 1:
            return False, f"El valor de '{clave}' debe ser 1 o mayor."
        limpios[clave] = v

    # Validación de coherencia: 'por vencer' debe ser menor que 'vencido'
    if "op_por_vencer" in limpios and "op_vencido" in limpios:
        if limpios["op_por_vencer"] >= limpios["op_vencido"]:
            return False, "Operaciones: 'Por vencer' debe ser menor que 'Vencido'."
    if "co_por_vencer" in limpios and "co_vencido" in limpios:
        if limpios["co_por_vencer"] >= limpios["co_vencido"]:
            return False, "Comercial: 'Por vencer' debe ser menor que 'Vencido'."

    if not limpios:
        return False, "No se recibió ningún valor para guardar."

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            for clave, valor in limpios.items():
                cur.execute(
                    f"UPDATE `{NOMBRE_TABLA}` SET valor = %s WHERE clave = %s",
                    (valor, clave),
                )
        conn.commit()
    finally:
        conn.close()
    return True, "Umbrales guardados correctamente."
