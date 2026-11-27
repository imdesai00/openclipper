// Apply the saved theme before first paint to avoid a flash. External file so CSP can stay script-src 'self'.
try {
  var t = localStorage.getItem('oc-theme');
  var dark = t ? t === 'dark' : window.matchMedia('(prefers-color-scheme: dark)').matches;
  if (dark) document.documentElement.classList.add('dark');
} catch (e) {}
