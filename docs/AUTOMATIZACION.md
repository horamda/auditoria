# Automatización

Tres piezas, todas opcionales y desactivables. Nada se ejecuta contra un entorno sin allowlist, y producción sigue en solo lectura.

## 1. Ejecución programada
Proyecto → pestaña **Automatización** → elegir suite, entorno y frecuencia (5 min a 7 días).
- Un hilo del propio servicio revisa cada 30 s. Respeta el kill switch y no encola si la ejecución anterior sigue en curso.
- Las pruebas de **carga** no se programan (se ejecutan a mano). Carga/Android sobre producción están bloqueados.
- `QA_SCHEDULER_ENABLED=false` lo desactiva. Si el servicio se reinicia, lo vencido se lanza al volver (una vez).
- Limitación: ejecutor en proceso; una ejecución en curso se pierde si el servicio se reinicia.

## 2. Avisos
Variable `QA_NOTIFY_WEBHOOK_URL` (https). Recibe `{"text": ..., "content": ...}` (compatible con Slack y Discord; Telegram requiere un puente).
- Avisa cuando falla una ejecución **automática** (programada o de deploy) y la primera vez que vuelve a pasar tras un fallo.
- Las ejecuciones manuales no avisan. Sin la variable queda registrado `notify_skipped` en auditoría.
- El mensaje no incluye evidencias, credenciales ni URLs.

## 3. Hook de deploy
`QA_DEPLOY_HOOK_TOKEN` (cadena larga aleatoria). Sin token, el hook responde 404.
```
POST /api/hooks/deploy      X-QA-Token: <token>
{"project": "FichaYa", "environment": "staging", "version_label": "1.25.0"}
-> 202 {"runs": [12, 13]}
GET  /api/hooks/runs/12     X-QA-Token: <token>   -> {"id":12,"status":"passed","summary":{...}}
```
Lanza las suites `api` y `web` del entorno (máx. 10). Nunca carga, Android ni seguridad.

### Ejemplo de GitHub Action (va en el repo de la app, p. ej. como paso posterior al deploy)
Requiere secretos `QA_URL` y `QA_TOKEN`. Este archivo **no** se instaló en ningún repositorio: copiarlo solo si querés.
```yaml
name: qa-post-deploy
on: workflow_dispatch   # o el evento de deploy que uses
jobs:
  qa:
    runs-on: ubuntu-latest
    steps:
      - name: Lanzar y esperar pruebas
        run: |
          set -e
          ids=$(curl -fsS -X POST "$QA_URL/api/hooks/deploy" -H "X-QA-Token: $QA_TOKEN" -H "Content-Type: application/json" \
                -d "{\"project\":\"FichaYa\",\"environment\":\"staging\",\"version_label\":\"$GITHUB_SHA\"}" | python3 -c "import sys,json;print(' '.join(map(str,json.load(sys.stdin)['runs'])))")
          bad=0
          for id in $ids; do
            for i in $(seq 1 60); do
              st=$(curl -fsS "$QA_URL/api/hooks/runs/$id" -H "X-QA-Token: $QA_TOKEN" | python3 -c "import sys,json;print(json.load(sys.stdin)['status'])")
              case "$st" in queued|running) sleep 10;; passed) break;; *) bad=1; break;; esac
            done
          done
          exit $bad
        env: { QA_URL: "${{ secrets.QA_URL }}", QA_TOKEN: "${{ secrets.QA_TOKEN }}" }
```

## 4. Suite de humo autogenerada
Botón **Generar suite de humo** (o `POST /api/projects/{id}/suites/auto-smoke`): toma los `GET` descubiertos sin autenticación detectada y sin parámetros de ruta, y crea la suite "Humo (autogenerada)".
- **Alcance real:** comprueba disponibilidad (la respuesta no es 5xx, < 5 s). No valida contenido ni lógica. Un endpoint que requiere login pero no se detectó como tal puede dar 401 y aun así "pasa".
- Necesita haber corrido el descubrimiento, que requiere los repos en disco (no ocurre en Railway). Correr el descubrimiento desde una PC apuntando a la misma base (`QA_DATABASE_URL`) o cargar la suite a mano.
