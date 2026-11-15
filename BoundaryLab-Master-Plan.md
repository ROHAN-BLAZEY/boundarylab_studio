# BoundaryLab Studio — Master Product and Deployment Checklist

**Status:** Planning only. No application code changes are included in this document.

## 1. Product editions and client experience

- [ ] Define the target user group for the first release: students, individual creators, teams, or another audience.
- [ ] Offer two clearly described editions:
  - [ ] **Browser edition:** users open an HTTPS URL; browser compatible processing runs on their device.
  - [ ] **Desktop + phone edition:** users install the laptop app and pair a phone using a QR code on the same Wi-Fi network.
- [ ] Explain in plain language where processing runs, where files are stored, and what information leaves the device.
- [ ] Document which features are available in each edition and label unavailable features clearly.
- [ ] Provide a first run walkthrough: open a sample, adjust controls, inspect stages, capture an image, and export it.
- [ ] Include a sample project or sample image so users can learn without risking their own images.
- [ ] Add a quick start guide and a short setup video for clients.

## 2. Architecture and maintainability

- [ ] Keep image processing, settings, camera streaming, gallery/storage, routes, UI behavior, and diagnostics in clearly separated modules.
- [ ] Preserve existing behavior while refactoring; use the current app as the reference for expected workflows.
- [ ] Define a shared, validated processing settings model for phone and laptop controls.
- [ ] Keep interface code, backend behavior, and storage responsibilities clearly documented.
- [ ] Establish dependency update and compatibility maintenance practices.

## 3. Stream reliability and performance

- [ ] Reconnect cleanly after temporary WebSocket interruptions, using bounded retry delays.
- [ ] Report camera permission, HTTPS, WebSocket, network, and processing errors in plain language.
- [ ] Keep frame queues bounded; discard stale frames rather than accumulating delay.
- [ ] Resize frames for previews and avoid unnecessary full resolution processing.
- [ ] Send all processing stage previews less often than the primary preview.
- [ ] Avoid image processing when no client is viewing the relevant preview.
- [ ] Display clear connecting, loading, stale frame, disconnected, and retrying states.
- [ ] Track camera connection state, recent frame activity, dropped frames, and processing latency.
- [ ] Clean up subscriptions, queues, workers, and background tasks when clients disconnect.
- [ ] Set practical performance targets for supported devices, frame rate, and preview latency.
- [ ] Handle large images and slower hardware gracefully; provide size warnings and adjustable preview quality.

## 4. Image processing stages and controls

- [ ] Map each returned stage image to the correct named stage.
- [ ] Request all stages or a selected stage as appropriate to the current UI selection.
- [ ] Add useful loading and error states when stage previews are unavailable.
- [ ] Preserve double click stage enlargement and reliable close behavior.
- [ ] Keep stage image download and share actions working.
- [ ] Keep phone and laptop processing controls synchronized and previews consistent.
- [ ] Make image upload, rotation, noise cleanup, filter, threshold, neon, and output style controls responsive.
- [ ] Support sensible preview quality controls for constrained devices.

## 5. Gallery, storage, backup, and recovery

- [ ] Preserve saved images and gallery records across application updates.
- [ ] Rebuild missing gallery index metadata from existing image files where safe.
- [ ] Keep recovered gallery data scoped to the correct account.
- [ ] Retain gallery open, edit, delete, share, and download functions.
- [ ] Show users where their gallery files are stored and how much space they use.
- [ ] Warn users before storage becomes low.
- [ ] Let users select or confirm a gallery storage folder where appropriate.
- [ ] Provide gallery backup export and restore without overwriting existing captures unexpectedly.
- [ ] Autosave settings and recover gracefully after an application or browser crash.
- [ ] Document whether uninstalling preserves gallery data.
- [ ] Offer a separate, deliberate option to erase local data.
- [ ] Provide an exit path to export images, settings, and account information before uninstalling or discontinuing the product.

## 6. Phone and mobile experience

- [ ] Show color source, grayscale, and processed preview together on phone layouts.
- [ ] Make controls readable and usable on small screens.
- [ ] Explain camera permissions and secure HTTPS requirements before camera use.
- [ ] Show whether the phone is connected and whether frames are arriving.
- [ ] For desktop + phone edition, show a laptop QR code and clear connection status.
- [ ] Explain same Wi-Fi requirements, firewall prompts, and HTTPS requirements for pairing.
- [ ] Explain the browser edition's limitations around direct phone-to-laptop streaming.

## 7. Privacy, security, and account handling

