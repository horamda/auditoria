# Arquitectura y decisiones técnicas

## Componentes

```
frontend (React+TS, Vite) ──HTTP/SSE──► backend FastAPI ──► MySQL (base exclusiva QA)
                                            │
                                            ├─ services/executor  (pool de hilos: cancelación, timeout, kill switch)
                                            │     ├─ workers/api_runner   (httpx + safe_request)
                                            │     ├─ workers/web_runner   (Playwright, route interception)
                                            │     └─ workers/load_runner  (Locust en subproceso)
                                            ├─ workers/discovery          (análisis estático, solo lectura)
                                            ├─ services/reports           (HTML + comparación/regresión)
                                            └─ core/guard                 (política de entornos)
```

## Decisiones

| Decisión | Motivo |
|---|---|
| Ejecutor en proceso (ThreadPool) en el MVP, en lugar de Celery+Redis | Menos piezas para validar el flujo completo. La interfaz `submit_run`/`cancel_run` está aislada para reemplazarla por Celery sin tocar la API. Limitación: las ejecuciones en curso se pierden si se reinicia la API. |
| Locust como **subproceso** | Aislamiento de gevent respecto de FastAPI; se puede matar (`kill`) ante cancelación, timeout duro o kill switch. |
| Validación de URL **en cada salto** (`safe_request`), redirecciones no seguidas por Locust | Evita redirecciones a hosts no autorizados / metadata cloud (169.254.169.254). |
| Allowlist obligatoria por entorno | Una URL no autorizada nunca recibe tráfico, aunque un usuario la cargue. |
| Bloqueo de IP internas salvo `allow_private_network` (solo admin, nunca producción) | Protección SSRF; permite probar localhost en desarrollo de forma explícita. |
| Producción: el backend **fuerza** `allow_writes/allow_load/allow_private_network = false` | La protección no depende de la UI ni de que alguien configure bien el entorno. |
| Acciones sensibles (eliminar, liberar, pagar, anular…) bloqueadas por texto/selector | Evita clics destructivos; requieren `allow_sensitive` en el escenario y nunca en producción. |
| Credenciales por referencia `${NOMBRE}` → variable de entorno | Nunca se guardan secretos en la base ni en el código; `redact()` depura claves sensibles antes de persistir evidencias. |
| `evidence_level`: static → observed → verified | Distingue lo descubierto en código de lo probado. Solo una prueba aprobada que declare `feature_ids` marca `verified`. |
| IDs estables = hash(capa, tipo, nombre normalizado) | No cambian si el código se mueve de línea; las funcionalidades que desaparecen quedan `present=false` (no se borran). |
| Semgrep/OSV-Scanner en Docker con el repo montado `:ro`, `--memory 2g --cpus 2 --pids-limit 512 --no-new-privileges`, contenedor con nombre para `docker kill` | Garantía física de no modificar el código analizado; aislamiento, límites y cancelación. |
| pip-audit con `--disable-pip --no-deps` sobre una copia filtrada (solo `==`) fuera del repo | El modo normal instala paquetes (puede ejecutar código de terceros). Las dependencias sin versión fija se informan como limitación; OSV-Scanner igual las cubre parcialmente. |
| Hallazgos sin fragmento de código | Las reglas de secretos devolverían el secreto en la evidencia. |
| Huella de hallazgo independiente del número de línea (regla + archivo + n.º de ocurrencia; paquete + versión + ID para dependencias) | Permite marcar nuevos / existentes / resueltos entre escaneos sin falsos "nuevos" al desplazar código. |
| Una ejecución de seguridad "falla" si hay hallazgos high/critical | Criterio explícito; los hallazgos son candidatos a revisión, no defectos confirmados. |
| Triage de hallazgos asociado a la huella, en tabla propia (`finding_triage`) | La decisión humana (falso positivo / riesgo aceptado / en corrección) sobrevive a nuevos escaneos y a cambios de línea. Descartar o aceptar exige un motivo y queda auditado. |
| Runner Android por `adb` con detección de pantalla en negro (muestreo del framebuffer crudo) | Un "proceso vivo" no prueba que la app se vea: una prueba dio falso aprobado con capturas en negro. Ahora una pantalla uniforme tras iniciar o rotar hace fallar el caso. |
| Los resultados Android guardan el tipo de dispositivo real (`emulator`/`physical`, por propiedades del sistema) | Separa emuladores de dispositivos físicos; el físico exige `allow_physical`. |
| SQLite solo para dev/tests | Producción usa MySQL; la suite completa fue validada sobre MySQL 8.4. |

