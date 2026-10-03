//! DecentAI's desktop app: the window, the icon by the clock, and what
//! they ask of the engine and the launcher.
//!
//! ```text
//! engine.rs     Docker or Podman: which, whether it runs, starting it
//! launcher.rs   the launcher's container, and what it is asked
//! release.rs    the signed release file: the version, the launcher
//! kept.rs       what is remembered between one opening and the next
//! log.rs        what the app did, for when an install goes wrong
//! window.rs     DecentAI itself, in a browser window of its own
//! program.rs    a program on this computer, run and listened to
//! ```
//!
//! docs/system/desktop-install.md describes the whole.

mod engine;
mod kept;
mod launcher;
mod log;
mod program;
mod release;
mod window;

use serde::Serialize;
use tauri::menu::{Menu, MenuItem, PredefinedMenuItem};
use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};
use tauri::{AppHandle, Emitter, Manager, WindowEvent};

use engine::Engine;
use kept::Kept;
use launcher::{Launcher, Status};
use release::Release;
use window::Window;

#[derive(Serialize)]
struct EngineState {
    name: String,
    ready: bool,
    missing: bool,
    can_install: bool,
}

#[derive(Serialize)]
struct Update {
    version: String,
}

/// What the window shows: the engine, the install, a newer release.
#[derive(Serialize)]
struct State {
    app_version: String,
    engine: EngineState,
    installed: bool,
    running: bool,
    version: String,
    address: String,
    update: Option<Update>,
}

#[derive(Serialize)]
struct Places {
    engine: String,
    places: Vec<String>,
}

#[derive(Serialize)]
struct Removed {
    kept: String,
}

/// Everything the window and the tray menu can ask for.
struct Desktop {
    app: AppHandle,
}

impl Desktop {
    fn new(app: &AppHandle) -> Desktop {
        Desktop { app: app.clone() }
    }

    /// One line of work under way, shown in the window.
    fn say(&self, text: &str) {
        let _ = self.app.emit("progress", text);
    }

    /// The launcher on the engine in use, with the release it is for.
    fn launcher(&self) -> Result<(Launcher, Option<Release>), String> {
        let kept = Kept::here();
        let engine = Engine::chosen(&kept);
        if !engine.running() {
            return Err(format!("{} is not running.", engine.title()));
        }
        let release = Release::latest().ok();
        let launcher = Launcher::ready(engine, &kept, release.as_ref(), &|t| self.say(t))?;
        Ok((launcher, release))
    }

    fn state(&self) -> Result<State, String> {
        let kept = Kept::here();
        let engine = Engine::chosen(&kept);
        let ready = engine.running();
        let mut state = State {
            app_version: self.app.package_info().version.to_string(),
            engine: EngineState {
                name: if engine.installed() { engine.title().into() } else { String::new() },
                ready,
                missing: !engine.installed(),
                can_install: Engine::can_install(),
            },
            installed: false,
            running: false,
            version: String::new(),
            address: String::new(),
            update: None,
        };
        if !ready {
            return Ok(state);
        }
        let release = Release::latest().ok();
        let launcher = match Launcher::ready(engine, &kept, release.as_ref(), &|t| self.say(t)) {
            Ok(launcher) => launcher,
            // Nothing installed and no connection: there is an engine,
            // and the setup screen says what is missing when it is used.
            Err(_) if kept.get("engine").is_empty() => return Ok(state),
            Err(why) => return Err(why),
        };
        let status = launcher.status()?;
        if status.installed || status.begun {
            // Its data is on this engine: it is not looked for on another.
            kept.keep("engine", launcher.engine.name());
        }
        state.installed = status.installed;
        state.running = status.running();
        state.update = release
            .filter(|release| status.installed && release.newer_than(&status.version))
            .map(|release| Update { version: release.version });
        state.version = status.version;
        state.address = status.address;
        Ok(state)
    }

    fn install(&self, email: &str, password: &str) -> Result<(), String> {
        let (launcher, release) = self.launcher()?;
        if release.is_none() && !launcher.unsigned() {
            return Err("DecentAI's releases could not be reached. Check the connection \
                        and try again."
                .into());
        }
        launcher.install(email, password, release.as_ref(), &|t| self.say(t))?;
        Kept::here().keep("engine", launcher.engine.name());
        Ok(())
    }

