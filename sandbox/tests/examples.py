"""The example agents of documentation/examples, as the sandbox's tests run them: each folder zipped the same way
every time, and the catalog made from them. To write the catalog again after changing an example:

    python sandbox/tests/examples.py
"""

from __future__ import annotations

import hashlib
import io
import json
import sys
import zipfile
from pathlib import Path

import yaml


class Example:
    FOLDER = Path(__file__).resolve().parents[2] / "documentation" / "examples"
    CATALOG = FOLDER / "catalog.json"
    #: What a folder holds beside its program.
    MANIFEST = "manifest.yaml"
    FIXED_TIME = (1980, 1, 1, 0, 0, 0)

    def __init__(self, name: str):
        self.folder = self.FOLDER / name

    @classmethod
    def all(cls) -> list["Example"]:
        return [cls(path.name) for path in sorted(cls.FOLDER.iterdir()) if (path / cls.MANIFEST).is_file()]

    def manifest(self) -> dict:
        return yaml.safe_load((self.folder / self.MANIFEST).read_text(encoding="utf-8"))

    def program(self) -> bytes:
        """Every file of the folder but its manifest, in one zip: the same bytes from the same files, wherever."""
        written = io.BytesIO()
        with zipfile.ZipFile(written, "w", zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(self.folder.rglob("*")):
                if path.is_file() and path.name != self.MANIFEST and "__pycache__" not in path.parts:
                    name = path.relative_to(self.folder).as_posix()
                    info = zipfile.ZipInfo(name, self.FIXED_TIME)
                    info.create_system = 3
                    info.external_attr = (0o100755 if name == "start" else 0o100644) << 16
                    info.compress_type = zipfile.ZIP_DEFLATED
                    archive.writestr(info, path.read_bytes().replace(b"\r\n", b"\n"))
        return written.getvalue()

    def digest(self) -> str:
        return "sha256:" + hashlib.sha256(self.program()).hexdigest()

    def entry(self) -> dict:
        """Its entry in the catalog: one program for both machines, as it is Python on the base."""
        manifest, body = self.manifest(), self.program()
        url = f"{manifest['agent']['id']}/{manifest['agent']['version']}/program.zip"
        file = {"url": url, "digest": "sha256:" + hashlib.sha256(body).hexdigest(), "size": len(body)}
        return {"manifest": manifest, "programs": {"x86_64": file, "aarch64": file}, "base": "python-3.12",
                "readme": manifest["agent"]["description"].strip()}

    @classmethod
    def catalog(cls) -> dict:
        return {"schema": 3, "catalog": {"id": "decentai_examples", "name": "DecentAI examples"},
                "agents": [example.entry() for example in cls.all()]}


if __name__ == "__main__":
    Example.CATALOG.write_text(json.dumps(Example.catalog(), indent=2) + "\n", encoding="utf-8")
    sys.stdout.write(f"{Example.CATALOG}\n")