## Limitaciones conocidas (medidas, no supuestas)

- Descubrimiento por expresiones regulares: puede omitir rutas dinámicas o generar falsos positivos.
  - Prefijos aplicados al registrar (`app.include_router(..., prefix=...)` en FastAPI, `app.register_blueprint(..., url_prefix=...)` en Flask) **no** se resuelven todavía; solo los declarados en el propio `APIRouter`/`Blueprint`.
  - Autenticación aplicada globalmente (`before_request`, middleware) no se detecta por endpoint.
  - Android nativo: se descubren activities/services/receivers, permisos y tests (`androidTest`, `test`); todavía no pantallas/fragments ni llamadas HTTP en Java/Kotlin.
- Android (adb): el MVP verifica arranque, estabilidad (rotación, texto 1.5×, cerrar y reabrir), memoria PSS y cierres/ANR en logcat. No hace login ni recorre pantallas (eso requiere Maestro o `integration_test`, pendientes). La CPU es una sola muestra de `top`, orientativa. El APK probado puede no corresponder al código actual del repositorio (se usa el compilado existente; no se compila nada en los proyectos originales). Un APK puede conectarse a su servidor de producción al abrirse: por eso el runner no ingresa credenciales ni ejecuta operaciones. Dispositivos físicos: soportados por el código (`device_kind: physical` + `allow_physical`) pero sin probar por no haber uno conectado.
- El análisis se acota a las carpetas declaradas en los componentes del proyecto; sin componentes con ruta, se analiza la carpeta completa.
  - `auth_detected` solo reconoce decoradores/dependencias comunes (`jwt_required`, `login_required`, `Depends(get_current_*)`, `require*`, `roles_required`). Un `false` significa "no detectado", no "sin autenticación".
- La resolución DNS se valida antes de cada petición, pero no se fija la IP para la conexión (riesgo residual de DNS rebinding).
- El límite de intentos de login es en memoria (se reinicia con la API y no se comparte entre instancias).
- Un único proceso API: el SSE consulta la base cada segundo.

- Semgrep usa reglas del registro (`p/security-audit`, `p/secrets`, `p/owasp-top-ten`): requiere acceso a internet. Sin reglas específicas para Dart/Flutter.
- OSV-Scanner solo analiza manifiestos/lockfiles que reconoce (requirements, package-lock, pubspec.lock, gradle lockfiles…); dependencias Gradle sin lockfile no se auditan.

- Android: el emulador puede quedar con la pantalla degradada (negra) tras rotar; se observó una vez y se resolvió con un arranque en frío (`-no-snapshot`). No se determinó la causa. Si aparece "pantalla en negro", repetir con arranque en frío antes de considerarlo un defecto de la app.
- Android: la CPU es una sola muestra de `top`; no hay prueba de voz, cámara/GPS ni flujos con login.
- Android: FichaYa no compila hoy con su configuración (AGP 8.11.1 con Gradle 9.7.1 del wrapper; Gradle 9.6+ eliminó una API interna que AGP 8.x usa). Se probó el APK release 1.24.0+32 ya generado. Esto es un hallazgo sobre el proyecto, no se modificó.

## Política de retención (pendiente de implementar como tarea programada)

Propuesta: artefactos (capturas/CSV) 90 días; resultados y métricas 1 año; `audit_logs` sin borrado automático.

## Hoja de ruta

1. **Android**: runner Maestro + `flutter test integration_test` vía ADB, separando emulador y dispositivo físico; métricas de memoria/CPU con `adb shell dumpsys`.
2. **Seguridad** ✅ estática implementada. Triage persistente ✅. Pendiente: revisión dinámica no destructiva de CORS/cabeceras/autorización sobre entornos autorizados.
3. **Integridad de datos**: escenarios de carrera (solicitudes simultáneas, duplicadas, reintentos) solo en entornos aislados con datos sintéticos.
4. **Regresión inteligente**: `regression_baselines` por versión, mapeo archivo→funcionalidad→caso a partir del inventario, disparo por commit.
5. **Celery + Redis**, `execution_agents`, límites de recursos por contenedor.
6. **Reporte PDF** (desde el HTML) y política de retención automática.
7. **GitHub**: Action que llama a la API de QA Control Center y publica estado (permisos mínimos, sin escribir en el repo analizado).
