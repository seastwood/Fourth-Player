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
    # 1.5 Mb/s. There is no congestion control here -- gstreamer's rtpgccbwe is
    # not in this distribution -- so the bitrate never adapts, and a figure the
    # link cannot carry does not degrade the picture, it queues packets and
    # turns into delay. Conservative is therefore the low-latency choice.
    bitrate_kbps: int = 1500
    hardware_encode: bool = True
    # target-usage 1 measured *fastest* on Polaris, which is the opposite of
    # what the name suggests; 4 and 7 both came in a third slower.
    target_usage: int = 1
    # 0 means "two seconds' worth", computed from the frame rate. A keyframe is
    # several times the size of the frames around it, so on a thin link one per
    # second is a burst per second, and every burst is a delay spike. Guests
    # joining are sent one on demand regardless, so the interval only bounds
    # the wait when that request is lost.
    keyframe_interval: int = 0

    # How much encoded video may pile up per guest before frames are dropped.
    # This is latency, directly: when the link cannot keep up, the queue fills
    # and everything behind it is that much further behind. It was 200 ms,
    # which is a fifth of a second of delay handed out for free the moment a
    # connection gets tight.
    queue_ms: int = 60

    # The encoder's own buffer, in milliseconds of bitrate. Small keeps it from
    # smoothing bursts by holding frames back -- smoothing is exactly what adds
    # delay here.
    cpb_ms: int = 150

    # constrained-baseline is the one every browser accepts. The encoder
    # defaults to high, and webrtcbin sends no a=fmtp line at all, so a guest
    # applies the spec default -- constrained baseline, single-NAL -- and a
    # strict browser rejects the high-profile stream it actually receives.
    # Safari does exactly that, intermittently enough to look like a network
    # fault. main or high look slightly better and are a gamble.
    h264_profile: str = "constrained-baseline"

    # "auto" asks the first guest what it can decode and picks the best codec
    # both ends manage; naming one pins it instead. H.265 is about half the
    # bitrate for the same picture and is refused by most browsers -- Safari on
    # recent Apple hardware takes it, Firefox does not, Chrome mostly does not
    # -- which is exactly why it is worth asking rather than assuming.
    #
    # The picture is encoded once for everybody, so this is a property of the
    # session and not of each guest: it is settled while nobody else is
    # connected, and a later guest that cannot decode it is told so plainly.
    codec: str = "auto"

    # -- audio --
    audio: bool = True
    # The monitor of whatever sink applications are actually playing into,
    # rather than a fixed device name. Following the default is what makes this
    # keep working when the sound card, the HDMI output or a virtual sink
    # changes underneath -- and on the machine this was built for the default
    # sink is one Sunshine created, not the HDMI output you would have guessed.
    audio_device: str = "@DEFAULT_MONITOR@"
    # 128 rather than 96. Both are respectable for stereo Opus, and the
    # difference was academic for as long as the offer said nothing and every
    # guest decoded it as one channel anyway. Now that both channels arrive,
    # the extra 32 kb/s buys the top end back on a stream whose whole audio
    # budget is a rounding error beside the video's.
    audio_bitrate_kbps: int = 128
    # 10 ms frames. Opus will happily do 20 or 60, and every one of those
    # milliseconds is added to the delay between the television and the guest.
    # 10 ms is the low-latency choice and 20 the robust one: the same sound in
    # half as many packets, so there is half as much to lose. Chopped audio
    # under a picture that is otherwise fine is usually packet loss rather than
    # bandwidth, and this is the knob for it.
    audio_frame_ms: int = 10
    # How much sound may wait between the capture and the encoder. It exists
    # to absorb the encoder or a guest's pipeline hesitating for a moment;
    # anything longer than this is not a hesitation but a fault, and the
    # oldest audio is dropped rather than the capture being held up. 120 ms is
    # far more than a hesitation and far less than a delay anybody notices,
    # and it is only ever reached when something is already wrong.
    audio_queue_ms: int = 120

    # -- what a guest's controller is allowed to reach --
    # A guest's pad is a real input device on this machine, wired to the
    # machine and not to the game: whatever has the foreground reads it. That
    # was tolerable when the only thing in front was ever RetroArch. Steam's
    # gamepad interface is a mouse pointer, an on-screen keyboard, a store
    # with a saved card, the account settings and a button marked "switch to
    # desktop", and none of that is something an invited guest should be able
    # to drive from another house.
    #
    # So the frames stop at the television while the thing in front is one of
    # those. Off makes this exactly what it was before, which is the right
    # setting for a machine with no Steam on it and nobody but the household
    # on the link.
    guest_input_needs_a_game: bool = True
    # Matched against the focused window's class and name, lowercased. A
    # blocklist, and deliberately: the failure an allowlist produces is a
    # controller going dead in the middle of a game nobody thought to name,
    # which is worse here than a guest reaching a menu. Empty falls back to
    # the list in screen.py.
    shell_windows: tuple = ()
    # How often to look. Half a second is far quicker than anybody can walk
    # from the television to a menu, and 120 subprocess calls a minute is
    # nothing beside encoding video.
    shell_poll_ms: int = 500

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
    # The guest's buffer: how much video the browser holds back before
    # showing it, so a frame that arrives late is still on time. This is sent
    # to the guest with the offer and applied there, because that is the only
    # end it can be applied at -- webrtcbin has a `latency` property that reads
    # like this one and is set from it, but it sizes the buffer for media
    # coming *in*, and a host that only sends never uses it. Raising this here
    # therefore did nothing at all until the browser was told.
    #
    # 60 ms is two frames at 30 fps. Every packet of a frame is sent in one
    # burst, and a burst spread out by a wifi hop or a tunnel used to arrive
    # after the moment Chrome had already decided to draw it -- which is a
    # picture that holds still and then jumps. Lower is less delay and less
    # tolerance for that; a guest on mobile data is happier at 100.
    jitter_ms: int = 60

    # -- session --
    # Whether a guest may start a game, and on what terms. See
    # session.LAUNCH_POLICIES. Off unless deliberately turned on: everything
    # else here affects a picture, and this one affects somebody's television.
    # Whether a guest needs the whole link, or just the address and the PIN.
    # On means the link is required, which is two secrets instead of one.
    require_link: bool = True
    # A PIN of the owner's choosing, reused for every session, instead of six
    # fresh digits each time that have to be read off the television before
    # anybody can join. Empty means a new random one per session, which is
    # still the safer default: a set PIN is one secret that stops changing.
    # 4 to 12 digits -- see invites.check_fixed_pin.
    fixed_pin: str = ""
    # Whether guests may drive the same controller together -- any number
    # of them, not a pair. Off means
    # picking a controller somebody holds swaps the two of you, which is the
    # right answer when everybody is a separate player. On is for the games
    # that were built to be played by passing one pad round a sofa -- Advance
    # Wars and the rest -- where the people taking turns are all player one.
    share_pads: bool = False
    guest_launch: str = "off"
    # How many can join at once. Three by default -- a fourth player for a
    # sofa that already has three on it, which is where the name comes from.
    slots: int = 3
    # Where the picker kodi-retrobox shows stops being able to lay the players
    # out, and past which nothing here has been run.
    max_slots: int = 8
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
        # This file can hold a set PIN, which is a password for the television.
        # Create it unreadable to anybody else rather than fixing the mode
        # afterwards, so there is no moment where it is world-readable.
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as handle:
            json.dump(asdict(self), handle, indent=2)
            handle.write("\n")
        try:
            os.chmod(path, 0o600)          # an existing file keeps its mode
        except OSError:
            pass

    @classmethod
    def load(cls, path=CONFIG_PATH):
        if not os.path.exists(path):
            return cls()
        with open(path) as handle:
            data = json.load(handle)
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in known})
