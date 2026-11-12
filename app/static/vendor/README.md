# Fixed offline assets

- `htmx-1.9.12.min.js`: htmx 1.9.12 upstream `dist/htmx.min.js`, Zero-Clause BSD license in `htmx-LICENSE.txt`.
- `tailwind-3.4.17.css`: generated from the checked-in templates and JavaScript by Tailwind CSS 3.4.17; MIT license in `tailwind-LICENSE.txt`.
- Rebuild CSS from repository root: `npm ci --ignore-scripts && npm run build:css`. Node is a build-time tool only. Commit generated CSS with template changes. Runtime never contacts a CDN.
