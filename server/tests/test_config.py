import pytest

from framelog_sync.config import SERVER_DIR, ConfigError, load_settings

GOOD = "Xq7v9Lm2Rt-test-only-pw"


@pytest.mark.parametrize("pw,msg", [
    ("", "Set SYNC_PASSWORD"),
    ("short-pw-1", "at least 16"),
    ("aaaaaaaaaaaaaaaaaaaaaaaa", "different characters"),
    ("abababababababababab", "different characters"),
    ("change-me-to-a-long-random-string", "example value"),
    (" " + GOOD, "spaces"),
])
def test_refuses_missing_or_weak_password(pw, msg):
    with pytest.raises(ConfigError, match=msg):
        load_settings({"SYNC_PASSWORD": pw})


def test_defaults():
    s = load_settings({"SYNC_PASSWORD": GOOD})
    assert s.port == 8766 and s.data_dir == SERVER_DIR / "data"
    assert s.forwarded_allow_ips == "127.0.0.1,::1"
    assert s.s3_enabled is False and s.s3_prefix == "framelog/"


def test_s3_settings():
    s = load_settings({"SYNC_PASSWORD": GOOD, "S3_BUCKET": "my-bucket", "AWS_REGION": "eu-west-2",
                       "S3_PREFIX": "/backups/framelog/"})
    assert s.s3_enabled and s.s3_region == "eu-west-2" and s.s3_prefix == "backups/framelog/"
    assert load_settings({"SYNC_PASSWORD": GOOD, "S3_PREFIX": ""}).s3_prefix == ""
    with pytest.raises(ConfigError, match="S3_REGION"):
        load_settings({"SYNC_PASSWORD": GOOD, "S3_BUCKET": "my-bucket"})


def test_bad_numbers():
    with pytest.raises(ConfigError, match="PORT"):
        load_settings({"SYNC_PASSWORD": GOOD, "PORT": "x"})
    with pytest.raises(ConfigError, match="MAX_BODY_BYTES"):
        load_settings({"SYNC_PASSWORD": GOOD, "MAX_BODY_BYTES": "10"})
