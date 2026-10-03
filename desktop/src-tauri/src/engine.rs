//! The container engine DecentAI runs on: Docker where it is running,
//! Podman otherwise — and, once an install is made on one, that one
//! for good, because its data is there.

use std::path::PathBuf;
use std::time::{Duration, Instant};

use crate::kept::Kept;
use crate::program::Program;

#[derive(Clone, Copy, PartialEq)]
pub enum Kind {
    Docker,
    Podman,
}

#[derive(Clone)]
pub struct Engine {
    pub kind: Kind,
    program: Option<Program>,
}

impl Engine {
    /// How long something that was started is waited for.
    const PATIENCE: Duration = Duration::from_secs(180);

    pub fn docker() -> Engine {
        let places = if cfg!(windows) {
            vec![Self::program_files().join("Docker\\Docker\\resources\\bin\\docker.exe")]
        } else {
            vec![PathBuf::from("/usr/local/bin/docker"), PathBuf::from("/opt/homebrew/bin/docker")]
        };
        Engine { kind: Kind::Docker, program: Program::find("docker", &places) }
    }

    pub fn podman() -> Engine {
        let places = if cfg!(windows) {
            vec![Self::program_files().join("RedHat\\Podman\\podman.exe")]
        } else {
            vec![
                PathBuf::from("/opt/podman/bin/podman"),
                PathBuf::from("/opt/homebrew/bin/podman"),
                PathBuf::from("/usr/local/bin/podman"),
            ]
        };
        Engine { kind: Kind::Podman, program: Program::find("podman", &places) }
    }

    /// The engine to use. Nothing is started or installed by asking.
    pub fn chosen(kept: &Kept) -> Engine {
        match kept.get("engine").as_str() {
            "docker" => return Engine::docker(),
            "podman" => return Engine::podman(),
            _ => {}
        }
        let docker = Engine::docker();
        if docker.running() {
            return docker;
        }
        let podman = Engine::podman();
        if podman.installed() || !docker.installed() {
            return podman;
        }
        docker
    }

    fn program_files() -> PathBuf {
        PathBuf::from(std::env::var("ProgramFiles").unwrap_or_else(|_| "C:\\Program Files".into()))
    }

    pub fn name(&self) -> &'static str {
        match self.kind {
            Kind::Docker => "docker",
            Kind::Podman => "podman",
        }
    }

    pub fn title(&self) -> &'static str {
        match self.kind {
            Kind::Docker => "Docker",
            Kind::Podman => "Podman",
        }
    }

    pub fn installed(&self) -> bool {
        self.program.is_some()
    }

    pub fn program(&self) -> Result<&Program, String> {
        self.program.as_ref().ok_or_else(|| format!("{} is not installed on this computer.", self.title()))
    }

    pub fn running(&self) -> bool {
        let Some(program) = &self.program else { return false };
        match self.kind {
            Kind::Docker => program.ask(&["version", "--format", "{{.Server.Version}}"]).ok(),
            Kind::Podman => program.ask(&["info", "--format", "{{.Host.RemoteSocket.Path}}"]).ok(),
        }
    }

    /// Where the engine listens, as a container is handed it.
    pub fn socket(&self) -> String {
        match (self.kind, &self.program) {
            (Kind::Podman, Some(program)) => program
                .ask(&["info", "--format", "{{.Host.RemoteSocket.Path}}"])
                .out
                .lines()
                .map(str::trim)
                .find(|line| line.starts_with("unix://") || line.starts_with('/'))
                .map(|line| line.trim_start_matches("unix://").to_string())
                .unwrap_or_else(|| "/run/podman/podman.sock".into()),
            _ => "/var/run/docker.sock".into(),
        }
    }

    // ------------------------------------------------------------------
    // Starting it
    // ------------------------------------------------------------------

    /// Start the engine and wait for it to answer. `say` hears what is
    /// being done.
    pub fn start(&self, say: &dyn Fn(&str)) -> Result<(), String> {
        if self.running() {
            return Ok(());
        }
        match self.kind {
            Kind::Docker => self.start_docker(say),
            Kind::Podman => self.start_podman(say),
        }
    }

    fn start_docker(&self, say: &dyn Fn(&str)) -> Result<(), String> {
        say("Starting Docker Desktop…");
        let started = if cfg!(windows) {
            Program::at(&Self::program_files().join("Docker\\Docker\\Docker Desktop.exe"))
                .map(|desktop| desktop.start(&[]))
                .unwrap_or(false)
        } else {
            Program::at(&PathBuf::from("/usr/bin/open"))
                .map(|open| open.start(&["-a", "Docker"]))
                .unwrap_or(false)
        };
        if !started {
            return Err("Docker Desktop could not be started from here. Open it yourself, \
                        then look again."
                .into());
        }
        self.wait_until_running()
            .then_some(())
            .ok_or_else(|| "Docker Desktop was started and did not answer in three minutes.".into())
    }

    fn start_podman(&self, say: &dyn Fn(&str)) -> Result<(), String> {
        let program = self.program()?;
        if !program.ask(&["machine", "inspect", "--format", "{{.State}}"]).ok() {
            say("Preparing Podman. This is done once…");
            let made = program.ask(&["machine", "init"]);
            if !made.ok() {
                return Err(format!("Podman could not be prepared.\n{}", made.why()));
            }
        }
        say("Starting Podman…");
        let started = program.ask(&["machine", "start"]);
        if self.wait_until_running() {
            return Ok(());
        }
        Err(format!(
            "Podman did not start.\n{}\n\nOn Windows this is most often an old Windows \
             Subsystem for Linux: run `wsl --update` in a terminal, then look again.",
            started.why()
        ))
    }

    fn wait_until_running(&self) -> bool {
        let until = Instant::now() + Self::PATIENCE;
        while Instant::now() < until {
            if self.running() {
                return true;
            }
            std::thread::sleep(Duration::from_secs(3));
        }
        false
    }

    // ------------------------------------------------------------------
    // Installing Podman, where there is nothing
    // ------------------------------------------------------------------

    /// Whether the app can install Podman on this computer itself.
    pub fn can_install() -> bool {
        Self::installer().is_some()
    }

    fn installer() -> Option<Program> {
        if cfg!(windows) {
            let apps = PathBuf::from(std::env::var("LOCALAPPDATA").unwrap_or_default())
                .join("Microsoft\\WindowsApps\\winget.exe");
            Program::find("winget", &[apps])
        } else {
            Program::find(
                "brew",
                &[PathBuf::from("/opt/homebrew/bin/brew"), PathBuf::from("/usr/local/bin/brew")],
            )
        }
    }

    /// Install Podman with the system's own installer, and start it.
    pub fn install_podman(say: &dyn Fn(&str)) -> Result<Engine, String> {
        let installer = Self::installer().ok_or_else(|| {
            "This computer has nothing to install Podman with. Install it from \
             https://podman.io, then look again."
                .to_string()
        })?;
        say("Installing Podman. It asks for your permission once…");
        let done = if cfg!(windows) {
            installer.ask(&[
                "install", "--id", "RedHat.Podman", "--exact", "--silent",
                "--accept-package-agreements", "--accept-source-agreements",
                "--disable-interactivity",
            ])
        } else {
            installer.ask(&["install", "podman"])
        };
        let podman = Engine::podman();
        if !podman.installed() {
            return Err(format!("Podman was not installed.\n{}", done.why()));
        }
        podman.start(say)?;
        Ok(podman)
    }
}
