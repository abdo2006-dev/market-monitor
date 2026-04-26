import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional
import aiohttp

logger = logging.getLogger(__name__)

DISCORD_RATE_LIMIT_DELAY = 1.0  # seconds between webhook calls


async def send_discord_webhook(webhook_url: str, payload: dict) -> bool:
    """Send a single Discord webhook message."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(webhook_url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 204:
                    return True
                elif resp.status == 429:
                    retry_after = float((await resp.json()).get("retry_after", 5))
                    logger.warning(f"Discord rate limited, retry after {retry_after}s")
                    await asyncio.sleep(retry_after)
                    return False
                else:
                    text = await resp.text()
                    logger.error(f"Discord webhook error {resp.status}: {text}")
                    return False
    except Exception as e:
        logger.error(f"Discord webhook exception: {e}")
        return False


def _format_price(price: Optional[float], currency: str = "USD") -> str:
    if price is None:
        return "N/A"
    symbols = {"USD": "$", "EUR": "€", "GBP": "£", "JPY": "¥"}
    sym = symbols.get(currency, currency + " ")
    return f"{sym}{price:,.2f}"


async def notify_new_product(webhook_url: str, competitor_name: str, product: dict):
    payload = {
        "embeds": [{
            "title": "🆕 New Product Found",
            "color": 0x00AA00,
            "fields": [
                {"name": "Competitor", "value": competitor_name, "inline": True},
                {"name": "Product", "value": product.get("title", "N/A")[:100], "inline": False},
                {"name": "Price", "value": _format_price(product.get("price"), product.get("currency", "USD")), "inline": True},
                {"name": "Stock", "value": product.get("stock_status", "unknown"), "inline": True},
                {"name": "URL", "value": product.get("url", "N/A")[:500], "inline": False},
                {"name": "Detected", "value": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"), "inline": True},
            ],
        }]
    }
    await send_discord_webhook(webhook_url, payload)
    await asyncio.sleep(DISCORD_RATE_LIMIT_DELAY)


async def notify_price_change(webhook_url: str, competitor_name: str, product_title: str,
                               product_url: str, old_price: Optional[float],
                               new_price: Optional[float], currency: str, event_type: str):
    direction = "📈" if event_type == "price_increase" else "📉"
    color = 0xFF4444 if event_type == "price_increase" else 0x00CC44
    diff_amount = None
    diff_pct = None
    if old_price is not None and new_price is not None:
        diff_amount = new_price - old_price
        diff_pct = (diff_amount / old_price * 100) if old_price else 0

    payload = {
        "embeds": [{
            "title": f"{direction} Price Changed",
            "color": color,
            "fields": [
                {"name": "Competitor", "value": competitor_name, "inline": True},
                {"name": "Product", "value": product_title[:100], "inline": False},
                {"name": "Old Price", "value": _format_price(old_price, currency), "inline": True},
                {"name": "New Price", "value": _format_price(new_price, currency), "inline": True},
                {"name": "Difference", "value": f"{_format_price(diff_amount, currency)} ({diff_pct:+.1f}%)" if diff_amount is not None else "N/A", "inline": True},
                {"name": "URL", "value": product_url[:500], "inline": False},
                {"name": "Detected", "value": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"), "inline": True},
            ],
        }]
    }
    await send_discord_webhook(webhook_url, payload)
    await asyncio.sleep(DISCORD_RATE_LIMIT_DELAY)


async def notify_stock_change(webhook_url: str, competitor_name: str, product_title: str,
                               product_url: str, old_stock: str, new_stock: str):
    payload = {
        "embeds": [{
            "title": "📦 Stock Status Changed",
            "color": 0xFFAA00,
            "fields": [
                {"name": "Competitor", "value": competitor_name, "inline": True},
                {"name": "Product", "value": product_title[:100], "inline": False},
                {"name": "Old Stock", "value": old_stock, "inline": True},
                {"name": "New Stock", "value": new_stock, "inline": True},
                {"name": "URL", "value": product_url[:500], "inline": False},
            ],
        }]
    }
    await send_discord_webhook(webhook_url, payload)
    await asyncio.sleep(DISCORD_RATE_LIMIT_DELAY)


async def notify_scrape_failure(webhook_url: str, competitor_name: str, error: str):
    payload = {
        "embeds": [{
            "title": "❌ Competitor Scan Failed",
            "color": 0xFF0000,
            "fields": [
                {"name": "Competitor", "value": competitor_name, "inline": True},
                {"name": "Error", "value": error[:500], "inline": False},
                {"name": "Time", "value": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"), "inline": True},
            ],
        }]
    }
    await send_discord_webhook(webhook_url, payload)


async def send_daily_summary(webhook_url: str, summary: dict):
    biggest_drops = summary.get("biggest_drops", [])
    drops_text = "\n".join(
        [f"• {d['title'][:50]}: {_format_price(d['old_price'])} → {_format_price(d['new_price'])}"
         for d in biggest_drops[:5]]
    ) or "None"

    payload = {
        "embeds": [{
            "title": "📊 Daily Market Monitor Summary",
            "color": 0x5865F2,
            "fields": [
                {"name": "New Products", "value": str(summary.get("new_products_today", 0)), "inline": True},
                {"name": "Price Changes", "value": str(summary.get("price_changes_today", 0)), "inline": True},
                {"name": "Failed Scans", "value": str(summary.get("failed_scans", 0)), "inline": True},
                {"name": "Competitors Scanned", "value": str(summary.get("competitors_scanned", 0)), "inline": True},
                {"name": "Biggest Price Drops", "value": drops_text, "inline": False},
            ],
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }]
    }
    await send_discord_webhook(webhook_url, payload)


async def dispatch_event_notifications(session, events: list, competitors_map: dict,
                                        products_map: dict, notifications_enabled: bool = True):
    """Send Discord notifications for a batch of events."""
    if not notifications_enabled:
        return
    from datetime import timezone
    now = datetime.now(timezone.utc)

    for event in events:
        if event.notification_sent:
            continue
        competitor = competitors_map.get(event.competitor_id)
        if not competitor or not competitor.discord_webhook_url:
            continue

        product = products_map.get(event.product_id) if event.product_id else None

        try:
            if event.event_type == "new_product" and product:
                nv = event.new_value or {}
                await notify_new_product(
                    competitor.discord_webhook_url,
                    competitor.name,
                    {"title": product.title, "price": nv.get("price"), "currency": product.currency,
                     "stock_status": product.stock_status, "url": product.url}
                )
            elif event.event_type in ("price_increase", "price_decrease", "price_changed") and product:
                ov = event.old_value or {}
                nv = event.new_value or {}
                await notify_price_change(
                    competitor.discord_webhook_url,
                    competitor.name,
                    product.title,
                    product.url,
                    ov.get("price"),
                    nv.get("price"),
                    product.currency,
                    event.event_type,
                )
            elif event.event_type in ("stock_in", "stock_out") and product:
                ov = event.old_value or {}
                nv = event.new_value or {}
                await notify_stock_change(
                    competitor.discord_webhook_url,
                    competitor.name,
                    product.title,
                    product.url,
                    ov.get("stock_status", "unknown"),
                    nv.get("stock_status", "unknown"),
                )

            event.notification_sent = True
            event.notification_sent_at = now
        except Exception as e:
            logger.error(f"Failed to send notification for event {event.id}: {e}")
