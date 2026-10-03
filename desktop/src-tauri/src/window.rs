//! The window DecentAI itself is shown in: a browser's, without its
//! tabs and its address bar, so that what a person sees is DecentAI and
//! not an address on their own computer. A real browser, because the
//! platform uses what one has: the microphone, the camera, notifications.

use std::path::PathBuf;

use crate::program::Program;

pub struct Window;

impl Window {
    /// Show `address`. False when nothing here could.
    pub fn show(address: &str) -> bool {
        let app = format!("--app={address}");
        if let Some(browser) = Self::browser() {
            if browser.start(&[&app]) {
                return true;
            }
        }
        Self::in_the_default_browser(address)
    }

    /// A browser that can show one page as a window of its own.
    fn browser() -> Option<Program> {
        Self::places().iter().find_map(|place| Program::at(place))
    }

    #[cfg(windows)]
    fn places() -> Vec<PathBuf> {
        let mut found = Vec::new();
        for root in ["ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA"] {
            let Ok(root) = std::env::var(root) else { continue };
            let root = PathBuf::from(root);
            // Edge, which every Windows carries, then Chrome.
            found.push(root.join("Microsoft\\Edge\\Application\\msedge.exe"));
            found.push(root.join("Google\\Chrome\\Application\\chrome.exe"));
        }
        found
    }

    #[cfg(not(windows))]
    fn places() -> Vec<PathBuf> {
        vec![
            PathBuf::from("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
            PathBuf::from("/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"),
            PathBuf::from("/Applications/Brave Browser.app/Contents/MacOS/Brave Browser"),
        ]
    }

    fn in_the_default_browser(address: &str) -> bool {
        let (opener, places): (&str, Vec<PathBuf>) = if cfg!(windows) {
            let windows = std::env::var("SystemRoot").unwrap_or_else(|_| "C:\\Windows".into());
            ("explorer", vec![PathBuf::from(windows).join("explorer.exe")])
        } else {
            ("open", vec![PathBuf::from("/usr/bin/open")])
        };
        Program::find(opener, &places).map(|program| program.start(&[address])).unwrap_or(false)
    }
}
