# Dashboard de Trámites — Zyra / Zeruk Brokers

Dashboard de monitoreo de trámites Comerciales y de Operaciones,
para embeber en Zyra.

> **Estado actual:** andamiaje inicial. Todavía NO es el dashboard.
> Solo expone endpoints `/debug/` para relevar la estructura real
> de la base de datos de Zyra. El dashboard se construye después,
> una vez confirmados los nombres reales de las tablas.

## Stack

- Python + Flask
- MySQL (base de datos de Zyra) — solo lectura
- Gunicorn (servidor de producción)
- Desplegado en Render.com

## Setup local (Windows / VS Code)

1. Crear y activar un entorno virtual:
   ```
   python -m venv venv
   venv\Scripts\activate
   ```
2. Instalar dependencias:
   ```
   pip install -r requirements.txt
   ```
3. Copiar `.env.example` como `.env` y completar con los valores reales
   de la base de datos de Zyra. **El `.env` nunca se sube al repo.**
4. Levantar:
   ```
   python app.py
   ```
5. Abrir en el navegador: `http://127.0.0.1:5000`

## Variables de entorno

Ver `.env.example`. Se cargan:
- en local: archivo `.env`
- en Render: panel del servicio → Environment

Nunca se escriben en el código ni se suben al repo.

## Endpoints actuales (todos requieren `?token=<DEBUG_TOKEN>`)

- `/` — healthcheck
- `/debug/ping?token=...` — prueba la conexión a la BD
- `/debug/explorar?token=...` — lista todas las tablas
- `/debug/explorar?token=...&buscar=tramite` — filtra tablas por nombre
- `/debug/tabla?token=...&nombre=NOMBRE_TABLA` — estructura + muestra de una tabla

## Deploy en Render

- Build command: `pip install -r requirements.txt`
- Start command: `gunicorn app:app`
- Cargar las variables de entorno en el panel de Render.
