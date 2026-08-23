"""Smoke tests for tgkit Phase 0.

Run with: pytest tests/test_smoke.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure src is on path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


# ============================================================================
# Import tests
# ============================================================================

def test_import_tgkit():
    """Top-level package imports."""
    import tgkit
    assert tgkit.__version__ == "0.1.0"
    assert tgkit.__author__ == "Ali-F-Harandi"


def test_import_config():
    """Config subsystem imports."""
    from tgkit.config import Config, ApiConfig, BotEntry, ChannelsConfig
    from tgkit.config import VaultConfig, ThrottleConfig, ScanConfig, DbConfig
    from tgkit.config import load_config, save_config, find_config_path
    assert Config is not None
    assert ApiConfig is not None


def test_import_transport():
    """Transport subsystem imports."""
    from tgkit.transport import TgClient, Bot, AsyncBotPool, ThrottlePolicy
    from tgkit.transport import ChannelCooldown, PeerResolver
    from tgkit.transport import FloodWaitError, is_flood_wait, classify_error
    assert TgClient is not None
    assert AsyncBotPool is not None


def test_import_models():
    """Models subsystem imports."""
    from tgkit.models import Channel, MessageRef, MessageInfo, MediaInfo
    from tgkit.models import parse_link, build_link, parse_channel_ref
    assert Channel is not None
    assert MessageRef is not None


def test_import_db():
    """DB subsystem imports."""
    from tgkit.db import Database, get_db, init_db, SCHEMA_SQL
    assert Database is not None
    assert "CREATE TABLE" in SCHEMA_SQL


def test_import_capabilities():
    """Capabilities module imports."""
    from tgkit.capabilities import detect_capabilities, Capability, CapabilityReport
    assert Capability is not None
    assert detect_capabilities is not None


def test_import_bot_api():
    """Bot API transport imports."""
    from tgkit.transport.bot_api import BotAPIClient, BotAPIError
    assert BotAPIClient is not None
    assert BotAPIError is not None


def test_import_db_store():
    """DB store imports."""
    from tgkit.db.store import Store, ChannelStore, MessageStore, OperationsLogStore
    assert Store is not None
    assert ChannelStore is not None


def test_import_db_sync():
    """DB sync imports."""
    from tgkit.db.sync import DBSync, DB_CAPTION_PREFIX
    assert DBSync is not None
    assert DB_CAPTION_PREFIX == "TGKIT_DB_BACKUP"


# ============================================================================
# Config tests
# ============================================================================

def test_config_defaults():
    """Config has sensible defaults."""
    from tgkit.config import Config
    c = Config()
    assert c.version == 1
    assert c.api.api_id == 0  # empty by default
    assert c.api.session_dir == "~/.tgkit/sessions"
    assert len(c.bots) == 0
    assert c.throttle.min_interval == 0.3
    assert c.scan.batch_size == 50
    assert c.scan.concurrency == 5
    assert c.vault.chunk_size_mb == 19
    assert c.db.enabled is True


def test_config_roundtrip(tmp_path):
    """Config can be saved and loaded."""
    from tgkit.config import Config, save_config, load_config
    c = Config()
    c.api.api_id = 12345
    c.api.api_hash = "abcdef"
    c.add_bot("8866132706:AAH_test", username="test_bot")

    path = tmp_path / "config.json"
    save_config(c, path)

    loaded = load_config(path)
    assert loaded.api.api_id == 12345
    assert loaded.api.api_hash == "abcdef"
    assert len(loaded.bots) == 1
    assert loaded.bots[0].token == "8866132706:AAH_test"
    assert loaded.bots[0].bot_id == 8866132706
    assert loaded.bots[0].username == "test_bot"


def test_bot_id_extraction():
    """BotEntry extracts bot_id from token."""
    from tgkit.config import BotEntry
    b = BotEntry(token="8866132706:AAH_test")
    assert b.bot_id == 8866132706


def test_expected_throughput():
    """Expected throughput calculation."""
    from tgkit.config import Config
    c = Config()
    c.throttle.min_interval = 0.5
    c.add_bot("1:abc")
    c.add_bot("2:def")
    c.add_bot("3:ghi")
    # 3 bots / 0.5s = 6 req/s
    assert abs(c.expected_throughput() - 6.0) < 0.01


# ============================================================================
# Model tests
# ============================================================================

def test_message_ref_frozen():
    """MessageRef is frozen (hashable)."""
    from tgkit.models import MessageRef
    r1 = MessageRef(channel_id=-1003873843444, msg_id=123)
    r2 = MessageRef(channel_id=-1003873843444, msg_id=123)
    assert r1 == r2
    assert hash(r1) == hash(r2)
    # Can't modify frozen dataclass
    import dataclasses
    try:
        r1.msg_id = 456
        assert False, "Should have raised"
    except dataclasses.FrozenInstanceError:
        pass


def test_channel_internal_id():
    """Channel.internal_id strips -100 prefix."""
    from tgkit.models import Channel
    c = Channel(id=-1003873843444)
    assert c.internal_id == 3873843444
    assert c.is_numeric_id is True

    c2 = Channel(id="@yxafile")
    assert c2.internal_id is None
    assert c2.is_numeric_id is False


def test_parse_link_public():
    """parse_link handles public channel links."""
    from tgkit.models import parse_link
    channel, msg_id = parse_link("https://t.me/yxafile/123")
    assert channel == "@yxafile"
    assert msg_id == 123


def test_parse_link_private():
    """parse_link handles private channel links."""
    from tgkit.models import parse_link
    channel, msg_id = parse_link("https://t.me/c/3873843444/456")
    assert channel == -1003873843444
    assert msg_id == 456


def test_parse_link_at_format():
    """parse_link handles @username/123 format."""
    from tgkit.models import parse_link
    channel, msg_id = parse_link("@yxafile/789")
    assert channel == "@yxafile"
    assert msg_id == 789


def test_build_link_public():
    """build_link for public channels."""
    from tgkit.models import build_link
    assert build_link("@yxafile", 123) == "https://t.me/yxafile/123"


def test_build_link_private():
    """build_link for private channels."""
    from tgkit.models import build_link
    assert build_link(-1003873843444, 456) == "https://t.me/c/3873843444/456"


def test_parse_channel_ref():
    """parse_channel_ref normalizes various forms."""
    from tgkit.models import parse_channel_ref
    assert parse_channel_ref("@yxafile") == "@yxafile"
    assert parse_channel_ref("yxafile") == "@yxafile"
    assert parse_channel_ref("-1003873843444") == -1003873843444
    assert parse_channel_ref("3873843444") == -1003873843444


def test_media_info_size_mb():
    """MediaInfo.file_size_mb converts bytes to MB."""
    from tgkit.models import MediaInfo
    m = MediaInfo(file_size=1048576)  # 1 MB
    assert m.file_size_mb == 1.0
    m2 = MediaInfo(file_size=0)
    assert m2.file_size_mb == 0.0


def test_message_info_to_dict():
    """MessageInfo flattens to 18-column dict."""
    from tgkit.models import MessageRef, MessageInfo, MediaInfo
    mi = MessageInfo(
        ref=MessageRef(channel_id=-1003873843444, msg_id=42),
        date="2026-07-14T12:00:00",
        caption="test caption",
        media=MediaInfo(media_type="document", file_name="test.zip", file_size=1024),
        message_link="https://t.me/c/3873843444/42",
    )
    d = mi.to_dict()
    assert d["msg_id"] == 42
    assert d["media_type"] == "document"
    assert d["file_name"] == "test.zip"
    assert d["caption"] == "test caption"
    assert len(d) == 18  # 18 columns


# ============================================================================
# Throttle tests
# ============================================================================

def test_throttle_defaults():
    """ThrottlePolicy has correct defaults."""
    from tgkit.transport import ThrottlePolicy
    t = ThrottlePolicy()
    assert t.min_interval == 0.3
    assert t.max_interval == 30.0
    assert t.current_delay == 0.5
    assert t.backoff_factor == 1.5


def test_throttle_on_floodwait():
    """ThrottlePolicy.on_floodwait increases delay."""
    from tgkit.transport import ThrottlePolicy
    t = ThrottlePolicy()
    initial = t.current_delay
    t.on_floodwait(10)  # 10 second FloodWait
    assert t.current_delay > initial
    # (10 + 1) * 1.5 = 16.5
    assert abs(t.current_delay - 16.5) < 0.1


def test_throttle_on_success_decay():
    """ThrottlePolicy.on_success decays toward min_interval."""
    from tgkit.transport import ThrottlePolicy
    t = ThrottlePolicy()
    t.current_delay = 5.0
    # Trigger decay_every (10) successes
    for _ in range(10):
        t.on_success()
    # 5.0 * 0.9 = 4.5
    assert abs(t.current_delay - 4.5) < 0.1
    assert t.current_delay >= t.min_interval


def test_throttle_max_interval_ceiling():
    """ThrottlePolicy never exceeds max_interval."""
    from tgkit.transport import ThrottlePolicy
    t = ThrottlePolicy(max_interval=10.0)
    t.on_floodwait(1000)  # huge FloodWait
    assert t.current_delay == 10.0  # capped


def test_channel_cooldown():
    """ChannelCooldown tracks per-channel cooldowns."""
    from tgkit.transport import ChannelCooldown
    cd = ChannelCooldown(extra_delay=0.5)
    ch = -1003873843444

    # Initially cooled down (no cooldown set)
    assert cd.is_cooled_down(ch)

    # Trigger cooldown
    cd.trigger(ch, 5.0)
    assert not cd.is_cooled_down(ch)

    # Clear it
    cd.clear(ch)
    assert cd.is_cooled_down(ch)


# ============================================================================
# DB tests
# ============================================================================

def test_db_init(tmp_path):
    """Database initializes and creates tables."""
    from tgkit.db import Database
    db = Database(tmp_path / "test.db")
    db.init()

    # Verify tables exist
    with db.connect() as conn:
        cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {row[0] for row in cur.fetchall()}

    assert "channels" in tables
    assert "bots" in tables
    assert "messages" in tables
    assert "library" in tables
    assert "operations_log" in tables


def test_db_channel_roundtrip(tmp_path):
    """Channel can be inserted and queried."""
    from tgkit.db import Database
    import time
    db = Database(tmp_path / "test.db")
    db.init()

    with db.connect() as conn:
        conn.execute("""
            INSERT INTO channels (id, username, title, type, role, added_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (-1003873843444, None, "Test Channel", "private", "destination", int(time.time())))

    rows = db.query_all("SELECT * FROM channels WHERE id = ?", (-1003873843444,))
    assert len(rows) == 1
    assert rows[0]["title"] == "Test Channel"
    assert rows[0]["role"] == "destination"


