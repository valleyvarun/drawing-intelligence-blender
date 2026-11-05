// ----------------------------------------------------------------------
// Drawing Intelligence download page
// ----------------------------------------------------------------------

// The installer is Windows-only for now; tell visitors on other systems.
(function showPlatformNote() {
  "use strict";

  const platform =
    (navigator.userAgentData && navigator.userAgentData.platform) ||
    navigator.platform ||
    "";
  const isWindows =
    /^win/i.test(platform) || /Windows NT/i.test(navigator.userAgent);
  if (isWindows) {
    return;
  }

  const note = document.getElementById("os-note");
  if (note) {
    note.hidden = false;
  }
})();
