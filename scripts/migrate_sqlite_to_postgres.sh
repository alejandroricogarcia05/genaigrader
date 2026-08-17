#!/usr/bin/env bash
# =============================================================================
# migrate_sqlite_to_postgres.sh
#
# Migra los datos del antiguo entorno SQLite (gunicorn + ngrok + tmux) a la
# base de datos PostgreSQL del entorno de producción Docker.
#
# Qué hace, paso a paso:
#   1. Comprueba que el entorno antiguo está parado y que Docker está listo.
#   2. (Solo con --reset) Borra los volúmenes de producción.
#   3. Levanta el stack de producción: PostgreSQL crea la BD vacía y el
#      contenedor web crea las tablas con 'migrate' (automático).
#   4. Copia db.sqlite3 (+ su WAL, donde están los datos más recientes) a un
#      directorio temporal y exporta los datos a datadump.json.
#   5. Importa datadump.json en PostgreSQL.
#   6. Copia uploaded_files/ al volumen de producción.
#   7. Verifica que cada tabla tiene el mismo número de filas que el JSON.
#
# Requisitos:
#   - Docker con el plugin Compose v2.
#   - El entorno antiguo parado:  ./scripts/stop_genaigrader.sh
#   - La PostgreSQL de destino VACÍA (o usar --reset para borrarla antes).
#
# Uso:
#   ./scripts/migrate_sqlite_to_postgres.sh [--reset]
#
# Notas:
#   - Todo se ejecuta dentro de contenedores: no hace falta Python ni uv en
#     la máquina donde se lanza el script.
#   - El db.sqlite3 original NO se modifica ni se borra.
# =============================================================================

set -euo pipefail

# --- Rutas y constantes ------------------------------------------------------
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

COMPOSE=(docker compose --env-file .env -f Docker/docker-compose.prod.yml)
DUMP_FILE="$PROJECT_DIR/datadump.json"
SNAP_DIR=""

log() { echo; echo "==> $*"; }
die() { echo; echo "ERROR: $*" >&2; exit 1; }

cleanup() {
    # Borra el directorio temporal con la copia del SQLite (si existe)
    [ -n "$SNAP_DIR" ] && [ -d "$SNAP_DIR" ] && rm -rf "$SNAP_DIR"
}
trap cleanup EXIT

# --- Argumentos --------------------------------------------------------------
RESET=0
for arg in "$@"; do
    case "$arg" in
        --reset) RESET=1 ;;
        -h|--help) grep '^#' "$0" | head -n 35; exit 0 ;;
        *) die "Opción desconocida: '$arg' (usa --help)" ;;
    esac
done

# =============================================================================
log "1/7 Comprobaciones previas..."
# =============================================================================
command -v docker >/dev/null 2>&1 || die "Docker no está instalado o no está en el PATH."
docker compose version >/dev/null 2>&1 || die "El plugin 'docker compose' (v2) no está disponible."
[ -f .env ] || die "No existe .env en $PROJECT_DIR (cópialo de .env.example y configúralo)."
[ -f db.sqlite3 ] || die "No existe db.sqlite3 en $PROJECT_DIR."

# El entorno antiguo usa siempre el settings_ngrok (gunicorn y qcluster).
# Si sigue corriendo, podría escribir en el SQLite mientras lo copiamos.
if pgrep -f "settings_ngrok" >/dev/null 2>&1; then
    die "El entorno antiguo (gunicorn/ngrok) sigue corriendo. Páralo antes con: ./scripts/stop_genaigrader.sh"
fi

# =============================================================================
if [ "$RESET" = "1" ]; then
    log "2/7 Borrando los volúmenes de producción (--reset)..."
    echo "¡ATENCIÓN! Esto destruirá TODOS los datos actuales de PostgreSQL"
    echo "y los volúmenes de ficheros subidos y estáticos."
    read -r -p "Escribe BORRAR para confirmar: " confirm
    [ "$confirm" = "BORRAR" ] || die "Cancelado por el usuario."
    "${COMPOSE[@]}" down -v
fi

# =============================================================================
log "3/7 Levantando el stack de producción (crea la BD vacía y las tablas)..."
# =============================================================================
"${COMPOSE[@]}" up -d --build

# =============================================================================
log "4/7 Exportando los datos del SQLite antiguo a JSON..."
# =============================================================================
# Copiamos db.sqlite3 junto con sus ficheros -wal/-shm: los datos más
# recientes están en el WAL y sin él la exportación estaría incompleta.
# Además, las copias pasan a ser nuestras (el WAL original suele ser de root)
# y el original queda intacto.
SNAP_DIR="$(mktemp -d)"
cp "$PROJECT_DIR"/db.sqlite3* "$SNAP_DIR"/

