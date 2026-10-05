"""Fetching agent code from a git repository.

Acquisition is the BACKEND's job, not the runtime's. Where code comes
from — git today, an uploaded archive or an object store tomorrow — is a
question about sources, and sources multiply; the runtime's job is to
receive code it has been handed, verify it, and import it. Keeping this
here means a new kind of source is a new module beside this one, not a
new capability in the process that runs agent code.

Two properties this file exists to guarantee:

* **A commit, not a branch.** A ref names a moving target; an install
  records the SHA it resolved to, and every later fetch asks for that
  exact commit. Code approved once cannot change underneath the approval.
* **Nothing runs.** Cloning and reading a manifest execute no agent code.
  Importing happens later, separately, only after an administrator has
  approved what the manifest says.

Credentials arrive per call, are handed to git through its askpass
protocol rather than the command line or the remote URL, and are never
written to disk or into a log line.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, Optional, Tuple

import yaml

from contracts.agent_samples import SampleSheet

from server.custom_logging import CustomLoggerFactory
from util import remove_tree

MANIFEST_FILENAME = "manifest.yaml"
CATALOG_FILENAME = "decentai-agents.yaml"
CATALOG_ID_RE = re.compile(r"^[a-z][a-z0-9_]{1,63}$")

# A ref names a branch, a tag, or a commit — nothing else, and in
# particular nothing beginning with '-'. git parses options anywhere in
# its argument list, so a "ref" of --upload-pack=<command> is not a ref
# at all: it tells git which program to run for the transfer, and for a
# local remote git runs it through a shell. Passing the argument list as
# a list rather than a string does not help, because the injection is an
# ARGUMENT, not shell syntax. Refusing the shape is the fix.
REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/+-]{0,254}$")

# Where code may be fetched from. https is the norm and ssh is the other
# way a company reaches a private repository. Everything else is refused
# by name rather than by omission:
#   file:// and bare paths — reads any repository on the backend's disk
#   ext::   — hands git a command line to execute
#   git://  — unauthenticated and unencrypted
ALLOWED_SCHEMES = ("https://", "ssh://", "git+ssh://")
#: A test seam, never a setting: a suite that builds a repository on
#: disk flips this to fetch from it. Deployments never do.
ALLOW_LOCAL_REPOSITORIES = False
#: The one exception a deployment may make, by saying so
#: (AGENT_SOURCE_FOLDER): a folder whose repositories may be sources —
#: on a person's own computer, the folder they write agents in, handed
#: to the backend read-only. Read at each call, so a test may set it.
LOCAL_SOURCE_SETTING = "agent_source_folder"


def local_source_folder() -> Optional[Path]:
    """The folder local sources may come from, or None: every server."""
    folder = os.environ.get("AGENT_SOURCE_FOLDER", "")
    try:
        from server.setup.app_state import get_state

        settings = get_state().settings
        folder = getattr(settings, LOCAL_SOURCE_SETTING, "") or folder
    except Exception:
        pass
    folder = str(folder or "").strip()
    return Path(folder).resolve() if folder else None


def local_source(url: str) -> Optional[str]:
    """``file://`` and the path, when ``url`` names a repository inside
    the local source folder; None when it is not a local address at all.
    Raises RepositoryError for one that is local and not allowed."""
    if url.startswith("file://"):
        from urllib.parse import urlsplit
        from urllib.request import url2pathname

        written = url2pathname(urlsplit(url).path)
    else:
        written = url
    if not Path(written).is_absolute():
        return None
    folder = local_source_folder()
    if folder is None:
        return None
    path = Path(written).resolve()
    try:
        path.relative_to(folder)
    except ValueError:
        raise RepositoryError(
            f"Only the repositories in {folder.as_posix()} may be added from "
            f"this computer; '{written}' is not one of them.")
    if not (path / ".git").exists():
        raise RepositoryError(
            f"'{path.as_posix()}' is not a git repository. Run `git init` "
            f"there and commit what you want to try: a source is read at a "
            f"commit, as it would be from anywhere else.")
    return path.as_uri()
SCP_STYLE_RE = re.compile(r"^[A-Za-z0-9._-]+@[A-Za-z0-9.-]+:[A-Za-z0-9._/~-]+$")


def clean_ref(ref: Any) -> str:
    """The ref to fetch, or a refusal. Empty means the default branch."""
    ref = str(ref or "").strip()
    if not ref:
        return ""
    if not REF_RE.match(ref) or ".." in ref or "@{" in ref:
        raise RepositoryError(
            f"'{ref}' is not a branch, tag or commit id. A ref may use "
            f"letters, digits, dot, dash, underscore and slash, and may "
            f"not begin with a dash."
        )
    return ref


def clean_url(url: Any) -> str:
    """The repository URL to fetch from, or a refusal."""
    url = str(url or "").strip()
    if not url:
        raise RepositoryError("A repository URL is required.")
    if url.startswith("-"):
        raise RepositoryError("A repository URL cannot begin with a dash.")
    if url.startswith(ALLOWED_SCHEMES) or SCP_STYLE_RE.match(url):
        return url
    if ALLOW_LOCAL_REPOSITORIES and url.startswith("file://"):
        return url
    local = local_source(url)
    if local is not None:
        return local
    raise RepositoryError(
        "A repository URL must be https://, ssh://, or user@host:path. "
        "Local paths and other transports are refused: agent code is "
        "fetched from a repository, never from this server's own disk."
    )

#: A monorepo, not a marketplace. Well past any real repository, and
#: short of a catalog nobody could review before approving it.
MAX_CATALOG_AGENTS = 100


class RepositoryError(RuntimeError):
    """A fetch failed. The message is meant for an administrator to read."""


class Repository:
    TIMEOUT_SECONDS = 120

    def __init__(self):
        self.logger = CustomLoggerFactory.get_logger(self.__class__.__name__)

    # ------------------------------------------------------------------
    def fetch(
        self,
        url: str,
        ref: str = "",
        into: Optional[Path] = None,
        credential: Optional[Dict[str, str]] = None,
    ) -> Tuple[Path, str]:
        """Clone ``url`` at ``ref`` (a branch, tag or commit; default the
        remote's own default branch) into ``into`` or a temporary
        directory. Returns (folder, resolved SHA).

        A fetch that fails leaves nothing behind. The caller only learns
        the folder's name by being handed it, so a temporary directory
        made here and then abandoned is one nobody can ever delete — and
        a source whose credential expired is refreshed on a timer, which
        turns that into a slow leak rather than a one-off."""
        # Both are checked before a directory exists, so a refused call
        # leaves nothing behind to clean up.
        url = clean_url(url)
        ref = clean_ref(ref)

        borrowed = into is not None
        target = Path(into) if borrowed else Path(tempfile.mkdtemp(prefix="agent-"))
        if target.exists() and any(target.iterdir()):
            shutil.rmtree(target, ignore_errors=True)
        target.mkdir(parents=True, exist_ok=True)

        try:
            with self._askpass(credential) as answers, \
                    self._local_trust(url) as trusted:
                env = {**self._environment(answers), **trusted}
                self._git(["init", "--quiet"], target, env)
                self._git(["remote", "add", "origin", url], target, env)
                # Fetching one ref keeps a large history from arriving
                # with it. No ref means the remote's DEFAULT branch, and
                # that has to be said as `HEAD`: a bare fetch takes every
                # branch, and FETCH_HEAD is then whichever one git listed
                # first — so a repository's second branch could quietly
                # become what "refresh" reads forever. '--' ends the
                # options, so even a ref that got past clean_ref could
                # not become one.
                fetch_args = ["fetch", "--depth", "1", "--quiet", "origin",
                              "--", ref or "HEAD"]
                self._git(fetch_args, target, env)
                self._git(["checkout", "--quiet", "FETCH_HEAD"], target, env)

                sha = self._git(["rev-parse", "HEAD"], target, env).strip()
        except BaseException:
            # A directory this method created is this method's to remove,
            # and only that one: a folder the caller chose is the
            # caller's. Failure is the common case here — a bad ref, a
            # private repo, a repository that moved — and every one of
            # those used to leave a checkout behind whose .git/config
            # holds the remote URL, credentials and all.
            if not borrowed:
                remove_tree(target)
            raise
        return target, sha

    def read_manifest(self, folder: Path) -> str:
        path = Path(folder) / MANIFEST_FILENAME
        if path.is_symlink():
            # Git checks a link out as a link, and following it would
            # read whatever file it names on this machine.
            raise RepositoryError(
                f"{MANIFEST_FILENAME} in '{Path(folder).name or '.'}' is a "
                f"symbolic link, which is not allowed.")
        if not path.is_file():
            raise RepositoryError(
                f"No {MANIFEST_FILENAME} in '{Path(folder).name or '.'}'."
            )
        try:
            return path.read_text(encoding="utf-8")
        except OSError as exc:
            raise RepositoryError(f"Cannot read {MANIFEST_FILENAME}: {exc}") from exc

    def read_manifest_document(self, folder: Path) -> Dict[str, Any]:
        """The manifest at ``folder``, parsed.

        A manifest that is not YAML — or is a list, or a bare string — is
        a repository this platform cannot read, not an exception on its
        way to somewhere unrelated. `acquire` catches RepositoryError and
        nothing else, so a YAMLError escaping from here would surface as
        a 500 rather than as a sentence about the repository."""
        try:
            document = yaml.safe_load(self.read_manifest(folder))
        except yaml.YAMLError as exc:
            raise RepositoryError(
                f"{MANIFEST_FILENAME} in "
                f"'{Path(folder).name or '.'}' is not valid YAML: {exc}"
            ) from exc
        if not isinstance(document, dict):
            raise RepositoryError(
                f"{MANIFEST_FILENAME} in "
                f"'{Path(folder).name or '.'}' must be a mapping."
            )
        return document

    def discover(self, folder: Path) -> Dict:
        """Return a validated catalog without importing repository code.

        Two shapes arrive here and one leaves. A repository holding a
        single agent may put its manifest at the root and skip the
        catalog altogether, and that is exposed as a one-item catalog so
        that nothing downstream has to know which kind it read.
        """
        root = Path(folder).resolve()
        catalog_path = root / CATALOG_FILENAME
        if catalog_path.is_symlink():
            raise RepositoryError(
                f"{CATALOG_FILENAME} is a symbolic link, which is not allowed.")
        if not catalog_path.is_file():
            return self._single_agent_catalog(root)

        metadata, entries = self._catalog(catalog_path)
        seen, discovered = set(), []
        for entry in entries:
            found = self._entry(root, entry, seen)
            seen.add(found["id"])
            discovered.append(found)
        return {"schema_version": "1.0", "catalog": metadata,
                "agents": discovered}

    def _single_agent_catalog(self, root: Path) -> Dict:
        """The catalog a root manifest implies, for a repository that
        holds one agent and never wrote one."""
        if not (root / MANIFEST_FILENAME).is_file():
            raise RepositoryError(
                f"This repository offers no agents. Expected "
                f"'{CATALOG_FILENAME}' at its root, listing each agent "
                f"with the folder it lives in — or, for a repository "
                f"holding a single agent, a '{MANIFEST_FILENAME}' at the "
                f"root instead. Both names are exact, and both are read "
                f"from the root of the branch or tag you named."
            )
        document = self.read_manifest_document(root)
        return {
            "schema_version": "1.0",
            "catalog": {"id": "repository", "name": "Repository agent"},
            "agents": [{
                "id": str((document.get("agent") or {}).get("id") or ""),
                "path": ".",
                "manifest": document,
                "samples": self._samples(root, document),
            }],
        }

    @staticmethod
    def _catalog(path: Path) -> Tuple[Dict, list]:
        """The catalog file's own shape: metadata and agent entries.

        Nothing about the agents themselves is decided here — only that
        this is a document this platform knows how to read, and that the
        list in it is a list of a sane size."""
        try:
            catalog = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            raise RepositoryError(
                f"{CATALOG_FILENAME} is invalid: {exc}") from exc

        if (not isinstance(catalog, dict)
                or str(catalog.get("schema_version")) != "1.0"):
            raise RepositoryError(
                f"{CATALOG_FILENAME} must use schema_version 1.0.")

        metadata = catalog.get("catalog") or {}
        if (not isinstance(metadata, dict)
                or not CATALOG_ID_RE.fullmatch(str(metadata.get("id") or ""))):
            raise RepositoryError("catalog.id must be a lowercase identifier.")

        entries = catalog.get("agents")
        if (not isinstance(entries, list) or not entries
                or len(entries) > MAX_CATALOG_AGENTS):
            raise RepositoryError(
                f"agents must contain between 1 and "
                f"{MAX_CATALOG_AGENTS} entries.")
        return metadata, entries

    def _entry(self, root: Path, entry: Any, seen: set) -> Dict:
        """One catalog entry, checked against the checkout it names.

        Every refusal here is about a repository reaching somewhere it
        should not: a path that climbs out of the checkout, a link that
        points anywhere it likes, or a manifest whose agent.id disagrees
        with the catalog — which would let one entry's approval install
        a different agent's code.
        """
        if not isinstance(entry, dict):
            raise RepositoryError("Every catalog agent must be an object.")

        local_id = str(entry.get("id") or "")
        relative = str(entry.get("path") or "")
        if not CATALOG_ID_RE.fullmatch(local_id) or local_id in seen:
            raise RepositoryError(
                f"Invalid or duplicate catalog agent id '{local_id}'.")

        # Asked of the path as written: resolved, a link is no longer
        # one, and a folder that only points at another was read as an
        # agent's own.
        linked = (root / relative).is_symlink()
        target = (root / relative).resolve()
        try:
            target.relative_to(root)
        except ValueError as exc:
            raise RepositoryError(
                f"Agent path '{relative}' escapes the repository.") from exc
        if linked or not (target / MANIFEST_FILENAME).is_file():
            raise RepositoryError(
                f"Agent '{local_id}' has no safe manifest at '{relative}'.")
        if any(path.is_symlink() for path in target.rglob("*")):
            raise RepositoryError(
                f"Agent '{local_id}' contains a symbolic link, which is "
                f"not allowed.")

        document = self.read_manifest_document(target)
        declared = str((document.get("agent") or {}).get("id") or "")
        if declared != local_id:
            raise RepositoryError(
                f"Catalog id '{local_id}' does not match manifest "
                f"agent.id '{declared}'."
            )
        return {"id": local_id, "path": relative, "manifest": document,
                "samples": self._samples(target, document)}

    @staticmethod
    def _samples(folder: Path, document: Dict) -> Optional[Dict]:
        """What the agent's sample sheet would load, or None when it
        ships none. A sheet that is wrong is listed with its errors, so
        the marketplace can say so before anyone installs."""
        sheet = SampleSheet.read(folder, document)
        return sheet.summary() if sheet else None

    # ------------------------------------------------------------------
    @staticmethod
    def _environment(extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        return {
            **os.environ,
            # Never let git stop to ask a human: this runs in a server.
            "GIT_TERMINAL_PROMPT": "0",
            # Neither the system config nor the process user's own may
            # speak here. A ~/.gitconfig is not part of this deployment's
            # reviewed behaviour, and two of its settings matter a great
            # deal: a credential.helper would write a tenant's token to
            # the host's disk, and protocol.ext.allow would re-enable the
            # transport that hands git a command line to run.
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            **(extra or {}),
        }

    @contextmanager
    def _local_trust(self, url: str) -> Iterator[Dict[str, str]]:
        """What lets git read a repository on this machine's own disk.

        git refuses a repository its user does not own, and a folder a
        person handed in from their computer is never the server's
        user's. For a local source, and for the one fetch, git is given
        a configuration of its own saying the folder may be read — as
        its global file, because git clears command-line configuration
        before it runs the process that reads a local repository. It
        says nothing else: no credential helper, no transport."""
        if not url.startswith("file://"):
            yield {}
            return
        folder = Path(tempfile.mkdtemp(prefix="gitconfig-"))
        try:
            config = folder / "config"
            config.write_text("[safe]\n\tdirectory = *\n", encoding="utf-8")
            yield {"GIT_CONFIG_GLOBAL": str(config)}
        finally:
            shutil.rmtree(folder, ignore_errors=True)

    @contextmanager
    def _askpass(
        self, credential: Optional[Dict[str, str]]
    ) -> Iterator[Dict[str, str]]:
        """The environment that answers git's credential prompts, if there
        is a credential to answer them with.

        git EXECUTES whatever GIT_ASKPASS names, so it has to be something
        the operating system can run on its own. A .py file is not: on
        Linux the kernel refuses it and /bin/sh then reads Python as shell
        script, and Windows has its own rules. So the helper gets a tiny
        launcher that calls this very interpreter. The launcher holds no
        secret — the username and token travel in the environment, never
        on a command line or on disk — and it is removed when the fetch
        ends.
        """
        token = str((credential or {}).get("token") or "")
        if not token:
            yield {}
            return

        helper = Path(__file__).resolve().parent / "git_askpass.py"
        folder = Path(tempfile.mkdtemp(prefix="askpass-"))
        try:
            if os.name == "nt":
                launcher = folder / "askpass.bat"
                launcher.write_text(
                    f'@echo off\r\n"{sys.executable}" "{helper}" %*\r\n',
                    encoding="utf-8",
                )
            else:
                launcher = folder / "askpass.sh"
                launcher.write_text(
                    f'#!/bin/sh\nexec "{sys.executable}" "{helper}" "$@"\n',
                    encoding="utf-8",
                )
                launcher.chmod(0o700)

            username = str((credential or {}).get("username") or "")
            yield {
                "GIT_ASKPASS": str(launcher),
                # GitHub, GitLab and Bitbucket authenticate on the token
                # and ignore the username, but git insists on asking for
                # one, so answer with the convention for token auth.
                "DECENTAI_GIT_USERNAME": username or "x-access-token",
                "DECENTAI_GIT_TOKEN": token,
            }
        finally:
            shutil.rmtree(folder, ignore_errors=True)

    def _git(self, args, cwd: Path, env: Dict[str, str]) -> str:
        try:
            completed = subprocess.run(
                ["git", *args], cwd=str(cwd), env=env,
                capture_output=True, text=True, timeout=self.TIMEOUT_SECONDS,
            )
        except FileNotFoundError as exc:
            raise RepositoryError(
                "git is not installed on this server, so repositories "
                "cannot be fetched."
            ) from exc
        except subprocess.SubprocessError as exc:
            raise RepositoryError(f"git {args[0]} failed: {exc}") from exc

        if completed.returncode != 0:
            detail = self._scrub(completed.stderr or completed.stdout, env)
            raise RepositoryError(self._explain(detail))
        return completed.stdout

    @staticmethod
    def _explain(detail: str) -> str:
        """git's own words, unless they name something an administrator
        can act on more directly."""
        lowered = detail.lower()
        if "could not read username" in lowered or "authentication failed" in lowered:
            return (
                "The repository refused access. If it is private, choose a "
                "credential with permission to read it."
            )
        if "repository not found" in lowered or "not found" in lowered:
            return (
                "No repository was found at that URL — check the address, "
                "and whether it needs a credential."
            )
        if "couldn't find remote ref" in lowered or "unknown revision" in lowered:
            return "That branch, tag or commit does not exist in the repository."
        return f"git failed: {detail}"

    @staticmethod
    def _scrub(message: str, env: Optional[Dict[str, str]] = None) -> str:
        """Never echo a credential back through an error message.

        Read from the environment handed to GIT, not from the backend's
        own: the token is put there for the askpass helper and is
        deliberately never set on this process, so looking it up in
        os.environ found nothing and redacted nothing."""
        token = (env or {}).get("DECENTAI_GIT_TOKEN") or ""
        text = (message or "").strip()[-400:]
        return text.replace(token, "***") if token else text
