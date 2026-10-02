#!/bin/bash
#
# DecentAI's starter for macOS (docs/system/desktop-install.md).
#
# What a person double-clicks in Finder. The launcher is a container and
# does the work; the starter is what has to be on the machine before a
# container can run at all:
#
#     it chooses the engine      Docker if it is running, Podman otherwise
#     it installs Podman         where there is no engine, with Homebrew,
#                                after asking
#     it remembers the engine    an install's data lives in the engine it
#                                was made on, and is looked for there
#     it runs the launcher       and shows DecentAI in a window of its own
#     it offers an app           DecentAI.app in your Applications folder,
#                                once
#
#     ./DecentAI.command                     install at first, start after that
#     ./DecentAI.command stop                nothing is removed by a stop
#     ./DecentAI.command status
#     ./DecentAI.command update
#     ./DecentAI.command backup
#     ./DecentAI.command stop-everything     ends everything the agents are doing
#     ./DecentAI.command reset-password      a new password, where there is no
#                                            email to send a reset link
#     ./DecentAI.command engine              which engine, and why
#     ./DecentAI.command shortcuts           put DecentAI.app in Applications
#     ./DecentAI.command develop <folder>    let the git repositories in a folder
#                                            of yours be agent sources; `off`
#                                            stops it
#
# Nothing here is typed by the person but yes or no, and their email and
# password at the first run, which the launcher asks for.
#
# Written for the bash every Mac carries (3.2): no associative arrays,
# nothing newer.

set -u

HERE="$(cd "$(dirname "$0")" && pwd -P)"
SELF="$HERE/$(basename "$0")"

# -- what the starter keeps, and where ------------------------------------

STARTER_HOME="${DECENTAI_STARTER_HOME:-$HOME/Library/Application Support/DecentAI}"
KEPT="$STARTER_HOME/starter.conf"
# In the repository the launcher is a build of one's own. The starter a
# release publishes has the published launcher's image written here in
# its place, by its digest.
IMAGE="${DECENTAI_LAUNCHER_IMAGE:-decentai-launcher:local}"
STATE="${DECENTAI_LAUNCHER_STATE:-decentai_launcher}"
PATIENCE="${DECENTAI_PATIENCE:-180}"
APPLICATIONS="${DECENTAI_APPLICATIONS:-$HOME/Applications}"
DOCKER_APP="${DECENTAI_DOCKER_APP:-/Applications/Docker.app}"
# Where engines put their programs, beside PATH: an app opened from
# Finder is given only the system's own folders.
PROGRAM_FOLDERS="${DECENTAI_PROGRAM_FOLDERS:-/usr/local/bin:/opt/homebrew/bin:$HOME/.docker/bin:/Applications/Docker.app/Contents/Resources/bin:/opt/podman/bin}"


# -- the person -----------------------------------------------------------

told() {
    printf '%s\n' "$*"
}

# DECENTAI_ANSWER answers for a person who is not there: a test, or an
# administrator installing for somebody else.
agrees() {
    local answer="${DECENTAI_ANSWER:-}"
    if [ -z "$answer" ]; then
        printf '%s (yes/no) ' "$1"
        read -r answer || answer=""
    fi
    answer="$(printf '%s' "$answer" | tr '[:upper:]' '[:lower:]' | tr -d '[:space:]')"
    [ "$answer" = "y" ] || [ "$answer" = "yes" ]
}

somebody_is_there() {
    [ -t 0 ] || [ -n "${DECENTAI_ANSWER:-}" ]
}


# -- what the starter keeps between one opening and the next --------------

kept() {
    [ -f "$KEPT" ] || return 0
    sed -n "s/^$1=//p" "$KEPT" | tail -n 1
}

keep() {
    [ "$(kept "$1")" = "$2" ] && return 0
    mkdir -p "$STARTER_HOME"
    if [ -f "$KEPT" ]; then
        grep -v "^$1=" "$KEPT" > "$KEPT.new" || true
        mv "$KEPT.new" "$KEPT"
    fi
    printf '%s=%s\n' "$1" "$2" >> "$KEPT"
}


