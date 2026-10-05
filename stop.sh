#!/bin/bash
# Stop MFDataIndia services

set -e

cd "$(dirname "$0")"

echo "🛑 Stopping MFDataIndia services..."

# Stop API
echo "🚀 Stopping API server..."
pkill -f uvicorn 2>/dev/null || echo "   No API processes found"

# Stop PostgreSQL container
echo "🐘 Stopping PostgreSQL container..."
podman stop mf-postgres-prod 2>/dev/null || echo "   PostgreSQL container not running"

echo "✅ Services stopped"
echo ""
echo "📋 To start again: ./start.sh"