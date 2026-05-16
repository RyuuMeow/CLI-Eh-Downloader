"""HTML parser for e-hentai / exhentai gallery pages."""

from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import unquote, urljoin, urlparse

from bs4 import BeautifulSoup

from .client import EHClient
from .models import GalleryImage, GalleryInfo, SearchPage, SearchResult, SiteType, TorrentInfo
from .utils import IMAGE_PAGE_URL_PATTERN, build_gallery_url, build_torrent_page_url, get_base_url, parse_gallery_url

log = logging.getLogger(__name__)

# E-Hentai JSON API endpoint (works for both sites)
API_URL = "https://api.e-hentai.org/api.php"


async def fetch_gallery_info(client: EHClient, gid: int, token: str, site: SiteType) -> GalleryInfo:
    """Fetch gallery metadata via the JSON API."""
    data = await client.post_json(API_URL, {
        "method": "gdata",
        "gidlist": [[gid, token]],
        "namespace": 1,
    })

    gmetadata = data.get("gmetadata", [])
    if not gmetadata:
        raise ValueError(f"No metadata returned for gallery {gid}/{token}")

    meta = gmetadata[0]
    if "error" in meta:
        raise ValueError(f"API error: {meta['error']}")

    # Parse tags into namespace -> list of tags
    tags: dict[str, list[str]] = {}
    for tag_str in meta.get("tags", []):
        if ":" in tag_str:
            ns, tag = tag_str.split(":", 1)
        else:
            ns, tag = "misc", tag_str
        tags.setdefault(ns, []).append(tag)

    return GalleryInfo(
        gid=int(meta["gid"]),
        token=meta["token"],
        url=f"{get_base_url(site)}/g/{meta['gid']}/{meta['token']}/",
        site=site,
        title=meta.get("title", ""),
        title_jpn=meta.get("title_jpn", ""),
        category=meta.get("category", ""),
        uploader=meta.get("uploader", ""),
        tags=tags,
        file_count=int(meta.get("filecount", 0)),
        filesize=str(meta.get("filesize", "")),
        posted=meta.get("posted", ""),
        rating=float(meta.get("rating", 0)),
        torrent_count=int(meta.get("torrentcount", 0)),
        thumb=meta.get("thumb", ""),
    )


async def fetch_image_list(client: EHClient, gallery: GalleryInfo) -> list[GalleryImage]:
    """Fetch the list of image page URLs from all gallery pages."""
    images: list[GalleryImage] = []
    missing_page_reasons: list[str] = []

    # Calculate number of gallery listing pages (typically 20 or 40 per page)
    # We use 20 as a conservative estimate; the actual count is determined by page content
    total_pages = (gallery.file_count + 19) // 20

    for page_num in range(total_pages):
        page_url = f"{gallery.url}?p={page_num}"
        soup = await _fetch_gallery_page_soup(client, page_url)

        # The #gdt container holds all thumbnails
        # Each thumbnail is an <a> directly inside #gdt (large mode: class="gt200")
        # or inside <div class="gdtm"> (normal mode) or <div class="gdtl"> (large mode)
        gdt = soup.select_one("#gdt")
        if not gdt:
            reason = _describe_missing_gdt_page(soup)
            missing_page_reasons.append(f"page {page_num}: {reason}")
            log.debug("No #gdt found on page %d: %s", page_num, reason)
            continue

        links = gdt.select("a[href]")
        page_images_found = 0

        for link in links:
            href = link.get("href", "")
            if not href or "/s/" not in href:
                continue

            index = len(images) + 1

            # Try to extract filename from the thumbnail div's title attribute
            # e.g. title="Page 1: 000.jpg"
            filename = None
            inner_div = link.select_one("div[title]")
            if inner_div:
                title_attr = inner_div.get("title", "")
                # Format: "Page N: filename.ext"
                match = re.match(r"Page\s+\d+:\s+(.+)", title_attr)
                if match:
                    filename = match.group(1).strip()

            images.append(GalleryImage(index=index, page_url=href, filename=filename))
            page_images_found += 1

        log.debug("Page %d: found %d images", page_num, page_images_found)

        # Stop if we've collected enough images
        if len(images) >= gallery.file_count:
            break

    log.info("Total image pages found: %d (expected: %d)", len(images), gallery.file_count)
    if not images and missing_page_reasons:
        raise ValueError(f"Could not read gallery image list ({missing_page_reasons[0]})")
    return images


