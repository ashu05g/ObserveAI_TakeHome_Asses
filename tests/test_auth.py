from api.utils.auth import verify_vapi_secret


class TestVerifyVapiSecret:
    def test_accepts_matching_secret(self):
        assert verify_vapi_secret("test-secret-do-not-use-in-prod") is True

    def test_rejects_mismatched_secret(self):
        assert verify_vapi_secret("wrong-secret") is False

    def test_rejects_none(self):
        assert verify_vapi_secret(None) is False

    def test_rejects_empty_string(self):
        assert verify_vapi_secret("") is False

    def test_rejects_when_env_unset(self, monkeypatch):
        monkeypatch.delenv("VAPI_WEBHOOK_SECRET", raising=False)
        assert verify_vapi_secret("anything") is False

    def test_rejects_when_env_empty(self, monkeypatch):
        monkeypatch.setenv("VAPI_WEBHOOK_SECRET", "")
        assert verify_vapi_secret("anything") is False

    def test_close_but_not_equal_string_is_rejected(self):
        assert verify_vapi_secret("test-secret-do-not-use-in-pro") is False
        assert verify_vapi_secret("test-secret-do-not-use-in-prodd") is False
