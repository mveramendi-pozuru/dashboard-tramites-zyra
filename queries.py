"""
Lógica de datos del Dashboard de Trámites.

Reglas de negocio (relevadas y confirmadas con Carla):

OPERACIONES  -> tabla `Tramite`
  - Estado en columna de texto `TramiteEstado`:
      PENDIENTE, GESTION  -> en curso
      FINALIZADO          -> finalizado
  - Fecha de referencia: `TramiteFechaSolicitud`

COMERCIAL    -> tabla `TramiteComercial`
  - Estado: se usa `TramiteComercialEstado` (texto) como principal;
    si está vacío, se cae al catálogo vía `EstadoTramiteId` (respaldo
    durante la migración). Estados:
      Elaboracion de propuesta / Propuesta enviada  -> en curso
      Propuesta completada / Propuesta Desestimada  -> finalizado
  - Fecha de referencia: `TramiteComercialFechaEmision`

CLASIFICACIÓN TEMPORAL (solo para trámites EN CURSO):
  antiguedad = días entre la fecha de referencia y hoy
    antiguedad <  umbral_por_vencer  -> En fecha
    umbral_por_vencer <= ant < umbral_vencido -> Por vencer
    antiguedad >= umbral_vencido     -> Vencido

OTRAS REGLAS:
  - Trámites sin estado: EXCLUIDOS de todo.
  - Fechas en BD están en UTC -> se convierten a UTC-5 con CONVERT_TZ.
  - "Crítico" = trámite en curso clasificado como "Vencido".
"""
from db import run_query

# Desfase horario: la BD guarda en UTC, Perú/México (sin DST) es UTC-5.
TZ_BD = "+00:00"
TZ_LOCAL = "-05:00"


# ==========================================================================
# Normalización de estados
# ==========================================================================
# Operaciones: valores reales en Tramite.TramiteEstado
OP_EN_CURSO = {"PENDIENTE", "GESTION"}
OP_FINALIZADO = {"FINALIZADO"}

# Comercial: equivalencias entre el campo de texto (sin espacios) y el
# catálogo (con espacios). Todo se compara en minúsculas para ser robusto.
# clave normalizada -> ('en_curso' | 'final_ok' | 'final_neg', etiqueta a mostrar)
CO_ESTADOS = {
    "elaboracionpropuesta":     ("en_curso", "Elaboración de propuesta"),
    "elaboraciondepropuesta":   ("en_curso", "Elaboración de propuesta"),
    "propuestaenviada":         ("en_curso", "Propuesta enviada"),
    "propuestacompletada":      ("final_ok", "Propuesta completada"),
    "propuestadesestimada":     ("final_neg", "Propuesta desestimada"),
}


def _norm(texto):
    """Normaliza un estado: minúsculas y sin espacios, para comparar."""
    return (texto or "").strip().lower().replace(" ", "")


def clasificar_estado_comercial(texto_estado, desc_catalogo):
    """
    Devuelve (categoria, etiqueta) para un trámite comercial.
    Usa el campo de texto como principal; si viene vacío, usa la
    descripción del catálogo (respaldo durante la migración).
    categoria: 'en_curso' | 'final_ok' | 'final_neg' | None (sin estado)
    """
    clave = _norm(texto_estado) or _norm(desc_catalogo)
    if not clave:
        return None, None
    return CO_ESTADOS.get(clave, (None, None))


def clasificar_temporal(antiguedad_dias, umbral_por_vencer, umbral_vencido):
    """Clasifica un trámite EN CURSO según su antigüedad en días."""
    if antiguedad_dias >= umbral_vencido:
        return "vencido"
    if antiguedad_dias >= umbral_por_vencer:
        return "por_vencer"
    return "en_fecha"


