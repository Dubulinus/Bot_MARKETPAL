import pandas as pd
from pathlib import Path

print("=== TICK DATA SKENER (LEVEL 2) ===")
seznam = list(Path('.').rglob('*.csv'))

for cesta in seznam:
    soubor = str(cesta)
    print(f"\nSkenuji: {cesta.name}")
    try:
        # Přečteme data
        df = pd.read_csv(soubor)
        chyby = []
        
        # Pojistka, jestli jsou to vůbec ticky
        if 'bid' not in df.columns or 'time' not in df.columns:
            print("  ❌ Ignoruji (nejsou to ticková data)")
            continue
            
        # 1. Kontrola Ghost Ticků (Cena nesmí být nikdy 0 nebo záporná)
        if (df['bid'] <= 0).any() or (df['ask'] <= 0).any():
            chyby.append("OBSAHUJE NULOVOU CENU (Ghost Ticks)!")
            
        # 2. Cestování v čase
        df['time'] = pd.to_datetime(df['time'])
        if not df['time'].is_monotonic_increasing:
            chyby.append("Čas jde pozpátku! (Nutno seřadit)")
            
        # 3. Změření černé díry (Výpadky spojení)
        rozdily = df['time'].diff()
        max_dira = rozdily.max()
        
        if chyby:
            print(f"  ❌ FATÁLNÍ CHYBY: {', '.join(chyby)}")
        else:
            print(f"  ✅ Data jsou zdravá.")
            
        print(f"  ⏱️ Největší časový výpadek: {max_dira}")
            
    except Exception as e:
        print(f"  💀 NELZE PŘEČÍST: {e}")

print("\n=== KONEC ===")