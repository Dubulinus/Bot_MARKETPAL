import json
import os
from datetime import datetime

class FTMORiskManager:
    def __init__(self, soubor="portfolio_status.json", start_balance=100000):
        self.soubor = soubor
        self.start_balance = start_balance
        
        # FTMO Limity vytesané do kamene
        self.max_daily_loss_pct = 0.05  # 5 % max denní ztráta
        self.max_total_loss_pct = 0.10  # 10 % max celková ztráta
        
        if not os.path.exists(self.soubor):
            self.reset_portfolio()
        
        self.load_portfolio()
        self._check_new_day() # Zkontroluje, jestli není nový den pro reset denního limitu

    def reset_portfolio(self):
        """Resetuje účet na startovní částku (Restart hry/FTMO)"""
        data = {
            "balance": self.start_balance,
            "equity": self.start_balance,
            "positions": {},
            "history": [],
            "current_date": datetime.now().strftime("%Y-%m-%d"),
            "daily_start_balance": self.start_balance # Zůstatek o půlnoci
        }
        self.save_data(data)
        print(f"🏦 [RISK MANAGER] Nový účet založen: ${self.start_balance}")

    def load_portfolio(self):
        with open(self.soubor, 'r') as f:
            self.data = json.load(f)

    def save_data(self, data=None):
        if data:
            self.data = data
        with open(self.soubor, 'w') as f:
            json.dump(self.data, f, indent=4)

    def _check_new_day(self):
        """FTMO resetuje denní ztrátu o půlnoci. My to děláme při prvním spuštění daný den."""
        today = datetime.now().strftime("%Y-%m-%d")
        if self.data.get("current_date") != today:
            self.data["current_date"] = today
            # Nastavíme startovní balance pro dnešek na aktuální balance
            self.data["daily_start_balance"] = self.data["balance"]
            self.save_data()
            print(f"📅 [RISK MANAGER] Nový obchodní den. Denní limit ztráty se resetoval.")

    def schvaleni_obchodu(self):
        """
        Tohle je to tvoje 'poslední slovo'.
        Risk Manager buď dá zelenou (True), nebo obchod zařízne (False).
        """
        current_balance = self.data["balance"]
        
        # 1. Hard Check: Celková ztráta (Max 10 %)
        minimalni_povolena_equity = self.start_balance * (1 - self.max_total_loss_pct)
        if current_balance <= minimalni_povolena_equity:
            print(f"💀 [RISK VETO] FTMO CHALLENGE FAILED. Celková ztráta překročila 10 %. Účet zablokován.")
            return False
            
        # 2. Hard Check: Denní ztráta (Max 5 %)
        daily_start = self.data.get("daily_start_balance", self.start_balance)
        minimalni_denni_equity = daily_start * (1 - self.max_daily_loss_pct)
        if current_balance <= minimalni_denni_equity:
            print(f"🛑 [RISK VETO] DENNÍ LIMIT DOSAŽEN. Ztratil jsi více než 5 % za dnešek. Jdi se projít.")
            return False
            
        return True

    def otevrit_pozici(self, symbol, cena, typ_akce, size=1.0):
        # Zeptáme se šéfa přes riziko, jestli vůbec smíme
        if not self.schvaleni_obchodu():
            print(f"❌ Exekuce zamítnuta Risk Managerem. Ochrana kapitálu aktivní.")
            return False

        if symbol in self.data["positions"]:
            print(f"⚠️ Už máš otevřenou pozici na {symbol}. Ignoruji.")
            return False

        print(f"📉 Otevírám {typ_akce} na {symbol} za {cena}...")
        
        pozice = {
            "symbol": symbol,
            "entry_price": cena,
            "size": size,
            "type": typ_akce,
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        
        self.data["positions"][symbol] = pozice
        self.save_data()
        return True

    def zavrit_pozici(self, symbol, current_price):
        if symbol not in self.data["positions"]:
            print(f"⚠️ Nemůžu zavřít {symbol}, nic nedržíme.")
            return None

        pos = self.data["positions"][symbol]
        
        if pos["type"] == "BUY":
            pnl = (current_price - pos["entry_price"]) * pos["size"]
        else: # SELL
            pnl = (pos["entry_price"] - current_price) * pos["size"]

        multiplier = 1
        if "USD" in symbol and "=" in symbol:
            multiplier = 100000 
        
        final_profit = pnl * multiplier
        self.data["balance"] += final_profit
        
        zaznam = {
            "symbol": symbol,
            "type": pos["type"],
            "entry": pos["entry_price"],
            "exit": current_price,
            "profit": round(final_profit, 2),
            "close_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        self.data["history"].append(zaznam)
        
        del self.data["positions"][symbol]
        self.save_data()
        
        print(f"💰 Obchod uzavřen. Zisk: ${final_profit:.2f}. Nový zůstatek: ${self.data['balance']:.2f}")
        return final_profit

    def get_status(self):
        pocet_pozic = len(self.data["positions"])
        balance = self.data["balance"]
        return f"🏦 Účet: ${balance:,.2f} | Pozice: {pocet_pozic}"

# --- Testovací sekce ---
if __name__ == "__main__":
    rm = FTMORiskManager()
    
    # Zkusíme schválně simulovat ztrátu, aby nás dráb vyhodil
    rm.otevrit_pozici("BTC-USD", 65000, "BUY", size=0.1) 
    # Proděláme 6000 USD (Cena spadne z 65000 na 5000, násobeno 0.1) = to je víc než 5% denní limit z 100k
    rm.zavrit_pozici("BTC-USD", 5000) 
    
    # Pokus o další obchod by měl narazit na VETO
    rm.otevrit_pozici("ETH-USD", 3000, "BUY", size=1.0)