    fn start(&self) -> Result<(), String> {
        let (launcher, _) = self.launcher()?;
        self.say("Starting…");
        launcher.run(&["start"], None, &|_| {}).map(|_| ())
    }

    fn stop(&self) -> Result<(), String> {
        let (launcher, _) = self.launcher()?;
        launcher.run(&["stop"], None, &|_| {}).map(|_| ())
    }

    fn update(&self) -> Result<(), String> {
        let (launcher, release) = self.launcher()?;
        launcher.update(release.as_ref(), &|t| self.say(t))
    }

    fn open(&self) -> Result<(), String> {
        let (launcher, _) = self.launcher()?;
        let status = launcher.status()?;
        if status.address.is_empty() || !Window::show(&status.address) {
            return Err("DecentAI could not be opened in a browser on this computer.".into());
        }
        Ok(())
    }

    fn data(&self) -> Result<Places, String> {
        let kept = Kept::here();
        let engine = Engine::chosen(&kept);
        let project = Launcher::project();
        Ok(Places {
            engine: engine.title().into(),
            places: vec![
                format!("the database: volume {project}_mongo_data"),
                format!("uploaded files: volume {project}_uploads_data"),
                format!("approved agents: volumes {project}_agent_packages and {project}_agents_data"),
                format!(
                    "this install's keys and the copies of its database: volume {}",
                    Launcher::state_name()
                ),
                format!("what this app remembers: {}", kept.folder().display()),
            ],
        })
    }

    /// Take DecentAI off this computer. `keep` leaves a copy of the
    /// database in the Documents folder; `images` removes what was
    /// downloaded too.
    fn uninstall(&self, keep: bool, images: bool) -> Result<Removed, String> {
        let (launcher, _) = self.launcher()?;
        let status: Status = launcher.status()?;
        let program = launcher.engine.program()?.clone();
        let copy = launcher.uninstall(keep, &|t| self.say(t))?;

        let mut kept_at = String::new();
        if !copy.is_empty() {
            let documents = self
                .app
                .path()
                .document_dir()
                .map_err(|failed| format!("The Documents folder was not found: {failed}"))?;
            let name = format!("DecentAI backup {copy}");
            let taken = program.ask(&[
                "run", "--rm",
                "-v", &format!("{}:/state", launcher.state_volume()),
                "-v", &format!("{}:/out", documents.display()),
                "--entrypoint", "cp",
                launcher.image(),
                &format!("/state/backups/{copy}"),
                &format!("/out/{name}"),
            ]);
            if !taken.ok() {
                // The copy is still in the launcher's volume: it is left
                // there, and said, rather than removed with the rest.
                return Err(format!(
                    "DecentAI was removed, but the copy of its database could not be put in \
                     Documents. It is still in the volume {}, as backups/{copy}.\n{}",
                    launcher.state_volume(),
                    taken.why()
                ));
            }
            kept_at = documents.join(&name).display().to_string();
        }

        self.say("Removing what is left…");
        program.ask(&["volume", "rm", "-f", launcher.state_volume()]);
        if images {
            let mut names: Vec<&str> = status.images.values().map(String::as_str).collect();
            names.push(launcher.image());
            for name in names {
                program.ask(&["rmi", name]);
            }
        }
        Kept::here().forget();
        Ok(Removed { kept: kept_at })
    }

    fn start_engine(&self) -> Result<(), String> {
        Engine::chosen(&Kept::here()).start(&|t| self.say(t))
    }

    fn install_engine(&self) -> Result<(), String> {
        Engine::install_podman(&|t| self.say(t)).map(|_| ())
    }

    // ------------------------------------------------------------------
    // The window and the icon by the clock
    // ------------------------------------------------------------------

    fn show_window(&self) {
        if let Some(window) = self.app.get_webview_window("main") {
            let _ = window.show();
            let _ = window.unminimize();
            let _ = window.set_focus();
        }
    }

