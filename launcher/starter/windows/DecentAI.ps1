<#
DecentAI's starter for Windows (docs/system/desktop-install.md).

What a person double-clicks. The launcher is a container and does the
work; the starter is what has to be on the machine before a container
can run at all:

    it chooses the engine      Docker if it is running, Podman otherwise
    it installs Podman         where there is no engine, after asking
    it updates the subsystem   where Podman's machine will not start
                               without it, after asking
    it remembers the engine    an install's data lives in the engine it
                               was made on, and is looked for there
    it runs the launcher       and shows DecentAI in a window of its own
    it offers a shortcut       on the desktop and in the Start menu, once

    DecentAI.cmd                     install at first, start after that
    DecentAI.cmd stop                nothing is removed by a stop
    DecentAI.cmd status
    DecentAI.cmd update
    DecentAI.cmd backup
    DecentAI.cmd stop-everything     ends everything the agents are doing
    DecentAI.cmd engine              which engine, and why
    DecentAI.cmd shortcuts           put it on the desktop and in the Start menu
    DecentAI.cmd reset-password      a new password, where there is no email
                                     to send a reset link
    DecentAI.cmd develop <folder>    let the git repositories in a folder of
                                     your own be agent sources; `off` stops it

Nothing here is typed by the person but yes or no, and their email and
password at the first run, which the launcher asks for.

This file is plain ASCII: Windows PowerShell reads a script without a
byte order mark in the machine's own code page.
#>

param(
    [Parameter(Position = 0)][string]$Command = "open",
    [Parameter(Position = 1, ValueFromRemainingArguments = $true)][string[]]$Rest = @()
)

$ErrorActionPreference = "Stop"


class Person {
    static [void] Told([string]$text) {
        Write-Host $text
    }

    # DECENTAI_ANSWER answers for a person who is not there: a test,
    # or an administrator installing for somebody else.
    static [bool] Agrees([string]$question) {
        $answer = $env:DECENTAI_ANSWER
        if (-not $answer) {
            $answer = Read-Host "$question (yes/no)"
        }
        return @("y", "yes") -contains "$answer".Trim().ToLower()
    }
}


class Answer {
    [int]$Code
    [string]$Said
}


# A program of the machine's: found where programs are looked for, or
# in the folder its installer puts it, which a window opened before the
# install does not know of yet.
class Program {
    [string]$Name
    [string[]]$Elsewhere

    Program([string]$name, [string[]]$elsewhere) {
        $this.Name = $name
        $this.Elsewhere = $elsewhere
    }

    [string] Path() {
        $found = Get-Command $this.Name -CommandType Application -ErrorAction SilentlyContinue |
            Select-Object -First 1
        if ($found) {
            return $found.Source
        }
        foreach ($candidate in $this.Elsewhere) {
            if ($candidate -and (Test-Path $candidate)) {
                return $candidate
            }
        }
        return ""
    }

    [bool] Here() {
        return [bool]$this.Path()
    }

    static [string] Line([string[]]$arguments) {
        $words = foreach ($argument in $arguments) {
            if ($argument -match '^[A-Za-z0-9_.:/=\\{}@,+-]+$') {
                $argument
            } else {
                '"' + ($argument -replace '"', '\"') + '"'
            }
        }
        return ($words -join " ")
    }

    [System.Diagnostics.ProcessStartInfo] Start([string[]]$arguments) {
        $start = New-Object System.Diagnostics.ProcessStartInfo
        $start.FileName = $this.Path()
        $start.Arguments = [Program]::Line($arguments)
        $start.UseShellExecute = $false
        return $start
    }

    # What the program says, and how it ended. Nothing is shown.
    [Answer] Ask([string[]]$arguments) {
        $answer = [Answer]::new()
        if (-not $this.Here()) {
            $answer.Code = 127
            $answer.Said = "$($this.Name) is not on this computer"
            return $answer
        }
        $start = $this.Start($arguments)
        $start.RedirectStandardOutput = $true
        $start.RedirectStandardError = $true
        $start.RedirectStandardInput = $true
        $start.CreateNoWindow = $true
        try {
            $process = [System.Diagnostics.Process]::Start($start)
            $process.StandardInput.Close()
            $errors = $process.StandardError.ReadToEndAsync()
            $said = $process.StandardOutput.ReadToEnd()
            $process.WaitForExit()
            $answer.Code = $process.ExitCode
            # The subsystem's own program writes two bytes a letter.
            $answer.Said = ($said + $errors.Result) -replace "`0", ""
        } catch {
            $answer.Code = 126
            $answer.Said = "$($this.Name) could not be run: $($_.Exception.Message)"
        }
        return $answer
    }