# ==========================================================================
# Lectura de trámites desde la BD
# ==========================================================================
def _traer_operaciones():
    """
    Trae todos los trámites de Operaciones con los datos que el
    dashboard necesita. La antigüedad se calcula en MySQL con CONVERT_TZ.
    """
    sql = f"""
        SELECT
            t.TramiteId                                   AS id,
            t.TramiteCorrelativo                          AS codigo,
            t.TramiteEstado                               AS estado_raw,
            TRIM(e.EmpresaRazonSocial)                    AS contratante,
            COALESCE(ej.EjecutivoNombres, '')             AS ejecutivo,
            COALESCE(r.RamoNombre, '')                    AS ramo,
            COALESCE(a.AseguradoraNombre, '')             AS aseguradora,
            DATEDIFF(
                CONVERT_TZ(NOW(), '{TZ_BD}', '{TZ_LOCAL}'),
                CONVERT_TZ(t.TramiteFechaSolicitud, '{TZ_BD}', '{TZ_LOCAL}')
            )                                             AS antiguedad_dias
        FROM Tramite t
        LEFT JOIN Empresa     e  ON e.EmpresaID    = t.EmpresaID
        LEFT JOIN Ejecutivo   ej ON ej.EjecutivoId = t.EjecutivoId
        LEFT JOIN Ramo        r  ON r.RamoId       = t.RamoId
        LEFT JOIN Aseguradora a  ON a.AseguradoraID = t.AseguradoraID
    """
    return run_query(sql)


def _traer_comerciales():
    """Trae todos los trámites Comerciales con los datos necesarios."""
    sql = f"""
        SELECT
            tc.TramiteComercialId                         AS id,
            tc.TramiteComercialCodigo                     AS codigo,
            tc.TramiteComercialEstado                     AS estado_texto,
            COALESCE(et.EstadoTramiteDesc, '')            AS estado_catalogo,
            TRIM(e.EmpresaRazonSocial)                    AS contratante,
            COALESCE(ej.EjecutivoNombres, '')             AS ejecutivo,
            COALESCE(r.RamoNombre, '')                    AS ramo,
            DATEDIFF(
                CONVERT_TZ(NOW(), '{TZ_BD}', '{TZ_LOCAL}'),
                CONVERT_TZ(tc.TramiteComercialFechaEmision, '{TZ_BD}', '{TZ_LOCAL}')
            )                                             AS antiguedad_dias
        FROM TramiteComercial tc
        LEFT JOIN EstadoTramite et ON et.EstadoTramiteId = tc.EstadoTramiteId
        LEFT JOIN Empresa       e  ON e.EmpresaID         = tc.EmpresaID
        LEFT JOIN Ejecutivo     ej ON ej.EjecutivoId      = tc.EjecutivoId
        LEFT JOIN Ramo          r  ON r.RamoId            = tc.RamoId
    """
    return run_query(sql)