# ============================================================================
# Error classification tests
# ============================================================================

def test_is_flood_wait():
    """is_flood_wait detects FloodWait exceptions."""
    from tgkit.transport import is_flood_wait

    # Simulate pyrofork's FloodWait
    class FakeFloodWait(Exception):
        value = 30

    assert is_flood_wait(FakeFloodWait()) is True

    class OtherError(Exception):
        pass

    assert is_flood_wait(OtherError()) is False


def test_classify_error_permanent():
    """classify_error identifies permanent errors."""
    from tgkit.transport import classify_error

    err = Exception("Peer id invalid: -1003873843444")
    info = classify_error(err)
    assert info["kind"] == "permanent"
    assert info["should_retry"] is False


def test_classify_error_transient():
    """classify_error identifies transient errors."""
    from tgkit.transport import classify_error

    err = Exception("Connection timeout")
    info = classify_error(err)
    assert info["kind"] == "transient"
    assert info["should_retry"] is True


# ============================================================================
# Capability tests
# ============================================================================

def test_capabilities_tier_none():
    """No bots → no capabilities."""
    from tgkit.capabilities import detect_capabilities, Capability
    from tgkit.config.schema import Config

    config = Config()  # empty
    report = detect_capabilities(config)

    assert report.tier == "none"
    assert report.bot_count == 0
    assert not report.can(Capability.SEND_MESSAGE)


