"""fourth-player -- browser guests join a game on this machine with real pads.

Four modules do the work and are deliberately separable, because three of them
can be tested without a GPU, a network or a browser:

    protocol   the wire format for one pad's state, and nothing else
    pads       kernel-level virtual gamepads, one per guest
    invites    who is allowed in, for how long, and how that is revoked
    video      the one GStreamer pipeline everybody watches

`session` ties them together and `signalling` is the socket they arrive on.
"""

__version__ = "0.1.0"
