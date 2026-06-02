#!/bin/bash
# Daily AI Art Report — Fetch 10 data sources, then build report.html
# Runs as no_agent=true cron job at 22:15 UTC
REPORT_DIR="${HERMES_DATA_DIR:-/tmp/hermes_report}"
HERMES_REPO_DIR="${HERMES_REPO_DIR:-/home/ubuntu/.hermes/ai-art-tools}"
HERMES_SCRIPT_DIR="$HERMES_REPO_DIR/scripts"
export PIXIV_TOKEN_FILE="$HERMES_SCRIPT_DIR/.pixiv_token.json"
mkdir -p "$REPORT_DIR"

required_scripts=(
    seaart_trending.py
    pixiv_app.py
    danbooru_trending.py
    civitai_trending.py
    pixai_trending.py
    pixiv_search_ba.py
    pixai_ba.py
    reddit_rss.py
    hf_models_trending.py
    build_report_canonical.py
)

if [ ! -d "$HERMES_SCRIPT_DIR" ]; then
    echo "❌ HERMES_SCRIPT_DIR does not exist: $HERMES_SCRIPT_DIR" >&2
    exit 1
fi

for script in "${required_scripts[@]}"; do
    if [ ! -f "$HERMES_SCRIPT_DIR/$script" ]; then
        echo "❌ Required script missing: $HERMES_SCRIPT_DIR/$script" >&2
        exit 1
    fi
done

cd "$HERMES_SCRIPT_DIR"
export HERMES_REPO_DIR HERMES_SCRIPT_DIR

echo "📡 Daily Report Pipeline — $(date -u '+%Y-%m-%d %H:%M:%S UTC')"
FAILED=0
STATUS_FILE="$REPORT_DIR/source_status.json"
export STATUS_FILE

update_source_status() {
    python3 -c '
import json, os, sys
from datetime import datetime, timezone

label, json_file, tmp_file, duration, success, item_count, error_msg = sys.argv[1:8]
quality = sys.argv[8] if len(sys.argv) > 8 else None
warning = sys.argv[9] if len(sys.argv) > 9 else None
status_file = os.environ["STATUS_FILE"]
now_dt = datetime.now(timezone.utc)
now = now_dt.isoformat()

try:
    duration_sec = float(duration)
except Exception:
    duration_sec = 0.0
success = success == "1"
try:
    item_count = int(item_count)
except Exception:
    item_count = 0
error_msg = (error_msg or "").strip() or None
if error_msg and len(error_msg) > 240:
    error_msg = error_msg[:237] + "..."

status = {"generated_at": now, "sources": {}}
known_sources = {
    "SeaArt", "Pixiv SFW", "Pixiv R18", "Danbooru", "CivitAI", "PixAI",
    "BA Pixiv", "BA Pixai", "Reddit", "HuggingFace",
}
if os.path.exists(status_file):
    try:
        with open(status_file, "r", encoding="utf-8") as f:
            prev = json.load(f)
        if isinstance(prev, dict):
            status["sources"] = prev.get("sources", {}) if isinstance(prev.get("sources"), dict) else {}
            status["sources"] = {k: v for k, v in status["sources"].items() if k in known_sources}
    except Exception:
        pass

prev_entry = status["sources"].get(label, {})
last_success_at = prev_entry.get("last_success_at") if isinstance(prev_entry, dict) else None
fallback_exists = os.path.exists(json_file) and os.path.getsize(json_file) > 10
stale = (not success) and fallback_exists
missing = (not success) and (not fallback_exists)

if success:
    last_success_at = now
elif stale:
    try:
        with open(json_file, "r", encoding="utf-8") as f:
            item_count = len(json.load(f))
    except Exception:
        item_count = 0
        stale = False
        missing = True

age_sec = 0
if last_success_at:
    try:
        last_dt = datetime.fromisoformat(last_success_at.replace("Z", "+00:00"))
        age_sec = max(0, int((now_dt - last_dt).total_seconds()))
    except Exception:
        age_sec = 0

status["generated_at"] = now
status["sources"][label] = {
    "ok": success,
    "stale": stale,
    "used_fallback": stale,
    "missing": missing,
    "items": item_count,
    "duration_sec": duration_sec,
    "error": None if success else error_msg,
    "quality": quality,
    "warning": warning,
    "json_path": json_file,
    "updated_at": now,
    "last_success_at": last_success_at,
    "age_sec": age_sec,
}

tmp_status = status_file + ".tmp"
with open(tmp_status, "w", encoding="utf-8") as f:
    json.dump(status, f, indent=2, ensure_ascii=False)
    f.write("\n")
os.replace(tmp_status, status_file)
' "$@"
}

run_json_source() {
    local label="$1"
    local output="$2"
    shift 2

    local tmp="${output}.tmp.$$"
    local err="${output}.err.$$"
    local json_err="${output}.json.err.$$"
    rm -f "$tmp"
    rm -f "$err"
    rm -f "$json_err"

    local source_timeout="${HERMES_SOURCE_TIMEOUT:-180}"
    local start=$SECONDS
    local rc=0
    local duration=0
    local item_count=0
    local error_msg=""
    local quality=""
    local warning=""

    timeout "$source_timeout" "$@" > "$tmp" 2> "$err"
    rc=$?
    duration=$((SECONDS - start))

    if [ "$rc" -eq 0 ]; then
        if item_count=$(python3 -c "import json, sys; print(len(json.load(open(sys.argv[1], encoding='utf-8'))))" "$tmp" 2> "$json_err"); then
            if mv "$tmp" "$output"; then
                if [ "$label" = "SeaArt" ]; then
                    if grep -q "content_changed=true" "$err"; then
                        quality="hot_week"
                    elif grep -q "content_changed=false" "$err"; then
                        quality="default_feed"
                        warning="filter did not change content; using default feed"
                    fi
                fi
                if ! update_source_status "$label" "$output" "$tmp" "$duration" "1" "$item_count" "" "$quality" "$warning"; then
                    echo "⚠️ $label status update failed" >&2
                fi
                rm -f "$err"
                rm -f "$json_err"
                return 0
            fi
            error_msg="could not replace JSON output"
        else
            error_msg="invalid JSON: $(tr '\n' ' ' < "$json_err" | cut -c1-180)"
        fi
    elif [ "$rc" -eq 124 ] || [ "$rc" -eq 137 ]; then
        error_msg="timeout after ${source_timeout}s"
    else
        error_msg="$(tr '\n' ' ' < "$err" | cut -c1-180)"
        [ -n "$error_msg" ] || error_msg="exit code $rc"
    fi

    if [ -s "$output" ] && [ "$(wc -c < "$output")" -gt 10 ]; then
        echo "⚠️ $label failed: $error_msg; using stale fallback" >&2
    else
        echo "⚠️ $label failed: $error_msg" >&2
    fi

    if ! update_source_status "$label" "$output" "$tmp" "$duration" "0" "0" "$error_msg" "default_feed" "filter not applied; using default feed"; then
        echo "⚠️ $label status update failed" >&2
    fi
    rm -f "$tmp"
    rm -f "$err"
    rm -f "$json_err"
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
run_json_source "BA Pixai" "$REPORT_DIR/ba_pixai.json" python3 pixai_ba.py 5

# ─── 9. Reddit ───
echo "[9/10] Reddit RSS..."
run_json_source "Reddit" "$REPORT_DIR/reddit.json" python3 reddit_rss.py

# ─── 10. HuggingFace ───
echo "[10/10] HuggingFace..."
run_json_source "HuggingFace" "$REPORT_DIR/hf_models.json" python3 hf_models_trending.py 8

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
