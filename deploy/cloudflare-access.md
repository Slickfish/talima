# Cloudflare Access in front of the hosted testers

This note says how to put Cloudflare Access, through a Cloudflare Tunnel,
in front of the `hosted` profile's testers (docs/DEPLOY.md, "One pod per
tester, behind your identity proxy"). It is a note, not a tested path. CI
never runs Cloudflare: the deploy smoke uses a stand-in
(`deploy/compose.identity-sim.yml`).

**Read this first.** Cloudflare's own documentation could not be read when
this note was written: the session that wrote it could not reach
developers.cloudflare.com. Every statement about Cloudflare below is
marked *(from memory, not checked here)*. The list at the end says what to
confirm on Cloudflare's own pages before you rely on any of it.

## What NEXUS needs from the identity proxy

These are NEXUS's own rules, and the repository tests them.

- One NEXUS process per tester. Each one answers for one login, its owner
  (`NEXUS_T1_OWNER` for tester 1, and so on), compared exactly, case
  included. Only the spaces and tabs around a login are trimmed. A request
  with any other login gets 403. With no owner set, every request gets 403
  "owner not set". NEXUS never adopts a login.
- The login arrives in one header, `NEXUS_IDENTITY_HEADER`. Behind
  Cloudflare Access set it to `Cf-Access-Authenticated-User-Email`. It must
  arrive exactly once, and not empty. Without it, a request gets 401.
- Each tester's relay (`hosted-t1` and so on, port 8100) answers only the
  identity proxy's one address on the `front` network,
  `NEXUS_IDENTITY_PROXY_IP` (172.31.240.250 unless you set it). It answers
  403 to anything else.
- The identity proxy must remove any copy of the header that a client sent
  before it sets its own. NEXUS takes the header as it arrives.
- To check a tester's process: `GET /whoami` through Cloudflare names that
  tester's email and the mechanism `proxy`.

## The shape

Run `cloudflared`, the tunnel's connector, in a container on the `front`
network at the identity proxy's address, with a route out: it dials out to
Cloudflare, and nothing needs to reach it from outside *(from memory, not
checked here)*. Give each tester a host name of their own, each routed to
that tester's relay, and an Access application on each host name whose
policy allows only that tester. Each tester's console then has its own
origin. A path prefix per tester does not work: the console fetches
absolute paths, such as `/chat`.

Add the container in an override file of your own, saved beside
`docker-compose.yml` as `compose.cloudflare.yml` (the name the command below uses; the
repository has no such file), as the smoke adds its stand-in. A sketch, not tested here, with the image's settings *(from
memory, not checked here)*:

    services:
      cloudflared:
        image: cloudflare/cloudflared:<a pinned tag>
        profiles: [hosted]
        command: tunnel --no-autoupdate run
        environment:
          TUNNEL_TOKEN: ${TUNNEL_TOKEN:?set the tunnel's token}
        networks:
          front:
            ipv4_address: ${NEXUS_IDENTITY_PROXY_IP:-172.31.240.250}
          egress: {}

Start the stack with both files:

    docker compose -f docker-compose.yml -f compose.cloudflare.yml --profile hosted up -d

In the tunnel's configuration, route each host name to a relay *(from
memory, not checked here)*:

    tester1.example.org  ->  http://hosted-t1:8100
    tester2.example.org  ->  http://hosted-t2:8100
    tester3.example.org  ->  http://hosted-t3:8100

Then, in `.env`:

    NEXUS_IDENTITY_HEADER=Cf-Access-Authenticated-User-Email
    NEXUS_T1_OWNER=<tester 1's email, as Cloudflare sends it>

## What Cloudflare does, as remembered

- After a person passes an Access policy, Cloudflare sends the origin
  `Cf-Access-Authenticated-User-Email`, and `Cf-Access-Jwt-Assertion`, a
  token Cloudflare signs *(from memory, not checked here)*.
- Cloudflare's guidance is to validate that token at the origin rather
  than trust the email header alone *(from memory, not checked here)*.
- A request made with an Access service token carries no email
  *(from memory, not checked here)*. NEXUS then answers 401.
- `cloudflared` connects to the service URL it is given, which need not be
  localhost *(from memory, not checked here)*.
- Whether Cloudflare removes a client's own copy of
  `Cf-Access-Authenticated-User-Email` from a request is not remembered
  here at all. Confirm it before anything else.

**NEXUS does not validate the token.** It trusts the email header for one
reason: only the tunnel's connector, at the identity proxy's fixed address,
can reach a tester's relay, and each relay refuses every other peer. So a
forged header can only arrive through Cloudflare itself. That is why the
second item in the list below matters, and why no Access policy that lets
a request through without a person (a bypass rule) may cover a tester's
host name.

**Requests from another site.** Access knows the person by a cookie in
their browser *(from memory, not checked here)*. A page from another site,
open in the tester's browser, can make the browser send a request to the
tester's host name. Whether the cookie goes with it depends on the cookie's
SameSite setting, which is not remembered here. NEXUS refuses such a
request either way when it could change something. Any method but GET,
HEAD and OPTIONS from a browser must say `Sec-Fetch-Site: same-origin`, or
it gets 403. One that carries `Origin` and no `Sec-Fetch-Site` gets 403 too
(an older browser: the rule fails closed). A client that sends neither
header is not a browser, and NEXUS does not refuse it; without the cookie,
Access gives it no login. The rule reads the two headers the browser sends,
so Cloudflare must pass them through as they came.

## Confirm on Cloudflare's own pages before you rely on this

1. The header's exact name (`Cf-Access-Authenticated-User-Email`), and the
   exact form of the email it carries (its case). NEXUS compares it
   exactly.
2. That Cloudflare removes or overwrites a client's own copy of that
   header on every request it sends to the origin, including any a bypass
   or service-token policy lets through.
3. That `Cf-Access-Jwt-Assertion` is the token Cloudflare says to validate
   at the origin, and that you accept NEXUS does not validate it: it trusts
   the email header because only the tunnel reaches the relay.
4. That `cloudflared` accepts a service URL that is not localhost, such as
   `http://hosted-t1:8100`.
5. The container's settings (the tunnel token, the command) and a tag to
   pin.
6. That Access and the tunnel pass a browser's `Sec-Fetch-Site` and
   `Origin` headers through unchanged (NEXUS's cross-site rule reads them),
   and the SameSite setting of the cookie Access sets.
