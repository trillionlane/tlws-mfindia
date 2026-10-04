#!/bin/bash
# Start MFDataIndia with PostgreSQL backend

set -e

cd "$(dirname "$0")"

echo "🚀 Starting MFDataIndia with PostgreSQL..."

# Start PostgreSQL. Prefer the existing container (its data is in the container
# filesystem, so a plain stop/start preserves the migrated data).
if ! podman ps --filter name=mf-postgres-prod --format '{{.Names}}' | grep -q mf-postgres-prod; then
    if podman ps -a --filter name=mf-postgres-prod --format '{{.Names}}' | grep -q mf-postgres-prod; then
        echo "🐘 Starting existing PostgreSQL container..."
        podman start mf-postgres-prod || {
            echo "❌ Failed to start PostgreSQL container"
            exit 1
        }
    else
        echo "⚠️  PostgreSQL container is missing — creating a fresh one (postgres:18)."
        echo "   NOTE: a brand-new container has NO data. If your previous one was"
        echo "   removed, re-run the PGlite→Postgres migration to restore it."
        podman run --name mf-postgres-prod \
            -e POSTGRES_PASSWORD=secret -e POSTGRES_DB=mfdataindia \
            -p 5432:5432 -d postgres:18 || {
            echo "❌ Failed to create PostgreSQL container"
            exit 1
        }
    fi
    sleep 5
fi

# Wait for PostgreSQL to be ready
echo "⏳ Waiting for PostgreSQL to be ready..."
for i in {1..30}; do
    if podman exec mf-postgres-prod pg_isready -U postgres -d mfdataindia >/dev/null 2>&1; then
        echo "✅ PostgreSQL is ready"
        break
    fi
    echo "⏳ Still waiting... ($i/30)"
    sleep 2
done

# Start API
echo "🚀 Starting API server..."
MFDATAINDIA_DSN="postgresql://postgres:secret@localhost:5432/mfdataindia" \
PYTHONPATH=src nohup python3 -m uvicorn mfdataindia.api.app:create_app --factory --host 127.0.0.1 --port 8000 > /tmp/mfdataindia-api.log 2>&1 &

# Wait for API to start
sleep 3

# Check if it's running
if curl -s http://127.0.0.1:8000/api/health >/dev/null 2>&1; then
    echo "✅ API server is running"
    echo ""
    echo "🎉 MFDataIndia is ready!"
    echo "🌐 Visit: http://127.0.0.1:8000"
    echo "📊 API docs: http://127.0.0.1:8000/docs"
    echo ""
    echo "📋 To stop services:"
    echo "   pkill -f uvicorn"
    echo "   podman stop mf-postgres-prod"
else
    echo "❌ API server failed to start"
    echo "📝 Check logs: tail -f /tmp/mfdataindia-api.log"
    exit 1
fi