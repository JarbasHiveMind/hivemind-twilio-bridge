"""Unit tests: construct the bridge offline and drive it with mocks.

No live Twilio or HiveMind connection is made. A pre-built mock
twilio.rest.Client and a pre-built mock HiveMessageBusClient are
injected so the bridge never touches the network.
"""
import asyncio
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient


def _make_bridge(**kwargs):
    from hivemind_twilio_bridge import HiveMindTwilioBridge

    fake_client = MagicMock(name="HiveMessageBusClient")
    fake_twilio = MagicMock(name="TwilioClient")
    fake_twilio.messages = MagicMock()
    fake_twilio.messages.create = MagicMock()

    bridge = HiveMindTwilioBridge(client=fake_client, twilio_client=fake_twilio, **kwargs)
    return bridge, fake_client, fake_twilio


def test_import_package_and_version():
    import hivemind_twilio_bridge
    from hivemind_twilio_bridge.version import __version__

    assert isinstance(__version__, str)
    assert __version__
    assert hivemind_twilio_bridge.platform.startswith("HiveMindTwilioBridge")


def test_construct_bridge_without_connecting():
    bridge, fake_client, fake_twilio = _make_bridge()
    assert bridge._connected is False
    fake_client.connect.assert_not_called()


def test_credentials_required_without_injected_twilio_client():
    from hivemind_twilio_bridge import HiveMindTwilioBridge

    with pytest.raises(ValueError):
        HiveMindTwilioBridge(client=MagicMock())


def test_connect_hivemind_calls_connect_once_and_registers_handlers():
    """connect_hivemind() must call connect() exactly once, never run_forever()."""
    bridge, fake_client, fake_twilio = _make_bridge()
    bridge.connect_hivemind()

    fake_client.connect.assert_called_once_with(site_id="twilio")
    fake_client.run_forever.assert_not_called()
    assert bridge._connected is True
    registered = {call.args[0] for call in fake_client.on_mycroft.call_args_list}
    assert registered == {"speak", "hive.complete_intent_failure"}


def test_inbound_sms_forwarded_to_hivemind_after_connect():
    from hivemind_bus_client import HiveMessage, HiveMessageType

    bridge, fake_client, fake_twilio = _make_bridge()
    bridge.connect_hivemind()

    client = TestClient(bridge.app)
    resp = client.post("/sms", data={"From": "+15551234567", "Body": "turn on the lights"})

    assert resp.status_code == 200
    fake_client.emit.assert_called_once()
    sent = fake_client.emit.call_args[0][0]
    assert isinstance(sent, HiveMessage)
    assert sent.msg_type == HiveMessageType.BUS
    payload = sent.payload
    assert payload.msg_type == "recognizer_loop:utterance"
    assert payload.data["utterances"] == ["turn on the lights"]
    assert payload.context["from_number"] == "+15551234567"
    assert payload.context["session"]["session_id"] == "twilio-+15551234567"


def test_no_forward_before_hivemind_connected():
    bridge, fake_client, fake_twilio = _make_bridge()
    # deliberately not calling bridge.connect_hivemind()

    client = TestClient(bridge.app)
    resp = client.post("/sms", data={"From": "+15551234567", "Body": "hello"})

    assert resp.status_code == 200
    fake_client.emit.assert_not_called()


def test_empty_body_is_ignored():
    bridge, fake_client, fake_twilio = _make_bridge()
    bridge.connect_hivemind()

    client = TestClient(bridge.app)
    resp = client.post("/sms", data={"From": "+15551234567", "Body": "   "})

    assert resp.status_code == 200
    fake_client.emit.assert_not_called()


def test_speak_sends_sms_to_originating_number():
    from ovos_bus_client.message import Message

    bridge, fake_client, fake_twilio = _make_bridge()
    msg = Message("speak", {"utterance": "hi there"}, {"from_number": "+15551234567"})
    bridge.handle_speak(msg)

    fake_twilio.messages.create.assert_called_once_with(
        body="hi there", from_=bridge.twilio_number, to="+15551234567"
    )


def test_speak_with_no_from_number_is_ignored():
    from ovos_bus_client.message import Message

    bridge, fake_client, fake_twilio = _make_bridge()
    msg = Message("speak", {"utterance": "hi"}, {})
    bridge.handle_speak(msg)
    fake_twilio.messages.create.assert_not_called()


def test_intent_failure_sends_fallback_sms():
    from ovos_bus_client.message import Message

    bridge, fake_client, fake_twilio = _make_bridge()
    msg = Message("hive.complete_intent_failure", {}, {"from_number": "+15551234567"})
    bridge.handle_intent_failure(msg)

    fake_twilio.messages.create.assert_called_once()
    _, kwargs = fake_twilio.messages.create.call_args
    assert kwargs["to"] == "+15551234567"
    assert "don't know" in kwargs["body"]
