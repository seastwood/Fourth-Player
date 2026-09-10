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

print("\na VA encoder is used with or without vapostproc")
# Skylake registers vah264lpenc and no vapostproc, and requiring the pair sent
# an HD 530 to x264enc while its own encoder went unused.
import fourthplayer.video as _v
find = _v.Gst.ElementFactory.find
have_pp = find("vapostproc") is not None
for codec in ("h264", "h265"):
    got = video.pick_encoder(codec)
    if got is None or not got[0].startswith("va"):
        continue
    check(got[2] is (_v._VA if have_pp else _v._SW),
          "%s feeds %s from %s memory (vapostproc %s)"
          % (got[0], codec, "card" if have_pp else "system",
             "here" if have_pp else "missing"))
va_h264 = find("vah264enc") or find("vah264lpenc")
if va_h264:
    got = video.pick_encoder("h264")
    check(got is not None and got[1] == "hardware",
          "a machine with a VA H.264 encoder uses it, not x264enc (got %s)"
          % (got[0] if got else None))

print("\nH.265 is only offered when the card can encode it")
# The case this exists for: an Intel HD 530 encodes H.264 in hardware and
# H.265 not at all, so the only H.265 encoder present is x265enc. Offering
# H.265 there picks the better codec and the far worse session -- 1080p at a
# load average of fifty-five, against hardware H.264 that barely registers.
h265 = video.pick_encoder("h265")
offered265 = "h265" in video.host_codecs()
if h265 is None:
    check(not offered265, "nothing here encodes H.265, and none is offered")
elif h265[1] == "software":
    check(not offered265,
          "only %s encodes H.265 here, so H.265 is not offered" % h265[0])
else:
    check(offered265, "%s encodes H.265 in hardware, so it is offered"
          % h265[0])
check("h265" not in video.host_codecs() or video.pick_encoder("h265")[1] == "hardware",
      "put the other way round: H.265 offered implies H.265 in hardware")
check("h264" in video.host_codecs(),
      "and H.264 is always offered, so refusing H.265 never leaves a host "
      "with nothing to send")

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
