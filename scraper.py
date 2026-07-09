"""
Uber Eats & DoorDash Scraper Library and CLI

This module can be used as a standalone CLI tool or imported as a Python library
to scrape restaurant information, menu items, and product images from Uber Eats
and DoorDash store URLs.

Examples:
    CLI Usage:
        # Scrape URLs from a text file (links.txt) and save as both CSV and JSON with images
        python scraper.py -i links.txt --format all --download-images -o output
        
        # Scrape a direct URL without downloading images, saving only JSON
        python scraper.py "https://www.ubereats.com/store/..." --format json --no-images

    Python Library Usage:
        from scraper import scrape_urls, load_urls_from_file
        
        urls = load_urls_from_file("links.txt")
        results = scrape_urls(
            urls=urls,
            output_dir="output",
            formats=["csv", "json"],
            download_images=True
        )
"""

import argparse
import csv
from datetime import datetime, timezone
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from typing import Dict, List, Optional, Tuple, Union
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright, Page


def clean_text(text: Optional[str]) -> str:
    """Clean and normalize whitespace in a text string."""
    if not text:
        return ""
    return re.sub(r'\s+', ' ', str(text)).strip()


def sanitize_filename(name: str) -> str:
    """Sanitize a string to be safe for use as a filesystem path or filename."""
    return re.sub(r'[\\/*?:"<>|]', "", str(name)).replace(" ", "_").lower()


def get_store_slug(url: str) -> str:
    """Extract a clean store identifier slug from a restaurant URL."""
    match = re.search(r'/store/([^/\?#]+)', url)
    if match:
        return sanitize_filename(match.group(1))
    parsed = urllib.parse.urlsplit(url)
    parts = [p for p in parsed.path.split('/') if p]
    if parts:
        return sanitize_filename(parts[-1])
    return "unknown_store"


def download_image(url: str, save_path: str, timeout: int = 15) -> bool:
    """Download an image from a URL and save it to the specified filesystem path."""
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    }
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as response:
            with open(save_path, 'wb') as f:
                f.write(response.read())
        return True
    except Exception as e:
        print(f"Warning: Failed to download image {url}: {e}")
        return False


def extract_doordash_hours(payload: str) -> str:
    """Extract operation hours from DoorDash embedded JSON payloads."""
    idx = 0
    schedules = []
    while True:
        pos = payload.find('{"__typename":"OperationHourInfo"', idx)
        if pos == -1:
            break
        braces = 0
        end_pos = -1
        for i in range(pos, len(payload)):
            if payload[i] == '{':
                braces += 1
            elif payload[i] == '}':
                braces -= 1
                if braces == 0:
                    end_pos = i + 1
                    break
        if end_pos != -1:
            block = payload[pos:end_pos]
            try:
                block_clean = block.replace('\\"', '"').replace('\\\\', '\\')
                data = json.loads(block_clean)
                desc = data.get("description", "")
                if "actual hours of operation" in desc.lower() or not schedules:
                    sched_list = []
                    for day_sched in data.get("operationSchedule", []):
                        day = day_sched.get("dayOfWeek", "")
                        slots = ", ".join(day_sched.get("timeSlotList", []))
                        sched_list.append(f"{day}: {slots}")
                    if sched_list:
                        schedules = sched_list
            except Exception:
                pass
            idx = end_pos
        else:
            idx = pos + 1
    return "; ".join(schedules) if schedules else "N/A"


