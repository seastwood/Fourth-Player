"""Tell RetroArch what our pads are, so the buttons come out where expected.

RetroArch identifies a controller by name, or failing that by vendor and
product, and looks up an autoconfig profile. Our pads answer 045e:028e -- an
Xbox 360 pad -- but no profile on a stock install carries that product id, and
nothing is named "Fourth Player". So no profile matched, RetroArch fell back to
guessing, and the confirm and back buttons landed somewhere arbitrary: guests
found A and B behaving like Start and Back in the player picker.

Writing a profile per pad fixes it deterministically, and it is also what makes
kodi-retrobox's picker show sensible button labels, because that reads the same
files (`ra_players.py`, `pad_controls`).

Indices are the ones RetroArch's udev driver assigns: buttons numbered in
ascending evdev code order over the codes we declare, axes likewise, and the
D-pad as hat 0. They are the same numbers a real Xbox pad's profile uses,
because we declare the same capability set.
"""

import logging
import os

log = logging.getLogger("fourthplayer.retroarch")

AUTOCONFIG_DIRS = (
    os.path.expanduser("~/.config/retroarch/autoconfig/udev"),
    os.path.expanduser("~/.config/retroarch/autoconfig"),
)

# 045e:028e in decimal, which is the form autoconfig files use.
VENDOR_ID, PRODUCT_ID = 1118, 654

TEMPLATE = """# Written by fourth-player. Regenerated whenever a session opens.
# One of these exists per remote pad so RetroArch does not have to guess.
input_driver = "udev"
input_device = "{name}"
input_device_display_name = "{display}"
input_vendor_id = "{vendor}"
input_product_id = "{product}"

input_b_btn = "0"
input_a_btn = "1"
input_y_btn = "2"
input_x_btn = "3"
input_l_btn = "4"
input_r_btn = "5"
input_select_btn = "6"
input_start_btn = "7"
input_menu_toggle_btn = "8"
input_l3_btn = "9"
input_r3_btn = "10"
input_l2_axis = "+2"
input_r2_axis = "+5"
input_up_btn = "h0up"
input_down_btn = "h0down"
input_left_btn = "h0left"
input_right_btn = "h0right"
input_l_x_plus_axis = "+0"
input_l_x_minus_axis = "-0"
input_l_y_plus_axis = "+1"
input_l_y_minus_axis = "-1"
input_r_x_plus_axis = "+3"
input_r_x_minus_axis = "-3"
input_r_y_plus_axis = "+4"
input_r_y_minus_axis = "-4"

# The labels matter as much as the numbers. kodi-retrobox's picker decides
# which button confirms from `input_a_btn_label`: anything other than "a" means
# an Xbox-style layout, where the bottom button is `input_b_btn`. Label these
# the way an Xbox pad does and the bottom button confirms, as a guest expects.
input_b_btn_label = "A"
input_a_btn_label = "B"
input_y_btn_label = "X"
input_x_btn_label = "Y"
input_l_btn_label = "LB"
input_r_btn_label = "RB"
input_l2_axis_label = "LT"
input_r2_axis_label = "RT"
input_select_btn_label = "Back"
input_start_btn_label = "Start"
input_menu_toggle_btn_label = "Guide"
input_l3_btn_label = "Left Thumb"
input_r3_btn_label = "Right Thumb"
input_up_btn_label = "D-Pad Up"
input_down_btn_label = "D-Pad Down"
input_left_btn_label = "D-Pad Left"
input_right_btn_label = "D-Pad Right"
"""


def profile_dir():
    for candidate in AUTOCONFIG_DIRS:
        if os.path.isdir(candidate):
            return candidate
    return None


def write_profiles(names, display=None):
    """Write one profile per pad name. Returns the paths written."""
    directory = profile_dir()
    if directory is None:
        log.info("no RetroArch autoconfig directory found; skipping profiles")
        return []
    written = []
    for index, name in enumerate(names, start=1):
        path = os.path.join(directory, f"{name}.cfg")
        body = TEMPLATE.format(name=name, vendor=VENDOR_ID, product=PRODUCT_ID,
                               display=display or f"Remote player {index}")
        try:
            with open(path, "w") as handle:
                handle.write(body)
            written.append(path)
        except OSError as exc:
            log.warning("could not write %s: %s", path, exc)
    if written:
        log.info("wrote %d RetroArch profile(s) to %s", len(written), directory)
    return written
