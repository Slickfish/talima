# Tailscale in front of the hosted testers

This note says how to put Tailscale in front of the `hosted` profile's
testers (docs/DEPLOY.md, "One pod per tester, behind your identity proxy").
It is a note, not a tested path. CI never runs Tailscale: the deploy smoke
uses a stand-in (`deploy/compose.identity-sim.yml`).

**Read this first.** Tailscale's own documentation could not be read when
this note was written: the session that wrote it could not reach
tailscale.com. Every statement about Tailscale below is marked *(from
memory, not checked here)*. The list at the end says what to confirm on
Tailscale's own pages before you rely on any of it.

## What NEXUS needs from the identity proxy

These are NEXUS's own rules, and the repository tests them.

- One NEXUS process per tester. Each one answers for one login, its owner
  (`NEXUS_T1_OWNER` for tester 1, and so on), compared exactly, case
  included. Only the spaces and tabs around a login are trimmed. A request
  with any other login gets 403. With no owner set, every request gets 403
  "owner not set". NEXUS never adopts a login.
- The login arrives in one header, `NEXUS_IDENTITY_HEADER`
  (`Tailscale-User-Login` unless you set it). It must arrive exactly once,
  and not empty. Without it, a request gets 401.
- Each tester's relay (`hosted-t1` and so on, port 8100) answers only the
  identity proxy's one address on the `front` network,
  `NEXUS_IDENTITY_PROXY_IP` (172.31.240.250 unless you set it). It answers
  403 to anything else.
- The identity proxy must remove any copy of the header that a client sent
  before it sets its own. NEXUS takes the header as it arrives, so this is
  what the whole arrangement rests on.
- To check a tester's process: `GET /whoami` through the identity proxy
  names that tester's login and the mechanism `proxy`.

## The shape

Run a Tailscale container on the `front` network at the identity proxy's
address, with a route out, because Tailscale needs the internet. Give each
tester a port of their own on the Tailscale node, each forwarding to that
tester's relay. Each tester's console then has its own address. A path
prefix per tester does not work: the console fetches absolute paths, such
as `/chat`.

Add the container in an override file of your own, saved beside
`docker-compose.yml` as `compose.tailscale.yml` (the name the command below uses; the
repository has no such file), as the smoke adds its stand-in. A sketch, not tested here, with the image's settings *(from
memory, not checked here)*:

    services:
      tailscale:
        image: tailscale/tailscale:<a pinned tag>
        profiles: [hosted]
        hostname: nexus
        environment:
          TS_AUTHKEY: ${TS_AUTHKEY:?set a Tailscale auth key}
          TS_STATE_DIR: /var/lib/tailscale
        volumes:
          - tailscale_state:/var/lib/tailscale
        networks:
          front:
            ipv4_address: ${NEXUS_IDENTITY_PROXY_IP:-172.31.240.250}
          egress: {}
    volumes:
      tailscale_state: {}

Start the stack with both files:

    docker compose -f docker-compose.yml -f compose.tailscale.yml --profile hosted up -d

Then have Tailscale Serve send each port to a tester's relay *(from
memory, not checked here)*:

    docker compose exec tailscale tailscale serve --bg --https=443 http://hosted-t1:8100
    docker compose exec tailscale tailscale serve --bg --https=8443 http://hosted-t2:8100
    docker compose exec tailscale tailscale serve --bg --https=10000 http://hosted-t3:8100

Set each tester's owner to the login Tailscale sends for them, exactly:

    NEXUS_T1_OWNER=<tester 1's Tailscale login>

## What Tailscale does, as remembered

- Serve terminates HTTPS on the node and forwards to the target it is
  given *(from memory, not checked here)*.
- For a request from a tailnet user's device, Serve adds
  `Tailscale-User-Login`, `Tailscale-User-Name` and
  `Tailscale-User-Profile-Pic` *(from memory, not checked here)*. NEXUS
  reads only the one `NEXUS_IDENTITY_HEADER` names.
- A request from a tagged device, or one through Funnel (the public
  internet), carries no identity header *(from memory, not checked here)*.
  NEXUS then answers 401, so do not route testers through Funnel.
- Serve removes a client's own copies of those headers before it sets them
  *(from memory, not checked here)*.
- Older versions of the serve command refused a target that was not
  localhost or 127.0.0.1 *(from memory, not checked here)*. If yours does,
  the target `http://hosted-t1:8100` will not work. Then run a small
  forwarder in the Tailscale container's own network namespace
  (`network_mode: service:tailscale`), listening on 127.0.0.1 and
  forwarding to the relays, and point Serve at it. The relays still see the
  identity proxy's address, because the forwarder shares the namespace.
  The forwarder must pass the identity header through unchanged.

Any tailnet user who can reach the node can open any tester's port. They
present their own login, and another tester's NEXUS answers 403. Use
Tailscale's access controls to keep the node to the people who should reach
it.

That is not the whole story, because Serve identifies the device, not the
page that made a request *(from memory, not checked here)*. It gives the
tester's login to every request from the tester's device. That includes a
request that a page from another site, open in the tester's browser, makes
the browser send. NEXUS refuses such a request when it could change
something. Any method but GET, HEAD and OPTIONS from a browser must say
`Sec-Fetch-Site: same-origin`, or it gets 403. One that carries `Origin` and
no `Sec-Fetch-Site` gets 403 too (an older browser: the rule fails closed).
The rule does not cover two things:

- A program on the tester's device that is not a browser sends neither
  header. NEXUS takes it as the tester, as it takes the operator's scripts.
  Keep other people's programs off a tester's device.
- The rule reads the two headers the browser sends. Serve must pass them
  through as they came *(from memory, not checked here)*.

## Confirm on Tailscale's own pages before you rely on this

1. The header's exact name, and the exact form of the login it carries
   (an email address or not, and its case). NEXUS compares it exactly.
2. That Serve removes a client's own copy of the identity header, on
   every request, before it sets its own.
3. Whether the serve command accepts a target that is not localhost, such
   as `http://hosted-t1:8100`. If it does not, use the forwarder above.
4. Which HTTPS ports Serve may listen on, for one port per tester.
5. That a request from a tagged device or through Funnel carries no
   identity header.
6. The container image's settings (the auth key and the state directory)
   and a tag to pin.
7. That Serve passes a browser's `Sec-Fetch-Site` and `Origin` headers
   through unchanged. NEXUS's cross-site rule reads them.
