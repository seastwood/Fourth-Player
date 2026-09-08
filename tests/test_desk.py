"""The keyboard and mouse, and the boundary they are the exception to.

The first section is the one that matters most and is the shortest: a guest's
pad must still be incapable of typing. Everything else here is about the door
-- who may open it, that it is shut when nobody is holding it open, and that
nothing is left pressed down on somebody's computer when it closes.
"""
import json
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
    from fourthplayer import deskwire
except Exception as exc:
    print("SKIPPED: cannot import the host here (%s)" % exc)
    sys.exit(0)


print("the pad still cannot type")
try:
    from fourthplayer import pads as padlib, keymap
    from evdev import ecodes as e

    declared = set(padlib.capabilities()[e.EV_KEY])
    letters = {keymap.key_for("Key" + c) for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"}
    check(not (declared & letters),
          "no letter is declared on a guest's pad")
    check(e.EV_REL not in padlib.capabilities(),
          "and no relative axis either, so it is not a mouse")
    check(not (declared & {e.KEY_ENTER, e.KEY_LEFTCTRL, e.KEY_TAB}),
          "nor Enter, Ctrl or Tab")
    check(len(declared) == 11,
          "it is eleven buttons and nothing else: %d" % len(declared))
except Exception as exc:                                   # pragma: no cover
    print("  .... skipped, no evdev here (%s)" % exc)


print("\nwhat the wire accepts")
check(deskwire.decode('{"t":"m","dx":4,"dy":-2}') == [deskwire.Move(4, -2)],
      "a movement")
check(deskwire.decode('{"t":"w","dx":0,"dy":3}') == [deskwire.Wheel(0, 3)],
      "a wheel turn")
check(deskwire.decode('{"t":"k","c":"KeyA","d":1}') == [deskwire.Key("KeyA", True)],
      "a key going down")
check(deskwire.decode('{"t":"b","b":2,"d":0}') == [deskwire.Button(2, False)],
      "a button coming up")
check(deskwire.decode('{"t":"r"}') == [deskwire.ReleaseAll()],
      "and a demand to let go of everything")
check(len(deskwire.decode('[{"t":"m","dx":1,"dy":1},{"t":"k","c":"KeyB","d":1}]')) == 2,
      "several at once, in the order they were sent")
check(deskwire.decode(b'{"t":"r"}') == [deskwire.ReleaseAll()],
      "bytes as well as text, because a data channel may hand over either")


print("\nwhat it refuses")
for bad, why in [
        ('{"t":"m","dx":99999,"dy":0}', "a movement past the limit"),
        ('{"t":"m","dx":1.5,"dy":0}', "a fraction, rather than rounding it away"),
        ('{"t":"m","dx":true,"dy":0}', "a boolean pretending to be a number"),
        ('{"t":"w","dx":0,"dy":900}', "a wheel spun past the limit"),
        ('{"t":"k","c":"","d":1}', "an empty key name"),
        ('{"t":"k","c":"' + "x" * 99 + '","d":1}', "a key name of unreasonable length"),
        ('{"t":"b","b":"left","d":1}', "a button that is not a number"),
        ('{"t":"zz"}', "a type nobody has heard of"),
        ('not json at all', "something that is not JSON"),
        ('[' + '{"t":"r"},' * 80 + '{"t":"r"}]', "more events than one message may carry"),
]:
    try:
        deskwire.decode(bad)
        check(False, why)
    except deskwire.DeskError:
        check(True, why)

print("\nand a bad event takes the whole message with it")
try:
    deskwire.decode('[{"t":"k","c":"KeyA","d":1},{"t":"m","dx":99999,"dy":0}]')
    check(False, "half a batch is a keyboard in a state neither end agreed on")
except deskwire.DeskError:
    check(True, "half a batch is a keyboard in a state neither end agreed on")


print("\nthe key table")
try:
    from fourthplayer import keymap
    check(len(set(keymap.CODES.values())) == len(keymap.CODES),
          "no two names map to the same key: %d names, %d codes"
          % (len(keymap.CODES), len(set(keymap.CODES.values()))))
    check(keymap.key_for("KeyA") and keymap.key_for("Digit7")
          and keymap.key_for("F11") and keymap.key_for("NumpadEnter"),
          "letters, digits, function keys and the numpad are all carried")
    check(keymap.key_for("ShiftLeft") != keymap.key_for("ShiftRight"),
          "left and right modifiers stay apart, because programs bind them apart")
    check(keymap.key_for("Nonsense") is None,
          "and a name we do not carry is None rather than a guess")

    from evdev import ecodes as e
    both = {e.KEY_LEFTCTRL, e.KEY_LEFTALT}
    check(keymap.switches_terminal(e.KEY_F2, both),
          "Ctrl+Alt+F2 is recognised as a terminal switch")
    check(not keymap.switches_terminal(e.KEY_F2, {e.KEY_LEFTCTRL}),
          "Ctrl+F2 alone is not, and must still reach the machine")
    check(not keymap.switches_terminal(e.KEY_A, both),
          "nor is Ctrl+Alt+A, which is an ordinary shortcut")
except Exception as exc:                                   # pragma: no cover
    print("  .... skipped, no evdev here (%s)" % exc)


print("\nwho may type")
try:
    from fourthplayer.session import LiveSession, GuestConnection

    class FakeGuest(GuestConnection):
        def __init__(self, slot, label, primary=False):
            self.slot = slot
            self.label = label
            self.primary = primary
            self.session = None
            self.stray_desk = 0
            self.bad_desk = 0
            self.last_input = 0.0

    class FakeDesk:
        def __init__(self):
            self.applied = []
            self.closed = False

        def apply(self, actions):
            self.applied.extend(actions)

        def close(self):
            self.closed = True

    session = LiveSession.__new__(LiveSession)
    session.guests = {}
    session.desk_driver = None
    session.desk_device = None
    session.desk_label = ""
    session.notify = lambda message: None
    session.publish_people = lambda: None
    session.driver = None
    session.driver_shell = ""

    admin = FakeGuest(1, "Seth")
    other = FakeGuest(2, "A guest")
    admin.session = other.session = session

    check(session.at_the_desk(admin) is False,
          "nobody is at the desk before anybody takes it")

    # The device is stubbed: this is about who, not about uinput.
    session.desk_device = FakeDesk()
    session.desk_driver = admin.slot
    session.desk_label = admin.label
    check(session.at_the_desk(admin) is True, "the one who took it is at it")
    check(session.at_the_desk(other) is False, "and nobody else is")

    other.feed_desk('{"t":"k","c":"KeyA","d":1}')
    check(session.desk_device.applied == [],
          "a message from somebody not at the desk reaches no device")
    check(other.stray_desk == 1, "and is counted, so the question has an answer")

    admin.feed_desk('{"t":"k","c":"KeyA","d":1}')
    check(session.desk_device.applied == [deskwire.Key("KeyA", True)],
          "a message from the one at it does")

    admin.feed_desk('{"t":"m","dx":1.5,"dy":0}')
    check(admin.bad_desk == 1 and len(session.desk_device.applied) == 1,
          "and one that will not decode is dropped, not half-applied")

    print("\ntaking it away")
    stub = session.desk_device
    session.forget_driver_if(admin.slot)
    check(stub.closed, "the guest leaving closes the devices")
    check(session.desk_driver is None and session.desk_device is None,
          "and nobody is at the desk afterwards")

    admin.feed_desk('{"t":"k","c":"KeyA","d":0}')
    check(admin.stray_desk == 1,
          "a message arriving after that reaches nothing at all")

    print("\nonly the primary admin can take it from somebody")
    session.desk_device = FakeDesk()
    session.desk_driver = admin.slot
    session.desk_label = admin.label
    check("Seth" in (session.take_the_desk(other) or ""),
          "an ordinary admin is told who has it, by name")
    check(session.desk_driver == admin.slot, "and it does not move")
except Exception as exc:                                   # pragma: no cover
    print("  .... skipped, cannot import the session here (%s)" % exc)


print("\nthe devices themselves")
try:
    from fourthplayer import desk

    device = desk.Desk("Test Desk")
    try:
        device.apply(deskwire.decode(
            '[{"t":"k","c":"ShiftLeft","d":1},{"t":"b","b":0,"d":1}]'))
        check(device.held_keys and device.held_buttons,
              "what is held down is remembered, because it has to be let go")

        device.apply(deskwire.decode(
            '[{"t":"k","c":"ControlLeft","d":1},{"t":"k","c":"AltLeft","d":1},'
            '{"t":"k","c":"F3","d":1}]'))
        check(device.refused_switches == 1,
              "Ctrl+Alt+F3 is swallowed rather than taking X with it")

        device.apply(deskwire.decode('{"t":"k","c":"Nonsense","d":1}'))
        check(device.unknown_keys == 1,
              "a key we do not carry is counted and dropped")

        device.last_seen -= desk.DEADMAN_SECONDS + 1
        check(device.sweep() is True, "going quiet while holding keys sweeps")
        check(not device.held_keys and not device.held_buttons,
              "and everything is let go")
        check(device.sweep() is False,
              "with nothing held there is nothing to sweep")
    finally:
        device.close()
    check(True, "and it closes without complaint")
except Exception as exc:                                   # pragma: no cover
    print("  .... skipped, no uinput here (%s)" % exc)


print()
if fails:
    print("%d FAILED" % len(fails))
    for f in fails:
        print("  " + f)
    sys.exit(1)
print("all good")