- [ ] Keep secrets, local databases, gallery files, and certificates out of source control.
- [ ] Keep all account and gallery access checks scoped to the signed in user.
- [ ] Ensure diagnostics never include passwords, session secrets, image contents, or private filesystem paths.
- [ ] Provide a clear privacy notice describing local image processing and any collected account information.
- [ ] Obtain consent for any personal information collection.
- [ ] State whether image data or account information is transmitted, and to which service.
- [ ] Provide a support diagnostic export that excludes images, credentials, and secrets.
- [ ] Document incident response steps for broken releases, data loss reports, or exposed credentials.

## 8. Deployment and packaging

### Desktop + phone edition

- [ ] Package the app as a signed Windows installer.
- [ ] Ensure clients do not need to install Python, Docker, or use a terminal.
- [ ] Add a first run setup screen for account creation, data folder selection, and permissions.
- [ ] Include a **Test setup** action for storage, camera, and network access.
- [ ] Launch the desktop dashboard automatically after installation or provide a clear app shortcut.
- [ ] Publish versioned installers through GitHub Releases with release notes and integrity checks.
- [ ] Provide a rollback path to the previous working release.

### Browser edition

- [ ] Host the static browser compatible app at an HTTPS URL.
- [ ] Keep image processing in the browser when images should remain on the user's device.
- [ ] Document supported browsers and which functions require HTTPS or browser permissions.
- [ ] Connect the static site repository to the chosen host for repeatable deployments.
- [ ] Confirm deployment settings and URL paths for the browser app.

## 9. Release, updates, and support operations

- [ ] Define supported Windows versions, browsers, Android versions, phone models, and network configurations.
- [ ] Publish minimum device requirements and known limitations.
- [ ] Show the installed version and provide a clear update path.
- [ ] Provide release notes, installer integrity information, and rollback instructions.
- [ ] Define who handles releases, updates, user support, and incoming feedback.
- [ ] Provide a way to report issues without automatically attaching images or private diagnostics.
- [ ] Define maintenance responsibilities if the primary maintainer is unavailable.
- [ ] Establish an end of support and product discontinuation process.
- [ ] Explain how users can export their data if the service or product ends.

## 10. Accessibility, localization, licensing, and pricing

- [ ] Check keyboard navigation and visible focus states throughout the interface.
- [ ] Check screen reader labels, semantic structure, and accessible error messages.
- [ ] Check readable contrast and support for reduced motion preferences.
- [ ] Decide whether languages beyond English are needed and plan localization accordingly.
- [ ] Document ownership of exported images and permitted app usage.
- [ ] Track third party library licenses and include required notices.
- [ ] Clarify which edition is free or paid and what each license includes.
- [ ] Document renewal, cancellation, refund, and offline usage rules for paid packages.

## 11. Testing and acceptance criteria

- [ ] Define measurable acceptance criteria for “ready to release.”
- [ ] Verify clean installation and first launch on a clean Windows account.
- [ ] Verify phone pairing and camera permissions on supported devices.
- [ ] Verify reconnection after network interruption and browser refresh.
- [ ] Verify settings synchronization and output consistency across phone and laptop.
- [ ] Verify image processing stages, enlargement, download, and share actions.
- [ ] Verify gallery open, edit, delete, recovery, backup, and restore behavior.
- [ ] Verify images remain available after restart and can be exported.
- [ ] Verify updates, rollback, uninstall behavior, and data retention expectations.
- [ ] Verify the browser URL works on supported desktop and mobile browsers.
- [ ] Verify release packages contain no local secrets, private gallery data, or certificates.
- [ ] Test performance against the agreed device, frame rate, and latency targets.

## 12. Suggested delivery sequence

- [ ] Confirm target users, editions, data flow, supported platforms, and pricing model.
- [ ] Finalize the shared settings model and module boundaries.
- [ ] Stabilize frame queues, reconnect behavior, stream status, and diagnostics.
- [ ] Verify stage previews, gallery recovery, export, backup, and restore.
- [ ] Improve phone layout and first run onboarding.
- [ ] Complete privacy, accessibility, licensing, and support documentation.
- [ ] Package and validate the desktop installer.
- [ ] Deploy and validate the static browser edition.
- [ ] Run release acceptance checks, publish release notes, and prepare rollback artifacts.

## Product decisions to settle before implementation

- [ ] Who is the first release for?
- [ ] Which operating systems, browsers, and phones will be supported?
- [ ] Which processing edition runs locally, and which requires a hosted service?
- [ ] What data, if any, is collected by the product owner?
- [ ] Which features belong to the free browser edition and the paid installable edition?
- [ ] Who owns ongoing support, updates, and release operations?
