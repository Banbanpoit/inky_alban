from flask import Flask

from src.blueprints.playlist import playlist_bp
from src.model import Playlist, PlaylistManager


class DummyDeviceConfig:
    def __init__(self, playlist_manager):
        self.playlist_manager = playlist_manager
        self.write_calls = 0

    def get_playlist_manager(self):
        return self.playlist_manager

    def write_config(self):
        self.write_calls += 1


class DummyRefreshTask:
    def __init__(self):
        self.signal_calls = 0

    def signal_config_change(self):
        self.signal_calls += 1


def make_client(playlists):
    app = Flask(__name__)
    app.register_blueprint(playlist_bp)

    playlist_manager = PlaylistManager(playlists=playlists)
    device_config = DummyDeviceConfig(playlist_manager)
    refresh_task = DummyRefreshTask()

    app.config["DEVICE_CONFIG"] = device_config
    app.config["REFRESH_TASK"] = refresh_task

    return app.test_client(), device_config, refresh_task, playlist_manager


def test_toggle_playlist_success():
    client, device_config, refresh_task, playlist_manager = make_client([
        Playlist("Default", "00:00", "24:00", enabled=True)
    ])

    response = client.put("/toggle_playlist/Default", json={"enabled": False})

    assert response.status_code == 200
    data = response.get_json()
    assert data["success"] is True
    assert data["enabled"] is False
    assert playlist_manager.get_playlist("Default").enabled is False
    assert device_config.write_calls == 1
    assert refresh_task.signal_calls == 1


def test_toggle_playlist_missing_enabled_returns_400():
    client, _, _, _ = make_client([Playlist("Default", "00:00", "24:00", enabled=True)])

    response = client.put("/toggle_playlist/Default", json={})

    assert response.status_code == 400


def test_toggle_playlist_invalid_enabled_returns_400():
    client, _, _, _ = make_client([Playlist("Default", "00:00", "24:00", enabled=True)])

    response = client.put("/toggle_playlist/Default", json={"enabled": "yes"})

    assert response.status_code == 400


def test_toggle_playlist_not_found_returns_400():
    client, _, _, _ = make_client([Playlist("Default", "00:00", "24:00", enabled=True)])

    response = client.put("/toggle_playlist/Missing", json={"enabled": False})

    assert response.status_code == 400


def test_update_playlist_legacy_payload_still_works():
    client, device_config, refresh_task, playlist_manager = make_client([
        Playlist("Default", "00:00", "24:00", enabled=False)
    ])

    response = client.put(
        "/update_playlist/Default",
        json={"new_name": "Updated", "start_time": "01:00", "end_time": "23:00"},
    )

    assert response.status_code == 200
    updated = playlist_manager.get_playlist("Updated")
    assert updated is not None
    assert updated.enabled is False
    assert device_config.write_calls == 1
    assert refresh_task.signal_calls == 1


def test_update_playlist_updates_enabled_when_provided():
    client, _, _, playlist_manager = make_client([
        Playlist("Default", "00:00", "24:00", enabled=True)
    ])

    response = client.put(
        "/update_playlist/Default",
        json={"new_name": "Default", "start_time": "00:00", "end_time": "24:00", "enabled": False},
    )

    assert response.status_code == 200
    assert playlist_manager.get_playlist("Default").enabled is False
