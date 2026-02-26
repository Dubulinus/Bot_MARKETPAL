import requests
from bs4 import BeautifulSoup
import pandas as pd
import logging
import time
from datetime import datetime

# Configure logging for the "ghetto-server"
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("insider_tracker.log"),
        logging.StreamHandler()
    ]
)

logger = logging.getLogger("InsiderTracker")

class InsiderTracker:
    """
    Scrapes OpenInsider for 'Cluster Buys' - a high-conviction signal where 
    multiple insiders buy stock in a short window.
    """
    
    BASE_URL = "http://openinsider.com/cluster-buys"

    def __init__(self):
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }

    def fetch_cluster_buys(self):
        """
        Fetches the latest cluster buys from OpenInsider.
        """
        logger.info("Fetching latest cluster buys from OpenInsider...")
        try:
            response = requests.get(self.BASE_URL, headers=self.headers, timeout=15)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.text, 'html.parser')
            table = soup.find('table', {'class': 'tinytable'})
            
            if not table:
                logger.warning("Could not find the cluster buys table on the page.")
                return []

            # Parse table rows
            rows = []
            for tr in table.find('tbody').find_all('tr'):
                cols = tr.find_all('td')
                if len(cols) < 10:
                    continue
                
                row_data = {
                    'filling_date': cols[1].text.strip(),
                    'trade_date': cols[2].text.strip(),
                    'ticker': cols[3].text.strip(),
                    'company_name': cols[4].text.strip(),
                    'insider_name': cols[5].text.strip(),
                    'title': cols[6].text.strip(),
                    'trade_type': cols[7].text.strip(),
                    'price': float(cols[8].text.strip().replace('$', '').replace(',', '')),
                    'qty': int(cols[9].text.strip().replace('+', '').replace(',', '')),
                    'owned': cols[10].text.strip(),
                    'value': float(cols[12].text.strip().replace('$', '').replace(',', '').replace('+', ''))
                }
                rows.append(row_data)
            
            logger.info(f"Successfully scraped {len(rows)} insider trades.")
            return rows

        except Exception as e:
            logger.error(f"Error fetching cluster buys: {e}")
            return []

    def get_high_conviction_signals(self, min_value=100000):
        """
        Filters for high-conviction signals (e.g., total cluster value > $100k).
        """
        trades = self.fetch_cluster_buys()
        if not trades:
            return []

        df = pd.DataFrame(trades)
        # Group by ticker to see total cluster value and count of insiders
        summary = df.groupby('ticker').agg({
            'value': 'sum',
            'insider_name': 'count',
            'price': 'mean'
        }).rename(columns={'insider_name': 'insider_count'})

        # Filter for clusters with at least 2 insiders and significant value
        signals = summary[(summary['insider_count'] >= 2) & (summary['value'] >= min_value)]
        
        return signals.to_dict('index')

if __name__ == "__main__":
    # Example usage for the Academic Weapon
    tracker = InsiderTracker()
    signals = tracker.get_high_conviction_signals(min_value=500000) # $500k minimum
    
    if signals:
        print("\n🚨 HIGH CONVICTION INSIDER CLUSTER DETECTED 🚨")
        for ticker, data in signals.items():
            print(f"Ticker: {ticker}")
            print(f"  Total Value: ${data['value']:,.2f}")
            print(f"  Insider Count: {data['insider_count']}")
            print(f"  Avg Entry Price: ${data['price']:.2f}")
            print("-" * 40)
    else:
        print("No high-conviction clusters detected in the latest window.")
