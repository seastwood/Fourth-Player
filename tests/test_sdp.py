"""The H.264 parameters a guest's browser reads before it agrees to decode.

webrtcbin writes no `a=fmtp` line: the payloader learns the profile from the
stream's first SPS, and the offer is written before a frame has flowed. Every
guest therefore fell back to the spec default -- constrained baseline,
single-NAL -- and the browsers that check properly refused the high-profile
stream that actually turned up. It looked like a network fault, intermittently.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from fourthplayer.video import (with_fmtp, h264_profile_level_id,
                                    h265_fmtp, h265_level_id,
                                    fmtp_for)
except (ImportError, ValueError) as exc:
    print("SKIPPED: %s -- needs the GStreamer bindings, which live on the host"
          % exc)
    sys.exit(0)

fails = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        fails.append(msg)


OFFER = ("v=0\r\n"
         "m=video 9 UDP/TLS/RTP/SAVPF 96\r\n"
         "a=rtpmap:96 H264/90000\r\n"
         "a=sendonly\r\n"
         "m=application 0 UDP/DTLS/SCTP webrtc-datachannel\r\n")

FMTP = "profile-level-id=42e01f;packetization-mode=1;level-asymmetry-allowed=1"

print("the parameters land where a browser looks for them")
out = with_fmtp(OFFER, FMTP)
check("a=fmtp:96 " + FMTP in out, "the fmtp line is present")
lines = out.splitlines()
check(lines.index("a=fmtp:96 " + FMTP) == lines.index("a=rtpmap:96 H264/90000") + 1,
      "and sits directly after its rtpmap, where it belongs")
check(out.count("a=fmtp:96") == 1, "exactly once")

print("\nline endings are not mangled")
check("\r\n" in out and "\n\r" not in out, "CRLF is preserved")
check(out.replace("a=fmtp:96 " + FMTP + "\r\n", "") == OFFER,
      "nothing else in the offer is touched")

print("\nit never overwrites parameters that are already there")
already = OFFER.replace("a=rtpmap:96 H264/90000\r\n",
                        "a=rtpmap:96 H264/90000\r\na=fmtp:96 profile-level-id=640c1f\r\n")
check(with_fmtp(already, FMTP) == already,
      "an offer that already states a profile is left alone")

print("\nand does nothing when there is nothing to say")
check(with_fmtp(OFFER, "") == OFFER, "no fmtp configured, no change")
check(with_fmtp("m=video 9 UDP/TLS/RTP/SAVPF 98\r\n", FMTP)
      == "m=video 9 UDP/TLS/RTP/SAVPF 98\r\n",
      "a stream that is not our H264 payload is left alone")

print("\nthe profile-level-id says something true")
check(h264_profile_level_id("constrained-baseline", 720) == "42e01f",
      "constrained baseline at 720p is 42e01f, got %r"
      % h264_profile_level_id("constrained-baseline", 720))
check(h264_profile_level_id("constrained-baseline", 480) == "42e01e",
      "a smaller frame needs a lower level")
check(h264_profile_level_id("constrained-baseline", 1080) == "42e028",
      "and a larger one a higher level")
check(h264_profile_level_id("main", 720) == "4d001f", "main has its own idc")
check(h264_profile_level_id("high", 720) == "64001f", "so does high")
check(h264_profile_level_id("nonsense", 720).startswith("42e0"),
      "an unknown profile falls back to the one everything accepts")


print()
print("H.265, which had no parameters at all until a 1080p stream went black")

# A browser given no a=fmtp for H.265 does not merely guess a profile, as it
# does for H.264. RFC 7798's default is Main at level 3.1 -- roughly 720p30 --
# so a 1080p60 stream reached a decoder set up for less than a third of it.
HEVC = ("v=0\r\n"
        "m=video 9 UDP/TLS/RTP/SAVPF 96\r\n"
        "a=rtpmap:96 H265/90000\r\n"
        "a=sendonly\r\n")

out = with_fmtp(HEVC, h265_fmtp(1920, 1080, 60), "H265")
check("a=fmtp:96 " in out, "an H.265 offer gets parameters")
check("profile-id=1" in out and "tier-flag=0" in out,
      "Main profile, Main tier -- what the hardware encoders here produce")

# The level has to cover the picture *and* the rate. 1080p fits inside level
# 3.1 by size alone; it is sixty frames a second that needs 4.1.
check(h265_level_id(1280, 720, 30) == 93, "720p30 is level 3.1 (93), got %d"
      % h265_level_id(1280, 720, 30))
check(h265_level_id(1280, 720, 60) == 120, "720p60 is level 4.0 (120), got %d"
      % h265_level_id(1280, 720, 60))
check(h265_level_id(1920, 1080, 30) == 120, "1080p30 is level 4.0 (120), got %d"
      % h265_level_id(1920, 1080, 30))
check(h265_level_id(1920, 1080, 60) == 123, "1080p60 is level 4.1 (123), got %d"
      % h265_level_id(1920, 1080, 60))
check(h265_level_id(3840, 2160, 60) == 153, "4k60 is level 5.1 (153), got %d"
      % h265_level_id(3840, 2160, 60))

# The specific regression: never quietly claim less than is being sent.
check(h265_level_id(1920, 1080, 60) > 93,
      "1080p60 must not be offered at the spec default level, which is what "
      "an absent fmtp line meant and what drew nothing")

# An H.264 offer must not collect H.265 parameters, and vice versa.
check("a=fmtp" not in with_fmtp(OFFER, h265_fmtp(1920, 1080, 60), "H265"),
      "H.265 parameters are not attached to an H.264 rtpmap")
check("a=fmtp" not in with_fmtp(HEVC, FMTP, "H264"),
      "H.264 parameters are not attached to an H.265 rtpmap")

# The payload type is read, not assumed: an fmtp on the wrong number is
# ignored in silence, which is the same black screen with a longer search.
odd = ("m=video 9 UDP/TLS/RTP/SAVPF 98\r\n"
       "a=rtpmap:98 H265/90000\r\n")
check("a=fmtp:98 " in with_fmtp(odd, h265_fmtp(1280, 720, 30), "H265"),
      "the fmtp follows the payload type in the rtpmap")

# H264 and H265 both present -- only the one being sent gets parameters.
both = ("m=video 9 UDP/TLS/RTP/SAVPF 96 98\r\n"
        "a=rtpmap:96 H264/90000\r\n"
        "a=rtpmap:98 H265/90000\r\n")
mixed = with_fmtp(both, h265_fmtp(1280, 720, 30), "H265")
check("a=fmtp:98 " in mixed and "a=fmtp:96" not in mixed,
      "with both offered, only the codec being sent is described")
print()
print("the Stage asks one function which parameters to state")

# The wiring, not just the helper. This is the line that was an empty string
# for H.265: the helpers can all be correct and still never be called.
check(fmtp_for("h265", "main", 1920, 1080, 60) == h265_fmtp(1920, 1080, 60),
      "an H.265 stage states H.265 parameters")
check("profile-id=1" in fmtp_for("hevc", "main", 1280, 720, 30),
      "'hevc' is the same codec by another name")
check(fmtp_for("h264", "high", 1280, 720, 30).startswith("profile-level-id=6400"),
      "an H.264 stage still states its profile-level-id")
check(fmtp_for("h265", "high", 1280, 720, 30).find("profile-level-id") == -1,
      "and H.265 is not given H.264's parameter by mistake")
check(fmtp_for("h265", "main", 1920, 1080, 60)
      != fmtp_for("h265", "main", 1280, 720, 30),
      "the parameters follow the picture, rather than being one fixed string")

print("\nFAILURES: %d" % len(fails))
sys.exit(1 if fails else 0)
