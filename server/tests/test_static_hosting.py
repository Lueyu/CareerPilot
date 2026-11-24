"""Exercise static hosting with a synthetic build and no application lifespan."""

import runpy
from pathlib import Path

from fastapi.testclient import TestClient

from app import config


def test_configured_build_serves_assets_and_spa_alongside_api(tmp_path, monkeypatch):
    build = tmp_path / "dist"
    (build / "assets").mkdir(parents=True)
    (build / "index.html").write_text("<main>CareerPilot fixture</main>", encoding="utf-8")
    (build / "assets" / "bundle.js").write_text("console.log('fixture')", encoding="utf-8")
    (build / "robots.txt").write_text("User-agent: *", encoding="utf-8")
    dotenv = tmp_path / ".env"
    dotenv.write_text("STATIC_DIR=dist\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("STATIC_DIR", raising=False)
    settings = config.Settings(_env_file=dotenv)
    monkeypatch.setattr(config, "get_settings", lambda: settings)

    entrypoint = Path(config.__file__).with_name("main.py")
    app = runpy.run_path(str(entrypoint))["app"]
    client = TestClient(app)

    assert client.get("/healthz").json()["ok"] is True
    assert client.get("/api/v1/settings/workspaces").status_code == 401
    for path in ("/api", "/api/v1/missing"):
        response = client.get(path)
        assert response.status_code == 404
        assert response.json() == {"detail": "Not Found"}

    for path in ("/", "/study/session"):
        response = client.get(path)
        assert response.status_code == 200
        assert response.text == "<main>CareerPilot fixture</main>"

    assert client.get("/robots.txt").text == "User-agent: *"
    asset = client.get("/assets/bundle.js")
    assert asset.status_code == 200
    assert asset.text == "console.log('fixture')"
    assert client.head("/assets/bundle.js").status_code == 200
    assert client.get("/assets/missing.js").status_code == 404
