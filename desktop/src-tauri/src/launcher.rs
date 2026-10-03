//! The launcher, as the app runs it: a container handed the engine's
//! socket and a volume of its own (launcher/launcher/__main__.py). It
//! does the installing, starting, stopping and updating; the app asks.

use serde::Deserialize;

use crate::engine::Engine;
use crate::kept::Kept;
use crate::log::Log;
use crate::release::Release;

/// What the launcher says is installed (`status --json`).
#[derive(Deserialize, Default, Clone)]
pub struct Status {
    #[serde(default)]
    pub installed: bool,
    /// A first run made its keys and did not finish.
    #[serde(default)]
    pub begun: bool,
    #[serde(default)]
    pub version: String,
    #[serde(default)]
    pub address: String,
    #[serde(default)]
    pub services: std::collections::BTreeMap<String, String>,
    #[serde(default)]
    pub images: std::collections::BTreeMap<String, String>,
}

impl Status {
    /// Up: every part is there and none has stopped.
    pub fn running(&self) -> bool {
        self.installed
            && !self.services.is_empty()
            && self.services.values().all(|state| state != "exited" && state != "absent")
    }
}

pub struct Launcher {
    pub engine: Engine,
    image: String,
    /// The launcher's own volume: the install's settings, keys and backups.
    state: String,
    /// A build of one's own, which nobody signed.
    unsigned: bool,
}

impl Launcher {
    const STATE: &'static str = "decentai_launcher";
    pub const PROJECT: &'static str = "decentai-app";

    /// The launcher for `release`, fetched if it is not on this
    /// computer yet. Without a release — no connection — the one last
    /// used is carried on with.
    pub fn ready(
        engine: Engine,
        kept: &Kept,
        release: Option<&Release>,
        say: &dyn Fn(&str),
    ) -> Result<Launcher, String> {
        let own = std::env::var("DECENTAI_LAUNCHER_IMAGE").unwrap_or_default();
        let image = if !own.is_empty() {
            own.clone()
        } else if let Some(named) = release.map(|r| r.launcher.clone()).filter(|l| !l.is_empty()) {
            named
        } else {
            kept.get("launcher")
        };
        if image.is_empty() {
            return Err("DecentAI's releases could not be reached, and nothing of it is on \
                        this computer yet. Check the connection and look again."
                .into());
        }
        let launcher = Launcher {
            engine,
            image,
            state: Self::state_name(),
            // Only with a launcher of one's own: the word alone, left set
            // on a computer, must not stop a published install updating.
            unsigned: !own.is_empty() && std::env::var("DECENTAI_UNSIGNED").as_deref() == Ok("1"),
        };
        launcher.fetch(say)?;
        if own.is_empty() {
            kept.keep("launcher", &launcher.image);
        }
        Ok(launcher)
    }

    fn fetch(&self, say: &dyn Fn(&str)) -> Result<(), String> {
        let program = self.engine.program()?;
        if program.ask(&["image", "inspect", &self.image, "--format", "{{.Id}}"]).ok() {
            return Ok(());
        }
        say("Downloading DecentAI's launcher…");
        let pulled = program.ask(&["pull", &self.image]);
        pulled
            .ok()
            .then_some(())
            .ok_or_else(|| format!("The launcher could not be downloaded.\n{}", pulled.why()))
    }

    /// The launcher's own volume, by name.
    pub fn state_name() -> String {
        std::env::var("DECENTAI_LAUNCHER_STATE")
            .ok()
            .filter(|said| !said.is_empty())
            .unwrap_or_else(|| Self::STATE.into())
    }

    pub fn project() -> String {
        std::env::var("DECENTAI_PROJECT")
            .ok()
            .filter(|said| !said.is_empty())
            .unwrap_or_else(|| Self::PROJECT.into())
    }

    pub fn unsigned(&self) -> bool {
        self.unsigned
    }

    pub fn state_volume(&self) -> &str {
        &self.state
    }

    pub fn image(&self) -> &str {
        &self.image
    }