# ==========================================================================
# Construcción de los datos del dashboard
# ==========================================================================
def construir_dashboard(config):
    """
    Procesa Operaciones + Comercial y arma toda la estructura que la
    plantilla necesita. 'config' es el dict de umbrales (de config.leer_config).
    """
    op_pv = config["op_por_vencer"]
    op_ve = config["op_vencido"]
    co_pv = config["co_por_vencer"]
    co_ve = config["co_vencido"]

    # --- Procesar OPERACIONES -------------------------------------------
    ops_en_curso = []      # trámites en curso, ya clasificados
    op_finalizados = 0
    op_sin_estado = 0
    for t in _traer_operaciones():
        estado = (t["estado_raw"] or "").strip().upper()
        if estado in OP_FINALIZADO:
            op_finalizados += 1
        elif estado in OP_EN_CURSO:
            ant = t["antiguedad_dias"] if t["antiguedad_dias"] is not None else 0
            clase = clasificar_temporal(ant, op_pv, op_ve)
            ops_en_curso.append({
                "modulo": "Operaciones",
                "codigo": t["codigo"],
                "contratante": t["contratante"] or "(sin contratante)",
                "ejecutivo": t["ejecutivo"] or "Sin asignar",
                "ramo": t["ramo"],
                "aseguradora": t["aseguradora"],
                "estado": "Pendiente" if estado == "PENDIENTE" else "En gestión",
                "antiguedad": ant,
                "clase": clase,
            })
        else:
            op_sin_estado += 1

    # --- Procesar COMERCIAL ---------------------------------------------
    co_en_curso = []
    co_final_ok = 0       # Propuesta completada
    co_final_neg = 0      # Propuesta desestimada
    co_sin_estado = 0
    for t in _traer_comerciales():
        categoria, etiqueta = clasificar_estado_comercial(
            t["estado_texto"], t["estado_catalogo"]
        )
        if categoria == "final_ok":
            co_final_ok += 1
        elif categoria == "final_neg":
            co_final_neg += 1
        elif categoria == "en_curso":
            ant = t["antiguedad_dias"] if t["antiguedad_dias"] is not None else 0
            clase = clasificar_temporal(ant, co_pv, co_ve)
            co_en_curso.append({
                "modulo": "Comercial",
                "codigo": t["codigo"],
                "contratante": t["contratante"] or "(sin contratante)",
                "ejecutivo": t["ejecutivo"] or "Sin asignar",
                "ramo": t["ramo"],
                "aseguradora": "",
                "estado": etiqueta,
                "antiguedad": ant,
                "clase": clase,
            })
        else:
            co_sin_estado += 1

    # --- Contadores por clase temporal ----------------------------------
    def contar(lista, clase):
        return sum(1 for x in lista if x["clase"] == clase)

    op_vencido = contar(ops_en_curso, "vencido")
    op_por_vencer = contar(ops_en_curso, "por_vencer")
    op_en_fecha = contar(ops_en_curso, "en_fecha")

    co_vencido = contar(co_en_curso, "vencido")
    co_por_vencer = contar(co_en_curso, "por_vencer")
    co_en_fecha = contar(co_en_curso, "en_fecha")

    co_finalizados = co_final_ok + co_final_neg

    # --- Tarjetas superiores (con desglose Op | Co) ---------------------
    tarjetas = {
        "vencidos":    {"total": op_vencido + co_vencido,
                        "op": op_vencido, "co": co_vencido},
        "por_vencer":  {"total": op_por_vencer + co_por_vencer,
                        "op": op_por_vencer, "co": co_por_vencer},
        "en_fecha":    {"total": op_en_fecha + co_en_fecha,
                        "op": op_en_fecha, "co": co_en_fecha},
        "finalizados": {"total": op_finalizados + co_finalizados,
                        "op": op_finalizados, "co": co_finalizados,
                        "co_completadas": co_final_ok,
                        "co_desestimadas": co_final_neg},
    }

    # --- Barras de distribución por módulo ------------------------------
    barra_operaciones = {
        "en_fecha": op_en_fecha, "por_vencer": op_por_vencer,
        "vencido": op_vencido, "finalizado": op_finalizados,
    }
    barra_comercial = {
        "en_fecha": co_en_fecha, "por_vencer": co_por_vencer,
        "vencido": co_vencido, "finalizado": co_finalizados,
    }

    # --- Trámites críticos (vencidos), ordenados por antigüedad ---------
    criticos = [x for x in (ops_en_curso + co_en_curso) if x["clase"] == "vencido"]
    criticos.sort(key=lambda x: x["antiguedad"], reverse=True)

    # --- Carga por ejecutivo (de los críticos) --------------------------
    carga = {}
    for c in criticos:
        nombre = c["ejecutivo"]
        carga[nombre] = carga.get(nombre, 0) + 1
    carga_ejecutivos = sorted(
        ({"ejecutivo": k, "criticos": v} for k, v in carga.items()),
        key=lambda x: x["criticos"], reverse=True,
    )

    return {
        "tarjetas": tarjetas,
        "barra_operaciones": barra_operaciones,
        "barra_comercial": barra_comercial,
        "criticos": criticos,
        "carga_ejecutivos": carga_ejecutivos,
        "config": config,
        "avisos": {
            "op_sin_estado": op_sin_estado,
            "co_sin_estado": co_sin_estado,
        },
        "totales": {
            "op_en_curso": len(ops_en_curso),
            "co_en_curso": len(co_en_curso),
        },
    }
