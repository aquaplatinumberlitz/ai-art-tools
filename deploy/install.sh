#!/bin/bash
set -euo pipefail

HERMES_REPO_URL="${HERMES_REPO_URL:-https://github.com/aquaplatinumberlitz/ai-art-tools.git}"
HERMES_REPO_DIR="${HERMES_REPO_DIR:-/tmp/ai-art-tools}"
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
HERMES_DRY_RUN="${HERMES_DRY_RUN:-0}"
HERMES_INTERACTIVE="${HERMES_INTERACTIVE:-0}"
HERMES_RUN_ONCE="${HERMES_RUN_ONCE:-0}"

ENV_FILE="$HERMES_HOME/.env"
ENV_EXAMPLE="$HERMES_REPO_DIR/.env.example"
VENV_DIR="$HERMES_REPO_DIR/.venv"
CRON_BEGIN="# BEGIN HERMES AI ART TOOLS"
CRON_END="# END HERMES AI ART TOOLS"
CRON_SCHEDULE="${HERMES_CRON_SCHEDULE:-15 22 * * *}"

ACCOUNT_KEYS=(
    PIXAI_EMAIL
    PIXAI_PASSWORD
    CIVITAI_API_TOKEN
    PIXIV_TOKEN_FILE
    PIXAI_STATE_FILE
)

REQUIRED_KEYS=(
    PIXAI_EMAIL
    PIXAI_PASSWORD
)

declare -A ACCOUNT_VALUES=()

log() {
    printf '[hermes-install] %s\n' "$*"
}

die() {
    printf '[hermes-install] ERROR: %s\n' "$*" >&2
    exit 1
}

run_cmd() {
    if [ "$HERMES_DRY_RUN" = "1" ]; then
        log "dry run: $*"
    else
        "$@"
    fi
}

is_known_key() {
    local key="$1"
    local known
    for known in "${ACCOUNT_KEYS[@]}"; do
        [ "$key" = "$known" ] && return 0
    done
    return 1
}

shell_quote() {
    printf "%q" "$1"
}

clone_or_update_repo() {
    if [ -d "$HERMES_REPO_DIR/.git" ]; then
        log "repository found: $HERMES_REPO_DIR"
        run_cmd git -C "$HERMES_REPO_DIR" pull --ff-only
    elif [ -e "$HERMES_REPO_DIR" ]; then
        die "$HERMES_REPO_DIR exists but is not a git repository"
    else
        log "repository missing; cloning into $HERMES_REPO_DIR"
        run_cmd git clone "$HERMES_REPO_URL" "$HERMES_REPO_DIR"
    fi
}

create_venv_and_install() {
    log "setting up Python virtual environment"
    run_cmd python3 -m venv "$VENV_DIR"
    run_cmd "$VENV_DIR/bin/python" -m pip install --upgrade pip
    run_cmd "$VENV_DIR/bin/python" -m pip install -r "$HERMES_REPO_DIR/requirements.txt"
    run_cmd "$VENV_DIR/bin/python" -m playwright install chromium
}

create_hermes_dirs() {
    log "creating Hermes directories"
    run_cmd mkdir -p \
        "$HERMES_HOME" \
        "$HERMES_HOME/logs" \
        "$HERMES_HOME/cron" \
        "$HERMES_HOME/cron/images" \
        "$HERMES_HOME/scripts" \
        "$HERMES_HOME/accounts" \
        "$HERMES_HOME/references" \
        "/tmp/hermes_report"
}

find_account_file() {
    local candidates=()
    if [ -n "${HERMES_ACCOUNTS_FILE:-}" ]; then
        candidates+=("$HERMES_ACCOUNTS_FILE")
    fi
    candidates+=(
        "$HERMES_HOME/accounts.md"
        "$HERMES_HOME/accounts/accounts.md"
        "$HERMES_HOME/references/accounts.md"
        "$HOME/.hermes/accounts.md"
        "$HOME/.hermes/references/accounts.md"
    )

    local candidate
    for candidate in "${candidates[@]}"; do
        if [ -f "$candidate" ]; then
            printf '%s\n' "$candidate"
            return 0
        fi
    done
    return 1
}