    /// The engine's command line for one launcher command.
    fn line(&self, arguments: &[String], with_password: bool) -> Vec<String> {
        let mut line: Vec<String> = vec![
            "run".into(),
            "--rm".into(),
            "-v".into(),
            format!("{}:/var/run/docker.sock", self.engine.socket()),
            "-v".into(),
            format!("{}:/state", self.state),
        ];
        for name in ["DECENTAI_PROJECT", "DECENTAI_DNS"] {
            if let Ok(value) = std::env::var(name) {
                if !value.is_empty() {
                    line.push("-e".into());
                    line.push(format!("{name}={value}"));
                }
            }
        }
        if with_password {
            // By name: its value is the engine's to read, not the line's to show.
            line.push("-e".into());
            line.push("DECENTAI_PASSWORD".into());
        }
        line.push(self.image.clone());
        line.extend(arguments.iter().cloned());
        line
    }

    /// One launcher command, each line it prints handed to `say`.
    /// Returns everything it printed, or why it failed in its own words.
    pub fn run(
        &self,
        arguments: &[&str],
        password: Option<&str>,
        say: &dyn Fn(&str),
    ) -> Result<String, String> {
        let arguments: Vec<String> = arguments.iter().map(|a| a.to_string()).collect();
        let environment: Vec<(&str, &str)> =
            password.map(|p| vec![("DECENTAI_PASSWORD", p)]).unwrap_or_default();
        let said = self.engine.program()?.listen(
            &self.line(&arguments, password.is_some()),
            &environment,
            |line| say(line),
        );
        // The password travels in the environment, never in the line.
        Log::write(&format!(
            "launcher {} -> {}
{}{}",
            arguments.join(" "),
            said.code,
            said.out,
            said.err
        ));
        if said.ok() {
            Ok(said.out)
        } else {
            Err(said.why())
        }
    }

    pub fn status(&self) -> Result<Status, String> {
        let said = self.run(&["status", "--json"], None, &|_| {})?;
        said.lines()
            .rev()
            .find_map(|line| serde_json::from_str::<Status>(line.trim()).ok())
            .ok_or_else(|| {
                "This launcher is older than the app and cannot say what is installed. \
                 Check for updates."
                    .to_string()
            })
    }

    /// The arguments that name the release to install: the published
    /// one the app read, or a build of one's own.
    fn release_arguments(&self, release: Option<&Release>) -> Vec<String> {
        if self.unsigned {
            return vec!["--unsigned".into()];
        }
        match release {
            Some(release) => vec!["--release".into(), release.address()],
            None => Vec::new(),
        }
    }

    pub fn install(
        &self,
        email: &str,
        password: &str,
        release: Option<&Release>,
        say: &dyn Fn(&str),
    ) -> Result<(), String> {
        let mut arguments = vec!["install".to_string(), "--email".into(), email.into()];
        if let Ok(port) = std::env::var("DECENTAI_PORT") {
            if !port.is_empty() {
                arguments.push("--port".into());
                arguments.push(port);
            }
        }
        arguments.extend(self.release_arguments(release));
        let arguments: Vec<&str> = arguments.iter().map(String::as_str).collect();
        self.run(&arguments, Some(password), say).map(|_| ())
    }

    pub fn update(&self, release: Option<&Release>, say: &dyn Fn(&str)) -> Result<(), String> {
        let mut arguments = vec!["update".to_string(), "--yes".into()];
        arguments.extend(self.release_arguments(release));
        let arguments: Vec<&str> = arguments.iter().map(String::as_str).collect();
        self.run(&arguments, None, say).map(|_| ())
    }

    /// Take the stack off this computer. Returns the name of the copy
    /// of the database it kept, in the launcher's volume, when asked to.
    pub fn uninstall(&self, keep: bool, say: &dyn Fn(&str)) -> Result<String, String> {
        let mut arguments = vec!["uninstall", "--yes"];
        if keep {
            arguments.push("--keep");
        }
        let said = self.run(&arguments, None, say)?;
        Ok(said
            .lines()
            .find_map(|line| line.trim().strip_prefix("Kept: "))
            .unwrap_or_default()
            .to_string())
    }
}
