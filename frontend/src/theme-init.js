// Applies the person's theme before the first paint, so a light theme
// does not open on a dark flash (or the other way round) while the app
// loads. ThemeService takes over once it runs; the storage key is its,
// and so is the rule: 'light' or 'dark' is a choice, anything else
// follows the system.
(function () {
  var saved = null;
  try {
    saved = localStorage.getItem('decentai.theme');
  } catch (error) {
    // Storage that cannot be read leaves the system preference to decide.
  }
  var dark =
    saved === 'dark' ||
    (saved !== 'light' &&
      !!window.matchMedia &&
      window.matchMedia('(prefers-color-scheme: dark)').matches);
  document.documentElement.classList.toggle('dark', dark);
  // The browser's own chrome takes the page's colour: --bg in
  // tailwind.css, written out because the stylesheet is not read yet.
  var meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.setAttribute('content', dark ? '#0f0e0d' : '#faf9f7');
})();
