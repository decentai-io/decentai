//! The current release, as the app reads it: the signed file the
//! release workflow publishes (launcher/launcher/release.py), believed
//! for its signature and for nothing else.
//!
//! The app reads it for two things — the version, to say when a newer
//! one exists, and the launcher's image, which it fetches by that name.
//! The launcher reads the same file again itself before it installs.

use std::time::Duration;

use base64::Engine as _;
use ed25519_dalek::{Signature, Verifier, VerifyingKey};

pub struct Release {
    pub version: String,
    /// The launcher's image for this release, by its digest.
    pub launcher: String,
}

impl Release {
    const LATEST: &'static str =
        "https://github.com/decentai-io/decentai/releases/latest/download/release.json";
    /// The public keys a release is believed for: the ones the launcher
    /// carries, from the same files.
    const KEYS: [&'static str; 2] = [
        include_str!("../../../launcher/keys/release.pub"),
        include_str!("../../../launcher/keys/backup.pub"),
    ];
    const PATIENCE: Duration = Duration::from_secs(20);
    const MAX_BYTES: usize = 64 * 1024;

    /// The release published last, or why it could not be read.
    pub fn latest() -> Result<Release, String> {
        let raw = Self::fetch(Self::LATEST)?;
        let signature = Self::fetch(&format!("{}.sig", Self::LATEST))?;
        Self::read(&raw, &signature)
    }

    /// Where exactly this release's file is: what the launcher is told
    /// to install, so that it installs the one the app read.
    pub fn address(&self) -> String {
        format!(
            "https://github.com/decentai-io/decentai/releases/download/v{}/release.json",
            self.version
        )
    }

    fn fetch(address: &str) -> Result<Vec<u8>, String> {
        let answer = ureq::get(address)
            .timeout(Self::PATIENCE)
            .call()
            .map_err(|failed| format!("The releases page could not be reached: {failed}"))?;
        let mut raw = Vec::new();
        std::io::Read::read_to_end(
            &mut std::io::Read::take(answer.into_reader(), Self::MAX_BYTES as u64 + 1),
            &mut raw,
        )
        .map_err(|failed| format!("The release could not be read: {failed}"))?;
        if raw.len() > Self::MAX_BYTES {
            return Err("The release file is larger than one can be.".into());
        }
        Ok(raw)
    }

    /// The release in `raw`, when one of the app's keys signed exactly
    /// those bytes.
    pub fn read(raw: &[u8], signature: &[u8]) -> Result<Release, String> {
        if !Self::signed(raw, signature) {
            return Err("The release is not signed by a key this app accepts, so it is \
                        not installed."
                .into());
        }
        let document: serde_json::Value = serde_json::from_slice(raw)
            .map_err(|failed| format!("The release file cannot be read: {failed}"))?;
        let text = |name: &str| document[name].as_str().unwrap_or_default().to_string();
        let release = Release { version: text("version"), launcher: text("launcher") };
        if Self::number(&release.version) == (0, 0, 0) {
            return Err("The release file names no version.".into());
        }
        Ok(release)
    }

    fn signed(raw: &[u8], signature: &[u8]) -> bool {
        let decode = |text: &[u8]| {
            let text = String::from_utf8_lossy(text);
            base64::engine::general_purpose::STANDARD.decode(text.trim()).ok()
        };
        let Some(signature) = decode(signature).and_then(|bytes| Signature::from_slice(&bytes).ok())
        else {
            return false;
        };
        Self::KEYS.iter().any(|key| {
            decode(key.as_bytes())
                .and_then(|bytes| <[u8; 32]>::try_from(bytes).ok())
                .and_then(|bytes| VerifyingKey::from_bytes(&bytes).ok())
                .map(|key| key.verify(raw, &signature).is_ok())
                .unwrap_or(false)
        })
    }

    /// A version as something to compare: its three numbers.
    pub fn number(version: &str) -> (u64, u64, u64) {
        let mut parts = version
            .split(|c: char| c == '.' || c == '-')
            .map(|part| part.parse::<u64>().unwrap_or(0));
        (parts.next().unwrap_or(0), parts.next().unwrap_or(0), parts.next().unwrap_or(0))
    }

    pub fn newer_than(&self, version: &str) -> bool {
        Self::number(&self.version) > Self::number(version)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    // Release 0.2.2 as it was published, and its signature.
    const PUBLISHED: &[u8] = include_bytes!("../tests/release-0.2.2.json");
    const SIGNATURE: &[u8] = include_bytes!("../tests/release-0.2.2.json.sig");

    #[test]
    fn a_published_release_is_believed() {
        let release = Release::read(PUBLISHED, SIGNATURE).expect("signed by the release key");
        assert_eq!(release.version, "0.2.2");
        assert!(release.newer_than("0.2.1") && !release.newer_than("0.2.2"));
    }

    #[test]
    fn one_byte_changed_and_it_is_not() {
        let mut changed = PUBLISHED.to_vec();
        let at = changed.iter().position(|byte| *byte == b'2').unwrap();
        changed[at] = b'9';
        assert!(Release::read(&changed, SIGNATURE).is_err());
        assert!(Release::read(PUBLISHED, b"not a signature").is_err());
    }

    #[test]
    fn versions_compare_by_their_numbers() {
        assert!(Release::number("0.10.0") > Release::number("0.9.9"));
        assert_eq!(Release::number("1.4.0-local"), (1, 4, 0));
    }
}