    /// Something the tray menu did: the window is told to look again,
    /// and shown when it failed, since the tray has nowhere to say why.
    fn from_the_tray(app: &AppHandle, work: fn(&Desktop) -> Result<(), String>) {
        let app = app.clone();
        std::thread::spawn(move || {
            let desktop = Desktop::new(&app);
            if work(&desktop).is_err() {
                desktop.show_window();
            }
            let _ = app.emit("changed", ());
        });
    }

    fn tray(app: &AppHandle) -> tauri::Result<()> {
        let item = |id: &str, text: &str| MenuItem::with_id(app, id, text, true, None::<&str>);
        let menu = Menu::with_items(
            app,
            &[
                &item("open", "Open DecentAI")?,
                &item("show", "Show this app")?,
                &PredefinedMenuItem::separator(app)?,
                &item("start", "Start")?,
                &item("stop", "Stop")?,
                &PredefinedMenuItem::separator(app)?,
                &item("quit", "Quit the app (DecentAI keeps running)")?,
            ],
        )?;
        let mut tray = TrayIconBuilder::with_id("decentai")
            .tooltip("DecentAI")
            .menu(&menu)
            .show_menu_on_left_click(false)
            .on_menu_event(|app, event| match event.id.as_ref() {
                "open" => Desktop::from_the_tray(app, Desktop::open),
                "show" => Desktop::new(app).show_window(),
                "start" => Desktop::from_the_tray(app, Desktop::start),
                "stop" => Desktop::from_the_tray(app, Desktop::stop),
                "quit" => app.exit(0),
                _ => {}
            })
            .on_tray_icon_event(|tray, event| {
                if let TrayIconEvent::Click {
                    button: MouseButton::Left,
                    button_state: MouseButtonState::Up,
                    ..
                } = event
                {
                    Desktop::new(tray.app_handle()).show_window();
                }
            });
        if let Some(icon) = app.default_window_icon() {
            tray = tray.icon(icon.clone());
        }
        tray.build(app)?;
        Ok(())
    }
}

// What the window may ask for. Each runs off the main thread: all of
// them wait on the engine.

#[tauri::command(async)]
fn state(app: AppHandle) -> Result<State, String> {
    Desktop::new(&app).state()
}

#[tauri::command(async)]
fn install(app: AppHandle, email: String, password: String) -> Result<(), String> {
    Desktop::new(&app).install(&email, &password)
}

#[tauri::command(async)]
fn start(app: AppHandle) -> Result<(), String> {
    Desktop::new(&app).start()
}

#[tauri::command(async)]
fn stop(app: AppHandle) -> Result<(), String> {
    Desktop::new(&app).stop()
}

#[tauri::command(async)]
fn update(app: AppHandle) -> Result<(), String> {
    Desktop::new(&app).update()
}

#[tauri::command(async)]
fn open(app: AppHandle) -> Result<(), String> {
    Desktop::new(&app).open()
}

#[tauri::command(async)]
fn data(app: AppHandle) -> Result<Places, String> {
    Desktop::new(&app).data()
}

#[tauri::command(async)]
fn uninstall(app: AppHandle, keep: bool, images: bool) -> Result<Removed, String> {
    Desktop::new(&app).uninstall(keep, images)
}

#[tauri::command(async)]
fn start_engine(app: AppHandle) -> Result<(), String> {
    Desktop::new(&app).start_engine()
}

#[tauri::command(async)]
fn install_engine(app: AppHandle) -> Result<(), String> {
    Desktop::new(&app).install_engine()
}

pub fn run() {
    tauri::Builder::default()
        // Opened a second time, the app that is already open is shown.
        .plugin(tauri_plugin_single_instance::init(|app, _arguments, _folder| {
            Desktop::new(app).show_window();
        }))
        .setup(|app| {
            Desktop::tray(app.handle())?;
            Ok(())
        })
        .on_window_event(|window, event| {
            // Closing the window leaves the icon by the clock: DecentAI
            // itself runs whether the app is open or not.
            if let WindowEvent::CloseRequested { api, .. } = event {
                api.prevent_close();
                let _ = window.hide();
            }
        })
        .invoke_handler(tauri::generate_handler![
            state, install, start, stop, update, open, data, uninstall, start_engine,
            install_engine
        ])
        .run(tauri::generate_context!())
        .expect("DecentAI could not start");
}