def scrape_doordash(page: Page, url: str, timeout: int = 60000) -> Tuple[Dict, List[Dict]]:
    """Scrape store metadata and menu items from a DoorDash store URL."""
    print(f"Loading DoorDash page: {url}")
    page.goto(url, wait_until="domcontentloaded", timeout=timeout)
    try:
        page.wait_for_selector('script[type="application/ld+json"]', state="attached", timeout=15000)
    except Exception:
        page.wait_for_timeout(4000)
    
    html = page.content()
    soup = BeautifulSoup(html, "html.parser")
    
    image_map = {}
    next_f_parts = []
    for s in soup.find_all("script"):
        text = s.string or ""
        matches = re.findall(r'self\.__next_f\.push\(\[\d+,\s*"(.*?)"\]\)', text, re.DOTALL)
        for m in matches:
            try:
                decoded = json.loads(f'"{m}"')
                next_f_parts.append(decoded)
            except Exception:
                next_f_parts.append(m.replace('\\"', '"').replace('\\\\', '\\'))
    
    full_payload = "".join(next_f_parts)
    
    idx = 0
    while True:
        pos = full_payload.find('{"__typename":"MenuPageItem"', idx)
        if pos == -1:
            pos = full_payload.find('{"__typename":"StorePageCarouselItem"', idx)
        if pos == -1:
            break
            
        braces = 0
        end_pos = -1
        for i in range(pos, len(full_payload)):
            if full_payload[i] == '{':
                braces += 1
            elif full_payload[i] == '}':
                braces -= 1
                if braces == 0:
                    end_pos = i + 1
                    break
        if end_pos != -1:
            block = full_payload[pos:end_pos]
            try:
                data = json.loads(block)
                name = data.get("name")
                img = data.get("imageUrl") or data.get("imgUrl")
                if name and img:
                    image_map[name.strip().lower()] = img
            except Exception:
                try:
                    unescaped = block.replace('\\"', '"').replace('\\\\', '\\')
                    data = json.loads(unescaped)
                    name = data.get("name")
                    img = data.get("imageUrl") or data.get("imgUrl")
                    if name and img:
                        image_map[name.strip().lower()] = img
                except Exception:
                    pass
            idx = pos + 1
        else:
            idx = pos + 1
            
    hours = extract_doordash_hours(full_payload)
    
    restaurant_info = {}
    menu_items = []
    
    def extract_sections(section_list):
        sections = []
        for item in section_list:
            if isinstance(item, list):
                sections.extend(extract_sections(item))
            elif isinstance(item, dict):
                sections.append(item)
        return sections
        
    scripts = soup.find_all("script", type="application/ld+json")
    for i, s in enumerate(scripts):
        try:
            data = json.loads(s.string or "")
            t = data.get("@type")
            
            if t == "Restaurant":
                ld_hours = data.get("openingHours") or data.get("openingHoursSpecification")
                restaurant_info = {
                    "name": data.get("name"),
                    "address": data.get("address", {}).get("streetAddress") if isinstance(data.get("address"), dict) else data.get("address"),
                    "rating_value": data.get("aggregateRating", {}).get("ratingValue") if isinstance(data.get("aggregateRating"), dict) else None,
                    "review_count": data.get("aggregateRating", {}).get("reviewCount") if isinstance(data.get("aggregateRating"), dict) else None,
                    "hours": hours if hours != "N/A" else (str(ld_hours) if ld_hours else "N/A")
                }
                
            elif t == "Menu":
                sections = extract_sections(data.get("hasMenuSection", []))
                for sec in sections:
                    sec_name = sec.get("name")
                    items = sec.get("hasMenuItem", [])
                    for item in items:
                        offers = item.get("offers", {})
                        price = None
                        if isinstance(offers, dict):
                            price = offers.get("price")
                        elif isinstance(offers, list) and len(offers) > 0:
                            price = offers[0].get("price")
                            
                        item_name = item.get("name")
                        desc = item.get("description")
                        img = image_map.get(item_name.strip().lower()) if item_name else None
                        
                        menu_items.append({
                            "section": sec_name,
                            "item_name": item_name,
                            "item_description": desc,
                            "item_price": price,
                            "item_image": img
                        })
        except Exception as e:
            print(f"Warning: Error parsing LD+JSON block {i}: {e}")
            
    return restaurant_info, menu_items


def format_uber_hours(hours_spec: Union[List, Dict, str]) -> str:
    """Format Uber Eats opening hours specification into a human-readable string."""
    if not hours_spec:
        return "N/A"
    if isinstance(hours_spec, list):
        formatted = []
        for h in hours_spec:
            days = h.get("dayOfWeek", [])
            if isinstance(days, list):
                days_str = ", ".join(days)
            else:
                days_str = str(days)
            opens = h.get("opens", "")
            closes = h.get("closes", "")
            formatted.append(f"{days_str}: {opens} - {closes}")
        return "; ".join(formatted)
    return str(hours_spec)


