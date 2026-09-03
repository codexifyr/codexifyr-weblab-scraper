# Changelog

## 4.2.3
- Fixed all desktop download buttons by enabling pywebview/WebView2 file downloads before the GUI starts. Native Save As now works for JSON, CSV, reports, migration ZIPs, and the WordPress plugin ZIP.
- Added source-platform-aware classification for Shopify -> WordPress migrations.
- Shopify `/products/` pages are classified and saved as Products, `/collections/` as Categories, `/blogs/` as Blogs, and `/policies/`/`/pages/` as Pages.
- WordPress migrator reuses the bundled proven Shopify extraction logic through a read-only adapter; the original Shopify scraper source remains byte-for-byte unchanged.
- Added a true Max Products limiter independent of Max Crawl URLs.
- Products are prioritized after the homepage; Products-only mode queues direct products plus only useful product-discovery routes instead of unrelated policy/blog/info pages.
- Capture checkboxes remain strict: WebLab may visit discovery paths but saves only selected content types.
- Shopify variants/options/attribute names, sold-out state, variant images and SEO are normalized for WordPress migration JSON.

## 4.1.0
- Enforced capture-checkbox isolation: discovery URLs may be visited but unchecked content is not saved.
- Product-only scans no longer export Page/Blog/Category/Menu/Branding/Design/SEO data unless selected.
- Exported media is rebuilt only from selected capture types, preventing discovery-page assets from leaking into site-data.json.
- Selection-aware crawl priority surfaces selected product URLs earlier while retaining archive pages as discovery paths.
- Validation now reports WooCommerce attribute coverage and real variations, and respects intentionally disabled capture options.
- Max Pages UI renamed Max Crawl URLs to match actual behavior.
- Added safe-content read after navigation and corrected stop-during-preflight summary path.
- Windows shell installer fixes from 4.0.2 retained.
- Bundled Shopify scraper remains byte-for-byte unchanged.

## 4.1.0
- Fixed Windows PowerShell 5.1 shortcut setup failure caused by using unsupported `New-Item -LiteralPath`.
- Desktop shortcut, Start Menu shortcut, and uninstall entry now use PowerShell 5.1-compatible commands.
- No changes to the Shopify scraper engine.

## 4.1.0
- Fixed Windows installer shortcut creation failure caused by cmd.exe passing a literal caret (`^`) into inline PowerShell.
- Moved Desktop/Start Menu shortcut and uninstall-registry creation into `installer/install_shell.ps1` with no path arguments.
- Uses Windows shell-known Desktop/Programs folders, including redirected/OneDrive desktops.
- Corrected the WebLab uninstall registry key.

## 4.0.0 — Codexifyr WebLab
- Added Shopify Product Scraper as a separate locked engine.
- Added separate WordPress and Shopify CSV validators.
- File Correction can reuse extraction logic from the unchanged bundled scraper.
- Updated home branding to WebLab while preserving the Codexifyr logo.
- Full-page animated background remains behind the transparent logo.


## 3.1.2
- Fixed Windows installer path parsing when the project or user profile path contains spaces.
- Installer copy helper now derives source/install paths internally and receives no path arguments.
- Preserves the Data folder during installs and updates.

## 3.1.1 - Windows installer reliability fix

- Replaced the fragile Robocopy installation step with a staged PowerShell copy routine.
- Preserves the separate Data folder during installs and updates.
- Safely replaces partial/older App folders and reports useful copy errors.
- Uses Windows known-folder locations for Desktop and Start Menu shortcuts, including redirected/OneDrive desktops.
- Keeps the existing scraper, repair, CAPTCHA, migration and WordPress logic unchanged.

# v3.0.1 — Design integration

- Integrated transparent Codexifyr hero wordmark.
- Added animated purple particle/grid/wave background to the shared frontend shell.
- Updated home page with centered branding and three primary product cards.
- Preserved existing scraper, repair, job, validation, export, plugin, and documentation backend logic.


## 3.0.0 — Codexifyr Website Migrator PRO

- Rebuilt frontend in black + neon/dark-purple responsive design.
- Added separate Website Scraper, File Correction, Job History, Validation, Migration/Export, WordPress Plugin and Info pages.
- Added persistent multi-job workspaces so scan and repair jobs can run simultaneously.
- Added restart recovery for interrupted scans and targeted repairs.
- Added network-loss waiting for common browser network-disconnect errors.
- Added manual CAPTCHA handling based on the proven Codexifyr product-scraper behavior.
- Added 404/410 no-retry behavior and sitemap utility/media URL filtering.
- Added generalized WooCommerce variation extraction with embedded JSON, live DOM, AJAX and browser-interaction fallbacks.
- Added variation stock, prices, source SKU, primary variation image and optional dynamic variation gallery capture.
- Added targeted field-level File Correction with immutable original JSON and delta checkpoints.
- Added anomaly detection/readiness scoring and suspicious Default Title variant detection.
- Added corrected JSON, repair report and full WordPress package downloads.
- Added persistent package availability after application restart.
- Added normal streamed browser downloads and suppressed broken-pipe/local disconnect tracebacks.
- Added downloadable WordPress importer plugin page.
- Updated WordPress importer to 3.0.0 with resumable stage messaging and expected-vs-imported verification.
- Added Codexifyr logo/favicon assets and web manifest.
- Added GitHub-ready README, Mermaid diagrams, `.gitignore`, `.env.example`, SECURITY and CONTRIBUTING docs.

## 3.1.0 - Windows Desktop Edition
- Added native pywebview/WebView2 desktop shell.
- Added double-click Windows installer that creates Desktop + Start Menu shortcuts.
- Moved persistent customer/job data outside application files to LocalAppData.
- Added safe uninstaller that preserves job data by default.
- Added PyInstaller + Inno Setup build kit for producing a branded Setup.exe on Windows.
- Preserved separate visible Playwright browser windows for CAPTCHA/manual site interaction.
