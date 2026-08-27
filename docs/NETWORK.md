# Getting it out of the house

The server is one process on one machine. Everything in this file happens
somewhere else — the router, the DNS, the reverse proxy — which is why it is
the part most likely to go wrong and the part the installer cannot do.

The deployment this was written against is **pfSense running HAProxy, with
Cloudflare in front**. That combination has one constraint that shapes
everything below.

## Do not put Cloudflare's proxy in front of it

**Use a DNS-only record (grey cloud).** This was found the hard way: with the
orange cloud on, the join page loads, the PIN is accepted by the look of it,
and then nothing happens — the socket never answers and the page sits there.
Turning the proxy off fixed it outright.

Why it does not work is less certain than the fact that it does not. The
signalling is a long-lived WebSocket carrying an SDP offer and a stream of ICE
candidates, and a proxy in the middle can buffer it, time it out, or interfere
with the upgrade; Cloudflare also imposes its own idle limits. The honest
summary is that this needs a transparent path and the proxy is not one.

The important part is that **proxying buys nothing here anyway**. The video and
the sound never go through Cloudflare — they are UDP, direct to the box — so
your address is already in the ICE candidates that every guest receives. A
proxied record hides an address that the media hands out regardless, at the
cost of a class of failures that look exactly like the software being broken.

If you want to try it anyway, the symptom to watch for is a join that is never
answered, and the thing to check first is whether the WebSocket upgrade on
`/ws` survives the proxy.

## The other constraint

**Cloudflare's proxy carries HTTP and WebSocket. It will not carry WebRTC's UDP
media.**

So the traffic splits in two, and only one half can go the way you might expect:

| | Path | Through Cloudflare? |
|---|---|---|
| The join page, and signalling | TCP 443 → HAProxy → the box | Yes |
| The video and the pad | UDP, guest ↔ the box | **No** |

Signalling is a WebSocket on the same origin as the page, so HAProxy needs
nothing special beyond allowing the upgrade. The media is the interesting half.

## Three ways to carry the media

Pick one as the default; the others stay as automatic fallbacks, which is how
WebRTC prefers to work anyway — it tries them in order and keeps the first that
connects.

### 1. Direct UDP — the default, and the best to play on

**Two rules, not one.** This is the single thing most likely to go wrong, and
the symptom is always the same: the page loads, the PIN is accepted, the slot is
taken — and the picture stays black.

| Port | Protocol | Carries | Forward it? |
|---|---|---|---|
| `8443` | **TCP** | The join page, the PIN, signalling | Yes |
| `40000–40100` | **UDP** | **The video and the sound** | **Yes — this is the one people miss** |

Forwarding only 8443 gets you a working join page and no video, forever. The
media never touches that port: WebRTC negotiates its own UDP sockets, and until
those are reachable there is nothing to show.

```
Firewall → NAT → Port Forward
  Interface     WAN
  Protocol      TCP
  Destination   WAN address, port 8443
  Redirect to   192.168.1.50, 8443            # your box

Firewall → NAT → Port Forward
  Interface     WAN
  Protocol      UDP
  Destination   WAN address, ports 40000–40100
  Redirect to   192.168.1.50, same range      # your box
```

Keep the range identical on both sides. The address a guest is told to use
comes from STUN, which reports the port your router mapped — if the router
rewrites it to something outside the forwarded range, the guest is told about a
port nothing is listening on.

`rtp_port_min` / `rtp_port_max` in the config set that range, and they are
genuinely applied now: the server builds webrtcbin's ICE agent itself and hands
it over at construction, because reading the one webrtcbin makes for itself
corrupts it. Verify with a guest connected:

```sh
ss -ulnpH | grep "$(pgrep -f 'fourthplayer serve')" | awk '{print $4}'
```

Two sockets per guest should land inside the range. Ports on `1900` are UPnP
discovery and are not media.

**Is it actually offering a reachable address?** The server logs every kind of
ICE candidate it gathers:

