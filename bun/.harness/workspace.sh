#!/bin/bash
set -e -o pipefail

case ${BASH_SOURCE[0]} in
  */*) _workspace_script_dir=${BASH_SOURCE[0]%/*} ;;
  *) _workspace_script_dir=. ;;
esac
SCRIPT_DIR=$(cd "$_workspace_script_dir" && pwd -P)
ROOT=${HARNESS_WORKSPACE_ROOT:-$(cd "$SCRIPT_DIR/.." && pwd -P)}
LOCK=${HARNESS_WORKSPACE_LOCK:-$SCRIPT_DIR/workspace.lock}
HOME_VALUE=${HOME:-}
TOOLS_ROOT=${HARNESS_WORKSPACE_TOOLS:-${HOME_VALUE:+$HOME_VALUE/.local/share/harness/tools}}
CACHE_ROOT=${HARNESS_WORKSPACE_CACHE_ROOT:-${HOME_VALUE:+$HOME_VALUE/.cache/harness}}
OFFLINE=${OFFLINE:-0}
MANIFEST_FORMAT=

TOOL_PROFILES=()
TOOL_NAMES=()
TOOL_VERSIONS=()
TOOL_KINDS=()
TOOL_PATHS=()
TOOL_PROBES=()
TOOL_EXPECTED=()
TOOL_SOURCES=()

PIN_PROFILES=()
PIN_NAMES=()
PIN_VERSIONS=()
PIN_KINDS=()
PIN_PATHS=()
PIN_PROBES=()
PIN_EXPECTED=()
PIN_SOURCES=()

ARTIFACT_NAMES=()
ARTIFACT_KEYS=()
ARTIFACT_PLATFORMS=()
ARTIFACT_URLS=()
ARTIFACT_SHAS=()
ARTIFACT_ARCHIVES=()
ARTIFACT_PAYLOADS=()

INPUT_PROFILES=()
INPUT_ECOSYSTEMS=()
INPUT_PATHS=()
INPUT_SHAS=()

SELECTED_PROFILES=()
PLATFORM=
PLATFORM_ERROR=
PREFLIGHT_ERRORS=()
HOOK_PRE_COMMIT=
HOOK_PRE_PUSH=
CLEANUP_PATHS=()
ACTIVE_REPLACEMENT_DESTINATION=
ACTIVE_REPLACEMENT_BACKUP=
ACTIVE_REPLACEMENT_PUBLISHED=0
CACHED_ARTIFACT_PATH=

say() {
  printf '%s\n' "$*"
}

die() {
  printf 'workspace: %s\n' "$*" >&2
  exit 1
}

manifest_error() {
  printf 'workspace: lock line %s: %s\n' "$1" "$2" >&2
  return 1
}

is_supported_profile() {
  case $1 in
    common|python|bun|go|rust) return 0 ;;
    *) return 1 ;;
  esac
}

is_safe_relative_path() {
  local value=$1 part
  [ -n "$value" ] || return 1
  case $value in
    /*|*\\*|*//*|.|..|../*|*/../*|*/..|./*|*/./*|*/.) return 1 ;;
  esac
  local IFS=,
  local parts=()
  read -r -a parts <<<"$value"
  [ "${#parts[@]}" -gt 0 ] || return 1
  for part in "${parts[@]}"; do
    [ -n "$part" ] || return 1
    case /$part/ in
      */../*|*/./*) return 1 ;;
    esac
  done
}

record_name_exists() {
  local wanted=$1 item
  for item in "${TOOL_NAMES[@]}" "${PIN_NAMES[@]}"; do
    [ "$item" = "$wanted" ] && return 0
  done
  return 1
}

artifact_exists() {
  local wanted_name=$1 wanted_key=$2 wanted_platform=$3 i
  for ((i = 0; i < ${#ARTIFACT_NAMES[@]}; i += 1)); do
    if [ "${ARTIFACT_NAMES[$i]}" = "$wanted_name" ] && \
      [ "${ARTIFACT_KEYS[$i]}" = "$wanted_key" ] && \
      [ "${ARTIFACT_PLATFORMS[$i]}" = "$wanted_platform" ]; then
      return 0
    fi
  done
  return 1
}

input_exists() {
  local wanted_profile=$1 wanted_ecosystem=$2 wanted_path=$3 i
  for ((i = 0; i < ${#INPUT_PROFILES[@]}; i += 1)); do
    if [ "${INPUT_PROFILES[$i]}" = "$wanted_profile" ] && \
      [ "${INPUT_ECOSYSTEMS[$i]}" = "$wanted_ecosystem" ] && \
      [ "${INPUT_PATHS[$i]}" = "$wanted_path" ]; then
      return 0
    fi
  done
  return 1
}

validate_version() {
  [[ $1 =~ ^[0-9]+([.][0-9]+)*([+_-][0-9A-Za-z.-]+)?$ ]]
}

validate_sha256() {
  [ "${#1}" -eq 64 ] || return 1
  case $1 in
    *[!0-9a-f]*) return 1 ;;
    *) return 0 ;;
  esac
}

reset_manifest() {
  TOOL_PROFILES=()
  TOOL_NAMES=()
  TOOL_VERSIONS=()
  TOOL_KINDS=()
  TOOL_PATHS=()
  TOOL_PROBES=()
  TOOL_EXPECTED=()
  TOOL_SOURCES=()
  PIN_PROFILES=()
  PIN_NAMES=()
  PIN_VERSIONS=()
  PIN_KINDS=()
  PIN_PATHS=()
  PIN_PROBES=()
  PIN_EXPECTED=()
  PIN_SOURCES=()
  ARTIFACT_NAMES=()
  ARTIFACT_KEYS=()
  ARTIFACT_PLATFORMS=()
  ARTIFACT_URLS=()
  ARTIFACT_SHAS=()
  ARTIFACT_ARCHIVES=()
  ARTIFACT_PAYLOADS=()
  INPUT_PROFILES=()
  INPUT_ECOSYSTEMS=()
  INPUT_PATHS=()
  INPUT_SHAS=()
}

read_manifest_format() {
  local line line_no=0 count=0 fields_count record
  local fields=()
  MANIFEST_FORMAT=
  while IFS= read -r line || [ -n "$line" ]; do
    line_no=$((line_no + 1))
    case $line in
      ''|'#'*) continue ;;
    esac
    fields=()
    IFS=$'\t' read -r -a fields <<<"$line"
    fields_count=${#fields[@]}
    record=${fields[0]-}
    [ "$record" = format ] || continue
    [ "$fields_count" -eq 2 ] || manifest_error "$line_no" "malformed format record" || return 1
    case ${fields[1]} in
      1|2) ;;
      *) manifest_error "$line_no" "unsupported lock format '${fields[1]}'" || return 1 ;;
    esac
    MANIFEST_FORMAT=${fields[1]}
    count=$((count + 1))
  done <"$LOCK"
  [ "$count" -eq 1 ] || die "lock manifest must contain exactly one format record"
}

parse_manifest() {
  local line line_no=0 record fields_count index key platform_index url_index sha_index archive_index payload_index input_path actual_sha
  local fields=()
  reset_manifest
  [ -f "$LOCK" ] || die "lock manifest not found: $LOCK"
  [ -r "$LOCK" ] || die "lock manifest is not readable: $LOCK"
  read_manifest_format

  while IFS= read -r line || [ -n "$line" ]; do
    line_no=$((line_no + 1))
    case $line in
      ''|'#'*) continue ;;
    esac
    fields=()
    IFS=$'\t' read -r -a fields <<<"$line"
    fields_count=${#fields[@]}
    record=${fields[0]-}
    case $record in
      format)
        ;;
      input)
        [ "$MANIFEST_FORMAT" = 2 ] || manifest_error "$line_no" "input record requires lock format 2" || return 1
        [ "$fields_count" -eq 5 ] || manifest_error "$line_no" "malformed input record" || return 1
        is_supported_profile "${fields[1]}" || manifest_error "$line_no" "unknown input profile '${fields[1]}'" || return 1
        case ${fields[2]} in
          uv|bun|go|cargo) ;;
          *) manifest_error "$line_no" "unknown input ecosystem '${fields[2]}'" || return 1 ;;
        esac
        is_safe_relative_path "${fields[3]}" || manifest_error "$line_no" "unsafe input path '${fields[3]}'" || return 1
        validate_sha256 "${fields[4]}" || manifest_error "$line_no" "invalid input SHA-256 '${fields[4]}'" || return 1
        if input_exists "${fields[1]}" "${fields[2]}" "${fields[3]}"; then
          manifest_error "$line_no" "duplicate input '${fields[1]}/${fields[2]}/${fields[3]}'"
          return 1
        fi
        index=${#INPUT_PROFILES[@]}
        INPUT_PROFILES[$index]=${fields[1]}
        INPUT_ECOSYSTEMS[$index]=${fields[2]}
        INPUT_PATHS[$index]=${fields[3]}
        INPUT_SHAS[$index]=${fields[4]}
        ;;
      tool|pin)
        [ "$fields_count" -eq 9 ] || manifest_error "$line_no" "malformed $record record" || return 1
        is_supported_profile "${fields[1]}" || manifest_error "$line_no" "unknown profile '${fields[1]}'" || return 1
        [[ ${fields[2]} =~ ^[a-z0-9][a-z0-9-]*$ ]] || manifest_error "$line_no" "invalid tool name '${fields[2]}'" || return 1
        validate_version "${fields[3]}" || manifest_error "$line_no" "invalid version '${fields[3]}'" || return 1
        if record_name_exists "${fields[2]}"; then
          manifest_error "$line_no" "duplicate tool or pin '${fields[2]}'"
          return 1
        fi
        if [ "$record" = tool ]; then
          [ "${fields[4]}" = archive ] || manifest_error "$line_no" "unsupported direct tool kind '${fields[4]}'" || return 1
          is_safe_relative_path "${fields[5]}" || manifest_error "$line_no" "unsafe tool path '${fields[5]}'" || return 1
          [ "${fields[8]}" = - ] || manifest_error "$line_no" "archive tool source must be '-'" || return 1
          index=${#TOOL_NAMES[@]}
          TOOL_PROFILES[$index]=${fields[1]}
          TOOL_NAMES[$index]=${fields[2]}
          TOOL_VERSIONS[$index]=${fields[3]}
          TOOL_KINDS[$index]=${fields[4]}
          TOOL_PATHS[$index]=${fields[5]}
          TOOL_PROBES[$index]=${fields[6]}
          TOOL_EXPECTED[$index]=${fields[7]}
          TOOL_SOURCES[$index]=${fields[8]}
        else
          case ${fields[4]} in
            uv-python|pypi-wheel|npm-package|go-module|rustup-toolchain|cargo-crate) ;;
            *) manifest_error "$line_no" "unsupported pin kind '${fields[4]}'" || return 1 ;;
          esac
          if [ "${fields[5]}" != - ]; then
            is_safe_relative_path "${fields[5]}" || manifest_error "$line_no" "unsafe pin path '${fields[5]}'" || return 1
          fi
          index=${#PIN_NAMES[@]}
          PIN_PROFILES[$index]=${fields[1]}
          PIN_NAMES[$index]=${fields[2]}
          PIN_VERSIONS[$index]=${fields[3]}
          PIN_KINDS[$index]=${fields[4]}
          PIN_PATHS[$index]=${fields[5]}
          PIN_PROBES[$index]=${fields[6]}
          PIN_EXPECTED[$index]=${fields[7]}
          PIN_SOURCES[$index]=${fields[8]}
        fi
        ;;
      artifact)
        if [ "$MANIFEST_FORMAT" = 1 ]; then
          [ "$fields_count" -eq 7 ] || manifest_error "$line_no" "malformed artifact record" || return 1
          key=-
          platform_index=2
          url_index=3
          sha_index=4
          archive_index=5
          payload_index=6
        else
          [ "$fields_count" -eq 8 ] || manifest_error "$line_no" "malformed artifact record" || return 1
          key=${fields[2]}
          platform_index=3
          url_index=4
          sha_index=5
          archive_index=6
          payload_index=7
          case $key in
            -) ;;
            *) [[ $key =~ ^[a-z0-9][a-z0-9-]*$ ]] || manifest_error "$line_no" "invalid artifact key '$key'" || return 1 ;;
          esac
        fi
        [[ ${fields[1]} =~ ^[a-z0-9][a-z0-9-]*$ ]] || manifest_error "$line_no" "invalid artifact name '${fields[1]}'" || return 1
        case ${fields[$platform_index]} in
          darwin-x86_64|darwin-arm64|linux-x86_64|linux-arm64|any) ;;
          *) manifest_error "$line_no" "unknown artifact platform '${fields[$platform_index]}'" || return 1 ;;
        esac
        case ${fields[$url_index]} in
          https://*) ;;
          file://*)
            [ "${HARNESS_WORKSPACE_TESTING:-0}" = 1 ] || manifest_error "$line_no" "artifact URL must use https" || return 1
            ;;
          *) manifest_error "$line_no" "artifact URL must use https" || return 1 ;;
        esac
        validate_sha256 "${fields[$sha_index]}" || manifest_error "$line_no" "invalid SHA-256 '${fields[$sha_index]}'" || return 1
        case ${fields[$archive_index]} in
          tar.gz|tgz|zip|raw|tar.xz|wheel|crate|manifest) ;;
          *) manifest_error "$line_no" "unsupported artifact format '${fields[$archive_index]}'" || return 1 ;;
        esac
        is_safe_relative_path "${fields[$payload_index]}" || manifest_error "$line_no" "unsafe artifact payload '${fields[$payload_index]}'" || return 1
        if artifact_exists "${fields[1]}" "$key" "${fields[$platform_index]}"; then
          manifest_error "$line_no" "duplicate artifact '${fields[1]}/$key/${fields[$platform_index]}'"
          return 1
        fi
        index=${#ARTIFACT_NAMES[@]}
        ARTIFACT_NAMES[$index]=${fields[1]}
        ARTIFACT_KEYS[$index]=$key
        ARTIFACT_PLATFORMS[$index]=${fields[$platform_index]}
        ARTIFACT_URLS[$index]=${fields[$url_index]}
        ARTIFACT_SHAS[$index]=${fields[$sha_index]}
        ARTIFACT_ARCHIVES[$index]=${fields[$archive_index]}
        ARTIFACT_PAYLOADS[$index]=${fields[$payload_index]}
        ;;
      *) manifest_error "$line_no" "unknown record '$record'" || return 1 ;;
    esac
  done <"$LOCK"

  local i platform common_tool=0
  [ "${#TOOL_NAMES[@]}" -gt 0 ] || die "lock: manifest must contain at least one direct tool"
  for ((i = 0; i < ${#ARTIFACT_NAMES[@]}; i += 1)); do
    record_name_exists "${ARTIFACT_NAMES[$i]}" || \
      die "lock: artifact references unknown tool or pin '${ARTIFACT_NAMES[$i]}'"
  done
  for ((i = 0; i < ${#INPUT_PROFILES[@]}; i += 1)); do
    input_path=$ROOT/${INPUT_PATHS[$i]}
    [ -f "$input_path" ] && [ ! -L "$input_path" ] && [ -r "$input_path" ] || \
      die "lock: input is missing or unreadable: ${INPUT_PATHS[$i]}"
    actual_sha=$(sha256_file "$input_path")
    [ "$actual_sha" = "${INPUT_SHAS[$i]}" ] || \
      die "lock: input checksum mismatch for ${INPUT_PATHS[$i]}"
  done
  for ((i = 0; i < ${#TOOL_NAMES[@]}; i += 1)); do
    [ "${TOOL_PROFILES[$i]}" != common ] || common_tool=1
    for platform in darwin-x86_64 darwin-arm64 linux-x86_64 linux-arm64; do
      if ! index=$(artifact_index_for "${TOOL_NAMES[$i]}" "$platform"); then
        die "lock: missing artifact '${TOOL_NAMES[$i]}/$platform'"
      fi
      case ${ARTIFACT_ARCHIVES[$index]} in
        raw|tar.gz|tgz|zip) ;;
        *) die "lock: direct tool '${TOOL_NAMES[$i]}/$platform' uses unsupported install archive '${ARTIFACT_ARCHIVES[$index]}'" ;;
      esac
    done
    if artifact_exists "${TOOL_NAMES[$i]}" - any; then
      die "lock: direct tool '${TOOL_NAMES[$i]}' must not use an any-platform artifact"
    fi
    for ((index = 0; index < ${#ARTIFACT_NAMES[@]}; index += 1)); do
      if [ "${ARTIFACT_NAMES[$index]}" = "${TOOL_NAMES[$i]}" ] && [ "${ARTIFACT_KEYS[$index]}" != - ]; then
        die "lock: direct tool '${TOOL_NAMES[$i]}' must not use keyed artifacts"
      fi
    done
  done
  [ "$common_tool" -eq 1 ] || die "lock: manifest must contain a common direct tool"
}

select_profiles() {
  local requested=${1-} profile seen=' common '
  SELECTED_PROFILES=(common)
  [ "$#" -gt 0 ] || return 0
  for requested in "$@"; do
    if [ "$requested" = all ]; then
      for profile in python bun go rust; do
        case $seen in
          *" $profile "*) ;;
          *) SELECTED_PROFILES[${#SELECTED_PROFILES[@]}]=$profile; seen="$seen$profile " ;;
        esac
      done
      continue
    fi
    is_supported_profile "$requested" || die "unknown profile '$requested'"
    case $seen in
      *" $requested "*) ;;
      *) SELECTED_PROFILES[${#SELECTED_PROFILES[@]}]=$requested; seen="$seen$requested " ;;
    esac
  done
}

profile_selected() {
  local wanted=$1 profile
  for profile in "${SELECTED_PROFILES[@]}"; do
    [ "$profile" = "$wanted" ] && return 0
  done
  return 1
}

detect_platform() {
  local kernel machine libc_output=
  PLATFORM=
  PLATFORM_ERROR=
  if ! command -v uname >/dev/null 2>&1; then
    PLATFORM_ERROR="missing prerequisite: uname"
    return 1
  fi
  kernel=$(uname -s 2>/dev/null || true)
  machine=$(uname -m 2>/dev/null || true)
  case $kernel in
    Darwin) kernel=darwin ;;
    Linux) kernel=linux ;;
    *) PLATFORM_ERROR="unsupported operating system '$kernel' (supported: macOS and glibc Linux)"; return 1 ;;
  esac
  case $machine in
    x86_64|amd64) machine=x86_64 ;;
    arm64|aarch64) machine=arm64 ;;
    *) PLATFORM_ERROR="unsupported CPU '$machine' (supported: x86_64 and arm64)"; return 1 ;;
  esac
  if [ "$kernel" = linux ]; then
    if command -v getconf >/dev/null 2>&1; then
      libc_output=$(getconf GNU_LIBC_VERSION 2>&1 || true)
    fi
    case $libc_output in
      *glibc*|*GLIBC*|*"GNU libc"*) ;;
      *)
        if command -v ldd >/dev/null 2>&1; then
          libc_output=$(ldd --version 2>&1 || true)
        fi
        case $libc_output in
          *musl*|*MUSL*) PLATFORM_ERROR="unsupported libc 'musl' (glibc Linux is required)"; return 1 ;;
          *glibc*|*GLIBC*|*"GNU libc"*) ;;
          *) PLATFORM_ERROR="could not verify glibc Linux"; return 1 ;;
        esac
        ;;
    esac
  fi
  PLATFORM=$kernel-$machine
}

add_preflight_error() {
  PREFLIGHT_ERRORS[${#PREFLIGHT_ERRORS[@]}]=$1
}

JSON_TOKEN_TYPES=()
JSON_TOKEN_VALUES=()
JSON_TOKEN_ESCAPED=()
JSON_CURSOR=0
JSON_RESULT_KIND=
JSON_RESULT_VALUE=
JSON_STOP_COMMAND_FOUND=0
JSON_EXPECTED_STOP_COMMAND=

json_add_token() {
  local index=${#JSON_TOKEN_TYPES[@]}
  JSON_TOKEN_TYPES[$index]=$1
  JSON_TOKEN_VALUES[$index]=${2-}
  JSON_TOKEN_ESCAPED[$index]=${3:-0}
}

json_tokenize() {
  local file=$1 data length i=0 char next value digits closed had_escape
  JSON_TOKEN_TYPES=()
  JSON_TOKEN_VALUES=()
  JSON_TOKEN_ESCAPED=()
  [ -f "$file" ] && [ -r "$file" ] || return 1
  data=$(<"$file")
  length=${#data}
  while [ "$i" -lt "$length" ]; do
    char=${data:$i:1}
    case $char in
      ' '|$'\t'|$'\r'|$'\n') i=$((i + 1)) ;;
      '{'|'}'|'['|']'|':'|',')
        json_add_token "$char"
        i=$((i + 1))
        ;;
      '"')
        value=
        closed=0
        had_escape=0
        i=$((i + 1))
        while [ "$i" -lt "$length" ]; do
          char=${data:$i:1}
          if [ "$char" = '"' ]; then
            closed=1
            i=$((i + 1))
            break
          fi
          if [ "$char" = \\ ]; then
            had_escape=1
            i=$((i + 1))
            [ "$i" -lt "$length" ] || return 1
            next=${data:$i:1}
            case $next in
              '"'|\\|/) value=$value$next; i=$((i + 1)) ;;
              b) value=$value$'\b'; i=$((i + 1)) ;;
              f) value=$value$'\f'; i=$((i + 1)) ;;
              n) value=$value$'\n'; i=$((i + 1)) ;;
              r) value=$value$'\r'; i=$((i + 1)) ;;
              t) value=$value$'\t'; i=$((i + 1)) ;;
              u)
                [ "$((i + 4))" -lt "$length" ] || return 1
                digits=${data:$((i + 1)):4}
                [[ $digits =~ ^[0-9A-Fa-f]{4}$ ]] || return 1
                value=$value'\u'$digits
                i=$((i + 5))
                ;;
              *) return 1 ;;
            esac
            continue
          fi
          [[ $char == [[:cntrl:]] ]] && return 1
          value=$value$char
          i=$((i + 1))
        done
        [ "$closed" -eq 1 ] || return 1
        json_add_token string "$value" "$had_escape"
        ;;
      t)
        [ "${data:$i:4}" = true ] || return 1
        json_add_token literal true
        i=$((i + 4))
        ;;
      f)
        [ "${data:$i:5}" = false ] || return 1
        json_add_token literal false
        i=$((i + 5))
        ;;
      n)
        [ "${data:$i:4}" = null ] || return 1
        json_add_token literal null
        i=$((i + 4))
        ;;
      -|[0-9])
        value=
        while [ "$i" -lt "$length" ]; do
          char=${data:$i:1}
          case $char in
            ' '|$'\t'|$'\r'|$'\n'|'{'|'}'|'['|']'|':'|',') break ;;
            *) value=$value$char; i=$((i + 1)) ;;
          esac
        done
        [[ $value =~ ^-?(0|[1-9][0-9]*)([.][0-9]+)?([eE][+-]?[0-9]+)?$ ]] || return 1
        json_add_token number "$value"
        ;;
      *) return 1 ;;
    esac
  done
  [ "${#JSON_TOKEN_TYPES[@]}" -gt 0 ]
}

json_parse_value() {
  local path=$1 type=${JSON_TOKEN_TYPES[$JSON_CURSOR]-}
  JSON_RESULT_KIND=
  JSON_RESULT_VALUE=
  case $type in
    string|number|literal)
      JSON_RESULT_KIND=$type
      JSON_RESULT_VALUE=${JSON_TOKEN_VALUES[$JSON_CURSOR]}
      JSON_CURSOR=$((JSON_CURSOR + 1))
      ;;
    '{') json_parse_object "$path" ;;
    '[') json_parse_array "$path" ;;
    *) return 1 ;;
  esac
}

json_parse_object() {
  local path=$1 key existing child_path object_type= object_command=
  local object_keys=()
  [ "${JSON_TOKEN_TYPES[$JSON_CURSOR]-}" = '{' ] || return 1
  JSON_CURSOR=$((JSON_CURSOR + 1))
  if [ "${JSON_TOKEN_TYPES[$JSON_CURSOR]-}" = '}' ]; then
    JSON_CURSOR=$((JSON_CURSOR + 1))
    JSON_RESULT_KIND=object
    JSON_RESULT_VALUE=
    return 0
  fi
  while :; do
    [ "${JSON_TOKEN_TYPES[$JSON_CURSOR]-}" = string ] || return 1
    [ "${JSON_TOKEN_ESCAPED[$JSON_CURSOR]-0}" -eq 0 ] || return 1
    key=${JSON_TOKEN_VALUES[$JSON_CURSOR]}
    for existing in "${object_keys[@]}"; do
      [ "$existing" != "$key" ] || return 1
    done
    object_keys[${#object_keys[@]}]=$key
    JSON_CURSOR=$((JSON_CURSOR + 1))
    [ "${JSON_TOKEN_TYPES[$JSON_CURSOR]-}" = ':' ] || return 1
    JSON_CURSOR=$((JSON_CURSOR + 1))
    child_path=$path/$key
    json_parse_value "$child_path" || return 1
    if [ "$path" = '/hooks/Stop[]/hooks[]' ] && [ "$JSON_RESULT_KIND" = string ]; then
      case $key in
        type) object_type=$JSON_RESULT_VALUE ;;
        command) object_command=$JSON_RESULT_VALUE ;;
      esac
    fi
    case ${JSON_TOKEN_TYPES[$JSON_CURSOR]-} in
      ',') JSON_CURSOR=$((JSON_CURSOR + 1)) ;;
      '}') JSON_CURSOR=$((JSON_CURSOR + 1)); break ;;
      *) return 1 ;;
    esac
  done
  if [ "$path" = '/hooks/Stop[]/hooks[]' ] && [ "$object_type" = command ] && \
    [ "$object_command" = "$JSON_EXPECTED_STOP_COMMAND" ]; then
    JSON_STOP_COMMAND_FOUND=1
  fi
  JSON_RESULT_KIND=object
  JSON_RESULT_VALUE=
}

json_parse_array() {
  local path=$1
  [ "${JSON_TOKEN_TYPES[$JSON_CURSOR]-}" = '[' ] || return 1
  JSON_CURSOR=$((JSON_CURSOR + 1))
  if [ "${JSON_TOKEN_TYPES[$JSON_CURSOR]-}" = ']' ]; then
    JSON_CURSOR=$((JSON_CURSOR + 1))
    JSON_RESULT_KIND=array
    JSON_RESULT_VALUE=
    return 0
  fi
  while :; do
    json_parse_value "$path[]" || return 1
    case ${JSON_TOKEN_TYPES[$JSON_CURSOR]-} in
      ',') JSON_CURSOR=$((JSON_CURSOR + 1)) ;;
      ']') JSON_CURSOR=$((JSON_CURSOR + 1)); break ;;
      *) return 1 ;;
    esac
  done
  JSON_RESULT_KIND=array
  JSON_RESULT_VALUE=
}

json_has_stop_command() {
  local file=$1 expected=$2
  json_tokenize "$file" || return 1
  JSON_CURSOR=0
  JSON_STOP_COMMAND_FOUND=0
  JSON_EXPECTED_STOP_COMMAND=$expected
  json_parse_value '' || return 1
  [ "$JSON_CURSOR" -eq "${#JSON_TOKEN_TYPES[@]}" ] || return 1
  [ "$JSON_STOP_COMMAND_FOUND" -eq 1 ]
}

codex_wrapper_is_exact() {
  local file=$1
  [ -x "$file" ] || return 1
  cmp -s "$file" <(
    printf '%s\n' \
      '#!/bin/sh' \
      'set -u' \
      '' \
      'if [ "$#" -eq 0 ]; then' \
      "  printf '%s\\n' '{\"decision\":\"block\",\"reason\":\"Codex stop-hook wrapper received no command to run.\"}'" \
      '  exit 0' \
      'fi' \
      '' \
      'if "$@" >&2; then' \
      "  printf '%s\\n' '{\"continue\":true}'" \
      'else' \
      "  printf '%s\\n' '{\"decision\":\"block\",\"reason\":\"Stop hook checks failed; review the output above and fix it before stopping.\"}'" \
      'fi'
  )
}

validate_stop_wiring() {
  local claude=$ROOT/.claude/settings.json codex=$ROOT/.codex/hooks.json wrapper=$ROOT/.codex/hooks/codex-stop-hook.sh
  if ! json_has_stop_command "$claude" 'cd $CLAUDE_PROJECT_DIR && make stop-hook'; then
    add_preflight_error "Claude Stop configuration must contain the managed command hook: $claude"
  fi
  if ! json_has_stop_command "$codex" \
    'cd "$(git rev-parse --show-toplevel)" && .codex/hooks/codex-stop-hook.sh make stop-hook'; then
    add_preflight_error "Codex Stop configuration must contain the managed root-entering command hook: $codex"
  fi
  codex_wrapper_is_exact "$wrapper" || \
    add_preflight_error "Codex Stop wrapper differs from the managed implementation: $wrapper"
}

skill_source() {
  if [ -f "$ROOT/.harness/skills/harness/SKILL.md" ]; then
    printf '%s\n' "$ROOT/.harness/skills/harness"
  elif [ -f "$ROOT/skills/harness/SKILL.md" ]; then
    printf '%s\n' "$ROOT/skills/harness"
  else
    return 1
  fi
}

expected_hook() {
  local name=$1
  HOOK_TEXT=$'#!/bin/sh\nset -eu\nroot=$(git rev-parse --show-toplevel)\ncd "$root"\nexec make '"$name"$'\n'
}

hook_is_exact() {
  local file=$1 expected=$2
  cmp -s "$file" <(printf '%s' "$expected")
}

hook_is_known() {
  local file=$1 name=$2 runner legacy
  [ ! -e "$file" ] && return 0
  [ -f "$file" ] || return 1
  expected_hook "$name"
  hook_is_exact "$file" "$HOOK_TEXT" && return 0
  for runner in 'make' 'uv run harness' 'bun harness.ts' 'go run harness.go' 'cargo harness'; do
    legacy=$'#!/bin/sh\nexec '"$runner $name"$'\n'
    hook_is_exact "$file" "$legacy" && return 0
  done
  for runner in 'uv run harness' 'bun harness.ts' 'go run harness.go' 'cargo harness'; do
    legacy=$'#!/bin/sh\n'"$runner $name"$'\n'
    hook_is_exact "$file" "$legacy" && return 0
  done
  if [ "$name" = pre-commit ]; then
    for runner in 'uv run pre-commit' 'bun harness.ts --pre-commit'; do
      legacy=$'#!/bin/sh\n'"$runner"$'\n'
      hook_is_exact "$file" "$legacy" && return 0
    done
  fi
  return 1
}

resolve_hooks() {
  local value
  value=$(git -C "$ROOT" rev-parse --git-path hooks/pre-commit 2>/dev/null) || return 1
  case $value in
    /*) HOOK_PRE_COMMIT=$value ;;
    *) HOOK_PRE_COMMIT=$ROOT/$value ;;
  esac
  value=$(git -C "$ROOT" rev-parse --git-path hooks/pre-push 2>/dev/null) || return 1
  case $value in
    /*) HOOK_PRE_PUSH=$value ;;
    *) HOOK_PRE_PUSH=$ROOT/$value ;;
  esac
}

check_git_and_hooks() {
  local resolved_root
  if [ ! -d "$ROOT" ]; then
    add_preflight_error "workspace root does not exist: $ROOT"
    return
  fi
  if ! resolved_root=$(git -C "$ROOT" rev-parse --show-toplevel 2>/dev/null); then
    add_preflight_error "workspace root is not a Git worktree: $ROOT"
    return
  fi
  if [ "$(cd "$ROOT" && pwd -P)" != "$(cd "$resolved_root" && pwd -P)" ]; then
    add_preflight_error "workspace script must belong to the Git root: $resolved_root"
  fi
  git -C "$ROOT" diff --quiet -- || \
    add_preflight_error "tracked worktree changes must be committed or stashed"
  git -C "$ROOT" diff --cached --quiet -- || \
    add_preflight_error "staged index changes must be committed or unstaged"
  if resolve_hooks; then
    hook_is_known "$HOOK_PRE_COMMIT" pre-commit || \
      add_preflight_error "unmanaged Git hook blocks workspace setup: $HOOK_PRE_COMMIT"
    hook_is_known "$HOOK_PRE_PUSH" pre-push || \
      add_preflight_error "unmanaged Git hook blocks workspace setup: $HOOK_PRE_PUSH"
  else
    add_preflight_error "could not resolve Git hook destinations"
  fi
}

run_preflight() {
  local command_name sha_found=0 resolved_skill
  parse_manifest
  select_profiles "$@"
  case $OFFLINE in
    0|1) ;;
    *) die "OFFLINE must be 0 or 1" ;;
  esac
  PREFLIGHT_ERRORS=()
  for command_name in make bash git curl tar unzip cmp diff awk mktemp mkdir cp chmod mv rm; do
    command -v "$command_name" >/dev/null 2>&1 || add_preflight_error "missing prerequisite: $command_name"
  done
  if command -v unzip >/dev/null 2>&1 && ! unzip -Z -h >/dev/null 2>&1; then
    add_preflight_error "unsupported prerequisite: unzip must provide Info-ZIP -Z listing mode"
  fi
  if command -v sha256sum >/dev/null 2>&1 || command -v shasum >/dev/null 2>&1; then
    sha_found=1
  fi
  [ "$sha_found" -eq 1 ] || add_preflight_error "missing prerequisite: sha256sum or shasum"
  if profile_selected rust; then
    command -v cc >/dev/null 2>&1 || add_preflight_error "missing prerequisite for Rust: cc"
  fi
  if [ -z "$HOME_VALUE" ] || [ ! -d "$HOME_VALUE" ] || [ ! -w "$HOME_VALUE" ]; then
    add_preflight_error "HOME must name a writable directory"
  fi
  case $TOOLS_ROOT in
    /*) ;;
    *) add_preflight_error "managed tools root must be absolute" ;;
  esac
  case $CACHE_ROOT in
    /*) ;;
    *) add_preflight_error "managed cache root must be absolute" ;;
  esac
  if ! detect_platform; then
    add_preflight_error "$PLATFORM_ERROR"
  fi
  if command -v git >/dev/null 2>&1 && command -v cmp >/dev/null 2>&1; then
    check_git_and_hooks
  fi
  validate_stop_wiring
  if ! resolved_skill=$(skill_source); then
    add_preflight_error "canonical or embedded harness skill is missing"
  elif [ ! -r "$resolved_skill/SKILL.md" ]; then
    add_preflight_error "harness skill is not readable: $resolved_skill"
  fi
  if [ "${#PREFLIGHT_ERRORS[@]}" -gt 0 ]; then
    printf 'workspace: preflight failed:\n' >&2
    for command_name in "${PREFLIGHT_ERRORS[@]}"; do
      printf '  - %s\n' "$command_name" >&2
    done
    return 1
  fi
  say "workspace: preflight ok ($PLATFORM; profiles: ${SELECTED_PROFILES[*]})"
}

artifact_index_for() {
  local name=$1 platform=$2 key=${3:--} i
  for ((i = 0; i < ${#ARTIFACT_NAMES[@]}; i += 1)); do
    if [ "${ARTIFACT_NAMES[$i]}" = "$name" ] && \
      [ "${ARTIFACT_KEYS[$i]}" = "$key" ] && \
      [ "${ARTIFACT_PLATFORMS[$i]}" = "$platform" ]; then
      printf '%s\n' "$i"
      return 0
    fi
  done
  return 1
}

tool_index_for() {
  local wanted=$1 i
  for ((i = 0; i < ${#TOOL_NAMES[@]}; i += 1)); do
    if [ "${TOOL_NAMES[$i]}" = "$wanted" ]; then
      printf '%s\n' "$i"
      return 0
    fi
  done
  return 1
}

pin_index_for() {
  local wanted=$1 i
  for ((i = 0; i < ${#PIN_NAMES[@]}; i += 1)); do
    if [ "${PIN_NAMES[$i]}" = "$wanted" ]; then
      printf '%s\n' "$i"
      return 0
    fi
  done
  return 1
}

input_index_for() {
  local wanted_profile=$1 wanted_ecosystem=$2 wanted_path=$3 i
  for ((i = 0; i < ${#INPUT_PROFILES[@]}; i += 1)); do
    if [ "${INPUT_PROFILES[$i]}" = "$wanted_profile" ] && \
      [ "${INPUT_ECOSYSTEMS[$i]}" = "$wanted_ecosystem" ] && \
      [ "${INPUT_PATHS[$i]}" = "$wanted_path" ]; then
      printf '%s\n' "$i"
      return 0
    fi
  done
  return 1
}

selected_pin_kind_exists() {
  local wanted=$1 i
  for ((i = 0; i < ${#PIN_NAMES[@]}; i += 1)); do
    if [ "${PIN_KINDS[$i]}" = "$wanted" ] && profile_selected "${PIN_PROFILES[$i]}"; then
      return 0
    fi
  done
  return 1
}

tool_dir() {
  printf '%s/%s/%s\n' "$TOOLS_ROOT" "${TOOL_NAMES[$1]}" "${TOOL_VERSIONS[$1]}"
}

receipt_for() {
  local tool_index=$1 artifact_index=$2
  printf 'format\t1\n'
  printf 'tool\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
    "${TOOL_PROFILES[$tool_index]}" "${TOOL_NAMES[$tool_index]}" \
    "${TOOL_VERSIONS[$tool_index]}" "${TOOL_PATHS[$tool_index]}" \
    "${TOOL_PROBES[$tool_index]}" "${TOOL_EXPECTED[$tool_index]}" \
    "${TOOL_SOURCES[$tool_index]}"
  printf 'artifact\t%s\t%s\t%s\t%s\t%s\t%s\n' \
    "${ARTIFACT_NAMES[$artifact_index]}" "${ARTIFACT_PLATFORMS[$artifact_index]}" \
    "${ARTIFACT_URLS[$artifact_index]}" "${ARTIFACT_SHAS[$artifact_index]}" \
    "${ARTIFACT_ARCHIVES[$artifact_index]}" "${ARTIFACT_PAYLOADS[$artifact_index]}"
}

python_receipt_for() {
  local pin_index=$1 artifact_index=$2 input_index=$3
  printf 'format\t2\n'
  printf 'pin\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
    "${PIN_PROFILES[$pin_index]}" "${PIN_NAMES[$pin_index]}" \
    "${PIN_VERSIONS[$pin_index]}" "${PIN_KINDS[$pin_index]}" \
    "${PIN_PATHS[$pin_index]}" "${PIN_PROBES[$pin_index]}" \
    "${PIN_EXPECTED[$pin_index]}" "${PIN_SOURCES[$pin_index]}"
  printf 'input\t%s\t%s\t%s\t%s\n' \
    "${INPUT_PROFILES[$input_index]}" "${INPUT_ECOSYSTEMS[$input_index]}" \
    "${INPUT_PATHS[$input_index]}" "${INPUT_SHAS[$input_index]}"
  printf 'artifact\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
    "${ARTIFACT_NAMES[$artifact_index]}" "${ARTIFACT_KEYS[$artifact_index]}" \
    "${ARTIFACT_PLATFORMS[$artifact_index]}" "${ARTIFACT_URLS[$artifact_index]}" \
    "${ARTIFACT_SHAS[$artifact_index]}" "${ARTIFACT_ARCHIVES[$artifact_index]}" \
    "${ARTIFACT_PAYLOADS[$artifact_index]}"
}

python_cli_module() {
  case $1 in
    lizard|vulture) printf '%s\n' "$1" ;;
    pip-audit) printf '%s\n' pip_audit ;;
    *) return 1 ;;
  esac
}

python_cli_wrapper_for() {
  local module=$1
  printf '%s\n' \
    '#!/bin/sh' \
    'set -eu' \
    'case $0 in' \
    '  */*) bin_dir=${0%/*} ;;' \
    '  *) bin_dir=. ;;' \
    'esac' \
    'root=$(CDPATH= cd -- "$bin_dir/.." && pwd -P)' \
    "exec \"\$root/runtime/bin/python\" -m $module \"\$@\""
}

python_cli_receipt_for() {
  local pin_index=$1 artifact_index=$2 input_index=$3 python_pin=$4 uv_tool=$5
  local python_receipt=$TOOLS_ROOT/python/${PIN_VERSIONS[$python_pin]}/.harness-workspace-receipt
  printf 'format\t2\n'
  printf 'pin\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
    "${PIN_PROFILES[$pin_index]}" "${PIN_NAMES[$pin_index]}" \
    "${PIN_VERSIONS[$pin_index]}" "${PIN_KINDS[$pin_index]}" \
    "${PIN_PATHS[$pin_index]}" "${PIN_PROBES[$pin_index]}" \
    "${PIN_EXPECTED[$pin_index]}" "${PIN_SOURCES[$pin_index]}"
  printf 'input\t%s\t%s\t%s\t%s\n' \
    "${INPUT_PROFILES[$input_index]}" "${INPUT_ECOSYSTEMS[$input_index]}" \
    "${INPUT_PATHS[$input_index]}" "${INPUT_SHAS[$input_index]}"
  printf 'artifact\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
    "${ARTIFACT_NAMES[$artifact_index]}" "${ARTIFACT_KEYS[$artifact_index]}" \
    "${ARTIFACT_PLATFORMS[$artifact_index]}" "${ARTIFACT_URLS[$artifact_index]}" \
    "${ARTIFACT_SHAS[$artifact_index]}" "${ARTIFACT_ARCHIVES[$artifact_index]}" \
    "${ARTIFACT_PAYLOADS[$artifact_index]}"
  printf 'builder\tuv\t%s\n' "${TOOL_VERSIONS[$uv_tool]}"
  printf 'python\t%s\t%s\n' "${PIN_VERSIONS[$python_pin]}" "$(sha256_file "$python_receipt")"
}

knip_wrapper_for() {
  local bun=$1
  printf '%s\n' \
    '#!/bin/sh' \
    'set -eu' \
    'case $0 in' \
    '  */*) bin_dir=${0%/*} ;;' \
    '  *) bin_dir=. ;;' \
    'esac' \
    'root=$(CDPATH= cd -- "$bin_dir/.." && pwd -P)' \
    "exec \"$bun\" \"\$root/runtime/node_modules/knip/bin/knip.js\" \"\$@\""
}

knip_receipt_for() {
  local pin_index=$1 artifact_index=$2 package_input=$3 lock_input=$4 bun_tool=$5
  printf 'format\t2\n'
  printf 'pin\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
    "${PIN_PROFILES[$pin_index]}" "${PIN_NAMES[$pin_index]}" \
    "${PIN_VERSIONS[$pin_index]}" "${PIN_KINDS[$pin_index]}" \
    "${PIN_PATHS[$pin_index]}" "${PIN_PROBES[$pin_index]}" \
    "${PIN_EXPECTED[$pin_index]}" "${PIN_SOURCES[$pin_index]}"
  printf 'input\t%s\t%s\t%s\t%s\n' \
    "${INPUT_PROFILES[$package_input]}" "${INPUT_ECOSYSTEMS[$package_input]}" \
    "${INPUT_PATHS[$package_input]}" "${INPUT_SHAS[$package_input]}"
  printf 'input\t%s\t%s\t%s\t%s\n' \
    "${INPUT_PROFILES[$lock_input]}" "${INPUT_ECOSYSTEMS[$lock_input]}" \
    "${INPUT_PATHS[$lock_input]}" "${INPUT_SHAS[$lock_input]}"
  printf 'artifact\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
    "${ARTIFACT_NAMES[$artifact_index]}" "${ARTIFACT_KEYS[$artifact_index]}" \
    "${ARTIFACT_PLATFORMS[$artifact_index]}" "${ARTIFACT_URLS[$artifact_index]}" \
    "${ARTIFACT_SHAS[$artifact_index]}" "${ARTIFACT_ARCHIVES[$artifact_index]}" \
    "${ARTIFACT_PAYLOADS[$artifact_index]}"
  printf 'builder\tbun\t%s\n' "${TOOL_VERSIONS[$bun_tool]}"
}

go_package_for() {
  local pin_index=$1 source version suffix
  source=${PIN_SOURCES[$pin_index]}
  version=${PIN_VERSIONS[$pin_index]}
  suffix=@v$version
  case $source in
    *"$suffix") printf '%s\n' "${source%$suffix}" ;;
    *) return 1 ;;
  esac
}

go_module_for() {
  case $1 in
    govulncheck) printf '%s\n' golang.org/x/vuln ;;
    go-arch-lint) printf '%s\n' github.com/fe3dback/go-arch-lint ;;
    gremlins) printf '%s\n' github.com/go-gremlins/gremlins ;;
    *) return 1 ;;
  esac
}

go_module_sum() {
  local module=$1 version=$2 sum_input=$3
  awk -v module="$module" -v version="$version" \
    '$1 == module && $2 == version { print $3; found++ } END { exit found != 1 }' \
    "$ROOT/${INPUT_PATHS[$sum_input]}"
}

go_tool_receipt_for() {
  local pin_index=$1 mod_input=$2 sum_input=$3 go_tool=$4 module version module_sum package
  module=$(go_module_for "${PIN_NAMES[$pin_index]}") || return 1
  version=v${PIN_VERSIONS[$pin_index]}
  module_sum=$(go_module_sum "$module" "$version" "$sum_input") || return 1
  package=$(go_package_for "$pin_index") || return 1
  printf 'format\t2\n'
  printf 'pin\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
    "${PIN_PROFILES[$pin_index]}" "${PIN_NAMES[$pin_index]}" \
    "${PIN_VERSIONS[$pin_index]}" "${PIN_KINDS[$pin_index]}" \
    "${PIN_PATHS[$pin_index]}" "${PIN_PROBES[$pin_index]}" \
    "${PIN_EXPECTED[$pin_index]}" "${PIN_SOURCES[$pin_index]}"
  printf 'input\t%s\t%s\t%s\t%s\n' \
    "${INPUT_PROFILES[$mod_input]}" "${INPUT_ECOSYSTEMS[$mod_input]}" \
    "${INPUT_PATHS[$mod_input]}" "${INPUT_SHAS[$mod_input]}"
  printf 'input\t%s\t%s\t%s\t%s\n' \
    "${INPUT_PROFILES[$sum_input]}" "${INPUT_ECOSYSTEMS[$sum_input]}" \
    "${INPUT_PATHS[$sum_input]}" "${INPUT_SHAS[$sum_input]}"
  printf 'builder\tgo\t%s\n' "${TOOL_VERSIONS[$go_tool]}"
  printf 'module\t%s\t%s\t%s\t%s\n' "$package" "$module" "$version" "$module_sum"
}

rust_target_for_platform() {
  case $1 in
    darwin-x86_64) printf '%s\n' x86_64-apple-darwin ;;
    darwin-arm64) printf '%s\n' aarch64-apple-darwin ;;
    linux-x86_64) printf '%s\n' x86_64-unknown-linux-gnu ;;
    linux-arm64) printf '%s\n' aarch64-unknown-linux-gnu ;;
    *) return 1 ;;
  esac
}

validate_rust_dist_closure() {
  local pin_index=$1 input_index=$2 target
  target=$(rust_target_for_platform "$PLATFORM") || return 1
  awk -F '\t' -v platform="$PLATFORM" -v target="$target" \
    -v version="${PIN_VERSIONS[$pin_index]}" \
    -v testing="${HARNESS_WORKSPACE_TESTING:-0}" '
      BEGIN {
        expected["cargo"] = 1
        expected["clippy-preview"] = 1
        expected["llvm-tools-preview"] = 1
        expected["rust-std"] = 1
        expected["rustc"] = 1
        expected["rustfmt-preview"] = 1
      }
      /^#/ || NF == 0 { next }
      $1 == "format" {
        if (NF != 2 || $2 != "1") exit 1
        formats++
        next
      }
      $1 == "release" {
        if (NF != 3 || $2 != version || $3 !~ /^[0-9]{4}-[0-9]{2}-[0-9]{2}$/) exit 1
        releases++
        next
      }
      $1 == "manifest" {
        if (NF != 5 || length($3) != 64 || $3 ~ /[^0-9a-f]/ ||
            length($5) != 64 || $5 ~ /[^0-9a-f]/) exit 1
        manifests++
        next
      }
      $1 == "component" {
        if (NF != 6 || length($6) != 64 || $6 ~ /[^0-9a-f]/) exit 1
        if ($2 != platform) next
        if (!($3 in expected) || $4 != target || seen[$3]++) exit 1
        if (testing == "1") {
          if ($5 !~ /^https:\/\// && $5 !~ /^file:\/\//) exit 1
        } else if ($5 !~ /^https:\/\//) exit 1
        selected++
        next
      }
      { exit 1 }
      END {
        if (formats != 1 || releases != 1 || manifests != 1 || selected != 6) exit 1
        for (component in expected) if (seen[component] != 1) exit 1
      }
    ' "$ROOT/${INPUT_PATHS[$input_index]}"
}

rust_receipt_for() {
  local pin_index=$1 input_index=$2 rustup_tool=$3 target rustup_receipt
  target=$(rust_target_for_platform "$PLATFORM") || return 1
  rustup_receipt=$(tool_dir "$rustup_tool")/.harness-workspace-receipt
  printf 'format\t2\n'
  printf 'pin\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
    "${PIN_PROFILES[$pin_index]}" "${PIN_NAMES[$pin_index]}" \
    "${PIN_VERSIONS[$pin_index]}" "${PIN_KINDS[$pin_index]}" \
    "${PIN_PATHS[$pin_index]}" "${PIN_PROBES[$pin_index]}" \
    "${PIN_EXPECTED[$pin_index]}" "${PIN_SOURCES[$pin_index]}"
  printf 'input\t%s\t%s\t%s\t%s\n' \
    "${INPUT_PROFILES[$input_index]}" "${INPUT_ECOSYSTEMS[$input_index]}" \
    "${INPUT_PATHS[$input_index]}" "${INPUT_SHAS[$input_index]}"
  printf 'target\t%s\n' "$target"
  awk -F '\t' -v platform="$PLATFORM" \
    '$1 == "component" && $2 == platform { print }' \
    "$ROOT/${INPUT_PATHS[$input_index]}"
  printf 'builder\trustup\t%s\t%s\n' "${TOOL_VERSIONS[$rustup_tool]}" \
    "$(sha256_file "$rustup_receipt")"
}

cargo_modules_receipt_for() {
  local pin_index=$1 artifact_index=$2 lock_input=$3 rust_pin=$4 rust_input=$5
  local rust_receipt=$TOOLS_ROOT/rust/${PIN_VERSIONS[$rust_pin]}/.harness-workspace-receipt
  printf 'format\t2\n'
  printf 'pin\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
    "${PIN_PROFILES[$pin_index]}" "${PIN_NAMES[$pin_index]}" \
    "${PIN_VERSIONS[$pin_index]}" "${PIN_KINDS[$pin_index]}" \
    "${PIN_PATHS[$pin_index]}" "${PIN_PROBES[$pin_index]}" \
    "${PIN_EXPECTED[$pin_index]}" "${PIN_SOURCES[$pin_index]}"
  printf 'input\t%s\t%s\t%s\t%s\n' \
    "${INPUT_PROFILES[$lock_input]}" "${INPUT_ECOSYSTEMS[$lock_input]}" \
    "${INPUT_PATHS[$lock_input]}" "${INPUT_SHAS[$lock_input]}"
  printf 'artifact\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
    "${ARTIFACT_NAMES[$artifact_index]}" "${ARTIFACT_KEYS[$artifact_index]}" \
    "${ARTIFACT_PLATFORMS[$artifact_index]}" "${ARTIFACT_URLS[$artifact_index]}" \
    "${ARTIFACT_SHAS[$artifact_index]}" "${ARTIFACT_ARCHIVES[$artifact_index]}" \
    "${ARTIFACT_PAYLOADS[$artifact_index]}"
  printf 'builder\trust\t%s\t%s\n' "${PIN_VERSIONS[$rust_pin]}" "$(sha256_file "$rust_receipt")"
  printf 'closure\t%s\n' "${INPUT_SHAS[$rust_input]}"
}

probe_matches() {
  local output=$1 expected=$2
  case $output in
    "$expected"|"$expected "*|"$expected+"*|"$expected"' ('*) return 0 ;;
    *) return 1 ;;
  esac
}

verify_tool_at() {
  local tool_index=$1 base=$2 artifact_index=$3 path executable output
  local IFS=,
  local paths=()
  read -r -a paths <<<"${TOOL_PATHS[$tool_index]}"
  for path in "${paths[@]}"; do
    [ -x "$base/$path" ] || return 1
  done
  [ -f "$base/.harness-workspace-receipt" ] || return 1
  cmp -s "$base/.harness-workspace-receipt" <(receipt_for "$tool_index" "$artifact_index") || return 1
  executable=$base/${paths[0]}
  if [ "${TOOL_NAMES[$tool_index]}" = cargo-llvm-cov ]; then
    if ! output=$(LC_ALL=C "$executable" llvm-cov "${TOOL_PROBES[$tool_index]}" 2>&1); then
      return 1
    fi
  elif ! output=$(LC_ALL=C "$executable" "${TOOL_PROBES[$tool_index]}" 2>&1); then
    return 1
  fi
  probe_matches "$output" "${TOOL_EXPECTED[$tool_index]}" || return 1
  return 0
}

verify_python_at() {
  local base=$1 pin_index=$2 artifact_index=$3 input_index=$4 executable output
  executable=$base/runtime/bin/python3.13
  [ -x "$executable" ] || return 1
  [ -f "$base/.harness-workspace-receipt" ] || return 1
  cmp -s "$base/.harness-workspace-receipt" \
    <(python_receipt_for "$pin_index" "$artifact_index" "$input_index") || return 1
  if ! output=$(LC_ALL=C "$executable" "${PIN_PROBES[$pin_index]}" 2>&1); then
    return 1
  fi
  [ "$output" = "${PIN_EXPECTED[$pin_index]}" ]
}

verify_python_cli_at() {
  local base=$1 pin_index=$2 artifact_index=$3 input_index=$4 python_pin=$5 uv_tool=$6
  local name module executable output
  name=${PIN_NAMES[$pin_index]}
  module=$(python_cli_module "$name") || return 1
  executable=$base/bin/$name
  [ -x "$base/runtime/bin/python" ] || return 1
  [ -x "$executable" ] || return 1
  cmp -s "$executable" <(python_cli_wrapper_for "$module") || return 1
  [ -f "$base/.harness-workspace-receipt" ] || return 1
  cmp -s "$base/.harness-workspace-receipt" \
    <(python_cli_receipt_for "$pin_index" "$artifact_index" "$input_index" "$python_pin" "$uv_tool") || return 1
  if ! output=$(LC_ALL=C "$executable" "${PIN_PROBES[$pin_index]}" 2>&1); then
    return 1
  fi
  [ "$output" = "${PIN_EXPECTED[$pin_index]}" ]
}

verify_knip_at() {
  local base=$1 pin_index=$2 artifact_index=$3 package_input=$4 lock_input=$5 bun_tool=$6
  local bun executable output
  bun=$(tool_dir "$bun_tool")/bin/bun
  executable=$base/bin/knip
  [ -x "$bun" ] || return 1
  [ -f "$base/runtime/node_modules/knip/bin/knip.js" ] || return 1
  [ -x "$executable" ] || return 1
  cmp -s "$executable" <(knip_wrapper_for "$bun") || return 1
  cmp -s "$base/runtime/package.json" "$ROOT/${INPUT_PATHS[$package_input]}" || return 1
  cmp -s "$base/runtime/bun.lock" "$ROOT/${INPUT_PATHS[$lock_input]}" || return 1
  [ -f "$base/.harness-workspace-receipt" ] || return 1
  cmp -s "$base/.harness-workspace-receipt" \
    <(knip_receipt_for "$pin_index" "$artifact_index" "$package_input" "$lock_input" "$bun_tool") || return 1
  if ! output=$(LC_ALL=C "$executable" "${PIN_PROBES[$pin_index]}" 2>&1); then
    return 1
  fi
  [ "$output" = "${PIN_EXPECTED[$pin_index]}" ]
}

verify_go_tool_at() {
  local base=$1 pin_index=$2 mod_input=$3 sum_input=$4 go_tool=$5
  local name executable go module version module_sum package metadata first_line output
  name=${PIN_NAMES[$pin_index]}
  executable=$base/bin/$name
  go=$(tool_dir "$go_tool")/go/bin/go
  module=$(go_module_for "$name") || return 1
  version=v${PIN_VERSIONS[$pin_index]}
  module_sum=$(go_module_sum "$module" "$version" "$sum_input") || return 1
  package=$(go_package_for "$pin_index") || return 1
  [ -x "$go" ] && [ -x "$executable" ] || return 1
  [ -f "$base/.harness-workspace-receipt" ] || return 1
  cmp -s "$base/.harness-workspace-receipt" \
    <(go_tool_receipt_for "$pin_index" "$mod_input" "$sum_input" "$go_tool") || return 1
  if ! metadata=$(LC_ALL=C "$go" version -m "$executable" 2>&1); then
    return 1
  fi
  first_line=${metadata%%$'\n'*}
  [ "$first_line" = "$executable: go${TOOL_VERSIONS[$go_tool]}" ] || return 1
  printf '%s\n' "$metadata" | awk -F '\t' -v package="$package" -v module="$module" \
    -v version="$version" -v module_sum="$module_sum" '
      $2 == "path" && $3 == package { path_found++ }
      $2 == "mod" && $3 == module && $4 == version && $5 == module_sum { module_found++ }
      END { exit !(path_found == 1 && module_found == 1) }
    ' || return 1
  if ! output=$(LC_ALL=C "$executable" "${PIN_PROBES[$pin_index]}" 2>&1); then
    return 1
  fi
  case $output in
    "${PIN_EXPECTED[$pin_index]}"*) return 0 ;;
    *) return 1 ;;
  esac
}

verify_rust_at() {
  local base=$1 pin_index=$2 input_index=$3 rustup_tool=$4 target toolchain output sysroot executable
  local installed cargo_path expected_component normalized component_count=0
  target=$(rust_target_for_platform "$PLATFORM") || return 1
  toolchain=${PIN_VERSIONS[$pin_index]}-$target
  for executable in rustup rustc rustdoc cargo rustfmt cargo-fmt clippy-driver cargo-clippy; do
    [ -x "$base/cargo/bin/$executable" ] || return 1
  done
  sysroot=$base/rustup/toolchains/$toolchain
  [ -x "$sysroot/bin/rustc" ] && [ -x "$sysroot/bin/cargo" ] || return 1
  [ -x "$sysroot/lib/rustlib/$target/bin/llvm-cov" ] || return 1
  [ -x "$sysroot/lib/rustlib/$target/bin/llvm-profdata" ] || return 1
  [ -f "$base/.harness-workspace-receipt" ] || return 1
  cmp -s "$base/.harness-workspace-receipt" \
    <(rust_receipt_for "$pin_index" "$input_index" "$rustup_tool") || return 1
  if ! output=$(LC_ALL=C RUSTUP_HOME="$base/rustup" CARGO_HOME="$base/cargo" \
    RUSTUP_TOOLCHAIN="$toolchain" RUSTUP_AUTO_INSTALL=0 \
    "$base/cargo/bin/rustc" "${PIN_PROBES[$pin_index]}" 2>&1); then
    return 1
  fi
  probe_matches "$output" "${PIN_EXPECTED[$pin_index]}" || return 1
  if ! output=$(LC_ALL=C RUSTUP_HOME="$base/rustup" CARGO_HOME="$base/cargo" \
    RUSTUP_TOOLCHAIN="$toolchain" RUSTUP_AUTO_INSTALL=0 "$base/cargo/bin/rustc" -vV 2>&1); then
    return 1
  fi
  case $'\n'$output$'\n' in
    *$'\nhost: '"$target"$'\n'*) ;;
    *) return 1 ;;
  esac
  if ! output=$(LC_ALL=C RUSTUP_HOME="$base/rustup" CARGO_HOME="$base/cargo" \
    RUSTUP_TOOLCHAIN="$toolchain" RUSTUP_AUTO_INSTALL=0 \
    "$base/cargo/bin/rustc" --print sysroot 2>&1); then
    return 1
  fi
  [ "$output" = "$sysroot" ] || return 1
  if ! cargo_path=$(LC_ALL=C RUSTUP_HOME="$base/rustup" CARGO_HOME="$base/cargo" \
    RUSTUP_AUTO_INSTALL=0 "$base/cargo/bin/rustup" which cargo --toolchain "$toolchain" 2>&1); then
    return 1
  fi
  [ "$cargo_path" = "$sysroot/bin/cargo" ] || return 1
  if ! installed=$(LC_ALL=C RUSTUP_HOME="$base/rustup" CARGO_HOME="$base/cargo" \
    RUSTUP_AUTO_INSTALL=0 "$base/cargo/bin/rustup" component list --installed \
      --toolchain "$toolchain" 2>&1); then
    return 1
  fi
  for expected_component in cargo clippy llvm-tools rust-std rustc rustfmt; do
    normalized=
    while IFS= read -r executable || [ -n "$executable" ]; do
      case $executable in
        "$expected_component-$target") normalized=$executable; component_count=$((component_count + 1)) ;;
      esac
    done <<<"$installed"
    [ "$normalized" = "$expected_component-$target" ] || return 1
  done
  [ "$component_count" -eq 6 ] || return 1
  [ "$(printf '%s\n' "$installed" | awk 'NF { count++ } END { print count + 0 }')" -eq 6 ] || return 1
  LC_ALL=C RUSTUP_HOME="$base/rustup" CARGO_HOME="$base/cargo" \
    RUSTUP_TOOLCHAIN="$toolchain" RUSTUP_AUTO_INSTALL=0 \
    "$base/cargo/bin/cargo" --version >/dev/null 2>&1 || return 1
  LC_ALL=C RUSTUP_HOME="$base/rustup" CARGO_HOME="$base/cargo" \
    RUSTUP_TOOLCHAIN="$toolchain" RUSTUP_AUTO_INSTALL=0 \
    "$base/cargo/bin/rustfmt" --version >/dev/null 2>&1 || return 1
  LC_ALL=C RUSTUP_HOME="$base/rustup" CARGO_HOME="$base/cargo" \
    RUSTUP_TOOLCHAIN="$toolchain" RUSTUP_AUTO_INSTALL=0 \
    "$base/cargo/bin/clippy-driver" --version >/dev/null 2>&1 || return 1
  LC_ALL=C "$sysroot/lib/rustlib/$target/bin/llvm-cov" --version >/dev/null 2>&1 || return 1
}

verify_cargo_modules_at() {
  local base=$1 pin_index=$2 artifact_index=$3 lock_input=$4 rust_pin=$5 rust_input=$6
  local executable output
  executable=$base/bin/cargo-modules
  [ -x "$executable" ] || return 1
  [ -f "$base/.harness-workspace-receipt" ] || return 1
  cmp -s "$base/.harness-workspace-receipt" \
    <(cargo_modules_receipt_for "$pin_index" "$artifact_index" "$lock_input" \
      "$rust_pin" "$rust_input") || return 1
  if ! output=$(LC_ALL=C RUST_LOG=off NO_COLOR=1 \
    "$executable" "${PIN_PROBES[$pin_index]}" 2>&1); then
    return 1
  fi
  [ "$output" = "${PIN_EXPECTED[$pin_index]}" ]
}

sha256_file() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | awk '{print $1}'
  else
    shasum -a 256 "$1" | awk '{print $1}'
  fi
}

safe_remove() {
  local target=$1
  case $target in
    "$TOOLS_ROOT"/*|"$HOME_VALUE/.claude/skills"/*|"$HOME_VALUE/.agents/skills"/*) rm -rf -- "$target" ;;
    *) die "refusing to remove path outside managed roots: $target" ;;
  esac
}

register_cleanup_path() {
  CLEANUP_PATHS[${#CLEANUP_PATHS[@]}]=$1
}

clear_active_replacement() {
  ACTIVE_REPLACEMENT_DESTINATION=
  ACTIVE_REPLACEMENT_BACKUP=
  ACTIVE_REPLACEMENT_PUBLISHED=0
}

rollback_active_replacement() {
  if [ "$ACTIVE_REPLACEMENT_PUBLISHED" -eq 1 ] && \
    { [ -e "$ACTIVE_REPLACEMENT_DESTINATION" ] || [ -L "$ACTIVE_REPLACEMENT_DESTINATION" ]; }; then
    rm -rf -- "$ACTIVE_REPLACEMENT_DESTINATION"
  fi
  if [ -n "$ACTIVE_REPLACEMENT_BACKUP" ] && \
    { [ -e "$ACTIVE_REPLACEMENT_BACKUP" ] || [ -L "$ACTIVE_REPLACEMENT_BACKUP" ]; }; then
    if [ ! -e "$ACTIVE_REPLACEMENT_DESTINATION" ] && [ ! -L "$ACTIVE_REPLACEMENT_DESTINATION" ]; then
      mv "$ACTIVE_REPLACEMENT_BACKUP" "$ACTIVE_REPLACEMENT_DESTINATION"
    fi
  fi
  clear_active_replacement
}

cleanup_workspace() {
  local status=$? path
  trap - EXIT HUP INT TERM
  set +e
  rollback_active_replacement
  for path in "${CLEANUP_PATHS[@]}"; do
    if [ -e "$path" ] || [ -L "$path" ]; then
      rm -rf -- "$path"
    fi
  done
  exit "$status"
}

download_artifact() {
  local url=$1 destination=$2
  case $url in
    file://*)
      [ "${HARNESS_WORKSPACE_TESTING:-0}" = 1 ] || return 1
      curl --fail --location --silent --show-error "$url" --output "$destination"
      ;;
    https://*)
      curl --fail --location --silent --show-error --proto '=https' --proto-redir '=https' \
        --tlsv1.2 "$url" --output "$destination"
      ;;
    *) return 1 ;;
  esac
}

ensure_cached_artifact() {
  local label=$1 url=$2 sha=$3 cache_dir=$4 suffix=$5 destination temp actual
  cache_dir=$CACHE_ROOT/$cache_dir
  destination=$cache_dir/$sha$suffix
  CACHED_ARTIFACT_PATH=
  if [ -f "$destination" ] && [ ! -L "$destination" ]; then
    actual=$(sha256_file "$destination")
    if [ "$actual" = "$sha" ]; then
      CACHED_ARTIFACT_PATH=$destination
      return 0
    fi
  fi
  if [ "$OFFLINE" = 1 ]; then
    printf 'workspace: offline cache miss for %s at %s\n' "$label" "$destination" >&2
    return 1
  fi
  mkdir -p "$cache_dir"
  temp=$(mktemp "$cache_dir/.$sha.tmp.XXXXXX")
  register_cleanup_path "$temp"
  if ! download_artifact "$url" "$temp"; then
    printf 'workspace: failed to download %s from %s\n' "$label" "$url" >&2
    rm -f -- "$temp"
    return 1
  fi
  actual=$(sha256_file "$temp")
  if [ "$actual" != "$sha" ]; then
    printf 'workspace: checksum mismatch for %s: expected %s, got %s\n' \
      "$label" "$sha" "$actual" >&2
    rm -f -- "$temp"
    return 1
  fi
  chmod 0644 "$temp"
  if ! mv "$temp" "$destination"; then
    rm -f -- "$temp"
    return 1
  fi
  CACHED_ARTIFACT_PATH=$destination
}

archive_is_safe() {
  local archive=$1 downloaded=$2 work_dir=$3 line member mode member_count=0 type_count=0
  local names=$work_dir/archive-members types=$work_dir/archive-types
  case $archive in
    tar.gz|tgz)
      tar -tzf "$downloaded" >"$names" || return 1
      tar -tvzf "$downloaded" >"$types" || return 1
      ;;
    crate)
      tar -tzf "$downloaded" >"$names" || return 1
      tar -tvzf "$downloaded" >"$types" || return 1
      ;;
    zip)
      unzip -Z1 "$downloaded" >"$names" || return 1
      unzip -Z -l "$downloaded" >"$types" || return 1
      ;;
    raw) return 0 ;;
    *) return 1 ;;
  esac
  while IFS= read -r member || [ -n "$member" ]; do
    [ -n "$member" ] || return 1
    is_safe_relative_path "$member" || return 1
    member_count=$((member_count + 1))
  done <"$names"
  [ "$member_count" -gt 0 ] || return 1
  while IFS= read -r line || [ -n "$line" ]; do
    set -- $line
    mode=${1-}
    case $archive:$mode in
      tar.gz:-*|tar.gz:d*|tgz:-*|tgz:d*|crate:-*|crate:d*|zip:-*|zip:d*)
        type_count=$((type_count + 1))
        ;;
      tar.gz:*|tgz:*|crate:*) return 1 ;;
      zip:l*|zip:b*|zip:c*|zip:p*|zip:s*) return 1 ;;
      zip:*) ;;
    esac
  done <"$types"
  [ "$type_count" -eq "$member_count" ]
}

extract_artifact() {
  local archive=$1 downloaded=$2 extract_dir=$3 payload=$4 raw_destination
  local IFS=,
  local payloads=()
  read -r -a payloads <<<"$payload"
  mkdir -p "$extract_dir"
  case $archive in
    tar.gz|tgz) tar -xzf "$downloaded" -C "$extract_dir" -- "${payloads[@]}" ;;
    zip) unzip -q "$downloaded" "${payloads[@]}" -d "$extract_dir" ;;
    raw)
      [ "${#payloads[@]}" -eq 1 ] || return 1
      raw_destination=$extract_dir/${payloads[0]}
      mkdir -p "${raw_destination%/*}"
      cp "$downloaded" "$raw_destination"
      ;;
    *) return 1 ;;
  esac
}

stage_payloads() {
  local tool_index=$1 artifact_index=$2 extract_dir=$3 stage_dir=$4 i source destination
  local IFS=,
  local payloads=() paths=()
  read -r -a payloads <<<"${ARTIFACT_PAYLOADS[$artifact_index]}"
  read -r -a paths <<<"${TOOL_PATHS[$tool_index]}"
  [ "${#payloads[@]}" -eq "${#paths[@]}" ] || return 1
  mkdir -p "$stage_dir"
  for ((i = 0; i < ${#payloads[@]}; i += 1)); do
    source=$extract_dir/${payloads[$i]}
    [ -e "$source" ] || return 1
    if [ -d "$source" ]; then
      cp -R "$source" "$stage_dir/${source##*/}"
    else
      destination=$stage_dir/${paths[$i]}
      mkdir -p "${destination%/*}"
      cp "$source" "$destination"
      chmod 0755 "$destination"
    fi
  done
  receipt_for "$tool_index" "$artifact_index" >"$stage_dir/.harness-workspace-receipt"
}

