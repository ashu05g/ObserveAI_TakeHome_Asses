import json

import httpx
import pytest
import respx

from scripts import vapi_sync


@pytest.fixture(autouse=True)
def _vapi_sync_env(monkeypatch):
    monkeypatch.setenv("VAPI_API_KEY", "vapi_test_key")
    monkeypatch.setenv("SERVER_URL", "https://test.example.com")
    # VAPI_WEBHOOK_SECRET is set by conftest's _vapi_env fixture


@pytest.fixture
def stub_prompt(monkeypatch):
    """Replace assemble_system_prompt with a stub returning a fixed string,
    so sync tests don't depend on the actual prompt text or KB content."""
    monkeypatch.setattr(vapi_sync, "assemble_system_prompt", lambda: "TEST SYSTEM PROMPT")


class TestAssembleSystemPrompt:
    def test_includes_base_prompt_and_all_kb_sections(self):
        prompt = vapi_sync.assemble_system_prompt()

        # Base prompt content
        assert "You are Emma" in prompt
        assert "lookup_caller" in prompt

        # KB delimiter + every section heading we configured
        assert "# REFERENCE INFORMATION" in prompt
        for heading, _filename in vapi_sync.KB_FILES:
            assert f"## {heading}" in prompt

        # A couple of distinctive KB facts that should be present
        assert "1987" in prompt          # from about_company.md
        assert "deductible" in prompt.lower()  # from policies_and_coverage.md
        assert "portal.observeinsurance.com" in prompt  # from customer_service.md


class TestBuildToolConfig:
    def test_includes_server_url_with_lookup_path(self):
        cfg = vapi_sync.build_tool_config("https://api.example.com", "secret123")
        assert cfg["server"]["url"] == "https://api.example.com/lookup"
        assert cfg["server"]["headers"]["X-VAPI-Secret"] == "secret123"

    def test_function_schema_requires_phone(self):
        cfg = vapi_sync.build_tool_config("https://x", "s")
        schema = cfg["function"]
        assert schema["name"] == "lookup_caller"
        assert schema["parameters"]["required"] == ["phone"]
        assert "phone" in schema["parameters"]["properties"]

    def test_tool_is_synchronous(self):
        # async=True (VAPI's dashboard default) causes VAPI to discard the
        # response body and inject "Success." for the LLM. Catastrophic for
        # a lookup tool — without this flag set explicitly, the LLM never
        # sees the caller's data.
        cfg = vapi_sync.build_tool_config("https://x", "s")
        assert cfg["async"] is False


class TestBuildAssistantConfig:
    def test_model_is_gpt_4_1_with_low_temperature(self):
        cfg = vapi_sync.build_assistant_config("https://x", "s", "PROMPT", "tool_id_1")
        assert cfg["model"]["provider"] == "openai"
        assert cfg["model"]["model"] == "gpt-4.1"
        assert cfg["model"]["temperature"] == 0.3

    def test_system_prompt_passed_into_messages(self):
        cfg = vapi_sync.build_assistant_config(
            "https://x", "s", "MY CUSTOM PROMPT", "tool_id_1"
        )
        assert cfg["model"]["messages"] == [
            {"role": "system", "content": "MY CUSTOM PROMPT"}
        ]

    def test_tool_attached_by_id(self):
        cfg = vapi_sync.build_assistant_config(
            "https://x", "s", "p", "tool_abc"
        )
        assert cfg["model"]["toolIds"] == ["tool_abc"]

    def test_server_url_is_webhook_with_auth_header(self):
        cfg = vapi_sync.build_assistant_config(
            "https://api.example.com", "secret123", "p", "t"
        )
        assert cfg["server"]["url"] == "https://api.example.com/webhook"
        assert cfg["server"]["headers"]["X-VAPI-Secret"] == "secret123"

    def test_end_of_call_report_enabled(self):
        cfg = vapi_sync.build_assistant_config("https://x", "s", "p", "t")
        assert "end-of-call-report" in cfg["serverMessages"]

    def test_live_event_subscriptions(self):
        # status-update + final transcripts drive the per-call waterfall.
        # model-output is excluded by design — it fires per streaming token
        # chunk and ballooned trace count to ~970/call. tool-calls is
        # traced at /lookup directly.
        cfg = vapi_sync.build_assistant_config("https://x", "s", "p", "t")
        subs = set(cfg["serverMessages"])
        assert subs >= {"end-of-call-report", "status-update", "transcript"}
        assert "model-output" not in subs

    def test_number_endpointing_is_generous(self):
        # Callers pause between digit groups ("415 ... 555 ... 0001").
        # The on-number wait must exceed those natural breaks or the
        # tool gets called with a partial number.
        cfg = vapi_sync.build_assistant_config("https://x", "s", "p", "t")
        plan = cfg["startSpeakingPlan"]["transcriptionEndpointingPlan"]
        assert plan["onNumberSeconds"] >= 2.5
        assert plan["onNoPunctuationSeconds"] >= 1.5

    def test_transcriber_is_nova_3(self):
        cfg = vapi_sync.build_assistant_config("https://x", "s", "p", "t")
        assert cfg["transcriber"]["model"] == "nova-3"

    def test_voice_uses_flash_v2_5(self):
        cfg = vapi_sync.build_assistant_config("https://x", "s", "p", "t")
        assert cfg["voice"]["model"] == "eleven_flash_v2_5"

    def test_voice_id_is_elevenlabs_hash(self):
        # ElevenLabs voice IDs are 20-char alphanumeric hashes. Using a
        # named voice ("Rachel", "jennifer") fails when the workspace has
        # ElevenLabs credentials linked — the named lookup hits the user's
        # account rather than VAPI's curated list.
        cfg = vapi_sync.build_assistant_config("https://x", "s", "p", "t")
        voice_id = cfg["voice"]["voiceId"]
        assert len(voice_id) == 20
        assert voice_id.isalnum()


