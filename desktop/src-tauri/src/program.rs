//! A program on this computer, as the app runs one: found on the PATH
//! or where its installer puts it, run without a console window of its
//! own, and listened to line by line.

use std::io::{BufRead, BufReader, Read};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};

/// What a program answered: how it ended and what it printed.
pub struct Said {
    pub code: i32,
    pub out: String,
    pub err: String,
}

impl Said {
    pub fn ok(&self) -> bool {
        self.code == 0
    }

    /// The last thing it complained of, or printed: what to show a
    /// person when it failed.
    pub fn why(&self) -> String {
        let said = if self.err.trim().is_empty() { &self.out } else { &self.err };
        let said = said.trim();
        // The end of a long complaint is where the reason is.
        let start = said.len().saturating_sub(600);
        let start = (start..said.len()).find(|i| said.is_char_boundary(*i)).unwrap_or(0);
        said[start..].to_string()
    }
}

#[derive(Clone)]
pub struct Program {
    path: PathBuf,
}

impl Program {
    /// The program called `name`, on the PATH or at one of `places`.
    pub fn find(name: &str, places: &[PathBuf]) -> Option<Program> {
        let file = if cfg!(windows) { format!("{name}.exe") } else { name.to_string() };
        let on_path = std::env::var_os("PATH")
            .map(|path| std::env::split_paths(&path).collect::<Vec<_>>())
            .unwrap_or_default();
        on_path
            .iter()
            .map(|folder| folder.join(&file))
            .chain(places.iter().cloned())
            .find(|candidate| candidate.is_file())
            .map(|path| Program { path })
    }

    pub fn at(path: &Path) -> Option<Program> {
        path.is_file().then(|| Program { path: path.to_path_buf() })
    }

    fn command(&self) -> Command {
        let mut command = Command::new(&self.path);
        command.stdin(Stdio::null());
        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            // No console window flashes behind the app for each call.
            const CREATE_NO_WINDOW: u32 = 0x0800_0000;
            command.creation_flags(CREATE_NO_WINDOW);
        }
        command
    }

    /// Run it to its end and hand back what it said.
    pub fn ask(&self, arguments: &[&str]) -> Said {
        match self.command().args(arguments).output() {
            Ok(done) => Said {
                code: done.status.code().unwrap_or(-1),
                out: String::from_utf8_lossy(&done.stdout).into_owned(),
                err: String::from_utf8_lossy(&done.stderr).into_owned(),
            },
            Err(failed) => Said { code: -1, out: String::new(), err: failed.to_string() },
        }
    }

    /// Run it, handing each line it prints to `heard` as it is printed.
    /// `environment` is added to the app's own for this one run.
    pub fn listen(
        &self,
        arguments: &[String],
        environment: &[(&str, &str)],
        mut heard: impl FnMut(&str),
    ) -> Said {
        let mut command = self.command();
        command.args(arguments).stdout(Stdio::piped()).stderr(Stdio::piped());
        for (name, value) in environment {
            command.env(name, value);
        }
        let mut child = match command.spawn() {
            Ok(child) => child,
            Err(failed) => {
                return Said { code: -1, out: String::new(), err: failed.to_string() }
            }
        };
        // What it complains of is read apart, so that a full pipe of
        // complaints never stops it printing.
        let complaints = child.stderr.take().map(|mut pipe| {
            std::thread::spawn(move || {
                let mut said = String::new();
                let _ = pipe.read_to_string(&mut said);
                said
            })
        });
        let mut out = String::new();
        if let Some(pipe) = child.stdout.take() {
            for line in BufReader::new(pipe).lines().map_while(Result::ok) {
                heard(&line);
                out.push_str(&line);
                out.push('\n');
            }
        }
        let code = child.wait().ok().and_then(|status| status.code()).unwrap_or(-1);
        let err = complaints.and_then(|thread| thread.join().ok()).unwrap_or_default();
        Said { code, out, err }
    }

    /// Start it and let it go: a browser window, an engine's own app.
    pub fn start(&self, arguments: &[&str]) -> bool {
        self.command().args(arguments).spawn().is_ok()
    }
}
