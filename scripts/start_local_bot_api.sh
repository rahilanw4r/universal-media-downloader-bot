#!/usr/bin/env bash
# ====================================================================
# Launch Telegram Local Bot API Server (2,000 MB / 2 GB Uploads)
# ====================================================================
set -e

# Load credentials from .env if present
if [ -f .env ]; then
    # Read variables safely
    while IFS='=' read -r key val || [ -n "$key" ]; do
        key=$(echo "$key" | tr -d ' \r\t')
        val=$(echo "$val" | tr -d '\r')
        if [[ ! "$key" =~ ^# ]] && [ -n "$key" ]; then
            export "$key=$val" 2>/dev/null || true
        fi
    done < .env
fi

API_ID="${TELEGRAM_API_ID:-$1}"
API_HASH="${TELEGRAM_API_HASH:-$2}"
HTTP_PORT="${LOCAL_BOT_API_PORT:-8081}"

if [ -z "$API_ID" ] || [ "$API_ID" = "your_api_id_here" ] || [ -z "$API_HASH" ] || [ "$API_HASH" = "your_api_hash_here" ]; then
    echo "❌ Error: TELEGRAM_API_ID and TELEGRAM_API_HASH must be configured in .env or passed as arguments."
    echo "Usage: ./scripts/start_local_bot_api.sh [API_ID] [API_HASH]"
    exit 1
fi

echo "🚀 Starting Telegram Local Bot API Server..."

# Check if Docker is installed
if command -v docker >/dev/null 2>&1; then
    echo "🐳 Found Docker. Running telegram-bot-api container..."
    docker stop telegram-bot-api 2>/dev/null || true
    docker rm telegram-bot-api 2>/dev/null || true
    docker run -d \
        --name telegram-bot-api \
        --restart always \
        -p ${HTTP_PORT}:8081 \
        -e TELEGRAM_API_ID="${API_ID}" \
        -e TELEGRAM_API_HASH="${API_HASH}" \
        -e TELEGRAM_LOCAL=1 \
        -v telegram-bot-api-data:/var/lib/telegram-bot-api \
        aiogram/telegram-bot-api:latest \
        --local --http-port 8081
    echo "✅ Local Bot API Server is running on http://localhost:${HTTP_PORT}"
    echo "📊 You can check logs with: docker logs -f telegram-bot-api"
else
    echo "⚠️ Docker is not installed on your system."
    echo ""
    echo "To install Docker on Linux Mint / Ubuntu, run:"
    echo "   sudo apt update && sudo apt install -y docker.io"
    echo "   sudo usermod -aG docker \$USER"
    echo ""
    echo "Then re-run this script: bash scripts/start_local_bot_api.sh"
    exit 1
fi

