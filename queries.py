"""
Lógica de datos del Dashboard de Trámites.

OPERACIONES  -> tabla `Tramite`
  - Estado en columna de texto `TramiteEstado`:
      PENDIENTE, GESTION  -> en curso (se evalúan por antigüedad)
      FINALIZADO          -> finalizado
  - Fecha de referencia: `TramiteFechaSolicitud`
  (Operaciones no cambió — se evalúa desde la fecha de solicitud.)

COMERCIAL    -> tabla `TramiteComercial`
  - Estado SIEMPRE vía el FK `EstadoTramiteId` -> `EstadoTramite.EstadoTramiteDesc`.
    (Descartamos el campo de texto. Mientras el EMI regulariza los registros
    huérfanos, los que tienen FK NULL se muestran en un aviso para corregir.)
  - 5 estados:
      Elaboracion de propuesta  -> en elaboración (sin reloj)
      Propuesta enviada         -> en elaboración (sin reloj)
      Propuesta completada      -> el reloj de vencimiento ARRANCA acá
      Propuesta presentada      -> cierra el reloj (cuenta como aceptada)
      Propuesta Desestimada     -> cierra el reloj (cuenta como desestimada)
  - Fecha de referencia del reloj: `TramiteComercialFechaCompletad`
    (columna pendiente que el EMI agregará). Si todavía no existe, se cae
    a `TramiteComercialFechaEmision` y se muestra un aviso.

CLASIFICACIÓN TEMPORAL (solo para trámites en estado "Propuesta completada"):
  antigüedad = días entre la fecha de completado y hoy
    antigüedad <  umbral_por_vencer        -> En fecha
    umbral_por_vencer <= ant < umbral_vencido -> Por vencer
    antigüedad >= umbral_vencido           -> Vencido
"""
from db import get_connection, run_query

# Desfase horario: la BD guarda en UTC, Perú es UTC-5.
TZ_BD = "+00:00"
TZ_LOCAL = "-05:00"


# ==========================================================================
# Estados de OPERACIONES (texto plano en Tramite.TramiteEstado)
# ==========================================================================
OP_EN_CURSO = {"PENDIENTE", "GESTION"}
OP_FINALIZADO = {"FINALIZADO"}


# ==========================================================================
# Estados de COMERCIAL — se identifican por UUID del catálogo EstadoTramite.
# El catálogo de Zyra es estable: cada estado tiene un ID fijo que no cambia.
# Mapa: EstadoTramiteId -> (categoría, etiqueta para mostrar)
# ==========================================================================
CO_ESTADOS_POR_ID = {
    "b86a26a6-c459-4b52-b98e-cabcaec38847": ("en_elaboracion", "Elaboración de propuesta"),
    "a29bed7c-7a01-46b8-9ca9-7634468c9b2b": ("presentada",     "Propuesta presentada"),
    "3a891d6b-f427-4556-8c30-055fb360077c": ("en_curso_reloj", "Propuesta completada"),
    "aaeabcfa-037a-46e0-afbc-647e199dbaa2": ("aceptada",       "Propuesta aceptada"),
    "ced0944b-4712-4369-bc84-3545459bbd38": ("desestimada",    "Propuesta desestimada"),
}


def clasificar_temporal(dias, umbral_por_vencer, umbral_vencido):
    if dias >= umbral_vencido:
        return "vencido"
    if dias >= umbral_por_vencer:
        return "por_vencer"
    return "en_fecha"


# ==========================================================================
# Detección del campo nuevo TramiteComercialFechaCompletad
# Si la columna todavía no existe, usamos FechaEmision como fallback.
# ==========================================================================
def _columna_existe(tabla, columna):
    res = run_query("""
        SELECT 1 FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME   = %s
          AND COLUMN_NAME  = %s
        LIMIT 1
    """, [tabla, columna])
    return bool(res)


# ==========================================================================
# Lectura de la BD
# ==========================================================================
def _traer_operaciones():
    sql = f"""
        SELECT
            t.TramiteId                                    AS id,
            t.TramiteCorrelativo                           AS codigo,
            t.TramiteEstado                                AS estado_raw,
            TRIM(e.EmpresaRazonSocial)                     AS contratante,
            COALESCE(ej.EjecutivoNombres, '')              AS ejecutivo,
            COALESCE(r.RamoNombre, '')                     AS ramo,
            COALESCE(a.AseguradoraNombre, '')              AS aseguradora,
            DATEDIFF(
                CONVERT_TZ(NOW(), '{TZ_BD}', '{TZ_LOCAL}'),
                CONVERT_TZ(t.TramiteFechaSolicitud, '{TZ_BD}', '{TZ_LOCAL}')
            )                                              AS antiguedad_dias
        FROM Tramite t
        LEFT JOIN Empresa     e  ON e.EmpresaID    = t.EmpresaID
        LEFT JOIN Ejecutivo   ej ON ej.EjecutivoId = t.EjecutivoId
        LEFT JOIN Ramo        r  ON r.RamoId       = t.RamoId
        LEFT JOIN Aseguradora a  ON a.AseguradoraID = t.AseguradoraID
    """
    return run_query(sql)


