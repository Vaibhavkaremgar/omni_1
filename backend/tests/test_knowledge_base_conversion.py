import base64
import json

from app.services.knowledge_base_files import to_pdf
from app.integrations.omnidimension.agents import OmniDimensionAgentProvider


def test_txt_is_converted_to_pdf():
    content, filename = to_pdf(b"Project Aurora\nPrice: 5000000", "facts.txt")
    assert filename == "facts.pdf"
    assert content.startswith(b"%PDF")
    assert b"Project Aurora" in content


def test_provider_uses_confirmed_knowledge_contract():
    class Client:
        def __init__(self): self.calls = []
        def post(self, path, *, json): self.calls.append((path, json)); return {"file": {"id": 41}}
        def delete(self, path): self.calls.append((path, None))
    client = Client(); provider = OmniDimensionAgentProvider(client)
    provider.upload_knowledge_file(base64.b64encode(b"%PDF-1.4").decode(), "facts.pdf")
    provider.attach_knowledge_file("41", "99", "Use this document for policy questions.")
    assert client.calls[0] == ("/knowledge_base/create", {"file": base64.b64encode(b"%PDF-1.4").decode(), "filename": "facts.pdf"})
    assert client.calls[1] == ("/knowledge_base/attach", {"file_ids": [41], "agent_id": [99][0], "when_to_use": "Use this document for policy questions."})
