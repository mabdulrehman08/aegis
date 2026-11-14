// Copy to arduino_secrets.h (git-ignored) to enable WiFi, then re-flash.
// Without arduino_secrets.h the device works over USB only.
//
// Use the bridge laptop's Windows Mobile Hotspot (2.4 GHz): see
// hardware/scripts/start-hotspot.ps1, which prints these values.
// The Arduino cannot join eduroam (WPA2-Enterprise) or a guest sign-in portal.

#define SECRET_SSID "AEGIS-RESPONDER"
#define SECRET_PASS "change-me-8+chars"

// Windows Mobile Hotspot always gives the hosting laptop this address.
#define BRIDGE_HOST "192.168.137.1"
#define BRIDGE_PORT 7777
