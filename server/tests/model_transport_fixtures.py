"""Response-only fixtures. These do not establish outbound network safety."""
from app.services import model_outbound


def install_model_transport(monkeypatch, transport):
    # Keep URL/literal policy active. H2 tests use the actual transport/backend.
    monkeypatch.setattr(model_outbound, "PinnedModelTransport", lambda endpoint, policy: transport)