    # The program, in this window: the person sees what it says and
    # answers what it asks.
    [int] Run([string[]]$arguments) {
        if (-not $this.Here()) {
            return 127
        }
        try {
            $process = [System.Diagnostics.Process]::Start($this.Start($arguments))
            $process.WaitForExit()
            return $process.ExitCode
        } catch {
            [Person]::Told("$($this.Name) could not be run: $($_.Exception.Message)")
            return 126
        }
    }
}


# The Windows Subsystem for Linux, which Podman's machine runs on.
class Subsystem {
    # The version Podman's machine was seen to start on. On 2.1.5 it
    # did not: the machine's systemd no longer runs on the layout of
    # control groups that version gave it.
    static [version]$SeenToWork = [version]"2.7.14"

    [Program]$Program = [Program]::new("wsl", @())

    [version] Version() {
        $answer = $this.Program.Ask(@("--version"))
        if ($answer.Code -eq 0 -and $answer.Said -match '(\d+\.\d+\.\d+)') {
            return [version]$Matches[1]
        }
        return [version]"0.0.0"
    }

    [bool] BroughtUpToDate() {
        $has = $this.Version()
        if ($has -ge [Subsystem]::SeenToWork) {
            [Person]::Told("The Windows Subsystem for Linux is up to date (version $has), so that is not why.")
            return $false
        }
        [Person]::Told("")
        [Person]::Told("Podman needs a newer Windows Subsystem for Linux than this computer has.")
        [Person]::Told("Updating it takes a few minutes and asks for administrator rights once.")
        [Person]::Told("Every container on this computer stops while it is updated. If you use")
        [Person]::Told("Docker Desktop, it has to be started again afterwards.")
        if (-not [Person]::Agrees("Update it now?")) {
            [Person]::Told("Nothing was changed.")
            return $false
        }
        if ($this.Program.Run(@("--update")) -ne 0) {
            [Person]::Told("The update did not finish.")
            return $false
        }
        return $true
    }
}


class Engine {
    [string]$Name
    [string]$Title
    [Program]$Program

    [bool] Running() { return $false }
    [string] Socket() { return "" }
    [bool] MadeReady() { return $false }

    [bool] Has([string]$image) {
        return $this.Program.Ask(@("image", "inspect", $image, "--format", "{{.Id}}")).Code -eq 0
    }

    # Where this engine finds a folder of this computer, for a container
    # to be handed it: its own name for the drive, then the path.
    [string] Drives() { return "" }

    [string] Finds([string]$folder) {
        if ($folder -notmatch '^([A-Za-z]):\\(.*)$') {
            return ""
        }
        $rest = ($Matches[2] -replace '\\', '/').TrimEnd('/')
        $found = "$($this.Drives())/$($Matches[1].ToLower())"
        if ($rest) {
            $found = "$found/$rest"
        }
        return $found
    }

    # How long something that was started is waited for, in seconds.
    static [int] Patience() {
        if ($env:DECENTAI_PATIENCE) {
            return [int]$env:DECENTAI_PATIENCE
        }
        return 180
    }
}


class Docker : Engine {
    Docker() {
        $this.Name = "docker"
        $this.Title = "Docker"
        $this.Program = [Program]::new("docker", @())
    }

    [bool] Running() {
        return $this.Program.Ask(@("version", "--format", "{{.Server.Version}}")).Code -eq 0
    }

    [string] Socket() {
        return "/var/run/docker.sock"
    }

    # Docker Desktop's machine sees this computer's drives here.
    [string] Drives() {
        return "/run/desktop/mnt/host"
    }

    [string] Desktop() {
        if ($env:DECENTAI_DOCKER_DESKTOP) {
            return $env:DECENTAI_DOCKER_DESKTOP
        }
        return (Join-Path $env:ProgramFiles "Docker\Docker\Docker Desktop.exe")
    }

    # Docker is somebody's own choice and their own install: it is
    # started where it is found, and never installed from here.
    [bool] MadeReady() {
        if ($this.Running()) {
            return $true
        }
        $desktop = $this.Desktop()
        if (-not (Test-Path $desktop)) {
            return $false
        }
        [Person]::Told("Starting Docker Desktop...")
        Start-Process -FilePath $desktop | Out-Null
        $until = (Get-Date).AddSeconds([Engine]::Patience())
        while ((Get-Date) -lt $until) {
            if ($this.Running()) {
                return $true
            }
            Start-Sleep -Seconds 3
        }
        return $false
    }
}


