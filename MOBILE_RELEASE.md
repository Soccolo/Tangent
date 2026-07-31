# Tangent mobile release plan

Checked against Apple and Google documentation on **31 July 2026**.

## Recommendation

Validate the new loop first as the installable PWA already in this repository, then
ship a shared **Capacitor** shell for iOS and Android. Keep FastAPI and Postgres as the
hosted backend and bundle the HTML/CSS/JS in the apps.

- A PWA is the quickest way to test home-screen use, but it does not create an App
  Store listing. Apple supports installing web apps from Safari.
- A Google Trusted Web Activity is a legitimate Android-only PWA wrapper, linked to
  the site with Digital Asset Links.
- A Capacitor shell is the practical two-store route. A full native rewrite is not
  justified yet.

Apple can reject a wrapper that is merely a repackaged website under Guideline 4.2.
Give the shell native value: offline access to recent lessons, secure credential
storage, push reminders, haptics, native sharing/deep links and, later, a widget.

Sources: [Apple web apps](https://support.apple.com/en-ie/guide/iphone/iphea86e5236/ios),
[Apple App Review Guidelines](https://developer.apple.com/app-store/review/guidelines/),
[Android Trusted Web Activities](https://developer.android.com/develop/ui/views/layout/webapps/trusted-web-activities),
[Capacitor documentation](https://capacitorjs.com/docs).

## Architecture work

1. Keep the production API on an always-on HTTPS service with managed Postgres,
   backups and Alembic migrations. Do not submit while the reviewer can hit a
   free-tier cold start or lose in-flight generation.
2. Bundle the web assets in Capacitor. The current client assumes same-origin paths
   and an HttpOnly `SameSite=Lax` cookie. A bundled shell should use a mobile API
   origin and bearer tokens stored in Keychain/Keystore, or carefully designed
   CORS/cookie rules.
3. Keep screen capture desktop-only in mobile v1. A native version needs system
   capture APIs, fresh consent, a persistent visible capture state and an immediate
   stop action. See
   [Android MediaProjection](https://developer.android.com/media/grow/media-projection)
   and [Apple ReplayKit](https://developer.apple.com/documentation/ReplayKit).
4. Generate the full native icon/splash set. The current SVG is a useful source asset,
   not the final set of App Store and Play assets.
5. Use a current Capacitor release and verify Apple privacy manifests/signatures for
   every listed third-party SDK in the final archive:
   [Apple SDK requirements](https://developer.apple.com/support/third-party-SDK-requirements/).

## Launch blockers before submission

- Publish Privacy Policy, Terms of Use and support pages, linked both in-app and from
  the listings. Complete Apple App Privacy and Google Data Safety accurately,
  including work logs, profiles, avatars, progress, shared lessons and screen frames
  sent to Anthropic. Google includes ephemeral off-device processing in its form.
- The existing in-app deletion is a strong start. Google also requires a public URL
  where an uninstalled user can request deletion. Add `/delete-account` or an
  equivalent request flow. Copied lessons are already anonymized on deletion.
- Add “Report lesson / report AI output,” a moderation queue, removal workflow and
  support contact. Google requires in-app reporting for generative-AI content, and
  shared/library lessons can also trigger user-generated-content rules.
- Put a prominent disclosure immediately before screen capture: what is sampled,
  why it is sent to Anthropic, retention behavior and how to stop. A privacy-policy
  link alone is not enough.
- Create a permanent reviewer account with pre-generated lessons. Review must not
  depend on waiting for a model call, SMTP delivery or geography-specific access.

Sources: [Apple privacy details](https://developer.apple.com/app-store/app-privacy-details/),
[Google User Data policy](https://support.google.com/googleplay/android-developer/answer/10144311?hl=en),
[Google Data Safety](https://support.google.com/googleplay/android-developer/answer/10787469?hl=en),
[Apple account deletion](https://developer.apple.com/news/?id=12m75xbj),
[Google account deletion](https://support.google.com/googleplay/android-developer/answer/13327111?hl=en-EN),
[Google AI-content policy](https://support.google.com/googleplay/android-developer/answer/13985936?hl=en),
[Google UGC policy](https://support.google.com/googleplay/android-developer/answer/9876937?hl=en-IN).

## Accounts, fees and current requirements

### Apple

- Apple Developer Program: **US$99 per year** (or local equivalent). An organization
  enrollment needs a legal entity and D-U-N-S number; an individual publishes under
  their legal name.
- Since 28 April 2026, uploads must use **Xcode 26+ and the iOS 26 SDK+**.
- TestFlight allows up to 100 internal and 10,000 external testers. The first external
  build receives TestFlight App Review.
- Supply a fully functional demo account and keep the backend live during review.

Sources: [membership and fee](https://developer.apple.com/support/compare-memberships/),
[upcoming requirements](https://developer.apple.com/news/upcoming-requirements/),
[TestFlight overview](https://developer.apple.com/help/app-store-connect/test-a-beta-version/testflight-overview/).

### Google Play

- Play Console registration: **US$25 once**. Choose personal vs organization before
  enrolling; organization verification uses a D-U-N-S number.
- New personal accounts created after 13 November 2023 need a closed test with at
  least **12 opted-in testers for 14 continuous days**, then an application for
  production access.
- Upload a signed Android App Bundle and use Play App Signing.
- From **31 August 2026**, new apps and updates must target Android 16 / API 36. Build
  to API 36 now. Check all native plugins for 16 KB page-size compatibility.

Sources: [Play registration](https://support.google.com/googleplay/android-developer/answer/14659200?hl=en),
[account types](https://support.google.com/googleplay/android-developer/answer/13634885?hl=en),
[closed-test requirement](https://support.google.com/googleplay/android-developer/answer/14151465?hl=en),
[app setup and AAB](https://support.google.com/googleplay/android-developer/answer/9859152?hl=en),
[target API requirement](https://developer.android.com/google/play/requirements/target-sdk),
[16 KB pages](https://developer.android.com/guide/practices/page-sizes).

## Submission sequence

1. Stabilize the hosted backend, migrations, backups and monitoring.
2. Publish privacy, terms, support, external deletion and moderation/reporting flows.
3. Create immutable bundle IDs/package name and the Apple/Google developer accounts.
4. Build the Capacitor shell with secure mobile auth, offline recent lessons, native
   sharing/deep links, reminders and haptics.
5. Produce icons, screenshots, listing copy, reviewer notes and the seeded login.
6. Complete privacy, data-safety, age/content and EU trader declarations.
7. Test via TestFlight and Play internal/closed tracks.
8. Submit, leaving time for Google's mandatory test window if the account is personal.

Earned Tangent coins do not need store billing. If coins, hints, freezes or
subscriptions are ever sold for real money, default to StoreKit and Google Play
Billing; do not award coins for ratings or reviews.

## Product backlog after this release

1. **Tangent Constellation:** a glowing skill map with the user's role in the centre
   and learned adjacent disciplines lighting up around it.
2. **Three-minute reviews:** spaced repetition with confidence ratings and mixed
   question formats.
3. **Daily missions + weekly scenario:** tiny daily goals and one cross-discipline
   “boss” case that applies several learned tangents.
4. **Owl Workshop:** cosmetic coin sinks—owl accessories, desk objects, themes and
   celebration styles—so currency stays fun without becoming pay-to-win.
5. **Private circles:** buddy streaks and small team challenges rather than a global
   leaderboard.
