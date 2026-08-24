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
    bitrate_kbps: int = 6000
    hardware_encode: bool = True
    # target-usage 1 measured *fastest* on Polaris, which is the opposite of
    # what the name suggests; 4 and 7 both came in a third slower.
    target_usage: int = 1
    # 0 means "one second's worth", computed from the frame rate. A guest
    # joining mid-session is sent an immediate keyframe anyway; this only
    # bounds the wait if that request goes missing.
    keyframe_interval: int = 0

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
