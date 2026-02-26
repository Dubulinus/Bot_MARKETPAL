import json
import os
from datetime import datetime

class PortfolioManager:
    def __init__(self, soubor="portfolio_status.json", start_balance=100000):
        self.soubor = soubor
        self.start_balance = start_balance
        
        # Pokud soubor neexistuje, založíme nový účet
        if not os.path.exists(self.soubor):
            self.reset_portfolio()
        
        self.load_portfolio()

    def reset_portfolio(self):
        """Resetuje účet na startovní částku (Restart hry)"""
        data = {
            "balance": self.start_balance,  # Hotovost
            "equity": self.start_balance,   # Hotovost + Otevřené pozice
            "positions": {},                # Otevřené obchody
            "history": []                   # Historie uzavřených obchodů
        }
        self.save_data(data)
        print(f"🏦 Nový účet založen: ${self.start_balance}")

    def load_portfolio(self):
        with open(self.soubor, 'r') as f:
            self.data = json.load(f)

    def save_data(self, data=None):
        if data:
            self.data = data
        with open(self.soubor, 'w') as f:
            json.dump(self.data, f, indent=4)

    def otevrit_pozici(self, symbol, cena, typ_akce, size=1.0):
        """
        Simuluje otevření obchodu.
        size = počet lotů/mincí (zjednodušeně)
        """
        if symbol in self.data["positions"]:
            print(f"⚠️ Už máš otevřenou pozici na {symbol}. Ignoruji.")
            return False

        # Výpočet nákladů (zjednodušeně bez páky pro teď)
        cost = cena * size 
        
        # Kontrola, jestli na to máme (velmi zjednodušené pro FX/Crypto)
        # U FX s pákou je margin menší, ale pro logiku Paper Tradingu stačí:
        print(f"📉 Otevírám {typ_akce} na {symbol} za {cena}...")
        
        pozice = {
            "symbol": symbol,
            "entry_price": cena,
            "size": size,
            "type": typ_akce, # BUY/SELL
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        
        self.data["positions"][symbol] = pozice
        self.save_data()
        return True

    def zavrit_pozici(self, symbol, current_price):
        """
        Zavře pozici a přičte/odečte zisk.
        """
        if symbol not in self.data["positions"]:
            print(f"⚠️ Nemůžu zavřít {symbol}, nic nedržíme.")
            return None

        pos = self.data["positions"][symbol]
        
        # Výpočet zisku (P&L)
        # Long: (Exit - Entry) * Size
        # Short: (Entry - Exit) * Size
        if pos["type"] == "BUY":
            pnl = (current_price - pos["entry_price"]) * pos["size"]
        else: # SELL
            pnl = (pos["entry_price"] - current_price) * pos["size"]

        # U Forexu/Crypto musíme PnL škálovat (např. u EURUSD je pohyb o 0.0001 = 1 pip)
        # Pro zjednodušení teď bereme PnL v dolarech jako prostý rozdíl * size.
        # POZOR: U Forexu 1 Lot = 100,000 jednotek. Takže size 1.0 = násobič 100000.
        # Aby to dávalo smysl, dáme multiplier.
        multiplier = 1
        if "USD" in symbol and "=" in symbol: # Forex (EURUSD=X)
            multiplier = 100000 
        
        final_profit = pnl * multiplier
        
        # Update Balance
        self.data["balance"] += final_profit
        
        # Uložit do historie
        zaznam = {
            "symbol": symbol,
            "type": pos["type"],
            "entry": pos["entry_price"],
            "exit": current_price,
            "profit": round(final_profit, 2),
            "close_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        self.data["history"].append(zaznam)
        
        # Smazat z otevřených
        del self.data["positions"][symbol]
        self.save_data()
        
        print(f"💰 Obchod uzavřen. Zisk: ${final_profit:.2f}. Nový zůstatek: ${self.data['balance']:.2f}")
        return final_profit

    def get_status(self):
        """Vrátí string s aktuálním stavem pro Telegram"""
        pocet_pozic = len(self.data["positions"])
        balance = self.data["balance"]
        return f"🏦 Účet: ${balance:,.2f} | Pozice: {pocet_pozic}"

# --- Testovací sekce ---
if __name__ == "__main__":
    pm = PortfolioManager()
    # 1. Koupíme
    pm.otevrit_pozici("BTC-USD", 65000, "BUY", size=0.1) 
    # 2. Jakože cena stoupla
    pm.zavrit_pozici("BTC-USD", 66000) 
    # Měl by být zisk $100 (rozdíl 1000 * 0.1)