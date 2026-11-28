"""Static routing with synthetic assets and a provisioned isolated recovery database."""

import runpy
from pathlib import Path

import httpx
import pytest

from app import config


@pytest.mark.asyncio
async def test_configured_build_serves_assets_and_spa_alongside_api(tmp_path, monkeypatch, db_session):
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
    # AsyncClient uses the same loop/NullPool test sessions as recovery admission.
    # db_session provisions the fresh test installation; production remains fail-closed.
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://fixture") as client:

        assert (await client.get("/healthz")).json()["ok"] is True
        assert (await client.get("/api/v1/settings/workspaces")).status_code == 401
        for path in ("/api", "/api/v1/missing"):
            response = await client.get(path)
            assert response.status_code == 404
            assert response.json()["detail"] == "Not Found"
            assert response.json()["code"] == "NOT_FOUND"
            assert response.json()["request_id"] == response.headers["X-Request-ID"]

        for path in ("/", "/study/session"):
            response = await client.get(path)
            assert response.status_code == 200
            assert response.text == "<main>CareerPilot fixture</main>"

        assert (await client.get("/robots.txt")).text == "User-agent: *"
        asset = await client.get("/assets/bundle.js")
        assert asset.status_code == 200
        assert asset.text == "console.log('fixture')"
        assert (await client.head("/assets/bundle.js")).status_code == 200
        assert (await client.get("/assets/missing.js")).status_code == 404

        # Prove the fixture did not bypass or weaken the production recovery guard.
        from sqlalchemy import text

        from app.services.recovery_control import provision_known_installation
        try:
            await db_session.execute(text("UPDATE recovery_control SET access_state='QUARANTINED'"))
            await db_session.commit()
            blocked = await client.get("/api/v1/settings/workspaces")
            assert blocked.status_code == 503
            assert blocked.json()["detail"]["code"] == "RECOVERY_QUARANTINED"
            assert (await client.get("/assets/bundle.js")).status_code == 200
        finally:
            await db_session.execute(text("DELETE FROM recovery_control"))
            await provision_known_installation(db_session)
            await db_session.commit()
