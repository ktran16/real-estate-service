import logging
import os
import subprocess

import typer
from rich.console import Console
from rich.logging import RichHandler

from danang_realestate.db import get_connection, init_db
from danang_realestate.pipeline.geocoder import Geocoder
from danang_realestate.pipeline.loader import load_listings
from danang_realestate.pipeline.rescraper import rescrape_active_listings
from danang_realestate.scrapers import get_scraper
from danang_realestate.scrapers.nhatot import NhaTotScraper
from danang_realestate.utils.http import SafeHTTPClient
from danang_realestate.utils.timeutil import utcnow
from danang_realestate.validation.schema_validator import validate_schema

# Setup rich console and logging
console = Console()
logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
    datefmt="[%X]",
    handlers=[RichHandler(console=console, rich_tracebacks=True)]
)
logger = logging.getLogger("danang_realestate")

app = typer.Typer(help="Da Nang Real Estate Analytics CLI")

@app.command()
def scrape(
    type: str = typer.Option("all", help="Transaction type: sale, rent, or all"),
    limit: int = typer.Option(None, help="Maximum number of listings to scrape"),
    region: str = typer.Option("danang", help="Region to scrape (currently only danang is supported)"),
    source: str = typer.Option("nhatot", help="Source to scrape: nhatot (batdongsan: not yet implemented)")
):
    """Scrape listings from a source and load into DuckDB."""
    console.print(f"[bold green]Starting Scraper[/bold green] - Source: [bold]{source}[/bold], Type: [bold]{type}[/bold], Limit: [bold]{limit}[/bold], Region: [bold]{region}[/bold]")

    # Initialize database tables
    init_db()

    client = SafeHTTPClient()
    scraper = get_scraper(source, client)

    try:
        listings = scraper.scrape(transaction_type=type, limit=limit)
        console.print(f"[green]Scraped {len(listings)} listings from nhatot.com.[/green]")
        
        conn = get_connection()
        try:
            load_listings(conn, listings)
            console.print("[bold green]Success: Listings loaded into DuckDB.[/bold green]")
        finally:
            conn.close()
            
    except Exception as e:
        logger.exception(f"Scrape failed: {e}")
        raise typer.Exit(code=1)
    finally:
        client.close()

@app.command()
def rescrape(
    check_prices: bool = typer.Option(True, "--check-prices", help="Check active listings for price changes and status")
):
    """Recheck currently active listings in DuckDB for price changes and status updates."""
    console.print("[bold green]Rescraping Active Listings[/bold green] to check for price changes and validity...")
    init_db()

    conn = get_connection()
    client = SafeHTTPClient()
    scraper = NhaTotScraper(client)

    try:
        result = rescrape_active_listings(conn, scraper)
        if result.checked == 0:
            console.print("No active listings found in database to recheck.")
            return
        console.print(
            f"[bold green]Rescrape complete.[/bold green] Checked {result.checked} ads, "
            f"updated prices for {result.price_updated}, marked {result.deactivated} offline."
        )
    finally:
        client.close()
        conn.close()

@app.command()
def geocode():
    """Run geocoding on listings that don't have coordinates."""
    console.print("[bold green]Starting Geocoder[/bold green]")
    init_db()
    
    conn = get_connection()
    geocoder = Geocoder(conn)
    try:
        geocoder.geocode_pending_listings()
    finally:
        geocoder.close()
        conn.close()

@app.command()
def transform():
    """Run dbt models to transform raw data to analytics marts."""
    console.print("[bold green]Running dbt Transformations[/bold green]")
    dbt_dir = os.path.join(os.getcwd(), "dbt")
    if not os.path.exists(dbt_dir):
        console.print("[red]Error: 'dbt' project folder not found.[/red]")
        raise typer.Exit(code=1)
        
    try:
        # Run dbt debug or run directly using local profiles directory
        res = subprocess.run(["uv", "run", "dbt", "run", "--profiles-dir", "."], cwd=dbt_dir, check=True)
        if res.returncode == 0:
            console.print("[bold green]dbt run completed successfully.[/bold green]")
    except Exception as e:
        logger.error(f"dbt run failed: {e}")
        raise typer.Exit(code=1)

@app.command()
def run_all(
    type: str = typer.Option("all", help="Transaction type: sale, rent, or all"),
    limit: int = typer.Option(None, help="Maximum number of listings to scrape"),
    source: str = typer.Option("nhatot", help="Source to scrape: nhatot (batdongsan: not yet implemented)")
):
    """Full pipeline: scrape listings -> geocode -> transform."""
    console.print("[bold cyan]Executing full pipeline (Scrape -> Geocode -> Transform)...[/bold cyan]")
    scrape(type=type, limit=limit, region="danang", source=source)
    geocode()
    try:
        transform()
    except Exception:
        console.print("[yellow]Warning: transform phase failed or skipped (make sure dbt models are set up).[/yellow]")

@app.command()
def refresh_wards():
    """Fetch ward mappings from nhatot.com API and save to database."""
    console.print("[bold green]Refreshing Ward Mappings[/bold green]")
    init_db()
    
    client = SafeHTTPClient()
    conn = get_connection()
    
    # Map district area codes to their names
    areas = {
        301701: "Quận Liên Chiểu",
        301702: "Quận Thanh Khê",
        301703: "Quận Hải Châu",
        301704: "Quận Sơn Trà",
        301705: "Quận Ngũ Hành Sơn",
        301706: "Quận Cẩm Lệ",
        301707: "Huyện Hòa Vang"
    }
    
    url = "https://gateway.chotot.com/v2/public/chapy-pro/wards"
    ward_data = []
    
    try:
        for area_code, district_name in areas.items():
            console.print(f"Fetching wards for [bold]{district_name}[/bold] (area: {area_code})...")
            params = {
                "region": 3017,
                "area": area_code
            }
            try:
                data = client.get(url, params=params)
                if data and isinstance(data, dict):
                    wards_list = data.get("wards", [])
                    for w in wards_list:
                        if not isinstance(w, dict):
                            continue
                        code = w.get("id")
                        name = w.get("name")
                        if code and name and code != 0:
                            # Save with region code 3017 for Da Nang
                            ward_data.append((int(code), name, district_name, 3017, utcnow()))
            except Exception as e:
                logger.error(f"Failed to fetch wards for {district_name}: {e}")
                
        if not ward_data:
            console.print("[red]Could not retrieve any ward lists from API.[/red]")
            raise typer.Exit(code=1)
            
        console.print(f"Retrieved {len(ward_data)} wards in total. Saving to ward_mapping...")
        
        conn.execute("BEGIN TRANSACTION")
        try:
            # Clear old mappings for Da Nang (3017)
            conn.execute("DELETE FROM ward_mapping WHERE region_code = 3017")
            
            conn.executemany(
                """
                INSERT INTO ward_mapping (ward_code, ward_name, district_name, region_code, fetched_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                ward_data
            )
            conn.execute("COMMIT")
            console.print(f"[bold green]Successfully refreshed {len(ward_data)} wards in ward_mapping.[/bold green]")
        except Exception as e:
            conn.execute("ROLLBACK")
            logger.error(f"Failed to load ward mappings to database: {e}")
            raise e
    finally:
        client.close()
        conn.close()

@app.command()
def validate_schema_cmd():
    """Check for API schema drift against Pydantic model definitions."""
    client = SafeHTTPClient()
    try:
        report = validate_schema(client)
        report.print_summary()
    except Exception as e:
        logger.error(f"Schema validation check failed: {e}")
        raise typer.Exit(code=1)
    finally:
        client.close()

if __name__ == "__main__":
    app()
