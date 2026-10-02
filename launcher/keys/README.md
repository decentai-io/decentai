# The keys a release is believed for

Every `*.pub` file here is an Ed25519 public key, in base64, that this
launcher accepts a release file's signature from. There are two: the
key releases are signed with (`release.pub`), and a backup kept
somewhere else (`backup.pub`), so that a lost key is not a launcher
nobody can update.

The private halves are never in this repository. The release key's is
a secret of the repository on GitHub (`RELEASE_SIGNING_KEY`), which the
release workflow signs with; the backup's is kept off any server. So a
release is as trustworthy as that GitHub account, and no more: whoever
controls it can sign one.

A key is made with `python -m launcher.publish keys <name> launcher/keys`,
from `launcher/`. It writes the public half here and prints the private
half once. A key that is here is in every launcher already out, so one
is added under a new name and never replaced.