def test_capabilities_tier1():
    """Bots but no api_id/hash → Tier 1."""
    from tgkit.capabilities import detect_capabilities, Capability
    from tgkit.config.schema import Config

    config = Config()
    config.add_bot("123:abc")
    report = detect_capabilities(config)

    assert report.tier == "tier1_basic"
    assert report.can(Capability.SEND_MESSAGE)
    assert report.can(Capability.FORWARD)
    assert report.can(Capability.DB_SYNC)
    assert not report.can(Capability.SCAN_CHANNEL)
    assert not report.can(Capability.COPY_FILE_ID)


def test_capabilities_tier2():
    """Bots + api_id/hash → Tier 2."""
    from tgkit.capabilities import detect_capabilities, Capability
    from tgkit.config.schema import Config

    config = Config()
    config.api.api_id = 21724
    config.api.api_hash = "abc"
    config.add_bot("123:abc")
    report = detect_capabilities(config)

    assert report.tier == "tier2_extended"
    assert report.can(Capability.SCAN_CHANNEL)
    assert report.can(Capability.COPY_FILE_ID)
    assert report.can(Capability.READ_HISTORY)


# ============================================================================
# DB Store tests
# ============================================================================

def test_db_store_channels(tmp_path):
    """ChannelStore CRUD works."""
    from tgkit.db import Database
    from tgkit.db.store import ChannelStore

    db = Database(tmp_path / "test.db")
    db.init()
    store = ChannelStore(db)

    # Insert
    store.upsert(-1001234567890, username="test", title="Test Channel",
                 ch_type="public", role="source")

    # Get
    ch = store.get(-1001234567890)
    assert ch is not None
    assert ch["title"] == "Test Channel"
    assert ch["role"] == "source"

    # List
    all_channels = store.list_all()
    assert len(all_channels) == 1

    # Update role
    store.upsert(-1001234567890, role="destination")
    ch = store.get(-1001234567890)
    assert ch["role"] == "destination"

    # Remove
    assert store.remove(-1001234567890) is True
    assert store.get(-1001234567890) is None


