# Punto Biometrico Facial Generico

Sistema offline-first de control de asistencia facial para mini PC, kiosco o terminal de acceso. Funciona con webcam USB o camara IP/RTSP, base local SQLite/MySQL y una integracion externa configurable por SQL directo o API REST.

## Caracteristicas

- Kiosco PyQt6 fullscreen con guia facial.
- Registro admin protegido por PIN.
- Seleccion de empleado desde una fuente externa generica.
- Rostros pendientes de asociar cuando no se elige empleado.
- Registro de marcaciones `IN` / `OUT` con cooldown anti-duplicado.
- Intervalo minimo configurable entre marcaciones validas; por ejemplo, bloquear una nueva marca si paso menos de 1 hora.
- Reconocimiento ArcFace/InsightFace con fallback demo.
- FAISS si esta disponible, fallback NumPy.
- Multicamara: webcams e IP/RTSP en paralelo.
- Descubridor RTSP para detectar fuentes en la red local.
- Modo offline con cola local y sincronizacion posterior.

## Instalacion

```powershell
.\scripts\install.ps1
```

Inicializar la base local/biometrica:

```powershell
.\.venv\Scripts\python.exe .\scripts\init_db.py
```

Ejecutar:

```powershell
.\scripts\run_marker.ps1
```

Diagnostico:

```powershell
.\.venv\Scripts\python.exe -m app.main --diagnose
```

Descubrir camaras RTSP:

```powershell
.\.venv\Scripts\python.exe .\scripts\discover_video_sources.py
```

Build portable:

```powershell
.\scripts\build_portable.ps1
```

## Integracion Por DB

Configurar `integration_mode = "generic_db"` y ajustar las consultas en `generic_db`.

```json
"generic_db": {
  "url": "mysql+pymysql://usuario:password@127.0.0.1:3306/rrhh",
  "employee_query": "SELECT id, code AS marker_code, name, status FROM employees WHERE (:query = '' OR name LIKE :query_like OR code LIKE :query_like) ORDER BY name LIMIT :limit",
  "attendance_insert_sql": "INSERT INTO attendance_events (employee_code, timestamp, type, device_id, confidence) VALUES (:marker_code, :timestamp, :type, :device_id, :confidence)",
  "biometric_faces_table": "biometric_faces"
}
```

Parametros disponibles para `employee_query`: `query`, `query_like`, `limit`.

Parametros disponibles para `attendance_insert_sql`: `marker_code`, `timestamp`, `type`, `device_id`, `confidence`.

## Integracion Por REST

Configurar `integration_mode = "generic_rest"`.

```json
"generic_rest": {
  "base_url": "http://127.0.0.1:8000/api",
  "token": "",
  "employees_path": "/employees",
  "biometric_faces_path": "/biometric-faces",
  "attendance_path": "/attendance",
  "timeout_seconds": 8
}
```

Endpoints esperados:

- `GET /employees?q=&limit=` devuelve lista o `{ "employees": [...] }` con `id`, `code`/`marker_code`, `name`, `status`.
- `POST /attendance` recibe `employee_marker_code`, `timestamp`, `type`, `device_id`, `confidence`.
- `POST /biometric-faces` recibe embedding/foto en base64 para asociacion externa.
- `GET /biometric-faces?device_id=&status=&limit=` puede devolver rostros vinculados o pendientes para sincronizar cambios.

## Esquema Generico

Para MySQL puede usar:

```powershell
mysql -u root -p < sql\generic_integration_schema.sql
```

El esquema es solo una referencia. En instalaciones reales se puede apuntar a tablas existentes ajustando SQL en `config.json`.

## Configuracion De Video

`camera_sources` acepta indices OpenCV y URLs:

```json
"camera_sources": [
  {"id": "FRONTAL", "name": "Webcam frontal", "source": 0, "enabled": true, "primary": true},
  {"id": "RTSP_1", "name": "Camara IP", "source": "rtsp://192.168.1.50:554/stream1", "enabled": false}
]
```

Desde Configuracion tambien se puede usar `Descubrir RTSP`; las fuentes encontradas se agregan como deshabilitadas para revisar y activar.

## Seguridad

No versionar `config.json`, `local_cache.db`, `snapshots`, `logs`, builds ni releases. Los rostros y embeddings son datos biometricos sensibles.
