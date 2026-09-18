"""Keyframes nobody asked for, on a fixed beat, are what pulses the picture.

Every guest here can ask for a keyframe and does -- that is the whole of the
recovery path, and the log shows it working. A periodic keyframe on top of
that buys nothing and costs a beat: under CBR the encoder has to fit an IDR
inside its VBV window, so the frames after one are starved to pay for it.
Quality drops and recovers on a timer, which at 144fps and a two-second
interval is a pulse every 288 frames.

So the interval is asked of the encoder rather than assumed, because they
disagree about what "never" is -- and about whether they have the property at
all.
"""
import os
import sys

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

fails = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        fails.append(msg)


try:
    from fourthplayer import video
except Exception as exc:
    print("SKIPPED: cannot import the host here (%s)" % exc)
    sys.exit(0)


class Spec:
    def __init__(self, minimum, maximum):
        self.minimum, self.maximum = minimum, maximum


class FakeElement:
    def __init__(self, props):
        self.props = props

    def find_property(self, name):
        return self.props.get(name)


made = {}


class FakeFactory:
    @staticmethod
    def make(element, name=None):
        return made.get(element)


real = video.Gst.ElementFactory
video.Gst.ElementFactory = FakeFactory

NVENC = "{el} name=enc bitrate={kbps} gop-size={keyint} zerolatency=true"
VA = "{el} name=enc bitrate={kbps} key-int-max={keyint} cpb-size={cpb}"
NONE = "{el} name=enc bitrate={kbps}"

try:
    print("an encoder that can be told never, is")
    made["nvd3d11h265enc"] = FakeElement({"gop-size": Spec(-1, 2147483647)})
    check(video.keyframe_gap("nvd3d11h265enc", NVENC, 144) == -1,
          "NVENC, whose gop-size goes down to -1, is asked for no periodic one")

    print("and one that cannot is given a long interval, not a forbidden value")
    made["vah265enc"] = FakeElement({"key-int-max": Spec(0, 1024)})
    gap = video.keyframe_gap("vah265enc", VA, 144)
    check(gap == 1024,
          "a cap of 1024 is respected rather than overrun (got %r)" % gap)
    check(gap != -1, "and -1 is not handed to something that would refuse it")

    made["vah264enc"] = FakeElement({"key-int-max": Spec(0, 2147483647)})
    check(video.keyframe_gap("vah264enc", VA, 30)
          == 30 * video.KEYFRAME_FALLBACK_SECONDS,
          "with room to spare it is the fallback interval, not two seconds")

    print("an encoder with no such property at all is not asked about one")
    made["mfh264enc"] = FakeElement({})
    check(video.keyframe_gap("mfh264enc", NONE, 60)
          == 60 * video.KEYFRAME_FALLBACK_SECONDS,
          "a template with no keyframe property still returns a sane number")

    print("an element that will not even be made does not raise")
    check(video.keyframe_gap("notreal", NVENC, 60)
          == 60 * video.KEYFRAME_FALLBACK_SECONDS,
          "a missing element falls back rather than throwing into pipeline setup")

    print("and what somebody configured is still what they get")
    check(video.keyframe_gap("nvd3d11h265enc", NVENC, 144, wanted=90) == 90,
          "keyframe_interval in the config wins over all of this")

    print("the fallback is long enough to stop being a beat")
    check(video.KEYFRAME_FALLBACK_SECONDS >= 5,
          "%ds between unrequested keyframes" % video.KEYFRAME_FALLBACK_SECONDS)
finally:
    video.Gst.ElementFactory = real

print("FAILED" if fails else "PASSED")
sys.exit(1 if fails else 0)