install_archive_tool() {
  local tool_index=$1 artifact_index=$2 destination parent temp_dir download extract_dir stage_dir actual_sha backup=
  destination=$(tool_dir "$tool_index")
  if verify_tool_at "$tool_index" "$destination" "$artifact_index"; then
    say "workspace: reuse ${TOOL_NAMES[$tool_index]} ${TOOL_VERSIONS[$tool_index]}"
    return 0
  fi
  if [ "$OFFLINE" = 1 ]; then
    printf 'workspace: offline cache miss for %s %s at %s\n' \
      "${TOOL_NAMES[$tool_index]}" "${TOOL_VERSIONS[$tool_index]}" "$destination" >&2
    return 1
  fi
  parent=$TOOLS_ROOT/${TOOL_NAMES[$tool_index]}
  mkdir -p "$parent"
  temp_dir=$(mktemp -d "$parent/.${TOOL_VERSIONS[$tool_index]}.tmp.XXXXXX")
  register_cleanup_path "$temp_dir"
  download=$temp_dir/artifact
  extract_dir=$temp_dir/extract
  stage_dir=$temp_dir/stage
  if ! download_artifact "${ARTIFACT_URLS[$artifact_index]}" "$download"; then
    safe_remove "$temp_dir"
    return 1
  fi
  actual_sha=$(sha256_file "$download")
  if [ "$actual_sha" != "${ARTIFACT_SHAS[$artifact_index]}" ]; then
    printf 'workspace: checksum mismatch for %s %s: expected %s, got %s\n' \
      "${TOOL_NAMES[$tool_index]}" "${TOOL_VERSIONS[$tool_index]}" \
      "${ARTIFACT_SHAS[$artifact_index]}" "$actual_sha" >&2
    safe_remove "$temp_dir"
    return 1
  fi
  if ! archive_is_safe "${ARTIFACT_ARCHIVES[$artifact_index]}" "$download" "$temp_dir"; then
    printf 'workspace: unsafe or unsupported archive contents for %s %s\n' \
      "${TOOL_NAMES[$tool_index]}" "${TOOL_VERSIONS[$tool_index]}" >&2
    safe_remove "$temp_dir"
    return 1
  fi
  if ! extract_artifact "${ARTIFACT_ARCHIVES[$artifact_index]}" "$download" "$extract_dir" \
    "${ARTIFACT_PAYLOADS[$artifact_index]}"; then
    printf 'workspace: failed to extract %s %s\n' "${TOOL_NAMES[$tool_index]}" "${TOOL_VERSIONS[$tool_index]}" >&2
    safe_remove "$temp_dir"
    return 1
  fi
  if ! stage_payloads "$tool_index" "$artifact_index" "$extract_dir" "$stage_dir" || \
    ! verify_tool_at "$tool_index" "$stage_dir" "$artifact_index"; then
    printf 'workspace: artifact payload failed verification for %s %s\n' \
      "${TOOL_NAMES[$tool_index]}" "${TOOL_VERSIONS[$tool_index]}" >&2
    safe_remove "$temp_dir"
    return 1
  fi
  if [ -e "$destination" ] || [ -L "$destination" ]; then
    backup=$parent/.${TOOL_VERSIONS[$tool_index]}.old.$$
    [ ! -e "$backup" ] && [ ! -L "$backup" ] || safe_remove "$backup"
    ACTIVE_REPLACEMENT_DESTINATION=$destination
    ACTIVE_REPLACEMENT_BACKUP=$backup
    ACTIVE_REPLACEMENT_PUBLISHED=0
    if ! mv "$destination" "$backup"; then
      clear_active_replacement
      safe_remove "$temp_dir"
      return 1
    fi
  else
    ACTIVE_REPLACEMENT_DESTINATION=$destination
    ACTIVE_REPLACEMENT_BACKUP=
    ACTIVE_REPLACEMENT_PUBLISHED=0
  fi
  if ! mv "$stage_dir" "$destination"; then
    rollback_active_replacement
    safe_remove "$temp_dir"
    return 1
  fi
  ACTIVE_REPLACEMENT_PUBLISHED=1
  if ! verify_tool_at "$tool_index" "$destination" "$artifact_index"; then
    rollback_active_replacement
    safe_remove "$temp_dir"
    return 1
  fi
  [ -z "$backup" ] || safe_remove "$backup"
  clear_active_replacement
  safe_remove "$temp_dir"
  say "workspace: installed ${TOOL_NAMES[$tool_index]} ${TOOL_VERSIONS[$tool_index]}"
}

