import sys
import types
from pathlib import Path

from scripts import deploy_huggingface as d


def test_bundle_contains_app_and_space_header(tmp_path):
    d.build_bundle(tmp_path)
    readme = (tmp_path / "README.md").read_text()
    assert readme.startswith("---\n") and "sdk: docker" in readme and "app_port: 7860" in readme
    for f in ("Dockerfile", "start.sh", "requirements.txt", "app/main.py", "static/index.html"):
        assert (tmp_path / f).exists(), f
    assert not (tmp_path / "tests").exists() and not list(tmp_path.rglob("__pycache__"))


def test_space_url():
    assert d.space_url("Rahul_B/insta.save") == "https://rahul-b-insta-save.hf.space"


def test_main_creates_space_sets_secrets_and_uploads(monkeypatch):
    calls = {}

    class FakeApi:
        def __init__(self, token):
            calls["token"] = token

        def whoami(self):
            return {"name": "someone"}

        def create_repo(self, repo_id, **kw):
            calls["create"] = (repo_id, kw)

        def add_space_secret(self, repo_id, key, value):
            calls.setdefault("secrets", {})[key] = value

        def upload_folder(self, **kw):
            calls["files"] = sorted(p.relative_to(kw["folder_path"]).as_posix()
                                    for p in Path(kw["folder_path"]).rglob("*") if p.is_file())
            calls["upload"] = kw

    monkeypatch.setitem(sys.modules, "huggingface_hub", types.SimpleNamespace(HfApi=FakeApi))
    monkeypatch.setenv("HF_TOKEN", "hf_test")
    monkeypatch.delenv("HF_SPACE", raising=False)
    monkeypatch.setenv("COOKIES_TXT", "cookie-data")
    monkeypatch.delenv("YT_PROXY", raising=False)
    assert d.main() == 0
    assert calls["create"] == ("someone/instasave", {"repo_type": "space", "space_sdk": "docker",
                                                     "exist_ok": True, "private": False})
    assert calls["secrets"] == {"COOKIES_TXT": "cookie-data"}
    assert calls["upload"]["repo_type"] == "space" and "Dockerfile" in calls["files"]


def test_main_requires_token(monkeypatch):
    monkeypatch.setitem(sys.modules, "huggingface_hub", types.SimpleNamespace(HfApi=None))
    monkeypatch.setenv("HF_TOKEN", "")
    assert d.main() == 1


def test_main_explains_pro_requirement(monkeypatch, capsys):
    class PaymentRequired(Exception):
        response = types.SimpleNamespace(status_code=402)

    class FakeApi:
        def __init__(self, token):
            pass

        def whoami(self):
            return {"name": "someone"}

        def create_repo(self, repo_id, **kw):
            raise PaymentRequired("402 Payment Required")

    monkeypatch.setitem(sys.modules, "huggingface_hub", types.SimpleNamespace(HfApi=FakeApi))
    monkeypatch.setenv("HF_TOKEN", "hf_test")
    assert d.main() == 2
    assert "PRO subscription" in capsys.readouterr().err
