//! The window DecentAI itself is shown in: one of the app's own, so
//! that what a person sees, pins and switches to is DecentAI, under its
//! own icon, and not a browser.
//!
//! The page in it is the platform's, served by the install on this
//! computer. It is given nothing of the app: the commands the app's own
//! window may ask for are for that window alone
//! (capabilities/default.json).

use std::path::PathBuf;

use tauri::webview::NewWindowResponse;
use tauri::{AppHandle, Manager, Url, WebviewUrl, WebviewWindowBuilder};

use crate::program::Program;

pub struct Window;

impl Window {
    const LABEL: &'static str = "decentai";

    /// Show DecentAI at `address`: the window it is already in, brought
    /// to the front, or a new one.
    pub fn show(app: &AppHandle, address: &str) -> Result<(), String> {
        if let Some(open) = app.get_webview_window(Self::LABEL) {
            let _ = open.unminimize();
            let _ = open.show();
            let _ = open.set_focus();
            return Ok(());
        }
        let address: Url = address
            .parse()
            .map_err(|_| format!("'{address}' is not an address DecentAI can be opened at."))?;
        let home = address.clone();
        WebviewWindowBuilder::new(app, Self::LABEL, WebviewUrl::External(address))
            .title("DecentAI")
            .inner_size(1280.0, 860.0)
            .min_inner_size(420.0, 560.0)
            // A window the page opens itself. Its own — the sign-in
            // window of a connected account starts empty and is led to
            // the provider — is opened as asked, and stays tied to the
            // page that opened it, which waits for its answer. A link
            // to anywhere else is the person's browser's to show.
            .on_new_window(move |url, _features| {
                if Self::is_the_pages_own(&url, &home) {
                    NewWindowResponse::Allow
                } else {
                    Self::in_the_browser(url.as_str());
                    NewWindowResponse::Deny
                }
            })
            .build()
            .map(|_| ())
            .map_err(|failed| format!("DecentAI's window could not be opened: {failed}"))
    }

    /// Close it: DecentAI was stopped or removed, and the page in it
    /// has nothing behind it.
    pub fn close(app: &AppHandle) {
        if let Some(open) = app.get_webview_window(Self::LABEL) {
            let _ = open.close();
        }
    }

    fn is_the_pages_own(url: &Url, home: &Url) -> bool {
        url.scheme() == "about" || url.origin() == home.origin()
    }

    /// Hand an address to the person's own browser.
    fn in_the_browser(address: &str) -> bool {
        let (opener, places): (&str, Vec<PathBuf>) = if cfg!(windows) {
            let windows = std::env::var("SystemRoot").unwrap_or_else(|_| "C:\\Windows".into());
            ("explorer", vec![PathBuf::from(windows).join("explorer.exe")])
        } else {
            ("open", vec![PathBuf::from("/usr/bin/open")])
        };
        Program::find(opener, &places).map(|program| program.start(&[address])).unwrap_or(false)
    }

    pub fn is_the_apps_own(label: &str) -> bool {
        label != Self::LABEL
    }
}
