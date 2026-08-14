# HiveMind Twilio Bridge

This bridges Twilio SMS to a HiveMind node. A HiveMind bridge is a
satellite whose input and output are a chat platform instead of a
microphone: text messages sent to your Twilio number become HiveMind
utterances, and the hub's spoken replies are sent back as SMS to the
same phone number.

This bridge needs a real Twilio account and a real phone number. It has
not been exercised against live Twilio traffic or a real HiveMind hub —
only unit-tested with both sides mocked.

## Getting a Twilio account and number

1. Sign up at [twilio.com](https://www.twilio.com/try-twilio). A trial
   account works for testing, with two limits worth knowing up front:
   trial messages carry a "Sent from a Twilio trial account" prefix, and
   you can only send SMS to phone numbers you have verified in the
   trial console. Neither limit applies once you upgrade to a paid
   account.
2. In the [Twilio console](https://console.twilio.com), copy your
   **Account SID** and **Auth Token** from the dashboard. Treat the
   Auth Token like a password.
3. Buy (or claim the free trial) a phone number with SMS capability:
   console sidebar → Phone Numbers → Buy a number. Note the number in
   E.164 format, e.g. `+15551234567`.

## Pointing Twilio at this bridge

This bridge exposes a webhook at `/sms`. Twilio needs to reach it over
the public internet, so you need either a public server or a tunnel
(e.g. `ngrok http 8080` while testing locally).

1. Run this bridge (see below) so it is listening, e.g. at
   `https://your-public-host/sms`.
2. In the Twilio console: Phone Numbers → your number → Messaging
   configuration → "A MESSAGE COMES IN" → set to Webhook, method POST,
   URL `https://your-public-host/sms`. Save.

Twilio will now POST every inbound SMS to this bridge.

## Registering the bridge on the hub

Every HiveMind client needs credentials and, separately, permission to
send the message types it uses. On the machine running `hivemind-core`:

```bash
hivemind-core add-client
```

This prints an access key and password; pass them to the bridge as
`--access-key` / `--password` (or store them once with
`hivemind-client set-identity` and omit the flags).

A freshly added client is denied every message type by default. The
bridge needs at least:

```bash
hivemind-core allow-msg recognizer_loop:utterance <client_id>
hivemind-core allow-msg speak <client_id>
```

`<client_id>` is printed by `add-client` (and by `hivemind-core
list-clients` afterwards). Skipping this step is the single most common
reason a bridge "connects fine" but nothing ever seems to happen: the
hub silently drops every message the client sends until it is
whitelisted.

## Running the bridge

```bash
pip install .
hivemind-twilio-bridge \
  --twilio-account-sid <ACxxxx> \
  --twilio-auth-token <token> \
  --twilio-number +15551234567 \
  --public-url https://your-public-host \
  --access-key <key> --password <password> \
  --host ws://127.0.0.1 --port 5678
```

This starts a web server (default `0.0.0.0:8080`, override with
`--web-host` / `--web-port`) that Twilio's webhook needs to reach.

Useful flags:

- `--site-id`: this bridge's HiveMind site id. If you run more than one
  bridge on the same host, give each a distinct site id — otherwise
  they collide over the same identity file and pinned peer keys.
- `--self-signed`: accept a self-signed TLS certificate on `wss://`
  hubs.
- `--lang`: the language tag attached to forwarded utterances (default
  `en-us`).

Run `hivemind-twilio-bridge --help` for the full list.

## Docker

```bash
docker build -t hivemind-twilio-bridge .
docker run --rm -p 8080:8080 \
  -e TWILIO_ACCOUNT_SID=... \
  -e TWILIO_AUTH_TOKEN=... \
  -e TWILIO_NUMBER=+15551234567 \
  -e PUBLIC_URL=https://your-public-host \
  -e HIVEMIND_ACCESS_KEY=... \
  -e HIVEMIND_PASSWORD=... \
  -e HIVEMIND_HOST=ws://hivemind-core \
  hivemind-twilio-bridge
```

or via `docker-compose.yml` — copy it, fill in the environment section,
and `docker compose up`.

## What this bridge does, precisely

- Runs a FastAPI web server exposing `POST /sms`, the webhook Twilio
  calls on every inbound SMS with `From` (sender's E.164 number) and
  `Body` (message text) as form fields.
- Connects to the HiveMind hub with
  `hivemind_bus_client.HiveMessageBusClient`.
- Drops empty messages and anything received before the HiveMind
  handshake has completed — forwarding earlier would get the connection
  killed by the hub instead of just failing the one message.
- Forwards each remaining message as a `recognizer_loop:utterance` bus
  message, carrying the sender's phone number in the message context so
  the hub's `speak` reply can be routed back to the right number.
- Sends `speak` replies (and a fixed fallback line on
  `hive.complete_intent_failure`) back to the originating number via the
  Twilio REST API (`twilio.rest.Client.messages.create`).
- Voice calls are not implemented. Twilio also supports a `<Gather>`
  speech webhook for inbound calls; this bridge only handles SMS. Adding
  a `/voice` webhook that transcribes speech via `<Gather
  input="speech">` and replies with TwiML `<Say>` would follow the same
  shape as `/sms` but is out of scope here.

## Testing

```bash
pip install -e .[test]
pytest tests/
```

The test suite mocks both the Twilio REST client and the HiveMind
`HiveMessageBusClient`, so it runs without a live Twilio account or a
live hub. It has not been exercised against real Twilio traffic or a
real HiveMind hub — that needs an actual Twilio account and phone
number, which this repository does not have.
