from marketpal_logger import Denik

print("🧪 Testuji zápis do deníku...")
denik = Denik()
denik.zapis_obchod(
    akce="TEST_BUY", 
    symbol="BTC-USD", 
    cena=69420.00, 
    duvod="Manualni test systemu", 
    strategie="Test_Protocol_Alpha"
)
print("✅ Pokud vidíš soubor 'obchodni_denik.json', funguje to.")