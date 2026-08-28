import httpx
from bs4 import BeautifulSoup
from typing import Dict, Any, Optional

async def crawl_dental_school_url(url: str, timeout: float = 20.0) -> Dict[str, Any]:
    """
    Crawls a dental school web page and sanitizes HTML into structured text with headers and tables.
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
    }
    
    async with httpx.AsyncClient(follow_redirects=True, timeout=timeout) as client:
        try:
            response = await client.get(url, headers=headers)
            response.raise_for_status()
            html_content = response.text
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "url": url,
                "raw_text": "",
                "title": "",
                "tables": []
            }
            
    soup = BeautifulSoup(html_content, "html.parser")
    
    # Remove unwanted scripts and styles
    for element in soup(["script", "style", "nav", "footer", "header", "noscript", "svg"]):
        element.decompose()
        
    title = soup.title.string.strip() if soup.title and soup.title.string else url
    
    # Extract tables in structured markdown format
    tables_data = []
    for table in soup.find_all("table"):
        table_rows = []
        for tr in table.find_all("tr"):
            row_cells = [td.get_text(strip=True) for td in tr.find_all(["td", "th"])]
            if any(row_cells):
                table_rows.append(" | ".join(row_cells))
        if table_rows:
            tables_data.append("\n".join(table_rows))
            
    # Extract clean text
    lines = [line.strip() for line in soup.get_text(separator="\n").splitlines() if line.strip()]
    cleaned_text = "\n".join(lines)
    
    return {
        "success": True,
        "url": url,
        "title": title,
        "raw_text": cleaned_text[:35000],  # Keep up to 35k characters
        "tables": tables_data
    }