class TestUpsertTool:
    @respx.mock
    async def test_creates_when_not_found(self):
        respx.get("https://api.vapi.ai/tool").respond(200, json=[])
        respx.post("https://api.vapi.ai/tool").respond(200, json={"id": "tool_new"})

        async with httpx.AsyncClient(
            base_url=vapi_sync.VAPI_BASE,
            headers={"Authorization": "Bearer test"},
        ) as client:
            tool_id = await vapi_sync.upsert_tool(
                client, vapi_sync.build_tool_config("https://x", "s")
            )
        assert tool_id == "tool_new"

    @respx.mock
    async def test_patches_when_exists(self):
        respx.get("https://api.vapi.ai/tool").respond(
            200,
            json=[{"id": "tool_existing", "function": {"name": "lookup_caller"}}],
        )
        patch_route = respx.patch("https://api.vapi.ai/tool/tool_existing").respond(
            200, json={"id": "tool_existing"}
        )

        async with httpx.AsyncClient(
            base_url=vapi_sync.VAPI_BASE,
            headers={"Authorization": "Bearer test"},
        ) as client:
            tool_id = await vapi_sync.upsert_tool(
                client, vapi_sync.build_tool_config("https://x", "s")
            )

        assert tool_id == "tool_existing"
        assert patch_route.called
        sent = json.loads(patch_route.calls.last.request.read())
        assert sent["function"]["name"] == "lookup_caller"
        # VAPI's UpdateToolDTO doesn't accept `type` — strip it on PATCH.
        assert "type" not in sent

    @respx.mock
    async def test_skips_other_named_tools(self):
        """A workspace may contain unrelated tools — we must not patch them."""
        respx.get("https://api.vapi.ai/tool").respond(
            200,
            json=[
                {"id": "tool_other", "function": {"name": "send_sms"}},
                {"id": "tool_us", "function": {"name": "lookup_caller"}},
            ],
        )
        respx.patch("https://api.vapi.ai/tool/tool_us").respond(200, json={"id": "tool_us"})

        async with httpx.AsyncClient(
            base_url=vapi_sync.VAPI_BASE,
            headers={"Authorization": "Bearer test"},
        ) as client:
            tool_id = await vapi_sync.upsert_tool(
                client, vapi_sync.build_tool_config("https://x", "s")
            )

        assert tool_id == "tool_us"


class TestUpsertAssistant:
    @respx.mock
    async def test_creates_when_not_found(self):
        respx.get("https://api.vapi.ai/assistant").respond(200, json=[])
        respx.post("https://api.vapi.ai/assistant").respond(
            200, json={"id": "asst_new"}
        )

        async with httpx.AsyncClient(
            base_url=vapi_sync.VAPI_BASE,
            headers={"Authorization": "Bearer test"},
        ) as client:
            assistant_id = await vapi_sync.upsert_assistant(
                client,
                vapi_sync.build_assistant_config("https://x", "s", "p", "t"),
            )
        assert assistant_id == "asst_new"

    @respx.mock
    async def test_patches_when_exists(self):
        respx.get("https://api.vapi.ai/assistant").respond(
            200, json=[{"id": "asst_existing", "name": "Emma"}]
        )
        patch_route = respx.patch(
            "https://api.vapi.ai/assistant/asst_existing"
        ).respond(200, json={"id": "asst_existing"})

        async with httpx.AsyncClient(
            base_url=vapi_sync.VAPI_BASE,
            headers={"Authorization": "Bearer test"},
        ) as client:
            assistant_id = await vapi_sync.upsert_assistant(
                client,
                vapi_sync.build_assistant_config("https://x", "s", "PROMPT", "t1"),
            )

        assert assistant_id == "asst_existing"
        sent = json.loads(patch_route.calls.last.request.read())
        assert sent["model"]["temperature"] == 0.3
        assert sent["model"]["toolIds"] == ["t1"]


class TestEndToEndSync:
    @respx.mock
    async def test_sync_orchestrates_tool_then_assistant(self, stub_prompt):
        respx.get("https://api.vapi.ai/tool").respond(200, json=[])
        respx.post("https://api.vapi.ai/tool").respond(200, json={"id": "tool_x"})
        respx.get("https://api.vapi.ai/assistant").respond(200, json=[])
        post_assistant = respx.post("https://api.vapi.ai/assistant").respond(
            200, json={"id": "asst_x"}
        )

        code = await vapi_sync.sync()
        assert code == 0

        # Assistant POST must reference the tool ID returned by the tool POST.
        sent = json.loads(post_assistant.calls.last.request.read())
        assert sent["model"]["toolIds"] == ["tool_x"]

    @respx.mock
    async def test_4xx_propagates_as_failure(self, stub_prompt):
        respx.get("https://api.vapi.ai/tool").respond(
            403, json={"error": "forbidden"}
        )
        with pytest.raises(httpx.HTTPStatusError):
            await vapi_sync.sync()
