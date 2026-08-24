# Getting it out of the house

The server is one process on one machine. Everything in this file happens
somewhere else — the router, the DNS, the reverse proxy — which is why it is
the part most likely to go wrong and the part the installer cannot do.

The deployment this was written against is **pfSense running HAProxy, with
Cloudflare in front**. That combination has one constraint that shapes
everything below.

## The constraint

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

pfSense forwards a UDP range straight to the box, and a **DNS-only (grey cloud)**
hostname supplies the address that ends up in the ICE candidates. Guests connect
to your address directly, so there is no relay in the path and no added latency.

The cost, stated plainly: **anyone holding a live invite can see your home IP
address.** For friends that is proportionate. For a link you would post in
public it is not.

```
Firewall → NAT → Port Forward
  Interface     WAN
  Protocol      UDP
  Destination   WAN address, port range 40000–40100
  Redirect to   192.168.1.50, same range      # your box
```

> **A caveat that matters.** The server cannot currently bound that range.
> `webrtcbin`'s `ice-agent` property cannot safely be read from Python on
> GStreamer 1.24 — doing so corrupts the agent and the process dies the moment
> the first guest negotiates — and the ICE agent is not reachable through
> `GstChildProxy` either. So libnice picks ephemeral ports, and a narrow forward
> will sometimes miss them.
>
> Until that is fixed, either forward a wide range (`1024–65535` to the box,
> which is a real widening of your attack surface and should be weighed), or use
> option 2 below and forward nothing at all. `rtp_port_min` / `rtp_port_max` in
> the config are read but not yet applied; they are there for when this is fixed.

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
| `play.example.com` | Proxied (orange) | your WAN address | the page and signalling |
| `media.example.com` | **DNS only (grey)** | your WAN address | nothing directly — it is what the ICE candidates resolve to |

The grey record is what stops Cloudflare being asked to do something it cannot.
If you use option 2 you do not need it.

Set `public_url` so the printed link and the QR code use the proxied name:

```json
{ "public_url": "https://play.example.com", "behind_proxy": true }
```

`behind_proxy` also makes the server speak plain HTTP (HAProxy is terminating
TLS) and start trusting `X-Forwarded-For` for rate-limiting. **Only** set it
when a proxy really is in front: without one, any caller could pick their own
rate-limit bucket by sending a header, and the lockout would stop working.

## HAProxy on pfSense

One backend, one frontend rule. The only thing to get right is the WebSocket
upgrade, which HAProxy handles natively as long as the timeout is not short —
signalling sockets stay open for the whole session.

**Backend**

```
Name             fourthplayer
Server           192.168.1.50:8443            # your box
Encrypt(SSL)     no      # behind_proxy = true
Health check     HTTP, GET /healthz, expect 200
```

**Frontend** — an ACL on the hostname, pointing at that backend:

```
Type                        http / https(offloading)
ACL: Host matches           play.example.com
Action                      Use Backend → fourthplayer
```

**Timeouts.** In the backend's advanced settings:

```
timeout tunnel  1h
timeout client  1h
timeout server  1h
```

Without `timeout tunnel`, HAProxy closes the signalling socket after the default
idle timeout and a guest silently loses the ability to renegotiate — the video
keeps playing, which makes it look like nothing is wrong.

Do **not** add a rule for anything but the hostname above. The control socket is
a Unix socket and is not reachable over the network at all; there is no admin
HTTP route to protect, and there should never be one.

## Checking it from outside

```sh
curl -sS https://play.example.com/healthz          # expect: ok
```

Then open the join link on a phone on mobile data, with wifi off. That is the
only test that actually exercises the path a guest takes; a laptop on your own
LAN will connect over the local candidates and prove nothing about the internet.

## If it does not connect

| Symptom | Almost always |
|---|---|
| Page loads, PIN accepted, video never starts | The media path. ICE has no route — check the UDP forward, or set a TURN server |
| Works on the LAN, not from outside | Testing from inside the network. Use mobile data |
| Connects, then drops after a few minutes | `timeout tunnel` missing in HAProxy |
| `502` from Cloudflare | HAProxy cannot reach the box, or `behind_proxy` is false so the box is speaking TLS to a proxy expecting plain HTTP |
| Video plays, controller does nothing | The data channel, not the video. Check the browser console; the pad must be pressed once before a browser reveals it |
