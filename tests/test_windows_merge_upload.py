import importlib.util
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


def test_remote_upload_commands_quote_url_derived_paths(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    module = load_merge_upload_module(monkeypatch, tmp_path)
    calls: list[tuple[tuple[object, ...], bool]] = []

    def fake_run(*args: object, single_cmd: bool = False) -> str:
        calls.append((args, single_cmd))
        return ""

    module._run = fake_run
    module.PLINK = "plink.exe"
    module.PSCP = "pscp.exe"
    module.UPLOAD_HOST = "downloads.example.org"
    module.UPLOAD_USER = "release-manager"

    dest = module.url2path(
        "https://www.python.org/ftp/python/3.14.0;touch PTP/python-3.14.0-amd64.exe"
    )

    module.prepare_upload_dir(dest)
    module.upload_ssh("python-3.14.0-amd64.exe", dest)

    assert calls == [
        (
            (
                "plink.exe",
                "-batch",
                "release-manager@downloads.example.org",
                "mkdir '/srv/www.python.org/ftp/python/3.14.0;touch PTP' && "
                "chgrp downloads '/srv/www.python.org/ftp/python/3.14.0;touch PTP' && "
                "chmod a+rx '/srv/www.python.org/ftp/python/3.14.0;touch PTP'",
            ),
            False,
        ),
        (
            (
                "pscp.exe",
                "-batch",
                "python-3.14.0-amd64.exe",
                "release-manager@downloads.example.org:"
                "'/srv/www.python.org/ftp/python/3.14.0;touch PTP/"
                "python-3.14.0-amd64.exe'",
            ),
            False,
        ),
        (
            (
                "plink.exe",
                "-batch",
                "release-manager@downloads.example.org",
                "chgrp downloads "
                "'/srv/www.python.org/ftp/python/3.14.0;touch PTP/"
                "python-3.14.0-amd64.exe' && chmod g-x,o+r "
                "'/srv/www.python.org/ftp/python/3.14.0;touch PTP/"
                "python-3.14.0-amd64.exe'",
            ),
            False,
        ),
    ]
