"""The invite: two factors, a deadline, and taking it all back.

Time is a parameter everywhere in `invites`, so a ten-minute lockout and an
eight-hour session are both tested in microseconds.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from fourthplayer import invites as I

fails = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        fails.append(msg)


def fresh(slots=3, duration=3600.0, now=0.0):
    s = I.Session(slots=slots, duration=duration, now=now)
    return s, *s.clear_invite


print("the invite itself")
s, tok, pin = fresh()
check(len(pin) == I.PIN_DIGITS and pin.isdigit(), "the PIN is six digits, got %r" % pin)
check(len(tok) >= 40, "the token is long, got %d chars" % len(tok))
check(s.token_digest != tok.encode(), "only a digest of the token is stored")
check(s.pin_digest != pin.encode(), "only a digest of the PIN is stored")

print("\nboth factors are required")
s, tok, pin = fresh()
try:
    s.join(tok, "000000" if pin != "000000" else "111111", now=1, address="a")
    check(False, "a wrong PIN was accepted")
except I.BadPin:
    check(True, "a wrong PIN is refused")
s, tok, pin = fresh()
try:
    s.join(I.new_token(), pin, now=1, address="a")
    check(False, "a wrong token was accepted")
except I.BadPin:
    check(True, "a wrong token is refused")

print("\nliveness of a link is answerable without the PIN")
s, tok, pin = fresh()
check(s.token_valid(tok, now=1), "a live link reads as live")
check(not s.token_valid(I.new_token(), now=1), "someone else's link does not")
check(not s.token_valid(tok, now=4000), "an expired link reads as dead")

print("\nslots are handed out in order and run out")
s, tok, pin = fresh(slots=2)
slot_a, ga = s.join(tok, pin, now=1, address="a")
slot_b, gb = s.join(tok, pin, now=2, address="b")
check((slot_a, slot_b) == (0, 1), "slots go in order, got %r" % ((slot_a, slot_b),))
check(ga != gb, "each guest gets its own token")
try:
    s.join(tok, pin, now=3, address="c")
    check(False, "a third guest got into a two-slot session")
except I.SessionFull:
    check(True, "a full session refuses the next guest")

print("\na guest reconnects without spending the PIN again")
check(s.guest_for(ga, now=4).slot == 0, "a known guest resolves to their slot")
try:
    s.guest_for(I.new_token(), now=4)
    check(False, "a made-up guest token resolved")
except I.UnknownGuest:
    check(True, "a made-up guest token does not resolve")

print("\nkicking frees the slot and burns the way back in")
check(s.kick(0) is True, "kicking a present guest reports success")
check(s.free_slot() == 0, "the slot is free again")
try:
    s.guest_for(ga, now=5)
    check(False, "a kicked guest still resolved")
except I.UnknownGuest:
    check(True, "a kicked guest cannot resolve")
slot_c, gc = s.join(tok, pin, now=6, address="c")
check(slot_c == 0, "somebody else can take the freed slot")
check(s.kick(0) and not s.kick(0), "kicking twice is not an error the second time")

print("\nreleasing a slot is not the same as kicking somebody out")
s, tok, pin = fresh(slots=2)
slot_a, ga = s.join(tok, pin, now=1, address="a")
s.join(tok, pin, now=1, address="b")
check(s.free_slot() is None, "both slots are taken")
check(s.release(slot_a) is True, "releasing reports success")
check(s.free_slot() == slot_a, "and the slot is genuinely free again")
check(s.release(slot_a) is False, "releasing an empty slot changes nothing")
slot_again, _ = s.join(tok, pin, now=2, address="a")
check(slot_again == slot_a, "somebody can take the freed slot")
# Their old guest token stops resolving -- the record is gone -- so a released
# guest comes back with the PIN. That is the difference from a kick, which also
# burns the credential so the link itself stops working for them.
try:
    s.guest_for(ga, now=2)
    check(False, "a released guest's old token still resolved")
except I.UnknownGuest:
    check(True, "a released guest rejoins with the PIN, not the old token")

print("\na guest who left comes back on their token alone")
s, tok, pin = fresh(slots=2)
slot_a, ga = s.join(tok, pin, now=1, address="a")
s.release(slot_a, now=10)
check(s.free_slot() == slot_a, "their slot is free for anybody straight away")
check(s.reclaim(ga, now=11) == slot_a,
      "and they get it back on the token, with no PIN")
check(s.free_slot() != slot_a, "which takes the slot again")

print("\nthe same token keeps working, so a second drop is no worse")
s.release(slot_a, now=20)
check(s.reclaim(ga, now=21) == slot_a, "reclaimed twice with the one token")

print("\nif somebody else took the slot, they get another")
s, tok, pin = fresh(slots=2)
slot_a, ga = s.join(tok, pin, now=1, address="a")
s.release(slot_a, now=5)
other, _ = s.join(tok, pin, now=6, address="b")
check(other == slot_a, "the freed slot went to whoever asked first")
check(s.reclaim(ga, now=7) != slot_a,
      "and the returning guest is given a different one rather than evicting them")

print("\nand is told honestly when there is no room")
s, tok, pin = fresh(slots=1)
slot_a, ga = s.join(tok, pin, now=1, address="a")
s.release(slot_a, now=5)
s.join(tok, pin, now=6, address="b")
try:
    s.reclaim(ga, now=7)
    check(False, "a full session let somebody reclaim anyway")
except I.SessionFull:
    check(True, "a full session refuses a reclaim rather than evicting")

print("\na claim does not outlive the session or its welcome")
s, tok, pin = fresh(slots=2)
slot_a, ga = s.join(tok, pin, now=1, address="a")
s.release(slot_a, now=5)
try:
    s.reclaim(ga, now=5 + I.CLAIM_SECONDS + 1)
    check(False, "an ancient claim was honoured")
except I.UnknownGuest:
    check(True, "a claim expires after %.0f minutes" % (I.CLAIM_SECONDS / 60))

print("\nand a kicked guest cannot reclaim their way back")
s, tok, pin = fresh(slots=2)
slot_a, ga = s.join(tok, pin, now=1, address="a")
s.kick(slot_a)
try:
    s.reclaim(ga, now=2)
    check(False, "a kicked guest reclaimed a slot")
except I.UnknownGuest:
    check(True, "kicking still means kicked")

print("\na token nobody minted reclaims nothing")
s, tok, pin = fresh(slots=2)
try:
    s.reclaim(I.new_token(), now=2)
    check(False, "an invented token was accepted")
except I.UnknownGuest:
    check(True, "an invented token is refused")

print("\nten wrong PINs destroy the invite outright")
s, tok, pin = fresh()
wrong = "000000" if pin != "000000" else "111111"
for i in range(I.MAX_PIN_ATTEMPTS):
    try:
        # A fresh address each time, so the per-address lockout does not mask
        # the attempt counter this is actually testing.
        s.join(tok, wrong, now=1 + i, address="addr%d" % i)
    except I.JoinError:
        pass
check(s.destroyed, "the invite is destroyed after %d attempts" % I.MAX_PIN_ATTEMPTS)
try:
    s.join(tok, pin, now=99, address="z")
    check(False, "the correct PIN still worked after destruction")
except I.InviteDestroyed:
    check(True, "even the correct PIN is refused afterwards")

print("\nper-address lockout escalates")
s, tok, pin = fresh()
wrong = "000000" if pin != "000000" else "111111"
for i in range(I.LOCKOUT_AFTER):
    try:
        s.join(tok, wrong, now=1, address="same")
    except I.JoinError:
        pass
try:
    s.join(tok, pin, now=1, address="same")
    check(False, "a locked-out address was served")
except I.LockedOut as ex:
    check(abs(ex.seconds - I.LOCKOUT_STEPS[0]) < 1e-6,
          "first lockout is %ss, got %s" % (I.LOCKOUT_STEPS[0], ex.seconds))
check(s.join(tok, pin, now=1 + I.LOCKOUT_STEPS[0] + 1, address="same")[0] == 0,
      "and lifts once it has run out")

print("\na lockout does not stop a different address")
s, tok, pin = fresh()
for i in range(I.LOCKOUT_AFTER):
    try:
        s.join(tok, wrong, now=1, address="noisy")
    except I.JoinError:
        pass
check(s.join(tok, pin, now=1, address="quiet")[0] == 0,
      "an innocent address is unaffected by someone else's lockout")

print("\nthe deadline")
s, tok, pin = fresh(duration=10.0)
check(s.remaining(now=4) == 6.0, "remaining counts down, got %r" % s.remaining(now=4))
check(s.alive(now=9.9), "alive just before the deadline")
check(not s.alive(now=10.0), "not alive at the deadline")
slot, g = s.join(tok, pin, now=1, address="a")
try:
    s.guest_for(g, now=11)
    check(False, "a guest outlived the session")
except I.InviteExpired:
    check(True, "an established guest is cut at the deadline too")

print("\nthe clear pair is memory-only")
s, tok, pin = fresh()
s.forget_clear()
check(s.clear_invite is None, "after a restart the owner cannot re-read it")
check(s.token_valid(tok, now=1), "but the invite itself still works")

print("\nFAILURES: %d" % len(fails))
sys.exit(1 if fails else 0)
