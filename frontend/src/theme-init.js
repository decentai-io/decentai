// Applies the person's theme before the first paint, so a light theme
// does not open on a dark flash (or the other way round) while the app
// loads. ThemeService takes over once it runs; the storage key is its.
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
  document.documentElement.setAttribute('data-md-theme', dark ? 'dark' : 'light');
})();
