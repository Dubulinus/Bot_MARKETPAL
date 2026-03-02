import requests
from bs4 import BeautifulSoup
import os
import zipfile

class HistDataMiner:
    def __init__(self, data_dir="C:\\Bot_MARKETPAL\\data_raw"):
        # Cílíme na minutové (M1) svíčky
        self.base_url = "http://www.histdata.com/download-free-forex-historical-data/?/ascii/1-minute-bar-quotes"
        self.data_dir = data_dir
        self.session = requests.Session()
        
        # Maskujeme se jako normální prohlížeč, ne jako bot z Ghetto-Serveru
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
        })
        
        # Vytvoření složky, pokud neexistuje
        if not os.path.exists(self.data_dir):
            os.makedirs(self.data_dir)
            print(f"📁 [MINER] Vytvořena složka pro surová data: {self.data_dir}")

    def stahni_a_priprav_par(self, par, rok):
        print(f"⛏️ [MINER] Zahajuji těžbu 1M dat pro {par} (Rok: {rok})...")
        url = f"{self.base_url}/{par}/{rok}"
        
        try:
            # 1. Průzkum terénu: Načtení stránky a hledání skrytého tokenu ('tk')
            response = self.session.get(url, timeout=10)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, 'html.parser')
            
            form = soup.find('form', id='file_down')
            if not form:
                print(f"❌ [MINER] Formulář nenalezen. Nejsou pro tento rok ({rok}) data rozdělená po měsících? Zkus jiný rok.")
                return False
                
            # Posbíráme všechny skryté hodnoty, které server vyžaduje k uvolnění ZIPu
            payload = {}
            for input_tag in form.find_all('input'):
                payload[input_tag.get('name')] = input_tag.get('value')
            
            # 2. Exekuce: Odeslání POST požadavku s ukradeným tokenem
            print(f"🔑 [MINER] Zabezpečení prolomeno. Zahajuji masivní stahování archivu...")
            download_url = "http://www.histdata.com/get.php"
            headers = {"Referer": url}
            
            zip_response = self.session.post(download_url, data=payload, headers=headers, stream=True, timeout=30)
            zip_response.raise_for_status()
            
            # 3. Uložení na disk Ghetto-Serveru
            zip_filename = f"HISTDATA_{par}_{rok}.zip"
            zip_filepath = os.path.join(self.data_dir, zip_filename)
            
            with open(zip_filepath, 'wb') as f:
                for chunk in zip_response.iter_content(chunk_size=8192):
                    f.write(chunk)
            print(f"💾 [MINER] Archiv stažen: {zip_filename}")
            
            # 4. Příprava pro Rafinerii: Okamžité rozbalení
            print(f"📦 [MINER] Rozbaluji surová data pro rafinerii...")
            with zipfile.ZipFile(zip_filepath, 'r') as zip_ref:
                zip_ref.extractall(self.data_dir)
                rozbalene_soubory = zip_ref.namelist()
            
            # Úklid: Smažeme ZIP, ať si zbytečně nezaplácáš disk
            os.remove(zip_filepath)
            print(f"✅ [MINER] Hotovo! Extrahováno {len(rozbalene_soubory)} souborů. ZIP smazán.")
            return True
            
        except Exception as e:
            print(f"💀 [MINER CHYBA] Operace selhala: {e}")
            return False

# --- Testovací zážeh ---
if __name__ == "__main__":
    miner = HistDataMiner()
    # Zkusíme vytěžit minutovky EUR/USD za rok 2023
    miner.stahni_a_priprav_par("EURUSD", "2023")