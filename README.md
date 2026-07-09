# Uber Eats & DoorDash Scraper

A powerful, highly modular **Python library and Command-Line Interface (CLI)** for scraping structured restaurant metadata, menus, prices, and product images from **Uber Eats** and **DoorDash** store pages using headless Chromium via [Playwright](https://playwright.dev/python/).

---

## Features

- **Dual Platform Support**: Seamlessly scrapes both **Uber Eats** and **DoorDash** restaurant store URLs.
- **Batch Scraping via `links.txt`**: Supply input URLs either directly via the CLI or from a newline-separated file (e.g., `links.txt`).
- **Flexible Data Export Formats**:
  - **CSV**: Exports `info.csv` (store metadata) and `menu.csv` (menu items).
  - **JSON**: Exports clean, structured `info.json`, `menu.json`, plus a consolidated `store_data.json` package.
  - **All**: Export both CSV and JSON simultaneously.
- **Customizable Image Storage**:
  - Optionally download high-resolution menu item images locally.
  - Customize the image storage folder name (`--image-dir`) or skip image downloading entirely (`--no-images`).
- **Dual-Use Architecture**: Use directly from your terminal as a CLI tool or import it as a Python module in your own scripts and data pipelines.

---

## What Information is Scraped

For each restaurant store URL, the scraper extracts detailed metadata about the store as well as every item in its menu catalog:

### 1. Store Information (`info.csv` / `info.json`)

| Field | Description | Example |
| :--- | :--- | :--- |
| **Restaurant Name** | Official business name of the store | `Starbucks` |
| **Address** | Full physical street address, city, region, and postal code | `123 Main St, Tampa, FL 33602` |
| **Average Rating** | Aggregate customer rating score | `4.8` |
| **Review Count** | Total number of customer reviews or ratings | `500` |
| **Hours of Operation** | Weekly operating schedule by day | `Mo-Su: 06:00 - 21:00` |
| **Source URL** | The canonical store URL that was scraped | `https://www.ubereats.com/store/...` |

### 2. Menu Items (`menu.csv` / `menu.json`)

| Field | Description | Example |
| :--- | :--- | :--- |
| **Category / Section** | Menu section header where the item is listed | `Espresso Beverages` |
| **Item Name** | Product title | `Caramel Macchiato` |
| **Item Description** | Detailed description or list of ingredients | `Freshly steamed milk with vanilla-flavored syrup...` |
| **Item Price** | Formatted price string | `$5.45` |
| **Item Image URL** | Remote CDN link to the item photo | `https://tb-static.uber.com/.../image.jpg` |
| **Local Image Path** | Relative filesystem path to the downloaded image file | `images/caramel_macchiato.jpg` |

---

## Installation

### 1. Clone the repository & install dependencies

Ensure you have Python 3.8+ installed, then install required Python packages:

```bash
pip install -r requirements.txt
```

### 2. Install Playwright Chromium Browser

Install the Chromium browser binaries required by Playwright:

```bash
playwright install chromium
```

---

## CLI Interface & Usage

### Command-Line Arguments

| Flag / Argument | Description | Default |
| :--- | :--- | :--- |
| `urls` | Positional restaurant URLs to scrape directly | *None* |
| `-i`, `--input-file`, `--links-file` | Path to a text file (e.g., `links.txt`) containing URLs (one per line) | *None* |
| `-o`, `--output-dir` | Root directory where structured store folders will be saved | `output` |
| `-F`, `--format`, `--formats` | Output formats: `csv`, `json`, or `all` | `all` |
| `--download-images` | Download product images locally | `True` |
| `--no-images`, `--skip-images` | Skip downloading product images | `False` |
| `--image-dir` | Subdirectory name within the store folder to save downloaded images | `images` |
| `--headless` / `--no-headless` | Run browser in headless mode or with visible UI | `--headless` |
| `--timeout` | Page navigation timeout in milliseconds | `60000` |

---

### Example 1: Scraping from a `links.txt` File

Create a file named `links.txt` where each line is an Uber Eats or DoorDash restaurant page:

```text
# links.txt
https://www.ubereats.com/store/starbucks-tampa/abc123def
https://www.doordash.com/store/mcdonalds-tampa-12345/
```

Run the scraper using the `-i` flag:

```bash
python scraper.py -i links.txt --format all --download-images -o output
```

### Example 2: Scraping Direct URLs & Skipping Images

Scrape a single store URL and save only JSON data without downloading product images:

```bash
python scraper.py "https://www.ubereats.com/store/example-store/12345" --format json --no-images
```

### Example 3: Customizing Output & Image Directory

```bash
python scraper.py -i links.txt -o my_data_exports --format csv json --image-dir assets/photos
```

---

## Output Structure & Data Formats

When you scrape a store, a dedicated folder named after the store slug is created inside the output directory:

```text
output/
└── starbucks-tampa/
    ├── store_data.json         # Combined store metadata & menu payload
    ├── info/
    │   ├── info.csv            # Store info in CSV format
    │   └── info.json           # Store info in JSON format
    ├── menu/
    │   ├── menu.csv            # Menu items in CSV format
    │   └── menu.json           # Menu items in JSON format
    └── images/                 # Downloaded menu item images (.jpg, .png, .webp)
        ├── caramel_macchiato.jpg
        └── iced_latte.jpg
```

### CSV Schemas

- **`info/info.csv`**:
  - `Restaurant Name`, `Address`, `Average Rating`, `Review Count`, `Hours of Operation`, `Source URL`
- **`menu/menu.csv`**:
  - `Category/Section`, `Item Name`, `Item Description`, `Item Price`, `Item Image URL`, `Local Image Path`

### Combined JSON Schema (`store_data.json`)

```json
{
  "store_slug": "starbucks-tampa",
  "source_url": "https://www.ubereats.com/store/starbucks-tampa/abc123def",
  "scraped_at": "2026-07-09T21:52:00.000000+00:00",
  "store_info": {
    "name": "Starbucks",
    "address": "123 Main St, Tampa, FL 33602",
    "rating_value": 4.8,
    "review_count": 500,
    "hours": "Mo-Su: 06:00 - 21:00",
    "source_url": "https://www.ubereats.com/store/starbucks-tampa/abc123def"
  },
  "menu_items_count": 42,
  "menu_items": [
    {
      "section": "Espresso Beverages",
      "item_name": "Caramel Macchiato",
      "item_description": "Freshly steamed milk with vanilla-flavored syrup...",
      "item_price": "$5.45",
      "item_image": "https://tb-static.uber.com/.../image.jpg",
      "local_image_path": "images/caramel_macchiato.jpg"
    }
  ]
}
```

---

## Using as a Python Library

You can easily import `scraper.py` into your own Python projects:

```python
from scraper import load_urls_from_file, scrape_urls, scrape_url

# 1. Load URLs from a file
urls = load_urls_from_file("links.txt")

# 2. Batch scrape all URLs
results = scrape_urls(
    urls=urls,
    output_dir="output",
    formats=["csv", "json"],
    download_images=True,
    image_dir="images",
    headless=True
)

for res in results:
    print(f"Scraped {res['store_slug']} — {res['items_count']} items saved to {res['store_dir']}")
```

### Custom Pipeline with `scrape_url`

```python
from scraper import scrape_url

store_result = scrape_url(
    url="https://www.ubereats.com/store/starbucks-tampa/abc123def",
    output_dir="data",
    formats=["json"],
    download_images=False
)
```

---

## Version Control & Output Ignoring

A `.gitignore` file is included to ensure that generated files inside the `output/` directory and downloaded media are not accidentally committed to git:

```gitignore
output/
output/*
__pycache__/
*.py[cod]
```