class Podman : Engine {
    [Subsystem]$Subsystem = [Subsystem]::new()

    Podman() {
        $this.Name = "podman"
        $this.Title = "Podman"
        $folder = $env:DECENTAI_PODMAN_HOME
        if (-not $folder) {
            $folder = Join-Path $env:ProgramFiles "RedHat\Podman"
        }
        $this.Program = [Program]::new("podman", @((Join-Path $folder "podman.exe")))
    }

    [bool] Running() {
        return $this.Program.Ask(@("info", "--format", "{{.Host.RemoteSocket.Path}}")).Code -eq 0
    }

    [string] Socket() {
        $said = $this.Program.Ask(@("info", "--format", "{{.Host.RemoteSocket.Path}}")).Said
        $found = $said -split "`r?`n" | Where-Object { $_ -match "^unix://" } | Select-Object -First 1
        return ("$found".Trim() -replace "^unix://", "")
    }

    # Podman's machine, a WSL distribution, sees this computer's drives here.
    [string] Drives() {
        return "/mnt"
    }

    [bool] Installed() {
        if ($this.Program.Here()) {
            return $true
        }
        [Person]::Told("")
        [Person]::Told("DecentAI runs in containers, and this computer has nothing that runs them.")
        [Person]::Told("Podman does, and is free. Installing it downloads about 1 GB and asks for")
        [Person]::Told("administrator rights once.")
        if (-not [Person]::Agrees("Install Podman now?")) {
            [Person]::Told("Nothing was installed.")
            return $false
        }
        $winget = [Program]::new("winget", @(
            (Join-Path $env:LOCALAPPDATA "Microsoft\WindowsApps\winget.exe")))
        if (-not $winget.Here()) {
            [Person]::Told("This computer has no winget to install it with. Install Podman from")
            [Person]::Told("https://podman.io and open DecentAI again.")
            return $false
        }
        $code = $winget.Run(@(
            "install", "--id", "RedHat.Podman", "--exact", "--silent",
            "--accept-package-agreements", "--accept-source-agreements",
            "--disable-interactivity"))
        if ($code -ne 0 -or -not $this.Program.Here()) {
            [Person]::Told("Podman was not installed.")
            return $false
        }
        return $true
    }

    [bool] HasMachine() {
        return $this.Program.Ask(@("machine", "inspect", "--format", "{{.State}}")).Code -eq 0
    }

    # What Podman says as it starts is for whoever runs Podman: it is
    # shown when the machine did not start, and not otherwise.
    [bool] Started() {
        $said = $this.Program.Ask(@("machine", "start")).Said
        if ($this.Running()) {
            return $true
        }
        [Person]::Told($said.Trim())
        return $false
    }

    [bool] MadeReady() {
        if (-not $this.Installed()) {
            return $false
        }
        if ($this.Running()) {
            return $true
        }
        if (-not $this.HasMachine()) {
            [Person]::Told("Preparing Podman. This is done once.")
            if ($this.Program.Run(@("machine", "init")) -ne 0) {
                [Person]::Told("Podman could not be prepared.")
                return $false
            }
        }
        [Person]::Told("Starting Podman...")
        if ($this.Started()) {
            return $true
        }
        [Person]::Told("Podman did not start.")
        if (-not $this.Subsystem.BroughtUpToDate()) {
            return $false
        }
        [Person]::Told("Starting Podman...")
        return $this.Started()
    }
}


# The window DecentAI is shown in: a browser's, without its tabs and its
# address bar, so that what a person sees is DecentAI and not an address
# on their own computer.
class Window {
    # The browsers that can show one page as a window of its own, by the
    # names Windows knows them under when one is the person's own.
    static [string[]]$Family = @("MSEdge", "Chrome", "Brave", "Vivaldi", "Chromium", "Opera")

    # The person's own browser where it can do this: they are signed in
    # there. Edge, which every Windows carries, where it cannot.
    [string] Program() {
        if ($env:DECENTAI_WINDOW_PROGRAM) {
            return $env:DECENTAI_WINDOW_PROGRAM
        }
        $own = $this.Own()
        if ($own) {
            return $own
        }
        foreach ($root in @(${env:ProgramFiles(x86)}, $env:ProgramFiles)) {
            if (-not $root) {
                continue
            }
            $edge = Join-Path $root "Microsoft\Edge\Application\msedge.exe"
            if (Test-Path $edge) {
                return $edge
            }
        }
        return ""
    }

