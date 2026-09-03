# Full E-commerce to Shopify Scraper

## Purpose

This project collects publicly available product data from an online storefront
and creates Shopify-compatible product CSV files. It is intended for stores and
websites that you are authorized to collect.

The scraper can collect:

- Product titles and cleaned URL handles
- Prices and compare-at prices as numeric values
- Product variants, options, SKUs, stock quantities, and weights
- Product images converted to JPEG import URLs
- Descriptions, tags, vendor data, and SEO metadata
- Collection names discovered from the storefront navigation

The generated CSV includes a `Collection` column. Products found in the same
website collection receive the same collection name during Shopify import.
The collection named `Shop`, blog pages, policy pages, FAQ pages, and tracking
pages are excluded from crawling.

## Project Setup

From the `full_ecommerce_scraper` folder, install Python 3.10 or newer and run:

    .\full_scraper\.venv\Scripts\python.exe -m pip install -r .\full_scraper\requirements.txt
    .\full_scraper\.venv\Scripts\python.exe -m playwright install chromium

The virtual environment already exists in `full_scraper\.venv` if setup has
already been completed.

## Basic Scrape

Run the active scraper from the project root:

    .\full_scraper\.venv\Scripts\python.exe .\scraper.py --url "https://example.com" --output ".\new_store" --reset

Example:

    .\full_scraper\.venv\Scripts\python.exe .\scraper.py --url "https://trapstarukshop.com/" --output ".\new_trapstar" --reset

Use a new output folder to keep earlier exports unchanged. The `--reset` flag
removes only that output folder's checkpoint database and starts a fresh crawl.

## CAPTCHA And Speed

The browser is visible by default so CAPTCHA or browser challenges can be
completed manually. Do not use `--headless` when manual interaction is needed.
After solving a challenge, return to the terminal and press Enter when asked.

For a faster run while keeping the browser visible:

    .\full_scraper\.venv\Scripts\python.exe .\scraper.py --url "https://example.com" --output ".\new_store" --reset --delay 0.5 --retries 3

Headless mode is available only when the website does not require manual CAPTCHA:

    .\full_scraper\.venv\Scripts\python.exe .\scraper.py --url "https://example.com" --output ".\new_store" --reset --headless

## Useful Options

- `--url`: storefront URL. If omitted, the scraper asks for it.
- `--output`: output directory for the CSV, report, and checkpoint.
- `--reset`: delete the selected output checkpoint and crawl from the beginning.
- `--headless`: hide the browser; manual CAPTCHA cannot be solved in this mode.
- `--delay`: pause between requests. The default is `0.7` seconds.
- `--retries`: number of navigation attempts. The default is `3`.
- `--max-products`: stop after this many products; useful for testing.
- `--max-pages`: maximum pages per collection.
- `--scrolls`: number of infinite-scroll attempts.
- `--timeout`: page navigation timeout in milliseconds.
- `--export-only`: regenerate CSV files from an existing checkpoint without crawling.
- `--cleanup`: remove previously saved non-product pages and refresh saved products.

## Output Files

Each output directory can contain:

- `shopify_products.csv`: main Shopify product import file.
- `collections/*.csv`: one self-contained CSV per scraped collection.
- `collections_meta.csv`: collection URLs and SEO metadata when available.
- `scrape_report.json`: crawl statistics and source URL.
- `checkpoint.sqlite`: saved categories and products used to resume or export.

The CSV uses Shopify's standard columns plus the allowed `Collection` column.
Image URLs are placed in `Image Src` and `Variant Image`, and duplicate image
URLs are removed within each product export block.

## Resume And Cleanup

If a run stops, repeat the command without `--reset` using the same output folder:

    .\full_scraper\.venv\Scripts\python.exe .\scraper.py --url "https://example.com" --output ".\new_store"

To repair an existing checkpoint without discovering categories again:

    .\full_scraper\.venv\Scripts\python.exe .\scraper.py --url "https://example.com" --output ".\new_store" --cleanup

To export the existing checkpoint without visiting the website:

    .\full_scraper\.venv\Scripts\python.exe .\scraper.py --output ".\new_store" --export-only

## Limitations And Responsible Use

Different storefronts expose different fields and may block automated browsing.
The scraper does not bypass CAPTCHA or browser security challenges. Respect the
site's terms, robots.txt, rate limits, privacy requirements, and applicable law.
Shopify image imports also require publicly accessible image URLs that Shopify
can download. Always inspect `scrape_report.json` and review the CSV before import.