parse_account_file() {
    local account_file="$1"
    local line key value

    log "account file: $account_file"
    run_cmd chmod 600 "$account_file"

    while IFS= read -r line || [ -n "$line" ]; do
        line="${line#"${line%%[![:space:]]*}"}"
        line="${line%"${line##*[![:space:]]}"}"
        [[ "$line" =~ ^[A-Za-z_][A-Za-z0-9_]*= ]] || continue
        key="${line%%=*}"
        is_known_key "$key" || continue
        value="${line#*=}"
        value="${value%%#*}"
        value="${value%"${value##*[![:space:]]}"}"
        if [[ "$value" =~ ^\".*\"$ ]] || [[ "$value" =~ ^\'.*\'$ ]]; then
            value="${value:1:${#value}-2}"
        fi
        [ -n "$value" ] || continue
        ACCOUNT_VALUES["$key"]="$value"
    done < "$account_file"

    for key in "${ACCOUNT_KEYS[@]}"; do
        if [ -n "${ACCOUNT_VALUES[$key]:-}" ]; then
            log "$key: found"
        else
            log "$key: missing"
        fi
    done
}

env_has_nonempty_key() {
    local key="$1"
    [ -f "$ENV_FILE" ] || return 1
    grep -Eq "^[[:space:]]*${key}=.+" "$ENV_FILE"
}

env_has_key() {
    local key="$1"
    [ -f "$ENV_FILE" ] || return 1
    grep -Eq "^[[:space:]]*${key}=" "$ENV_FILE"
}

set_env_key() {
    local key="$1"
    local value="$2"
    local quoted
    quoted="$(shell_quote "$value")"

    if env_has_nonempty_key "$key"; then
        return 0
    fi

    if [ "$HERMES_DRY_RUN" = "1" ]; then
        if env_has_key "$key"; then
            log "dry run: would fill $key in $ENV_FILE"
        else
            log "dry run: would append $key to $ENV_FILE"
        fi
        return 0
    fi

    if env_has_key "$key"; then
        sed -i "s|^[[:space:]]*${key}=.*|${key}=${quoted}|" "$ENV_FILE"
    else
        printf '\n%s=%s\n' "$key" "$quoted" >> "$ENV_FILE"
    fi
}

force_set_env_key() {
    local key="$1"
    local value="$2"
    local quoted
    quoted="$(shell_quote "$value")"

    if [ "$HERMES_DRY_RUN" = "1" ]; then
        log "dry run: would set $key in $ENV_FILE"
        return 0
    fi

    if env_has_key "$key"; then
        sed -i "s|^[[:space:]]*${key}=.*|${key}=${quoted}|" "$ENV_FILE"
    else
        printf '\n%s=%s\n' "$key" "$quoted" >> "$ENV_FILE"
    fi
}

generate_env() {
    local timestamp key created_env=0

    [ -f "$ENV_EXAMPLE" ] || die "missing $ENV_EXAMPLE"

    if [ -f "$ENV_FILE" ]; then
        timestamp="$(date -u '+%Y%m%d%H%M%S')"
        log ".env exists; backing up before adding missing values"
        run_cmd cp "$ENV_FILE" "$ENV_FILE.bak.$timestamp"
    else
        log "creating $ENV_FILE from .env.example"
        run_cmd cp "$ENV_EXAMPLE" "$ENV_FILE"
        created_env=1
    fi

    if [ "$created_env" -eq 1 ]; then
        force_set_env_key HERMES_REPORT_DIR "$HERMES_HOME/cron"
        force_set_env_key HERMES_IMAGE_DIR "$HERMES_HOME/cron/images"
    fi
    force_set_env_key HERMES_SCRIPT_DIR "$HERMES_REPO_DIR/scripts"

    for key in "${ACCOUNT_KEYS[@]}"; do
        if [ -n "${ACCOUNT_VALUES[$key]:-}" ]; then
            set_env_key "$key" "${ACCOUNT_VALUES[$key]}"
        fi
    done

    run_cmd chmod 600 "$ENV_FILE"
}

get_missing_keys() {
    local key missing=()
    for key in "${REQUIRED_KEYS[@]}"; do
        if env_has_nonempty_key "$key" || [ -n "${ACCOUNT_VALUES[$key]:-}" ]; then
            continue
        else
            missing+=("$key")
        fi
    done
    [ "${#missing[@]}" -gt 0 ] && printf '%s\n' "${missing[@]}"
}

prompt_interactive() {
    local missing key value
    mapfile -t missing < <(get_missing_keys)
    [ "${#missing[@]}" -gt 0 ] || return 0

    if [ "$HERMES_INTERACTIVE" != "1" ]; then
        return 0
    fi

    for key in "${missing[@]}"; do
        if [[ "$key" == *PASSWORD* || "$key" == *TOKEN* ]]; then
            read -r -s -p "Enter $key: " value
            printf '\n'
        else
            read -r -p "Enter $key: " value
        fi
        [ -n "$value" ] || continue
        ACCOUNT_VALUES["$key"]="$value"
        set_env_key "$key" "$value"
    done
    run_cmd chmod 600 "$ENV_FILE"
}

print_missing_instructions() {
    local missing key
    mapfile -t missing < <(get_missing_keys)
    if [ "${#missing[@]}" -eq 0 ]; then
        log "required credentials: found"
        return 0
    fi

    log "missing required credentials:"
    for key in "${missing[@]}"; do
        log "  $key"
    done
    log "Add missing values to $ENV_FILE or an account markdown file, then rerun."
    log "Use HERMES_INTERACTIVE=1 bash deploy/install.sh to prompt for missing values."
}

install_secret_file() {
    local source_file="$1"
    local dest_file="$2"

    run_cmd mkdir -p "$(dirname "$dest_file")"
    if [ -e "$dest_file" ] && [ "$source_file" -ef "$dest_file" ]; then
        run_cmd chmod 600 "$dest_file"
        return 0
    fi
    run_cmd cp "$source_file" "$dest_file"
    run_cmd chmod 600 "$dest_file"
}

setup_token_files() {
    local pixiv_token pixai_state
    pixiv_token="${ACCOUNT_VALUES[PIXIV_TOKEN_FILE]:-}"
    pixai_state="${ACCOUNT_VALUES[PIXAI_STATE_FILE]:-}"

    if [ -n "$pixiv_token" ]; then
        if [ -f "$pixiv_token" ]; then
            log "Pixiv token file: found"
            install_secret_file "$pixiv_token" "$HERMES_REPO_DIR/scripts/.pixiv_token.json"
        else
            log "Pixiv token file: missing at configured path"
        fi
    else
        log "Pixiv token file: missing"
    fi

    if [ -n "$pixai_state" ]; then
        if [ -f "$pixai_state" ]; then
            log "PixAI state file: found"
            install_secret_file "$pixai_state" "$HERMES_HOME/scripts/.pixai_state.json"
        else
            log "PixAI state file: missing at configured path"
        fi
    else
        log "PixAI state file: missing"
    fi
}

setup_cron() {
    local cron_tmp command quoted_repo quoted_env quoted_log block
    quoted_repo="$(shell_quote "$HERMES_REPO_DIR")"
    quoted_env="$(shell_quote "$ENV_FILE")"
    quoted_log="$(shell_quote "$HERMES_HOME/logs/pipeline.log")"
    command="cd $quoted_repo && set -a && . $quoted_env && set +a && bash scripts/daily_report_pipeline.sh >> $quoted_log 2>&1"
    block="${CRON_BEGIN}
${CRON_SCHEDULE} ${command}
${CRON_END}"

    log "installing cron block"
    if [ "$HERMES_DRY_RUN" = "1" ]; then
        log "dry run: would install cron block:"
        printf '%s\n' "$block"
        return 0
    fi

    cron_tmp="$(mktemp)"
    (crontab -l 2>/dev/null || true) | awk -v begin="$CRON_BEGIN" -v end="$CRON_END" '
        $0 == begin {skip=1; next}
        $0 == end {skip=0; next}
        skip != 1 {print}
    ' > "$cron_tmp"
    {
        cat "$cron_tmp"
        printf '%s\n' "$block"
    } | crontab -
    rm -f "$cron_tmp"
}

verify_syntax() {
    log "running syntax checks"
    run_cmd bash -n "$HERMES_REPO_DIR/deploy/install.sh"
    run_cmd bash -n "$HERMES_REPO_DIR/scripts/daily_report_pipeline.sh"
    run_cmd "$VENV_DIR/bin/python" -m py_compile "$HERMES_REPO_DIR"/scripts/*.py
}

run_pipeline_once() {
    if [ "$HERMES_RUN_ONCE" != "1" ]; then
        log "skipping one-time pipeline run (set HERMES_RUN_ONCE=1 to enable)"
        return 0
    fi

    log "running pipeline once"
    if [ "$HERMES_DRY_RUN" = "1" ]; then
        log "dry run: would run pipeline once"
        return 0
    fi

    (
        cd "$HERMES_REPO_DIR"
        set -a
        # shellcheck disable=SC1090
        . "$ENV_FILE"
        set +a
        bash scripts/daily_report_pipeline.sh
    )
}

main() {
    local account_file=""

    clone_or_update_repo
    create_venv_and_install
    create_hermes_dirs

    if account_file="$(find_account_file)"; then
        parse_account_file "$account_file"
    else
        log "account file: missing"
        for key in "${ACCOUNT_KEYS[@]}"; do
            log "$key: missing"
        done
    fi

    generate_env
    prompt_interactive
    print_missing_instructions
    setup_token_files
    setup_cron
    verify_syntax
    run_pipeline_once

    log "done"
}

main "$@"