    [string] Own() {
        try {
            $choice = (Get-ItemProperty -ErrorAction Stop -Path (
                "HKCU:\Software\Microsoft\Windows\Shell\Associations\" +
                "UrlAssociations\http\UserChoice")).ProgId
            $known = [Window]::Family | Where-Object { $choice -like "$_*" }
            if (-not $known) {
                return ""
            }
            $command = (Get-ItemProperty -ErrorAction Stop -Path (
                "Registry::HKEY_CLASSES_ROOT\$choice\shell\open\command")).'(default)'
            if ($command -match '^"([^"]+\.exe)"' -or $command -match '^(\S+\.exe)') {
                if (Test-Path $Matches[1]) {
                    return $Matches[1]
                }
            }
        } catch {
        }
        return ""
    }

    [bool] Shows([string]$address) {
        $program = $this.Program()
        if (-not $program) {
            return $false
        }
        try {
            Start-Process -FilePath $program -ArgumentList "--app=$address" | Out-Null
            return $true
        } catch {
            return $false
        }
    }
}


# DecentAI on the desktop and in the Start menu: a shortcut to this
# starter, so that opening it is a double-click on its own name.
class Shortcuts {
    [string]$Starter

    Shortcuts([string]$folder) {
        $this.Starter = Join-Path $folder "DecentAI.cmd"
    }

    [string[]] Places() {
        if ($env:DECENTAI_SHORTCUTS_HOME) {
            return @((Join-Path $env:DECENTAI_SHORTCUTS_HOME "desktop"),
                     (Join-Path $env:DECENTAI_SHORTCUTS_HOME "programs"))
        }
        return @([Environment]::GetFolderPath("Desktop"),
                 [Environment]::GetFolderPath("Programs"))
    }

    [bool] Made() {
        try {
            $shell = New-Object -ComObject WScript.Shell
            foreach ($place in $this.Places()) {
                New-Item -ItemType Directory -Force -Path $place | Out-Null
                $shortcut = $shell.CreateShortcut((Join-Path $place "DecentAI.lnk"))
                $shortcut.TargetPath = $this.Starter
                $shortcut.WorkingDirectory = Split-Path $this.Starter
                $shortcut.Description = "DecentAI on this computer"
                $icon = Join-Path (Split-Path $this.Starter) "DecentAI.ico"
                if (Test-Path $icon) {
                    $shortcut.IconLocation = $icon
                }
                $shortcut.Save()
            }
            return $true
        } catch {
            [Person]::Told("The shortcuts could not be made: $($_.Exception.Message)")
            return $false
        }
    }
}


class Starter {
    [string]$Folder
    [string]$Image
    [string]$State
    [Shortcuts]$Shortcuts

    Starter([string]$here) {
        $this.Shortcuts = [Shortcuts]::new($here)
        $this.Folder = $env:DECENTAI_STARTER_HOME
        if (-not $this.Folder) {
            $this.Folder = Join-Path $env:LOCALAPPDATA "DecentAI"
        }
        # In the repository the launcher is a build of one's own. The
        # starter a release publishes has the published launcher's
        # image written here in its place, by its digest.
        $this.Image = $env:DECENTAI_LAUNCHER_IMAGE
        if (-not $this.Image) {
            $this.Image = "decentai-launcher:local"
        }
        $this.State = $env:DECENTAI_LAUNCHER_STATE
        if (-not $this.State) {
            $this.State = "decentai_launcher"
        }
    }

    # ------------------------------------------------------------------
    # Which engine
    # ------------------------------------------------------------------

    # What the starter keeps between one opening and the next.
    [hashtable] Kept() {
        $found = @{}
        $kept = Join-Path $this.Folder "starter.json"
        if (Test-Path $kept) {
            try {
                $read = Get-Content $kept -Raw | ConvertFrom-Json
                foreach ($property in $read.PSObject.Properties) {
                    $found[$property.Name] = "$($property.Value)"
                }
            } catch {
            }
        }
        return $found
    }

    [void] Keep([string]$name, [string]$value) {
        $kept = $this.Kept()
        if ($kept[$name] -eq $value) {
            return
        }
        $kept[$name] = $value
        New-Item -ItemType Directory -Force -Path $this.Folder | Out-Null
        $kept | ConvertTo-Json |
            Set-Content -Path (Join-Path $this.Folder "starter.json") -Encoding ASCII
    }