# La exportación se hace DENTRO de un contenedor temporal de la propia imagen
# web (así se usa exactamente el mismo Django que producción). Un pequeño
# programa Python apunta Django a la copia del SQLite y llama a 'dumpdata'.
"${COMPOSE[@]}" run --rm --no-deps -T \
    -e DATABASE_URL= \
    -e DJANGO_DB_ENGINE= \
    -v "$SNAP_DIR:/snapshot" \
    --entrypoint uv \
    web run python - /snapshot/db.sqlite3 /snapshot/datadump.json <<'PYTHON'
import os
import sys

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "mi_web.settings")

import django

django.setup()

# Las conexiones de Django son perezosas: como aún no se ha abierto ninguna,
# podemos cambiar aquí la ruta del SQLite para que apunte a NUESTRA COPIA.
from django.conf import settings

settings.DATABASES["default"]["NAME"] = sys.argv[1]

from django.core.management import call_command

# Tablas que NO se exportan:
#   - contenttypes / auth.permission : las regenera 'migrate' automáticamente
#   - admin.logentry                 : historial del panel de administración
#   - sessions                       : sesiones web (los usuarios vuelven a entrar)
#   - django_q.OrmQ / django_q.Schedule : cola interna del worker (vacía)
EXCLUDE = [
    "contenttypes",
    "auth.permission",
    "admin.logentry",
    "sessions",
    "django_q.OrmQ",
    "django_q.Schedule",
]

with open(sys.argv[2], "w", encoding="utf-8") as f:
    call_command("dumpdata", exclude=EXCLUDE, indent=2, stdout=f)

print(f"Dump creado en {sys.argv[2]}")
PYTHON

mv "$SNAP_DIR/datadump.json" "$DUMP_FILE"
rm -rf "$SNAP_DIR"
SNAP_DIR=""
echo "    -> $DUMP_FILE"

# =============================================================================
log "5/7 Importando el JSON en PostgreSQL..."
# =============================================================================
# 'run' crea un contenedor temporal: el entrypoint aplica las migraciones
# (ya hechas, no hace nada) y después ejecuta nuestro 'loaddata'.
# Si la BD destino no está vacía, loaddata falla por claves duplicadas y NO
# importa nada (la carga es transaccional).
if ! "${COMPOSE[@]}" run --rm -T \
    -e RUN_COLLECTSTATIC=0 \
    -v "$DUMP_FILE:/tmp/datadump.json:ro" \
    web uv run manage.py loaddata /tmp/datadump.json; then
    die "Fallo al importar. ¿La BD de destino NO estaba vacía? Usa --reset (destruye los datos actuales) o parte de una BD vacía."
fi

# =============================================================================
log "6/7 Copiando uploaded_files/ al volumen de producción..."
# =============================================================================
"${COMPOSE[@]}" cp uploaded_files/. web:/app/uploaded_files/

# =============================================================================
log "7/7 Verificando que cada tabla tiene las filas esperadas..."
# =============================================================================
# Contamos los objetos del JSON por modelo. En este proyecto la tabla SQL de
# cada modelo es simplemente "app_modelo" (p. ej. genaigrader.course ->
# genaigrader_course).
EXPECTED="$("${COMPOSE[@]}" run --rm --no-deps -T \
    -v "$DUMP_FILE:/tmp/datadump.json:ro" \
    --entrypoint uv \
    web run python - /tmp/datadump.json <<'PYTHON'
import json
import sys
from collections import Counter

with open(sys.argv[1], encoding="utf-8") as f:
    data = json.load(f)

counts = Counter(obj["model"].replace(".", "_") for obj in data)
for table, n in sorted(counts.items()):
    print(table, n)
PYTHON
)"

FAILURES=0
while read -r table expected; do
    actual="$("${COMPOSE[@]}" exec -T db sh -c \
        'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc "SELECT COUNT(*) FROM '"$table"'"')"
    if [ "$actual" = "$expected" ]; then
        printf '    OK     %-35s %s filas\n' "$table" "$expected"
    else
        printf '    FALLO  %-35s esperadas %s, encontradas %s\n' "$table" "$expected" "$actual"
        FAILURES=1
    fi
done <<< "$EXPECTED"

[ "$FAILURES" = "0" ] || die "La verificación ha encontrado diferencias (revisa la lista de arriba)."

# =============================================================================
log "¡Migración completada con éxito!"
# =============================================================================
cat <<EOF

Resumen:
  - Origen    : db.sqlite3 (se usó una copia temporal; el original sigue intacto)
  - Destino   : PostgreSQL del docker-compose de producción
  - Volcado   : datadump.json (contiene contraseñas cifradas y tokens; está en
                .gitignore. Guárdalo como backup o bórralo cuando verifiques)
  - Media     : uploaded_files/ copiado al volumen de producción

EOF
