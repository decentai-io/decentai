//! What the app did, kept beside what it remembers: each launcher
//! command and what it said. It is what somebody is asked for when an
//! install goes wrong on a computer nobody else can see.

use std::io::Write;

use crate::kept::Kept;

pub struct Log;

impl Log {
    const FILE: &'static str = "app.log";
    /// Past this the file is started again: it is for the last few
    /// things that happened, not a history.
    const MAX_BYTES: u64 = 512 * 1024;

    pub fn write(text: &str) {
        let folder = Kept::here().folder().clone();
        let _ = std::fs::create_dir_all(&folder);
        let path = folder.join(Self::FILE);
        let large = std::fs::metadata(&path).map(|m| m.len() > Self::MAX_BYTES).unwrap_or(false);
        let file = std::fs::OpenOptions::new()
            .create(true)
            .append(!large)
            .write(true)
            .truncate(large)
            .open(&path);
        if let Ok(mut file) = file {
            let now = std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .map(|d| d.as_secs())
                .unwrap_or(0);
            let _ = writeln!(file, "[{now}] {}", text.trim_end());
        }
    }
}
