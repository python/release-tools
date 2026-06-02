import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest


def load_merge_upload_module(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Any:
    for name in (
        "INDEX_FILE",
        "LOCAL_INDEX",
        "MAKECAT",
        "MANIFEST_FILE",
        "NO_UPLOAD",
        "PLINK",
        "PSCP",
        "SIGN_COMMAND",
        "UPLOAD_HOST",
        "UPLOAD_HOST_KEY",
        "UPLOAD_KEYFILE",
        "UPLOAD_PATH_PREFIX",
        "UPLOAD_URL_PREFIX",
        "UPLOAD_USER",
    ):
        monkeypatch.delenv(name, raising=False)

    monkeypatch.chdir(tmp_path)
    script = Path(__file__).parents[1] / "windows-release" / "merge-and-upload.py"
    spec = importlib.util.spec_from_file_location("merge_and_upload_for_test", script)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)

    with pytest.raises(SystemExit) as exc_info:
        spec.loader.exec_module(module)

    assert exc_info.value.code == 1
    return module


def test_calculate_uploads_uses_full_artifact_sbom_name(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    module = load_merge_upload_module(monkeypatch, tmp_path)
    artifact = tmp_path / "python-3.14.0-amd64.exe"
    sbom = tmp_path / "python-3.14.0-amd64.exe.spdx.json"
    artifact.write_bytes(b"installer")
    sbom.write_text("{}", encoding="utf-8")
    (tmp_path / "__install__.amd64.json").write_text(
        json.dumps(
            {
                "url": (
                    "https://www.python.org/ftp/python/3.14.0/python-3.14.0-amd64.exe"
                )
            }
        ),
        encoding="utf-8",
    )

    uploads = list(module.calculate_uploads())

    assert len(uploads) == 1
    _, _, _, upload_sbom, sbom_dest = uploads[0]
    assert upload_sbom == sbom
    assert (
        sbom_dest
        == "/srv/www.python.org/ftp/python/3.14.0/python-3.14.0-amd64.exe.spdx.json"
    )
