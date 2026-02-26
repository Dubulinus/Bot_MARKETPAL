#include <Adafruit_GFX.h>
#include <MCUFRIEND_kbv.h>

MCUFRIEND_kbv tft;

// Barvy (Cyberpunk styl)
#define BLACK   0x0000
#define GREEN   0x07E0
#define RED     0xF800
#define WHITE   0xFFFF
#define CYAN    0x07FF
#define GREY    0x8410
#define YELLOW  0xFFE0

// Proměnné pro ukládání starých hodnot (abychom nepřekreslovali celý displej a neblikalo to)
String oldPrice = "";
String oldScore = "";
String oldSignal = "";

void setup() {
    Serial.begin(9600);
    uint16_t ID = tft.readID();
    if (ID == 0xD3D3) ID = 0x9486; 
    tft.begin(ID);
    tft.setRotation(1); // Displej na šířku
    
    // Vykreslení statického UI (Tohle se nakreslí jen jednou)
    tft.fillScreen(BLACK);
    
    // Hlavička
    tft.fillRect(0, 0, 480, 40, GREY);
    tft.setTextSize(2);
    tft.setTextColor(CYAN);
    tft.setCursor(10, 10);
    tft.println("MARKETPAL GOD-MODE");

    // Box pro Cenu
    tft.drawRect(10, 50, 200, 100, CYAN);
    tft.setCursor(20, 60);
    tft.setTextColor(WHITE);
    tft.setTextSize(2);
    tft.println("BTC/USD");

    // Box pro OSINT Skóre
    tft.drawRect(220, 50, 150, 100, CYAN);
    tft.setCursor(230, 60);
    tft.println("OSINT SCORE");

    // Box pro Signál
    tft.drawRect(10, 160, 360, 80, CYAN);
    tft.setCursor(20, 170);
    tft.println("ALGORITHM STATUS");
}

void loop() {
    // Čekáme na formát z Pythonu: "CENA,SKORE,SIGNAL\n" (např. "65400,85,STRONG BUY\n")
    if (Serial.available() > 0) {
        String data = Serial.readStringUntil('\n');
        
        // Rozsekání dat podle čárky
        int firstComma = data.indexOf(',');
        int secondComma = data.indexOf(',', firstComma + 1);
        
        if (firstComma > 0 && secondComma > 0) {
            String newPrice = data.substring(0, firstComma);
            String newScore = data.substring(firstComma + 1, secondComma);
            String newSignal = data.substring(secondComma + 1);

            // 1. Aktualizace Ceny
            if (newPrice != oldPrice) {
                tft.fillRect(20, 90, 180, 40, BLACK); // Vymaže starou cenu
                tft.setCursor(20, 90);
                tft.setTextColor(GREEN);
                tft.setTextSize(4);
                tft.print("$"); tft.println(newPrice);
                oldPrice = newPrice;
            }

            // 2. Aktualizace Skóre
            if (newScore != oldScore) {
                tft.fillRect(230, 90, 130, 40, BLACK);
                tft.setCursor(250, 90);
                tft.setTextColor(YELLOW);
                tft.setTextSize(4);
                tft.println(newScore);
                oldScore = newScore;
            }

            // 3. Aktualizace Signálu
            if (newSignal != oldSignal) {
                tft.fillRect(20, 200, 340, 35, BLACK);
                tft.setCursor(20, 200);
                tft.setTextSize(3);
                if (newSignal == "BUY") tft.setTextColor(GREEN);
                else if (newSignal == "SELL") tft.setTextColor(RED);
                else tft.setTextColor(WHITE);
                tft.println(newSignal);
                oldSignal = newSignal;
            }
        }
    }
}