def scrape_ubereats(page: Page, url: str, timeout: int = 60000) -> Tuple[Dict, List[Dict]]:
    """Scrape store metadata and menu items from an Uber Eats store URL."""
    print(f"Loading Uber Eats page: {url}")
    page.goto(url, wait_until="domcontentloaded", timeout=timeout)
    try:
        page.wait_for_selector('script[type="application/ld+json"]', state="attached", timeout=15000)
    except Exception:
        page.wait_for_timeout(4000)
    
    html = page.content()
    soup = BeautifulSoup(html, "html.parser")
    
    image_map = {}
    script_rq = soup.find("script", id="__REACT_QUERY_STATE__")
    if script_rq:
        text = script_rq.string or ""
        try:
            prepared_text = text.replace('%5C', '\\\\').replace('%5c', '\\\\')
            decoded = prepared_text.encode('utf-8').decode('unicode_escape')
            data = json.loads(decoded)
            
            queries = data.get("queries", [])
            if queries:
                qdata = queries[0].get("state", {}).get("data", {})
                if qdata and isinstance(qdata, dict):
                    sections_map = qdata.get("catalogSectionsMap", {})
                    for sec_key, subsections in sections_map.items():
                        if isinstance(subsections, list):
                            for subsec in subsections:
                                payload = subsec.get("payload", {})
                                standard = payload.get("standardItemsPayload", {})
                                catalog_items = standard.get("catalogItems", [])
                                for item in catalog_items:
                                    name = item.get("title")
                                    img = item.get("imageUrl")
                                    if name and img:
                                        image_map[name.strip().lower()] = img
                                        
                    feat_sec = qdata.get("featuredItemsSections", {})
                    if isinstance(feat_sec, dict):
                        for sec_key, sec_val in feat_sec.items():
                            payload = sec_val.get("payload", {})
                            standard = payload.get("standardItemsPayload", {})
                            catalog_items = standard.get("catalogItems", [])
                            for item in catalog_items:
                                name = item.get("title")
                                img = item.get("imageUrl")
                                if name and img:
                                    image_map[name.strip().lower()] = img
        except Exception as e:
            print(f"Warning: Failed to parse React Query state for images: {e}")
            
    restaurant_info = {}
    menu_items = []
    
    scripts_ld = soup.find_all("script", type="application/ld+json")
    for i, s in enumerate(scripts_ld):
        try:
            data = json.loads(s.string or "")
            t = data.get("@type")
            
            if t == "Restaurant":
                hours_spec = data.get("openingHoursSpecification")
                hours_str = format_uber_hours(hours_spec)
                addr_data = data.get("address", {})
                if isinstance(addr_data, dict):
                    addr_str = addr_data.get("streetAddress", "")
                    locality = addr_data.get("addressLocality", "")
                    region = addr_data.get("addressRegion", "")
                    postal = addr_data.get("postalCode", "")
                    addr = f"{addr_str}, {locality}, {region} {postal}".strip(", ")
                else:
                    addr = str(addr_data)
                    
                restaurant_info = {
                    "name": data.get("name"),
                    "address": addr,
                    "rating_value": data.get("aggregateRating", {}).get("ratingValue") if isinstance(data.get("aggregateRating"), dict) else None,
                    "review_count": data.get("aggregateRating", {}).get("reviewCount") if isinstance(data.get("aggregateRating"), dict) else None,
                    "hours": hours_str
                }
                
                has_menu = data.get("hasMenu", {})
                sections = has_menu.get("hasMenuSection", [])
                for sec in sections:
                    sec_name = sec.get("name") or "Menu"
                    items = sec.get("hasMenuItem", [])
                    for item in items:
                        offers = item.get("offers", {})
                        price = None
                        if isinstance(offers, dict):
                            price = offers.get("price")
                            curr = offers.get("priceCurrency")
                            if price and curr == "USD":
                                price = f"${price}"
                            elif price:
                                price = f"{price} {curr}"
                                
                        item_name = item.get("name")
                        desc = item.get("description")
                        img = image_map.get(item_name.strip().lower()) if item_name else None
                        
                        menu_items.append({
                            "section": sec_name,
                            "item_name": item_name,
                            "item_description": desc,
                            "item_price": price,
                            "item_image": img
                        })
        except Exception as e:
            print(f"Warning: Error parsing Uber Eats LD+JSON block {i}: {e}")
            
    return restaurant_info, menu_items


