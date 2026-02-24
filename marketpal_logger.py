import json
import os
from datetime import datetime

class Denik:
    def __init__(self, soubor="obchodni_denik.json"):
        self.soubor = soubor
        # Pokud soubor neexistuje, vytvoříme prázdný seznam
        if not os.path.exists(self.soubor):
            with open(self.soubor, 'w') as f:
                json.dump([], f)
    
    def zapis_obchod(self, akce, symbol, cena, duvod, strategie):
        """
        Zapíše obchod do JSON databáze.
        """
        # 1. Vytvoření záznamu
        zaznam = {
            "cas": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "id_obchodu":  f"{symbol}_{int(datetime.now().timestamp())}",
            "symbol": symbol,
            "akce": akce,  # BUY nebo SELL
            "cena": cena,
            "strategie": strategie,
            "duvod": duvod, # Např. "SMA 26 překročil 90"
            "status": "OTEVRENO"
        }
        
        # 2. Načtení starých dat
        with open(self.soubor, 'r') as f:
            data = json.load(f)
            
        # 3. Přidání nového
        data.append(zaznam)
        
        # 4. Uložení
        with open(self.soubor, 'w') as f:
            json.dump(data, f, indent=4)
            
        print(f"✅ Zapsáno do deníku: {akce} {symbol} za {cena}")

# --- Rychlý test, jestli to funguje ---
if __name__ == "__main__":
    muj_denik = Denik()
    muj_denik.zapis_obchod(
        akce="BUY", 
        symbol="EURUSD", 
        cena=1.0540, 
        duvod="Golden Cross 26/90", 
        strategie="SMA_V1"
    )