# -- the machine's programs -----------------------------------------------

# Where a program is: on PATH, or in a folder engines put theirs in.
found() {
    local name="$1" folder
    if command -v "$name" >/dev/null 2>&1; then
        command -v "$name"
        return 0
    fi
    local IFS=":"
    for folder in $PROGRAM_FOLDERS; do
        if [ -x "$folder/$name" ]; then
            printf '%s\n' "$folder/$name"
            return 0
        fi
    done
    return 1
}


# -- the engines ----------------------------------------------------------
#
# Every engine answers the same questions, by its name:
#   <engine>_running      whether it answers now
#   <engine>_socket       its socket, as a container on it finds it
#   <engine>_ready        made to run, started where it can be
#   <engine>_finds DIR    a folder of this Mac, as a container is handed it

TITLE_docker="Docker"
TITLE_podman="Podman"

docker_program() { found docker; }

docker_running() {
    local docker
    docker="$(docker_program)" || return 1
    "$docker" version --format '{{.Server.Version}}' >/dev/null 2>&1
}

docker_socket() {
    printf '%s\n' "/var/run/docker.sock"
}

# Docker is somebody's own choice and their own install: it is started
# where it is found, and never installed from here.
docker_ready() {
    docker_running && return 0
    [ -d "$DOCKER_APP" ] || return 1
    told "Starting Docker Desktop..."
    open -a "$DOCKER_APP" >/dev/null 2>&1
    waited_for docker_running
}

