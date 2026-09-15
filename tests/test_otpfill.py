"""Letting a password manager fill the authenticator code.

Reported as: on macOS the authenticator code always has to be typed by hand,
where iOS offers it from Passwords.

Both platforms were given the same field -- autocomplete="one-time-code",
numeric inputmode, a six-digit pattern -- which is the whole recipe, and is
why iOS worked. What differed is the form around it:

  Both login forms carried autocomplete="off". macOS Safari honours a
  form-level "off" far more strictly than iOS, which largely ignores it for
  passwords and codes. So the same saved entry filled itself on a phone and
  was suppressed on a Mac.

  And the code-only form -- the one shown when something needs a fresh code
  from somebody already signed in -- had no account name in it. A password
  manager knows the codes; it cannot tell whose form this is without one.
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)

fails = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        fails.append(msg)


html = open(os.path.join(ROOT, "web", "index.html")).read()
js = open(os.path.join(ROOT, "web", "app.js")).read()
css = open(os.path.join(ROOT, "web", "style.css")).read()


def form(identifier):
    start = html.index('id="%s"' % identifier)
    # Back to the opening <form, forward to its </form>.
    return html[html.rindex("<form", 0, start):html.index("</form>", start)]


print("the code fields still say what they are")
for field in ("login-code", "login-again-code"):
    block = html[html.index('id="%s"' % field):][:400]
    check('autocomplete="one-time-code"' in block,
          "%s asks for a one-time code" % field)
    check('inputmode="numeric"' in block, "%s is numeric" % field)

print()
print("and nothing around them tells the browser not to bother")
# This is the bit that differed. iOS fills these anyway; macOS does not.
for name in ("login-form", "login-again"):
    block = form(name)
    opening = block[:block.index(">") + 1]
    check('autocomplete="off"' not in opening,
          "%s does not carry a form-level autocomplete=off, which is what "
          "suppressed the offer on macOS while iOS ignored it" % name)

print()
print("the code-only form says whose code it is")
# A manager holding a verification code still has to match it to an account,
# and this form has no account name of its own -- the person is already
# signed in, which is the whole reason it is shown.
again = form("login-again")
check('autocomplete="username"' in again,
      "there is a username field for the manager to match on")
check('readonly' in again, "it is not something to type into")
check('tabindex="-1"' in again, "nor to tab through")
check('aria-hidden="true"' in again, "nor to read out")
check('class="offscreen"' in again,
      "and it is moved off-screen rather than hidden: a manager skips a field "
      "it considers invisible, which would put this back where it started")

print()
print("off-screen means off-screen, not display:none")
rule = css[css.index(".offscreen {"):css.index("}", css.index(".offscreen {"))]
check("display: none" not in rule and "visibility: hidden" not in rule,
      "the rule does not use either of the two things that would undo it")
check("position: absolute" in rule and "left: -9999px" in rule,
      "it is moved instead")

print()
print("and it is filled in with the account actually signed in")
check('who.value = (account && account.name)' in js,
      "the field is set from the signed-in account")
check(js.index('who.value = (account && account.name)')
      < js.index('const field = el("login-again-code")'),
      "before the code field is focused, so the manager sees a complete form")

print("\nFAILURES: %d" % len(fails))
sys.exit(1 if fails else 0)
