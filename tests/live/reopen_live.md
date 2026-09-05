# Closing the app and opening it again

`tests/browser/reopen.mjs` drives a real Chrome against a *running* host: it
joins with the link and PIN, logs in with "remember this device" ticked, closes
the page, opens it again, and checks the account came back.

It is not in `run.sh` -- it needs an open session, an account, and it uses the
network -- but it is the only test that covers the path a person actually
takes, and it is the path that broke twice. The device token was added to the
two reconnect paths and not to the one on the load path, which is the only one
that runs when somebody closes the app and opens it again.

`tests/test_device.py` is the cheap half of the same guard: it reads app.js and
refuses to let any connect() call site carry its own token, so the funnel stays
the only place that attaches it. Run that one always; run this one after
touching the join, resume or login paths.

    fourth-player admin add probe
    fourth-player admin can probe steam lock
    fourth-player reshare                       # for a link and PIN

    cd tests/browser && npm install puppeteer-core
    LINK="https://<box>:8443/j/<token>" PIN=<pin> SECRET=<totp-secret> \
      node reopen.mjs

    fourth-player admin remove probe

The password is hard-coded as `throwaway-probe-pw`; make the probe account with
that, and delete it afterwards.