async def _fetch_gallery_page_soup(client: EHClient, page_url: str) -> BeautifulSoup:
    response = await client.get(page_url)
    soup = BeautifulSoup(response.text, "lxml")

    if soup.select_one("#gdt"):
        return soup

    warning_link = _find_view_gallery_link(soup)
    if warning_link:
        response = await client.get(warning_link)
        soup = BeautifulSoup(response.text, "lxml")

    return soup


def _find_view_gallery_link(soup: BeautifulSoup) -> str:
    for link in soup.select("a[href]"):
        if link.get_text(" ", strip=True).lower() == "view gallery":
            return link["href"]
    return ""


def _describe_missing_gdt_page(soup: BeautifulSoup) -> str:
    text = soup.get_text(" ", strip=True)
    if "Content Warning" in text and "View Gallery" in text:
        return "content warning page was not bypassed"
    if "This gallery has been removed" in text or "Gallery not found" in text:
        return "gallery is unavailable"
    if "Your IP address has been temporarily banned" in text:
        return "temporary IP ban"
    title = soup.select_one("title")
    if title:
        return f"unexpected page: {title.get_text(' ', strip=True)}"
    return "unexpected page without thumbnail container"


async def fetch_image_url(client: EHClient, image: GalleryImage) -> GalleryImage:
    """Resolve the actual image URL from an image viewer page."""
    response = await client.get(image.page_url)
    soup = BeautifulSoup(response.text, "lxml")

    # The main image element: <img id="img" src="...">
    img_tag = soup.select_one("#img")
    if img_tag and img_tag.get("src"):
        image.image_url = img_tag["src"]
    else:
        log.warning("No #img found on page %s", image.page_url)

    # Try to get filename from #i4 if not already set from thumbnail
    # The #i4 div contains text like "000.jpg :: 1280 x 1791 :: 332.9 KiB"
    if not image.filename:
        i4 = soup.select_one("#i4")
        if i4:
            text = i4.get_text(strip=True)
            parts = text.split("::")
            if parts:
                image.filename = parts[0].strip()

    # Fallback: derive filename from the image URL
    if not image.filename and image.image_url:
        url_path = image.image_url.split("?")[0]
        image.filename = url_path.split("/")[-1]

    # Final fallback: generate a numbered filename
    if not image.filename:
        image.filename = f"{image.index:04d}.jpg"

    return image


async def resolve_gallery_url_from_image_page(client: EHClient, image_page_url: str) -> str:
    """Resolve an E-Hentai/ExHentai image page URL back to its gallery URL."""
    image_page_url = image_page_url.strip()
    if not IMAGE_PAGE_URL_PATTERN.match(image_page_url):
        raise ValueError("Invalid image page URL")

    response = await client.get(image_page_url)
    soup = BeautifulSoup(response.text, "lxml")

    gallery_links: list[str] = []
    for link in soup.select("a[href]"):
        href = urljoin(str(response.url), link["href"])
        parsed = parse_gallery_url(href)
        if not parsed:
            continue

        link_text = link.get_text(" ", strip=True).lower()
        if "back to gallery" in link_text:
            gid, token, site = parsed
            return build_gallery_url(gid, token, site)
        gallery_links.append(href)

    for href in gallery_links:
        parsed = parse_gallery_url(href)
        if parsed:
            gid, token, site = parsed
            return build_gallery_url(gid, token, site)

    raise ValueError("Could not find gallery URL from image page")


async def fetch_torrent_list(client: EHClient, gallery: GalleryInfo) -> list[TorrentInfo]:
    """Fetch available torrents for a gallery."""
    if gallery.torrent_count == 0:
        return []

    torrent_url = build_torrent_page_url(gallery.gid, gallery.token, gallery.site)
    response = await client.get(torrent_url)

    torrents = parse_torrent_list_html(response.text, str(response.url))
    log.info("Found %d torrents for gallery %d", len(torrents), gallery.gid)
    return torrents


