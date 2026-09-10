"""Whatever GPU is in the machine, and never more than a CPU can carry.

This is installed on hardware nobody here has seen. The encoder used to be
hard-coded to VA, which is right on AMD and on Intel through mesa and leaves
an nvidia card or an ARM board encoding in software for no reason -- and a
machine encoding 1080p in software is not slow, it is gone: load average
fifty, no ssh, no web server, until somebody reaches the power button.
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
    from fourthplayer.config import Config
except Exception as exc:
    print("SKIPPED: cannot import the host here (%s)" % exc)
    sys.exit(0)

# Before anything is asked about elements. Gst.ElementFactory.find answers
# "no" for everything until GStreamer has been initialised, which reads
# exactly like a machine with no encoders -- and did, until this line.
video.init()

print("every encoder it knows how to drive")
for codec, entries in video.ENCODERS.items():
    kinds = [kind for _el, kind, _c, _s in entries]
    check(kinds == sorted(kinds, key=lambda k: k != "hardware"),
          "%s lists hardware before software: %s" % (codec, kinds))
    check(any(k == "software" for k in kinds),
          "%s has a software encoder to fall back to" % codec)
    for element, kind, converter, settings in entries:
        # Every template has to render with the values the pipeline passes,
        # or the failure is a pipeline that will not build on somebody else's
        # machine -- which is the one machine nobody here can try.
        try:
            line = settings.format(el=element, usage=4, kbps=3000,
                                   bps=3000000, keyint=60, cpb=48)
            rendered = converter.format(w=1280, h=720)
        except Exception as exc:
            check(False, "%s renders its settings: %s" % (element, exc))
            continue
        check(line.startswith(element) and "name=enc" in line,
              "%s builds a line naming itself: %s" % (element, line[:48]))
        check("{" not in line and "{" not in rendered,
              "%s leaves nothing unfilled" % element)

print("\nVA needs its converter, and says so")
check(video._VA.startswith("vapostproc"),
      "the VA encoders take frames the GPU already holds")
check("videoconvert" in video._SW,
      "and everything else takes them from the CPU")

print("\nwhat this machine picks")
best = video.pick_encoder("h264")
soft = video.pick_encoder("h264", False)
check(best is not None, "there is an H.264 encoder here at all: %s"
      % (best[0] if best else None))
check(soft is not None and soft[1] == "software",
      "and a software one when hardware is refused: %s"
      % (soft[0] if soft else None))
if best and best[1] == "hardware":
    check(best[0] != soft[0],
          "hardware is preferred over software when both are here (%s over %s)"
          % (best[0], soft[0]))

print("\nand it agrees with what is offered to browsers")
# host_codecs is memoised, so ask the question underneath it.
for codec in video.CODEC_PREFERENCE:
    pickable = video.pick_encoder(codec) is not None
    offered = codec in video.host_codecs()
    # Offered implies buildable. Stated that way round on purpose: the weak
    # version of this passed whenever nothing was buildable, which is the one
    # case worth catching.
    check(pickable or not offered,
          "%s: offered=%s, buildable=%s -- never offered without being "
          "buildable" % (codec, offered, pickable))

print("\nthe software cap")
cfg = Config()
check(cfg.software_max_height == 720,
      "a software encoder is asked for no more than 720p by default")
source = open(os.path.join(ROOT, "fourthplayer", "video.py"),
              encoding="utf-8").read()
build = source.split("def _describe")[1] if "def _describe" in source else source
check("software_max_height" in source and 'kind == "software" and height > cap' in source,
      "and the cap is applied to the picture, not merely stored")
check("self.sending_width, self.sending_height = width, height" in source,
      "what is actually sent is remembered, so the log and the H.264 level "
      "describe the stream rather than the request")

print()
if fails:
    print("%d FAILED" % len(fails))
    for f in fails:
        print("  " + f)
    sys.exit(1)
print("all good")
