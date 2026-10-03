//! What the app keeps between one opening and the next: which engine
//! the install was made on, and the launcher it was last run with.
//!
//! The file is the one the starter scripts wrote (`starter.json` in the
//! app's own folder), so an install made with a script is found by the
//! app and carried on with.

use std::collections::BTreeMap;
use std::path::PathBuf;

pub struct Kept {
    folder: PathBuf,
}

impl Kept {
    const FILE: &'static str = "starter.json";

    pub fn here() -> Kept {
        let folder = match std::env::var("DECENTAI_STARTER_HOME") {
            Ok(said) if !said.is_empty() => PathBuf::from(said),
            _ => Self::own_folder(),
        };
        Kept { folder }
    }

    fn own_folder() -> PathBuf {
        if cfg!(windows) {
            PathBuf::from(std::env::var("LOCALAPPDATA").unwrap_or_default()).join("DecentAI")
        } else {
            PathBuf::from(std::env::var("HOME").unwrap_or_default())
                .join("Library/Application Support/DecentAI")
        }
    }

    pub fn folder(&self) -> &PathBuf {
        &self.folder
    }

    fn all(&self) -> BTreeMap<String, String> {
        std::fs::read_to_string(self.folder.join(Self::FILE))
            .ok()
            // PowerShell writes the file with a mark at its start.
            .map(|text| text.trim_start_matches('\u{feff}').to_string())
            .and_then(|text| serde_json::from_str::<BTreeMap<String, serde_json::Value>>(&text).ok())
            .map(|found| {
                found
                    .into_iter()
                    .map(|(name, value)| {
                        let value = value.as_str().map(str::to_string).unwrap_or_else(|| value.to_string());
                        (name, value)
                    })
                    .collect()
            })
            .unwrap_or_default()
    }

    pub fn get(&self, name: &str) -> String {
        self.all().get(name).cloned().unwrap_or_default()
    }

    pub fn keep(&self, name: &str, value: &str) {
        let mut all = self.all();
        if all.get(name).map(String::as_str) == Some(value) {
            return;
        }
        all.insert(name.to_string(), value.to_string());
        let _ = std::fs::create_dir_all(&self.folder);
        if let Ok(text) = serde_json::to_string_pretty(&all) {
            let _ = std::fs::write(self.folder.join(Self::FILE), text);
        }
    }

    /// Forget the install: the next one chooses its engine afresh.
    pub fn forget(&self) {
        let _ = std::fs::remove_file(self.folder.join(Self::FILE));
    }
}
