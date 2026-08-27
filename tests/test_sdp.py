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
    from fourthplayer.video import with_fmtp, h264_profile_level_id
except ImportError as exc:
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

print("\nFAILURES: %d" % len(fails))
sys.exit(1 if fails else 0)
