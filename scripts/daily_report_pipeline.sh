#!/bin/bash
# Daily AI Art Report — Fetch 10 data sources, then build report.html
# Runs as no_agent=true cron job at 22:15 UTC
set -e
REPORT_DIR="/tmp/hermes_report"
mkdir -p "$REPORT_DIR"
cd /home/ubuntu/.hermes/scripts

echo "📡 Daily Report Pipeline — $(date -u '+%Y-%m-%d %H:%M:%S UTC')"
FAILED=0

# ─── 1. SeaArt ───
echo "[1/10] SeaArt..."
python3 seaart_trending.py > "$REPORT_DIR/seaart.json" || { echo "⚠️ SeaArt failed" >&2; ((FAILED++)); }

# ─── 2. Pixiv SFW ───
echo "[2/10] Pixiv SFW..."
python3 pixiv_app.py day 10 > "$REPORT_DIR/pixiv_sfw.json" || { echo "⚠️ Pixiv SFW failed" >&2; ((FAILED++)); }

# ─── 3. Pixiv R18 ───
echo "[3/10] Pixiv R18..."
python3 pixiv_app.py day_r18 10 > "$REPORT_DIR/pixiv_r18.json" || { echo "⚠️ Pixiv R18 failed" >&2; ((FAILED++)); }

# ─── 4. Danbooru ───
echo "[4/10] Danbooru..."
python3 danbooru_trending.py 10 day > "$REPORT_DIR/danbooru.json" || { echo "⚠️ Danbooru failed" >&2; ((FAILED++)); }

# ─── 5. CivitAI ───
echo "[5/10] CivitAI..."
python3 civitai_trending.py 10 > "$REPORT_DIR/civitai.json" || { echo "⚠️ CivitAI failed" >&2; ((FAILED++)); }

# ─── 6. PixAI ───
echo "[6/10] PixAI..."
python3 pixai_trending.py > "$REPORT_DIR/pixai.json" || { echo "⚠️ PixAI failed" >&2; ((FAILED++)); }

# ─── 7. Blue Archive Pixiv ───
echo "[7/10] Blue Archive Pixiv..."
python3 pixiv_search_ba.py 5 > "$REPORT_DIR/ba_pixiv.json" || { echo "⚠️ BA Pixiv failed" >&2; ((FAILED++)); }

# ─── 8. Blue Archive PixAI ───
echo "[8/10] Blue Archive PixAI..."
python3 pixai_ba.py 5 > "$REPORT_DIR/ba_pixai.json" || { echo "⚠️ BA PixAI failed" >&2; ((FAILED++)); }

# ─── 9. Reddit ───
echo "[9/10] Reddit RSS..."
python3 reddit_rss.py > "$REPORT_DIR/reddit.json" || { echo "⚠️ Reddit failed" >&2; ((FAILED++)); }

# ─── 10. HuggingFace ───
echo "[10/10] HuggingFace..."
python3 hf_models_trending.py 8 > "$REPORT_DIR/hf_models.json" || { echo "⚠️ HuggingFace failed" >&2; ((FAILED++)); }

echo ""
echo "📊 Fetched: $((10 - FAILED))/10 sources"

# ─── BUILD ───
echo ""
echo "🔧 Building report.html..."
if python3 build_report_canonical.py; then
    echo "✅ http://150.230.56.153:8002/report.html"
else
    echo "❌ Build failed" >&2
    exit 1
fi

[ "$FAILED" -gt 0 ] && echo "⚠️ $FAILED source(s) failed"
exit 0
