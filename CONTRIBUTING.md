# Contributing

1. Create a branch for the change.
2. Keep crawler changes focused; avoid changing unrelated extraction behavior.
3. Preserve manual CAPTCHA behavior and local-first storage.
4. Test Python syntax with `python -m py_compile backend/*.py run.py`.
5. Test the WordPress plugin with `php -l wordpress-plugin/codexifyr-migrator-importer/codexifyr-migrator-importer.php` when PHP is available.
6. Never add customer `site-data.json`, runtime job folders, credentials or generated migration packages to Git.

For migration parser changes, add a note describing the source layout/platform pattern that required the change and what fallback behavior remains.