def parse_torrent_list_html(html: str, base_url: str = "") -> list[TorrentInfo]:
    """Parse available torrents from a gallery torrent page HTML document."""
    soup = BeautifulSoup(html, "lxml")
    torrents: list[TorrentInfo] = []

    # Torrent pages have changed shape over time. Most entries are form/table
    # blocks, but the reliable anchor is the ehtracker "get" link itself.
    tracker_links = [
        link for link in soup.select("a[href]")
        if _is_torrent_download_href(str(link.get("href", "")))
    ]
    seen_urls: set[str] = set()

    for tracker_link in tracker_links:
        try:
            download_url = _torrent_download_url_from_link(tracker_link, base_url)
            if download_url in seen_urls:
                continue
            seen_urls.add(download_url)

            container = _find_torrent_entry_container(tracker_link)
            text = container.get_text(" ", strip=True) if container else tracker_link.get_text(" ", strip=True)

            name = tracker_link.get_text(" ", strip=True) or _torrent_name_from_url(download_url)
            stats = _extract_torrent_stats(container, text)

            torrents.append(TorrentInfo(
                name=name,
                url=download_url,
                size=stats["size"],
                seeds=stats["seeds"],
                peers=stats["peers"],
                downloads=stats["downloads"],
                posted=stats["posted"],
            ))
        except Exception:
            log.debug("Failed to parse torrent entry", exc_info=True)

    if tracker_links:
        torrents.sort(key=lambda t: t.seeds, reverse=True)
        return torrents

    for form in _find_torrent_info_forms(soup):
        try:
            gtid_input = form.select_one("input[name='gtid']")
            gtid = str(gtid_input.get("value", "")).strip() if gtid_input else ""
            if not gtid:
                continue

            info_url = urljoin(base_url, str(form.get("action", "")))
            dedupe_key = f"gtid:{gtid}"
            if dedupe_key in seen_urls:
                continue
            seen_urls.add(dedupe_key)

            text = form.get_text(" ", strip=True)
            stats = _extract_torrent_stats(form, text)
            name = _extract_torrent_entry_name(form) or f"torrent_{gtid}"

            torrents.append(TorrentInfo(
                name=name,
                url=info_url,
                size=stats["size"],
                seeds=stats["seeds"],
                peers=stats["peers"],
                downloads=stats["downloads"],
                posted=stats["posted"],
                gtid=gtid,
            ))
        except Exception:
            log.debug("Failed to parse torrent info form", exc_info=True)

    # Sort by seeds (highest first)
    torrents.sort(key=lambda t: t.seeds, reverse=True)
    return torrents


def _extract_int(text: str, pattern: str) -> int:
    """Extract an integer from text using a regex pattern."""
    match = re.search(pattern, text)
    return int(match.group(1).replace(",", "")) if match else 0


def _is_torrent_download_href(href: str) -> bool:
    href_lower = href.lower()
    if not href_lower:
        return False
    if "ehtracker.org/get/" in href_lower:
        return True
    if "/torrent/" in href_lower and href_lower.split("?", 1)[0].endswith(".torrent"):
        return True
    parsed = urlparse(href_lower)
    return parsed.netloc.endswith("ehtracker.org") and parsed.path.startswith("/get/")


def _torrent_download_url_from_link(link: Any, base_url: str) -> str:
    onclick = str(link.get("onclick", ""))
    match = re.search(r"document\.location\s*=\s*['\"]([^'\"]+\.torrent)['\"]", onclick, re.IGNORECASE)
    if match:
        return urljoin(base_url, match.group(1))
    return urljoin(base_url, str(link.get("href", "")))


def _find_torrent_info_forms(soup: BeautifulSoup) -> list[Any]:
    forms = []
    for form in soup.select("form[action]"):
        if not form.select_one("input[name='gtid']"):
            continue
        if not form.select_one("input[name='torrent_info']"):
            continue
        forms.append(form)
    return forms


def _find_torrent_entry_container(link) -> Any:
    for selector in ("form", "tr", "table"):
        container = link.find_parent(selector)
        if container:
            return container
    parent = link.parent
    for _ in range(3):
        if not parent:
            break
        text = parent.get_text(" ", strip=True)
        if "Seeds" in text or "Peers" in text or "Downloads" in text:
            return parent
        parent = parent.parent
    return link.parent or link