def test_db_store_operations_log(tmp_path):
    """OperationsLogStore logs and queries."""
    from tgkit.db import Database
    from tgkit.db.store import OperationsLogStore, ChannelStore

    db = Database(tmp_path / "test.db")
    db.init()

    # Insert channels first (operations_log FKs to channels)
    ch_store = ChannelStore(db)
    ch_store.upsert(-1001, title="Source")
    ch_store.upsert(-1002, title="Dest")

    store = OperationsLogStore(db)

    # Log some operations
    store.log("copy", "ok", source_channel=-1001, source_msg_id=42,
              dest_channel=-1002, dest_msg_id=100, bot_id=123)
    store.log("forward", "failed", source_channel=-1001, source_msg_id=43,
              error="FloodWait")

    # List recent
    recent = store.list_recent(limit=10)
    assert len(recent) == 2

    # Filter by type
    copies = store.list_recent(op_type="copy")
    assert len(copies) == 1
    assert copies[0]["op_type"] == "copy"


def test_db_store_messages(tmp_path):
    """MessageStore insert + query."""
    from tgkit.db import Database
    from tgkit.db.store import MessageStore, ScanStore, ChannelStore

    db = Database(tmp_path / "test.db")
    db.init()

    # Insert channel first (scans FK to channels)
    ch_store = ChannelStore(db)
    ch_store.upsert(-1001234567890, title="Test Channel")

    # Create a scan
    scan_store = ScanStore(db)
    scan_id = scan_store.create(-1001234567890, 1, 100)

    # Insert messages
    msg_store = MessageStore(db)
    messages = [
        {"msg_id": 1, "media_type": "document", "file_name": "test1.zip",
         "file_size": 1024, "file_extension": ".zip"},
        {"msg_id": 2, "media_type": "document", "file_name": "test2.pdf",
         "file_size": 2048, "file_extension": ".pdf"},
    ]
    count = msg_store.insert_batch(scan_id, -1001234567890, messages)
    assert count == 2

    # Query
    results = msg_store.query_by_scan(scan_id)
    assert len(results) == 2

    # Filter by extension
    results = msg_store.query_by_scan(scan_id, filters={"file_extension": ".zip"})
    assert len(results) == 1
    assert results[0]["file_name"] == "test1.zip"
