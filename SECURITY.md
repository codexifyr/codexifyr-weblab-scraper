# Security and Privacy

Codexifyr is designed as a local-first migration workspace. Website scan data, uploaded `site-data.json` files, repair deltas, logs and generated migration packages are stored under the local runtime workspace and are ignored by Git.

Do not commit customer/source website captures, credentials, cookies, API keys, WordPress passwords, private media, `.env` files or generated migration packages.

The crawler is intended for websites you own or are authorized to migrate. CAPTCHA and browser challenges are handled manually in the visible browser; the project does not implement CAPTCHA bypassing.

Before publishing a repository, review `git status` and run your preferred secret scanner. Git/GitHub actions are never performed automatically by Codexifyr.