    [string] Remembered() {
        return "$($this.Kept()['engine'])"
    }

    [void] Remember([Engine]$engine) {
        $this.Keep("engine", $engine.Name)
    }

    [Engine] Named([string]$name) {
        if ($name -eq "docker") {
            return [Docker]::new()
        }
        return [Podman]::new()
    }

    # The engine to use, and in a sentence why. Nothing is started or
    # installed by asking.
    [object[]] Chosen() {
        $kept = $this.Remembered()
        if ($kept) {
            $engine = $this.Named($kept)
            return @($engine, "DecentAI was installed on $($engine.Title), and its data is there.")
        }
        $docker = [Docker]::new()
        if ($docker.Running()) {
            return @($docker, "Docker is running on this computer.")
        }
        $podman = [Podman]::new()
        if ($podman.Program.Here()) {
            return @($podman, "Docker is not running on this computer, and Podman is installed.")
        }
        return @($podman, "Nothing that runs containers is running on this computer.")
    }

    [Engine] Ready() {
        $engine = $this.Chosen()[0]
        if ($engine.MadeReady()) {
            return $engine
        }
        if ($this.Remembered()) {
            [Person]::Told("")
            [Person]::Told("DecentAI was installed on $($engine.Title), which did not start. Its data is")
            [Person]::Told("there, so it is not looked for anywhere else. Start $($engine.Title) and open")
            [Person]::Told("DecentAI again.")
        }
        return $null
    }

    # ------------------------------------------------------------------
    # The launcher
    # ------------------------------------------------------------------

    [string[]] Line([Engine]$engine, [string[]]$arguments, [bool]$shown) {
        $line = @("run", "--rm")
        if ($shown) {
            # A window somebody sits at, or a program's pipe.
            $line += $(if ([Console]::IsInputRedirected) { "-i" } else { "-it" })
        }
        $line += @(
            "-v", "$($engine.Socket()):/var/run/docker.sock",
            "-v", "$($this.State):/state")
        if ($env:DECENTAI_PROJECT) {
            $line += @("-e", "DECENTAI_PROJECT=$($env:DECENTAI_PROJECT)")
        }
        if ($env:DECENTAI_DNS) {
            $line += @("-e", "DECENTAI_DNS=$($env:DECENTAI_DNS)")
        }
        if ($env:DECENTAI_PASSWORD -and @("install", "reset-password") -contains $arguments[0]) {
            # By name: its value is the engine's to read, not the line's to show.
            $line += @("-e", "DECENTAI_PASSWORD")
        }
        return $line + @($this.Image) + $arguments
    }

    [string[]] Asked([string]$command, [string[]]$rest) {
        $arguments = @($command)
        if (@("install", "update") -contains $command -and $env:DECENTAI_UNSIGNED -eq "1") {
            $arguments += "--unsigned"
        }
        if ($command -eq "install" -and $env:DECENTAI_PORT) {
            $arguments += @("--port", $env:DECENTAI_PORT)
        }
        return $arguments + $rest
    }

    [bool] HasLauncher([Engine]$engine) {
        if ($engine.Has($this.Image)) {
            return $true
        }
        [Person]::Told("Downloading DecentAI's launcher...")
        if ($engine.Program.Run(@("pull", $this.Image)) -eq 0) {
            return $true
        }
        [Person]::Told("")
        [Person]::Told("The launcher ($($this.Image)) is not on this computer and could not be")
        [Person]::Told("downloaded.")
        return $false
    }

    [string] Address([Engine]$engine) {
        $status = $engine.Program.Ask($this.Line($engine, @("status"), $false))
        if ($status.Said -match '(http://localhost:\d+)') {
            return $Matches[1]
        }
        return ""
    }

    [void] Shown([string]$address) {
        if (-not $address) {
            return
        }
        if ($env:DECENTAI_NO_BROWSER -eq "1") {
            [Person]::Told("Open $address")
            return
        }
        # A window of its own; a tab of whatever browser there is,
        # where no browser here can show one.
        if (-not [Window]::new().Shows($address)) {
            Start-Process $address | Out-Null
        }
    }

