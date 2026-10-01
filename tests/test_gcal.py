"""Google Calendar tokens are stored protected (DPAPI, like the API key), and an unreadable token file is a clear error."""
import json

import pytest

import gcal
import secret


@pytest.fixture
def appdata(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setattr(secret, "_backend", (lambda b: b[::-1], lambda b: b[::-1]))
    return tmp_path / "Vox"


TOK = {"refresh_token": "R123", "access_token": "A456", "expires": 0, "email": "e@x.com"}


def test_tokens_are_not_plain_on_disk_and_round_trip(appdata):
    gcal._save_token(dict(TOK))
    raw = open(gcal.token_path(), encoding="utf-8").read()
    assert "R123" not in raw and "A456" not in raw
    assert gcal._load_token()["refresh_token"] == "R123"
    assert gcal._load_token()["access_token"] == "A456"
    assert gcal.account() == "e@x.com"


def test_a_legacy_plain_file_is_read_and_protected_at_once(appdata):
    (appdata).mkdir(parents=True, exist_ok=True)
    with open(gcal.token_path(), "w", encoding="utf-8") as f:
        json.dump(TOK, f)
    tok = gcal._load_token()
    assert tok["refresh_token"] == "R123"
    raw = open(gcal.token_path(), encoding="utf-8").read()
    assert "R123" not in raw and "A456" not in raw


def test_an_unreadable_token_file_asks_to_connect_again(appdata):
    appdata.mkdir(parents=True, exist_ok=True)
    with open(gcal.token_path(), "w", encoding="utf-8") as f:
        f.write("{}garbage")
    with pytest.raises(RuntimeError, match="Connect again"):
        gcal._access_token()


def test_a_token_that_another_windows_user_protected_asks_to_connect_again(appdata, monkeypatch):
    gcal._save_token(dict(TOK))
    monkeypatch.setattr(secret, "_backend", (lambda b: b, lambda b: (_ for _ in ()).throw(OSError("other user"))))
    with pytest.raises(RuntimeError, match="Connect again"):
        gcal._access_token()
