#!/usr/bin/env python3
"""Build reproducible installable source ZIPs without private/research artifacts."""

import hashlib
import json
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DOMAIN = "xgimi_elfin_flip"


def main():
    component = ROOT / "custom_components" / DOMAIN
    manifest = json.loads((component / "manifest.json").read_text())
    for path in component.rglob("*.json"):
        json.loads(path.read_text())
    files = [
        p
        for p in component.rglob("*")
        if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc"
    ]
    files += [ROOT / name for name in ("README.md", "LICENSE", "hacs.json")]
    output = ROOT / "dist" / f"xgimi-elfin-flip-{manifest['version']}.zip"
    output.parent.mkdir(exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(files):
            entry = zipfile.ZipInfo(path.relative_to(ROOT).as_posix(), (2026, 11, 10, 0, 0, 0))
            entry.compress_type = zipfile.ZIP_DEFLATED
            entry.external_attr = 0o100644 << 16
            archive.writestr(entry, path.read_bytes())
    with zipfile.ZipFile(output) as archive:
        if archive.testzip() is not None:
            raise RuntimeError("ZIP verification failed")
        names = archive.namelist()
    result = {
        "file": str(output),
        "sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
        "version": manifest["version"],
        "files": len(names),
        "archive_verified": True,
    }
    output.with_suffix(".json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
