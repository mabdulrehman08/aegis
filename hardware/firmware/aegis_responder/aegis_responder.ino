// AEGIS responder device firmware — Arduino UNO R4 WiFi + Grove Base Shield.
//
// The device only displays assignments the laptop bridge sends it and reports
// button presses back. It never decides, approves, or dispatches anything.
//
// Wiring (Grove Base Shield):
//   Grove button -> D2
//   Grove LED    -> D4
//
// Serial protocol (115200 baud, one ASCII line per message, '\n' terminated):
//   Host -> device
//     HELLO                          -> HELLO aegis-responder fw=<ver> device=<id>
//     PING                           -> PONG
//     ASSIGN <cmd> <label> <urgency> -> DELIVERED <cmd>   (urgency: high|normal)
//     CANCEL <cmd>                   -> CANCELLED <cmd>
//     RESET                          -> CLEARED  (drop whatever is shown)
//     OK <kind> <cmd>                -> (stops retrying that ACK/DONE report)
//   Device -> host
//     ACK <cmd>    short press while an assignment is shown
//     DONE <cmd>   long press after ACK
//     ERR <reason>
// ACK and DONE are re-sent every RETRY_MS until the host answers OK, so a
// dropped line is not lost; the bridge de-duplicates repeats.

#include "ArduinoGraphics.h"
#include "Arduino_LED_Matrix.h"

const char *FW_VERSION = "0.1.0";
const char *DEVICE_ID = "responder-device-01";

const int BUTTON_PIN = 2;
const int LED_PIN = 4;

const unsigned long DEBOUNCE_MS = 30;
const unsigned long LONG_PRESS_MS = 1500;
const unsigned long RETRY_MS = 1000;
const int MAX_RETRIES = 10;
const unsigned long BLINK_FAST_MS = 150;
const unsigned long BLINK_SLOW_MS = 500;

const int CMD_MAX = 40;
const int LABEL_MAX = 4;
const int LINE_MAX = 96;

ArduinoLEDMatrix matrix;

enum State { IDLE, ALERTING, ACKED };
State state = IDLE;

char commandId[CMD_MAX + 1] = "";
char label[LABEL_MAX + 1] = "";
bool urgent = false;

char pendingKind[5] = "";  // "ACK" or "DONE" awaiting host OK
unsigned long pendingSentAt = 0;
int pendingRetries = 0;

char lineBuf[LINE_MAX + 1];
int lineLen = 0;
bool lineOverflow = false;

bool buttonStable = HIGH;
bool buttonLastRead = HIGH;
unsigned long buttonChangedAt = 0;
unsigned long pressStartedAt = 0;
bool longPressFired = false;

bool ledOn = false;
unsigned long ledToggledAt = 0;

// ---------------------------------------------------------------- display

void showText(const char *text) {
  matrix.beginDraw();
  matrix.clear();
  matrix.stroke(0xFFFFFFFF);
  matrix.textFont(Font_4x6);
  matrix.beginText(0, 1, 0xFFFFFF);
  matrix.print(text);
  matrix.endText();
  matrix.endDraw();
}

void showIdle() {
  matrix.beginDraw();
  matrix.clear();
  matrix.point(11, 7);  // single "alive" pixel in the corner
  matrix.endDraw();
}

// ---------------------------------------------------------------- reports

void sendReport(const char *kind) {
  Serial.print(kind);
  Serial.print(' ');
  Serial.println(commandId);
}

void queueReport(const char *kind) {
  strncpy(pendingKind, kind, sizeof(pendingKind) - 1);
  pendingKind[sizeof(pendingKind) - 1] = '\0';
  pendingRetries = 0;
  pendingSentAt = millis();
  sendReport(pendingKind);
}

void retryPendingReport() {
  if (pendingKind[0] == '\0') return;
  if (millis() - pendingSentAt < RETRY_MS) return;
  if (pendingRetries >= MAX_RETRIES) {
    Serial.print("ERR no_host_ok ");
    Serial.println(pendingKind);
    pendingKind[0] = '\0';
    return;
  }
  pendingRetries++;
  pendingSentAt = millis();
  sendReport(pendingKind);
}

void clearAssignment() {
  state = IDLE;
  commandId[0] = '\0';
  label[0] = '\0';
  urgent = false;
  digitalWrite(LED_PIN, LOW);
  ledOn = false;
  showIdle();
}

// ---------------------------------------------------------------- commands

// Splits buf in place on spaces; returns the number of tokens found.
int tokenize(char *buf, char **tokens, int maxTokens) {
  int count = 0;
  char *p = buf;
  while (*p && count < maxTokens) {
    while (*p == ' ') p++;
    if (!*p) break;
    tokens[count++] = p;
    while (*p && *p != ' ') p++;
    if (*p) *p++ = '\0';
  }
  return count;
}

