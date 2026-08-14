"""HiveMind <-> Twilio SMS bridge.

A HiveMind bridge is a satellite whose input and output are a chat
platform instead of a microphone. This one is a small FastAPI web
server: Twilio POSTs inbound SMS to a webhook this bridge exposes, the
text is forwarded onto the HiveMind bus, and the hub's ``speak`` reply
is sent back to the sender through the Twilio REST API.

Connection lifecycle, spelled out because getting it wrong is the
recurring bug across every HiveMind bridge written so far:

- ``HiveMessageBusClient.connect()`` already starts and owns the
  reconnect worker in a background thread, and blocks synchronously
  until the handshake completes (or fails). Call it exactly once. Do
  not also call ``run_forever()`` afterwards -- there is nothing left
  to start, the connection is already live in its own thread.
- Nothing is forwarded to HiveMind before ``connect()`` returns.
- host/port are configuration, not constants.
- a freshly registered HiveMind client is denied every message type
  until a hub admin runs ``hivemind-core allow-msg
  recognizer_loop:utterance <client_id>`` (and usually ``speak`` too);
  this bridge cannot do that step itself. See the README.
"""
from typing import Optional

from fastapi import FastAPI, Form, Response
from hivemind_bus_client import (
    HiveMessage,
    HiveMessageType,
    HiveMessageBusClient,
)
from ovos_bus_client.message import Message
from ovos_utils.log import LOG

platform = "HiveMindTwilioBridgeV0.1"

# hivemind-bus-client >= 1.0.13a1 makes HiveMessageBusClient.connect()
# block on the handshake; handshake_max_retries=None (the client's own
# default) retries forever. Bound it here so a stalled/unreachable hub
# (down, wrong password) fails fast instead of hanging the bridge.
DEFAULT_HANDSHAKE_MAX_RETRIES = 10


