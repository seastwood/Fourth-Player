"""Settings, and where the defaults come from.

The video defaults are not taste. They were measured on the machine this was
written for -- an AMD Phenom II X6 with a Radeon RX 470 -- where 720p60 through
the hardware encoder runs at about 54 fps for a quarter of one core, and 1080p60
does not run at all (36 fps at best). Raising `height` above 720 on hardware
like that buys a sharper picture and loses half the frames.
"""

import json
import os
from dataclasses import dataclass, asdict, field

STATE_DIR = os.path.expanduser("~/.local/state/fourth-player")
CONFIG_PATH = os.path.expanduser("~/.config/fourth-player/config.json")


# Named starting points. Frame rate and resolution are traded before bitrate,
# because on a thin link a smaller sharp picture beats a bigger smeared one,
# and keyframes are spaced further apart when there is less budget to spend on
# them -- a guest joining is sent one on demand anyway.
PRESETS = {
    "smooth":  dict(width=1280, height=720, fps=30, bitrate_kbps=3000,
                    keyframe_interval=0),
    "sharp":   dict(width=1280, height=720, fps=60, bitrate_kbps=8000,
                    keyframe_interval=0),
    "remote":  dict(width=960, height=540, fps=30, bitrate_kbps=2000,
                    keyframe_interval=60),
    "minimum": dict(width=854, height=480, fps=30, bitrate_kbps=800,
                    keyframe_interval=90),
}


@dataclass
class Config:
    # -- capture and encode --
    display: str = ":0"
    width: int = 1280
    height: int = 720
    # 30 rather than 60. Measured, this machine holds 54 fps at 720p60 with
    # nothing else running -- and a game is something else running. Halving the
    # frame rate halves the encode load, which is what actually shortens the
    # delay between the television and the guest's screen. Set 60 if the host
    # has the headroom.
    fps: int = 30
    # 3 Mb/s. Comfortable on a LAN and survivable over a VPN or a home upload,
    # which is where this actually gets used.
    bitrate_kbps: int = 3000
    hardware_encode: bool = True
    # target-usage 1 measured *fastest* on Polaris, which is the opposite of
    # what the name suggests; 4 and 7 both came in a third slower.
    target_usage: int = 1
    # 0 means "one second's worth", computed from the frame rate. A guest
    # joining mid-session is sent an immediate keyframe anyway; this only
    # bounds the wait if that request goes missing.
    keyframe_interval: int = 0

    # -- audio --
    audio: bool = True
    # The monitor of whatever sink applications are actually playing into,
    # rather than a fixed device name. Following the default is what makes this
    # keep working when the sound card, the HDMI output or a virtual sink
    # changes underneath -- and on the machine this was built for the default
    # sink is one Sunshine created, not the HDMI output you would have guessed.
    audio_device: str = "@DEFAULT_MONITOR@"
    audio_bitrate_kbps: int = 96
    # 10 ms frames. Opus will happily do 20 or 60, and every one of those
    # milliseconds is added to the delay between the television and the guest.
    audio_frame_ms: int = 10

    # -- packet size --
    # The RTP packet, before DTLS-SRTP (~16 bytes), UDP (8) and IP (20) are
    # added. GStreamer defaults to 1400, which puts ~1444 bytes on the wire and
    # does not fit a WireGuard tunnel's usual 1420 MTU: every video packet is
    # fragmented or dropped, while the tiny audio packets sail through. The
    # symptom is a black picture with sound, from outside the network only.
    # Sunshine ships 1392 for the same reason; 1200 is the value most WebRTC
    # stacks settle on and survives a tunnel inside a tunnel.
    rtp_mtu: int = 1200

    # -- reaching us from outside --
    # Announce each LAN socket a second time at the public address, same port,
    # so a static port forward is usable. Needed because a symmetric NAT makes
    # STUN's reported port worthless -- see fourthplayer/net.py.
    advertise_public_ip: bool = True
    public_ip: str = ""            # blank: discover it with STUN at startup

    # -- webrtc --
    stun_server: str = "stun://stun.cloudflare.com:3478"
    turn_server: str = ""
    ice_tcp: bool = True
    # Bounded so the router rule can be written once. Five ports is comfortable
    # for three guests sharing one bundled connection each.
    rtp_port_min: int = 40000
    rtp_port_max: int = 40100
    # The receiver's buffer. Lower is less delay and less tolerance for a
    # network that arrives unevenly; 30 ms is a reasonable middle for people on
    # home connections rather than mobile data.
    jitter_ms: int = 30

    # -- session --
    slots: int = 3
    default_duration_minutes: int = 120
    max_duration_minutes: int = 480

    # -- server --
    host: str = "0.0.0.0"
    port: int = 8443
    # Serve TLS ourselves. Keep this on even behind a reverse proxy unless the
    # proxy really is speaking plain HTTP to us: the browser needs a secure
    # context or it will not hand out the Gamepad API at all, so anybody
    # reaching the box directly over http:// gets a picture and no controller.
    tls: bool = True
    # Trust X-Forwarded-For. Separate from `tls` on purpose -- a proxy that
    # terminates its own TLS and one that re-encrypts to us are both proxies,
    # and both need this, or every guest shares one rate-limit bucket and one
    # person's failed PINs lock out the rest.
    behind_proxy: bool = False
    cert_path: str = os.path.join(STATE_DIR, "cert", "server.pem")
    key_path: str = os.path.join(STATE_DIR, "cert", "server.key")
    public_url: str = ""

    # -- host machine --
    manage_gpu_clocks: bool = True
    overlay: bool = True

    def save(self, path=CONFIG_PATH):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as handle:
            json.dump(asdict(self), handle, indent=2)
            handle.write("\n")

    @classmethod
    def load(cls, path=CONFIG_PATH):
        if not os.path.exists(path):
            return cls()
        with open(path) as handle:
            data = json.load(handle)
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in known})