def _extract_torrent_stats(container: Any, text: str) -> dict[str, Any]:
    table_stats = _extract_torrent_table_stats(container)

    seeds = table_stats.get("seeds", 0) or _extract_labeled_int(text, "Seeds")
    peers = table_stats.get("peers", 0) or _extract_labeled_int(text, "Peers")
    downloads = table_stats.get("downloads", 0) or _extract_labeled_int(text, "Downloads")
    size = table_stats.get("size", "") or _extract_labeled_text(text, "Size", r"[\d.]+\s*(?:[KMGT]i?B|bytes?)")
    posted = table_stats.get("posted", "") or _extract_labeled_text(
        text,
        "Posted",
        r"\d{4}-\d{2}-\d{2}(?:\s+\d{2}:\d{2})?",
    )

    return {
        "size": size,
        "seeds": seeds,
        "peers": peers,
        "downloads": downloads,
        "posted": posted,
    }


def _extract_labeled_int(text: str, label: str) -> int:
    return _extract_int(text, rf"\b{re.escape(label)}\b\s*:?\s*([0-9][0-9,]*)")


def _extract_labeled_text(text: str, label: str, value_pattern: str) -> str:
    match = re.search(rf"\b{re.escape(label)}\b\s*:?\s*({value_pattern})", text, re.IGNORECASE)
    return match.group(1).strip() if match else ""


def _extract_torrent_table_stats(container: Any) -> dict[str, Any]:
    stats: dict[str, Any] = {}
    if not container:
        return stats

    for row in container.select("tr"):
        cells = [cell.get_text(" ", strip=True) for cell in row.select("th, td")]
        cells = [cell for cell in cells if cell]
        if len(cells) < 2:
            continue

        for cell in cells:
            key, value = _split_labeled_torrent_cell(cell)
            if key and value:
                stats[key] = _coerce_torrent_stat_value(key, value)
        if any(_split_labeled_torrent_cell(cell)[0] for cell in cells):
            continue

        # Label/value rows, e.g. "Seeds" "12".
        if len(cells) == 2:
            key = _normalize_torrent_stat_label(cells[0])
            if key:
                stats[key] = _coerce_torrent_stat_value(key, cells[1])
            continue

        # Header row followed by value row, e.g. "Posted Size Seeds..." then values.
        keys = [_normalize_torrent_stat_label(cell) for cell in cells]
        if any(keys):
            next_row = row.find_next_sibling("tr")
            if not next_row:
                continue
            values = [cell.get_text(" ", strip=True) for cell in next_row.select("th, td")]
            for key, value in zip(keys, values):
                if key and value:
                    stats[key] = _coerce_torrent_stat_value(key, value)

    return stats


def _normalize_torrent_stat_label(label: str) -> str:
    normalized = re.sub(r"[^a-z]", "", label.lower())
    aliases = {
        "posted": "posted",
        "size": "size",
        "seeds": "seeds",
        "seed": "seeds",
        "peers": "peers",
        "peer": "peers",
        "downloads": "downloads",
        "download": "downloads",
        "completed": "downloads",
    }
    return aliases.get(normalized, "")


def _split_labeled_torrent_cell(text: str) -> tuple[str, str]:
    match = re.match(r"\s*([A-Za-z]+)\s*:\s*(.+?)\s*$", text)
    if not match:
        return "", ""
    return _normalize_torrent_stat_label(match.group(1)), match.group(2)


def _coerce_torrent_stat_value(key: str, value: str) -> Any:
    if key in {"seeds", "peers", "downloads"}:
        match = re.search(r"[0-9][0-9,]*", value)
        return int(match.group(0).replace(",", "")) if match else 0
    return value.strip()


def _torrent_name_from_url(url: str) -> str:
    path_name = unquote(urlparse(url).path.rstrip("/").split("/")[-1])
    return path_name or "torrent"


def _extract_torrent_entry_name(container: Any) -> str:
    if not container:
        return ""

    for row in reversed(container.select("tr")):
        cells = [cell.get_text(" ", strip=True) for cell in row.select("td, th")]
        for cell in reversed(cells):
            if _looks_like_torrent_name(cell):
                return cell

    for text in reversed(list(container.stripped_strings)):
        if _looks_like_torrent_name(text):
            return text
    return ""