def _traer_comerciales(usar_fecha_completado):
    """
    Si usar_fecha_completado=True (el campo existe en BD), el reloj se
    calcula desde TramiteComercialFechaCompletad. Si no, se cae a la
    FechaEmision como respaldo.
    """
    if usar_fecha_completado:
        fecha_reloj_sql = "tc.TramiteComercialFechaCompletad"
    else:
        fecha_reloj_sql = "tc.TramiteComercialFechaEmision"

    sql = f"""
        SELECT
            tc.TramiteComercialId                          AS id,
            tc.TramiteComercialCodigo                      AS codigo,
            tc.EstadoTramiteId                             AS estado_id,
            TRIM(e.EmpresaRazonSocial)                     AS contratante,
            COALESCE(ej.EjecutivoNombres, '')              AS ejecutivo,
            COALESCE(r.RamoNombre, '')                     AS ramo,
            CASE
              WHEN {fecha_reloj_sql} IS NULL THEN NULL
              ELSE DATEDIFF(
                CONVERT_TZ(NOW(), '{TZ_BD}', '{TZ_LOCAL}'),
                CONVERT_TZ({fecha_reloj_sql}, '{TZ_BD}', '{TZ_LOCAL}')
              )
            END                                            AS antiguedad_dias
        FROM TramiteComercial tc
        LEFT JOIN Empresa   e  ON e.EmpresaID    = tc.EmpresaID
        LEFT JOIN Ejecutivo ej ON ej.EjecutivoId = tc.EjecutivoId
        LEFT JOIN Ramo      r  ON r.RamoId       = tc.RamoId
    """
    return run_query(sql)