class HiveMindTwilioBridge:
    """Bridge Twilio SMS to a HiveMind node."""

    def __init__(self,
                 account_sid: Optional[str] = None,
                 auth_token: Optional[str] = None,
                 twilio_number: Optional[str] = None,
                 key: Optional[str] = None,
                 password: Optional[str] = None,
                 host: Optional[str] = None,
                 port: int = 5678,
                 self_signed: bool = False,
                 lang: str = "en-us",
                 site_id: str = "twilio",
                 handshake_max_retries: int = DEFAULT_HANDSHAKE_MAX_RETRIES,
                 *,
                 client: Optional[HiveMessageBusClient] = None,
                 twilio_client=None):
        """
        Parameters
        ----------
        account_sid, auth_token: Twilio account credentials, used to send
            replies via the REST API. Required unless ``twilio_client`` is
            injected (tests).
        twilio_number: the Twilio phone number this bridge sends replies
            from. Required unless ``twilio_client`` is injected.
        key, password, host, port, self_signed: HiveMind hub connection.
        lang: default utterance language tag.
        site_id: this bridge's HiveMind site id.
        handshake_max_retries: bound on connect()'s handshake retries so
            a stalled/unreachable hub fails fast instead of hanging the
            bridge forever (the client's own default is unbounded).
        client: pre-built HiveMessageBusClient (tests / advanced setups).
            NOTE: HiveMessageBusClient does NOT open a connection in
            __init__ -- call connect_hivemind() to connect.
        twilio_client: pre-built ``twilio.rest.Client`` (tests). When not
            given, one is built lazily from account_sid/auth_token the
            first time a reply needs to be sent.
        """
        if twilio_client is None and not (account_sid and auth_token and twilio_number):
            raise ValueError(
                "account_sid, auth_token and twilio_number are required "
                "unless a twilio_client is injected"
            )

        self.account_sid = account_sid
        self.auth_token = auth_token
        self.twilio_number = twilio_number
        self.lang = lang
        self.site_id = site_id
        self.handshake_max_retries = handshake_max_retries
        self._twilio_client = twilio_client

        self.client = client or HiveMessageBusClient(
            key=key,
            password=password,
            host=host,
            port=port,
            useragent=platform,
            self_signed=self_signed,
        )
        self._connected = False

        self.app = FastAPI()
        self.app.add_api_route("/sms", self.handle_sms_webhook, methods=["POST"])

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------
    def connect_hivemind(self) -> None:
        """Connect to the HiveMind hub and wait for the handshake.

        Calls ``HiveMessageBusClient.connect()`` exactly once; that call
        already starts and owns the reconnect worker thread. Never call
        ``run_forever()`` in addition to this.
        """
        self.client.connect(site_id=self.site_id,
                            handshake_max_retries=self.handshake_max_retries)
        self.client.on_mycroft("speak", self.handle_speak)
        self.client.on_mycroft("hive.complete_intent_failure",
                               self.handle_intent_failure)
        self._connected = True
        LOG.info("== connected to HiveMind")
        LOG.warning(
            "a freshly registered HiveMind client is denied every message "
            "type until an admin runs `hivemind-core allow-msg "
            "recognizer_loop:utterance <client_id>` on the hub (and "
            "usually `speak` too). If messages seem to vanish silently, "
            "check that first."
        )

    def stop(self) -> None:
        try:
            self.client.close()
        except Exception:
            LOG.exception("error closing HiveMind client")
        self._connected = False

    @property
    def twilio_client(self):
        if self._twilio_client is None:
            from twilio.rest import Client
            self._twilio_client = Client(self.account_sid, self.auth_token)
        return self._twilio_client

    # ------------------------------------------------------------------
    # Twilio -> HiveMind
    # ------------------------------------------------------------------
    async def handle_sms_webhook(self, From: str = Form(...),
                                  Body: str = Form(...)) -> Response:
        """Twilio's inbound-SMS webhook.

        Twilio POSTs ``From`` (the sender's E.164 number) and ``Body``
        (the message text) as form fields. Empty bodies are dropped, and
        nothing is forwarded before the HiveMind handshake has completed
        -- forwarding earlier would get the connection killed by the hub
        instead of just failing the one message. Twilio expects a 200
        with an (optionally empty) TwiML response; an empty
        ``<Response/>`` means "no immediate reply", the actual reply is
        sent later via the REST API from the hub's ``speak``.
        """
        if not Body or not Body.strip():
            return Response(content="<Response/>", media_type="application/xml")

        if not self._connected:
            LOG.warning("dropping SMS from %s, not connected to HiveMind yet", From)
            return Response(content="<Response/>", media_type="application/xml")

        self.forward_to_hivemind(Body, From)
        return Response(content="<Response/>", media_type="application/xml")

    def forward_to_hivemind(self, text: str, from_number: str) -> None:
        msg = Message(
            "recognizer_loop:utterance",
            {"utterances": [text], "lang": self.lang},
            {
                "source": platform,
                "destination": "HiveMind",
                "platform": platform,
                "from_number": from_number,
                "user": {"phone_number": from_number},
                "session": {"session_id": f"twilio-{from_number}"},
            },
        )
        self.client.emit(HiveMessage(HiveMessageType.BUS, msg))

    # ------------------------------------------------------------------
    # HiveMind -> Twilio
    # ------------------------------------------------------------------
    def handle_speak(self, message: Message) -> None:
        to_number = message.context.get("from_number")
        if to_number is None:
            return
        utterance = message.data.get("utterance")
        if not utterance:
            return
        self.send_sms(utterance, to_number)

    def handle_intent_failure(self, message: Message) -> None:
        to_number = message.context.get("from_number")
        if to_number is None:
            return
        LOG.error("complete intent failure")
        self.send_sms("I don't know how to answer that", to_number)

    def send_sms(self, text: str, to_number: str) -> None:
        """Send ``text`` back to ``to_number`` via the Twilio REST API.

        Called from the HiveMind bus's own worker thread, not from the
        web server's event loop; the Twilio SDK's ``messages.create`` is
        a plain blocking HTTP call, so no extra scheduling is needed.
        """
        LOG.debug(f"Sending SMS to {to_number}: {text}")
        try:
            self.twilio_client.messages.create(
                body=text, from_=self.twilio_number, to=to_number
            )
        except Exception:
            LOG.exception(f"failed to send SMS to {to_number}")


__all__ = ["HiveMindTwilioBridge", "platform"]