def _looks_like_torrent_name(text: str) -> bool:
    if not text:
        return False
    lowered = text.lower()
    if lowered in {"information", "download", "back to index"}:
        return False
    if any(label in lowered for label in ("posted:", "size:", "seeds:", "peers:", "downloads:", "uploader:")):
        return False
    return lowered.endswith((".zip", ".torrent", ".cbz", ".rar", ".7z")) or len(text) > 20


async def search_galleries(
    client: EHClient,
    query: str,
    site: SiteType = SiteType.E_HENTAI,
    page: int = 0,
    url_override: str = "",
) -> SearchPage:
    """Search for galleries on e-hentai / exhentai.

    Returns a SearchPage with results and pagination info.
    Use url_override to navigate via next/prev cursor URLs.
    """
    import urllib.parse

    if url_override:
        url = url_override
    else:
        base = get_base_url(site)
        encoded = urllib.parse.quote(query)
        url = f"{base}/?f_search={encoded}"

    response = await client.get(url)
    soup = BeautifulSoup(response.text, "lxml")
    results: list[SearchResult] = []

    # --- Parse total results from "Found about N results." ---
    total_results = 0
    searchtext = soup.select_one("div.searchtext")
    if searchtext:
        st_text = searchtext.get_text(strip=True)
        count_match = re.search(r"([\d,]+)\s*results", st_text)
        if count_match:
            total_results = int(count_match.group(1).replace(",", ""))

    # --- Parse next/prev URLs from div.searchnav ---
    next_url = ""
    prev_url = ""
    nav = soup.select_one("div.searchnav")
    if nav:
        next_link = nav.select_one("a#unext")
        if next_link and next_link.get("href"):
            next_url = next_link["href"]
        prev_link = nav.select_one("a#uprev")
        if prev_link and prev_link.get("href"):
            prev_url = prev_link["href"]

    results = _parse_gallery_list_results(soup, site)
    if not results:
        log.warning("No gallery results found for query: %s", query)

    log.info("Search '%s' page %d returned %d results (total: %d)",
             query, page, len(results), total_results)
    return SearchPage(results=results, current_page=page, total_results=total_results,
                      next_url=next_url, prev_url=prev_url)


async def fetch_listing_page(
    client: EHClient,
    base_url: str,
    page: int = 0,
    url_override: str = "",
) -> SearchPage:
    """Fetch a listing page (tag, uploader, category, etc.) by URL.

    Appends ?page=N (or &page=N) for pagination. The parsing logic is the
    same as search_galleries — both use the standard gallery list table.
    """
    import urllib.parse

    if url_override:
        url = url_override
    else:
        # Build the paginated URL
        parsed = urllib.parse.urlparse(base_url)
        params = urllib.parse.parse_qs(parsed.query)
        params["page"] = [str(page)]
        new_query = urllib.parse.urlencode(params, doseq=True)
        url = urllib.parse.urlunparse(parsed._replace(query=new_query))

    response = await client.get(url)
    soup = BeautifulSoup(response.text, "lxml")
    results: list[SearchResult] = []

    # --- Parse total results ---
    total_results = 0
    searchtext = soup.select_one("div.searchtext")
    if searchtext:
        st_text = searchtext.get_text(strip=True)
        count_match = re.search(r"([\d,]+)\s*results", st_text)
        if count_match:
            total_results = int(count_match.group(1).replace(",", ""))

    # If no searchtext div, try to estimate from ip div (some listing pages)
    if not total_results:
        ip_div = soup.select_one("div.ip")
        if ip_div:
            ip_text = ip_div.get_text(strip=True)
            count_match = re.search(r"([\d,]+)\s*results", ip_text)
            if count_match:
                total_results = int(count_match.group(1).replace(",", ""))

    # --- Parse next/prev URLs ---
    next_url = ""
    prev_url = ""
    nav = soup.select_one("div.searchnav")
    if nav:
        next_link = nav.select_one("a#unext")
        if next_link and next_link.get("href"):
            next_url = next_link["href"]
        prev_link = nav.select_one("a#uprev")
        if prev_link and prev_link.get("href"):
            prev_url = prev_link["href"]

    # --- Determine site type from URL ---
    site = SiteType.EX_HENTAI if "exhentai.org" in base_url else SiteType.E_HENTAI

    results = _parse_gallery_list_results(soup, site)
    if not results:
        log.warning("No gallery results found for listing URL: %s", base_url[:80])

    log.info("Listing page %d returned %d results (total: %d)",
             page, len(results), total_results)
    return SearchPage(results=results, current_page=page, total_results=total_results,
                      next_url=next_url, prev_url=prev_url)