# ==========================================================================
# Construcción del dashboard
# ==========================================================================
def construir_dashboard(config):
    op_pv = config["op_por_vencer"]
    op_ve = config["op_vencido"]
    co_pv = config["co_por_vencer"]
    co_ve = config["co_vencido"]

    # --- OPERACIONES (sin cambios) --------------------------------------
    ops_en_curso = []
    op_finalizados = 0
    op_sin_estado = 0
    for t in _traer_operaciones():
        estado = (t["estado_raw"] or "").strip().upper()
        if estado in OP_FINALIZADO:
            op_finalizados += 1
        elif estado in OP_EN_CURSO:
            ant = t["antiguedad_dias"] or 0
            ops_en_curso.append({
                "modulo": "Operaciones",
                "codigo": t["codigo"],
                "contratante": t["contratante"] or "(sin contratante)",
                "ejecutivo": t["ejecutivo"] or "Sin asignar",
                "ramo": t["ramo"],
                "estado": "Pendiente" if estado == "PENDIENTE" else "En gestión",
                "antiguedad": ant,
                "clase": clasificar_temporal(ant, op_pv, op_ve),
            })
        else:
            op_sin_estado += 1

    op_vencido    = sum(1 for x in ops_en_curso if x["clase"] == "vencido")
    op_por_vencer = sum(1 for x in ops_en_curso if x["clase"] == "por_vencer")
    op_en_fecha   = sum(1 for x in ops_en_curso if x["clase"] == "en_fecha")

    # --- COMERCIAL (lógica nueva) ---------------------------------------
    campo_fecha_completado_existe = _columna_existe(
        "TramiteComercial", "TramiteComercialFechaCompletad"
    )
    co_trs = _traer_comerciales(campo_fecha_completado_existe)

    co_en_elaboracion = []   # estados 1 y 2: no se evalúan por antigüedad
    co_en_curso = []         # estado 3 "Propuesta completada": el reloj corre
    co_presentadas = 0       # estado 4 "Propuesta presentada": el reloj YA frenó
    co_aceptadas = 0         # estado 5 "Propuesta aceptada": final positivo
    co_desestimadas = 0      # estado 6 "Propuesta desestimada": final negativo
    co_sin_estado = 0
    co_completadas_sin_fecha = 0  # están "completadas" pero sin fecha de completado

    for t in co_trs:
        # Lookup directo por UUID — sin normalización de strings.
        cat, etiqueta = CO_ESTADOS_POR_ID.get(t["estado_id"], (None, None))
        if cat is None:
            # Estados sin asignar (estado_id NULL) o desconocidos (UUID
            # nuevo que aún no esté en el mapa) caen acá.
            co_sin_estado += 1
            continue

        if cat == "presentada":
            co_presentadas += 1
            continue
        if cat == "aceptada":
            co_aceptadas += 1
            continue
        if cat == "desestimada":
            co_desestimadas += 1
            continue
        if cat == "en_elaboracion":
            co_en_elaboracion.append({
                "modulo": "Comercial",
                "codigo": t["codigo"],
                "contratante": t["contratante"] or "(sin contratante)",
                "ejecutivo": t["ejecutivo"] or "Sin asignar",
                "ramo": t["ramo"],
                "estado": etiqueta,
                "antiguedad": None,
                "clase": "en_elaboracion",
            })
            continue
        # cat == "en_curso_reloj" -> Propuesta completada
        ant = t["antiguedad_dias"]
        if ant is None:
            # Está completada pero no tiene fecha de completado.
            # Lo contamos aparte; no clasifica por antigüedad.
            co_completadas_sin_fecha += 1
            co_en_curso.append({
                "modulo": "Comercial",
                "codigo": t["codigo"],
                "contratante": t["contratante"] or "(sin contratante)",
                "ejecutivo": t["ejecutivo"] or "Sin asignar",
                "ramo": t["ramo"],
                "estado": etiqueta + " (sin fecha de completado)",
                "antiguedad": 0,
                "clase": "sin_fecha",
            })
            continue
        co_en_curso.append({
            "modulo": "Comercial",
            "codigo": t["codigo"],
            "contratante": t["contratante"] or "(sin contratante)",
            "ejecutivo": t["ejecutivo"] or "Sin asignar",
            "ramo": t["ramo"],
            "estado": etiqueta,
            "antiguedad": ant,
            "clase": clasificar_temporal(ant, co_pv, co_ve),
        })

    co_vencido    = sum(1 for x in co_en_curso if x["clase"] == "vencido")
    co_por_vencer = sum(1 for x in co_en_curso if x["clase"] == "por_vencer")
    # 'En fecha' incluye: post-completado dentro del plazo + los previos al
    # reloj (Elaboración + Enviada). Ambos significan "está OK".
    co_en_fecha_post_completado = sum(
        1 for x in co_en_curso if x["clase"] == "en_fecha"
    )
    co_en_fecha = co_en_fecha_post_completado + len(co_en_elaboracion)
    co_finalizados = co_aceptadas + co_desestimadas

    # --- Críticos por módulo ----------------------------------------------
    op_criticos = sorted(
        [x for x in ops_en_curso if x["clase"] == "vencido"],
        key=lambda x: x["antiguedad"], reverse=True,
    )
    co_criticos = sorted(
        [x for x in co_en_curso if x["clase"] == "vencido"],
        key=lambda x: x["antiguedad"], reverse=True,
    )

    def carga_por_ejecutivo(lista_criticos):
        carga = {}
        for c in lista_criticos:
            carga[c["ejecutivo"]] = carga.get(c["ejecutivo"], 0) + 1
        return sorted(
            ({"ejecutivo": k, "criticos": v} for k, v in carga.items()),
            key=lambda x: x["criticos"], reverse=True,
        )

    operaciones = {
        "contadores": {
            "vencidos": op_vencido, "por_vencer": op_por_vencer,
            "en_fecha": op_en_fecha, "finalizados": op_finalizados,
        },
        "barra": {
            "en_fecha": op_en_fecha, "por_vencer": op_por_vencer,
            "vencido": op_vencido, "finalizado": op_finalizados,
        },
        "criticos": op_criticos,
        "carga": carga_por_ejecutivo(op_criticos),
        "en_curso": len(ops_en_curso),
        "sin_estado": op_sin_estado,
    }

    comercial = {
        "contadores": {
            "vencidos": co_vencido, "por_vencer": co_por_vencer,
            "en_fecha": co_en_fecha,
            "presentadas": co_presentadas,
            "finalizados": co_finalizados,
            "aceptadas": co_aceptadas, "desestimadas": co_desestimadas,
        },
        "barra": {
            "en_fecha": co_en_fecha, "por_vencer": co_por_vencer,
            "vencido": co_vencido,
            "presentadas": co_presentadas,
            "finalizado": co_finalizados,
        },
        "en_elaboracion": co_en_elaboracion,
        "en_elaboracion_total": len(co_en_elaboracion),
        "criticos": co_criticos,
        "carga": carga_por_ejecutivo(co_criticos),
        "en_curso": len(co_en_curso),  # solo "completadas con reloj"
        "sin_estado": co_sin_estado,
        "completadas_sin_fecha": co_completadas_sin_fecha,
    }

    avisos = []
    if not campo_fecha_completado_existe:
        avisos.append(
            "La columna 'TramiteComercialFechaCompletad' aún no existe "
            "en la BD. Mientras tanto, el reloj de vencimiento de Comercial "
            "se calcula desde la Fecha de Emisión (fallback)."
        )
    # Nota: los trámites en "Propuesta completada" sin fecha de completado
    # quedan fuera del cálculo de antigüedad y no se muestran como aviso
    # (decisión del área). El conteo sigue disponible en
    # comercial.completadas_sin_fecha por si se necesita en el futuro.

    return {
        "operaciones": operaciones,
        "comercial": comercial,
        "config": config,
        "avisos_globales": avisos,
        "campo_fecha_completado_existe": campo_fecha_completado_existe,
    }
