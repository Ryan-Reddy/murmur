# Putting Murmur on the Microsoft Store

The reason to bother: **Microsoft signs the package.** That is the whole
SmartScreen problem gone — no "Windows protected your PC", no *More info →
Run anyway*, no certificate of your own at roughly €300 a year that would not
buy instant reputation anyway. Someone installs it the same way they install
anything else, and your name is on it.

```powershell
winget install Microsoft.WindowsSDK.10.0.22621   # once
copy packaging\identity.example.json packaging\identity.json
# fill it in from Partner Center, then:
.\package.ps1                 # -> dist\Murmur.msix, ready to upload
.\package.ps1 -SelfSign       # signed with a test cert, installable here
```

## 1. The developer account

[partner.microsoft.com/dashboard](https://partner.microsoft.com/dashboard) →
register as an **individual**. Not a company: an individual account puts your
own name on the listing, which is the point. There is a one-time registration
fee — check the current figure at signup rather than trusting a number written
down here, because Microsoft has changed it more than once.

Verification for an individual is identity, not company documents, and takes
days rather than weeks.

## 2. Reserve the name

Create the app, reserve **Murmur**. If it is taken, the reserved name is what
the Store displays, so pick the fallback you would actually want to see on a
listing — `Murmur Reader`, not `Murmur2`.

Reserving gives you the two identity strings. Put them in
`packaging\identity.json`:

| from Partner Center | into identity.json |
|---|---|
| Package/Identity/Name | `name` |
| Package/Identity/Publisher (the `CN=…`) | `publisher` |

Your *display* name is already in the manifest as `PublisherDisplayName` and
says **Ryan Reddy**. That's the line under the app.

## 3. Build and upload

`.\package.ps1` produces `dist\Murmur.msix`. Upload it unsigned — the signing
happens on Microsoft's side, and a package you signed yourself is rejected.

Test it locally first with `-SelfSign`; the script prints the three commands
that trust the test certificate and install the package. Uninstall it before
submitting so you are not testing against a stale install.

## 4. The two things certification will ask about

**`runFullTrust` is a restricted capability.** Murmur is a PyInstaller desktop
app, not a UWP one, and full trust is what lets it run at all. Justification —
say something close to this:

> Murmur is a desktop application packaged with the Desktop Bridge. It needs
> full trust to register system-wide hotkeys, read the current selection via
> the clipboard, and run the ONNX speech model locally. It installs no service
> and no driver, and makes no outbound network connections.

That is true, and worth keeping true: the manifest deliberately does **not**
declare `internetClient`. Murmur never opens a socket outward — the model is
on disk and the text-in port is bound to `127.0.0.1`.

**The licence.** Murmur is GPL-3.0, which conflicts with the Store's Standard
Application License Terms. The Store has a supported way through this: in the
listing, choose **custom licence terms** and point at the GPL. This is exactly
how VLC ships on the Store, so it is a well-trodden path, not an argument you
have to win.

## 5. The listing — the part that is actually your name getting out

The package is engineering; the listing is the shopfront. Things that matter
more than they sound:

- **Screenshots.** At least one, 1366×768 or larger. The pill mid-read with a
  word lit says what this is faster than any sentence. `assets/store/` has the
  tiles; the screenshots you take yourself.
- **Short description** (one line, shown in search results). Something like:
  *"Select text anywhere in Windows and hear it read aloud, entirely on your
  own machine."*
- **The offline angle is the differentiator.** Every other reader on there
  wants an account and a connection. Say "no account, no network, nothing
  leaves your machine" high up, because that is the reason someone picks this
  one.
- **Link out.** The listing takes a website and a support contact. Point them
  at [reddy.world](https://reddy.world) and the GitHub repo — that is how a
  Store listing turns into people knowing who made it, rather than a download
  that ends there.
- **Category:** Productivity. **Age rating:** the questionnaire takes a
  minute; Murmur has no user content, no ads and no data collection, so it
  comes out as everyone.

## 6. What to expect

Certification takes a few days for a first submission and usually less after
that. A rejection is normally one specific thing with a pointer to the policy;
fix it and resubmit rather than starting over.

The package is ~500 MB because the Kokoro model is 338 MB of it. That is well
inside the Store's limits. The alternative — a small download that fetches the
model on first run — would break the one promise the app is making, so it
stays in the package.

## Updating

Bump `version` in `packaging\identity.json` (the Store refuses anything not
higher than the last upload, and the fourth number must stay `0`), run
`.\package.ps1`, upload. Updates go out to everyone who installed it, which is
the other thing the Store buys you.