def _parse_gallery_list_results(soup: BeautifulSoup, site: SiteType) -> list[SearchResult]:
    """Parse gallery results from both table and thumbnail-grid list modes."""
    results = _parse_table_gallery_results(soup, site)
    if results:
        return results
    return _parse_grid_gallery_results(soup, site)


def _parse_table_gallery_results(soup: BeautifulSoup, site: SiteType) -> list[SearchResult]:
    table = soup.select_one("table.itg")
    if not table:
        return []

    results: list[SearchResult] = []
    seen: set[tuple[int, str]] = set()
    for tr in table.select("tr")[1:]:  # skip header row
        try:
            name_td = tr.select_one("td.gl3c")
            if not name_td:
                continue
            link = name_td.select_one("a[href]")
            result = _search_result_from_link(link, site)
            if not result:
                continue

            cat_td = tr.select_one("td.gl1c")
            result.category = cat_td.get_text(strip=True) if cat_td else ""

            info_td = tr.select_one("td.gl2c")
            if info_td:
                result.pages = _extract_pages(info_td.get_text(" ", strip=True))
                result.posted = _extract_posted(info_td.get_text(" ", strip=True))

            up_td = tr.select_one("td.gl4c")
            if up_td:
                up_text = up_td.get_text(" ", strip=True)
                result.uploader = re.sub(r"\d+\s*pages?", "", up_text).strip()

            key = (result.gid, result.token)
            if key not in seen:
                seen.add(key)
                results.append(result)
        except Exception:
            log.debug("Failed to parse table gallery row", exc_info=True)
    return results


def _parse_grid_gallery_results(soup: BeautifulSoup, site: SiteType) -> list[SearchResult]:
    root = soup.select_one("div.itg")
    if not root:
        return []

    results: list[SearchResult] = []
    seen: set[tuple[int, str]] = set()
    for item in root.select(".gl1t, .gl1e, .gl2t, .gl3t"):
        try:
            link = item.select_one("a[href*='/g/']")
            result = _search_result_from_link(link, site)
            if not result:
                continue

            title_node = item.select_one(".glink")
            if title_node:
                result.title = title_node.get_text(" ", strip=True)

            category_node = item.select_one(".cs")
            if category_node:
                result.category = category_node.get_text(" ", strip=True)

            text = item.get_text(" ", strip=True)
            result.pages = _extract_pages(text)
            result.posted = _extract_posted(text)

            key = (result.gid, result.token)
            if key not in seen:
                seen.add(key)
                results.append(result)
        except Exception:
            log.debug("Failed to parse grid gallery item", exc_info=True)
    return results


def _search_result_from_link(link: Any, site: SiteType) -> SearchResult | None:
    if not link or not link.get("href"):
        return None

    href = str(link["href"])
    parsed = parse_gallery_url(href)
    if not parsed:
        return None

    gid, token, parsed_site = parsed
    glink = link.select_one(".glink")
    img = link.select_one("img[alt]")
    title = (
        glink.get_text(" ", strip=True)
        if glink
        else str(img.get("alt", "")).strip()
        if img
        else link.get_text(" ", strip=True)
    )

    return SearchResult(
        gid=gid,
        token=token,
        url=href,
        title=title,
        site=parsed_site or site,
    )


def _extract_pages(text: str) -> str:
    match = re.search(r"(\d+)\s*pages?", text, re.IGNORECASE)
    return match.group(1) if match else ""


def _extract_posted(text: str) -> str:
    match = re.search(r"(\d{4}-\d{2}-\d{2}\s*\d{2}:\d{2})", text)
    return match.group(1) if match else ""

