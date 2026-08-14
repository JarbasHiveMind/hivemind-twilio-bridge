"""CLI entry point for the HiveMind <-> Twilio SMS bridge.

HiveMind identity (key/password/host/port) defaults to the values stored
by ``hivemind-client set-identity``; flags override them.
"""
import click
import uvicorn
from ovos_utils.log import LOG

from hivemind_twilio_bridge import HiveMindTwilioBridge


def connect_twilio_to_hivemind(account_sid, auth_token, twilio_number,
                                key=None, password=None, host=None,
                                port=5678, self_signed=False,
                                lang="en-us", site_id="twilio"):
    bridge = HiveMindTwilioBridge(
        account_sid=account_sid, auth_token=auth_token,
        twilio_number=twilio_number, key=key, password=password,
        host=host, port=port, self_signed=self_signed, lang=lang,
        site_id=site_id,
    )
    bridge.connect_hivemind()
    return bridge


@click.command()
@click.option("--twilio-account-sid", required=True,
              help="Twilio Account SID (from the Twilio console)")
@click.option("--twilio-auth-token", required=True,
              help="Twilio Auth Token (from the Twilio console)")
@click.option("--twilio-number", required=True,
              help="The Twilio phone number this bridge sends replies from, "
                   "E.164 format e.g. +15551234567")
@click.option("--public-url", default=None,
              help="Public base URL this bridge is reachable at (informational; "
                   "printed at startup as a reminder of what to configure as "
                   "the Twilio webhook, e.g. https://example.com)")
@click.option("--web-host", default="0.0.0.0", help="address to bind the webhook server to")
@click.option("--web-port", type=int, default=8080, help="port to bind the webhook server to")
@click.option("--access-key", "key", default=None,
              help="HiveMind access key (default: from identity file)")
@click.option("--password", default=None,
              help="HiveMind password (default: from identity file)")
@click.option("--host", default=None,
              help="HiveMind host, e.g. ws://127.0.0.1 (default: from identity file)")
@click.option("--port", type=int, default=5678, help="HiveMind port (default: 5678)")
@click.option("--site-id", default="twilio", help="this bridge's HiveMind site id")
@click.option("--self-signed", is_flag=True, help="accept self-signed SSL certificates")
@click.option("--lang", default="en-us", help="utterance language")
def main(twilio_account_sid, twilio_auth_token, twilio_number, public_url,
         web_host, web_port, key, password, host, port, site_id,
         self_signed, lang):
    """Bridge Twilio SMS to a HiveMind node."""
    hive_host = host
    if hive_host and not hive_host.startswith("ws://") and not hive_host.startswith("wss://"):
        hive_host = "ws://" + hive_host

    bridge = connect_twilio_to_hivemind(
        account_sid=twilio_account_sid, auth_token=twilio_auth_token,
        twilio_number=twilio_number, key=key, password=password,
        host=hive_host, port=port, self_signed=self_signed, lang=lang,
        site_id=site_id,
    )

    if public_url:
        LOG.info(f"set the Twilio SMS webhook to: {public_url.rstrip('/')}/sms")
    else:
        LOG.info("set the Twilio number's 'A MESSAGE COMES IN' webhook to "
                  "this bridge's public URL + /sms")

    LOG.info(f"bridge listening on {web_host}:{web_port}; press Ctrl-C to stop")
    try:
        uvicorn.run(bridge.app, host=web_host, port=web_port)
    except KeyboardInterrupt:
        LOG.info("shutting down")
    finally:
        bridge.stop()


if __name__ == '__main__':
    main()