    # Asked once, when somebody is there to answer: a yes makes the
    # shortcuts, a no is remembered, and either can be changed with
    # `DecentAI.cmd shortcuts`.
    [void] Offered() {
        if ($this.Kept()["shortcuts"]) {
            return
        }
        if ([Console]::IsInputRedirected -and -not $env:DECENTAI_ANSWER) {
            return
        }
        [Person]::Told("")
        if ([Person]::Agrees("Put DecentAI on the desktop and in the Start menu, to open it with a double-click?")) {
            if ($this.Shortcuts.Made()) {
                $this.Keep("shortcuts", "made")
                [Person]::Told("DecentAI is on the desktop and in the Start menu.")
            }
        } else {
            $this.Keep("shortcuts", "declined")
            [Person]::Told("It can be put there later: DecentAI.cmd shortcuts")
        }
    }

    [int] Placed() {
        if (-not $this.Shortcuts.Made()) {
            return 1
        }
        $this.Keep("shortcuts", "made")
        [Person]::Told("DecentAI is on the desktop and in the Start menu.")
        return 0
    }

    # ------------------------------------------------------------------
    # What a person asks for
    # ------------------------------------------------------------------

    # Install at first, start after that, and show it.
    [int] Open() {
        $engine = $this.Ready()
        if (-not $engine) {
            return 1
        }
        if (-not $this.HasLauncher($engine)) {
            return 1
        }
        $status = $engine.Program.Ask($this.Line($engine, @("status"), $false))
        if ($status.Said -match "is not installed") {
            [Person]::Told("Installing DecentAI on $($engine.Title).")
            $code = $engine.Program.Run(
                $this.Line($engine, $this.Asked("install", @()), $true))
        } elseif ($status.Code -ne 0) {
            [Person]::Told($status.Said.Trim())
            return 1
        } else {
            $code = $engine.Program.Run($this.Line($engine, @("start"), $true))
        }
        if ($code -ne 0) {
            return $code
        }
        $this.Remember($engine)
        $this.Shown($this.Address($engine))
        $this.Offered()
        return 0
    }

    [int] Passed([string]$command, [string[]]$rest) {
        $engine = $this.Ready()
        if (-not $engine) {
            return 1
        }
        if (-not $this.HasLauncher($engine)) {
            return 1
        }
        $code = $engine.Program.Run(
            $this.Line($engine, $this.Asked($command, $rest), $true))
        if ($code -eq 0 -and @("install", "start") -contains $command) {
            $this.Remember($engine)
        }
        return $code
    }

    [int] Engine() {
        $chosen = $this.Chosen()
        [Person]::Told("$($chosen[0].Title): $($chosen[1])")
        return 0
    }

    # A folder of the person's own agents, handed to DecentAI as a place
    # its agent sources may come from (docs/developing.md in the agent
    # template). The launcher is told the folder the way the engine
    # finds it, which only the starter knows.
    [int] Developed([string[]]$rest) {
        $engine = $this.Ready()
        if (-not $engine) {
            return 1
        }
        if (-not $this.HasLauncher($engine)) {
            return 1
        }
        $asked = @("develop")
        if ($rest.Count -gt 0 -and $rest[0] -eq "off") {
            $asked += "--off"
        } elseif ($rest.Count -gt 0) {
            # Not $folder: a class's own properties are its variables too.
            $given = $rest[0]
            if (-not (Test-Path -LiteralPath $given -PathType Container)) {
                [Person]::Told("There is no folder $given on this computer.")
                return 1
            }
            $given = (Resolve-Path -LiteralPath $given).ProviderPath
            $found = $engine.Finds($given)
            if (-not $found) {
                [Person]::Told("$given is not on a drive of this computer; choose a folder on one.")
                return 1
            }
            [Person]::Told("Handing $given to DecentAI, to read.")
            $asked += $found
        }
        return $engine.Program.Run($this.Line($engine, $asked, $true))
    }
}


$starter = [Starter]::new($PSScriptRoot)
$known = @("install", "start", "stop", "status", "update", "backup", "stop-everything", "reset-password")

if ($Command -eq "open") {
    exit $starter.Open()
} elseif ($Command -eq "engine") {
    exit $starter.Engine()
} elseif ($Command -eq "shortcuts") {
    exit $starter.Placed()
} elseif ($Command -eq "develop") {
    exit $starter.Developed($Rest)
} elseif ($known -contains $Command) {
    exit $starter.Passed($Command, $Rest)
} else {
    [Person]::Told("DecentAI.cmd [stop | status | update | backup | stop-everything | reset-password | engine | shortcuts | develop]")
    exit 2
}
