#--------------------------------------------------------------------------------
# Unit tests for per-client listeners.
#
# Unlike the other test modules these do not require a running Live: the Live API
# objects are stubbed, so the listener bookkeeping in AbletonOSCHandler can be
# exercised directly. They cover the two properties that make listeners usable by
# more than one OSC client at a time:
#
#   1. two clients may subscribe to the same property without evicting each other
#   2. each client's updates are delivered to that client's own address
#--------------------------------------------------------------------------------

import sys
import types
import logging
import pytest

#--------------------------------------------------------------------------------
# Stub the Live/ableton modules that handler.py imports, so it can be loaded
# outside of Live. Only inserted if absent, so this is a no-op inside Live.
#--------------------------------------------------------------------------------
for _name in ["Live", "ableton", "ableton.v2", "ableton.v2.control_surface",
              "ableton.v2.control_surface.component"]:
    sys.modules.setdefault(_name, types.ModuleType(_name))
if not hasattr(sys.modules["ableton.v2.control_surface.component"], "Component"):
    class _Component:
        def __init__(self, *args, **kwargs):
            pass
    sys.modules["ableton.v2.control_surface.component"].Component = _Component

from ..abletonosc.handler import AbletonOSCHandler

CLIENT_A = ("127.0.0.1", 11001)
CLIENT_B = ("127.0.0.1", 11002)


class MockTrack:
    """A Live object exposing one listenable property."""
    def __init__(self):
        self._volume = 0.0
        self._listeners = []

    def add_volume_listener(self, callback):
        self._listeners.append(callback)

    def remove_volume_listener(self, callback):
        self._listeners.remove(callback)

    @property
    def volume(self):
        return self._volume

    def set_volume(self, value):
        self._volume = value
        for callback in list(self._listeners):
            callback()


class MockOSCServer:
    """Records sends instead of transmitting them."""
    def __init__(self):
        self.sent = []
        self.current_client = CLIENT_A
        self.logger = logging.getLogger("abletonosc")

    def send(self, address, params=(), remote_addr=None):
        self.sent.append((address, params, remote_addr))


class MockManager:
    def __init__(self, osc_server):
        self.osc_server = osc_server


@pytest.fixture
def rig():
    server = MockOSCServer()
    handler = AbletonOSCHandler(MockManager(server))
    handler.class_identifier = "track"
    return server, handler, MockTrack()


def test_two_clients_may_listen_to_the_same_property(rig):
    server, handler, track = rig

    server.current_client = CLIENT_A
    handler._start_listen(track, "volume", (0,))
    server.current_client = CLIENT_B
    handler._start_listen(track, "volume", (0,))

    #--------------------------------------------------------------------------------
    # Both registrations must survive: keying listeners by client is what stops the
    # second subscriber evicting the first (ideoforms/AbletonOSC#73).
    #--------------------------------------------------------------------------------
    assert len(track._listeners) == 2

    server.sent.clear()
    track.set_volume(0.5)

    assert sorted(sent[2] for sent in server.sent) == [CLIENT_A, CLIENT_B]
    assert all(sent[0] == "/live/track/get/volume" for sent in server.sent)
    assert all(sent[1] == (0, 0.5) for sent in server.sent)


def test_unsubscribing_one_client_leaves_the_other_listening(rig):
    server, handler, track = rig

    server.current_client = CLIENT_A
    handler._start_listen(track, "volume", (0,))
    server.current_client = CLIENT_B
    handler._start_listen(track, "volume", (0,))

    server.current_client = CLIENT_A
    handler._stop_listen(track, "volume", (0,))

    server.sent.clear()
    track.set_volume(0.75)

    assert [sent[2] for sent in server.sent] == [CLIENT_B]


def test_stop_listeners_for_client_removes_only_that_client(rig):
    server, handler, track = rig

    server.current_client = CLIENT_A
    handler._start_listen(track, "volume", (0,))
    server.current_client = CLIENT_B
    handler._start_listen(track, "volume", (1,))

    assert handler.stop_listeners_for_client(CLIENT_B) == 1

    server.sent.clear()
    track.set_volume(1.0)

    assert [sent[2] for sent in server.sent] == [CLIENT_A]


def test_single_client_behaviour_is_unchanged(rig):
    server, handler, track = rig

    server.current_client = CLIENT_A
    handler._start_listen(track, "volume", (0,))

    #--------------------------------------------------------------------------------
    # _start_listen sends the current value immediately on registration.
    #--------------------------------------------------------------------------------
    assert server.sent[-1] == ("/live/track/get/volume", (0, 0.0), CLIENT_A)

    server.sent.clear()
    track.set_volume(0.25)

    assert server.sent == [("/live/track/get/volume", (0, 0.25), CLIENT_A)]


def test_resubscribing_same_client_does_not_duplicate(rig):
    server, handler, track = rig

    server.current_client = CLIENT_A
    handler._start_listen(track, "volume", (0,))
    handler._start_listen(track, "volume", (0,))

    assert len(track._listeners) == 1

    server.sent.clear()
    track.set_volume(0.5)

    assert len(server.sent) == 1