# Docker Desktop shares the home folders and the volumes as they are.
docker_finds() {
    case "$1" in
        /Users/*|/Volumes/*) printf '%s\n' "$1" ;;
        *) return 1 ;;
    esac
}

podman_program() { found podman; }

podman_running() {
    local podman
    podman="$(podman_program)" || return 1
    "$podman" info --format '{{.Host.RemoteSocket.Path}}' >/dev/null 2>&1
}

podman_socket() {
    local podman
    podman="$(podman_program)" || return 1
    "$podman" info --format '{{.Host.RemoteSocket.Path}}' 2>/dev/null \
        | sed -n 's|^unix://||p' | head -n 1
}

podman_installed() {
    podman_program >/dev/null && return 0
    told ""
    told "DecentAI runs in containers, and this Mac has nothing that runs them."
    told "Podman does, and is free."
    local brew
    if ! brew="$(found brew)"; then
        told "Install Podman from https://podman.io (the installer for macOS), and"
        told "open DecentAI again."
        return 1
    fi
    if ! agrees "Install Podman now, with Homebrew?"; then
        told "Nothing was installed."
        return 1
    fi
    "$brew" install podman || true
    if ! podman_program >/dev/null; then
        told "Podman was not installed."
        return 1
    fi
}

podman_ready() {
    podman_installed || return 1
    podman_running && return 0
    local podman
    podman="$(podman_program)"
    if ! "$podman" machine inspect --format '{{.State}}' >/dev/null 2>&1; then
        told "Preparing Podman. This is done once."
        if ! "$podman" machine init; then
            told "Podman could not be prepared."
            return 1
        fi
    fi
    told "Starting Podman..."
    # What Podman says as it starts is for whoever runs Podman: shown
    # when the machine did not start, and not otherwise.
    local said
    said="$("$podman" machine start 2>&1)"
    podman_running && return 0
    told "$said"
    told "Podman did not start."
    return 1
}

# Podman's machine shares the home folders as they are.
podman_finds() {
    case "$1" in
        /Users/*) printf '%s\n' "$1" ;;
        *) return 1 ;;
    esac
}

engine_program() { "${1}_program"; }

waited_for() {
    local until=$(( $(date +%s) + PATIENCE ))
    while [ "$(date +%s)" -lt "$until" ]; do
        "$1" && return 0
        sleep 3
    done
    "$1"
}


# -- which engine ---------------------------------------------------------

ENGINE=""
WHY=""

# The engine to use, and in a sentence why. Nothing is started or
# installed by asking.
chosen() {
    local remembered
    remembered="$(kept engine)"
    if [ -n "$remembered" ]; then
        ENGINE="$remembered"
        WHY="DecentAI was installed on $(title "$ENGINE"), and its data is there."
    elif docker_running; then
        ENGINE="docker"
        WHY="Docker is running on this Mac."
    elif podman_program >/dev/null; then
        ENGINE="podman"
        WHY="Docker is not running on this Mac, and Podman is installed."
    else
        ENGINE="podman"
        WHY="Nothing that runs containers is running on this Mac."
    fi
}

title() {
    eval "printf '%s\n' \"\$TITLE_$1\""
}

ready() {
    chosen
    "${ENGINE}_ready" && return 0
    if [ -n "$(kept engine)" ]; then
        told ""
        told "DecentAI was installed on $(title "$ENGINE"), which did not start. Its data is"
        told "there, so it is not looked for anywhere else. Start $(title "$ENGINE") and open"
        told "DecentAI again."
    fi
    return 1
}


# -- the launcher ---------------------------------------------------------

# The engine's command line for one launcher command: LINE holds it.
LINE=()

line() {
    local shown="$1"
    shift
    LINE=(run --rm)
    if [ "$shown" = "shown" ]; then
        # A window somebody sits at, or a program's pipe.
        if [ -t 0 ]; then LINE+=(-it); else LINE+=(-i); fi
    fi
    LINE+=(-v "$("${ENGINE}_socket"):/var/run/docker.sock" -v "$STATE:/state")
    if [ -n "${DECENTAI_PROJECT:-}" ]; then
        LINE+=(-e "DECENTAI_PROJECT=$DECENTAI_PROJECT")
    fi
    if [ -n "${DECENTAI_PASSWORD:-}" ] && { [ "$1" = "install" ] || [ "$1" = "reset-password" ]; }; then
        # By name: its value is the engine's to read, not the line's to show.
        LINE+=(-e DECENTAI_PASSWORD)
    fi
    LINE+=("$IMAGE" "$@")
}

# A command as the launcher is asked it, with what the environment adds.
ASKED=()

asked() {
    local command="$1"
    shift
    ASKED=("$command")
    if { [ "$command" = "install" ] || [ "$command" = "update" ]; } \
            && [ "${DECENTAI_UNSIGNED:-}" = "1" ]; then
        ASKED+=(--unsigned)
    fi
    if [ "$command" = "install" ] && [ -n "${DECENTAI_PORT:-}" ]; then
        ASKED+=(--port "$DECENTAI_PORT")
    fi
    if [ "$#" -gt 0 ]; then
        ASKED+=("$@")
    fi
}

has_launcher() {
    local engine
    engine="$(engine_program "$ENGINE")"
    "$engine" image inspect "$IMAGE" --format '{{.Id}}' >/dev/null 2>&1 && return 0
    told "Downloading DecentAI's launcher..."
    "$engine" pull "$IMAGE" && return 0
    told ""
    told "The launcher ($IMAGE) is not on this Mac and could not be downloaded."
    return 1
}

launcher() {
    local engine
    engine="$(engine_program "$ENGINE")"
    "$engine" "${LINE[@]}"
}

address() {
    line quiet status
    launcher 2>/dev/null | grep -o 'http://localhost:[0-9]*' | head -n 1
}


# -- the window DecentAI is shown in --------------------------------------
#
# A browser's window without its tabs and its address bar, so that what a
# person sees is DecentAI and not an address on their own computer.

# The browsers that can show one page as a window of their own.
FAMILY="com.google.chrome com.microsoft.edgemac com.brave.browser com.vivaldi.vivaldi org.chromium.chromium com.operasoftware.opera"
FAMILY_APPS="Google Chrome:Microsoft Edge:Brave Browser:Vivaldi:Chromium:Opera"

# The person's own browser, by the id macOS knows it under: the handler
# whose scheme is http, read from what `defaults` prints, one key a line.
# A handler is settled where it ends ("}," — a nested one ends "};"),
# whatever order its keys came in; a nested role ("-", its preferred
# versions) is not a browser.
own_browser() {
    defaults read com.apple.LaunchServices/com.apple.launchservices.secure \
            LSHandlers 2>/dev/null \
        | awk '/LSHandlerRoleAll = / { value = $3; gsub(/[";]/, "", value);
                                       if (value != "-") role = value }
               /LSHandlerURLScheme = https?;/ { web = 1 }
               /^[[:space:]]*},?[[:space:]]*$/ { if (web && role != "") { print role; exit }
                                                 role = ""; web = 0 }' \
        | tr '[:upper:]' '[:lower:]'
}

app_of() {
    mdfind "kMDItemCFBundleIdentifier == '$1'" 2>/dev/null | grep '\.app$' | head -n 1
}

program_in() {
    local executable
    executable="$(defaults read "$1/Contents/Info" CFBundleExecutable 2>/dev/null)" || return 1
    [ -x "$1/Contents/MacOS/$executable" ] && printf '%s\n' "$1/Contents/MacOS/$executable"
}

# The person's own browser where it can do this: they are signed in
# there. The first of the family that is installed, where it cannot.
window_program() {
    if [ -n "${DECENTAI_WINDOW_PROGRAM:-}" ]; then
        printf '%s\n' "$DECENTAI_WINDOW_PROGRAM"
        return 0
    fi
    local own app name
    own="$(own_browser)"
    case " $FAMILY " in
        *" $own "*)
            app="$(app_of "$own")"
            if [ -n "$app" ] && program_in "$app"; then return 0; fi
            ;;
    esac
    local IFS=":"
    for name in $FAMILY_APPS; do
        for app in "/Applications/$name.app" "$HOME/Applications/$name.app"; do
            if [ -d "$app" ] && program_in "$app"; then return 0; fi
        done
    done
    return 1
}

shown() {
    local address="$1" program
    [ -n "$address" ] || return 0
    if [ "${DECENTAI_NO_BROWSER:-}" = "1" ]; then
        told "Open $address"
        return 0
    fi
    # A window of its own; a tab of whatever browser there is, where no
    # browser here can show one.
    if program="$(window_program)"; then
        nohup "$program" "--app=$address" >/dev/null 2>&1 &
    else
        open "$address"
    fi
}


# -- DecentAI.app ---------------------------------------------------------
#
# DecentAI in the Applications folder, so it opens from Launchpad,
# Spotlight or the Dock with its own name and icon. The app runs this
# starter; it is made for the checkout it was made from.

made_app() {
    local app="$APPLICATIONS/DecentAI.app"
    local contents="$app/Contents"
    mkdir -p "$contents/MacOS" "$contents/Resources" || return 1
    cat > "$contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key><string>DecentAI</string>
    <key>CFBundleDisplayName</key><string>DecentAI</string>
    <key>CFBundleIdentifier</key><string>io.decentai.starter</string>
    <key>CFBundleExecutable</key><string>DecentAI</string>
    <key>CFBundleIconFile</key><string>DecentAI</string>
    <key>CFBundlePackageType</key><string>APPL</string>
    <key>CFBundleShortVersionString</key><string>1.0</string>
    <key>LSUIElement</key><true/>
</dict>
</plist>
PLIST
    cat > "$contents/MacOS/DecentAI" <<APP
#!/bin/bash
# Opens DecentAI through its starter, $SELF.
log="\$HOME/Library/Logs/DecentAI.log"
mkdir -p "\$HOME/Library/Logs"
if ! "$SELF" open >>"\$log" 2>&1; then
    /usr/bin/osascript -e 'display alert "DecentAI did not open" message "What happened is in ~/Library/Logs/DecentAI.log. Opening DecentAI.command in Finder shows it as it happens."' >/dev/null 2>&1
fi
APP
    chmod +x "$contents/MacOS/DecentAI"
    made_icon "$contents/Resources/DecentAI.icns"
    touch "$app"
}

# The DecentAI mark, as the icon macOS shows: made from the picture
# beside this starter, with the tools every Mac carries.
made_icon() {
    local picture="$HERE/DecentAI.png" target="$1"
    [ -f "$picture" ] || return 0
    command -v sips >/dev/null 2>&1 && command -v iconutil >/dev/null 2>&1 || return 0
    local set
    set="$(mktemp -d)/DecentAI.iconset"
    mkdir -p "$set"
    local size
    for size in 16 32 128 256 512; do
        sips -z "$size" "$size" "$picture" --out "$set/icon_${size}x${size}.png" >/dev/null 2>&1
        sips -z $((size * 2)) $((size * 2)) "$picture" --out "$set/icon_${size}x${size}@2x.png" >/dev/null 2>&1
    done
    iconutil -c icns "$set" -o "$target" >/dev/null 2>&1 || true
    rm -rf "$(dirname "$set")"
}

# Asked once, when somebody is there to answer: a yes makes the app, a
# no is remembered, and either can be changed with `shortcuts`.
offered() {
    [ -n "$(kept shortcuts)" ] && return 0
    somebody_is_there || return 0
    told ""
    if agrees "Put DecentAI in your Applications folder, to open it from Launchpad or Spotlight?"; then
        if made_app; then
            keep shortcuts made
            told "DecentAI is in your Applications folder."
        fi
    else
        keep shortcuts declined
        told "It can be put there later: ./DecentAI.command shortcuts"
    fi
}

placed() {
    if ! made_app; then
        told "DecentAI.app could not be made in $APPLICATIONS."
        return 1
    fi
    keep shortcuts made
    told "DecentAI is in your Applications folder."
}


# -- what a person asks for -----------------------------------------------

# Install at first, start after that, and show it.
opened() {
    ready || return 1
    has_launcher || return 1
    local said code
    line quiet status
    said="$(launcher 2>&1)"
    code=$?
    if printf '%s' "$said" | grep -q "is not installed"; then
        told "Installing DecentAI on $(title "$ENGINE")."
        asked install
        line shown "${ASKED[@]}"
        launcher
        code=$?
    elif [ "$code" -ne 0 ]; then
        told "$said"
        return 1
    else
        line shown start
        launcher
        code=$?
    fi
    [ "$code" -eq 0 ] || return "$code"
    keep engine "$ENGINE"
    shown "$(address)"
    offered
    return 0
}

passed() {
    ready || return 1
    has_launcher || return 1
    asked "$@"
    line shown "${ASKED[@]}"
    launcher
    local code=$?
    if [ "$code" -eq 0 ] && { [ "$1" = "install" ] || [ "$1" = "start" ]; }; then
        keep engine "$ENGINE"
    fi
    return "$code"
}

which_engine() {
    chosen
    told "$(title "$ENGINE"): $WHY"
}

# A folder of the person's own agents, handed to DecentAI as a place its
# agent sources may come from (docs/developing.md in the agent template).
developed() {
    ready || return 1
    has_launcher || return 1
    if [ "${1:-}" = "off" ]; then
        line shown develop --off
    elif [ -n "${1:-}" ]; then
        if [ ! -d "$1" ]; then
            told "There is no folder $1 on this Mac."
            return 1
        fi
        local given finds
        given="$(cd "$1" && pwd -P)"
        if ! finds="$("${ENGINE}_finds" "$given")"; then
            told "$given is not in a folder $(title "$ENGINE") shares; choose one in your home folder."
            return 1
        fi
        told "Handing $given to DecentAI, to read."
        line shown develop "$finds"
    else
        line shown develop
    fi
    launcher
}


main() {
    local command="${1:-open}"
    if [ "$#" -gt 0 ]; then shift; fi

    case "$command" in
        open) opened ;;
        engine) which_engine ;;
        shortcuts) placed ;;
        develop) developed "$@" ;;
        install|start|stop|status|update|backup|stop-everything|reset-password)
            passed "$command" "$@" ;;
        *)
            told "./DecentAI.command [stop | status | update | backup | stop-everything | reset-password | engine | shortcuts | develop]"
            return 2 ;;
    esac
}

# Run, unless read by a test that calls the parts one by one.
if [ "${BASH_SOURCE[0]}" = "$0" ]; then
    main "$@"
fi
