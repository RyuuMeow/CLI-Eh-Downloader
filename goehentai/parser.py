"""HTML parser for e-hentai / exhentai gallery pages."""

from __future__ import annotations

import logging
import re
from typing import Any

from bs4 import BeautifulSoup

from .client import EHClient
from .models import GalleryImage, GalleryInfo, SearchResult, SiteType, TorrentInfo
from .utils import build_torrent_page_url, get_base_url, parse_gallery_url

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

    # Calculate number of gallery listing pages (typically 20 or 40 per page)
    # We use 20 as a conservative estimate; the actual count is determined by page content
    total_pages = (gallery.file_count + 19) // 20

    for page_num in range(total_pages):
        page_url = f"{gallery.url}?p={page_num}"
        response = await client.get(page_url)
        soup = BeautifulSoup(response.text, "lxml")

        # The #gdt container holds all thumbnails
        # Each thumbnail is an <a> directly inside #gdt (large mode: class="gt200")
        # or inside <div class="gdtm"> (normal mode) or <div class="gdtl"> (large mode)
        gdt = soup.select_one("#gdt")
        if not gdt:
            log.warning("No #gdt found on page %d", page_num)
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
    return images


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


async def fetch_torrent_list(client: EHClient, gallery: GalleryInfo) -> list[TorrentInfo]:
    """Fetch available torrents for a gallery."""
    if gallery.torrent_count == 0:
        return []

    torrent_url = build_torrent_page_url(gallery.gid, gallery.token, gallery.site)
    response = await client.get(torrent_url)

    soup = BeautifulSoup(response.text, "lxml")
    torrents: list[TorrentInfo] = []

    # Each torrent is in a <form> containing:
    #   - A <table> with stats (Posted, Size, Seeds, Peers, Downloads)
    #   - An <a href="https://ehtracker.org/get/..."> with the real torrent name
    forms = soup.select("form[action]")

    for form in forms:
        try:
            text = form.get_text(" ", strip=True)

            # The real torrent download URL and name come from the ehtracker link
            tracker_link = form.select_one("a[href*='ehtracker.org']")
            if not tracker_link:
                continue

            download_url = tracker_link["href"]
            name = tracker_link.get_text(strip=True)

            # Extract metrics from the table text
            seeds = _extract_int(text, r"Seeds:\s*(\d+)")
            peers = _extract_int(text, r"Peers:\s*(\d+)")
            downloads = _extract_int(text, r"Downloads:\s*(\d+)")
            size_match = re.search(r"Size:\s*([\d.]+\s*\w+)", text)
            size = size_match.group(1) if size_match else ""
            posted_match = re.search(r"Posted:\s*([\d-]+\s*[\d:]+)", text)
            posted = posted_match.group(1) if posted_match else ""

            torrents.append(TorrentInfo(
                name=name,
                url=download_url,
                size=size,
                seeds=seeds,
                peers=peers,
                downloads=downloads,
                posted=posted,
            ))
        except Exception:
            continue

    # Sort by seeds (highest first)
    torrents.sort(key=lambda t: t.seeds, reverse=True)
    log.info("Found %d torrents for gallery %d", len(torrents), gallery.gid)
    return torrents


def _extract_int(text: str, pattern: str) -> int:
    """Extract an integer from text using a regex pattern."""
    match = re.search(pattern, text)
    return int(match.group(1)) if match else 0


async def search_galleries(
    client: EHClient,
    query: str,
    site: SiteType = SiteType.E_HENTAI,
    page: int = 0,
) -> list[SearchResult]:
    """Search for galleries on e-hentai / exhentai.

    Returns a list of SearchResult from the search results page.
    """
    import urllib.parse

    base = get_base_url(site)
    encoded = urllib.parse.quote(query)
    url = f"{base}/?f_search={encoded}&page={page}"

    response = await client.get(url)
    soup = BeautifulSoup(response.text, "lxml")
    results: list[SearchResult] = []

    table = soup.select_one("table.itg")
    if not table:
        log.warning("No results table found for query: %s", query)
        return results

    rows = table.select("tr")
    for tr in rows[1:]:  # skip header row
        try:
            # Category
            cat_td = tr.select_one("td.gl1c")
            category = cat_td.get_text(strip=True) if cat_td else ""

            # Title + gallery link
            name_td = tr.select_one("td.gl3c")
            if not name_td:
                continue
            link = name_td.select_one("a")
            if not link or not link.get("href"):
                continue

            href = link["href"]
            parsed = parse_gallery_url(href)
            if not parsed:
                continue
            gid, token, _ = parsed
            glink = link.select_one(".glink")
            title = glink.get_text(" ", strip=True) if glink else link.get_text(" ", strip=True)

            # Date + pages from gl2c
            info_td = tr.select_one("td.gl2c")
            posted = ""
            pages = ""
            if info_td:
                info_text = info_td.get_text(" ", strip=True)
                pages_match = re.search(r"(\d+)\s*pages?", info_text)
                if pages_match:
                    pages = pages_match.group(1)
                date_match = re.search(r"(\d{4}-\d{2}-\d{2}\s*\d{2}:\d{2})", info_text)
                if date_match:
                    posted = date_match.group(1)

            # Uploader from gl4c
            up_td = tr.select_one("td.gl4c")
            uploader = ""
            if up_td:
                up_text = up_td.get_text(" ", strip=True)
                # Remove the pages part
                up_text = re.sub(r"\d+\s*pages?", "", up_text).strip()
                uploader = up_text

            results.append(SearchResult(
                gid=gid,
                token=token,
                url=href,
                title=title,
                category=category,
                uploader=uploader,
                pages=pages,
                posted=posted,
                site=site,
            ))
        except Exception:
            continue

    log.info("Search '%s' returned %d results", query, len(results))
    return results