```
peer slot0: gathered a host candidate     # a LAN address
peer slot0: gathered a srflx candidate    # your public address, via STUN
```

A guest on the internet needs to see `srflx` (or `relay`). If only `host`
appears, STUN is not getting out and no amount of forwarding will help — check
that outbound UDP 3478 is allowed.

### 2. A TURN relay — nothing forwarded, nothing exposed

The box makes only outbound connections and the guest talks to the relay, so
your address never reaches them and the router needs no inbound rule at all.
Costs 15–40 ms and a relay bill.

Cloudflare's own TURN is a good fit: free to 1 TB a month, then $0.05/GB. At the
default 8 Mb/s that is about 6.7 GB per guest-hour, so the free tier is roughly
150 guest-hours.

```json
{ "turn_server": "turn://USERNAME:CREDENTIAL@turn.cloudflare.com:3478" }
```

### 3. Inside the WebSocket — last resort

Everything rides the HAProxy backend you already have. No new ports, no new DNS,
nothing exposed — but TCP head-of-line blocking means one lost packet stalls the
picture, and proxying video strains Cloudflare's free-plan terms.

Not yet implemented. The browser will fall back to relay before this.

## DNS

Two names, and the difference between them is the whole point:

| Name | Cloudflare | Points at | Carries |
|---|---|---|---|
| `play.example.com` | **DNS only (grey)** | your WAN address | the page, the PIN and the signalling |

One record, unproxied. An orange-cloud record breaks the signalling — see the
section above — and cannot carry the media in any case.

Set `public_url` so the printed link and the QR code use the proxied name:

```json
{ "public_url": "https://play.example.com", "behind_proxy": true }
```

`behind_proxy` also makes the server speak plain HTTP (HAProxy is terminating
TLS) and start trusting `X-Forwarded-For` for rate-limiting. **Only** set it
when a proxy really is in front: without one, any caller could pick their own
rate-limit bucket by sending a header, and the lockout would stop working.

## HAProxy on pfSense

One backend, one frontend rule, and one setting that decides whether it works
at all.

**The backend must speak TLS to the box.** fourth-player serves HTTPS with its
own self-signed certificate, and it should keep doing so even behind a proxy:
browsers withhold the Gamepad API from any page that is not a secure context,
so a guest reaching the box over plain `http://` would get a picture and no
controller. A backend configured for plain HTTP against a TLS port gets its
connection reset, and HAProxy turns that into **502 Bad Gateway** — which is
the single most likely reason a domain 502s while the raw IP works.

**Backend**

```
Name             fourthplayer
Server           192.168.1.50:8443        # your box
Encrypt(SSL)     YES                      # <- the one that causes 502 if wrong
SSL checks       do NOT verify            # the certificate is self-signed
Health check     HTTP, GET /healthz, expect 200
```

pfSense calls the verification checkbox *"Verify SSL Certificate"* or offers a
CA to check against; leave it off, or trust the box's own certificate from
`~/.local/state/fourth-player/cert/server.pem`.

**Frontend** — an ACL on the hostname, pointing at that backend:

```
Type                        http / https(offloading)
ACL: Host matches           fourthplayer.example.com
Action                      Use Backend → fourthplayer
```

**Timeouts.** In the backend's advanced settings:

```
timeout tunnel  1h
timeout client  1h
timeout server  1h
```

Without `timeout tunnel`, HAProxy closes the signalling socket after the
default idle timeout. That no longer ends a session — the media survives it —
but it does stop a guest renegotiating, so a reconnect that should be invisible
turns into a reload.

**On the box**, tell it the name guests will use, and that a proxy is in front:

```json
{
  "public_url": "https://fourthplayer.example.com",
  "behind_proxy": true,
  "tls": true
}
```

