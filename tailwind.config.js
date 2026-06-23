/**
 * Tailwind v3 config for the self-hosted compiled build (standalone CLI, no Node).
 * Mirrors the old CDN setup: dark mode via the `dark` class, typography plugin.
 *
 * `content` MUST list every place a Tailwind class name can appear, or the purge
 * will drop classes that are only used there. Note: form widget classes live in
 * Python (core/forms.py, templatetags), so the .py globs are load-bearing — not
 * optional.
 */
module.exports = {
  darkMode: 'class',
  content: [
    './core/templates/**/*.html',
    './templates/**/*.html',
    './accounts/templates/**/*.html',
    './core/**/*.py',
    './accounts/**/*.py',
  ],
  // The {% icon %} templatetag builds its size class dynamically in Python
  // (core/templatetags/mb_icons.py: f'w-{size} h-{size}'), so Tailwind can't see
  // the concrete w-N/h-N. Safelist the icon dimensions so they're never purged.
  safelist: [
    { pattern: /^(w|h)-(3|4|5|6|7|8|10|12|14|16)$/ },
  ],
  theme: {
    extend: {},
  },
  plugins: [
    require('@tailwindcss/typography'),
  ],
};