def load_urls_from_file(file_path: str) -> List[str]:
    """
    Load restaurant URLs from a text file (e.g., links.txt).
    Each line should contain one URL. Blank lines and lines starting with '#' are ignored.
    """
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"Input file not found: {file_path}")
        
    urls = []
    with open(file_path, "r", encoding="utf-8") as f:
        for line_num, line in enumerate(f, start=1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            urls.append(stripped)
    return urls


def save_store_data(
    store_slug: str,
    info: Dict,
    menu: List[Dict],
    source_url: str,
    output_dir: str = "output",
    formats: Union[List[str], Tuple[str, ...]] = ("csv", "json"),
    download_images: bool = True,
    image_dir: str = "images"
) -> Dict[str, Union[str, List[str]]]:
    """
    Save scraped restaurant information and menu items to structured files (CSV/JSON)
    and optionally download product images locally.

    Args:
        store_slug: Unique identifier slug for the store directory.
        info: Dictionary containing store metadata (name, address, rating, hours, etc.).
        menu: List of dictionaries representing menu items.
        source_url: Original store URL that was scraped.
        output_dir: Root directory where output folders are created.
        formats: List of output formats to export ('csv', 'json', or both).
        download_images: Whether to download item images locally.
        image_dir: Subdirectory name for storing images within the store output folder.

    Returns:
        Dictionary summarizing saved output file paths.
    """
    store_dir = os.path.join(output_dir, store_slug)
    images_full_dir = os.path.join(store_dir, image_dir)
    info_dir = os.path.join(store_dir, "info")
    menu_dir = os.path.join(store_dir, "menu")
    
    os.makedirs(store_dir, exist_ok=True)
    if download_images:
        os.makedirs(images_full_dir, exist_ok=True)
    
    # Ensure formats list is lowercase
    fmt_set = {f.lower() for f in formats}
    if "all" in fmt_set:
        fmt_set = {"csv", "json"}
        
    saved_files = []
    
    # Process images
    for idx, item in enumerate(menu):
        img_url = item.get("item_image")
        if download_images and img_url:
            ext = ".jpg"
            img_lower = img_url.lower()
            if ".png" in img_lower:
                ext = ".png"
            elif ".webp" in img_lower:
                ext = ".webp"
            elif ".jpeg" in img_lower:
                ext = ".jpeg"
                
            item_name = item.get("item_name") or f"item_{idx}"
            filename = f"{sanitize_filename(item_name)}{ext}"
            local_path = os.path.join(images_full_dir, filename)
            
            print(f"  [{idx+1}/{len(menu)}] Downloading image: {item_name}")
            if download_image(img_url, local_path):
                # Save relative path inside store directory
                item["local_image_path"] = os.path.join(image_dir, filename).replace("\\", "/")
            else:
                item["local_image_path"] = ""
        else:
            item["local_image_path"] = ""

    # Add source url to info dict
    info_clean = {
        "name": info.get("name"),
        "address": info.get("address"),
        "rating_value": info.get("rating_value"),
        "review_count": info.get("review_count"),
        "hours": info.get("hours"),
        "source_url": source_url
    }

    # Save CSV format
    if "csv" in fmt_set:
        os.makedirs(info_dir, exist_ok=True)
        os.makedirs(menu_dir, exist_ok=True)
        
        info_csv_path = os.path.join(info_dir, "info.csv")
        with open(info_csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([
                "Restaurant Name", "Address", "Average Rating",
                "Review Count", "Hours of Operation", "Source URL"
            ])
            writer.writerow([
                clean_text(info_clean.get("name")),
                clean_text(info_clean.get("address")),
                info_clean.get("rating_value"),
                info_clean.get("review_count"),
                clean_text(info_clean.get("hours")),
                source_url
            ])
        saved_files.append(info_csv_path)
        print(f"  -> Saved CSV store info: {info_csv_path}")

        menu_csv_path = os.path.join(menu_dir, "menu.csv")
        with open(menu_csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([
                "Category/Section", "Item Name", "Item Description",
                "Item Price", "Item Image URL", "Local Image Path"
            ])
            for item in menu:
                writer.writerow([
                    clean_text(item.get("section")),
                    clean_text(item.get("item_name")),
                    clean_text(item.get("item_description")),
                    clean_text(item.get("item_price")),
                    clean_text(item.get("item_image")),
                    clean_text(item.get("local_image_path"))
                ])
        saved_files.append(menu_csv_path)
        print(f"  -> Saved CSV menu: {menu_csv_path}")

    # Save JSON format
    if "json" in fmt_set:
        os.makedirs(info_dir, exist_ok=True)
        os.makedirs(menu_dir, exist_ok=True)
        
        info_json_path = os.path.join(info_dir, "info.json")
        with open(info_json_path, "w", encoding="utf-8") as f:
            json.dump(info_clean, f, indent=2, ensure_ascii=False)
        saved_files.append(info_json_path)
        print(f"  -> Saved JSON store info: {info_json_path}")

        menu_json_path = os.path.join(menu_dir, "menu.json")
        with open(menu_json_path, "w", encoding="utf-8") as f:
            json.dump(menu, f, indent=2, ensure_ascii=False)
        saved_files.append(menu_json_path)
        print(f"  -> Saved JSON menu: {menu_json_path}")

        # Combined consolidated store package JSON at store root
        combined_json_path = os.path.join(store_dir, "store_data.json")
        combined_payload = {
            "store_slug": store_slug,
            "source_url": source_url,
            "scraped_at": datetime.now(timezone.utc).isoformat(),
            "store_info": info_clean,
            "menu_items_count": len(menu),
            "menu_items": menu
        }
        with open(combined_json_path, "w", encoding="utf-8") as f:
            json.dump(combined_payload, f, indent=2, ensure_ascii=False)
        saved_files.append(combined_json_path)
        print(f"  -> Saved combined store JSON: {combined_json_path}")

    return {
        "store_slug": store_slug,
        "store_dir": store_dir,
        "saved_files": saved_files,
        "items_count": len(menu)
    }


def scrape_url(
    url: str,
    page: Optional[Page] = None,
    output_dir: str = "output",
    formats: Union[List[str], Tuple[str, ...]] = ("csv", "json"),
    download_images: bool = True,
    image_dir: str = "images",
    headless: bool = False,
    timeout: int = 60000
) -> Optional[Dict]:
    """
    Scrape a single store URL and save the output.
    If `page` is None, a new Playwright instance will be created automatically.
    """
    url_lower = url.lower()
    store_slug = get_store_slug(url)
    print(f"\nProcessing Store: {store_slug} ({url})")

    managed_browser = False
    p_instance = None
    browser = None

    try:
        if page is None:
            managed_browser = True
            p_instance = sync_playwright().start()
            browser = p_instance.chromium.launch(
                headless=headless,
                args=["--disable-blink-features=AutomationControlled"]
            )
            context = browser.new_context(
                viewport={"width": 1280, "height": 800}
            )
            page = context.new_page()

        if "doordash.com" in url_lower:
            info, menu = scrape_doordash(page, url, timeout=timeout)
        elif "ubereats.com" in url_lower:
            info, menu = scrape_ubereats(page, url, timeout=timeout)
        else:
            print(f"Warning: Unsupported URL (only Uber Eats and DoorDash supported): {url}")
            return None

        if not info or not menu:
            if headless and managed_browser:
                print(f"Notice: Anti-bot challenge or incomplete load detected in headless mode for {url}. Automatically retrying with visible UI (headless=False)...")
                try:
                    if browser:
                        browser.close()
                    browser = p_instance.chromium.launch(
                        headless=False,
                        args=["--disable-blink-features=AutomationControlled"]
                    )
                    context = browser.new_context(
                        viewport={"width": 1280, "height": 800}
                    )
                    page = context.new_page()
                    if "doordash.com" in url_lower:
                        info, menu = scrape_doordash(page, url, timeout=timeout)
                    elif "ubereats.com" in url_lower:
                        info, menu = scrape_ubereats(page, url, timeout=timeout)
                except Exception as retry_e:
                    print(f"Warning: Retry failed: {retry_e}")

            if not info or not menu:
                print(f"Warning: Could not extract sufficient store metadata or menu items from {url}")
                return None

        result = save_store_data(
            store_slug=store_slug,
            info=info,
            menu=menu,
            source_url=url,
            output_dir=output_dir,
            formats=formats,
            download_images=download_images,
            image_dir=image_dir
        )
        print(f"Finished processing store: {store_slug}")
        return result

    except Exception as e:
        print(f"Error scraping {url}: {e}", file=sys.stderr)
        return None
    finally:
        if managed_browser:
            if browser:
                browser.close()
            if p_instance:
                p_instance.stop()


def scrape_urls(
    urls: List[str],
    output_dir: str = "output",
    formats: Union[List[str], Tuple[str, ...]] = ("csv", "json"),
    download_images: bool = True,
    image_dir: str = "images",
    headless: bool = False,
    timeout: int = 60000
) -> List[Dict]:
    """
    Scrape multiple store URLs efficiently using a shared Playwright browser instance.

    Args:
        urls: List of Uber Eats or DoorDash URLs to scrape.
        output_dir: Directory where outputs should be saved.
        formats: File formats to generate ('csv', 'json', or 'all').
        download_images: Whether to download item images.
        image_dir: Subdirectory name for images within each store directory.
        headless: Whether to run Playwright in headless mode.
        timeout: Navigation timeout in milliseconds.

    Returns:
        List of dictionaries containing results for each scraped store.
    """
    results = []
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=headless,
            args=["--disable-blink-features=AutomationControlled"]
        )
        context = browser.new_context(
            viewport={"width": 1280, "height": 800}
        )
        page = context.new_page()

        for url in urls:
            res = scrape_url(
                url=url,
                page=page,
                output_dir=output_dir,
                formats=formats,
                download_images=download_images,
                image_dir=image_dir,
                headless=headless,
                timeout=timeout
            )
            if not res and headless:
                print(f"Notice: Retrying {url} with visible UI (headless=False)...")
                res = scrape_url(
                    url=url,
                    page=None,
                    output_dir=output_dir,
                    formats=formats,
                    download_images=download_images,
                    image_dir=image_dir,
                    headless=False,
                    timeout=timeout
                )
            if res:
                results.append(res)

        browser.close()
    return results


def parse_args(args: Optional[List[str]] = None) -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Uber Eats & DoorDash Scraper — Scrape store metadata, menus, and product images into CSV and JSON."
    )
    parser.add_argument(
        "urls",
        nargs="*",
        help="One or more restaurant URLs to scrape directly."
    )
    parser.add_argument(
        "-i", "--input-file", "-f", "--links-file",
        dest="input_file",
        default=None,
        help="Path to a text file (e.g., links.txt) containing URLs to scrape (one URL per line)."
    )
    parser.add_argument(
        "-o", "--output-dir",
        default="output",
        help="Root directory where structured store outputs will be saved (default: output)."
    )
    parser.add_argument(
        "-F", "--format", "--formats",
        dest="formats",
        nargs="+",
        choices=["csv", "json", "all"],
        default=["all"],
        help="Output data format(s): csv, json, or all (default: all)."
    )
    parser.add_argument(
        "--download-images",
        dest="download_images",
        action="store_true",
        default=True,
        help="Download item images locally (default: True)."
    )
    parser.add_argument(
        "--no-images", "--skip-images",
        dest="download_images",
        action="store_false",
        help="Skip downloading item images."
    )
    parser.add_argument(
        "--image-dir",
        default="images",
        help="Subdirectory name within each store output folder to store images (default: images)."
    )
    parser.add_argument(
        "--headless",
        dest="headless",
        action="store_true",
        default=False,
        help="Run browser in headless mode."
    )
    parser.add_argument(
        "--no-headless",
        dest="headless",
        action="store_false",
        help="Run browser with visible UI (default: False)."
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=60000,
        help="Page navigation timeout in milliseconds (default: 60000)."
    )
    return parser.parse_args(args)


def main():
    args = parse_args()

    urls_to_scrape = list(args.urls) if args.urls else []

    if args.input_file:
        try:
            file_urls = load_urls_from_file(args.input_file)
            print(f"Loaded {len(file_urls)} URL(s) from {args.input_file}")
            urls_to_scrape.extend(file_urls)
        except Exception as e:
            print(f"Error loading input file '{args.input_file}': {e}", file=sys.stderr)
            sys.exit(1)

    # Remove duplicates while preserving order
    unique_urls = []
    seen = set()
    for u in urls_to_scrape:
        if u not in seen:
            unique_urls.append(u)
            seen.add(u)

    if not unique_urls:
        print("No URLs provided. Pass URLs as arguments or specify a file using -i/--input-file (e.g., -i links.txt).", file=sys.stderr)
        print("Run 'python scraper.py --help' for usage options.", file=sys.stderr)
        sys.exit(1)

    print(f"Starting scraper for {len(unique_urls)} unique store URL(s)...")

    results = scrape_urls(
        urls=unique_urls,
        output_dir=args.output_dir,
        formats=args.formats,
        download_images=args.download_images,
        image_dir=args.image_dir,
        headless=args.headless,
        timeout=args.timeout
    )

    print(f"\nScraping completed. Successfully processed {len(results)} out of {len(unique_urls)} store(s).")


if __name__ == "__main__":
    main()