`public_url` is what the printed link and the QR code use. `behind_proxy` makes
the server trust `X-Forwarded-For`, so rate limiting sees each guest instead of
bucketing every one of them under the proxy's address — one person fumbling
their PIN would otherwise lock out everybody. It is deliberately separate from
`tls`: a proxy that re-encrypts to the backend is still a proxy.

Only set `tls: false` (or `--no-tls`) if HAProxy really is speaking plain HTTP
to the box, and then nobody may reach it directly by address.

**What the proxy must be able to reach**, all on the one port:

```
GET  /healthz          200      health check
GET  /j/<token>        200      the join page
GET  /static/*         200      its script and stylesheet
     /ws               101      the signalling WebSocket -- upgrade must pass
```

**The media does not go through any of this.** Video and sound are UDP direct
to the box, so the Cloudflare and HAProxy path carries the page and the
signalling only, and the UDP forward from §1 is still required. A domain that
loads the page but shows a black picture is that forward missing, not the
proxy.

## Running over a VPN

WireGuard puts a guest inside the network, so nothing needs forwarding and the
LAN candidates work directly. One thing still bites: **packet size**. A tunnel
carries a smaller MTU than the link underneath it, and an RTP packet that does
not fit is dropped, not fragmented — so the small audio packets arrive and the
large video ones do not. A black picture with working sound is that, every time.

`rtp_mtu` defaults to 1200 for this reason (GStreamer's own default of 1400 does
not fit WireGuard's usual 1420). Lower it further if the tunnel is nested:

```sh
python3 -m fourthplayer serve --mtu 1100
```

## Telling one black screen from another

A guest with no picture can ask their own browser which route it took: tap the
status chip at the top of the stage, or wait for the failure message, and it
reports the address the media actually connected to and how much arrived. That
is the one fact the host cannot know — the host can only say what it sent, not
whether anything landed.

| What the guest sees | What it means |
|---|---|
| `Connected to 192.168.1.x (host), received N kB` | The LAN route. Fine at home; if they are *outside*, they are on a VPN and not testing the public path at all |
| `Connected to <public ip> (srflx), received N kB` | The forward is working |
| `Connected to …, received 0 kB` | The path is open but nothing is flowing — packet size, almost always. Try `--mtu 1100` |
| `Nothing connected. The host offered: …` | No route at all. If the list contains only `192.168.x`, the host never discovered a public address; if it contains the public one, the UDP forward is not open from outside |

**Testing from inside the network proves nothing about the outside.** A device on
WireGuard is *inside*, and a device on the LAN reaching the public address gets
there by NAT hairpin — both connect over the LAN candidate and never touch the
forward. The only real test is a phone on mobile data with wifi off.

## If it does not connect

| Symptom | Almost always |
|---|---|
| Page loads, PIN accepted, video never starts | **UDP 40000–40100 is not forwarded.** This is the common one. Check the table above; forwarding 8443 alone is never enough |
| Black over a VPN specifically | Packet size. A tunnel's MTU is smaller than a plain link's, and an RTP packet that does not fit is dropped rather than shrunk. `rtp_mtu` defaults to 1200 to survive this; lower it with `--mtu 1100` if a tunnel inside a tunnel is involved |
| Works on the LAN, not from outside | Testing from inside the network. Use mobile data |
| Connects, then drops after a few minutes | `timeout tunnel` missing in HAProxy |
| The page loads, the join is never answered | **Cloudflare's proxy is on.** Set the record to DNS only (grey cloud). The signalling WebSocket does not survive the proxy |
| `502` through the domain, while the raw IP works | The backend is set to plain HTTP against a TLS port. Turn **Encrypt(SSL)** on for the backend and leave certificate verification off. This is the usual cause |
| `503` through the domain | No backend matched — the frontend ACL does not match the hostname |
| Page loads on the domain, video black | The UDP forward, not the proxy — media never touches HAProxy |
| Video plays, controller does nothing | The data channel, not the video. Check the browser console; the pad must be pressed once before a browser reveals it |
