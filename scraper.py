import argparse
import csv
import json
import os
import re
import sys
import urllib.request
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

def clean_text(text):
    if not text:
        return ""
    return re.sub(r'\s+', ' ', text).strip()

def sanitize_filename(name):
    # Remove characters that are invalid in filenames
    return re.sub(r'[\\/*?:"<>|]', "", name).replace(" ", "_").lower()

def get_store_slug(url):
    match = re.search(r'/store/([^/\?#]+)', url)
    if match:
        return match.group(1)
    return "unknown_store"

def download_image(url, save_path):
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    }
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=15) as response:
            with open(save_path, 'wb') as f:
                f.write(response.read())
        return True
    except Exception as e:
        print(f"Warning: Failed to download image {url}: {e}")
        return False

def extract_doordash_hours(payload):
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

def scrape_doordash(page, url):
    print(f"Loading DoorDash page: {url}")
    page.goto(url, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(5000)
    
    html = page.content()
    soup = BeautifulSoup(html, "html.parser")
    
    # Reconstruct the next_f stream to get image map and hours
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
                        img = image_map.get(item_name.strip().lower())
                        
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

def format_uber_hours(hours_spec):
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

def scrape_ubereats(page, url):
    print(f"Loading Uber Eats page: {url}")
    page.goto(url, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(5000)
    
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
                        img = image_map.get(item_name.strip().lower())
                        
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

def main():
    parser = argparse.ArgumentParser(description="Scrape restaurant details, menus, and download images into structured directories.")
    parser.add_argument("urls", nargs="+", help="One or more restaurant URLs to scrape.")
    parser.add_argument("-o", "--output-dir", default="output", help="Directory where structured store outputs will be saved.")
    args = parser.parse_args()
    
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"]
        )
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 800}
        )
        page = context.new_page()
        
        for url in args.urls:
            url_lower = url.lower()
            store_slug = get_store_slug(url)
            print(f"\nProcessing Store: {store_slug}")
            
            try:
                if "doordash.com" in url_lower:
                    info, menu = scrape_doordash(page, url)
                elif "ubereats.com" in url_lower:
                    info, menu = scrape_ubereats(page, url)
                else:
                    print(f"Skipping unsupported URL: {url}")
                    continue
                
                if not info or not menu:
                    print(f"Failed to scrape sufficient data from {url}")
                    continue
                
                # Define structure directories
                store_dir = os.path.join(args.output_dir, store_slug)
                images_dir = os.path.join(store_dir, "images")
                menu_dir = os.path.join(store_dir, "menu")
                info_dir = os.path.join(store_dir, "info")
                
                os.makedirs(images_dir, exist_ok=True)
                os.makedirs(menu_dir, exist_ok=True)
                os.makedirs(info_dir, exist_ok=True)
                
                # Download Images and update local paths
                for idx, item in enumerate(menu):
                    img_url = item.get("item_image")
                    if img_url:
                        # Determine extension
                        ext = ".jpg"
                        if ".png" in img_url.lower():
                            ext = ".png"
                        elif ".webp" in img_url.lower():
                            ext = ".webp"
                        elif ".jpeg" in img_url.lower():
                            ext = ".jpeg"
                            
                        filename = f"{sanitize_filename(item['item_name'])}{ext}"
                        local_path = os.path.join(images_dir, filename)
                        
                        print(f"[{idx+1}/{len(menu)}] Downloading image for: {item['item_name']}")
                        if download_image(img_url, local_path):
                            item["local_image_path"] = os.path.join("images", filename)
                        else:
                            item["local_image_path"] = ""
                    else:
                        item["local_image_path"] = ""
                
                # Write Info CSV
                info_path = os.path.join(info_dir, "info.csv")
                with open(info_path, "w", newline="", encoding="utf-8") as f:
                    writer = csv.writer(f)
                    writer.writerow(["Restaurant Name", "Address", "Average Rating", "Review Count", "Hours of Operation", "Source URL"])
                    writer.writerow([
                        clean_text(info.get("name")),
                        clean_text(info.get("address")),
                        info.get("rating_value"),
                        info.get("review_count"),
                        clean_text(info.get("hours")),
                        url
                    ])
                print(f"Saved store info to {info_path}")
                
                # Write Menu CSV
                menu_path = os.path.join(menu_dir, "menu.csv")
                with open(menu_path, "w", newline="", encoding="utf-8") as f:
                    writer = csv.writer(f)
                    writer.writerow(["Category/Section", "Item Name", "Item Description", "Item Price", "Item Image URL", "Local Image Path"])
                    for item in menu:
                        writer.writerow([
                            clean_text(item.get("section")),
                            clean_text(item.get("item_name")),
                            clean_text(item.get("item_description")),
                            clean_text(item.get("item_price")),
                            clean_text(item.get("item_image")),
                            clean_text(item.get("local_image_path"))
                        ])
                print(f"Saved menu to {menu_path}")
                print(f"Finished processing store: {store_slug}")
                
            except Exception as e:
                print(f"Error scraping {url}: {e}", file=sys.stderr)
                
        browser.close()

if __name__ == "__main__":
    main()
