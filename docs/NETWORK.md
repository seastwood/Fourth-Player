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

## If it does not connect

| Symptom | Almost always |
|---|---|
| Page loads, PIN accepted, video never starts | **UDP 40000–40100 is not forwarded.** This is the common one. Check the table above; forwarding 8443 alone is never enough |
| Black over a VPN specifically | Packet size. A tunnel's MTU is smaller than a plain link's, and an RTP packet that does not fit is dropped rather than shrunk. `rtp_mtu` defaults to 1200 to survive this; lower it with `--mtu 1100` if a tunnel inside a tunnel is involved |
| Works on the LAN, not from outside | Testing from inside the network. Use mobile data |
| Connects, then drops after a few minutes | `timeout tunnel` missing in HAProxy |
| `502` from Cloudflare | HAProxy cannot reach the box, or `behind_proxy` is false so the box is speaking TLS to a proxy expecting plain HTTP |
| Video plays, controller does nothing | The data channel, not the video. Check the browser console; the pad must be pressed once before a browser reveals it |
