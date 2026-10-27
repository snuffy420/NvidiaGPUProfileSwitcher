# Contact page

The README's contact card links to:

https://snuffy420.github.io/NvidiaGPUProfileSwitcher/docs/contact.html

The page is a static GitHub Pages file. It uses the same existing Formspree
endpoint as the OpenLUFS contact form: `https://formspree.io/f/myeekrpy`.
Messages are identified with the subject `GPU Driver Profile Switcher contact`.
No endpoint or receiving account was changed.

Upload `docs/contact.html`, `assets/app-icon.png`, `assets/contact-button.svg`,
and the root `.nojekyll` file along with the repository contents. In GitHub
Settings → Pages, serve the repository's main branch from `/ (root)` if Pages
is not already configured. An existing Pages workflow can keep its current
configuration if it publishes the same paths.

The form validates email and message content, includes a honeypot, avoids
duplicate submissions, times out stalled requests, and keeps message text when
sending fails. Testing uses intercepted responses; no test message is sent to
the real endpoint. The endpoint must remain active in the owner's Formspree
account for delivery.
