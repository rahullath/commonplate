#!/bin/sh
set -e

# Fly.io mounts a persistent volume at /data (configured in fly.toml).
# Symlink /app/data → /data so SQLite + menu.json survive restarts.
if [ -d "/data" ] && [ ! -L "/app/data" ]; then
    rm -rf /app/data
    ln -s /data /app/data
fi

mkdir -p /app/data

# menu.json is repo-managed — scripts/build_menu.py and scripts/merge_petpooja.py
# are the only writers, nothing touches it at runtime. So always sync the bundled
# copy over the volume's, or the first boot's copy never updates (new items and
# half_price fields silently go stale on every future release).
cp /app/menu.json.bundled /app/data/menu.json

# One-off order cleanup, driven by env vars so it can be done without a shell.
#   PURGE_ORDERS_KEEP=46            -> dry run: logs what would be deleted, changes nothing
#   PURGE_ORDERS_KEEP=46 PURGE_ORDERS_APPLY=1 -> backs up the DB, deletes, then drops a
#                                    marker on the volume so a restart never repeats it.
# Remove both variables once done. Failures never block the app from starting.
if [ -n "$PURGE_ORDERS_KEEP" ]; then
    MARKER="/app/data/.purged-orders-$(echo "$PURGE_ORDERS_KEEP" | tr ' ,' '__')-latest${PURGE_ORDERS_KEEP_LATEST:-0}"
    if [ "$PURGE_ORDERS_APPLY" = "1" ] && [ ! -f "$MARKER" ]; then
        echo "[purge] APPLY, keeping order(s): $PURGE_ORDERS_KEEP"
        python scripts/purge_orders.py --keep $(echo "$PURGE_ORDERS_KEEP" | tr ',' ' ') --keep-latest "${PURGE_ORDERS_KEEP_LATEST:-0}" --apply && touch "$MARKER" || echo "[purge] FAILED, nothing marked done"
    elif [ -f "$MARKER" ]; then
        echo "[purge] already applied for $PURGE_ORDERS_KEEP (marker present), skipping"
    else
        echo "[purge] DRY RUN, keeping order(s): $PURGE_ORDERS_KEEP"
        python scripts/purge_orders.py --keep $(echo "$PURGE_ORDERS_KEEP" | tr ',' ' ') --keep-latest "${PURGE_ORDERS_KEEP_LATEST:-0}" || echo "[purge] dry run failed"
    fi
fi

exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"
