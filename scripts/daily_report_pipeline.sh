#!/bin/bash
# Daily AI Art Report — Fetch 10 data sources, then build report.html
# Runs as no_agent=true cron job at 22:15 UTC
REPORT_DIR="${HERMES_DATA_DIR:-/tmp/hermes_report}"
mkdir -p "$REPORT_DIR"
cd "${HERMES_SCRIPT_DIR:-/home/ubuntu/.hermes/scripts}"

echo "📡 Daily Report Pipeline — $(date -u '+%Y-%m-%d %H:%M:%S UTC')"
FAILED=0

run_json_source() {
    local label="$1"
    local output="$2"
    shift 2

    local tmp="${output}.tmp.$$"
    rm -f "$tmp"

    local source_timeout="${HERMES_SOURCE_TIMEOUT:-120}"
    if timeout "$source_timeout" "$@" > "$tmp"; then
        :
    else
        echo "⚠️ $label failed (timeout=${source_timeout}s)" >&2
        rm -f "$tmp"
        FAILED=$((FAILED + 1))
        return 1
    fi

    if python3 -c "import json, sys; json.load(open(sys.argv[1], encoding='utf-8'))" "$tmp"; then
        mv "$tmp" "$output"
        return 0
    fi

    echo "⚠️ $label failed: invalid JSON" >&2
    rm -f "$tmp"
    FAILED=$((FAILED + 1))
    return 1
}

# ─── 1. SeaArt ───
echo "[1/10] SeaArt..."
run_json_source "SeaArt" "$REPORT_DIR/seaart.json" python3 seaart_trending.py

# ─── 2. Pixiv SFW ───
echo "[2/10] Pixiv SFW..."
run_json_source "Pixiv SFW" "$REPORT_DIR/pixiv_sfw.json" python3 pixiv_app.py day 10

# ─── 3. Pixiv R18 ───
echo "[3/10] Pixiv R18..."
run_json_source "Pixiv R18" "$REPORT_DIR/pixiv_r18.json" python3 pixiv_app.py day_r18 10

# ─── 4. Danbooru ───
echo "[4/10] Danbooru..."
run_json_source "Danbooru" "$REPORT_DIR/danbooru.json" python3 danbooru_trending.py 10 day

# ─── 5. CivitAI ───
echo "[5/10] CivitAI..."
run_json_source "CivitAI" "$REPORT_DIR/civitai.json" python3 civitai_trending.py 10

# ─── 6. PixAI ───
echo "[6/10] PixAI..."
run_json_source "PixAI" "$REPORT_DIR/pixai.json" python3 pixai_trending.py

# ─── 7. Blue Archive Pixiv ───
echo "[7/10] Blue Archive Pixiv..."
run_json_source "BA Pixiv" "$REPORT_DIR/ba_pixiv.json" python3 pixiv_search_ba.py 5

# ─── 8. Blue Archive PixAI ───
echo "[8/10] Blue Archive PixAI..."
run_json_source "BA PixAI" "$REPORT_DIR/ba_pixai.json" python3 pixai_ba.py 5

# ─── 9. Reddit ───
echo "[9/10] Reddit RSS..."
run_json_source "Reddit" "$REPORT_DIR/reddit.json" python3 reddit_rss.py

# ─── 10. HuggingFace ───
echo "[10/10] HuggingFace..."
run_json_source "HuggingFace" "$REPORT_DIR/hf_models.json" python3 hf_models_trending.py 8

echo ""
echo "📊 Fetched: $((10 - FAILED))/10 sources"

STATUS_FILE="$REPORT_DIR/source_status.json"
python3 -c "
import json, os, sys
status = {
    'pipeline_time': '$(date -u -Iseconds)',
    'total': 10,
    'failed': $FAILED,
    'sources': {}
}
# check which files exist
sources = ['seaart.json','pixiv_sfw.json','pixiv_r18.json','danbooru.json','civitai.json','pixai.json','ba_pixiv.json','ba_pixai.json','reddit.json','hf_models.json']
names = ['SeaArt','Pixiv SFW','Pixiv R18','Danbooru','CivitAI','PixAI','BA Pixiv','BA Pixai','Reddit','HuggingFace']
for s,n in zip(sources,names):
    path = os.path.join('$REPORT_DIR', s)
    ok = os.path.exists(path) and os.path.getsize(path) > 10
    status['sources'][n] = {
        'ok': ok,
        'file': s,
        'size': os.path.getsize(path) if os.path.exists(path) else 0,
    }
with open('$STATUS_FILE', 'w') as f:
    json.dump(status, f, indent=2)
"

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