void handleLine(char *line) {
  char *t[5];
  int n = tokenize(line, t, 5);
  if (n == 0) return;

  if (strcmp(t[0], "HELLO") == 0) {
    Serial.print("HELLO aegis-responder fw=");
    Serial.print(FW_VERSION);
    Serial.print(" device=");
    Serial.println(DEVICE_ID);
  } else if (strcmp(t[0], "PING") == 0) {
    Serial.println("PONG");
  } else if (strcmp(t[0], "ASSIGN") == 0) {
    if (n != 4 || strlen(t[1]) > CMD_MAX || strlen(t[2]) > LABEL_MAX) {
      Serial.println("ERR bad_assign");
      return;
    }
    // A repeated ASSIGN for the command already shown is a host retry:
    // confirm delivery again without resetting the responder's progress.
    if (strcmp(t[1], commandId) != 0) {
      strcpy(commandId, t[1]);
      strcpy(label, t[2]);
      urgent = strcmp(t[3], "high") == 0;
      state = ALERTING;
      pendingKind[0] = '\0';
      showText(label);
    }
    Serial.print("DELIVERED ");
    Serial.println(commandId);
  } else if (strcmp(t[0], "CANCEL") == 0) {
    if (n == 2 && strcmp(t[1], commandId) == 0) {
      Serial.print("CANCELLED ");
      Serial.println(t[1]);
      pendingKind[0] = '\0';
      clearAssignment();
    } else {
      Serial.println("ERR unknown_command");
    }
  } else if (strcmp(t[0], "RESET") == 0) {
    pendingKind[0] = '\0';
    clearAssignment();
    Serial.println("CLEARED");
  } else if (strcmp(t[0], "OK") == 0) {
    if (n == 3 && strcmp(t[1], pendingKind) == 0 && strcmp(t[2], commandId) == 0) {
      bool wasDone = strcmp(pendingKind, "DONE") == 0;
      pendingKind[0] = '\0';
      if (wasDone) clearAssignment();
    }
  } else {
    Serial.println("ERR unknown_verb");
  }
}

void readSerial() {
  while (Serial.available() > 0) {
    char c = (char)Serial.read();
    if (c == '\r') continue;
    if (c == '\n') {
      if (!lineOverflow) {
        lineBuf[lineLen] = '\0';
        handleLine(lineBuf);
      } else {
        Serial.println("ERR line_too_long");
      }
      lineLen = 0;
      lineOverflow = false;
    } else if (lineLen < LINE_MAX) {
      lineBuf[lineLen++] = c;
    } else {
      lineOverflow = true;
    }
  }
}

// ---------------------------------------------------------------- button

void onShortPress() {
  if (state == ALERTING) {
    state = ACKED;
    digitalWrite(LED_PIN, HIGH);
    ledOn = true;
    queueReport("ACK");
  }
}

void onLongPress() {
  if (state == ACKED && pendingKind[0] == '\0') {
    queueReport("DONE");
    showText("OK");
  }
}

void readButton() {
  bool reading = digitalRead(BUTTON_PIN);
  // Grove button reads HIGH when pressed; normalise so LOW means pressed.
  reading = !reading;
  unsigned long now = millis();

  if (reading != buttonLastRead) {
    buttonChangedAt = now;
    buttonLastRead = reading;
  }
  if (now - buttonChangedAt < DEBOUNCE_MS || reading == buttonStable) {
    if (buttonStable == LOW && !longPressFired && now - pressStartedAt >= LONG_PRESS_MS) {
      longPressFired = true;
      onLongPress();
    }
    return;
  }

  buttonStable = reading;
  if (buttonStable == LOW) {
    pressStartedAt = now;
    longPressFired = false;
  } else if (!longPressFired) {
    onShortPress();
  }
}

// ---------------------------------------------------------------- LED

void updateLed() {
  if (state != ALERTING) return;
  unsigned long period = urgent ? BLINK_FAST_MS : BLINK_SLOW_MS;
  if (millis() - ledToggledAt >= period) {
    ledToggledAt = millis();
    ledOn = !ledOn;
    digitalWrite(LED_PIN, ledOn ? HIGH : LOW);
  }
}

// ---------------------------------------------------------------- main

void setup() {
  Serial.begin(115200);
  pinMode(BUTTON_PIN, INPUT);
  pinMode(LED_PIN, OUTPUT);
  matrix.begin();
  clearAssignment();
}

void loop() {
  readSerial();
  readButton();
  updateLed();
  retryPendingReport();
}