publish_verified_stage() {
  local stage_dir=$1 destination=$2 verifier=$3 parent backup=
  shift 3
  parent=${destination%/*}
  if [ -e "$destination" ] || [ -L "$destination" ]; then
    backup=$parent/.${destination##*/}.old.$$
    [ ! -e "$backup" ] && [ ! -L "$backup" ] || safe_remove "$backup"
    ACTIVE_REPLACEMENT_DESTINATION=$destination
    ACTIVE_REPLACEMENT_BACKUP=$backup
    ACTIVE_REPLACEMENT_PUBLISHED=0
    if ! mv "$destination" "$backup"; then
      clear_active_replacement
      return 1
    fi
  else
    ACTIVE_REPLACEMENT_DESTINATION=$destination
    ACTIVE_REPLACEMENT_BACKUP=
    ACTIVE_REPLACEMENT_PUBLISHED=0
  fi
  if ! mv "$stage_dir" "$destination"; then
    rollback_active_replacement
    return 1
  fi
  ACTIVE_REPLACEMENT_PUBLISHED=1
  if ! "$verifier" "$destination" "$@"; then
    rollback_active_replacement
    return 1
  fi
  [ -z "$backup" ] || safe_remove "$backup"
  clear_active_replacement
}

install_uv_python() {
  local pin_index=$1 artifact_index=$2 input_index=$3 uv_tool_index uv destination parent
  local temp_dir uv_install stage_dir candidate runtime= runtime_count=0
  destination=$TOOLS_ROOT/python/${PIN_VERSIONS[$pin_index]}
  if verify_python_at "$destination" "$pin_index" "$artifact_index" "$input_index"; then
    say "workspace: reuse python ${PIN_VERSIONS[$pin_index]}"
    return 0
  fi
  if [ "$OFFLINE" = 1 ]; then
    printf 'workspace: offline cache miss for python %s at %s\n' \
      "${PIN_VERSIONS[$pin_index]}" "$destination" >&2
    return 1
  fi
  uv_tool_index=$(tool_index_for uv) || die "lock: managed uv is required to install Python"
  uv=$(tool_dir "$uv_tool_index")/bin/uv
  [ -x "$uv" ] || die "managed uv is missing: $uv"
  parent=$TOOLS_ROOT/python
  mkdir -p "$parent" "$CACHE_ROOT/uv"
  temp_dir=$(mktemp -d "$parent/.${PIN_VERSIONS[$pin_index]}.tmp.XXXXXX")
  register_cleanup_path "$temp_dir"
  uv_install=$temp_dir/uv-install
  stage_dir=$temp_dir/stage
  if ! UV_CACHE_DIR=$CACHE_ROOT/uv UV_OFFLINE=0 \
    "$uv" python install --no-bin --managed-python --no-config \
      --python-downloads-json-url "file://$ROOT/${INPUT_PATHS[$input_index]}" \
      --install-dir "$uv_install" "${PIN_VERSIONS[$pin_index]}"; then
    printf 'workspace: failed to install python %s with managed uv\n' \
      "${PIN_VERSIONS[$pin_index]}" >&2
    safe_remove "$temp_dir"
    return 1
  fi
  for candidate in "$uv_install"/*; do
    [ -d "$candidate" ] || continue
    [ ! -L "$candidate" ] || continue
    [ -x "$candidate/bin/python3.13" ] || continue
    runtime=$candidate
    runtime_count=$((runtime_count + 1))
  done
  if [ "$runtime_count" -ne 1 ]; then
    printf 'workspace: managed Python install produced %s runtimes; expected exactly one\n' \
      "$runtime_count" >&2
    safe_remove "$temp_dir"
    return 1
  fi
  mkdir -p "$stage_dir"
  if ! mv "$runtime" "$stage_dir/runtime"; then
    safe_remove "$temp_dir"
    return 1
  fi
  python_receipt_for "$pin_index" "$artifact_index" "$input_index" \
    >"$stage_dir/.harness-workspace-receipt"
  if ! verify_python_at "$stage_dir" "$pin_index" "$artifact_index" "$input_index"; then
    printf 'workspace: managed Python payload failed verification for %s\n' \
      "${PIN_VERSIONS[$pin_index]}" >&2
    safe_remove "$temp_dir"
    return 1
  fi
  if ! publish_verified_stage "$stage_dir" "$destination" verify_python_at \
    "$pin_index" "$artifact_index" "$input_index"; then
    safe_remove "$temp_dir"
    return 1
  fi
  safe_remove "$temp_dir"
  say "workspace: installed python ${PIN_VERSIONS[$pin_index]}"
}

install_python_cli() {
  local pin_index=$1 artifact_index=$2 input_index=$3 python_pin=$4 uv_tool=$5
  local name version source module destination parent temp_dir stage_dir uv python offline_args=()
  name=${PIN_NAMES[$pin_index]}
  version=${PIN_VERSIONS[$pin_index]}
  source=${PIN_SOURCES[$pin_index]}
  [ "$source" = "$name==$version" ] || die "lock: invalid Python requirement '$source' for $name"
  module=$(python_cli_module "$name") || die "unsupported managed Python CLI: $name"
  destination=$TOOLS_ROOT/$name/$version
  if verify_python_cli_at "$destination" "$pin_index" "$artifact_index" "$input_index" \
    "$python_pin" "$uv_tool"; then
    say "workspace: reuse $name $version"
    return 0
  fi
  uv=$(tool_dir "$uv_tool")/bin/uv
  python=$TOOLS_ROOT/python/${PIN_VERSIONS[$python_pin]}/runtime/bin/python3.13
  [ -x "$uv" ] || die "managed uv is missing: $uv"
  [ -x "$python" ] || die "managed Python is missing: $python"
  [ "$OFFLINE" = 0 ] || offline_args=(--offline)
  parent=$TOOLS_ROOT/$name
  mkdir -p "$parent" "$CACHE_ROOT/uv"
  temp_dir=$(mktemp -d "$parent/.$version.tmp.XXXXXX")
  register_cleanup_path "$temp_dir"
  stage_dir=$temp_dir/stage
  mkdir -p "$stage_dir"
  if ! UV_CACHE_DIR=$CACHE_ROOT/uv \
    "$uv" venv "${offline_args[@]}" --no-config --no-python-downloads \
      --python "$python" "$stage_dir/runtime"; then
    if [ "$OFFLINE" = 1 ]; then
      printf 'workspace: offline cache miss for %s %s at %s\n' \
        "$name" "$version" "$destination" >&2
    else
      printf 'workspace: failed to create managed environment for %s %s\n' "$name" "$version" >&2
    fi
    safe_remove "$temp_dir"
    return 1
  fi
  if ! UV_CACHE_DIR=$CACHE_ROOT/uv \
    "$uv" pip install "${offline_args[@]}" --no-config --no-python-downloads \
      --python "$stage_dir/runtime/bin/python" --require-hashes --no-build \
      --constraint "$ROOT/${INPUT_PATHS[$input_index]}" "$source"; then
    if [ "$OFFLINE" = 1 ]; then
      printf 'workspace: offline cache miss for %s %s at %s\n' \
        "$name" "$version" "$destination" >&2
    else
      printf 'workspace: failed to install managed Python CLI %s %s\n' "$name" "$version" >&2
    fi
    safe_remove "$temp_dir"
    return 1
  fi
  mkdir -p "$stage_dir/bin"
  python_cli_wrapper_for "$module" >"$stage_dir/bin/$name"
  chmod 0755 "$stage_dir/bin/$name"
  python_cli_receipt_for "$pin_index" "$artifact_index" "$input_index" "$python_pin" "$uv_tool" \
    >"$stage_dir/.harness-workspace-receipt"
  if ! verify_python_cli_at "$stage_dir" "$pin_index" "$artifact_index" "$input_index" \
    "$python_pin" "$uv_tool"; then
    printf 'workspace: managed Python CLI failed verification for %s %s\n' "$name" "$version" >&2
    safe_remove "$temp_dir"
    return 1
  fi
  if ! publish_verified_stage "$stage_dir" "$destination" verify_python_cli_at \
    "$pin_index" "$artifact_index" "$input_index" "$python_pin" "$uv_tool"; then
    safe_remove "$temp_dir"
    return 1
  fi
  safe_remove "$temp_dir"
  say "workspace: installed $name $version"
}

verify_selected_python_clis() {
  local i artifact_index input_index python_pin uv_tool destination failed=0
  python_pin=$(pin_index_for python) || die "lock: managed Python pin is required"
  uv_tool=$(tool_index_for uv) || die "lock: managed uv is required"
  input_index=$(input_index_for common uv .harness/python-tools.lock) || \
    die "lock: Python tools input is required"
  for ((i = 0; i < ${#PIN_NAMES[@]}; i += 1)); do
    [ "${PIN_KINDS[$i]}" = pypi-wheel ] || continue
    profile_selected "${PIN_PROFILES[$i]}" || continue
    artifact_index=$(artifact_index_for "${PIN_NAMES[$i]}" any) || \
      die "lock: missing root artifact for ${PIN_NAMES[$i]}"
    destination=$TOOLS_ROOT/${PIN_NAMES[$i]}/${PIN_VERSIONS[$i]}
    if ! verify_python_cli_at "$destination" "$i" "$artifact_index" "$input_index" \
      "$python_pin" "$uv_tool"; then
      printf 'workspace: managed tool is missing or invalid: %s %s (%s)\n' \
        "${PIN_NAMES[$i]}" "${PIN_VERSIONS[$i]}" "$destination" >&2
      failed=1
    fi
  done
  [ "$failed" -eq 0 ]
}

install_selected_python_clis() {
  local i artifact_index input_index python_pin uv_tool
  python_pin=$(pin_index_for python) || die "lock: managed Python pin is required"
  uv_tool=$(tool_index_for uv) || die "lock: managed uv is required"
  input_index=$(input_index_for common uv .harness/python-tools.lock) || \
    die "lock: Python tools input is required"
  for ((i = 0; i < ${#PIN_NAMES[@]}; i += 1)); do
    [ "${PIN_KINDS[$i]}" = pypi-wheel ] || continue
    profile_selected "${PIN_PROFILES[$i]}" || continue
    artifact_index=$(artifact_index_for "${PIN_NAMES[$i]}" any) || \
      die "lock: missing root artifact for ${PIN_NAMES[$i]}"
    install_python_cli "$i" "$artifact_index" "$input_index" "$python_pin" "$uv_tool"
  done
}

install_knip() {
  local pin_index=$1 artifact_index=$2 package_input=$3 lock_input=$4 bun_tool=$5
  local version source bun destination parent temp_dir stage_dir offline_args=()
  version=${PIN_VERSIONS[$pin_index]}
  source=${PIN_SOURCES[$pin_index]}
  [ "$source" = "knip@$version" ] || die "lock: invalid Bun requirement '$source' for knip"
  bun=$(tool_dir "$bun_tool")/bin/bun
  destination=$TOOLS_ROOT/knip/$version
  if verify_knip_at "$destination" "$pin_index" "$artifact_index" "$package_input" \
    "$lock_input" "$bun_tool"; then
    say "workspace: reuse knip $version"
    return 0
  fi
  [ -x "$bun" ] || die "managed Bun is missing: $bun"
  [ "$OFFLINE" = 0 ] || offline_args=(--offline)
  parent=$TOOLS_ROOT/knip
  mkdir -p "$parent" "$CACHE_ROOT/bun"
  temp_dir=$(mktemp -d "$parent/.$version.tmp.XXXXXX")
  register_cleanup_path "$temp_dir"
  stage_dir=$temp_dir/stage
  mkdir -p "$stage_dir/runtime"
  cp "$ROOT/${INPUT_PATHS[$package_input]}" "$stage_dir/runtime/package.json"
  cp "$ROOT/${INPUT_PATHS[$lock_input]}" "$stage_dir/runtime/bun.lock"
  if ! BUN_INSTALL_CACHE_DIR=$CACHE_ROOT/bun \
    "$bun" install "${offline_args[@]}" --cwd "$stage_dir/runtime" --frozen-lockfile \
      --ignore-scripts --backend copyfile --linker hoisted --no-progress --no-summary; then
    if [ "$OFFLINE" = 1 ]; then
      printf 'workspace: offline cache miss for knip %s at %s\n' "$version" "$destination" >&2
    else
      printf 'workspace: failed to install managed Knip %s\n' "$version" >&2
    fi
    safe_remove "$temp_dir"
    return 1
  fi
  mkdir -p "$stage_dir/bin"
  knip_wrapper_for "$bun" >"$stage_dir/bin/knip"
  chmod 0755 "$stage_dir/bin/knip"
  knip_receipt_for "$pin_index" "$artifact_index" "$package_input" "$lock_input" "$bun_tool" \
    >"$stage_dir/.harness-workspace-receipt"
  if ! verify_knip_at "$stage_dir" "$pin_index" "$artifact_index" "$package_input" \
    "$lock_input" "$bun_tool"; then
    printf 'workspace: managed Knip failed verification for %s\n' "$version" >&2
    safe_remove "$temp_dir"
    return 1
  fi
  if ! publish_verified_stage "$stage_dir" "$destination" verify_knip_at \
    "$pin_index" "$artifact_index" "$package_input" "$lock_input" "$bun_tool"; then
    safe_remove "$temp_dir"
    return 1
  fi
  safe_remove "$temp_dir"
  say "workspace: installed knip $version"
}

knip_components() {
  KNIP_PIN=$(pin_index_for knip) || return 1
  KNIP_ARTIFACT=$(artifact_index_for knip any) || die "lock: missing root artifact for knip"
  KNIP_PACKAGE_INPUT=$(input_index_for bun bun .harness/bun-tools/package.json) || \
    die "lock: Bun tool package input is required"
  KNIP_LOCK_INPUT=$(input_index_for bun bun .harness/bun-tools/bun.lock) || \
    die "lock: Bun tools lock input is required"
  KNIP_BUN_TOOL=$(tool_index_for bun) || die "lock: managed Bun is required"
}

verify_selected_knip() {
  local destination
  knip_components || return 0
  profile_selected "${PIN_PROFILES[$KNIP_PIN]}" || return 0
  destination=$TOOLS_ROOT/knip/${PIN_VERSIONS[$KNIP_PIN]}
  verify_knip_at "$destination" "$KNIP_PIN" "$KNIP_ARTIFACT" "$KNIP_PACKAGE_INPUT" \
    "$KNIP_LOCK_INPUT" "$KNIP_BUN_TOOL"
}

install_selected_knip() {
  knip_components || return 0
  profile_selected "${PIN_PROFILES[$KNIP_PIN]}" || return 0
  install_knip "$KNIP_PIN" "$KNIP_ARTIFACT" "$KNIP_PACKAGE_INPUT" "$KNIP_LOCK_INPUT" \
    "$KNIP_BUN_TOOL"
}

go_components() {
  GO_MOD_INPUT=$(input_index_for go go .harness/go-tools/go.mod) || \
    die "lock: Go tools module input is required"
  GO_SUM_INPUT=$(input_index_for go go .harness/go-tools/go.sum) || \
    die "lock: Go tools checksum input is required"
  GO_BUILDER_TOOL=$(tool_index_for go) || die "lock: managed Go is required"
}

install_go_tool() {
  local pin_index=$1 mod_input=$2 sum_input=$3 go_tool=$4
  local name version package go goroot destination parent temp_dir tools_dir stage_dir go_proxy
  name=${PIN_NAMES[$pin_index]}
  version=${PIN_VERSIONS[$pin_index]}
  package=$(go_package_for "$pin_index") || die "lock: invalid Go package source for $name"
  go=$(tool_dir "$go_tool")/go/bin/go
  goroot=$(tool_dir "$go_tool")/go
  destination=$TOOLS_ROOT/$name/$version
  if verify_go_tool_at "$destination" "$pin_index" "$mod_input" "$sum_input" "$go_tool"; then
    say "workspace: reuse $name $version"
    return 0
  fi
  [ -x "$go" ] || die "managed Go is missing: $go"
  if [ "$OFFLINE" = 1 ]; then
    go_proxy=off
  else
    go_proxy=https://proxy.golang.org,direct
  fi
  parent=$TOOLS_ROOT/$name
  mkdir -p "$parent" "$CACHE_ROOT/go/mod" "$CACHE_ROOT/go/build"
  temp_dir=$(mktemp -d "$parent/.$version.tmp.XXXXXX")
  register_cleanup_path "$temp_dir"
  tools_dir=$temp_dir/tools
  stage_dir=$temp_dir/stage
  mkdir -p "$tools_dir" "$stage_dir/bin"
  cp "$ROOT/${INPUT_PATHS[$mod_input]}" "$tools_dir/go.mod"
  cp "$ROOT/${INPUT_PATHS[$sum_input]}" "$tools_dir/go.sum"
  if ! (
    cd "$tools_dir"
    GOROOT=$goroot GOBIN=$stage_dir/bin GOMODCACHE=$CACHE_ROOT/go/mod \
      GOCACHE=$CACHE_ROOT/go/build GOENV=off GOWORK=off GOTOOLCHAIN=local \
      GOFLAGS=-mod=readonly CGO_ENABLED=0 GOPROXY=$go_proxy GOSUMDB=sum.golang.org \
      GOPRIVATE= GONOSUMDB= GOINSECURE= \
      "$go" install -mod=readonly "$package"
  ); then
    if [ "$OFFLINE" = 1 ]; then
      printf 'workspace: offline cache miss for %s %s at %s\n' "$name" "$version" "$destination" >&2
    else
      printf 'workspace: failed to build managed Go tool %s %s\n' "$name" "$version" >&2
    fi
    safe_remove "$temp_dir"
    return 1
  fi
  go_tool_receipt_for "$pin_index" "$mod_input" "$sum_input" "$go_tool" \
    >"$stage_dir/.harness-workspace-receipt"
  if ! verify_go_tool_at "$stage_dir" "$pin_index" "$mod_input" "$sum_input" "$go_tool"; then
    printf 'workspace: managed Go tool failed verification for %s %s\n' "$name" "$version" >&2
    safe_remove "$temp_dir"
    return 1
  fi
  if ! publish_verified_stage "$stage_dir" "$destination" verify_go_tool_at \
    "$pin_index" "$mod_input" "$sum_input" "$go_tool"; then
    safe_remove "$temp_dir"
    return 1
  fi
  safe_remove "$temp_dir"
  say "workspace: installed $name $version"
}

verify_selected_go_tools() {
  local i destination failed=0
  go_components
  for ((i = 0; i < ${#PIN_NAMES[@]}; i += 1)); do
    [ "${PIN_KINDS[$i]}" = go-module ] || continue
    profile_selected "${PIN_PROFILES[$i]}" || continue
    destination=$TOOLS_ROOT/${PIN_NAMES[$i]}/${PIN_VERSIONS[$i]}
    if ! verify_go_tool_at "$destination" "$i" "$GO_MOD_INPUT" "$GO_SUM_INPUT" "$GO_BUILDER_TOOL"; then
      printf 'workspace: managed tool is missing or invalid: %s %s (%s)\n' \
        "${PIN_NAMES[$i]}" "${PIN_VERSIONS[$i]}" "$destination" >&2
      failed=1
    fi
  done
  [ "$failed" -eq 0 ]
}

install_selected_go_tools() {
  local i
  go_components
  for ((i = 0; i < ${#PIN_NAMES[@]}; i += 1)); do
    [ "${PIN_KINDS[$i]}" = go-module ] || continue
    profile_selected "${PIN_PROFILES[$i]}" || continue
    install_go_tool "$i" "$GO_MOD_INPUT" "$GO_SUM_INPUT" "$GO_BUILDER_TOOL"
  done
}

rust_components() {
  RUST_PIN=$(pin_index_for rust) || die "lock: managed Rust pin is required"
  RUST_DIST_INPUT=$(input_index_for rust cargo .harness/rust-dist.lock) || \
    die "lock: Rust distribution input is required"
  RUSTUP_TOOL=$(tool_index_for rustup) || die "lock: managed rustup is required"
  CARGO_MODULES_PIN=$(pin_index_for cargo-modules) || \
    die "lock: managed cargo-modules pin is required"
  CARGO_MODULES_ARTIFACT=$(artifact_index_for cargo-modules any) || \
    die "lock: cargo-modules artifact is required"
  CARGO_MODULES_LOCK_INPUT=$(input_index_for rust cargo .harness/cargo-modules.lock) || \
    die "lock: cargo-modules lock input is required"
  [ "${PIN_SOURCES[$RUST_PIN]}" = "${PIN_VERSIONS[$RUST_PIN]}" ] || \
    die "lock: invalid Rust source '${PIN_SOURCES[$RUST_PIN]}'"
  [ "${PIN_SOURCES[$CARGO_MODULES_PIN]}" = \
    "cargo-modules@${PIN_VERSIONS[$CARGO_MODULES_PIN]}" ] || \
    die "lock: invalid cargo-modules source '${PIN_SOURCES[$CARGO_MODULES_PIN]}'"
  validate_rust_dist_closure "$RUST_PIN" "$RUST_DIST_INPUT" || \
    die "lock: invalid Rust distribution closure for $PLATFORM"
}

load_rust_manifest_record() {
  local input_index=$1 record first second third fourth extra found=0
  RUST_MANIFEST_URL=
  RUST_MANIFEST_SHA=
  RUST_MANIFEST_SIDECAR_URL=
  RUST_MANIFEST_SIDECAR_SHA=
  while IFS=$'\t' read -r record first second third fourth extra || [ -n "$record" ]; do
    [ "$record" = manifest ] || continue
    [ -z "${extra:-}" ] || return 1
    RUST_MANIFEST_URL=$first
    RUST_MANIFEST_SHA=$second
    RUST_MANIFEST_SIDECAR_URL=$third
    RUST_MANIFEST_SIDECAR_SHA=$fourth
    found=$((found + 1))
  done <"$ROOT/${INPUT_PATHS[$input_index]}"
  [ "$found" -eq 1 ]
}

install_rust_toolchain() {
  local pin_index=$1 input_index=$2 rustup_tool=$3 version target toolchain rustup_init
  local destination parent temp_dir stage_dir mirror manifest_cache sidecar_cache
  local record platform component component_target url sha extra count=0 install_log
  version=${PIN_VERSIONS[$pin_index]}
  target=$(rust_target_for_platform "$PLATFORM") || die "unsupported Rust platform: $PLATFORM"
  toolchain=$version-$target
  destination=$TOOLS_ROOT/rust/$version
  if verify_rust_at "$destination" "$pin_index" "$input_index" "$rustup_tool"; then
    say "workspace: reuse rust $version"
    return 0
  fi
  rustup_init=$(tool_dir "$rustup_tool")/bin/rustup-init
  [ -x "$rustup_init" ] || die "managed rustup-init is missing: $rustup_init"
  parent=$TOOLS_ROOT/rust
  mkdir -p "$parent" "$CACHE_ROOT/rust/dist"
  temp_dir=$(mktemp -d "$parent/.$version.tmp.XXXXXX")
  register_cleanup_path "$temp_dir"
  stage_dir=$temp_dir/stage
  mirror=$temp_dir/mirror
  install_log=$temp_dir/rustup.log
  mkdir -p "$stage_dir/cargo" "$mirror/dist"
  load_rust_manifest_record "$input_index" || die "lock: invalid Rust manifest record"
  ensure_cached_artifact "Rust $version channel manifest" "$RUST_MANIFEST_URL" \
    "$RUST_MANIFEST_SHA" rust/dist .toml || { safe_remove "$temp_dir"; return 1; }
  manifest_cache=$CACHED_ARTIFACT_PATH
  ensure_cached_artifact "Rust $version channel manifest sidecar" "$RUST_MANIFEST_SIDECAR_URL" \
    "$RUST_MANIFEST_SIDECAR_SHA" rust/dist .sha256 || { safe_remove "$temp_dir"; return 1; }
  sidecar_cache=$CACHED_ARTIFACT_PATH
  cp "$manifest_cache" "$mirror/dist/channel-rust-$version.toml"
  cp "$sidecar_cache" "$mirror/dist/channel-rust-$version.toml.sha256"
  while IFS=$'\t' read -r record platform component component_target url sha extra || \
    [ -n "$record" ]; do
    [ "$record" = component ] && [ "$platform" = "$PLATFORM" ] || continue
    [ -z "${extra:-}" ] || { safe_remove "$temp_dir"; return 1; }
    ensure_cached_artifact "Rust $version component $component" "$url" "$sha" \
      rust/dist '' || { safe_remove "$temp_dir"; return 1; }
    count=$((count + 1))
  done <"$ROOT/${INPUT_PATHS[$input_index]}"
  [ "$count" -eq 6 ] || die "lock: expected six Rust components for $PLATFORM"
  if ! RUSTUP_HOME=$stage_dir/rustup CARGO_HOME=$stage_dir/cargo \
    RUSTUP_AUTO_INSTALL=0 RUSTUP_INIT_SKIP_PATH_CHECK=yes RUSTUP_INIT_SKIP_SUDO_CHECK=yes \
    "$rustup_init" -y --no-modify-path --default-host "$target" \
      --default-toolchain none --profile minimal >"$install_log" 2>&1; then
    printf 'workspace: failed to initialize managed rustup %s\n' \
      "${TOOL_VERSIONS[$rustup_tool]}" >&2
    cat "$install_log" >&2
    safe_remove "$temp_dir"
    return 1
  fi
  mkdir -p "$stage_dir/rustup/downloads"
  while IFS=$'\t' read -r record platform component component_target url sha extra || \
    [ -n "$record" ]; do
    [ "$record" = component ] && [ "$platform" = "$PLATFORM" ] || continue
    cp "$CACHE_ROOT/rust/dist/$sha" "$stage_dir/rustup/downloads/$sha"
  done <"$ROOT/${INPUT_PATHS[$input_index]}"
  if ! RUSTUP_HOME=$stage_dir/rustup CARGO_HOME=$stage_dir/cargo \
    RUSTUP_DIST_SERVER=file://$mirror RUSTUP_UPDATE_ROOT=file://$mirror/rustup \
    RUSTUP_AUTO_INSTALL=0 "$stage_dir/cargo/bin/rustup" toolchain install "$toolchain" \
      --profile minimal --component clippy --component rustfmt \
      --component llvm-tools-preview --no-self-update >>"$install_log" 2>&1; then
    printf 'workspace: failed to install managed Rust toolchain %s\n' "$toolchain" >&2
    cat "$install_log" >&2
    safe_remove "$temp_dir"
    return 1
  fi
  if ! RUSTUP_HOME=$stage_dir/rustup CARGO_HOME=$stage_dir/cargo RUSTUP_AUTO_INSTALL=0 \
    "$stage_dir/cargo/bin/rustup" default "$toolchain" >>"$install_log" 2>&1; then
    printf 'workspace: failed to select managed Rust toolchain %s\n' "$toolchain" >&2
    cat "$install_log" >&2
    safe_remove "$temp_dir"
    return 1
  fi
  rust_receipt_for "$pin_index" "$input_index" "$rustup_tool" \
    >"$stage_dir/.harness-workspace-receipt"
  if ! verify_rust_at "$stage_dir" "$pin_index" "$input_index" "$rustup_tool"; then
    printf 'workspace: managed Rust toolchain failed verification for %s\n' "$toolchain" >&2
    safe_remove "$temp_dir"
    return 1
  fi
  if ! publish_verified_stage "$stage_dir" "$destination" verify_rust_at \
    "$pin_index" "$input_index" "$rustup_tool"; then
    safe_remove "$temp_dir"
    return 1
  fi
  safe_remove "$temp_dir"
  say "workspace: installed rust $version"
}

install_cargo_modules() {
  local pin_index=$1 artifact_index=$2 lock_input=$3 rust_pin=$4 rust_input=$5 rustup_tool=$6
  local version target toolchain rust_base cargo destination parent temp_dir stage_dir extract_dir
  local source_dir crate_cache build_log cargo_offline=false offline_args=()
  version=${PIN_VERSIONS[$pin_index]}
  target=$(rust_target_for_platform "$PLATFORM") || die "unsupported Rust platform: $PLATFORM"
  toolchain=${PIN_VERSIONS[$rust_pin]}-$target
  rust_base=$TOOLS_ROOT/rust/${PIN_VERSIONS[$rust_pin]}
  cargo=$rust_base/cargo/bin/cargo
  destination=$TOOLS_ROOT/cargo-modules/$version
  if verify_cargo_modules_at "$destination" "$pin_index" "$artifact_index" "$lock_input" \
    "$rust_pin" "$rust_input"; then
    say "workspace: reuse cargo-modules $version"
    return 0
  fi
  verify_rust_at "$rust_base" "$rust_pin" "$rust_input" "$rustup_tool" || \
    die "managed Rust toolchain is missing or invalid: $rust_base"
  [ -x "$cargo" ] || die "managed Cargo is missing: $cargo"
  ensure_cached_artifact "cargo-modules $version crate" \
    "${ARTIFACT_URLS[$artifact_index]}" "${ARTIFACT_SHAS[$artifact_index]}" \
    rust/crates .crate || return 1
  crate_cache=$CACHED_ARTIFACT_PATH
  parent=$TOOLS_ROOT/cargo-modules
  mkdir -p "$parent" "$CACHE_ROOT/cargo" "$CACHE_ROOT/cargo/target"
  temp_dir=$(mktemp -d "$parent/.$version.tmp.XXXXXX")
  register_cleanup_path "$temp_dir"
  stage_dir=$temp_dir/stage
  extract_dir=$temp_dir/source
  build_log=$temp_dir/cargo.log
  mkdir -p "$stage_dir" "$extract_dir"
  if ! archive_is_safe crate "$crate_cache" "$extract_dir"; then
    printf 'workspace: unsafe cargo-modules crate contents for %s\n' "$version" >&2
    safe_remove "$temp_dir"
    return 1
  fi
  if ! tar -xzf "$crate_cache" -C "$extract_dir"; then
    printf 'workspace: failed to extract cargo-modules crate %s\n' "$version" >&2
    safe_remove "$temp_dir"
    return 1
  fi
  source_dir=$extract_dir/cargo-modules-$version
  [ -f "$source_dir/Cargo.toml" ] && [ -f "$source_dir/Cargo.lock" ] || {
    printf 'workspace: cargo-modules crate has an unexpected root for %s\n' "$version" >&2
    safe_remove "$temp_dir"
    return 1
  }
  if ! cmp -s "$source_dir/Cargo.lock" "$ROOT/${INPUT_PATHS[$lock_input]}"; then
    printf 'workspace: cargo-modules crate lock differs from %s\n' \
      "${INPUT_PATHS[$lock_input]}" >&2
    safe_remove "$temp_dir"
    return 1
  fi
  if [ "$OFFLINE" = 1 ]; then
    cargo_offline=true
    offline_args=(--offline)
  fi
  if ! PATH=$rust_base/cargo/bin:$PATH \
    RUSTUP_HOME=$rust_base/rustup CARGO_HOME=$CACHE_ROOT/cargo \
    RUSTUP_TOOLCHAIN=$toolchain RUSTUP_AUTO_INSTALL=0 RUSTC=$rust_base/cargo/bin/rustc \
    RUSTDOC=$rust_base/cargo/bin/rustdoc RUSTC_WRAPPER= RUSTC_WORKSPACE_WRAPPER= \
    RUSTFLAGS= CARGO_ENCODED_RUSTFLAGS= CARGO_NET_OFFLINE=$cargo_offline \
    CARGO_REGISTRIES_CRATES_IO_PROTOCOL=sparse \
    CARGO_TARGET_DIR=$CACHE_ROOT/cargo/target/cargo-modules-$version-$PLATFORM \
    "$cargo" install "${offline_args[@]}" --locked --path "$source_dir" \
      --root "$stage_dir" --no-track --force >"$build_log" 2>&1; then
    if [ "$OFFLINE" = 1 ]; then
      printf 'workspace: offline cache miss while building cargo-modules %s\n' "$version" >&2
    else
      printf 'workspace: failed to build managed cargo-modules %s\n' "$version" >&2
    fi
    cat "$build_log" >&2
    safe_remove "$temp_dir"
    return 1
  fi
  cargo_modules_receipt_for "$pin_index" "$artifact_index" "$lock_input" \
    "$rust_pin" "$rust_input" >"$stage_dir/.harness-workspace-receipt"
  if ! verify_cargo_modules_at "$stage_dir" "$pin_index" "$artifact_index" "$lock_input" \
    "$rust_pin" "$rust_input"; then
    printf 'workspace: managed cargo-modules failed verification for %s\n' "$version" >&2
    safe_remove "$temp_dir"
    return 1
  fi
  if ! publish_verified_stage "$stage_dir" "$destination" verify_cargo_modules_at \
    "$pin_index" "$artifact_index" "$lock_input" "$rust_pin" "$rust_input"; then
    safe_remove "$temp_dir"
    return 1
  fi
  safe_remove "$temp_dir"
  say "workspace: installed cargo-modules $version"
}

verify_selected_rust() {
  local rust_destination cargo_modules_destination failed=0
  rust_components
  rust_destination=$TOOLS_ROOT/rust/${PIN_VERSIONS[$RUST_PIN]}
  if ! verify_rust_at "$rust_destination" "$RUST_PIN" "$RUST_DIST_INPUT" "$RUSTUP_TOOL"; then
    printf 'workspace: managed tool is missing or invalid: rust %s (%s)\n' \
      "${PIN_VERSIONS[$RUST_PIN]}" "$rust_destination" >&2
    failed=1
  fi
  cargo_modules_destination=$TOOLS_ROOT/cargo-modules/${PIN_VERSIONS[$CARGO_MODULES_PIN]}
  if ! verify_cargo_modules_at "$cargo_modules_destination" "$CARGO_MODULES_PIN" \
    "$CARGO_MODULES_ARTIFACT" "$CARGO_MODULES_LOCK_INPUT" "$RUST_PIN" "$RUST_DIST_INPUT"; then
    printf 'workspace: managed tool is missing or invalid: cargo-modules %s (%s)\n' \
      "${PIN_VERSIONS[$CARGO_MODULES_PIN]}" "$cargo_modules_destination" >&2
    failed=1
  fi
  [ "$failed" -eq 0 ]
}

install_selected_rust() {
  rust_components
  install_rust_toolchain "$RUST_PIN" "$RUST_DIST_INPUT" "$RUSTUP_TOOL"
  install_cargo_modules "$CARGO_MODULES_PIN" "$CARGO_MODULES_ARTIFACT" \
    "$CARGO_MODULES_LOCK_INPUT" "$RUST_PIN" "$RUST_DIST_INPUT" "$RUSTUP_TOOL"
}

verify_selected_tools() {
  local i artifact_index destination failed=0 python_pin python_input
  detect_platform || die "$PLATFORM_ERROR"
  for ((i = 0; i < ${#TOOL_NAMES[@]}; i += 1)); do
    profile_selected "${TOOL_PROFILES[$i]}" || continue
    artifact_index=$(artifact_index_for "${TOOL_NAMES[$i]}" "$PLATFORM") || die "missing artifact selection"
    destination=$(tool_dir "$i")
    if ! verify_tool_at "$i" "$destination" "$artifact_index"; then
      printf 'workspace: managed tool is missing or invalid: %s %s (%s)\n' \
        "${TOOL_NAMES[$i]}" "${TOOL_VERSIONS[$i]}" "$destination" >&2
      failed=1
    fi
  done
  if [ "$MANIFEST_FORMAT" = 2 ] && python_pin=$(pin_index_for python); then
    python_input=$(input_index_for common uv .harness/python-downloads.json) || \
      die "lock: Python downloads input is required"
    artifact_index=$(artifact_index_for python "$PLATFORM") || \
      die "lock: missing Python artifact for $PLATFORM"
    destination=$TOOLS_ROOT/python/${PIN_VERSIONS[$python_pin]}
    if ! verify_python_at "$destination" "$python_pin" "$artifact_index" "$python_input"; then
      printf 'workspace: managed tool is missing or invalid: python %s (%s)\n' \
        "${PIN_VERSIONS[$python_pin]}" "$destination" >&2
      failed=1
    fi
  fi
  if [ "$MANIFEST_FORMAT" = 2 ] && selected_pin_kind_exists pypi-wheel; then
    verify_selected_python_clis || failed=1
  fi
  if [ "$MANIFEST_FORMAT" = 2 ] && profile_selected bun; then
    if ! verify_selected_knip; then
      printf 'workspace: managed tool is missing or invalid: knip\n' >&2
      failed=1
    fi
  fi
  if [ "$MANIFEST_FORMAT" = 2 ] && profile_selected go; then
    verify_selected_go_tools || failed=1
  fi
  if [ "$MANIFEST_FORMAT" = 2 ] && profile_selected rust; then
    verify_selected_rust || failed=1
  fi
  [ "$failed" -eq 0 ]
}

command_install() {
  local i artifact_index python_pin python_input
  run_preflight "$@"
  mkdir -p "$TOOLS_ROOT" "$CACHE_ROOT"
  for ((i = 0; i < ${#TOOL_NAMES[@]}; i += 1)); do
    profile_selected "${TOOL_PROFILES[$i]}" || continue
    artifact_index=$(artifact_index_for "${TOOL_NAMES[$i]}" "$PLATFORM") || die "missing artifact selection"
    install_archive_tool "$i" "$artifact_index"
  done
  if [ "$MANIFEST_FORMAT" = 2 ] && python_pin=$(pin_index_for python); then
    python_input=$(input_index_for common uv .harness/python-downloads.json) || \
      die "lock: Python downloads input is required"
    artifact_index=$(artifact_index_for python "$PLATFORM") || \
      die "lock: missing Python artifact for $PLATFORM"
    install_uv_python "$python_pin" "$artifact_index" "$python_input"
  fi
  if [ "$MANIFEST_FORMAT" = 2 ] && selected_pin_kind_exists pypi-wheel; then
    install_selected_python_clis
  fi
  if [ "$MANIFEST_FORMAT" = 2 ] && profile_selected bun; then
    install_selected_knip
  fi
  if [ "$MANIFEST_FORMAT" = 2 ] && profile_selected go; then
    install_selected_go_tools
  fi
  if [ "$MANIFEST_FORMAT" = 2 ] && profile_selected rust; then
    install_selected_rust
  fi
}

pin_version() {
  local index
  index=$(pin_index_for "$1") || return 1
  printf '%s\n' "${PIN_VERSIONS[$index]}"
}

build_managed_path() {
  local i path dir managed= seen=: kind
  local IFS=,
  local paths=()
  for ((i = 0; i < ${#TOOL_NAMES[@]}; i += 1)); do
    profile_selected "${TOOL_PROFILES[$i]}" || continue
    paths=()
    read -r -a paths <<<"${TOOL_PATHS[$i]}"
    for path in "${paths[@]}"; do
      dir=$(tool_dir "$i")/${path%/*}
      case $seen in
        *:"$dir":*) ;;
        *) managed=${managed:+$managed:}$dir; seen=$seen$dir: ;;
      esac
    done
  done
  if [ "$MANIFEST_FORMAT" = 2 ]; then
    for ((i = 0; i < ${#PIN_NAMES[@]}; i += 1)); do
      profile_selected "${PIN_PROFILES[$i]}" || continue
      kind=${PIN_KINDS[$i]}
      case $kind in
        uv-python) dir=$TOOLS_ROOT/python/${PIN_VERSIONS[$i]}/runtime/bin ;;
        pypi-wheel|npm-package|go-module|cargo-crate)
          dir=$TOOLS_ROOT/${PIN_NAMES[$i]}/${PIN_VERSIONS[$i]}/bin
          ;;
        rustup-toolchain) dir=$TOOLS_ROOT/rust/${PIN_VERSIONS[$i]}/cargo/bin ;;
        *) continue ;;
      esac
      case $seen in
        *:"$dir":*) ;;
        *) managed=${managed:+$managed:}$dir; seen=$seen$dir: ;;
      esac
    done
  fi
  printf '%s\n' "$managed"
}

command_exec() {
  local profiles=() managed_path python_version rust_version rust_target go_tool go_root
  while [ "$#" -gt 0 ] && [ "$1" != -- ]; do
    profiles[${#profiles[@]}]=$1
    shift
  done
  [ "$#" -gt 0 ] && [ "$1" = -- ] || die "exec requires -- before the command"
  shift
  [ "$#" -gt 0 ] || die "exec requires a command"
  parse_manifest
  select_profiles "${profiles[@]}"
  verify_selected_tools
  managed_path=$(build_managed_path)
  python_version=$(pin_version python || true)
  rust_version=$(pin_version rust || true)
  mkdir -p "$CACHE_ROOT/uv" "$CACHE_ROOT/bun" "$CACHE_ROOT/go/mod" "$CACHE_ROOT/go/build" "$CACHE_ROOT/cargo"
  export PATH=$managed_path${managed_path:+:}$PATH
  export UV_CACHE_DIR=$CACHE_ROOT/uv
  export UV_PYTHON_INSTALL_DIR=$TOOLS_ROOT/python/$python_version
  export UV_PYTHON=$TOOLS_ROOT/python/$python_version/runtime/bin/python3.13
  export UV_PYTHON_DOWNLOADS=never
  export UV_NO_CONFIG=1
  unset PYTHONHOME PYTHONPATH NODE_PATH
  export PYTHONNOUSERSITE=1
  export BUN_INSTALL_CACHE_DIR=$CACHE_ROOT/bun
  export GOMODCACHE=$CACHE_ROOT/go/mod
  export GOCACHE=$CACHE_ROOT/go/build
  export CARGO_HOME=$CACHE_ROOT/cargo
  export RUSTUP_HOME=$TOOLS_ROOT/rust/$rust_version/rustup
  export UV_OFFLINE=$OFFLINE
  export HARNESS_WORKSPACE_OFFLINE=$OFFLINE
  if profile_selected go; then
    go_tool=$(tool_index_for go) || die "lock: managed Go is required"
    go_root=$(tool_dir "$go_tool")/go
    export GOROOT=$go_root
    export GOENV=off
    export GOTOOLCHAIN=local
    export GOFLAGS=
    export GOSUMDB=sum.golang.org
  fi
  if profile_selected rust; then
    rust_target=$(rust_target_for_platform "$PLATFORM") || die "unsupported Rust platform: $PLATFORM"
    export RUSTUP_TOOLCHAIN=$rust_version-$rust_target
    export RUSTUP_AUTO_INSTALL=0
    export RUSTC=$TOOLS_ROOT/rust/$rust_version/cargo/bin/rustc
    export RUSTDOC=$TOOLS_ROOT/rust/$rust_version/cargo/bin/rustdoc
    export RUSTFLAGS=
    export RUSTC_WRAPPER=
    export RUSTC_WORKSPACE_WRAPPER=
    export CARGO_ENCODED_RUSTFLAGS=
    export CARGO_NET_GIT_FETCH_WITH_CLI=false
    unset CARGO_BUILD_RUSTC CARGO_BUILD_RUSTC_WRAPPER
  fi
  if [ "$OFFLINE" = 1 ]; then
    export GOPROXY=off
    export CARGO_NET_OFFLINE=true
  else
    export GOPROXY=https://proxy.golang.org,direct
    export CARGO_NET_OFFLINE=false
  fi
  exec "$@"
}

write_hook_temp() {
  local directory=$1 name=$2 output=$3
  local temporary
  mkdir -p "$directory"
  temporary=$(mktemp "$directory/.harness-$name.XXXXXX")
  register_cleanup_path "$temporary"
  expected_hook "$name"
  printf '%s' "$HOOK_TEXT" >"$temporary"
  chmod 0755 "$temporary"
  printf -v "$output" '%s' "$temporary"
}

command_install_hooks() {
  local pre_commit_temp pre_push_temp
  run_preflight common
  resolve_hooks || die "could not resolve Git hook destinations"
  hook_is_known "$HOOK_PRE_COMMIT" pre-commit || die "unmanaged Git hook: $HOOK_PRE_COMMIT"
  hook_is_known "$HOOK_PRE_PUSH" pre-push || die "unmanaged Git hook: $HOOK_PRE_PUSH"
  write_hook_temp "${HOOK_PRE_COMMIT%/*}" pre-commit pre_commit_temp
  if ! write_hook_temp "${HOOK_PRE_PUSH%/*}" pre-push pre_push_temp; then
    rm -f -- "$pre_commit_temp"
    return 1
  fi
  mv "$pre_commit_temp" "$HOOK_PRE_COMMIT"
  mv "$pre_push_temp" "$HOOK_PRE_PUSH"
  say "workspace: installed Git hooks"
}

directories_equal() {
  [ -d "$1" ] && [ -d "$2" ] && diff -qr "$1" "$2" >/dev/null 2>&1
}

publish_skill() {
  local source=$1 destination=$2 parent stage backup=
  if directories_equal "$source" "$destination"; then
    say "workspace: reuse skill $destination"
    return 0
  fi
  parent=${destination%/*}
  mkdir -p "$parent"
  stage=$(mktemp -d "$parent/.harness.tmp.XXXXXX")
  register_cleanup_path "$stage"
  cp -R "$source/." "$stage/"
  if [ -e "$destination" ] || [ -L "$destination" ]; then
    backup=$parent/.harness.old.$$
    [ ! -e "$backup" ] && [ ! -L "$backup" ] || safe_remove "$backup"
    ACTIVE_REPLACEMENT_DESTINATION=$destination
    ACTIVE_REPLACEMENT_BACKUP=$backup
    ACTIVE_REPLACEMENT_PUBLISHED=0
    if ! mv "$destination" "$backup"; then
      clear_active_replacement
      return 1
    fi
  else
    ACTIVE_REPLACEMENT_DESTINATION=$destination
    ACTIVE_REPLACEMENT_BACKUP=
    ACTIVE_REPLACEMENT_PUBLISHED=0
  fi
  if ! mv "$stage" "$destination"; then
    rollback_active_replacement
    return 1
  fi
  ACTIVE_REPLACEMENT_PUBLISHED=1
  if ! directories_equal "$source" "$destination"; then
    rollback_active_replacement
    return 1
  fi
  [ -z "$backup" ] || safe_remove "$backup"
  clear_active_replacement
  say "workspace: deployed skill $destination"
}

command_sync_skills() {
  local source
  run_preflight common
  source=$(skill_source) || die "canonical or embedded harness skill is missing"
  publish_skill "$source" "$HOME_VALUE/.claude/skills/harness"
  publish_skill "$source" "$HOME_VALUE/.agents/skills/harness"
}

verify_hooks_exact() {
  resolve_hooks || return 1
  expected_hook pre-commit
  hook_is_exact "$HOOK_PRE_COMMIT" "$HOOK_TEXT" && [ -x "$HOOK_PRE_COMMIT" ] || return 1
  expected_hook pre-push
  hook_is_exact "$HOOK_PRE_PUSH" "$HOOK_TEXT" && [ -x "$HOOK_PRE_PUSH" ]
}

verify_skills_exact() {
  local source
  source=$(skill_source) || return 1
  directories_equal "$source" "$HOME_VALUE/.claude/skills/harness" && \
    directories_equal "$source" "$HOME_VALUE/.agents/skills/harness"
}

verify_git_clean() {
  local status
  if ! status=$(git -C "$ROOT" status --porcelain=v1 --untracked-files=no); then
    printf 'workspace: could not inspect tracked Git state\n' >&2
    return 1
  fi
  [ -z "$status" ] && return 0
  printf 'workspace: tracked Git changes detected:\n%s\n' "$status" >&2
  return 1
}

command_verify() {
  parse_manifest
  select_profiles "$@"
  PREFLIGHT_ERRORS=()
  validate_stop_wiring
  [ "${#PREFLIGHT_ERRORS[@]}" -eq 0 ] || die "Stop-hook verification failed"
  verify_selected_tools || return 1
  verify_hooks_exact || die "installed Git hooks do not match the managed shims"
  verify_skills_exact || die "deployed harness skills differ from the repository source"
  verify_git_clean || die "tracked worktree and/or index changed during workspace setup"
  say "workspace: verification ok"
}

usage() {
  cat <<'EOF'
Usage: .harness/workspace.sh COMMAND [ARG ...]

Commands:
  preflight [profile ...]       Validate prerequisites without modifying state
  install [profile ...]         Install verified direct artifacts
  exec [profile ...] -- command Run with the exact managed environment
  install-hooks                 Install collision-safe Git hooks
  sync-skills                   Deploy the embedded/canonical harness skill
  verify [profile ...]          Verify tools, hooks, skills, Stop wiring, and Git state
  validate-lock                 Validate the data-only lock manifest
  platform                      Print the supported platform key
  help                          Show this help

Profiles: common, python, bun, go, rust, all. Common is always selected.
Set OFFLINE=1 to prohibit downloads and require existing managed tools.
EOF
}

main() {
  local command=${1:-help}
  [ "$#" -eq 0 ] || shift
  case $command in
    preflight) run_preflight "$@" ;;
    install) command_install "$@" ;;
    exec) command_exec "$@" ;;
    install-hooks) [ "$#" -eq 0 ] || die "install-hooks takes no arguments"; command_install_hooks ;;
    sync-skills) [ "$#" -eq 0 ] || die "sync-skills takes no arguments"; command_sync_skills ;;
    verify) command_verify "$@" ;;
    validate-lock) [ "$#" -eq 0 ] || die "validate-lock takes no arguments"; parse_manifest; say "workspace: lock valid" ;;
    platform) [ "$#" -eq 0 ] || die "platform takes no arguments"; detect_platform || die "$PLATFORM_ERROR"; say "$PLATFORM" ;;
    help|-h|--help) usage ;;
    *) die "unknown command '$command'" ;;
  esac
}

trap cleanup_workspace EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

main "$@"
